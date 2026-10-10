"""The 'A' early-buy tier: 6/7 bull conditions with MACD still below zero but
RISING (the KO 2026-07-27 state, one bar before scanner_bull can fire)."""

import pandas as pd

from scanner import notify, scan, signals


def _bull_frame(macd_prev, macd_now):
    """Two bars where every non-MACD bull condition is met; only macd_diff varies."""
    return pd.DataFrame({
        "squeeze_on": [True, True], "rsi": [60.0, 60.0], "ppo": [1.0, 1.0],
        "ema8": [10.0, 10.0], "ema21": [9.0, 9.0], "ema34": [8.0, 8.0],
        "sma50": [7.0, 7.0], "sma200": [6.0, 6.0],
        "macd_diff": [macd_prev, macd_now],
        "moxie_up": [True, True], "moxie_dn": [False, False], "moxie_w": [2.0, 2.1],
    })


def test_scanner_bull_a_when_macd_negative_and_rising():
    r = signals.confluence(_bull_frame(-0.36, -0.23))   # KO 7/27 shape
    assert bool(r["scanner_bull_a"].iloc[1]) is True
    assert bool(r["scanner_bull"].iloc[1]) is False      # not yet the full signal


def test_full_signal_when_macd_crosses_green_not_a():
    r = signals.confluence(_bull_frame(-0.23, 0.18))    # KO 7/28 shape
    assert bool(r["scanner_bull"].iloc[1]) is True
    assert bool(r["scanner_bull_a"].iloc[1]) is False    # mutually exclusive


def test_no_a_when_macd_falling():
    r = signals.confluence(_bull_frame(-0.20, -0.36))   # negative but FALLING
    assert bool(r["scanner_bull_a"].iloc[1]) is False
    assert bool(r["scanner_bull"].iloc[1]) is False


def test_no_a_when_another_condition_missing():
    f = _bull_frame(-0.36, -0.23)
    f.loc[1, "rsi"] = 40.0          # RSI fails -> not even an A (needs all other 6)
    r = signals.confluence(f)
    assert bool(r["scanner_bull_a"].iloc[1]) is False


def test_rank_fired_puts_a_plus_above_a():
    ps = [{"direction": "bull", "grade": "A", "score": 80},
          {"direction": "bull", "grade": "A++", "score": 70}]
    ranked = scan.rank_fired(ps)
    assert ranked[0]["grade"] == "A++"   # full signal ranks above early, despite lower score


def _bear_frame(macd_prev, macd_now):
    """Two bars where every non-MACD bear condition is met; only macd_diff varies."""
    return pd.DataFrame({
        "squeeze_on": [True, True], "rsi": [40.0, 40.0], "ppo": [-1.0, -1.0],
        "ema8": [8.0, 8.0], "ema21": [9.0, 9.0], "ema34": [10.0, 10.0],
        "sma50": [6.0, 6.0], "sma200": [7.0, 7.0],
        "macd_diff": [macd_prev, macd_now],
        "moxie_up": [False, False], "moxie_dn": [True, True], "moxie_w": [-2.0, -2.1],
    })


def test_scanner_bear_a_when_macd_positive_and_falling():
    r = signals.confluence(_bear_frame(0.36, 0.23))     # mirror of KO 7/27
    assert bool(r["scanner_bear_a"].iloc[1]) is True
    assert bool(r["scanner_bear"].iloc[1]) is False      # not yet the full sell


def test_full_sell_when_macd_crosses_red_not_a():
    r = signals.confluence(_bear_frame(0.23, -0.18))    # crossed below zero
    assert bool(r["scanner_bear"].iloc[1]) is True
    assert bool(r["scanner_bear_a"].iloc[1]) is False


def test_no_bear_a_when_macd_rising():
    r = signals.confluence(_bear_frame(0.20, 0.36))     # positive but RISING
    assert bool(r["scanner_bear_a"].iloc[1]) is False
    assert bool(r["scanner_bear"].iloc[1]) is False


def test_fired_line_marks_a_sell_distinctly():
    p = {"symbol": "XYZ", "direction": "bear", "grade": "A", "date": "2026-07-27",
         "close": 50.0, "rsi": 43.0, "target_up": 55.0, "target_dn": 45.0,
         "stop": 52.0, "score": 30.0}
    line = notify._fired_line(p, show_ladder=True)
    assert "A-SELL" in line
    assert "not yet red" in line
    assert "🔽 MACD" in line          # MACD shown as not-yet-met (falling) in the ladder


def _payload(grade):
    return {"symbol": "KO", "direction": "bull", "grade": grade, "date": "2026-07-27",
            "close": 82.15, "rsi": 57.0, "target_up": 90.0, "target_dn": 75.0,
            "stop": 80.0, "score": 60.0}


def test_fired_line_marks_a_distinctly():
    line = notify._fired_line(_payload("A"), show_ladder=True)
    assert "A-BUY" in line
    assert "early A" in line
    assert "🔼 MACD" in line          # MACD shown as not-yet-met in the ladder
    assert "✅ Squeeze" in line       # the other conditions still met


def test_fired_line_full_signal_is_green_all_met():
    line = notify._fired_line(_payload("A++"), show_ladder=True)
    assert "🟢 BUY" in line
    assert "🔼 MACD" not in line       # full signal shows MACD met
    assert "✅ MACD" in line


# ---- 2026-10-10: the A buy needs weekly Moxie ABOVE ZERO, not rising ---------
# XOM 10/06-10/07: all six daily conditions met, weekly Moxie +1.6 (> 0) but
# below the prior week's +1.98, so the old "Moxie rising" gate held the A buy
# back until 10/08. The owner's TOS A column has no Moxie gate at all; the owner
# chose "Moxie > 0 only" for the A tier. A++ still needs Moxie rising.

def test_a_buy_fires_when_weekly_moxie_above_zero_but_not_rising():
    f = _bull_frame(-0.36, -0.23)
    f["moxie_up"] = [False, False]      # not higher than last week
    f["moxie_w"] = [1.98, 1.68]         # but still above zero
    r = signals.confluence(f)
    assert bool(r["scanner_bull_a"].iloc[1]) is True


def test_no_a_buy_when_weekly_moxie_below_zero():
    f = _bull_frame(-0.36, -0.23)
    f["moxie_up"] = [False, False]
    f["moxie_w"] = [-0.40, -0.50]
    r = signals.confluence(f)
    assert bool(r["scanner_bull_a"].iloc[1]) is False


def test_a_plus_plus_still_needs_moxie_rising():
    f = _bull_frame(-0.23, 0.18)        # MACD green -> the A++ shape
    f["moxie_up"] = [False, False]
    f["moxie_w"] = [1.98, 1.68]
    r = signals.confluence(f)
    assert bool(r["scanner_bull"].iloc[1]) is False


def test_xom_a_buy_on_2026_10_06():
    d = pd.read_csv("tests/fixtures/XOM_2026-10-09.csv", index_col=0, parse_dates=True)
    for day in ("2026-10-06", "2026-10-07"):
        assert signals.analyze(d.loc[:day]).iloc[-1]["grade"] == "A", day


def test_a_caption_marks_moxie_above_zero_but_not_rising():
    p = dict(_payload("A"), moxie_up=False)
    line = notify._fired_line(p, show_ladder=True)
    assert "🟡 Moxie&gt;0" in line
    assert "✅ Moxie" not in line


def test_a_caption_moxie_checked_when_rising():
    line = notify._fired_line(dict(_payload("A"), moxie_up=True), show_ladder=True)
    assert "✅ Moxie↑" in line
