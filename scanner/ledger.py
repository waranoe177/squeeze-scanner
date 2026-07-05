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

import pandas as pd

from scanner import backtest

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
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def save(path, records: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def append_fired(records: list[dict], fired: list[dict]) -> list[dict]:
    """Append a new record per fired payload, skipping ids already present."""
    known = {r["id"] for r in records}
    for payload in fired:
        rec = new_record(payload)
        if rec["id"] not in known:
            records.append(rec)
            known.add(rec["id"])
    return records


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
