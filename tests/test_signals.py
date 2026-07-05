"""Tests for the signal combiner. The bull/bear definition matches the user's
documented trading process (not the strict B3 top dot):

BUY when ALL of:
  squeeze ON, RSI > 50, PPO >= 0, EMA8 > EMA21, full stack (8>21>34 and 50>200),
  MACD green (Diff >= 0 and rising), and Moxie above zero AND green (rising).
SELL/short is the mirror.

The combiner tests use hand-set columns so the logic is exact. Numeric-indicator
parity with TOS is covered in test_indicators.py and the live validation cases.
"""

import numpy as np
import pandas as pd
import pytest

from scanner import signals as sig


def _bull_row_df():
    """A 2-bar frame whose last bar satisfies every bullish condition."""
    return pd.DataFrame(
        {
            "squeeze_on": [True, True],
            "rsi": [55.0, 60.0],
            "ppo": [0.5, 1.0],
            "ema8": [11.0, 12.0],
            "ema21": [10.5, 11.0],
            "ema34": [10.0, 10.0],
            "sma50": [100.0, 100.0],
            "sma200": [90.0, 90.0],
            "macd_diff": [0.5, 1.0],   # >=0 and rising -> macd green
            "moxie_up": [True, True],  # weekly Moxie >0 and rising
            "moxie_dn": [False, False],
        }
    )


def _bear_row_df():
    return pd.DataFrame(
        {
            "squeeze_on": [True, True],
            "rsi": [45.0, 40.0],
            "ppo": [-0.5, -1.0],
            "ema8": [9.0, 8.0],
            "ema21": [9.5, 9.0],
            "ema34": [10.0, 10.0],
            "sma50": [90.0, 90.0],
            "sma200": [100.0, 100.0],
            "macd_diff": [-0.5, -1.0],  # <=0 and falling -> macd red
            "moxie_up": [False, False],
            "moxie_dn": [True, True],   # weekly Moxie <0 and falling
        }
    )


# ---------------------------------------------------------------------------
# MACD color helpers
# ---------------------------------------------------------------------------

def test_macd_green_when_nonneg_and_rising():
    assert bool(sig.macd_green(pd.Series([0.5, 1.0])).iloc[-1]) is True


def test_macd_not_green_when_positive_but_fading():
    assert bool(sig.macd_green(pd.Series([1.0, 0.5])).iloc[-1]) is False


def test_macd_not_green_when_negative_even_if_rising():
    # User wants green crossing/above zero; a still-negative bar is not green.
    assert bool(sig.macd_green(pd.Series([-1.0, -0.5])).iloc[-1]) is False


def test_macd_red_when_nonpos_and_falling():
    assert bool(sig.macd_red(pd.Series([-0.5, -1.0])).iloc[-1]) is True


# ---------------------------------------------------------------------------
# Scanner confluence (the user's full process)
# ---------------------------------------------------------------------------

def test_scanner_bull_fires_when_everything_aligns():
    out = sig.confluence(_bull_row_df())
    assert bool(out["scanner_bull"].iloc[-1]) is True
    assert bool(out["scanner_bear"].iloc[-1]) is False


def test_scanner_bull_blocked_when_moxie_not_up():
    df = _bull_row_df()
    df["moxie_up"] = [False, False]  # Moxie red / below zero -> no buy
    out = sig.confluence(df)
    assert bool(out["scanner_bull"].iloc[-1]) is False


def test_scanner_bull_blocked_when_macd_not_green():
    df = _bull_row_df()
    df["macd_diff"] = [1.0, 0.5]  # positive but fading -> not green
    out = sig.confluence(df)
    assert bool(out["scanner_bull"].iloc[-1]) is False


def test_scanner_bull_blocked_when_rsi_below_50():
    df = _bull_row_df()
    df.loc[df.index[-1], "rsi"] = 49.0
    assert bool(sig.confluence(df)["scanner_bull"].iloc[-1]) is False


def test_scanner_bull_blocked_when_not_in_squeeze():
    df = _bull_row_df()
    df.loc[df.index[-1], "squeeze_on"] = False
    assert bool(sig.confluence(df)["scanner_bull"].iloc[-1]) is False


