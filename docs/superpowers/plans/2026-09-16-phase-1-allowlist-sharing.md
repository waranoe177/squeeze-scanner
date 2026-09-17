# Phase 1: Allowlist Sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the always-on Fly Telegram bot serve a fixed friends-&-family allowlist — replies routed per-requester, go/pass owner-only, non-owner `trade` restricted to the tracked universe, a first-use educational disclaimer, and a per-user rate limit.

**Architecture:** A new pure `scanner/access.py` holds all authorization logic (allowlist parsing, owner/allowed checks, rate limiter). `scanner/bot.py` wires it into `poll_once` (authorize each update, route each reply to that update's chat, gate go/pass to the owner) and into `serve` (owns the process-lifetime rate-limiter + seen-disclaimer set). `handle_trade` gains an `is_owner` flag that restricts non-owner `trade SYM` to the tracked universe. No data-plane change.

**Tech Stack:** Python 3.12, pytest, existing `scanner` package (no new dependencies).

**Spec:** docs/superpowers/specs/2026-09-16-phase-1-allowlist-sharing-design.md

## Global Constraints

- No new runtime dependencies; standard library + existing `scanner` modules only.
- TDD throughout; the existing suite is green and must stay green.
- Chat IDs are compared as **strings** everywhere (parity with the current `_from_owner`: `str(chat) != str(allowed_chat)`).
- The owner path is **byte-for-byte unchanged when `TELEGRAM_ALLOWLIST` is unset/empty** (regression guard).
- The repo is **public**: never commit a chat ID. The allowlist is only ever read from the `TELEGRAM_ALLOWLIST` env var.
- `signals.jsonl`, `decisions.jsonl`, `ghsync.py`, and the daily scan are **not** touched.
- Disclaimer copy (verbatim): the `_DISCLAIMER` string in Task 3.
- Rate limit default: 20 requests / 3600 seconds / non-owner chat.

---

### Task 1: `scanner/access.py` — pure authorization layer

**Files:**
- Create: `scanner/access.py`
- Test: `tests/test_access.py`

**Interfaces:**
- Consumes: nothing (standard library only).
- Produces:
  - `parse_allowlist(raw: str | None) -> set[str]`
  - `is_owner(chat_id, owner_id) -> bool`
  - `is_allowed(chat_id, owner_id, allowlist: set[str]) -> bool`
  - `class RateLimiter(limit: int = 20, window_seconds: int = 3600)` with `allow(chat_id, now: float | None = None) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_access.py
from scanner import access


def test_parse_allowlist_handles_blanks_and_whitespace():
    assert access.parse_allowlist(" 111, 222 ,, 333 ") == {"111", "222", "333"}
    assert access.parse_allowlist("") == set()
    assert access.parse_allowlist(None) == set()


def test_is_owner_string_compares():
    assert access.is_owner(111, "111") is True     # int vs str
    assert access.is_owner("222", "111") is False
    assert access.is_owner("111", None) is False    # no owner configured


def test_is_allowed_owner_and_listed_and_unlisted():
    allow = {"222", "333"}
    assert access.is_allowed("111", "111", allow) is True   # owner
    assert access.is_allowed("222", "111", allow) is True   # listed
    assert access.is_allowed(333, "111", allow) is True     # listed, int in
    assert access.is_allowed("999", "111", allow) is False  # unlisted


def test_rate_limiter_under_and_over_cap_with_injected_clock():
    rl = access.RateLimiter(limit=2, window_seconds=100)
    assert rl.allow("u", now=0.0) is True
    assert rl.allow("u", now=1.0) is True
    assert rl.allow("u", now=2.0) is False          # 3rd within window -> blocked
    # a different user is independent
    assert rl.allow("v", now=2.0) is True


def test_rate_limiter_prunes_old_hits_outside_window():
    rl = access.RateLimiter(limit=1, window_seconds=100)
    assert rl.allow("u", now=0.0) is True
    assert rl.allow("u", now=50.0) is False          # still inside window
    assert rl.allow("u", now=101.0) is True          # first hit aged out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_access.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanner.access'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# scanner/access.py
"""Pure authorization layer for the shared bot: who may use it, who is the
owner (may log go/pass), and a per-user rate limit. No I/O — unit-testable.

Chat IDs are compared as strings everywhere, matching bot._from_owner's
str(chat) != str(allowed) convention (Telegram ids arrive as ints or strs)."""

import time


def parse_allowlist(raw):
    """Comma-separated chat ids -> set[str]. None/'' -> empty set. Tolerates
    surrounding whitespace and blank entries."""
    if not raw:
        return set()
    return {tok.strip() for tok in raw.split(",") if tok.strip()}


def is_owner(chat_id, owner_id) -> bool:
    """True iff chat_id is the configured owner (string-compared). No owner
    configured (owner_id None/'') -> False."""
    if owner_id is None or owner_id == "":
        return False
    return str(chat_id) == str(owner_id)


def is_allowed(chat_id, owner_id, allowlist) -> bool:
    """True iff chat_id is the owner OR present in the allowlist set."""
    return is_owner(chat_id, owner_id) or str(chat_id) in allowlist


class RateLimiter:
    """In-memory sliding-window limiter keyed by chat id. `limit` events per
    `window_seconds`. State is a dict[str, list[float]] of hit timestamps;
    single-process serve() means no locking is needed."""

    def __init__(self, limit: int = 20, window_seconds: int = 3600):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, chat_id, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        key = str(chat_id)
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_access.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add scanner/access.py tests/test_access.py
git commit -m "feat: pure authorization layer (allowlist, owner check, rate limiter)"
```

---

### Task 2: `poll_once` — authorize + route replies per requester + owner-only go/pass

**Files:**
- Modify: `scanner/bot.py` (add `_update_chat_id`; rewrite `poll_once` at [scanner/bot.py:175-265](../../../scanner/bot.py#L175))
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `access.parse_allowlist`, `access.is_allowed`, `access.is_owner` (Task 1).
- Produces:
  - `_update_chat_id(update: dict) -> str | None`
  - `poll_once(..., command_handler=None, trade_handler=None)` where injected handlers are now called as `command_handler(sym, cid)` and `trade_handler(opts, cid, is_owner)`; defaults route to `handle_command(sym, cid, token)` / `handle_trade(opts, cid, token, is_owner=is_owner)`.

- [ ] **Step 1: Update the existing dispatch tests to the new handler signatures**

In `tests/test_bot.py`, the injected handlers gain a `cid` argument (and the trade handler an `is_owner`). Change every existing injected handler:
- `test_poll_once_dispatches_decision_and_chart` (~line 140): `command_handler=lambda sym: handled.append(sym) or True` → `command_handler=lambda sym, cid: handled.append(sym) or True`
- `test_poll_once_holds_offset_and_alerts_on_append_failure` (~line 165): `command_handler=lambda sym: True` → `command_handler=lambda sym, cid: True`
- `test_poll_once_advances_offset_when_append_succeeds` (~line 184): same change → `lambda sym, cid: True`
- `test_poll_once_ignores_foreign_chat` (~line 198): `command_handler=lambda sym: handled.append(sym) or True` → `lambda sym, cid: handled.append(sym) or True`

- [ ] **Step 2: Write the new failing tests**

```python
# tests/test_bot.py — append to the poll_once section

def test_poll_once_routes_reply_to_requester(tmp_path, monkeypatch):
    # An allowlisted non-owner (chat 2) gets served in THEIR chat, not owner's.
    monkeypatch.setenv("TELEGRAM_ALLOWLIST", "2")
    spath = tmp_path / "state.json"
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda token, offset, timeout=0: ([_update("nvda", uid=8, chat_id=2)], 9))
    seen = []
    bot.poll_once(token="T", chat_id="1", state_path=spath,
                  command_handler=lambda sym, cid: seen.append((sym, cid)) or True)
    assert seen == [("NVDA", "2")]                     # served, routed to chat 2


def test_poll_once_unlisted_chat_ignored(tmp_path, monkeypatch):
    # Chat 3 is not the owner and not in the allowlist -> dropped, offset consumed.
    monkeypatch.setenv("TELEGRAM_ALLOWLIST", "2")
    spath = tmp_path / "state.json"
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda token, offset, timeout=0: ([_update("nvda", uid=8, chat_id=3)], 9))
    handled = []
    bot.poll_once(token="T", chat_id="1", state_path=spath,
                  command_handler=lambda sym, cid: handled.append(sym) or True)
    assert handled == []
    assert decisions.load_state(spath) == {"offset": 9}


def test_poll_once_nonowner_decision_declined(tmp_path, monkeypatch):
    # An allowlisted non-owner's go/pass is NOT appended and gets a decline reply.
    monkeypatch.setenv("TELEGRAM_ALLOWLIST", "2")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    spath = tmp_path / "state.json"
    appended = []
    monkeypatch.setattr(bot.ghsync, "append_decision",
                        lambda repo, dec, token, **k: appended.append(dec) or True)
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda token, offset, timeout=0: ([_update("go", uid=7, chat_id=2, reply_to=123)], 8))
    msgs = []
    monkeypatch.setattr(bot.notify, "send_message",
                        lambda tok, cid, text: msgs.append((cid, text)))
    bot.poll_once(token="T", chat_id="1", state_path=spath)
    assert appended == []                              # nothing logged
    assert any(str(cid) == "2" and "owner" in text.lower() for cid, text in msgs)
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -k "routes_reply or unlisted or nonowner_decision" -v`
Expected: FAIL (e.g. `TypeError` on handler arity, or `_update_chat_id` missing / not routed).

- [ ] **Step 4: Add `_update_chat_id` and rewrite `poll_once`**

Add the import at the top of `scanner/bot.py` (extend the existing `from scanner import ...` line to include `access`):

```python
from scanner import access, captionparse, chart, data, decisions, ghsync, notify, optfmt, options, score, signals
```

Add the helper just above `poll_once` (after `_from_owner`, which may remain unused or be deleted):

```python
def _update_chat_id(update: dict) -> str | None:
    """The requester's chat id as a string, or None for a malformed update."""
    cid = ((update.get("message") or {}).get("chat") or {}).get("id")
    return None if cid is None else str(cid)
```

Replace the body of `poll_once` (keep the signature line, docstring, and the token/no-token guard) with:

```python
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    owner_id = str(chat_id) if chat_id is not None else (os.environ.get("TELEGRAM_CHAT_ID") or None)
    if not token:
        print("[bot] no TELEGRAM_BOT_TOKEN — skipping")
        return {"updates": 0, "decisions": 0, "charts": 0}

    allowlist = access.parse_allowlist(os.environ.get("TELEGRAM_ALLOWLIST"))
    command_handler = command_handler or (
        lambda sym, cid: handle_command(sym, cid, token))
    trade_handler = trade_handler or (
        lambda opts, cid, is_owner: handle_trade(opts, cid, token, is_owner=is_owner))

    state = decisions.load_state(state_path)
    updates, next_offset = decisions.fetch_updates(token, state["offset"], timeout=timeout)

    # authorize each update and remember WHOSE chat to reply to
    authorized = []
    for u in updates:
        cid = _update_chat_id(u)
        if cid is None:
            continue
        if not access.is_allowed(cid, owner_id, allowlist):
            print(f"  [bot] update from unlisted chat {cid} ignored")
            continue
        authorized.append((u, cid))

    # 1) decisions -> append to decisions.jsonl (OWNER ONLY). The bot NEVER
    #    writes signals.jsonl (the daily scan folds decisions in).
    gh_token = os.environ.get("GITHUB_TOKEN")
    parsed = []
    failed_update_ids = []
    for u, cid in authorized:
        p = decisions.parse_decision(u)
        if not p:
            continue
        if not access.is_owner(cid, owner_id):
            try:
                notify.send_message(token, cid, "Only the owner can log go/pass decisions.")
            except Exception:
                pass
            continue
        parsed.append(p)
        # A decision must never be lost — hold the offset if the append fails.
        persisted = bool(gh_token) and ghsync.append_decision(_REPO, p, gh_token)
        if not persisted:
            print(f"  [bot] decision append failed (holding offset): {p}")
            failed_update_ids.append(u["update_id"])

    # 2) trade + chart requests, each replied to the requester's own chat
    charts = 0
    for u, cid in authorized:
        if decisions.parse_decision(u):
            continue
        owner = access.is_owner(cid, owner_id)
        t = parse_trade(u)
        if t:
            try:
                if trade_handler(t, cid, owner):
                    charts += 1
            except Exception as exc:
                print(f"  [bot] trade failed for {t.get('symbol')}: {exc}")
                try:
                    notify.send_message(token, cid, f"Couldn't analyze {t.get('symbol')}: {exc}")
                except Exception:
                    pass
            continue
        sym = parse_command(u)
        if not sym:
            continue
        try:
            if command_handler(sym, cid):
                charts += 1
        except Exception as exc:
            print(f"  [bot] chart failed for {sym}: {exc}")
            try:
                notify.send_message(token, cid, f"Couldn't chart {sym}: {exc}")
            except Exception:
                pass

    # Hold the offset at the earliest un-persisted decision so it replays.
    if failed_update_ids:
        save_offset = min(failed_update_ids)
        try:
            notify.send_message(
                token, owner_id,
                "⚠️ decision persistence failing — a go/pass could not be saved. "
                "Check GITHUB_TOKEN/PAT. Holding offset to retry next poll.")
        except Exception:
            pass
    else:
        save_offset = next_offset
    decisions.save_state(state_path, {"offset": save_offset})
    print(f"[bot] {len(updates)} update(s), {len(parsed)} decision(s), {charts} chart(s)")
    return {"updates": len(updates), "decisions": len(parsed), "charts": charts}
```

- [ ] **Step 5: Run the full bot test file to verify pass + no regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -v`
Expected: PASS (existing tests updated in Step 1 + the 3 new tests). In particular `test_poll_once_ignores_foreign_chat` still passes (chat 999 unlisted, no `TELEGRAM_ALLOWLIST`).

- [ ] **Step 6: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat: allowlist-authorize updates and route replies per requester; go/pass owner-only"
```

---

### Task 3: `poll_once` — per-user rate limit + first-use disclaimer

**Files:**
- Modify: `scanner/bot.py` (`poll_once` signature + request loop; add `_DISCLAIMER`)
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `access.RateLimiter` (Task 1), the `poll_once` request loop (Task 2).
- Produces: `poll_once(..., rate=None, seen_disclaimer=None)` — when `None`, no limiting / no disclaimer (default; keeps prior tests unchanged). Rate limit + disclaimer apply to **non-owner** request messages only.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bot.py — append to the poll_once section

def test_poll_once_rate_limits_nonowner(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWLIST", "2")
    spath = tmp_path / "state.json"
    updates = [_update("nvda", uid=8, chat_id=2), _update("tsla", uid=9, chat_id=2)]
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda token, offset, timeout=0: (updates, 10))
    msgs = []
    monkeypatch.setattr(bot.notify, "send_message", lambda tok, cid, text: msgs.append(text))
    served = []
    rl = access.RateLimiter(limit=1, window_seconds=3600)
    bot.poll_once(token="T", chat_id="1", state_path=spath, rate=rl,
                  command_handler=lambda sym, cid: served.append(sym) or True)
    assert served == ["NVDA"]                                  # only the first got through
    assert any("too fast" in m.lower() for m in msgs)          # second throttled


def test_poll_once_sends_disclaimer_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWLIST", "2")
    spath = tmp_path / "state.json"
    seen = set()
    msgs = []
    monkeypatch.setattr(bot.notify, "send_message", lambda tok, cid, text: msgs.append(text))
    # first poll: one request from a brand-new non-owner chat
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda token, offset, timeout=0: ([_update("nvda", uid=8, chat_id=2)], 9))
    bot.poll_once(token="T", chat_id="1", state_path=spath, seen_disclaimer=seen,
                  command_handler=lambda sym, cid: True)
    assert sum("educational" in m.lower() for m in msgs) == 1  # disclaimer once
    assert "2" in seen
    # second poll: same chat again -> no second disclaimer
    msgs.clear()
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda token, offset, timeout=0: ([_update("tsla", uid=9, chat_id=2)], 10))
    bot.poll_once(token="T", chat_id="1", state_path=spath, seen_disclaimer=seen,
                  command_handler=lambda sym, cid: True)
    assert not any("educational" in m.lower() for m in msgs)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -k "rate_limits or disclaimer_once" -v`
Expected: FAIL with `TypeError: poll_once() got an unexpected keyword argument 'rate'`.

- [ ] **Step 3: Implement the disclaimer + rate/disclaimer gate**

Add the disclaimer constant near the top of `scanner/bot.py` (after `_REPO`):

```python
_DISCLAIMER = (
    "📊 Sqzdots — this is an educational watchlist & timing-aid tool. It flags a "
    "technical setup; it is not financial advice and has no proven edge. Do your "
    "own research and manage your own risk. Charts on request: send a ticker "
    "(e.g. NVDA)."
)
```

Change the `poll_once` signature to add the two params:

```python
def poll_once(token=None, chat_id=None, ledger_path=None,
              state_path=decisions.DEFAULT_STATE_PATH, timeout: int = 0,
              command_handler=None, trade_handler=None,
              rate=None, seen_disclaimer=None) -> dict:
```

In the request loop (loop `2)` from Task 2), insert the gate immediately after `owner = access.is_owner(cid, owner_id)` and before `t = parse_trade(u)`:

```python
        if not owner:
            if rate is not None and not rate.allow(cid):
                try:
                    notify.send_message(
                        token, cid,
                        "⚠️ you're sending requests too fast — try again in a bit.")
                except Exception:
                    pass
                continue
            if seen_disclaimer is not None and cid not in seen_disclaimer:
                try:
                    notify.send_message(token, cid, _DISCLAIMER)
                except Exception:
                    pass
                seen_disclaimer.add(cid)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -k "rate_limits or disclaimer_once" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat: per-user rate limit + first-use educational disclaimer for shared users"
