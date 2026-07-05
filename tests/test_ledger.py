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
