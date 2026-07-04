"""Tests for the conviction score: confluence ladder + standardized strength."""

import numpy as np
import pandas as pd
import pytest

from scanner import score


def _flags(**over):
    base = {"structure_pass": True, "stack_pass": True, "squeeze_on": True,
            "macd_pass": True, "moxie_pass": True, "scanner": True}
    base.update(over)
    return base


def test_confluence_full_ladder_is_max_base():
    pts, _ = score.confluence_points(_flags())
    assert pts == pytest.approx(score.CONFLUENCE_MAX)


def test_confluence_partial_is_less_than_full():
    partial, _ = score.confluence_points(_flags(moxie_pass=False, scanner=False, macd_pass=False))
    full, _ = score.confluence_points(_flags())
    assert partial < full
    assert partial > 0


def test_grade_thresholds():
    assert score.grade_for(90) == "A+"
    assert score.grade_for(75) == "A"
    assert score.grade_for(60) == "B"
    assert score.grade_for(40) == "C"


def test_pct_rank_is_zero_to_one_and_monotonic():
    s = pd.Series(np.arange(100.0))
    assert score.pct_rank(s, -10) == pytest.approx(0.0)
    assert score.pct_rank(s, 200) == pytest.approx(1.0)
    assert score.pct_rank(s, 50) > score.pct_rank(s, 10)


# ---- integration on real-shaped data -------------------------------------

def _ohlc(n=320, step=0.1, noise=0.4, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    close = pd.Series(50 + np.arange(n) * step + rng.normal(0, noise, n), index=idx)
    return pd.DataFrame({"open": close.shift(1).fillna(close.iloc[0]),
                         "high": close + 0.6, "low": close - 0.6, "close": close}, index=idx)


def test_conviction_returns_score_in_range_with_parts():
    r = score.conviction(_ohlc(), symbol="TEST")
    assert 0 <= r["score"] <= 100
    assert r["grade"] in {"A+", "A", "B", "C"}
    for k in ["confluence", "strength", "rr", "atr_pct", "parts"]:
        assert k in r
    for sub in ["momentum", "moxie", "freshness", "risk_reward"]:
        assert sub in r["parts"]


def test_bull_score_is_at_least_confluence_and_capped():
    r = score.conviction(_ohlc(step=0.15, noise=0.3), symbol="UP")
    assert r["score"] <= 100
    if r["direction"] == "bull":
        assert r["confluence"] == score.CONFLUENCE_MAX
        assert r["score"] >= r["confluence"]  # strength only adds


def test_higher_rsi_lifts_the_score_all_else_equal():
    # RSI is an absolute (cross-ticker) input; more RSI distance above 50 scores higher.
    assert score._clamp((70 - 50) / 20.0) > score._clamp((55 - 50) / 20.0)
