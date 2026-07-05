# Sqzdots Indicator — Phase 0/1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the personal scanner into the Phase 0/1 product: universe backtest CLI, live signal ledger, public track-record site, weekly recap card, channel-tier notifications, and the GitHub Actions jobs that run it all autonomously.

**Architecture:** One engine, three outputs. The existing `scanner/` engine stays the source of truth. New modules (`ledger.py`, `trackrecord.py`, `recap.py`, `delayed.py`) are downstream consumers of the same trade model defined in `backtest.py` (`trade_levels` + `simulate_trade` — one shared code path, enforced by test). Everything runs on GitHub Actions; the site deploys to a separate public repo so the scanner repo (the IP) can stay private.

**Tech Stack:** Python 3.12, pandas 2.2.3 (pinned), yfinance, matplotlib, requests, pytest. GitHub Actions + GitHub Pages (via `peaceiris/actions-gh-pages`). No new dependencies.

## Global Constraints

- Trade model (never deviate): entry = next bar's **open** after signal date; target = entry + 2.5×ATR; stop = entry − 1.5×ATR (mirrored for bear); time exit at close of 5th bar held; when one bar touches both stop and target, count as **stop** (conservative). All from `backtest.trade_levels(mode="entry")` and `backtest.simulate_trade` — never reimplement the math.
- Ledger: `ledger/signals.jsonl`, one JSON object per line, `schema_version: 1`. Closed records (`status` in `win|loss|time`) are **never modified**.
- LLM eval is removed from the pipeline (module stays in repo, unused). No `ANTHROPIC_API_KEY` anywhere in workflows.
- Public brand string: `Sqzdots Indicator`.
- Disclaimer text (site + methodology page, verbatim): "Educational tool, not investment advice. Past performance does not guarantee future results."
- pandas stays pinned at 2.2.3 (Windows Smart App Control; see README).
- Statuses: `pending_entry` → `open` → `win` | `loss` | `time`.
- Tests run from repo root: `.venv\Scripts\python.exe -m pytest <file> -v` (conftest.py handles the path).

## File Structure

```
scanner/ledger.py        NEW  signal ledger: record creation, load/save, entry backfill, close, stats
scanner/trackrecord.py   NEW  static site generator (index/signals/methodology + css) from ledger
scanner/recap.py         NEW  weekly recap card PNG + Telegram post, CLI
scanner/delayed.py       NEW  next-morning free-channel poster (Phase 2), CLI
scanner/backtest.py      MOD  extended stats (losing streak, drawdown), universe runner, CLI
scanner/signals.py       MOD  latest_signal payload += atr, ema21, lit_bull, lit_bear
scanner/scan.py          MOD  build_results += watching_detail
scanner/notify.py        MOD  provisional levels line, no-fire "squeezes building" message, footer
scanner/run.py           MOD  drop LLM eval; wire ledger + site + msg-id capture
universe.csv             NEW  curated ~120-name product universe
ledger/signals.jsonl     NEW  created empty (checked in)
.github/workflows/scan.yml         MOD  universe scan + ledger/site commit + Pages deploy + failure alert
.github/workflows/recap.yml        NEW  Sunday recap job
.github/workflows/free-delayed.yml NEW  Phase-2 morning job (gated on vars.PHASE == '2')
tests/test_ledger.py, tests/test_trackrecord.py, tests/test_recap.py, tests/test_delayed.py  NEW
tests/test_signals.py, tests/test_scan.py, tests/test_notify.py, tests/test_backtest.py      MOD (add tests)
```

---

### Task 1: Remove the LLM eval layer from the pipeline

**Files:**
- Modify: `scanner/run.py` (lines 16, 43–54)

**Interfaces:**
- Consumes: nothing new.
- Produces: `run.main` no longer imports or calls `llm_eval`; fired ranking is by conviction `score` only (already done in `scan.rank_fired`). `llm_eval.py` itself is untouched.

- [ ] **Step 1: Edit `scanner/run.py`**

Remove `llm_eval` from the import on line 16:

```python
from scanner import chart, data, notify, scan
```

Delete the entire LLM block (lines 43–54, from the comment `# Opt-in LLM eval layer` through the `results["fired"].sort(...)` line). Nothing replaces it — `build_results` already ranks fired signals.

- [ ] **Step 2: Run the full suite to verify nothing depended on the wiring**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all 68 tests PASS (test_llm_eval.py tests the module directly, not run.py).

- [ ] **Step 3: Smoke-run the CLI dry**

Run: `$env:PYTHONPATH="."; .venv\Scripts\python.exe -m scanner.run --dry-run --no-charts`
Expected: scan completes, prints message, no `llm` / `ANTHROPIC` output.

- [ ] **Step 4: Commit**

```bash
git add scanner/run.py
git commit -m "feat: remove LLM eval from scan pipeline (spec: deterministic only)"
```

---

### Task 2: Extend `latest_signal` payload with atr, ema21, and lit-condition counts

**Files:**
- Modify: `scanner/signals.py` (the `latest_signal` return dict, lines ~260–273)
- Test: `tests/test_signals.py` (append)

**Interfaces:**
- Consumes: `signals.analyze` columns already computed: `atr`, `ema21`, `ema8`, `ema34`, `sma50`, `sma200`, `rsi`, `ppo`, `squeeze_on`, `macd_green`, `macd_red`, `moxie_up`, `moxie_dn`.
- Produces: `latest_signal(daily, symbol)` payload gains keys:
  - `"atr": float` — signal-bar ATR(14)
  - `"ema21": float` — already computed locally; now exported
  - `"lit_bull": int`, `"lit_bear": int` — how many of the 7 buy (resp. sell) conditions are true on the signal bar.
  Downstream: Task 4 (`ledger.new_record` reads `atr`, `ema21`), Task 9 (`scan.build_results` reads `lit_bull`/`lit_bear`).

- [ ] **Step 1: Write the failing test** (append to `tests/test_signals.py`)

```python
def test_latest_signal_exports_atr_ema21_and_lit_counts(iyt_frame):
    p = signals.latest_signal(iyt_frame, symbol="IYT")
    assert isinstance(p["atr"], float) and p["atr"] > 0
    assert isinstance(p["ema21"], float) and p["ema21"] > 0
    assert 0 <= p["lit_bull"] <= 7
    assert 0 <= p["lit_bear"] <= 7
    # a bar can't fully satisfy both sides at once
    assert not (p["lit_bull"] == 7 and p["lit_bear"] == 7)
```

Note: `tests/test_signals.py` already loads fixture frames — reuse its existing fixture/loader for IYT (`tests/fixtures/IYT.csv`). If the existing file names the fixture differently, match the existing name instead of `iyt_frame`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_signals.py -v -k lit_counts`
Expected: FAIL with `KeyError: 'atr'`

- [ ] **Step 3: Implement** — in `latest_signal`, after `last = out.iloc[-1]` and the existing `ema21/atr/close` locals, add:

```python
    stack_bull = bool(last["ema8"] > last["ema21"] > last["ema34"]) and bool(last["sma50"] > last["sma200"])
    stack_bear = bool(last["ema8"] < last["ema21"] < last["ema34"]) and bool(last["sma50"] < last["sma200"])
    lit_bull = sum(map(bool, [
        last["squeeze_on"], last["rsi"] > 50, last["ppo"] >= 0,
        last["ema8"] > last["ema21"], stack_bull, last["macd_green"], last["moxie_up"],
    ]))
    lit_bear = sum(map(bool, [
        last["squeeze_on"], last["rsi"] < 50, last["ppo"] < 0,
        last["ema8"] < last["ema21"], stack_bear, last["macd_red"], last["moxie_dn"],
    ]))
```

and add to the returned dict:

```python
        "atr": atr,
        "ema21": ema21,
        "lit_bull": int(lit_bull),
        "lit_bear": int(lit_bear),
```

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_signals.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/signals.py tests/test_signals.py
git commit -m "feat: export atr/ema21/lit-condition counts from latest_signal"
```

---

### Task 3: Universe file + backtest extended stats + universe CLI (Phase 0 tool)

**Files:**
- Create: `universe.csv`
- Modify: `scanner/backtest.py` (add `extended_stats`, `run_universe`, `main`)
- Test: `tests/test_backtest.py` (append)

**Interfaces:**
- Consumes: `data.load_watchlist(path)`, `data.fetch_daily(symbols, period)`, existing `backtest(df, symbol, ...)` and `summarize(trades)`.
- Produces:
  - `extended_stats(trades: list[dict]) -> dict` with keys `max_losing_streak: int`, `max_drawdown_r: float` (peak-to-trough of cumulative R, trades ordered by `entry_date`).
  - `run_universe(universe_path: str, period: str = "5y", max_hold: int = 5, symbols: list[str] | None = None) -> list[dict]` — all trades across the universe.
  - CLI: `python -m scanner.backtest --universe universe.csv --period 5y --out out/backtest.json [--symbols IYT,QQQ]` writes `{"summary": {...}, "trades": [...]}` and prints the summary.

- [ ] **Step 1: Create `universe.csv`** — starter curation (owner edits freely later; one ticker per line, `Ticker` header):

