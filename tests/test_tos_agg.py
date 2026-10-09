"""TOS N-day aggregation: every N weekdays since Mon 1970-01-05, holidays count.
Parity values are from the owner's TOS SPY 2D/3D charts, 2026-10-09."""

import pandas as pd
import pytest

from scanner import chart
from scanner.indicators import ema, sma


def _spy():
    return pd.read_csv("tests/fixtures/SPY_unadj.csv", index_col=0, parse_dates=True)


def _starts(bars, k=20):
    return [d.strftime("%m%d") for d in bars.index[-k:]]


def test_2d_bar_starts_match_tos():
    assert _starts(chart.agg_multiday(_spy(), 2)) == (
        "0817 0819 0821 0825 0827 0831 0902 0904 0908 0910 "
        "0914 0916 0918 0922 0924 0928 0930 1002 1006 1008").split()


def test_3d_bar_starts_match_tos():
    assert _starts(chart.agg_multiday(_spy(), 3)) == (
        "0721 0724 0729 0803 0806 0811 0814 0819 0824 0827 "
        "0901 0904 0909 0914 0917 0922 0925 0930 1005 1008").split()


def test_2d_values_match_tos_at_the_0813_bar():
    b = chart.agg_multiday(_spy(), 2)
    t = pd.Timestamp("2026-08-13")
    assert b.loc[t, ["open", "high", "low", "close"]].tolist() == pytest.approx(
        [774.87, 779.37, 774.11, 776.34], abs=0.005)
    assert ema(b["close"], 21)[t] == pytest.approx(753.01, abs=0.005)
    assert sma(b["close"], 50)[t] == pytest.approx(733.30, abs=0.005)
    assert sma(b["close"], 200)[t] == pytest.approx(659.02, abs=0.005)


def test_3d_values_match_tos_on_the_unfinished_last_bar():
    b = chart.agg_multiday(_spy(), 3)
    assert b.index[-1] == pd.Timestamp("2026-10-08")   # Thu 10-08 + Fri 10-09 (+ Mon 10-12)
    assert b.iloc[-1][["open", "high", "low", "close"]].tolist() == pytest.approx(
        [774.86, 779.42, 770.44, 778.57], abs=0.005)
    assert ema(b["close"], 21).iloc[-1] == pytest.approx(763.24, abs=0.005)
    assert sma(b["close"], 50).iloc[-1] == pytest.approx(738.61, abs=0.005)
    assert sma(b["close"], 200).iloc[-1] == pytest.approx(644.31, abs=0.005)


def test_holiday_counts_as_a_day():
    # Labor Day Mon 2026-09-07: the 2D bar starting Fri 09-04 holds only that Friday.
    idx = pd.bdate_range("2026-08-31", "2026-09-11").drop(pd.Timestamp("2026-09-07"))
    close = pd.Series(range(len(idx)), index=idx, dtype=float)
    daily = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close})
    two = chart.agg_multiday(daily, 2)
    assert [d.strftime("%m%d") for d in two.index] == ["0831", "0902", "0904", "0908", "0910"]
    friday = two.loc[pd.Timestamp("2026-09-04")]
    assert friday["open"] == friday["close"] == close[pd.Timestamp("2026-09-04")]


def test_weekly_is_the_mon_fri_week_dated_by_its_first_trading_day():
    wk = chart.agg_multiday(_spy(), 5)
    assert pd.Timestamp("2026-09-08") in wk.index      # Labor Day week starts Tuesday
    assert wk.index[-1] == pd.Timestamp("2026-10-05")
    week = _spy().loc["2026-10-05":"2026-10-09"]
    assert wk.iloc[-1]["high"] == week["high"].max()
    assert wk.iloc[-1]["close"] == week["close"].iloc[-1]


def test_volume_and_extra_columns_are_dropped():
    spy = _spy().assign(volume=1.0)
    assert list(chart.agg_multiday(spy, 3).columns) == ["open", "high", "low", "close"]
