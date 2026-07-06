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