```

---

### Task 4: `handle_trade` — restrict non-owner `trade SYM` to the tracked universe

**Files:**
- Modify: `scanner/bot.py` (add `_tracked_universe`; `handle_trade` gains `is_owner` + `universe` params and a universe gate)
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `data.load_watchlist` (returns `list[str]`), `_load_results()` (returns the results snapshot or None).
- Produces:
  - `_tracked_universe(watchlist_path="watchlist.csv") -> set[str]` (uppercased symbols)
  - `handle_trade(opts, chat_id, token, *, ..., is_owner: bool = True, universe=None) -> bool` — when `is_owner` is False and `opts["symbol"]` is set (bare `trade SYM`) and the symbol is not in the universe, it replies with a nudge and returns False. Reply-path trades (`opts["symbol"]` is None) are never universe-checked. Owner is never checked.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bot.py — append to the handle_trade section

def test_handle_trade_nonowner_offuniverse_declined():
    msgs = []
    called = {"chain": False}
    ok = bot.handle_trade(
        {"symbol": "ZZZ", "p": None, "risk": None, "dte": None, "full": False, "caption": None},
        chat_id="2", token="T", is_owner=False, universe={"NVDA", "TSLA"},
        fetcher=lambda syms: {},
        chain_fetcher=lambda s: called.__setitem__("chain", True) or {"expiries": []},
        send_message=lambda tok, cid, text: msgs.append(text))
    assert ok is False
    assert called["chain"] is False                     # bailed before any pricing
    assert any("tracked universe" in m.lower() for m in msgs)


def test_handle_trade_owner_offuniverse_not_blocked(monkeypatch):
    # Owner (default is_owner=True) is never universe-checked: it proceeds into
    # the bare path and hits the normal "no active signal" refusal, NOT the nudge.
    monkeypatch.setattr(bot, "_load_results", lambda path=None: {"as_of": "x", "fired": []})
    msgs = []
    ok = bot.handle_trade(
        {"symbol": "ZZZ", "p": None, "risk": None, "dte": None, "full": False, "caption": None},
        chat_id="1", token="T",                          # is_owner defaults True
        fetcher=lambda syms: {},
        chain_fetcher=lambda s: {"expiries": []},
        send_message=lambda tok, cid, text: msgs.append(text))
    assert ok is False
    assert not any("tracked universe" in m.lower() for m in msgs)
    assert any("no active signal" in m.lower() for m in msgs)


def test_tracked_universe_reads_watchlist(monkeypatch):
    monkeypatch.setattr(bot.data, "load_watchlist", lambda path: ["nvda", "TSLA"])
    assert bot._tracked_universe("whatever.csv") == {"NVDA", "TSLA"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -k "offuniverse or tracked_universe" -v`