```csv
Ticker
SPY
QQQ
IWM
DIA
RSP
XLK
XLF
XLE
XLV
XLI
XLP
XLY
XLU
XLB
XLRE
XLC
SMH
XBI
ITB
XHB
IYT
IYR
KRE
XOP
OIH
GDX
GDXJ
XME
TAN
JETS
TLT
HYG
GLD
SLV
USO
AAPL
MSFT
NVDA
AMZN
GOOGL
META
TSLA
AVGO
AMD
NFLX
CRM
ORCL
ADBE
INTC
MU
QCOM
TXN
AMAT
LRCX
KLAC
PANW
CRWD
NOW
SNOW
PLTR
UBER
ABNB
SHOP
SQ
PYPL
COIN
JPM
BAC
WFC
GS
MS
SCHW
V
MA
AXP
BRK-B
UNH
JNJ
LLY
PFE
MRK
ABBV
TMO
AMGN
XOM
CVX
COP
SLB
OXY
CAT
DE
BA
GE
HON
LMT
RTX
UNP
UPS
FDX
WMT
COST
TGT
HD
LOW
NKE
SBUX
MCD
DIS
CMCSA
T
VZ
TMUS
PG
KO
PEP
PM
CL
MDLZ
FCX
NEM
LIN
APD
DAL
UAL
MAR
```

- [ ] **Step 2: Write the failing tests** (append to `tests/test_backtest.py`)

```python
from scanner.backtest import extended_stats


def _trade(entry_date, r, outcome=None):
    return {
        "symbol": "T", "signal_date": entry_date, "entry_date": entry_date,
        "direction": "bull", "entry": 100.0, "stop": 97.0, "target": 105.0,
        "outcome": outcome or ("win" if r > 0 else "loss"),
        "exit_price": 100.0 + 3 * r, "bars_held": 2,
        "r_multiple": r, "return_pct": r * 0.03,
    }


def test_extended_stats_losing_streak_and_drawdown():
    trades = [
        _trade("2025-01-02", 1.0),
        _trade("2025-01-06", -1.0),
        _trade("2025-01-08", -1.0),
        _trade("2025-01-10", -0.5),
        _trade("2025-01-14", 2.0),
    ]
    s = extended_stats(trades)
    assert s["max_losing_streak"] == 3
    assert s["max_drawdown_r"] == 2.5  # peak +1.0 -> trough -1.5


def test_extended_stats_empty():
    s = extended_stats([])
    assert s == {"max_losing_streak": 0, "max_drawdown_r": 0.0}
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_backtest.py -v -k extended`
Expected: FAIL with `ImportError: cannot import name 'extended_stats'`

- [ ] **Step 4: Implement** — append to `scanner/backtest.py`:

```python
def extended_stats(trades: list[dict]) -> dict:
    """Max consecutive losing trades and max drawdown of cumulative R,
    with trades ordered by entry_date (then signal_date as tiebreak)."""
    ordered = sorted(trades, key=lambda t: (t["entry_date"], t["signal_date"]))
    cum = peak = max_dd = 0.0
    streak = max_streak = 0
    for t in ordered:
        r = t["r_multiple"]
        cum += r
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        streak = streak + 1 if r < 0 else 0
        max_streak = max(max_streak, streak)
    return {"max_losing_streak": max_streak, "max_drawdown_r": round(max_dd, 3)}


def run_universe(universe_path: str, period: str = "5y", max_hold: int = 5,
                 symbols: list[str] | None = None) -> list[dict]:
    """Walk-forward backtest across every symbol in the universe file.
    Symbols that error are skipped with a warning (one bad ticker must not
    kill a multi-hour run)."""
    from scanner import data

    syms = symbols or data.load_watchlist(universe_path)
    frames = data.fetch_daily(syms, period=period)
    trades: list[dict] = []
    for sym, df in frames.items():
        try:
            trades.extend(backtest(df, sym, max_hold=max_hold))
            print(f"  {sym}: done ({len(trades)} trades total)")
        except Exception as exc:
            print(f"  [warn] backtest failed for {sym}: {exc}")
    return trades


def main(argv=None) -> dict:
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser(description="Universe walk-forward backtest (Phase 0)")
    ap.add_argument("--universe", default="universe.csv")
    ap.add_argument("--period", default="5y")
    ap.add_argument("--max-hold", type=int, default=5)
    ap.add_argument("--out", default="out/backtest.json")
    ap.add_argument("--symbols", default=None,
                    help="comma-separated subset for a quick smoke run")
    args = ap.parse_args(argv)

    subset = args.symbols.split(",") if args.symbols else None
    trades = run_universe(args.universe, period=args.period,
                          max_hold=args.max_hold, symbols=subset)
    summary = {**summarize(trades), **extended_stats(trades)}
    doc = {"summary": summary, "trades": trades}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2))
    print(json.dumps(summary, indent=2))
    return doc


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_backtest.py -v`
Expected: all PASS (existing + 2 new).

- [ ] **Step 6: Smoke the CLI on two symbols** (uses network; skip if offline)

Run: `$env:PYTHONPATH="."; .venv\Scripts\python.exe -m scanner.backtest --symbols IYT,QQQ --period 2y --out out/backtest_smoke.json`
Expected: prints per-symbol progress then a JSON summary with `n`, `win_rate`, `expectancy_r`, `max_losing_streak`, `max_drawdown_r`.

Note: the full 150-name 5y run is O(bars²) per symbol and can take hours — that is the actual Phase 0 execution the owner runs overnight, not part of this plan's verification.

- [ ] **Step 7: Commit**

```bash
git add universe.csv scanner/backtest.py tests/test_backtest.py
git commit -m "feat: universe file + backtest extended stats + Phase 0 CLI"
```

---

### Task 4: Ledger — records, load/save, append_fired

**Files:**
- Create: `scanner/ledger.py`, `ledger/signals.jsonl` (empty file, checked in)
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: fired payload dicts from `scan.build_results()["fired"]` — keys used: `symbol, direction, date, close, atr, ema21, score`.
- Produces (used by Tasks 5–8, 10):
  - `SCHEMA_VERSION = 1`, `CLOSED = ("win", "loss", "time")`, `MAX_HOLD = 5`
  - `new_record(payload: dict) -> dict` — id `f"{symbol}-{date}"`, status `pending_entry`
  - `load(path: str | Path) -> list[dict]` — `[]` when file missing/empty
  - `save(path: str | Path, records: list[dict]) -> None` — one JSON object per line
  - `append_fired(records: list[dict], fired: list[dict]) -> list[dict]` — appends `new_record` for each fired payload whose id isn't already present; returns the same list.

- [ ] **Step 1: Write the failing tests** — create `tests/test_ledger.py`:

```python
"""Ledger tests. Frames are tiny synthetic OHLC frames so every close path is
deterministic. Trade model: entry = next open; target = entry + 2.5*ATR;
stop = entry - 1.5*ATR; time exit close of 5th bar; stop-before-target."""

import pandas as pd

from scanner import ledger


def make_frame(rows, start="2026-01-05"):
    """rows: list of [open, high, low, close]."""
    idx = pd.bdate_range(start, periods=len(rows))
    df = pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"])
    df["volume"] = 1000
    return df


def fired_payload(symbol="TST", date="2026-01-05", direction="bull",
                  close=100.0, atr=2.0, ema21=99.0, score=80.0):
    return {"symbol": symbol, "date": date, "direction": direction,
            "close": close, "atr": atr, "ema21": ema21, "score": score}


def test_new_record_shape():
    rec = ledger.new_record(fired_payload())
    assert rec["id"] == "TST-2026-01-05"
    assert rec["status"] == "pending_entry"
    assert rec["schema_version"] == 1
    assert rec["signal_close"] == 100.0 and rec["atr"] == 2.0
    assert rec["entry"] is None and rec["stop"] is None and rec["target"] is None
    assert rec["exit_price"] is None and rec["r_multiple"] is None


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "signals.jsonl"
    records = [ledger.new_record(fired_payload()),
               ledger.new_record(fired_payload(symbol="QQQ"))]
    ledger.save(path, records)
    assert ledger.load(path) == records


def test_load_missing_file_returns_empty(tmp_path):
    assert ledger.load(tmp_path / "nope.jsonl") == []


def test_append_fired_dedupes_by_id():
    records = []
    ledger.append_fired(records, [fired_payload()])
    ledger.append_fired(records, [fired_payload()])  # same signal again
    assert len(records) == 1
    ledger.append_fired(records, [fired_payload(date="2026-01-06")])
    assert len(records) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanner.ledger'` (or ImportError).

- [ ] **Step 3: Implement** — create `scanner/ledger.py`:

