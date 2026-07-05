# Discretionary Decision Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the owner's GO/PASS reply to each Telegram alert in the signal ledger and report GO-vs-PASS performance (selection alpha) on the recap card and dashboard.

**Architecture:** A new pure module `scanner/decisions.py` parses Telegram `getUpdates` batches, matches replies to ledger records via the stored `telegram_msg_id` (symbol fallback), and writes three write-once fields onto the records. A twice-daily workflow ingests (Telegram discards unfetched updates after ~24h); `ledger.stats` gains a decisions split; recap and dashboard render it. No server — batch only.

**Tech Stack:** Python 3.12, requests (existing dep), pytest, GitHub Actions, Streamlit (dashboard only). No new dependencies.

## Global Constraints

- Decision fields are **write-once**: `decision` ("go"|"pass"), `decided_at` (ISO-8601 UTC from the Telegram reply timestamp), `decision_late` (bool). A second reply to the same record is ignored and logged. Outcome fields remain immutable per existing ledger rules.
- Accepted tokens (case-insensitive): `go`; `pass` or `skip` (both → "pass"). Anything else → ignored + logged, never guessed.
- Matching order: (1) `reply_to_message.message_id` == record `telegram_msg_id`; (2) plain `go SYM` → most recent record for that symbol with no decision. No match → log + skip.
- Late rule: `decision_late = decided_at >= 09:30 America/New_York on entry_date`. Records still `pending_entry` (no `entry_date`) are never late.
- Exactly-once ingestion: `ledger/telegram_state.json` holds `{"offset": <last update_id + 1>}`; ledger is saved **before** the state file (a crash between the two only causes harmless reprocessing thanks to write-once).
- The CTA line `↩️ Reply to this chart: go or pass` appears ONLY on per-ticker photo captions — never in `format_message` output (the summary and the Phase 2 delayed free-channel post must not carry it).
- Stats: `go`/`pass` buckets contain **clean (non-late) closed** records only; `late_n` counts late-decided closed records; `undecided` = closed records with no decision; `selection_alpha = go.avg_r - pass.avg_r`, `None` if either clean bucket is empty.
- Workflows share `concurrency: { group: sqzdots-ledger, cancel-in-progress: false }` (both commit `ledger/`).
- Tests run from repo root: `.venv\Scripts\python.exe -m pytest <file> -v`.

## File Structure

```
scanner/decisions.py        NEW  parse, match/apply, fetch, ingest glue, CLI, decision_table
tests/test_decisions.py     NEW
scanner/notify.py           MOD  _fired_line(p, cta=False) — CTA line on captions only
scanner/run.py              MOD  photo caption uses cta=True (2 call sites: send loop)
scanner/ledger.py           MOD  stats() gains "decisions" sub-dict
scanner/recap.py            MOD  "Your calls" block on the card
dashboard/app.py            MOD  Decision Review section
.github/workflows/decisions.yml  NEW  twice-daily ingest
.github/workflows/scan.yml       MOD  concurrency group + ingest step before scan
README.md                   MOD  short "Decision tracking" subsection
```

---

### Task 1: `parse_decision` — pure reply parsing

**Files:**
- Create: `scanner/decisions.py`
- Test: `tests/test_decisions.py`

**Interfaces:**
- Consumes: nothing from the codebase (pure).
- Produces: `parse_decision(update: dict) -> dict | None` returning
  `{"decision": "go"|"pass", "decided_at": str ISO-UTC, "reply_to_msg_id": int|None, "symbol": str|None}`.
  Task 2 consumes this shape verbatim.

- [ ] **Step 1: Write the failing tests** — create `tests/test_decisions.py`:

```python
"""Decision-tracking tests: parsing, matching, ingestion, reporting."""

from scanner import decisions


def _update(text, reply_to=None, date=1767625200, uid=1):  # 2026-01-05 15:00 UTC
    msg = {"message_id": 900, "date": date, "text": text, "chat": {"id": 1}}
    if reply_to is not None:
        msg["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": uid, "message": msg}


def test_parse_threaded_go():
    p = decisions.parse_decision(_update("go", reply_to=123))
    assert p == {"decision": "go", "decided_at": "2026-01-05T15:00:00+00:00",
                 "reply_to_msg_id": 123, "symbol": None}


def test_parse_pass_and_skip_alias_case_insensitive():
    assert decisions.parse_decision(_update("PASS", reply_to=5))["decision"] == "pass"
    assert decisions.parse_decision(_update("Skip", reply_to=5))["decision"] == "pass"


def test_parse_freeform_symbol():
    p = decisions.parse_decision(_update("go tsla"))
    assert p["decision"] == "go" and p["symbol"] == "TSLA"
    assert p["reply_to_msg_id"] is None


def test_parse_garbage_returns_none():
    assert decisions.parse_decision(_update("looks great!")) is None
    assert decisions.parse_decision(_update("gopher", reply_to=5)) is None
    assert decisions.parse_decision({"update_id": 2}) is None  # no message
    assert decisions.parse_decision(_update("go now then")) is None  # 3 words
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_decisions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanner.decisions'`

