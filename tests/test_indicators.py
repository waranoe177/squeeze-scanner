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

def test_moxie_of_constant_series_is_zero():
    s = pd.Series([50.0] * 60)
    mox = ind.moxie(s)
    assert mox.iloc[-1] == pytest.approx(0.0, abs=1e-9)


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