Expected: FAIL (`handle_trade` has no `is_owner`/`universe` kwargs; `_tracked_universe` missing).

- [ ] **Step 3: Add `_tracked_universe` and the universe gate**

Add the helper near `_load_results` in `scanner/bot.py`:

```python
def _tracked_universe(watchlist_path: str = "watchlist.csv") -> set[str]:
    """Symbols a non-owner may `trade`: the scanned watchlist, uppercased. Falls
    back to the fired symbols in the latest results snapshot if the watchlist
    file isn't in this checkout."""
    try:
        syms = data.load_watchlist(watchlist_path)
        if syms:
            return {s.upper() for s in syms}
    except Exception:
        pass
    results = _load_results()
    return {str(p.get("symbol", "")).upper() for p in (results or {}).get("fired", [])}
```

Change the `handle_trade` signature to add the two keyword params:

```python
def handle_trade(opts, chat_id, token, *, fetcher=None, chain_fetcher=None,
                 send_message=None, asof=None, renderer=None, send_photo=None,
                 tmp_dir=None, is_owner=True, universe=None) -> bool:
```

Insert the gate at the very start of the body, immediately after the collaborator defaults (`asof = asof or date.today()` line), before `caption_text = opts.get("caption")`:

```python
    # Non-owner bare `trade SYM` is restricted to the tracked universe. Reply
    # trades (symbol comes from the replied-to caption) are not checked.
    symbol = opts.get("symbol")
    if not is_owner and symbol:
        uni = universe if universe is not None else _tracked_universe()
        if symbol.upper() not in uni:
            send_message(token, chat_id,
                         f"{symbol} isn't in the tracked universe. "
                         f"Try `chart {symbol}` for a chart.")
            return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -k "offuniverse or tracked_universe" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat: restrict non-owner trade SYM to the tracked universe"
```