```python
"""Signal ledger: the live, public track record.

Append-oriented JSONL at ledger/signals.jsonl (committed — git history is the
tamper-evidence). Lifecycle: pending_entry -> open -> win|loss|time. Closed
records are never modified; `update` re-derives open records from all bars
after the signal date each run, so a missed day self-heals.

Trade math is imported from scanner.backtest (trade_levels/simulate_trade) —
the live record and the Phase 0 backtest are the same code path by design.
"""

import json
from pathlib import Path

SCHEMA_VERSION = 1
CLOSED = ("win", "loss", "time")
MAX_HOLD = 5
DEFAULT_PATH = "ledger/signals.jsonl"


def new_record(payload: dict) -> dict:
    """Create a ledger record from a fired scan payload."""
    return {
        "id": f"{payload['symbol']}-{payload['date']}",
        "schema_version": SCHEMA_VERSION,
        "symbol": payload["symbol"],
        "direction": payload["direction"],
        "signal_date": payload["date"],
        "signal_close": float(payload["close"]),
        "atr": float(payload["atr"]),
        "ema21": float(payload["ema21"]),
        "conviction_score": payload.get("score"),
        "telegram_msg_id": None,
        "status": "pending_entry",
        "entry": None, "entry_date": None,
        "stop": None, "target": None,
        "exit_price": None, "exit_date": None, "r_multiple": None,
    }


def load(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def save(path, records: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in records))


def append_fired(records: list[dict], fired: list[dict]) -> list[dict]:
    """Append a new record per fired payload, skipping ids already present."""
    known = {r["id"] for r in records}
    for payload in fired:
        rec = new_record(payload)
        if rec["id"] not in known:
            records.append(rec)
            known.add(rec["id"])
    return records
```

- [ ] **Step 4: Create the empty ledger file**

```powershell
New-Item -ItemType Directory -Force ledger | Out-Null; if (-not (Test-Path ledger/signals.jsonl)) { New-Item -ItemType File ledger/signals.jsonl }
```

- [ ] **Step 5: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ledger.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add scanner/ledger.py ledger/signals.jsonl tests/test_ledger.py
git commit -m "feat: signal ledger records, jsonl persistence, dedupe on append"
```

---

### Task 5: Ledger update — entry backfill, close paths, immutability, self-heal

**Files:**
- Modify: `scanner/ledger.py`
- Test: `tests/test_ledger.py` (append)

**Interfaces:**
- Consumes: `backtest.trade_levels(close, ema21, atr, entry, direction, mode="entry")` and `backtest.simulate_trade(bars, entry_price, target, stop, direction)`.
- Produces: `update(records: list[dict], frames: dict[str, pd.DataFrame]) -> list[dict]` — mutates and returns `records`. Rules:
  - `pending_entry` + a bar exists after `signal_date` → backfill `entry` (that bar's open), `entry_date`, compute `target`/`stop` via `trade_levels`, status → `open`.
  - Open records: evaluate the first `MAX_HOLD` bars from `entry_date` (inclusive) with `simulate_trade`; a `win`/`loss` outcome closes immediately; a `time` outcome closes only when ≥ `MAX_HOLD` bars exist, else stays `open`.
  - Closed records are returned untouched — asserted by test.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ledger.py`)

```python
# Shared scenario: signal on bar 1 (2026-01-05, close 100, atr 2, ema21 99).
# Entry = bar 2 open. With open=101: target = 101 + 5 = 106, stop = 101 - 3 = 98.

SIG = dict(symbol="TST", date="2026-01-05", direction="bull",
           close=100.0, atr=2.0, ema21=99.0, score=80.0)


def _ledger_with_signal():
    records = []
    ledger.append_fired(records, [dict(SIG)])
    return records


def test_update_stays_pending_when_no_bar_after_signal():
    records = _ledger_with_signal()
    frame = make_frame([[100, 101, 99, 100]])  # only the signal bar
    ledger.update(records, {"TST": frame})
    assert records[0]["status"] == "pending_entry"


def test_update_backfills_entry_and_levels():
    records = _ledger_with_signal()
    frame = make_frame([[100, 101, 99, 100],
                        [101, 102, 100, 101]])  # entry bar, no touch
    ledger.update(records, {"TST": frame})
    rec = records[0]
    assert rec["status"] == "open"
    assert rec["entry"] == 101.0 and rec["entry_date"] == "2026-01-06"
    assert rec["target"] == 106.0 and rec["stop"] == 98.0


def test_update_closes_win():
    records = _ledger_with_signal()
    frame = make_frame([[100, 101, 99, 100],
                        [101, 102, 100, 101],
                        [102, 107, 101, 106]])  # high 107 >= target 106
    ledger.update(records, {"TST": frame})
    rec = records[0]
    assert rec["status"] == "win"
    assert rec["exit_price"] == 106.0
    assert rec["exit_date"] == "2026-01-07"
    assert round(rec["r_multiple"], 3) == round(5 / 3, 3)


def test_update_closes_loss_stop_before_target_same_bar():
    records = _ledger_with_signal()
    # bar touches BOTH stop (low 97) and target (high 107) -> conservative loss
    frame = make_frame([[100, 101, 99, 100],
                        [101, 107, 97, 100]])
    ledger.update(records, {"TST": frame})
    rec = records[0]
    assert rec["status"] == "loss"
    assert rec["exit_price"] == 98.0
    assert rec["r_multiple"] == -1.0


def test_update_time_exit_after_five_bars():
    records = _ledger_with_signal()
    drift = [[101, 102, 100, 101],
             [101, 102, 100, 101],
             [101, 102, 100, 101],
             [101, 102, 100, 101],
             [101, 102, 100, 102]]  # 5 held bars, never touches 106/98
    frame = make_frame([[100, 101, 99, 100]] + drift)
    ledger.update(records, {"TST": frame})
    rec = records[0]
    assert rec["status"] == "time"
    assert rec["exit_price"] == 102.0  # close of 5th held bar
    assert rec["exit_date"] == "2026-01-12"


def test_update_stays_open_before_five_bars():
    records = _ledger_with_signal()
    frame = make_frame([[100, 101, 99, 100],
                        [101, 102, 100, 101],
                        [101, 102, 100, 101]])  # only 2 held bars
    ledger.update(records, {"TST": frame})
    assert records[0]["status"] == "open"


def test_update_never_mutates_closed_records():
    records = _ledger_with_signal()
    win_frame = make_frame([[100, 101, 99, 100],
                            [101, 102, 100, 101],
                            [102, 107, 101, 106]])
    ledger.update(records, {"TST": win_frame})
    closed = dict(records[0])
    # new data that would have hit the stop if re-evaluated
    crash = make_frame([[100, 101, 99, 100],
                        [101, 102, 100, 101],
                        [102, 107, 101, 106],
                        [90, 91, 85, 86]])
    ledger.update(records, {"TST": crash})
    assert records[0] == closed


def test_update_self_heals_across_missed_days():
    # One update call sees 3 days at once (simulating a missed run) and still
    # lands on the correct outcome.
    records = _ledger_with_signal()
    frame = make_frame([[100, 101, 99, 100],
                        [101, 102, 100, 101],
                        [101, 102, 99.5, 100],
                        [100, 107, 99, 106]])  # win on 3rd held bar
    ledger.update(records, {"TST": frame})
    assert records[0]["status"] == "win"
    assert records[0]["exit_date"] == "2026-01-08"


def test_update_missing_frame_is_skipped():
    records = _ledger_with_signal()
    ledger.update(records, {})  # symbol absent (fetch failed today)
    assert records[0]["status"] == "pending_entry"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ledger.py -v -k update`
Expected: FAIL with `AttributeError: module 'scanner.ledger' has no attribute 'update'`

- [ ] **Step 3: Implement** — append to `scanner/ledger.py`:

```python
import pandas as pd

from scanner import backtest


def update(records: list[dict], frames: dict) -> list[dict]:
    """Backfill entries and close positions from the latest bars.

    Re-derives each non-closed record from ALL bars after its signal date, so a
    missed run self-heals on the next one. Closed records are never touched.
    """
    for rec in records:
        if rec["status"] in CLOSED:
            continue
        df = frames.get(rec["symbol"])
        if df is None or df.empty:
            continue
        after = df[df.index > pd.Timestamp(rec["signal_date"])]
        if after.empty:
            continue

        if rec["status"] == "pending_entry":
            rec["entry"] = round(float(after["open"].iloc[0]), 4)
            rec["entry_date"] = after.index[0].strftime("%Y-%m-%d")
            target, stop = backtest.trade_levels(
                close=rec["signal_close"], ema21=rec["ema21"], atr=rec["atr"],
                entry=rec["entry"], direction=rec["direction"], mode="entry",
            )
            rec["target"], rec["stop"] = round(target, 4), round(stop, 4)
            rec["status"] = "open"

        hold = after.iloc[:MAX_HOLD][["high", "low", "close"]]
        result = backtest.simulate_trade(
            hold, rec["entry"], rec["target"], rec["stop"], rec["direction"]
        )
        if result["outcome"] in ("win", "loss") or len(hold) >= MAX_HOLD:
            rec["status"] = result["outcome"]
            rec["exit_price"] = round(result["exit_price"], 4)
            rec["exit_date"] = after.index[result["bars_held"] - 1].strftime("%Y-%m-%d")
            rec["r_multiple"] = round(result["r_multiple"], 3)
    return records
```

