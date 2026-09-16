"""Tests for the backtest harness: the trade-outcome simulator (pure logic) and
the walk-forward signal history (no-lookahead property).
"""

import numpy as np
import pandas as pd
import pytest

from scanner import backtest as bt
from scanner import options


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


# ---------------------------------------------------------------------------
# extended_stats — max losing streak and max drawdown
# ---------------------------------------------------------------------------

def _trade(entry_date, r, outcome=None):
    return {
        "symbol": "T", "signal_date": entry_date, "entry_date": entry_date,
        "direction": "bull", "entry": 100.0, "stop": 97.0, "target": 105.0,
        "outcome": outcome or ("win" if r > 0 else "loss"),
        "exit_price": 100.0 + 3 * r, "bars_held": 2,
        "r_multiple": r, "return_pct": r * 0.03,
    }


def test_extended_stats_losing_streak_and_drawdown():
    trades = [
        _trade("2025-01-02", 1.0),
        _trade("2025-01-06", -1.0),
        _trade("2025-01-08", -1.0),
        _trade("2025-01-10", -0.5),
        _trade("2025-01-14", 2.0),
    ]
    s = bt.extended_stats(trades)
    assert s["max_losing_streak"] == 3
    assert s["max_drawdown_r"] == 2.5  # peak +1.0 -> trough -1.5


def test_extended_stats_empty():
    s = bt.extended_stats([])
    assert s == {"max_losing_streak": 0, "max_drawdown_r": 0.0}


# ---------------------------------------------------------------------------
# option_leg — Playbook-B option P&L (BS, RV-as-IV, ATM, ~21 DTE)
# ---------------------------------------------------------------------------

def test_option_leg_call_gains_when_underlying_rises():
    leg = bt.option_leg(100.0, 108.0, "bull", iv=0.30, bars_held=3)
    assert leg["kind"] == "call"
    assert leg["strike"] == pytest.approx(100.0)   # exact ATM
    assert leg["dte"] == 21
    exp_entry = options.black_scholes(100.0, 100.0, 21 / 365, 0.043, 0.30, "call")["price"]
    assert leg["entry_premium"] == pytest.approx(exp_entry)  # wired to BS
    assert leg["exit_premium"] > leg["entry_premium"]        # underlying up -> call up
    assert leg["option_return"] > 0
    assert leg["option_outcome"] == "win"


def test_option_leg_put_gains_when_underlying_falls():
    leg = bt.option_leg(100.0, 92.0, "bear", iv=0.30, bars_held=3)
    assert leg["kind"] == "put"
    assert leg["exit_premium"] > leg["entry_premium"]
    assert leg["option_outcome"] == "win"


def test_option_leg_flat_underlying_is_a_loss_from_theta():
    # underlying unchanged, but 3 days of decay -> option worth less -> loss.
    # This is the whole reason the short fixed hold matters.
    leg = bt.option_leg(100.0, 100.0, "bull", iv=0.30, bars_held=3)
    assert leg["exit_premium"] < leg["entry_premium"]
    assert leg["option_return"] < 0
    assert leg["option_outcome"] == "loss"


# ---------------------------------------------------------------------------
# simulate_hold — fixed N-day exit (the E3 rule)
# ---------------------------------------------------------------------------

def test_simulate_hold_exits_at_close_of_day_n():
    bars = _bars([(105, 99, 101), (106, 100, 103), (107, 101, 104), (108, 102, 106)])
    bh, exit_px = bt.simulate_hold(bars, hold_days=3)
    assert bh == 3
    assert exit_px == pytest.approx(104.0)  # close of the 3rd bar, ignore target


def test_simulate_hold_short_window_uses_last_close():
    bars = _bars([(105, 99, 101), (106, 100, 103)])  # only 2 bars, ask for 3
    bh, exit_px = bt.simulate_hold(bars, hold_days=3)
    assert bh == 2
    assert exit_px == pytest.approx(103.0)


# ---------------------------------------------------------------------------
# summarize_option — option-vehicle win rate / expectancy / median
# ---------------------------------------------------------------------------

def test_summarize_option_winrate_expectancy_median():
    trades = [
        {"entry_premium": 2.0, "option_return": 0.5, "option_cash": 100.0},
        {"entry_premium": 2.0, "option_return": -0.3, "option_cash": -60.0},
        {"entry_premium": 2.0, "option_return": 0.1, "option_cash": 20.0},
    ]
    s = bt.summarize_option(trades)
    assert s["n"] == 3
    assert s["win_rate"] == pytest.approx(2 / 3)
    assert s["expectancy_pct"] == pytest.approx((0.5 - 0.3 + 0.1) / 3)
    assert s["median_pct"] == pytest.approx(0.1)
    assert s["total_cash"] == pytest.approx(60.0)


def test_summarize_option_skips_zero_premium_and_empty():
    assert bt.summarize_option([])["n"] == 0
    only_bad = [{"entry_premium": 0.0, "option_return": 0.0, "option_cash": 0.0}]
    assert bt.summarize_option(only_bad)["n"] == 0


# ---------------------------------------------------------------------------
# backtest — hold mode carries the option leg
# ---------------------------------------------------------------------------

def test_backtest_hold_mode_adds_option_fields():
    df = _ohlc()
    trades = bt.backtest(df, "T", exit="hold", hold_days=3, warmup=210)
    assert isinstance(trades, list)
    for t in trades:
        for k in ("entry_premium", "exit_premium", "option_return",
                  "option_outcome", "strike", "dte", "iv", "contracts"):
            assert k in t
        assert t["option_outcome"] in ("win", "loss", "flat")
        assert t["bars_held"] <= 3
