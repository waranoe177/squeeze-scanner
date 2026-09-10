"""Tests for the indicator primitives, written to match the ThinkScript semantics
in Sqzdots/Indicators (B3 Super dots, Moxie, MACD, Stacked EMAs, ATR Stop).

Parity is the whole point: every formula here mirrors a line of the TOS code.
Where ThinkScript made a specific choice (EMA seeding, population stdev, simple
vs Wilders averaging inside the squeeze), the tests pin that choice down.
"""

import numpy as np
import pandas as pd
import pytest

from scanner import indicators as ind


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------

def test_ema_of_constant_series_is_constant():
    s = pd.Series([5.0] * 10)
    result = ind.ema(s, 3)
    assert result.iloc[-1] == pytest.approx(5.0)


def test_ema_matches_thinkscript_recursion_seeded_at_first_value():
    # ThinkScript ExpAverage seeds with the first bar, alpha = 2/(len+1).
    # span=2 -> alpha=2/3. y0=1; y1=1/3*1+2/3*2=1.6667; y2=1/3*1.6667+2/3*3=2.5556
    s = pd.Series([1.0, 2.0, 3.0])
    result = ind.ema(s, 2)
    assert result.iloc[0] == pytest.approx(1.0)
    assert result.iloc[1] == pytest.approx(1.0 / 3 + 2.0 / 3 * 2)
    assert result.iloc[2] == pytest.approx(2.5555555, abs=1e-5)


def test_sma_is_trailing_mean():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    result = ind.sma(s, 2)
    assert np.isnan(result.iloc[0])
    assert result.iloc[1] == pytest.approx(1.5)
    assert result.iloc[3] == pytest.approx(3.5)


# ---------------------------------------------------------------------------
# True range / ATR
# ---------------------------------------------------------------------------

def test_true_range_first_bar_is_high_minus_low():
    high = pd.Series([10.0, 12.0])
    low = pd.Series([8.0, 9.0])
    close = pd.Series([9.0, 11.0])
    tr = ind.true_range(high, low, close)
    assert tr.iloc[0] == pytest.approx(2.0)  # 10 - 8, no prior close


def test_true_range_uses_prior_close_gap():
    # bar1: high 12, low 9, prev close 9 -> max(12-9, |12-9|, |9-9|) = 3
    high = pd.Series([10.0, 12.0])
    low = pd.Series([8.0, 9.0])
    close = pd.Series([9.0, 11.0])
    tr = ind.true_range(high, low, close)
    assert tr.iloc[1] == pytest.approx(3.0)


def test_atr_of_constant_true_range_converges_to_that_value():
    high = pd.Series([2.0] * 50)
    low = pd.Series([0.0] * 50)
    close = pd.Series([1.0] * 50)  # TR each bar = 2.0
    atr = ind.atr(high, low, close, 14)
    assert atr.iloc[-1] == pytest.approx(2.0, abs=1e-6)


# ---------------------------------------------------------------------------
# RSI (ThinkScript: RSI = 50 * (ChgRatio + 1), Wilders)
# ---------------------------------------------------------------------------

def test_rsi_all_up_series_is_100():
    s = pd.Series(np.arange(1.0, 60.0))  # strictly increasing
    rsi = ind.rsi(s, 14)
    assert rsi.iloc[-1] == pytest.approx(100.0, abs=1e-6)


def test_rsi_all_down_series_is_0():
    s = pd.Series(np.arange(60.0, 1.0, -1.0))  # strictly decreasing
    rsi = ind.rsi(s, 14)
    assert rsi.iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_rsi_flat_series_is_50():
    s = pd.Series([42.0] * 60)
    rsi = ind.rsi(s, 14)
    assert rsi.iloc[-1] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# MACD Diff (histogram = MACD line - signal line), PPO
# ---------------------------------------------------------------------------

def test_macd_diff_of_constant_series_is_zero():
    s = pd.Series([100.0] * 80)
    diff = ind.macd_diff(s, 12, 26, 34)
    assert diff.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_ppo_of_constant_series_is_zero():
    s = pd.Series([100.0] * 60)
    ppo = ind.ppo(s, 10, 20)
    assert ppo.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_ppo_positive_when_fast_above_slow():
    # rising series -> fast EMA above slow EMA -> PPO > 0
    s = pd.Series(np.arange(1.0, 80.0))
    ppo = ind.ppo(s, 10, 20)
    assert ppo.iloc[-1] > 0


