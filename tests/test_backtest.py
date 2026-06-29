"""Tests for the backtest harness: the trade-outcome simulator (pure logic) and
the walk-forward signal history (no-lookahead property).
"""

import numpy as np
import pandas as pd
import pytest

from scanner import backtest as bt


def _bars(rows):
    """rows: list of (high, low, close)."""
    idx = pd.bdate_range("2025-01-06", periods=len(rows))
    return pd.DataFrame(rows, columns=["high", "low", "close"], index=idx)


# ---------------------------------------------------------------------------
# simulate_trade — long
# ---------------------------------------------------------------------------

def test_long_trade_hits_target_is_a_win():
    bars = _bars([(105, 99, 104), (111, 103, 110)])  # 2nd bar high 111 >= target 110
    r = bt.simulate_trade(bars, entry_price=100.0, target=110.0, stop=95.0, direction="bull")
    assert r["outcome"] == "win"
    assert r["exit_price"] == pytest.approx(110.0)
    assert r["r_multiple"] == pytest.approx(2.0)  # (110-100)/(100-95)


def test_long_trade_hits_stop_is_a_loss():
    bars = _bars([(104, 94, 96)])  # low 94 <= stop 95
    r = bt.simulate_trade(bars, entry_price=100.0, target=110.0, stop=95.0, direction="bull")
    assert r["outcome"] == "loss"
    assert r["exit_price"] == pytest.approx(95.0)
    assert r["r_multiple"] == pytest.approx(-1.0)


def test_long_trade_time_exit_uses_last_close():
    bars = _bars([(103, 99, 101), (104, 100, 103)])  # never hits 110 or 95
    r = bt.simulate_trade(bars, entry_price=100.0, target=110.0, stop=95.0, direction="bull")
    assert r["outcome"] == "time"
    assert r["exit_price"] == pytest.approx(103.0)
    assert r["r_multiple"] == pytest.approx((103 - 100) / 5.0)


def test_long_same_bar_stop_and_target_counts_as_stop_conservative():
    bars = _bars([(111, 94, 100)])  # both target 110 and stop 95 touched same bar
    r = bt.simulate_trade(bars, entry_price=100.0, target=110.0, stop=95.0, direction="bull")
    assert r["outcome"] == "loss"  # conservative: assume stop first


# ---------------------------------------------------------------------------
# simulate_trade — short
# ---------------------------------------------------------------------------

def test_short_trade_hits_target_is_a_win():
    bars = _bars([(101, 89, 90)])  # low 89 <= target 90 (price falls)
    r = bt.simulate_trade(bars, entry_price=100.0, target=90.0, stop=105.0, direction="bear")
    assert r["outcome"] == "win"
    assert r["r_multiple"] == pytest.approx((100 - 90) / (105 - 100))  # 2.0


def test_short_trade_hits_stop_is_a_loss():
    bars = _bars([(106, 99, 105)])  # high 106 >= stop 105
    r = bt.simulate_trade(bars, entry_price=100.0, target=90.0, stop=105.0, direction="bear")
    assert r["outcome"] == "loss"
    assert r["r_multiple"] == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# trade_levels — target/stop models
# ---------------------------------------------------------------------------

def test_entry_relative_levels_are_symmetric_around_entry():
    # entry mode: target = entry + 2.5*ATR, stop = entry - 1.5*ATR (long)
    tgt, stop = bt.trade_levels(close=100, ema21=90, atr=2.0, entry=100,
                                direction="bull", mode="entry")
    assert tgt == pytest.approx(100 + 2.5 * 2.0)
    assert stop == pytest.approx(100 - 1.5 * 2.0)


def test_ema21_levels_anchor_to_ema21_not_entry():
    # ema21 mode reproduces the ATR Stop study: target = ema21 +/- 2.5*ATR
    tgt, stop = bt.trade_levels(close=100, ema21=90, atr=2.0, entry=100,
                                direction="bull", mode="ema21")
    assert tgt == pytest.approx(90 + 2.5 * 2.0)  # below entry when extended
    assert stop == pytest.approx(100 - 1.5 * 2.0)


def test_entry_relative_levels_short_mirror():
    tgt, stop = bt.trade_levels(close=100, ema21=110, atr=2.0, entry=100,
                                direction="bear", mode="entry")
    assert tgt == pytest.approx(100 - 2.5 * 2.0)
    assert stop == pytest.approx(100 + 1.5 * 2.0)


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

def test_summarize_computes_winrate_and_expectancy():
    trades = [
        {"outcome": "win", "r_multiple": 2.0},
        {"outcome": "loss", "r_multiple": -1.0},
        {"outcome": "win", "r_multiple": 2.0},
        {"outcome": "time", "r_multiple": 0.5},
    ]
    s = bt.summarize(trades)
    assert s["n"] == 4
    assert s["win_rate"] == pytest.approx(0.5)  # 2 wins / 4
    assert s["expectancy_r"] == pytest.approx((2.0 - 1.0 + 2.0 + 0.5) / 4)


def test_summarize_handles_no_trades():
    s = bt.summarize([])
    assert s["n"] == 0
    assert s["win_rate"] is None
    assert s["expectancy_r"] is None


# ---------------------------------------------------------------------------
# signal_history — walk-forward, no lookahead
# ---------------------------------------------------------------------------

def _ohlc(n=300, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-06-01", periods=n)
    close = pd.Series(50 + np.arange(n) * 0.05 + rng.normal(0, 0.4, n), index=idx)
    return pd.DataFrame(
        {"open": close.shift(1).fillna(close.iloc[0]),
         "high": close + 0.5, "low": close - 0.5, "close": close},
        index=idx,
    )


def test_signal_history_is_point_in_time():
    """A bar's signal must not change when future bars are removed."""
    df = _ohlc()
    full = bt.signal_history(df, warmup=210)
    cut_date = full.index[-20]
    truncated = bt.signal_history(df.loc[:cut_date], warmup=210)
    # Every overlapping date must agree -> no lookahead.
    common = full.index.intersection(truncated.index)
    assert len(common) > 0
    assert (full.loc[common] == truncated.loc[common]).all()