- [ ] **Step 3: Implement** — create `scanner/decisions.py`:

```python
"""Discretionary decision tracking: GO/PASS replies -> ledger fields.

The owner replies "go" or "pass" to a signal's chart photo in Telegram (or
sends "go SYM" unthreaded). A twice-daily batch job pulls getUpdates, matches
each reply to its ledger record, and writes three write-once fields:
decision / decided_at / decision_late. No server; exactly-once via a committed
offset state file. Everything here is deliberately conservative: anything
unparseable or unmatchable is logged and skipped, never guessed.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TOKENS = {"go": "go", "pass": "pass", "skip": "pass"}
_FREEFORM = re.compile(r"^(go|pass|skip)\s+([A-Za-z][A-Za-z.\-]{0,9})$", re.IGNORECASE)
_MARKET_TZ = ZoneInfo("America/New_York")
DEFAULT_STATE_PATH = "ledger/telegram_state.json"


def parse_decision(update: dict) -> dict | None:
    """Extract a decision from one Telegram update, or None if it isn't one."""
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    if not text or "date" not in msg:
        return None
    decided_at = datetime.fromtimestamp(msg["date"], tz=timezone.utc).isoformat()
    reply_to = (msg.get("reply_to_message") or {}).get("message_id")

    token = TOKENS.get(text.lower())
    if token:
        return {"decision": token, "decided_at": decided_at,
                "reply_to_msg_id": reply_to, "symbol": None}
    m = _FREEFORM.match(text)
    if m:
        return {"decision": TOKENS[m.group(1).lower()], "decided_at": decided_at,
                "reply_to_msg_id": reply_to, "symbol": m.group(2).upper()}
    return None
```

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_decisions.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/decisions.py tests/test_decisions.py
git commit -m "feat: parse go/pass decision replies"
```

---

### Task 2: `apply_decisions` — matching, write-once, late flag

**Files:**
- Modify: `scanner/decisions.py`
- Test: `tests/test_decisions.py` (append)

**Interfaces:**
- Consumes: ledger record dicts (fields `symbol`, `signal_date`, `entry_date`, `telegram_msg_id`, `status`).
- Produces: `apply_decisions(records: list[dict], parsed: list[dict]) -> list[dict]` — mutates and returns `records`; sets `decision`, `decided_at`, `decision_late` per the Global Constraints. Task 3's `ingest` calls it.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_decisions.py`):