# ---------------------------------------------------------------------------
# Bollinger / Keltner / Squeeze
# ---------------------------------------------------------------------------

def test_bollinger_uses_population_stdev():
    # ThinkScript StDev is population (ddof=0). For [1,2,3,4,5] mean=3,
    # population std = sqrt(2) = 1.41421356
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    upper, lower = ind.bollinger(s, length=5, mult=1.0)
    assert upper.iloc[-1] == pytest.approx(3.0 + np.sqrt(2.0))
    assert lower.iloc[-1] == pytest.approx(3.0 - np.sqrt(2.0))


def test_squeeze_on_when_close_range_tight_but_bars_wide():
    # Closes barely move (narrow Bollinger) but each bar has a big high-low
    # wick (wide Keltner). BB should sit inside KC -> squeeze ON.
    n = 60
    rng = np.random.default_rng(0)
    close = pd.Series(100.0 + rng.normal(0, 0.05, n))
    high = close + 3.0
    low = close - 3.0
    sqz = ind.squeeze_on(close, high, low, length=20, bb_mult=2.0, kc_mult=2.0)
    assert bool(sqz.iloc[-1]) is True


def test_squeeze_off_in_a_steady_trend():
    # A smooth ramp: each bar moves a little (small True Range -> narrow Keltner),
    # but the close dispersion over the window is large (wide Bollinger). BB ends
    # up outside KC -> squeeze OFF. This is the "trending, not coiled" case.
    n = 60
    close = pd.Series([100.0 + i * 1.0 for i in range(n)])
    high = close + 0.1
    low = close - 0.1
    sqz = ind.squeeze_on(close, high, low, length=20, bb_mult=2.0, kc_mult=2.0)
    assert bool(sqz.iloc[-1]) is False


# ---------------------------------------------------------------------------
# Moxie (MACD-histogram variant) + weekly resampling
# ---------------------------------------------------------------------------

def test_rescaled_rsi_spans_minus100_to_100():
    up = np.arange(1.0, 40.0)
    down = np.arange(40.0, 1.0, -1.0)
    s = pd.Series(np.concatenate([up, down]))
    r = ind.rescaled_rsi(s, 14).dropna()
    assert r.max() == pytest.approx(100.0)
    assert r.min() == pytest.approx(-100.0)
    assert r.between(-100.001, 100.001).all()  # tolerant of float epsilon at the bounds


def test_rev_eng_rsi_constant_series_equals_price():
    # No change -> RevEngRSI collapses to the price level.
    s = pd.Series([50.0] * 40)
    r = ind.rev_eng_rsi(s, 14)
    assert r.iloc[-1] == pytest.approx(50.0)


def test_rev_eng_rsi_below_price_in_uptrend():
    # In a steady uptrend the 50-RSI price level sits below current price.
    s = pd.Series(np.arange(1.0, 60.0))
    r = ind.rev_eng_rsi(s, 14)
    assert r.iloc[-1] < s.iloc[-1]


def test_moxie_of_constant_series_is_zero():
    s = pd.Series([50.0] * 60)
    mox = ind.moxie(s)
    assert mox.iloc[-1] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Major Pivots (major_pivots_count_occur.docx): centered fractal pivots +
# event-based retest counting. len=13 -> 12 bars each side.
# ---------------------------------------------------------------------------

def test_find_fractal_pivots_detects_a_centered_peak():
    # A symmetric up/down triangle: the apex is the only confirmable peak.
    high = pd.Series([1, 2, 3, 4, 5, 4, 3, 2, 1], dtype=float)
    low = high
    peaks, valleys = ind.find_fractal_pivots(high, low, length=5)
    assert peaks == [4]


def test_find_fractal_pivots_detects_a_centered_valley():
    low = pd.Series([5, 4, 3, 2, 1, 2, 3, 4, 5], dtype=float)
    high = low
    peaks, valleys = ind.find_fractal_pivots(high, low, length=5)
    assert valleys == [4]


