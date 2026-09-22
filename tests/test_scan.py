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


# --- stale-bar guard (2026-09-21 incident) -------------------------------
# On 2026-09-21 the scan alerted on signals computed from Friday 09-18 while the
# header read "bar 2026-09-21": yfinance returned NaN OHLC for a subset of
# symbols, normalize's dropna silently discarded those rows, and as_of =
# max(bar date) hid it. A signal whose bar is not the session must never reach
# the alert or the ledger -- its ATR-derived entry/stop/target are from the
# wrong day, so the printed risk is not the real risk.

def test_signals_on_an_older_bar_are_suppressed():
    payloads = [
        _payload("FRESH", "bull"),
        dict(_payload("STALE", "bull"), date="2026-06-23"),
    ]
    res = scan.build_results(payloads, as_of="2026-06-26")
    assert [p["symbol"] for p in res["fired"]] == ["FRESH"]
    assert [p["symbol"] for p in res["stale_fired"]] == ["STALE"]


def test_suppressed_signals_keep_their_real_bar_date_for_diagnosis():
    payloads = [dict(_payload("STALE", "bear"), date="2026-06-23")]
    res = scan.build_results(payloads, as_of="2026-06-26")
    assert res["fired"] == []
    assert res["stale_fired"][0]["date"] == "2026-06-23"


def test_no_stale_list_when_every_signal_is_on_the_session():
    res = scan.build_results([_payload("AAA", "bull"), _payload("BBB", "bear")],
                             as_of="2026-06-26")
    assert res["stale_fired"] == []
    assert len(res["fired"]) == 2


def test_watching_is_not_affected_by_the_stale_guard():
    payloads = [dict(_payload("COILED", "none", squeeze_on=True), date="2026-06-23")]
    res = scan.build_results(payloads, as_of="2026-06-26")
    assert "COILED" in res["watching"]


# --- session anchoring across asset classes ------------------------------
# Verified in live 2y data: 2025-01-09 (Carter day of mourning) and 2025-07-04
# have CME futures bars and NO equity bars. Both weekdays, so the cron runs.
# If 8 futures define the session, ~150 legitimate equity signals get routed to
# stale_fired: an empty alert, an empty ledger, and an operator message falsely
# blaming the data source. Next occurrence 2026-07-03.

def test_session_ignores_futures_that_traded_when_equities_did_not():
    payloads = [
        dict(_payload("SPY", "bull"), date="2026-07-02"),
        dict(_payload("AAPL", "bull"), date="2026-07-02"),
        dict(_payload("ES=F", "bull"), date="2026-07-03"),
        dict(_payload("GC=F", "bull"), date="2026-07-03"),
    ]
    assert scan.session_date(payloads) == "2026-07-02"


def test_session_uses_max_not_mode_so_a_stale_majority_cannot_win():
    """The 09-21 incident was a SUBSET falling behind. A modal session would
    follow them down and the guard would never fire."""
    payloads = [
        dict(_payload("A", "bull"), date="2026-09-18"),
        dict(_payload("B", "bull"), date="2026-09-18"),
        dict(_payload("C", "bull"), date="2026-09-21"),
    ]
    assert scan.session_date(payloads) == "2026-09-21"


def test_session_falls_back_to_futures_when_there_are_no_equities():
    payloads = [dict(_payload("ES=F", "bull"), date="2026-07-03")]
    assert scan.session_date(payloads) == "2026-07-03"


def test_futures_ahead_of_the_equity_session_are_not_suppressed():
    """Ahead is not stale. Only BEHIND the session means bad data."""
    payloads = [
        dict(_payload("SPY", "bull"), date="2026-07-02"),
        dict(_payload("ES=F", "bull"), date="2026-07-03"),
    ]
    res = scan.build_results(payloads, as_of=scan.session_date(payloads))
    assert sorted(p["symbol"] for p in res["fired"]) == ["ES=F", "SPY"]
    assert res["stale_fired"] == []