```python
def _rec(symbol="TSLA", signal_date="2026-01-05", entry_date="2026-01-06",
         msg_id=123, status="open"):
    return {"id": f"{symbol}-{signal_date}", "schema_version": 1, "symbol": symbol,
            "direction": "bull", "signal_date": signal_date, "signal_close": 100.0,
            "atr": 2.0, "ema21": 99.0, "conviction_score": 80.0,
            "telegram_msg_id": msg_id, "status": status, "entry": 101.0,
            "entry_date": entry_date, "stop": 98.0, "target": 106.0,
            "exit_price": None, "exit_date": None, "r_multiple": None}


def _parsed(decision="go", decided_at="2026-01-05T23:00:00+00:00",
            reply_to=123, symbol=None):
    return {"decision": decision, "decided_at": decided_at,
            "reply_to_msg_id": reply_to, "symbol": symbol}


def test_apply_matches_by_msg_id_and_sets_fields():
    recs = [_rec(msg_id=123), _rec(symbol="NVDA", msg_id=124)]
    decisions.apply_decisions(recs, [_parsed()])
    assert recs[0]["decision"] == "go"
    assert recs[0]["decided_at"] == "2026-01-05T23:00:00+00:00"
    assert recs[0]["decision_late"] is False   # decided evening before entry
    assert "decision" not in recs[1]


def test_apply_symbol_fallback_picks_latest_undecided():
    older = _rec(signal_date="2026-01-02", msg_id=50)
    newer = _rec(signal_date="2026-01-05", msg_id=51)
    decisions.apply_decisions([older, newer],
                              [_parsed(reply_to=None, symbol="TSLA")])
    assert "decision" not in older and newer["decision"] == "go"


def test_apply_write_once_first_decision_stands():
    rec = _rec()
    decisions.apply_decisions([rec], [_parsed(decision="go")])
    decisions.apply_decisions([rec], [_parsed(
        decision="pass", decided_at="2026-01-06T01:00:00+00:00")])
    assert rec["decision"] == "go"
    assert rec["decided_at"] == "2026-01-05T23:00:00+00:00"


def test_apply_late_boundary():
    # entry 2026-01-06; 09:30 America/New_York (EST) == 14:30 UTC
    early = _rec()
    late = _rec(symbol="NVDA", msg_id=124)
    decisions.apply_decisions(
        [early, late],
        [_parsed(decided_at="2026-01-06T14:29:00+00:00", reply_to=123),
         _parsed(decided_at="2026-01-06T14:31:00+00:00", reply_to=124)])
    assert early["decision_late"] is False
    assert late["decision_late"] is True


def test_apply_pending_entry_never_late():
    rec = _rec(entry_date=None, status="pending_entry")
    decisions.apply_decisions([rec], [_parsed(
        decided_at="2026-03-01T00:00:00+00:00")])
    assert rec["decision_late"] is False


def test_apply_no_match_is_skipped():
    rec = _rec()
    decisions.apply_decisions([rec], [_parsed(reply_to=999),
                                      _parsed(reply_to=None, symbol="ZZZZ")])
    assert "decision" not in rec
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_decisions.py -v -k apply`
Expected: FAIL with `AttributeError: ... no attribute 'apply_decisions'`

- [ ] **Step 3: Implement** (append to `scanner/decisions.py`):

```python
def _is_late(decided_at: str, entry_date: str | None) -> bool:
    """Late = decided at/after 09:30 America/New_York on the entry date.
    No entry date yet (pending_entry) -> decided before the entry, never late."""
    if not entry_date:
        return False
    decided = datetime.fromisoformat(decided_at)
    y, m, d = (int(x) for x in entry_date.split("-"))
    entry_open = datetime(y, m, d, 9, 30, tzinfo=_MARKET_TZ)
    return decided >= entry_open


def apply_decisions(records: list[dict], parsed: list[dict]) -> list[dict]:
    """Write each parsed decision onto its matching record (write-once)."""
    by_msg = {r["telegram_msg_id"]: r for r in records
              if r.get("telegram_msg_id") is not None}
    for p in parsed:
        rec = None
        if p["reply_to_msg_id"] is not None:
            rec = by_msg.get(p["reply_to_msg_id"])
        if rec is None and p["symbol"]:
            candidates = [r for r in records
                          if r["symbol"] == p["symbol"] and not r.get("decision")]
            rec = max(candidates, key=lambda r: r["signal_date"], default=None)
        if rec is None:
            print(f"  [decisions] no match for {p} — skipped")
            continue
        if rec.get("decision"):
            print(f"  [decisions] {rec['id']} already decided — reply ignored")
            continue
        rec["decision"] = p["decision"]
        rec["decided_at"] = p["decided_at"]
        rec["decision_late"] = _is_late(p["decided_at"], rec.get("entry_date"))
        print(f"  [decisions] {rec['id']} -> {p['decision']}"
              f"{' (late)' if rec['decision_late'] else ''}")
    return records
```

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_decisions.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/decisions.py tests/test_decisions.py
git commit -m "feat: apply decisions to ledger records - matching, write-once, late flag"
```

---

### Task 3: `fetch_updates`, `ingest`, state file, CLI

**Files:**
- Modify: `scanner/decisions.py`
- Test: `tests/test_decisions.py` (append)

**Interfaces:**
- Consumes: `ledger.load(path)` / `ledger.save(path, records)` (existing), `apply_decisions` (Task 2).
- Produces:
  - `fetch_updates(token: str, offset: int) -> tuple[list[dict], int]` — updates and the next offset (`max(update_id)+1`, or unchanged when empty).
  - `load_state(path) -> dict` (`{"offset": 0}` when missing), `save_state(path, state) -> None`.
  - `ingest(ledger_path=ledger.DEFAULT_PATH, state_path=DEFAULT_STATE_PATH, token=None) -> int` — applied-decision count; saves ledger BEFORE state.
  - CLI: `python -m scanner.decisions [--ledger ...] [--state ...]` — exits non-zero on fetch failure (workflow alerts on failure); prints and exits 0 when `TELEGRAM_BOT_TOKEN` is unset.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_decisions.py`):

