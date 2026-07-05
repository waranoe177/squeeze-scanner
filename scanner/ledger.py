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