(Put the `import pandas as pd` / `from scanner import backtest` lines at the top of the module with the existing imports, not mid-file.)

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ledger.py -v`
Expected: all PASS (13 total).

- [ ] **Step 5: Commit**

```bash
git add scanner/ledger.py tests/test_ledger.py
git commit -m "feat: ledger update - entry backfill, close paths, immutability, self-heal"
```

---

### Task 6: Ledger stats

**Files:**
- Modify: `scanner/ledger.py`
- Test: `tests/test_ledger.py` (append)

**Interfaces:**
- Consumes: ledger records.
- Produces: `stats(records: list[dict]) -> dict` with keys:
  `n_closed: int`, `n_open: int` (open + pending_entry), `wins: int`, `losses: int`, `time_exits: int`, `win_rate: float | None` (wins / n_closed), `avg_r: float | None`, `total_r: float`, `max_losing_streak: int`, `equity_curve: list[[str, float]]` (pairs of `[exit_date, cumulative_r]`, closed trades ordered by `exit_date`). Used by `trackrecord.render_site` (Task 7) and `recap.render_card` (Task 8).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ledger.py`)

```python
def _closed(id_, exit_date, r, outcome):
    return {"id": id_, "schema_version": 1, "symbol": id_.split("-")[0],
            "direction": "bull", "signal_date": "2026-01-05",
            "signal_close": 100.0, "atr": 2.0, "ema21": 99.0,
            "conviction_score": 80.0, "telegram_msg_id": None,
            "status": outcome, "entry": 101.0, "entry_date": "2026-01-06",
            "stop": 98.0, "target": 106.0, "exit_price": 101.0 + 3 * r,
            "exit_date": exit_date, "r_multiple": r}


def test_stats_full():
    records = [
        _closed("A-1", "2026-01-08", 1.667, "win"),
        _closed("B-1", "2026-01-09", -1.0, "loss"),
        _closed("C-1", "2026-01-12", -1.0, "loss"),
        _closed("D-1", "2026-01-14", 0.3, "time"),
        ledger.new_record(fired_payload(symbol="E")),  # open-ish, excluded
    ]
    s = ledger.stats(records)
    assert s["n_closed"] == 4 and s["n_open"] == 1
    assert s["wins"] == 1 and s["losses"] == 2 and s["time_exits"] == 1
    assert s["win_rate"] == 0.25
    assert round(s["avg_r"], 4) == round((1.667 - 1 - 1 + 0.3) / 4, 4)
    assert round(s["total_r"], 3) == -0.033
    assert s["max_losing_streak"] == 2
    assert s["equity_curve"][0] == ["2026-01-08", 1.667]
    assert s["equity_curve"][-1][0] == "2026-01-14"


def test_stats_empty():
    s = ledger.stats([])
    assert s["n_closed"] == 0 and s["win_rate"] is None and s["equity_curve"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ledger.py -v -k stats`
Expected: FAIL with `AttributeError: ... no attribute 'stats'`

- [ ] **Step 3: Implement** — append to `scanner/ledger.py`:

```python
def stats(records: list[dict]) -> dict:
    """Aggregate the ledger for the track-record site and recap card."""
    closed = sorted((r for r in records if r["status"] in CLOSED),
                    key=lambda r: (r["exit_date"], r["id"]))
    n = len(closed)
    wins = sum(1 for r in closed if r["status"] == "win")
    losses = sum(1 for r in closed if r["status"] == "loss")
    time_exits = sum(1 for r in closed if r["status"] == "time")

    curve, cum, streak, max_streak = [], 0.0, 0, 0
    for r in closed:
        cum += r["r_multiple"]
        curve.append([r["exit_date"], round(cum, 3)])
        streak = streak + 1 if r["r_multiple"] < 0 else 0
        max_streak = max(max_streak, streak)

    return {
        "n_closed": n,
        "n_open": len(records) - n,
        "wins": wins, "losses": losses, "time_exits": time_exits,
        "win_rate": (wins / n) if n else None,
        "avg_r": (sum(r["r_multiple"] for r in closed) / n) if n else None,
        "total_r": round(cum, 3),
        "max_losing_streak": max_streak,
        "equity_curve": curve,
    }
```

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ledger.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/ledger.py tests/test_ledger.py
git commit -m "feat: ledger stats - win rate, avg R, equity curve, losing streak"
```

---

### Task 7: Track-record static site generator

**Files:**
- Create: `scanner/trackrecord.py`
- Test: `tests/test_trackrecord.py`

**Interfaces:**
- Consumes: `ledger.load(path)`, `ledger.stats(records)`, record fields.
- Produces:
  - `render_site(records: list[dict], out_dir: str | Path, channel_username: str | None = None, channel_url: str | None = None) -> None` — writes `index.html`, `signals.html`, `methodology.html`, `style.css` into `out_dir`, and copies the ledger content to `out_dir / "signals.jsonl"` for public verification. When `channel_username` is set, each signal row with a `telegram_msg_id` links to `https://t.me/{channel_username}/{msg_id}`.
  - CLI: `python -m scanner.trackrecord --ledger ledger/signals.jsonl --out site [--channel-username sqzdots] [--channel-url https://t.me/sqzdots]`

- [ ] **Step 1: Write the failing tests** — create `tests/test_trackrecord.py`:

```python
from scanner import trackrecord


def _rec(id_="IYT-2026-01-05", status="win", msg_id=123):
    return {"id": id_, "schema_version": 1, "symbol": id_.split("-")[0],
            "direction": "bull", "signal_date": "2026-01-05",
            "signal_close": 100.0, "atr": 2.0, "ema21": 99.0,
            "conviction_score": 82.0, "telegram_msg_id": msg_id,
            "status": status, "entry": 101.0, "entry_date": "2026-01-06",
            "stop": 98.0, "target": 106.0,
            "exit_price": 106.0 if status == "win" else None,
            "exit_date": "2026-01-08" if status != "open" else None,
            "r_multiple": 1.667 if status == "win" else None}


def test_render_site_writes_all_pages(tmp_path):
    trackrecord.render_site([_rec()], tmp_path)
    for name in ("index.html", "signals.html", "methodology.html",
                 "style.css", "signals.jsonl"):
        assert (tmp_path / name).exists(), name


def test_index_shows_headline_stats(tmp_path):
    trackrecord.render_site([_rec()], tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Sqzdots Indicator" in html
    assert "100.0%" in html          # win rate: 1 of 1
    assert "1.667" in html           # avg / total R visible


def test_signals_page_lists_every_record_with_receipt_link(tmp_path):
    losing = _rec(id_="QQQ-2026-01-05", status="loss", msg_id=456)
    losing["exit_price"], losing["r_multiple"] = 98.0, -1.0
    trackrecord.render_site([_rec(), losing], tmp_path,
                            channel_username="sqzdots")
    html = (tmp_path / "signals.html").read_text(encoding="utf-8")
    assert "IYT" in html and "QQQ" in html            # losses are never hidden
    assert "https://t.me/sqzdots/123" in html          # timestamped receipt


def test_methodology_contains_disclaimer_and_trade_model(tmp_path):
    trackrecord.render_site([], tmp_path)
    html = (tmp_path / "methodology.html").read_text(encoding="utf-8")
    assert "Educational tool, not investment advice." in html
    assert "2.5" in html and "1.5" in html   # ATR multiples stated plainly


def test_open_records_shown_as_open(tmp_path):
    trackrecord.render_site([_rec(status="open", msg_id=None)], tmp_path)
    html = (tmp_path / "signals.html").read_text(encoding="utf-8")
    assert "open" in html.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_trackrecord.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — create `scanner/trackrecord.py`:

```python
"""Public track-record site generator.

Plain fast HTML/CSS regenerated from the ledger on every run — no framework,
must load instantly on a phone. Every signal ever fired is listed, losses
included; each row links to its timestamped Telegram post (the receipt).
"""

import argparse
import html
import json
from pathlib import Path

from scanner import ledger

BRAND = "Sqzdots Indicator"
DISCLAIMER = ("Educational tool, not investment advice. "
              "Past performance does not guarantee future results.")

CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0 auto;
       max-width: 780px; padding: 1rem; line-height: 1.5; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; }