```python
from scanner import ledger


def test_state_roundtrip_and_bootstrap(tmp_path):
    path = tmp_path / "state.json"
    assert decisions.load_state(path) == {"offset": 0}
    decisions.save_state(path, {"offset": 42})
    assert decisions.load_state(path) == {"offset": 42}


def test_ingest_applies_and_advances_offset(tmp_path, monkeypatch):
    lpath, spath = tmp_path / "signals.jsonl", tmp_path / "state.json"
    ledger.save(lpath, [_rec(msg_id=123)])

    calls = []
    def fake_fetch(token, offset):
        calls.append(offset)
        if offset == 0:
            return [_update("go", reply_to=123, uid=7)], 8
        return [], offset
    monkeypatch.setattr(decisions, "fetch_updates", fake_fetch)

    n = decisions.ingest(lpath, spath, token="T")
    assert n == 1
    assert ledger.load(lpath)[0]["decision"] == "go"
    assert decisions.load_state(spath) == {"offset": 8}

    # second run: nothing new, nothing re-applied
    assert decisions.ingest(lpath, spath, token="T") == 0
    assert calls == [0, 8]


def test_ingest_same_update_twice_is_harmless(tmp_path, monkeypatch):
    # crash-between-saves simulation: offset not advanced, update replayed
    lpath, spath = tmp_path / "signals.jsonl", tmp_path / "state.json"
    ledger.save(lpath, [_rec(msg_id=123)])
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda t, o: ([_update("go", reply_to=123, uid=7)], 8))
    decisions.ingest(lpath, spath, token="T")
    n = decisions.ingest(lpath, spath, token="T")  # replays same update
    assert n == 0                                   # write-once absorbed it
    assert ledger.load(lpath)[0]["decision"] == "go"


def test_ingest_without_token_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert decisions.ingest(tmp_path / "l.jsonl", tmp_path / "s.json") == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_decisions.py -v -k "state or ingest"`
Expected: FAIL with `AttributeError: ... no attribute 'load_state'`

- [ ] **Step 3: Implement** (append to `scanner/decisions.py`; also add `import argparse, os` usage as shown):

```python
def fetch_updates(token: str, offset: int) -> tuple[list[dict], int]:
    """One getUpdates batch. Returns (updates, next_offset)."""
    import requests

    resp = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"offset": offset, "timeout": 0,
                "allowed_updates": json.dumps(["message"])},
        timeout=30,
    )
    body = resp.json() if resp.ok else {}
    if not resp.ok or not body.get("ok", False):
        desc = body.get("description", (resp.text or "")[:300])
        raise RuntimeError(f"Telegram getUpdates {resp.status_code}: {desc}")
    updates = body.get("result", [])
    next_offset = max((u["update_id"] for u in updates), default=offset - 1) + 1 \
        if updates else offset
    return updates, next_offset


def load_state(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"offset": 0}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state) + "\n", encoding="utf-8")


def ingest(ledger_path=None, state_path=DEFAULT_STATE_PATH, token=None) -> int:
    """Pull replies, apply decisions, persist. Returns applied count.

    Ledger is saved BEFORE the offset state: a crash between the two replays
    updates on the next run, which write-once absorbs harmlessly.
    """
    import os

    from scanner import ledger

    ledger_path = ledger_path or ledger.DEFAULT_PATH
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[decisions] no TELEGRAM_BOT_TOKEN — skipping ingest")
        return 0

    state = load_state(state_path)
    updates, next_offset = fetch_updates(token, state["offset"])
    records = ledger.load(ledger_path)
    before = sum(1 for r in records if r.get("decision"))
    parsed = [p for p in (parse_decision(u) for u in updates) if p]
    apply_decisions(records, parsed)
    applied = sum(1 for r in records if r.get("decision")) - before

    ledger.save(ledger_path, records)
    save_state(state_path, {"offset": next_offset})
    print(f"[decisions] {len(updates)} update(s), {len(parsed)} decision reply(ies), "
          f"{applied} applied")
    return applied


def main(argv=None) -> int:
    import argparse

    from scanner import ledger

    ap = argparse.ArgumentParser(description="Ingest GO/PASS Telegram replies")
    ap.add_argument("--ledger", default=ledger.DEFAULT_PATH)
    ap.add_argument("--state", default=DEFAULT_STATE_PATH)
    args = ap.parse_args(argv)
    return ingest(args.ledger, args.state)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all PASS (125 existing + 14 new).

- [ ] **Step 5: Commit**

```bash
git add scanner/decisions.py tests/test_decisions.py
git commit -m "feat: decision ingestion - getUpdates, offset state, CLI"
```

---

### Task 4: CTA line on photo captions only

**Files:**
- Modify: `scanner/notify.py` (`_fired_line`), `scanner/run.py` (photo caption call)
- Test: `tests/test_notify.py` (append)

**Interfaces:**
- Consumes: existing `_fired_line(p)` (returns the caption/summary block per fired payload).
- Produces: `_fired_line(p, cta: bool = False)`; when `cta=True` the returned string ends with the line `↩️ Reply to this chart: go or pass`. `format_message` (and therefore the delayed poster) never passes `cta`, so summary output is unchanged.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_notify.py`):