def test_scanner_bull_blocked_when_stack_broken():
    df = _bull_row_df()
    df.loc[df.index[-1], "sma50"] = 80.0  # sma50 < sma200 breaks the stack
    assert bool(sig.confluence(df)["scanner_bull"].iloc[-1]) is False


def test_scanner_bear_fires_when_everything_aligns():
    out = sig.confluence(_bear_row_df())
    assert bool(out["scanner_bear"].iloc[-1]) is True
    assert bool(out["scanner_bull"].iloc[-1]) is False


def test_scanner_bear_blocked_when_moxie_not_down():
    df = _bear_row_df()
    df["moxie_dn"] = [False, False]
    assert bool(sig.confluence(df)["scanner_bear"].iloc[-1]) is False


# ---------------------------------------------------------------------------
# End-to-end analyze() on real-shaped OHLC
# ---------------------------------------------------------------------------

def _ohlc(n=320, start=50.0, step=0.0, noise=0.0, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    base = start + np.arange(n) * step + rng.normal(0, noise, n)
    close = pd.Series(base, index=idx)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
        },
        index=idx,
    )


def test_analyze_returns_expected_columns():
    out = sig.analyze(_ohlc(step=0.1, noise=0.3))
    for col in ["scanner_bull", "scanner_bear", "rsi", "ppo", "squeeze_on",
                "moxie_w", "moxie_up", "moxie_dn", "grade"]:
        assert col in out.columns


def test_analyze_never_marks_a_bar_both_bull_and_bear():
    out = sig.analyze(_ohlc(step=0.1, noise=0.5, seed=7))
    assert not (out["scanner_bull"] & out["scanner_bear"]).any()


def test_condition_breakdown_reports_each_layer_consistently():
    out = sig.condition_breakdown(_ohlc(step=0.1, noise=0.3))
    # every advertised condition key is present
    for key in ["squeeze_on", "rsi_pass", "ppo_pass", "structure_pass",
                "stack_pass", "macd_pass", "moxie_pass", "direction"]:
        assert key in out
    # pass-flags must agree with the raw values they summarize
    assert out["rsi_pass"] == (out["rsi"] > 50)
    assert out["ppo_pass"] == (out["ppo"] >= 0)
    assert out["structure_pass"] == (out["ema8"] > out["ema21"])
    # a bull direction requires every gate true
    if out["direction"] == "bull":
        assert all([out["squeeze_on"], out["rsi_pass"], out["ppo_pass"],
                    out["stack_pass"], out["macd_pass"], out["moxie_pass"]])


def test_b3_rows_has_all_seven_rows_with_valid_states():
    rows = sig.b3_rows(_ohlc(step=0.12, noise=0.3))
    assert list(rows.columns) == sig.B3_ROWS
    last = rows.iloc[-1]
    assert set(rows.values.ravel()) <= {"bull", "bear", "none", "neutral"}
    # steady uptrend -> structure and stack rows should read bull on the last bar
    assert last["structure"] == "bull"
    assert last["stack1"] == "bull"


def test_latest_signal_payload_has_levels_and_direction():
    payload = sig.latest_signal(_ohlc(step=0.1, noise=0.3), symbol="TEST")
    assert payload["symbol"] == "TEST"
    assert payload["direction"] in {"bull", "bear", "none"}
    for key in ["close", "target_up", "target_dn", "stop", "grade", "date"]:
        assert key in payload
    assert isinstance(payload["close"], float)


# ---------------------------------------------------------------------------
# Task 2: latest_signal exports atr, ema21, and lit-condition counts
# ---------------------------------------------------------------------------

@pytest.fixture
def iyt_frame():
    """Load IYT fixture frame from tests/fixtures/IYT.csv."""
    from pathlib import Path
    fixtures = Path(__file__).parent / "fixtures"
    df = pd.read_csv(fixtures / "IYT.csv", index_col=0, parse_dates=True)
    return df


def test_latest_signal_exports_atr_ema21_and_lit_counts(iyt_frame):
    p = sig.latest_signal(iyt_frame, symbol="IYT")
    assert isinstance(p["atr"], float) and p["atr"] > 0
    assert isinstance(p["ema21"], float) and p["ema21"] > 0
    assert 0 <= p["lit_bull"] <= 7
    assert 0 <= p["lit_bear"] <= 7
    # a bar can't fully satisfy both sides at once
    assert not (p["lit_bull"] == 7 and p["lit_bear"] == 7)
