"""B4 arrows: the owner's TOS "declare upper b4" study with a REV-RSI candle-colour gate (owner, 2026-10-22).

bull = aggressive squeeze ON and EMA8 > EMA21 and RSI(14) >= 50 and FullK > FullD and the TTM squeeze
       histogram rising 2 bars; bear mirrors it.
Gate: an up arrow needs a GREEN REV-RSI candle (rev_rsi <= EMA21), a down arrow a RED one.
One arrow per streak: on the first day the signal AND the colour agree (Option B).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scanner import b4

FIX = Path(__file__).parent / "fixtures"


def _fixture(sym):
    df = pd.read_csv(FIX / f"{sym}_daily_2026-10-21.csv", index_col=0, parse_dates=True)
    return df


def _days(mask):
    return [d.strftime("%Y-%m-%d") for d in mask[mask].index]


# ---- indicators -------------------------------------------------------------------------------

def test_full_stochastic_matches_a_hand_calculation():
    idx = pd.RangeIndex(6)
    high = pd.Series([10, 12, 11, 13, 14, 12], index=idx, dtype=float)
    low = pd.Series([8, 9, 9, 10, 11, 10], index=idx, dtype=float)
    close = pd.Series([9, 11, 10, 12, 13, 11], index=idx, dtype=float)
    k, d = b4.stoch_full(high, low, close, k_period=3, slowing=2, d_period=2)
    # FastK over 3 bars: bar2 (10-8)/(12-8)=50, bar3 (12-9)/(13-9)=75, bar4 (13-9)/(14-9)=80,
    # bar5 (11-10)/(14-10)=25. FullK = 2-bar mean: bar3 62.5, bar4 77.5, bar5 52.5. FullD: bar4 70, bar5 65.
    assert k.iloc[3:].round(6).tolist() == [62.5, 77.5, 52.5]
    assert d.iloc[4:].round(6).tolist() == [70.0, 65.0]


def test_linreg_end_is_the_least_squares_value_at_the_last_bar():
    rng = np.random.default_rng(7)
    y = pd.Series(rng.normal(0, 1, 40).cumsum())
    got = b4.linreg_end(y, 20)
    for i in (19, 25, 39):
        w = y.iloc[i - 19: i + 1].to_numpy()
        slope, icpt = np.polyfit(np.arange(20), w, 1)
        assert got.iloc[i] == pytest.approx(icpt + slope * 19, abs=1e-9)
    assert got.iloc[:19].isna().all()


def test_ttm_histogram_regresses_close_minus_the_donchian_sma_midline():
    df = _fixture("ABBV")
    c, h, l = df["close"], df["high"], df["low"]
    mid = ((h.rolling(20).max() + l.rolling(20).min()) / 2 + c.rolling(20).mean()) / 2
    want = b4.linreg_end(c - mid, 20)
    pd.testing.assert_series_equal(b4.ttm_histogram(c, h, l, 20), want, check_names=False)


# ---- the arrow rule ---------------------------------------------------------------------------

def _s(vals):
    return pd.Series(vals, index=pd.bdate_range("2026-10-01", periods=len(vals)), dtype=bool)


def test_a_streak_whose_first_day_has_the_wrong_colour_gets_its_arrow_on_the_first_agreeing_day():
    bull = _s([0, 1, 1, 1, 1, 0])
    green = _s([1, 0, 1, 1, 1, 1])                 # day 1 of the streak is a red candle
    up, down = b4.arrows(bull, _s([0] * 6), green)
    assert up.tolist() == [False, False, True, False, False, False]
    assert not down.any()


def test_a_down_arrow_needs_a_red_candle_and_an_up_arrow_a_green_one():
    on = _s([0, 1, 0, 0, 1, 0])
    green = _s([1, 1, 1, 0, 0, 0])
    up, _ = b4.arrows(on, _s([0] * 6), green)
    _, down = b4.arrows(_s([0] * 6), on, green)
    assert up.tolist() == [False, True, False, False, False, False]      # day 5 is red: no up arrow
    assert down.tolist() == [False, False, False, False, True, False]    # day 2 is green: no down arrow


def test_one_arrow_per_agreeing_run():
    bull = _s([1, 1, 1, 0, 1, 1])
    up, _ = b4.arrows(bull, _s([0] * 6), _s([1] * 6))
    assert up.tolist() == [True, False, False, False, True, False]


# ---- real data: the charts the owner reviewed on 2026-10-22 ---------------------------------

def test_abbv_arrows_match_the_reviewed_chart():
    up, down = b4.b4_arrows(_fixture("ABBV"))
    tail = slice("2026-06-15", "2026-10-21")
    assert _days(up.loc[tail]) == ["2026-06-23", "2026-08-18", "2026-09-17", "2026-10-05", "2026-10-12"]
    assert _days(down.loc[tail]) == []           # 8/6 and 9/10 were green candles: hidden


def test_afrm_streak_arrow_lands_on_day_two():
    up, _ = b4.b4_arrows(_fixture("AFRM"))
    assert "2026-10-07" in _days(up)
    assert "2026-10-06" not in _days(up)         # day 1 of the 10/6-10/9 streak was a red candle