---

### Task 5: `serve()` — own the rate limiter + disclaimer set, log allowlist size

**Files:**
- Modify: `scanner/bot.py` (`serve` at [scanner/bot.py:268-301](../../../scanner/bot.py#L268))
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `access.RateLimiter`, `access.parse_allowlist` (Task 1); `poll_once(..., rate=, seen_disclaimer=)` (Task 3).
- Produces: `serve()` constructs one process-lifetime `RateLimiter()` and one `set()` and passes them into every `poll_once` call.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot.py — append near the serve/poll_once tests

def test_serve_passes_rate_and_disclaimer_state(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWLIST", "2,3")
    captured = {}

    def fake_poll(**kw):
        captured.update(kw)
        raise KeyboardInterrupt                      # break serve's loop after one call

    monkeypatch.setattr(bot, "poll_once", fake_poll)
    bot.serve()                                      # returns on KeyboardInterrupt
    assert captured.get("rate") is not None
    assert captured.get("seen_disclaimer") is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -k serve_passes -v`
Expected: FAIL (`serve` calls `poll_once` without `rate`/`seen_disclaimer`, so `captured.get("rate")` is None).

- [ ] **Step 3: Wire the state into `serve`**

In `serve()`, after the startup validation block and before the `while True:` loop, add:

```python
    allowlist = access.parse_allowlist(os.environ.get("TELEGRAM_ALLOWLIST"))
    print(f"[bot] serving owner + {len(allowlist)} allowlisted user(s)")
    rate = access.RateLimiter(limit=20, window_seconds=3600)
    seen_disclaimer: set[str] = set()
```

Change the `poll_once(...)` call inside the loop to pass them:

```python
            poll_once(token=token, chat_id=chat_id, ledger_path=ledger_path,
                      state_path=state_path, timeout=poll_timeout,
                      rate=rate, seen_disclaimer=seen_disclaimer)
```

(Note the existing call passes `timeout=poll_timeout`; keep that.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -k serve_passes -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat: serve() owns per-user rate limiter + disclaimer set; log allowlist size"
```

---

### Task 6: Operator runbook — `deploy/README.md`

**Files:**
- Modify: `deploy/README.md`

- [ ] **Step 1: Add the allowlist section to `deploy/README.md`**

Append a section (adjust surrounding headings to match the file's existing style):

```markdown
## Sharing with friends & family (allowlist)

The bot is owner-only until you set an allowlist. To share:

1. Ask each person to message the bot once, then read the machine logs
   (`fly logs`) — an unlisted chat is logged as
   `update from unlisted chat <ID> ignored`. That `<ID>` is their Telegram
   chat id. (They can also DM `@userinfobot` on Telegram to get their id.)
2. Add the ids to the allowlist secret (comma-separated) and restart:

   ```bash
   fly secrets set TELEGRAM_ALLOWLIST=111,222,333
   ```

   Your own `TELEGRAM_CHAT_ID` is always allowed and does not need to be listed.
3. Remove someone by setting the secret again without their id.

Shared users can request `chart SYM` (any ticker) and `trade SYM` (tracked
universe only). They cannot log go/pass decisions (owner-only). They get a
one-time educational disclaimer and a 20-request/hour rate limit. The allowlist
lives ONLY in this Fly secret — never commit chat ids (the repo is public).
```

- [ ] **Step 2: Verify the section is present**

Run: `grep -n "TELEGRAM_ALLOWLIST" deploy/README.md`
Expected: at least one match in the new section.

- [ ] **Step 3: Full suite green + commit**

```bash
.venv/Scripts/python.exe -m pytest -q
git add deploy/README.md
git commit -m "docs: operator runbook for the friends-&-family allowlist"
```

Expected: entire suite passes (prior 266+ tests plus the new `test_access.py` and `test_bot.py` cases).

---

## Self-Review

**1. Spec coverage:**
- Allowlist via `TELEGRAM_ALLOWLIST` secret, owner always allowed, empty ⇒ owner-only → Task 1 (`parse_allowlist`, `is_allowed`) + Task 2 (wiring) + Task 5 (log). ✓
- Reply routing per requester → Task 2. ✓
- Owner-only go/pass → Task 2. ✓
- Non-owner `trade` restricted to tracked universe; `chart` open → Task 4. ✓
- First-use disclaimer → Task 3. ✓
- Per-user rate limit → Tasks 1 + 3. ✓
- In-memory per-user state owned by serve → Task 5. ✓
- Owner path unchanged when allowlist empty → guarded by defaults in Tasks 2-4, verified by unchanged existing tests in Task 2 Step 5. ✓
- Unknown chat ignored + logged → Task 2. ✓
- Daily broadcast (`TELEGRAM_ALERT_CHAT_IDS`) untouched → not modified in any task. ✓
- Runbook → Task 6. ✓
- All 9 acceptance scenarios map to tests in Tasks 2-4 (1: existing owner tests; 2: routes_reply; 3: disclaimer_once; 4: offuniverse_declined; 5: owner_offuniverse_not_blocked proves gate + inuniverse pass-through; 6: nonowner_decision_declined; 7: unlisted_chat_ignored; 8: rate_limits_nonowner; 9: existing owner tests with no allowlist env). ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N"; every code and test step is concrete. ✓

**3. Type consistency:** `is_owner`/`is_allowed`/`parse_allowlist`/`RateLimiter.allow` signatures identical across Tasks 1→2→3→5. `command_handler(sym, cid)` and `trade_handler(opts, cid, is_owner)` consistent between Task 2 definition and Task 3 usage. `handle_trade(..., is_owner=True, universe=None)` consistent between Task 4 definition and Task 2's default trade_handler call. `_update_chat_id`/`_tracked_universe`/`_DISCLAIMER` each defined once and referenced consistently. ✓