nav a { margin-right: 1rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
th, td { text-align: left; padding: 0.3rem 0.5rem; border-bottom: 1px solid #8884; }
.win { color: #1a7f37; } .loss { color: #cf222e; } .time { color: #9a6700; }
.stats { display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 1rem 0; }
.stat b { display: block; font-size: 1.3rem; }
footer { margin-top: 2rem; font-size: 0.75rem; opacity: 0.7; }
svg { max-width: 100%; }
"""


def _esc(x) -> str:
    return html.escape(str(x))


def _page(title: str, body: str, channel_url: str | None) -> str:
    channel = f'<a href="{_esc(channel_url)}">Telegram channel</a>' if channel_url else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} — {BRAND}</title><link rel="stylesheet" href="style.css"></head>
<body><nav><a href="index.html">Record</a><a href="signals.html">All signals</a>
<a href="methodology.html">Methodology</a>{channel}</nav>
{body}
<footer>{_esc(DISCLAIMER)} · Ledger: <a href="signals.jsonl">signals.jsonl</a></footer>
</body></html>"""


def _equity_svg(curve: list) -> str:
    """Cumulative-R sparkline as inline SVG. Empty string when < 2 points."""
    if len(curve) < 2:
        return ""
    ys = [pt[1] for pt in curve]
    lo, hi = min(ys + [0.0]), max(ys + [0.0])
    span = (hi - lo) or 1.0
    w, h = 700, 160
    step = w / (len(ys) - 1)
    pts = " ".join(f"{i * step:.1f},{h - (y - lo) / span * h:.1f}"
                   for i, y in enumerate(ys))
    zero_y = h - (0.0 - lo) / span * h
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="equity curve in R">'
            f'<line x1="0" y1="{zero_y:.1f}" x2="{w}" y2="{zero_y:.1f}" '
            f'stroke="#8888" stroke-dasharray="4"/>'
            f'<polyline points="{pts}" fill="none" stroke="#2f81f7" stroke-width="2"/></svg>')


def _row(rec: dict, channel_username: str | None) -> str:
    status = rec["status"]
    r = rec["r_multiple"]
    r_txt = f"{r:+.2f}R" if r is not None else "—"
    receipt = ""
    if channel_username and rec.get("telegram_msg_id"):
        receipt = (f'<a href="https://t.me/{_esc(channel_username)}/'
                   f'{rec["telegram_msg_id"]}">post</a>')
    return (f'<tr><td>{_esc(rec["signal_date"])}</td>'
            f'<td><b>{_esc(rec["symbol"])}</b></td>'
            f'<td>{_esc(rec["direction"])}</td>'
            f'<td>{rec["entry"] if rec["entry"] is not None else "—"}</td>'
            f'<td>{rec["stop"] if rec["stop"] is not None else "—"}</td>'
            f'<td>{rec["target"] if rec["target"] is not None else "—"}</td>'
            f'<td class="{_esc(status)}">{_esc(status)}</td>'
            f'<td>{r_txt}</td><td>{receipt}</td></tr>')


def render_site(records: list[dict], out_dir, channel_username: str | None = None,
                channel_url: str | None = None) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    s = ledger.stats(records)

    win_rate = f"{s['win_rate'] * 100:.1f}%" if s["win_rate"] is not None else "—"
    avg_r = f"{s['avg_r']:+.3f}" if s["avg_r"] is not None else "—"
    index_body = f"""<h1>{BRAND}</h1>
<p>A systematic squeeze scanner that publishes every signal it fires — wins and
losses — before the move happens. This page regenerates automatically from the
signal ledger after every scan.</p>
<div class="stats">
<div class="stat"><b>{s['n_closed']}</b>closed signals</div>
<div class="stat"><b>{win_rate}</b>win rate</div>
<div class="stat"><b>{avg_r}</b>avg R</div>
<div class="stat"><b>{s['total_r']:+.3f}</b>total R</div>
<div class="stat"><b>{s['max_losing_streak']}</b>worst losing streak</div>
<div class="stat"><b>{s['n_open']}</b>open</div>
</div>
{_equity_svg(s['equity_curve'])}
<p>This system has historically had losing streaks; the edge is in the average.
See <a href="methodology.html">methodology</a> for the exact rules and
<a href="signals.html">all signals</a> for the full record.</p>"""

    rows = "".join(_row(r, channel_username)
                   for r in sorted(records, key=lambda r: r["signal_date"], reverse=True))
    signals_body = f"""<h1>All signals</h1>
<p>Every signal ever fired, newest first. Nothing is removed or edited after the
fact — the <a href="signals.jsonl">raw ledger</a> and its git history are public.</p>
<table><tr><th>Signal</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Stop</th>
<th>Target</th><th>Status</th><th>R</th><th>Receipt</th></tr>{rows}</table>"""

    methodology_body = f"""<h1>Methodology</h1>
<p>BUY when ALL of: squeeze ON · RSI &gt; 50 · PPO ≥ 0 · EMA8 &gt; EMA21 ·
full EMA stack (8&gt;21&gt;34, 50&gt;200) · MACD green (rising, ≥ 0) ·
weekly Moxie &gt; 0 and rising. SELL is the strict mirror. Signals are computed
once daily on completed bars — never intraday, never revised.</p>
<h2>Trade model (fixed, mechanical)</h2>
<p>Entry: next day's open after the signal. Target: entry + 2.5 × ATR(14).
Stop: entry − 1.5 × ATR(14). Time exit: close of the 5th bar if neither level
is touched. When one bar touches both stop and target, it counts as a stop
(conservative). The public record and our backtests use the same code.</p>
<h2>Disclaimer</h2><p>{_esc(DISCLAIMER)}</p>"""

    (out / "style.css").write_text(CSS, encoding="utf-8")
    (out / "index.html").write_text(_page("Record", index_body, channel_url), encoding="utf-8")
    (out / "signals.html").write_text(_page("All signals", signals_body, channel_url), encoding="utf-8")
    (out / "methodology.html").write_text(_page("Methodology", methodology_body, channel_url), encoding="utf-8")
    (out / "signals.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Regenerate the track-record site")
    ap.add_argument("--ledger", default=ledger.DEFAULT_PATH)
    ap.add_argument("--out", default="site")
    ap.add_argument("--channel-username", default=None)
    ap.add_argument("--channel-url", default=None)
    args = ap.parse_args(argv)
    render_site(ledger.load(args.ledger), args.out,
                channel_username=args.channel_username, channel_url=args.channel_url)
    print(f"site written to {args.out}/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_trackrecord.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Eyeball it once**

Run: `$env:PYTHONPATH="."; .venv\Scripts\python.exe -m scanner.trackrecord --ledger ledger/signals.jsonl --out out/site_preview`
Open `out\site_preview\index.html` in a browser. Expected: brand header, zeroed stats, methodology page renders. (out/ is generated content; `out/site_preview` need not be committed.)

- [ ] **Step 6: Commit**

```bash
git add scanner/trackrecord.py tests/test_trackrecord.py
git commit -m "feat: public track-record static site generator"
```

---

### Task 8: Weekly recap card

**Files:**
- Create: `scanner/recap.py`
- Test: `tests/test_recap.py`

**Interfaces:**
- Consumes: `ledger.load`, `ledger.stats`, `notify.send_photo(token, chat_id, photo_path, caption)`.
- Produces:
  - `week_slice(records: list[dict], week_ending: str) -> dict` — `{"fired": [...], "closed": [...]}` for the 7 days ending `week_ending` (inclusive; `fired` by `signal_date`, `closed` by `exit_date`).
  - `render_card(records: list[dict], week_ending: str, out_path: str | Path) -> str` — 1200×675 PNG, returns the path.
  - CLI: `python -m scanner.recap --ledger ledger/signals.jsonl --out out/recap.png [--week-ending YYYY-MM-DD] [--dry-run]` — sends to `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` unless `--dry-run`; `--week-ending` defaults to today (UTC).

- [ ] **Step 1: Write the failing tests** — create `tests/test_recap.py`:

```python
from scanner import recap


def _rec(id_, signal_date, exit_date=None, r=None, status="open"):
    return {"id": id_, "schema_version": 1, "symbol": id_.split("-")[0],
            "direction": "bull", "signal_date": signal_date,
            "signal_close": 100.0, "atr": 2.0, "ema21": 99.0,
            "conviction_score": 80.0, "telegram_msg_id": None,
            "status": status, "entry": 101.0, "entry_date": signal_date,
            "stop": 98.0, "target": 106.0,
            "exit_price": 106.0 if status == "win" else None,
            "exit_date": exit_date, "r_multiple": r}


def test_week_slice_filters_by_dates():
    records = [
        _rec("A-1", "2026-06-22", "2026-06-25", 1.5, "win"),    # in week
        _rec("B-1", "2026-06-24"),                               # fired in week
        _rec("C-1", "2026-06-10", "2026-06-12", -1.0, "loss"),  # old
    ]
    wk = recap.week_slice(records, week_ending="2026-06-28")
    assert [r["id"] for r in wk["fired"]] == ["A-1", "B-1"]
    assert [r["id"] for r in wk["closed"]] == ["A-1"]


def test_render_card_writes_png(tmp_path):
    records = [_rec("A-1", "2026-06-22", "2026-06-25", 1.5, "win")]
    out = tmp_path / "recap.png"
    path = recap.render_card(records, "2026-06-28", out)
    assert out.exists() and out.stat().st_size > 5000
    assert str(out) == path


def test_render_card_handles_empty_week(tmp_path):
    out = tmp_path / "recap.png"
    recap.render_card([], "2026-06-28", out)
    assert out.exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recap.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — create `scanner/recap.py`:

```python
"""Weekly recap card: one shareable PNG, auto-posted every Sunday.

This is the word-of-mouth artifact — members forward it. Machine-made,
zero operator effort. 1200x675 (Telegram/Twitter-friendly)."""

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from scanner import ledger

BRAND = "Sqzdots Indicator"


def week_slice(records: list[dict], week_ending: str) -> dict:
    end = datetime.strptime(week_ending, "%Y-%m-%d").date()
    start = end - timedelta(days=6)

    def in_week(d):
        return d is not None and start <= datetime.strptime(d, "%Y-%m-%d").date() <= end

    return {
        "fired": [r for r in records if in_week(r["signal_date"])],
        "closed": [r for r in records if r["status"] in ledger.CLOSED
                   and in_week(r["exit_date"])],
    }


def render_card(records: list[dict], week_ending: str, out_path) -> str:
    wk = week_slice(records, week_ending)
    s = ledger.stats(records)
    fig = plt.figure(figsize=(12, 6.75), dpi=100)
    fig.patch.set_facecolor("#0d1117")
    ax = fig.add_axes([0.55, 0.12, 0.4, 0.35])

    def txt(x, y, string, size=16, color="#e6edf3", weight="normal"):
        fig.text(x, y, string, fontsize=size, color=color, weight=weight)

    txt(0.05, 0.90, BRAND, 26, "#2f81f7", "bold")
    txt(0.05, 0.84, f"Week ending {week_ending}", 14, "#8b949e")

    lines = []
    for r in wk["closed"]:
        mark = {"win": "+", "loss": "-", "time": "~"}[r["status"]]
        lines.append(f"{mark} {r['symbol']}  {r['r_multiple']:+.2f}R ({r['status']})")
    for r in wk["fired"]:
        if r["status"] not in ledger.CLOSED:
            lines.append(f"* {r['symbol']}  fired {r['signal_date']} ({r['status']})")
    if not lines:
        lines = ["No signals this week — 0 fired.",
                 "The squeeze is a waiting game; the scan ran every day."]
    for i, line in enumerate(lines[:9]):
        txt(0.05, 0.74 - i * 0.055, line, 15)

    win_rate = f"{s['win_rate'] * 100:.0f}%" if s["win_rate"] is not None else "—"
    avg_r = f"{s['avg_r']:+.2f}" if s["avg_r"] is not None else "—"
    txt(0.55, 0.84, "Running record", 14, "#8b949e")
    txt(0.55, 0.74, f"{s['n_closed']} closed · {win_rate} win rate · "
                    f"avg {avg_r}R · total {s['total_r']:+.1f}R", 15)

    curve = s["equity_curve"]
    if len(curve) >= 2:
        ys = [pt[1] for pt in curve]
        ax.plot(range(len(ys)), ys, color="#2f81f7", linewidth=2)
        ax.axhline(0, color="#8b949e", linewidth=0.7, linestyle="--")
        ax.set_facecolor("#0d1117")
        for spine in ax.spines.values():
            spine.set_color("#30363d")
        ax.tick_params(colors="#8b949e", labelsize=8)
        ax.set_title("Cumulative R", color="#8b949e", fontsize=10)
    else:
        ax.axis("off")

    txt(0.05, 0.04, "Every signal published before the move — wins and losses. "
                    "Educational tool, not investment advice.", 10, "#8b949e")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=fig.get_facecolor())
    plt.close(fig)
    return str(out)


def main(argv=None) -> str:
    from scanner import notify

    ap = argparse.ArgumentParser(description="Render + post the weekly recap card")
    ap.add_argument("--ledger", default=ledger.DEFAULT_PATH)
    ap.add_argument("--out", default="out/recap.png")
    ap.add_argument("--week-ending",
                    default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    records = ledger.load(args.ledger)
    path = render_card(records, args.week_ending, args.out)
    print(f"recap card written to {path}")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if args.dry_run or not (token and chat_id):
        print("[not sending: dry-run or missing TELEGRAM env]")
        return path
    notify.send_photo(token, chat_id, path,
                      caption=f"<b>{BRAND}</b> — weekly recap, week ending "
                              f"{args.week_ending}")
    print("[recap sent]")
    return path


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recap.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/recap.py tests/test_recap.py
git commit -m "feat: weekly recap card renderer + Telegram post CLI"
```

---

### Task 9: Notify tiers — provisional levels, no-fire message, footer; watching_detail

**Files:**
- Modify: `scanner/notify.py`, `scanner/scan.py`
- Test: `tests/test_notify.py`, `tests/test_scan.py` (append)

**Interfaces:**
- Consumes: payload keys `lit_bull`/`lit_bear` (Task 2).
- Produces:
  - `scan.build_results` adds `"watching_detail": list[dict]` — `{"symbol": str, "lit": int, "lean": "bull"|"bear"}` sorted by `lit` desc, for payloads with `direction == "none"` and `squeeze_on`. (`watching` symbol list stays for dashboard back-compat.)
  - `notify.format_message(results, footer=None)` — when fired: `_fired_line` shows provisional entry-anchored levels when `prov_target`/`prov_stop` present on the payload (set by run.py in Task 10), labeled "finalize at next open". When nothing fired: the quiet-day message — "Scanned N names. 0 fired." + up to 12 building squeezes + "Closest to trigger: SYM (k/7 conditions lit, leaning bull)". `footer` (a string) is appended as a final line when provided.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notify.py`:

```python
def test_fired_line_prefers_provisional_levels():
    p = _p("IYT", "bull")
    p["prov_target"], p["prov_stop"] = 105.0, 97.0
    msg = notify.format_message(_results([p]))
    assert "105.00" in msg and "97.00" in msg
    assert "next open" in msg.lower()


def test_no_fire_message_shows_building_squeezes():
    results = _results([], watching=["QQQ", "SPY"])
    results["watching_detail"] = [
        {"symbol": "QQQ", "lit": 6, "lean": "bull"},
        {"symbol": "SPY", "lit": 4, "lean": "bear"},
    ]
    msg = notify.format_message(results)
    assert "0 fired" in msg
    assert "QQQ" in msg
    assert "6/7" in msg and "bull" in msg


def test_footer_appended_when_provided():
    msg = notify.format_message(_results([]), footer="Track record: https://example.com")
    assert msg.rstrip().endswith("Track record: https://example.com")
```

Append to `tests/test_scan.py` (reuse its existing payload helper if one exists; otherwise this standalone works):

```python
def test_build_results_watching_detail_sorted_by_lit():
    payloads = [
        {"symbol": "AAA", "direction": "none", "squeeze_on": True,
         "lit_bull": 3, "lit_bear": 5, "score": 0},
        {"symbol": "BBB", "direction": "none", "squeeze_on": True,
         "lit_bull": 6, "lit_bear": 1, "score": 0},
        {"symbol": "CCC", "direction": "none", "squeeze_on": False,
         "lit_bull": 6, "lit_bear": 1, "score": 0},
    ]
    results = scan.build_results(payloads, as_of="2026-07-03")
    detail = results["watching_detail"]
    assert [d["symbol"] for d in detail] == ["BBB", "AAA"]  # CCC not squeezing
    assert detail[0] == {"symbol": "BBB", "lit": 6, "lean": "bull"}
    assert detail[1]["lean"] == "bear"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_notify.py tests/test_scan.py -v`
Expected: the 4 new tests FAIL.

- [ ] **Step 3: Implement `scan.build_results`** — in `scanner/scan.py`, replace the `return` block of `build_results` with:

```python
    watch_payloads = [
        p for p in payloads if p["direction"] == "none" and p.get("squeeze_on")
    ]
    watching_detail = sorted(
        (
            {
                "symbol": p["symbol"],
                "lit": max(p.get("lit_bull", 0), p.get("lit_bear", 0)),
                "lean": "bull" if p.get("lit_bull", 0) >= p.get("lit_bear", 0) else "bear",
            }
            for p in watch_payloads
        ),
        key=lambda d: -d["lit"],
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of,
        "universe": len(payloads),
        "fired_count": len(fired),
        "fired": fired,
        "watching": [d["symbol"] for d in watching_detail],
        "watching_detail": watching_detail,
    }
```

(The old `watching` list comprehension above the return is now redundant — delete it.)

- [ ] **Step 4: Implement notify changes** — in `scanner/notify.py`:

In `_fired_line`, replace the `target/stop` line with a provisional-aware version:

```python
    if p.get("prov_target") is not None:
        levels = (f"   target {p['prov_target']:.2f} · stop {p['prov_stop']:.2f}"
                  f" (finalize at next open)")
    else:
        levels = (f"   target {p['target_up']:.2f} / {p['target_dn']:.2f}"
                  f" · stop {p['stop']:.2f}")
    return (
        f"{head}\n"
        f"   close {p['close']:.2f} · RSI {p['rsi']:.0f}\n"
        f"{levels}"
        f"{tail}"
    )
```

Replace `format_message` with:

```python
def format_message(results: dict, footer: str | None = None) -> str:
    """Build the HTML message body for a results document."""
    lines = [f"<b>Sqzdots Scan</b> — bar {_esc(results['as_of'])}"]
    fired = results.get("fired", [])

    if fired:
        lines.append(f"{len(fired)} signal(s) fired:")
        lines.append("")
        lines.extend(_fired_line(p) for p in fired)
        watching = results.get("watching", [])
        if watching:
            lines.append("")
            lines.append("👀 Coiled (in squeeze, not yet aligned):")
            lines.append(_esc(", ".join(watching)))
    else:
        lines.append(f"Scanned {results.get('universe', 0)} names. 0 fired.")
        detail = results.get("watching_detail", [])
        # older results.json files have only the plain `watching` symbol list
        names = [d["symbol"] for d in detail] or results.get("watching", [])
        if names:
            lines.append(f"{len(names)} squeezes building: "
                         f"{_esc(', '.join(names[:12]))}")
            if detail:
                top = detail[0]
                lines.append(f"Closest to trigger: <b>{_esc(top['symbol'])}</b> "
                             f"({top['lit']}/7 conditions lit, leaning {_esc(top['lean'])})")
        else:
            lines.append("No squeezes building today.")

    if footer:
        lines.append("")
        lines.append(_esc(footer))
    return "\n".join(lines)
```

- [ ] **Step 5: Update the one existing test whose wording assertion no longer matches**

The no-fire header changed from "No signals fired today." to "Scanned N names. 0 fired." In `tests/test_notify.py`, `test_message_handles_no_fires` asserts `"no" in msg.lower()` — update that one assertion (the `"QQQ"` assertion still passes via the `watching` fallback):

```python
def test_message_handles_no_fires():
    msg = notify.format_message(_results([], watching=["QQQ"]))
    assert "0 fired" in msg           # quiet-day header
    assert "QQQ" in msg               # still shows what's coiled
```

Then run the full suite:

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scanner/notify.py scanner/scan.py tests/test_notify.py tests/test_scan.py
git commit -m "feat: quiet-day message, provisional levels, footer, watching_detail"
```

---

### Task 10: Wire ledger + site into the daily run

**Files:**
- Modify: `scanner/run.py`
- Test: `tests/test_run.py` (create)

**Interfaces:**
- Consumes: `ledger.load/append_fired/update/save/DEFAULT_PATH`, `trackrecord.render_site`, `backtest.trade_levels`, `notify.send_photo` return body (`body["result"]["message_id"]`).
- Produces: `run.main(argv)` new flags: `--ledger ledger/signals.jsonl`, `--site site`, `--no-site`. Order of operations: scan → results → provisional levels on fired payloads → ledger load/append/update → send alerts (capture `telegram_msg_id` into the matching ledger record) → ledger save → site render → results.json written (as before). Env: `TELEGRAM_FOOTER` (optional) passed to `format_message`; `SITE_CHANNEL_USERNAME`, `SITE_CHANNEL_URL` (optional) passed to `render_site`.

- [ ] **Step 1: Write the failing test** — create `tests/test_run.py`:

```python
"""End-to-end dry-run smoke test: fixture frames in, ledger + site out.
Network and Telegram are monkeypatched away."""

import json

import pandas as pd

from scanner import data, run


def _fixture_frames():
    frames = {}
    for sym in ("IYT", "QQQ", "RSP", "DIA", "XLRE"):
        df = pd.read_csv(f"tests/fixtures/{sym}.csv", index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]
        frames[sym] = df[["open", "high", "low", "close", "volume"]]
    return frames


def test_dry_run_writes_ledger_and_site(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "fetch_daily", lambda *a, **k: _fixture_frames())
    ledger_path = tmp_path / "signals.jsonl"
    site_dir = tmp_path / "site"
    out_dir = tmp_path / "out"

    results = run.main([
        "--dry-run", "--no-charts",
        "--watchlist", "watchlist.csv",          # symbols overridden by monkeypatch
        "--out", str(out_dir),
        "--ledger", str(ledger_path),
        "--site", str(site_dir),
    ])

    assert (out_dir / "results.json").exists()
    assert ledger_path.exists()                   # ledger saved even with 0 fires
    assert (site_dir / "index.html").exists()     # site regenerated every run
    # fired payloads (if any) carry provisional levels
    for p in results["fired"]:
        assert "prov_target" in p and "prov_stop" in p


def test_no_site_flag_skips_site(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "fetch_daily", lambda *a, **k: _fixture_frames())
    site_dir = tmp_path / "site"
    run.main(["--dry-run", "--no-charts", "--no-site",
              "--out", str(tmp_path / "out"),
              "--ledger", str(tmp_path / "signals.jsonl"),
              "--site", str(site_dir)])
    assert not site_dir.exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_run.py -v`
Expected: FAIL (`unrecognized arguments: --ledger`).

- [ ] **Step 3: Implement** — modify `scanner/run.py`:

Imports become:

```python
from scanner import backtest, chart, data, ledger, notify, scan, trackrecord
```

Add arguments after the existing ones:

```python
    ap.add_argument("--ledger", default=ledger.DEFAULT_PATH)
    ap.add_argument("--site", default="site")
    ap.add_argument("--no-site", action="store_true")
```

After `results = scan.build_results(...)`, insert:

```python
    # Provisional entry-anchored levels for the alert (finalized at next open).
    for p in results["fired"]:
        target, stop = backtest.trade_levels(
            close=p["close"], ema21=p["ema21"], atr=p["atr"],
            entry=p["close"], direction=p["direction"], mode="entry",
        )
        p["prov_target"], p["prov_stop"] = round(target, 2), round(stop, 2)

    # Ledger: record new fires, backfill entries, close finished positions.
    records = ledger.load(args.ledger)
    ledger.append_fired(records, results["fired"])
    ledger.update(records, frames)
```

Change the message construction line to pass the footer:

```python
    message = notify.format_message(results, footer=os.environ.get("TELEGRAM_FOOTER"))
```

Replace the Telegram send block's photo loop so message ids are captured, and
add ledger save + site render AFTER the send attempt (so msg ids land in the
saved ledger, and a send failure still saves/renders — wrap sends in the
existing try/except):

```python
    try:
        by_id = {r["id"]: r for r in records}
        for p in results["fired"]:
            cpath = out_dir / "charts" / f"{p['symbol']}.png"
            if cpath.exists():
                body = notify.send_photo(token, chat_id, str(cpath),
                                         caption=notify._fired_line(p))
                rec = by_id.get(f"{p['symbol']}-{p['date']}")
                if rec is not None and rec.get("telegram_msg_id") is None:
                    rec["telegram_msg_id"] = body["result"]["message_id"]
        notify.send_message(token, chat_id, message)
        print(f"[sent to Telegram chat {chat_id}]")
    except Exception as exc:
        print(f"[telegram send FAILED: {exc}]")
        print("[hint: open YOUR bot in Telegram and tap Start, and check the secrets]")
```

Finally, before BOTH `return results` exits (the dry-run early return and the
end of `main`), persist ledger + site. To avoid duplication, do it just before
the dry-run return and let the send path fall through to the same code — i.e.
restructure the tail of `main` to:

```python
    def _persist():
        ledger.save(args.ledger, records)
        if not args.no_site:
            trackrecord.render_site(
                records, args.site,
                channel_username=os.environ.get("SITE_CHANNEL_USERNAME"),
                channel_url=os.environ.get("SITE_CHANNEL_URL"),
            )

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if args.dry_run or not (token and chat_id):
        reason = "dry-run" if args.dry_run else "no TELEGRAM_BOT_TOKEN/CHAT_ID set"
        print(f"[not sending: {reason}]")
        _persist()
        return results

    try:
        ...send block above...
    except Exception as exc:
        ...
    _persist()
    return results
```

- [ ] **Step 4: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 5: Dry-run against the real watchlist** (network required)

Run: `$env:PYTHONPATH="."; .venv\Scripts\python.exe -m scanner.run --dry-run --no-charts`
Expected: message prints; `ledger/signals.jsonl` and `site/index.html` exist afterward. Revert any real-scan ledger noise before committing if a signal actually fired: `git checkout -- ledger/` (the live ledger starts clean at Phase 1 launch). Add `site/` to `.gitignore` (it is generated and deployed, never committed) — use the Edit tool or bash, NOT PowerShell `echo >>` (which writes UTF-16 and corrupts the file):

```bash
printf 'site/\n' >> .gitignore
```

- [ ] **Step 6: Commit**

```bash
git add scanner/run.py tests/test_run.py .gitignore
git commit -m "feat: wire ledger + track-record site into daily run"
```

---

### Task 11: Delayed free-channel poster (Phase 2 code, ready now)

**Files:**
- Create: `scanner/delayed.py`
- Test: `tests/test_delayed.py`

**Interfaces:**
- Consumes: `out/results.json` (written by the previous evening's run), `notify.format_message`, `notify.send_message`.
- Produces:
  - `format_delayed(results: dict, footer: str | None = None) -> str` — same body as `format_message` but headed `<b>Sqzdots — yesterday's signals</b>` and, when signals fired, a closing line `Same-day alerts are for members — link in footer.`
  - CLI: `python -m scanner.delayed --results out/results.json [--dry-run]` — sends to `TELEGRAM_BOT_TOKEN` + `TELEGRAM_FREE_CHAT_ID`; appends `TELEGRAM_FOOTER` env if set.

- [ ] **Step 1: Write the failing tests** — create `tests/test_delayed.py`:

```python
from scanner import delayed


def _results(fired):
    return {"generated_at": "2026-07-03T21:35:00+00:00", "as_of": "2026-07-03",
            "universe": 120, "fired_count": len(fired), "fired": fired,
            "watching": [], "watching_detail": []}


def _p(symbol):
    return {"symbol": symbol, "direction": "bull", "grade": "A", "close": 87.18,
            "rsi": 62.1, "ppo": 0.9, "squeeze_on": True, "moxie_w": 1.4,
            "target_up": 88.8, "target_dn": 80.77, "stop": 84.62,
            "date": "2026-07-03", "prov_target": 92.18, "prov_stop": 84.18}


def test_delayed_message_is_marked_as_yesterdays():
    msg = delayed.format_delayed(_results([_p("IYT")]))
    assert "yesterday" in msg.lower()
    assert "IYT" in msg
    assert "members" in msg.lower()   # upgrade nudge present when fired


def test_delayed_no_fire_has_no_upgrade_nudge():
    msg = delayed.format_delayed(_results([]))
    assert "yesterday" in msg.lower()
    assert "members" not in msg.lower()


def test_delayed_footer():
    msg = delayed.format_delayed(_results([]), footer="https://example.com")
    assert msg.rstrip().endswith("https://example.com")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_delayed.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — create `scanner/delayed.py`:

```python
"""Free-channel delayed poster (Phase 2): posts YESTERDAY's results each
morning. The free channel is the top of the funnel; same-day alerts are the
paid product."""

import argparse
import json
import os
from pathlib import Path

from scanner import notify


def format_delayed(results: dict, footer: str | None = None) -> str:
    body = notify.format_message(results)
    # swap the header line for the delayed variant
    lines = body.split("\n")
    lines[0] = f"<b>Sqzdots — yesterday's signals</b> — bar {notify._esc(results['as_of'])}"
    if results.get("fired"):
        lines.append("")
        lines.append("Same-day alerts are for members — link in footer.")
    if footer:
        lines.append("")
        lines.append(notify._esc(footer))
    return "\n".join(lines)


def main(argv=None) -> str:
    ap = argparse.ArgumentParser(description="Post yesterday's signals to the free channel")
    ap.add_argument("--results", default="out/results.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    results = json.loads(Path(args.results).read_text())
    msg = format_delayed(results, footer=os.environ.get("TELEGRAM_FOOTER"))
    print(msg)

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_FREE_CHAT_ID")
    if args.dry_run or not (token and chat_id):
        print("[not sending: dry-run or missing TELEGRAM_FREE_CHAT_ID]")
        return msg
    notify.send_message(token, chat_id, msg)
    print("[delayed post sent to free channel]")
    return msg


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_delayed.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/delayed.py tests/test_delayed.py
git commit -m "feat: delayed free-channel poster (Phase 2 tier)"
```

---

### Task 12: Workflows + README

**Files:**
- Modify: `.github/workflows/scan.yml`, `README.md`
- Create: `.github/workflows/recap.yml`, `.github/workflows/free-delayed.yml`

**Interfaces:**
- Consumes: all CLIs above. Repo secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_FREE_CHAT_ID` (Phase 2), `TELEGRAM_ADMIN_CHAT_ID`, `SITE_DEPLOY_TOKEN` (PAT with repo scope on the public site repo). Repo variables: `SITE_REPO` (e.g. `<owner>/sqzdots-site`), `PHASE` (`1` now, `2` later).
- Produces: three workflows. Deployment note: the site deploys to a **separate public repo** (`vars.SITE_REPO`) via `peaceiris/actions-gh-pages` so this repo — the indicator IP — can stay private while the track record (site + ledger copy + its own git history) is fully public.

- [ ] **Step 1: Replace `.github/workflows/scan.yml`** with:

```yaml
name: Daily Squeeze Scan

on:
  schedule:
    # 21:30 UTC, weekdays — after the US close; daily bar is finalized.
    - cron: "30 21 * * 1-5"
  workflow_dispatch: {}

permissions:
  contents: write          # commit out/ + ledger/ back to the repo

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install deps
        run: pip install -r requirements.txt

      - name: Run scan + ledger + site + notify
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          TELEGRAM_FOOTER: ${{ vars.TELEGRAM_FOOTER }}
          SITE_CHANNEL_USERNAME: ${{ vars.SITE_CHANNEL_USERNAME }}
          SITE_CHANNEL_URL: ${{ vars.SITE_CHANNEL_URL }}
        run: python -m scanner.run --watchlist universe.csv --out out --site site

      - name: Commit results + ledger
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add out/ ledger/
          git diff --staged --quiet || git commit -m "scan results $(date -u +%Y-%m-%d)"
          git push

      - name: Deploy track-record site
        if: vars.SITE_REPO != ''
        uses: peaceiris/actions-gh-pages@v4
        with:
          personal_token: ${{ secrets.SITE_DEPLOY_TOKEN }}
          external_repository: ${{ vars.SITE_REPO }}
          publish_branch: main
          publish_dir: ./site

      - name: Alert operator on failure
        if: failure()
        env:
          TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          ADMIN: ${{ secrets.TELEGRAM_ADMIN_CHAT_ID }}
        run: |
          curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
            -d chat_id="${ADMIN}" \
            -d text="🚨 Sqzdots daily scan FAILED — ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
```

- [ ] **Step 2: Create `.github/workflows/recap.yml`**:

```yaml
name: Weekly Recap Card

on:
  schedule:
    - cron: "0 14 * * 0"   # Sunday 14:00 UTC
  workflow_dispatch: {}

permissions:
  contents: read

jobs:
  recap:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install deps
        run: pip install -r requirements.txt

      - name: Render + post recap card
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python -m scanner.recap --ledger ledger/signals.jsonl --out out/recap.png

      - name: Alert operator on failure
        if: failure()
        env:
          TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          ADMIN: ${{ secrets.TELEGRAM_ADMIN_CHAT_ID }}
        run: |
          curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
            -d chat_id="${ADMIN}" \
            -d text="🚨 Sqzdots weekly recap FAILED — ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
```

- [ ] **Step 3: Create `.github/workflows/free-delayed.yml`** (inert until repo variable `PHASE` is set to `2`):

```yaml
name: Free Channel Delayed Post

on:
  schedule:
    - cron: "30 13 * * 2-6"   # next morning ET (Tue-Sat for Mon-Fri scans)
  workflow_dispatch: {}

permissions:
  contents: read

jobs:
  delayed:
    if: vars.PHASE == '2'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install deps
        run: pip install -r requirements.txt

      - name: Post yesterday's signals to the free channel
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_FREE_CHAT_ID: ${{ secrets.TELEGRAM_FREE_CHAT_ID }}
          TELEGRAM_FOOTER: ${{ vars.TELEGRAM_FOOTER }}
        run: python -m scanner.delayed --results out/results.json
```

- [ ] **Step 4: Validate workflow YAML parses**

PyYAML ships transitively with the dev requirements; validate all three files parse:

Run: `.venv\Scripts\python.exe -c "import yaml, glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]; print('workflows OK')"`
Expected: `workflows OK`. (If PyYAML is genuinely absent, `pip install pyyaml` into the venv first — it is validation-only, not a runtime dependency.)

After pushing (final step), trigger each workflow once via its **Run workflow** button: scan can run any time; `free-delayed` will show as skipped while `PHASE != '2'`; recap posts only if Telegram secrets are set.

- [ ] **Step 5: Update `README.md`** — replace the "Run the scan" and "Automate" sections' commands with the new flags, and append after the Backtest section:

```markdown
## Product pipeline (Sqzdots Indicator)

- `universe.csv` — curated ~120-name product universe (the scan input in CI)
- `ledger/signals.jsonl` — the live signal ledger (append-oriented; closed
  records are never edited; git history is the tamper-evidence)
- `python -m scanner.backtest --universe universe.csv --period 5y` — Phase 0
  walk-forward backtest (hours for the full universe; use `--symbols A,B` to smoke)
- `python -m scanner.trackrecord --out site` — regenerate the public
  track-record site (deployed to the public repo in `vars.SITE_REPO` by CI)
- `python -m scanner.recap --dry-run` — weekly recap card
- `python -m scanner.delayed --dry-run` — Phase 2 free-channel delayed post

CI secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ADMIN_CHAT_ID`,
`TELEGRAM_FREE_CHAT_ID` (Phase 2), `SITE_DEPLOY_TOKEN`.
CI variables: `SITE_REPO`, `PHASE`, `TELEGRAM_FOOTER`, `SITE_CHANNEL_USERNAME`,
`SITE_CHANNEL_URL`.
```

- [ ] **Step 6: Run the full suite one last time**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/ README.md
git commit -m "feat: CI - universe scan + site deploy + recap + phase-2 delayed post"
```

---

## Manual setup checklist (owner, not code — after all tasks land)

1. Create the public site repo (e.g. `sqzdots-site`), enable GitHub Pages on `main`.
2. Create a fine-grained PAT with write access to that repo → secret `SITE_DEPLOY_TOKEN`.
3. Set repo variables: `SITE_REPO`, `PHASE=1`; later `TELEGRAM_FOOTER`, `SITE_CHANNEL_USERNAME`, `SITE_CHANNEL_URL` once the public channel exists.
4. Create the public Telegram channel (Phase 1: `TELEGRAM_CHAT_ID` points at it). Add `TELEGRAM_ADMIN_CHAT_ID` (your private chat with the bot).
5. Run Phase 0: `python -m scanner.backtest --universe universe.csv --period 5y --out out/backtest.json` overnight; judge against the gate (avg R > 0, tolerable drawdown).
6. At Phase 2: create the private channel, repoint `TELEGRAM_CHAT_ID` to it, add `TELEGRAM_FREE_CHAT_ID` (the old public channel), set `PHASE=2`, configure Whop.
