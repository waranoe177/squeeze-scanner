"""Tests for the scan runner: assembling and ranking the daily results from
per-symbol signal payloads.
"""

import numpy as np
import pandas as pd
import pytest

from scanner import scan


def _payload(symbol, direction, rsi=60.0, squeeze_on=True, score=75.0):
    return {
        "symbol": symbol, "direction": direction, "grade": "A++" if direction != "none" else "",
        "close": 100.0, "rsi": rsi, "ppo": 1.0, "squeeze_on": squeeze_on, "moxie_w": 1.0,
        "target_up": 110.0, "target_dn": 90.0, "stop": 95.0, "date": "2026-06-26",
        "score": score,
    }


def test_build_results_separates_fired_from_watching():
    payloads = [
        _payload("AAA", "bull"),
        _payload("BBB", "none", squeeze_on=True),   # coiled, not fired -> watching
        _payload("CCC", "none", squeeze_on=False),  # nothing
    ]
    res = scan.build_results(payloads, as_of="2026-06-26")
    fired = [p["symbol"] for p in res["fired"]]
    watching = res["watching"]
    assert fired == ["AAA"]
    assert "BBB" in watching
    assert "CCC" not in watching
    assert res["as_of"] == "2026-06-26"
    assert res["universe"] == 3


def test_rank_fired_orders_bull_before_bear_then_by_score():
    payloads = [
        _payload("BEAR1", "bear", score=90.0),
        _payload("BULLWEAK", "bull", score=72.0),
        _payload("BULLSTRONG", "bull", score=88.0),
    ]
    ranked = scan.rank_fired([p for p in payloads])
    assert [p["symbol"] for p in ranked] == ["BULLSTRONG", "BULLWEAK", "BEAR1"]


def test_build_results_counts_and_timestamp_present():
    res = scan.build_results([_payload("AAA", "bull")], as_of="2026-06-26")
    assert res["fired_count"] == 1
    assert "generated_at" in res


def test_scan_frames_runs_engine_over_a_dict_of_frames():
    # one synthetic uptrend frame -> produces a payload with a direction
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2023-01-02", periods=320)
    close = pd.Series(50 + np.arange(320) * 0.08 + rng.normal(0, 0.4, 320), index=idx)
    df = pd.DataFrame({"open": close.shift(1).fillna(close.iloc[0]),
                       "high": close + 0.6, "low": close - 0.6, "close": close}, index=idx)
    payloads = scan.scan_frames({"DEMO": df})
    assert len(payloads) == 1
    assert payloads[0]["symbol"] == "DEMO"
    assert payloads[0]["direction"] in {"bull", "bear", "none"}


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