```python
def test_fired_line_cta_only_when_asked():
    p = _p("TSLA", "bear")
    assert "Reply to this chart" in notify._fired_line(p, cta=True)
    assert "Reply to this chart" not in notify._fired_line(p)


def test_format_message_never_contains_cta():
    msg = notify.format_message(_results([_p("TSLA", "bear")]))
    assert "Reply to this chart" not in msg
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_notify.py -v -k cta`
Expected: FAIL with `TypeError: _fired_line() got an unexpected keyword argument 'cta'`

- [ ] **Step 3: Implement** — in `scanner/notify.py`, change the `_fired_line` signature and its return:

```python
def _fired_line(p: dict, cta: bool = False) -> str:
```

and change the final `return` to append the CTA when asked (keep the existing
`head` / `levels` / `tail` construction unchanged above it):

```python
    cta_line = "\n   ↩️ Reply to this chart: go or pass" if cta else ""
    return (
        f"{head}\n"
        f"   close {p['close']:.2f} · RSI {p['rsi']:.0f}\n"
        f"{levels}"
        f"{tail}"
        f"{cta_line}"
    )
```

In `scanner/run.py`, in the send block, change the photo caption call:

```python
                body = notify.send_photo(token, chat_id, str(cpath),
                                         caption=notify._fired_line(p, cta=True))
```

- [ ] **Step 4: Run the full suite** (format_message tests guard the no-CTA side)

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/notify.py scanner/run.py tests/test_notify.py
git commit -m "feat: go/pass call-to-action on alert chart captions"
```

---

### Task 5: `ledger.stats` decisions split + `decision_table`

**Files:**
- Modify: `scanner/ledger.py` (`stats`), `scanner/decisions.py` (`decision_table`)
- Test: `tests/test_ledger.py`, `tests/test_decisions.py` (append)

**Interfaces:**
- Consumes: existing `stats(records)` return dict; `CLOSED`.
- Produces:
  - `stats(records)["decisions"]` =
    `{"go": BUCKET, "pass": BUCKET, "undecided": BUCKET, "late_n": int, "selection_alpha": float|None}`
    where `BUCKET = {"n", "wins", "win_rate" (None at n=0), "avg_r" (None at n=0), "total_r"}`;
    go/pass buckets are clean (non-late) closed records; undecided = closed with no decision.
  - `decisions.decision_table(records) -> list[dict]` — one row per decided record:
    `{"signal_date", "symbol", "direction", "decision", "status", "r_multiple", "late", "exit_date"}`,
    sorted by `signal_date` desc. Consumed by the dashboard (Task 7).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger.py`:

```python
def _decided(rec, decision, late=False):
    rec["decision"] = decision
    rec["decided_at"] = "2026-01-05T23:00:00+00:00"
    rec["decision_late"] = late
    return rec


def test_stats_decisions_split():
    records = [
        _decided(_closed("A-1", "2026-01-08", 1.667, "win"), "go"),
        _decided(_closed("B-1", "2026-01-09", -1.0, "loss"), "go"),
        _decided(_closed("C-1", "2026-01-12", -1.0, "loss"), "pass"),
        _decided(_closed("D-1", "2026-01-13", 2.0, "win"), "go", late=True),
        _closed("E-1", "2026-01-14", 0.3, "time"),          # undecided
        _decided(ledger.new_record(fired_payload(symbol="F")), "go"),  # open: excluded
    ]
    d = ledger.stats(records)["decisions"]
    assert d["go"]["n"] == 2 and d["go"]["wins"] == 1          # late one excluded
    assert round(d["go"]["avg_r"], 4) == round((1.667 - 1.0) / 2, 4)
    assert d["pass"]["n"] == 1 and d["pass"]["avg_r"] == -1.0
    assert d["undecided"]["n"] == 1
    assert d["late_n"] == 1
    assert round(d["selection_alpha"], 4) == round((1.667 - 1.0) / 2 - (-1.0), 4)


def test_stats_decisions_alpha_none_when_bucket_empty():
    records = [_decided(_closed("A-1", "2026-01-08", 1.0, "win"), "go")]
    d = ledger.stats(records)["decisions"]
    assert d["selection_alpha"] is None
    assert d["pass"]["n"] == 0 and d["pass"]["win_rate"] is None
```