def test_recent_bars_are_not_confirmed_pivots():
    # Strictly rising: the highest bar is the last one, which has no future
    # window and cannot be confirmed. No interior bar qualifies either.
    high = pd.Series(np.arange(1.0, 11.0))
    low = high
    peaks, valleys = ind.find_fractal_pivots(high, low, length=5)
    assert peaks == []
    assert valleys == []


def test_count_retests_counts_reentry_events_not_bars():
    # level=100, tol=1. Price sits in the zone for 3 bars (one event), leaves,
    # then returns for 2 bars (second event) -> 2 retests, not 5.
    high = pd.Series([100.5, 100.5, 100.5, 105, 105, 100.5, 100.5], dtype=float)
    low = pd.Series([99.5, 99.5, 99.5, 104, 104, 99.5, 99.5], dtype=float)
    assert ind.count_retests(100.0, high, low, start=-1, tol=1.0) == 2


def test_count_retests_ignores_bars_at_or_before_start():
    high = pd.Series([100.5, 105, 100.5], dtype=float)
    low = pd.Series([99.5, 104, 99.5], dtype=float)
    # start=0 -> the in-zone bar 0 (the pivot itself) is not counted; bar 2 is.
    assert ind.count_retests(100.0, high, low, start=0, tol=1.0) == 1


def test_major_pivots_excludes_untested_levels():
    # One peak (apex 5), then price falls away and never returns.
    high = pd.Series([1, 2, 3, 4, 5, 4, 3, 2, 1, 0, -1, -2, -3], dtype=float)
    low = high
    piv = ind.major_pivots(high, low, length=5, min_retests=1, tol_pct=0.0)
    assert all(p["retests"] >= 1 for p in piv)
    assert not any(p["kind"] == "peak" and p["level"] == 5.0 for p in piv)


def test_major_pivots_includes_retested_level():
    # Apex 5 at idx4; price returns to 5 once later -> one retest -> included.
    high = pd.Series([1, 2, 3, 4, 5, 4, 3, 4, 5, 4, 3, 2, 1], dtype=float)
    low = high
    piv = ind.major_pivots(high, low, length=5, min_retests=1, tol_pct=0.0)
    peaks = [p for p in piv if p["kind"] == "peak"]
    assert any(p["level"] == 5.0 and p["retests"] >= 1 for p in peaks)


def test_major_pivots_returns_positional_index_and_kind():
    high = pd.Series([1, 2, 3, 4, 5, 4, 3, 4, 5, 4, 3, 2, 1], dtype=float)
    low = high
    piv = ind.major_pivots(high, low, length=5, min_retests=1, tol_pct=0.0)
    assert piv, "expected at least one major pivot"
    p = piv[0]
    assert set(p) >= {"kind", "pos", "level", "retests"}
    assert p["kind"] in ("peak", "valley")
    assert isinstance(p["pos"], int)


def test_major_pivots_empty_on_short_history():
    high = pd.Series([1.0, 2.0, 3.0])
    low = high
    assert ind.major_pivots(high, low, length=13) == []


def test_resample_to_weekly_aggregates_ohlc():
    # 10 business days = 2 calendar weeks. Weekly bar = first open, max high,
    # min low, last close.
    idx = pd.bdate_range("2024-01-01", periods=10)  # Mon Jan 1 .. Fri Jan 12
    df = pd.DataFrame(
        {
            "open": np.arange(10.0),
            "high": np.arange(10.0) + 5,
            "low": np.arange(10.0) - 5,
            "close": np.arange(10.0) + 1,
        },
        index=idx,
    )
    wk = ind.resample_to_weekly(df)
    assert len(wk) == 2
    # First week (Jan 1-5): open=0, high=max(5..9)=9, low=min(-5..-1)=-5, close=last=5
    assert wk["open"].iloc[0] == pytest.approx(0.0)
    assert wk["high"].iloc[0] == pytest.approx(9.0)
    assert wk["low"].iloc[0] == pytest.approx(-5.0)
    assert wk["close"].iloc[0] == pytest.approx(5.0)