Append to `tests/test_decisions.py`:

```python
def test_decision_table_rows_sorted_desc():
    a = _rec(signal_date="2026-01-02", msg_id=1, status="win")
    a.update(decision="go", decided_at="x", decision_late=False,
             r_multiple=1.667, exit_date="2026-01-08")
    b = _rec(symbol="NVDA", signal_date="2026-01-05", msg_id=2)
    b.update(decision="pass", decided_at="x", decision_late=True)
    undecided = _rec(symbol="CAT", signal_date="2026-01-06", msg_id=3)
    rows = decisions.decision_table([a, b, undecided])
    assert [r["symbol"] for r in rows] == ["NVDA", "TSLA"]
    assert rows[0]["late"] is True and rows[1]["r_multiple"] == 1.667
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ledger.py tests/test_decisions.py -v -k "decision"`
Expected: new tests FAIL (`KeyError: 'decisions'`, `AttributeError: decision_table`).

- [ ] **Step 3: Implement**

In `scanner/ledger.py`, inside `stats()` just before its final `return`, build the split (reusing the `closed` list already computed there) and add `"decisions": decisions_split` to the returned dict:

```python
    def _bucket(rows):
        bn = len(rows)
        bwins = sum(1 for r in rows if r["status"] == "win")
        return {"n": bn, "wins": bwins,
                "win_rate": (bwins / bn) if bn else None,
                "avg_r": (sum(r["r_multiple"] for r in rows) / bn) if bn else None,
                "total_r": round(sum(r["r_multiple"] for r in rows), 3)}

    clean_go = [r for r in closed if r.get("decision") == "go" and not r.get("decision_late")]
    clean_pass = [r for r in closed if r.get("decision") == "pass" and not r.get("decision_late")]
    undecided = [r for r in closed if not r.get("decision")]
    go_b, pass_b = _bucket(clean_go), _bucket(clean_pass)
    decisions_split = {
        "go": go_b, "pass": pass_b, "undecided": _bucket(undecided),
        "late_n": sum(1 for r in closed if r.get("decision") and r.get("decision_late")),
        "selection_alpha": (go_b["avg_r"] - pass_b["avg_r"])
        if go_b["avg_r"] is not None and pass_b["avg_r"] is not None else None,
    }
```

In `scanner/decisions.py`, append:

```python
def decision_table(records: list[dict]) -> list[dict]:
    """Rows for the dashboard Decision Review table (decided records only)."""
    rows = [
        {"signal_date": r["signal_date"], "symbol": r["symbol"],
         "direction": r["direction"], "decision": r["decision"],
         "status": r["status"], "r_multiple": r.get("r_multiple"),
         "late": bool(r.get("decision_late")), "exit_date": r.get("exit_date")}
        for r in records if r.get("decision")
    ]
    return sorted(rows, key=lambda r: r["signal_date"], reverse=True)
```

- [ ] **Step 4: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/ledger.py scanner/decisions.py tests/test_ledger.py tests/test_decisions.py
git commit -m "feat: decisions split in ledger stats + dashboard decision table"
```

---

### Task 6: Recap card "Your calls" block

**Files:**
- Modify: `scanner/recap.py`
- Test: `tests/test_recap.py` (append)

**Interfaces:**
- Consumes: `ledger.stats(records)["decisions"]` (Task 5 shape).
- Produces: the card shows one extra line when any clean decided closed records exist; card renders unchanged when none do.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_recap.py`):

```python
def test_render_card_with_decisions(tmp_path):
    rec = _rec("A-1", "2026-06-22", "2026-06-25", 1.5, "win")
    rec.update(decision="go", decided_at="2026-06-22T23:00:00+00:00",
               decision_late=False)
    out = tmp_path / "recap.png"
    recap.render_card([rec], "2026-06-28", out)
    assert out.exists() and out.stat().st_size > 5000


def test_render_card_no_decisions_still_renders(tmp_path):
    out = tmp_path / "recap.png"
    recap.render_card([_rec("A-1", "2026-06-22", "2026-06-25", 1.5, "win")],
                      "2026-06-28", out)
    assert out.exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recap.py -v`
Expected: both new tests PASS trivially today (no code yet asserts the line) — so
first make them meaningful: this task's implementation must not break them, and
visual verification is Step 4. Run anyway; expected: PASS (they pin the
no-crash contract for both branches).

- [ ] **Step 3: Implement** — in `scanner/recap.py`, `render_card`, after the running-record `txt(...)` lines (the block that starts `txt(0.55, 0.84, "Running record", ...)`), add:

```python
    dec = s.get("decisions") or {}
    go_b, pass_b = dec.get("go") or {}, dec.get("pass") or {}
    if go_b.get("n") or pass_b.get("n"):
        def _fmt(b):
            if not b.get("n"):
                return "0"
            wr = f"{b['win_rate'] * 100:.0f}%" if b["win_rate"] is not None else "—"
            return f"{b['n']} · {wr} · {b['avg_r']:+.2f}R"
        alpha = dec.get("selection_alpha")
        alpha_txt = f"edge {alpha:+.2f}R" if alpha is not None else "edge —"
        txt(0.55, 0.62, "Your calls (clean)", 14, "#8b949e")
        txt(0.55, 0.55, f"GO {_fmt(go_b)}   |   PASS {_fmt(pass_b)}   |   {alpha_txt}", 13)
```

(`s` is already `ledger.stats(records)` in `render_card`; no new imports needed.)

- [ ] **Step 4: Visual check**

Run: `$env:PYTHONPATH="."; .venv\Scripts\python.exe -m pytest tests/test_recap.py -v` then render one card manually from the test fixture pattern and open it (or Read the PNG) to confirm the "Your calls" line sits below the running record without overlapping the equity axes (the axes rect starts at x=0.55, y=0.12, height 0.35 — y=0.55/0.62 clears it).
Expected: all recap tests PASS; line visible and non-overlapping.

- [ ] **Step 5: Commit**

```bash
git add scanner/recap.py tests/test_recap.py
git commit -m "feat: 'Your calls' decisions block on weekly recap card"
```

---

### Task 7: Dashboard Decision Review section

**Files:**
- Modify: `dashboard/app.py` (insert after the "Coiled" section, before "Ticker detail")
- Test: none automated (Streamlit page; the data functions it calls are covered by Tasks 3/5) — manual smoke in Step 2.

**Interfaces:**
- Consumes: `ledger.load`, `ledger.stats(...)["decisions"]`, `decisions.decision_table` — exact Task 5 shapes.

- [ ] **Step 1: Implement** — in `dashboard/app.py`, add to the imports line (`from scanner import chart, data, llm_eval, score`):

```python
from scanner import chart, data, decisions, ledger, llm_eval, score  # noqa: E402
```

and insert after the Coiled section (`st.write(" · ".join(results["watching"]))` block):

```python
# ---- decision review -------------------------------------------------------
LEDGER_PATH = ROOT / "ledger" / "signals.jsonl"
led_records = ledger.load(LEDGER_PATH)
dec_rows = decisions.decision_table(led_records)
if dec_rows:
    st.subheader("Decision review — your calls vs the machine")
    d = ledger.stats(led_records)["decisions"]

    def _b(b):
        return f"{b['n']} · {b['avg_r']:+.2f}R" if b["n"] and b["avg_r"] is not None else f"{b['n']}"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("GO (clean)", _b(d["go"]))
    m2.metric("PASS (clean)", _b(d["pass"]))
    alpha = d["selection_alpha"]
    m3.metric("Selection alpha", f"{alpha:+.3f}R" if alpha is not None else "—")
    m4.metric("Late / undecided", f"{d['late_n']} / {d['undecided']['n']}")

    closed_rows = [r for r in dec_rows if r["r_multiple"] is not None and not r["late"]]
    if len(closed_rows) >= 2:
        curves = {}
        for bucket in ("go", "pass"):
            cum, series = 0.0, {}
            for r in sorted((x for x in closed_rows if x["decision"] == bucket),
                            key=lambda x: x["exit_date"]):
                cum += r["r_multiple"]
                series[r["exit_date"]] = cum
            curves[bucket.upper()] = series
        st.line_chart(pd.DataFrame(curves).ffill())

    n_go, n_pass = d["go"]["n"], d["pass"]["n"]
    if n_go >= 30 and n_pass >= 30:
        import math
        rs_go = [r["r_multiple"] for r in closed_rows if r["decision"] == "go"]
        rs_pa = [r["r_multiple"] for r in closed_rows if r["decision"] == "pass"]
        se = math.sqrt(pd.Series(rs_go).var(ddof=1) / n_go
                       + pd.Series(rs_pa).var(ddof=1) / n_pass)
        st.caption(f"alpha t-stat ≈ {alpha / se:+.2f} (Welch, clean closed only)")
    else:
        st.caption(f"t-stat shown once both buckets reach 30 clean decisions "
                   f"(now {n_go} / {n_pass})")

    st.dataframe(pd.DataFrame(dec_rows), use_container_width=True, hide_index=True)
```

- [ ] **Step 2: Manual smoke**

Run: `.venv\Scripts\python.exe -m streamlit run dashboard/app.py` briefly (Ctrl+C after load) or at minimum `python -c "import ast; ast.parse(open('dashboard/app.py').read()); print('parses')"`.
Expected: page loads (section hidden while no decisions exist — ledger is empty today); parse check prints `parses`.

- [ ] **Step 3: Run the full suite** (guards against import errors breaking other modules)

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: dashboard decision review - metrics, GO/PASS curves, table"
```

---

### Task 8: Workflows + README

**Files:**
- Create: `.github/workflows/decisions.yml`
- Modify: `.github/workflows/scan.yml`, `README.md`

**Interfaces:**
- Consumes: `python -m scanner.decisions` CLI (Task 3). Secrets already configured: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID`.

- [ ] **Step 1: Create `.github/workflows/decisions.yml`**:

```yaml
name: Ingest GO/PASS Decisions

on:
  schedule:
    - cron: "0 6,18 * * *"   # twice daily, every day — Telegram drops updates after ~24h
  workflow_dispatch: {}

permissions:
  contents: write

concurrency:
  group: sqzdots-ledger
  cancel-in-progress: false

jobs:
  ingest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install deps
        run: pip install -r requirements.txt

      - name: Ingest decisions
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
        run: python -m scanner.decisions

      - name: Commit ledger + state
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add ledger/
          git diff --staged --quiet || git commit -m "decisions $(date -u +%Y-%m-%dT%H:%M)"
          git push

      - name: Alert operator on failure
        if: failure()
        env:
          TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          ADMIN: ${{ secrets.TELEGRAM_ADMIN_CHAT_ID }}
        run: |
          curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
            -d chat_id="${ADMIN}" \
            -d text="🚨 Sqzdots decision ingest FAILED — ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
```

- [ ] **Step 2: Modify `.github/workflows/scan.yml`** — add directly beneath the `permissions:` block:

```yaml
concurrency:
  group: sqzdots-ledger
  cancel-in-progress: false
```

and insert a new step immediately BEFORE the `Run scan + ledger + site + notify` step:

```yaml
      - name: Ingest pending decisions first
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
        run: python -m scanner.decisions
```

- [ ] **Step 3: Validate YAML + append README subsection**

Run: `.venv\Scripts\python.exe -c "import yaml, glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]; print('workflows OK')"`
Expected: `workflows OK`.

Append to `README.md` after the "Product pipeline (Sqzdots Indicator)" section:

```markdown
## Decision tracking (measure your own judgment)

Reply **go** or **pass** directly to a signal's chart photo in Telegram (long-press
→ Reply). A twice-daily job (`decisions.yml`) records your call in
`ledger/signals.jsonl` (`decision` / `decided_at` / `decision_late`) next to the
mechanical outcome. Decisions made after the entry open are flagged late and kept
out of the clean stats. The weekly recap card and the dashboard's Decision Review
section report GO vs PASS performance — the gap is your measured selection alpha.
Unthreaded `go TSLA` also works. `python -m scanner.decisions` runs an ingest
manually.
```

- [ ] **Step 4: Run the full suite once**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/decisions.yml .github/workflows/scan.yml README.md
git commit -m "feat: decisions ingest workflow + scan-time ingest + docs"
```

---

## Post-plan manual step (owner)

None — secrets already exist. After merge + push, trigger `Ingest GO/PASS Decisions` once via **Run workflow** to confirm a clean no-op run, then reply `go` or `pass` to the next real alert and check the ledger after the next ingest.
