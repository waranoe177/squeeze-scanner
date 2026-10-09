"""The v5 + REV-gate multi-timeframe table logic (spec §2-§4)."""

import math

import pandas as pd
import pytest

from scanner import mtf
from scanner.indicators import ema, macd_diff, rev_eng_rsi, rsi_exp

B1, B2, B3 = mtf.Cell("B", 1, False), mtf.Cell("B", 2, False), mtf.Cell("B", 3, True)
S1, S2 = mtf.Cell("S", 1, False), mtf.Cell("S", 2, True)
E = mtf.Cell(None, 0, False)


def _frame(sym):
    df = pd.read_csv(f"tests/fixtures/{sym}.csv", index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


# ---- rsi_exp ---------------------------------------------------------------

def test_rsi_exp_hand_computed():
    # length 2 -> alpha 2/3. chg = [nan, 1, -1, 2]
    # net = [nan, 1, -1/3, 11/9], tot = [nan, 1, 1, 5/3] -> ratio [nan, 1, -1/3, 11/15]
    r = rsi_exp(pd.Series([10.0, 11.0, 10.0, 12.0]), 2)
    assert math.isnan(r.iloc[0])
    assert r.iloc[1:].tolist() == pytest.approx([100.0, 100 / 3, 260 / 3])


def test_rsi_exp_flat_series_is_50():
    assert rsi_exp(pd.Series([5.0] * 4), 2).iloc[1:].tolist() == [50.0, 50.0, 50.0]


# ---- the rule --------------------------------------------------------------

@pytest.mark.parametrize("args, want", [
    ((70, 0.5, 0.4, 99, 100), "B"),
    ((70, 0.0, -0.1, 99, 100), "B"),        # diff >= 0 includes zero
    ((66, 0.5, 0.4, 99, 100), None),        # 66 is not above 66
    ((70, 0.4, 0.5, 99, 100), None),        # histogram falling
    ((70, 0.5, 0.4, 100, 100), None),       # REV tie blocks both sides
    ((70, 0.5, 0.4, 101, 100), None),       # REV red blocks a buy
    ((30, -0.5, -0.4, 101, 100), "S"),
    ((30, -0.5, -0.4, 99, 100), None),      # REV green blocks a sell
    ((34, -0.5, -0.4, 101, 100), None),     # 34 is not below 34
    ((float("nan"), 0.5, 0.4, 99, 100), None),
])
def test_bar_side(args, want):
    assert mtf.bar_side(*args) == want


def test_signal_flags_agree_with_bar_side_on_every_bar():
    bars = _frame("QQQ")
    buy, sell = mtf.signal_flags(bars)
    c = bars["close"]
    r, d, rev, leg = rsi_exp(c, 14), macd_diff(c), rev_eng_rsi(c), ema(c, 21)
    for i in range(1, len(c)):
        want = mtf.bar_side(r.iloc[i], d.iloc[i], d.iloc[i - 1], rev.iloc[i], leg.iloc[i])
        got = "B" if buy.iloc[i] else "S" if sell.iloc[i] else None
        assert got == want, bars.index[i]
    assert buy.any() and sell.any()     # the fixture exercises both sides


# ---- run counts and cells --------------------------------------------------

@pytest.mark.parametrize("flags, n", [
    ([False, True, True], 2), ([True, False, True], 1), ([True, True, False], 0), ([], 0),
])
def test_run_length(flags, n):
    assert mtf.run_length(pd.Series(flags, dtype=bool)) == n


def test_cell_from_and_text():
    t, f = pd.Series([False, True, True, True]), pd.Series([False] * 4)
    assert mtf.cell_from(t, f, True) == mtf.Cell("B", 3, True)
    assert mtf.cell_from(f, t, False) == mtf.Cell("S", 3, False)
    assert mtf.cell_from(f, f, True) == mtf.Cell(None, 0, True)
    assert mtf.Cell("B", 3, True).text() == "* B: 3"
    assert mtf.Cell("S", 1, False).text() == "S: 1"
    assert mtf.Cell(None, 0, True).text() == "—"


def test_cell_for_short_history_is_empty():
    assert mtf.cell_for(_frame("QQQ").iloc[:10]).side is None
    assert mtf.cell_for(_frame("QQQ").iloc[:0]) == mtf.Cell(None, 0, False)


def test_timeframe_bars():
    daily = _frame("QQQ")
    assert list(mtf.timeframe_bars(daily, 1).columns) == ["open", "high", "low", "close"]
    assert len(mtf.timeframe_bars(daily, 3)) < len(daily)


# ---- rows, order, record ---------------------------------------------------

def test_qualifies_and_direction():
    assert mtf.qualifies((B1, B2, E, E))
    assert not mtf.qualifies((B1, E, E, S1))
    assert mtf.Row("X", (S1, S2, S1, E)).direction == "sell"
    assert mtf.Row("X", (B1, B2, S1, E)).direction == "buy"
    assert mtf.Row("X", (B1, B2, S1, S2)).direction == "mixed"


def test_sort_is_tos_wk_column_then_lower_timeframes():
    rows = [
        mtf.Row("ZZ", (E, B1, B1, E)),     # no WK: led by 3D buy
        mtf.Row("SS", (S2, S1, E, E)),     # WK sell run 2
        mtf.Row("AA", (B3, B1, E, E)),     # WK buy run 3
        mtf.Row("BB", (B1, B1, E, E)),     # WK buy run 1
        mtf.Row("CC", (S1, S1, E, E)),     # WK sell run 1
        mtf.Row("AB", (B1, E, B2, E)),     # WK buy run 1, ties on symbol
        mtf.Row("DD", (E, E, S1, S1)),     # led by 2D sell
    ]
    got = [r.symbol for r in sorted(rows, key=mtf.sort_key)]
    assert got == ["AB", "BB", "AA", "CC", "SS", "ZZ", "DD"]
    assert mtf.lead(rows[0]) == (1, "B")


def test_display_name():
    assert mtf.display_name("ES=F") == "/ES"
    assert mtf.display_name("PM") == "PM"


def test_build_table_skips_empty_and_stale_frames(monkeypatch):
    monkeypatch.setattr(mtf, "cell_for", lambda bars: B1)   # every timeframe "fires"
    fresh = _frame("QQQ")
    as_of = fresh.index[-1].strftime("%Y-%m-%d")
    frames = {"QQQ": fresh, "ES=F": fresh, "OLD": fresh.iloc[:-1], "NONE": fresh.iloc[:0]}
    rows = mtf.build_table(frames, as_of)
    assert [r.symbol for r in rows] == ["/ES", "QQQ"]
    assert [r.symbol for r in mtf.build_table(frames, None)] == ["/ES", "OLD", "QQQ"]


def test_build_table_on_real_fixtures_is_sorted():
    frames = {s: _frame(s) for s in ("IYT", "QQQ", "RSP", "DIA", "XLRE")}
    rows = mtf.build_table(frames, None)
    assert [mtf.sort_key(r) for r in rows] == sorted(mtf.sort_key(r) for r in rows)
    assert all(mtf.qualifies(r.cells) for r in rows)


def test_counts_and_record():
    rows = [mtf.Row("A", (B1, B2, E, E)), mtf.Row("B", (S1, S2, E, E)),
            mtf.Row("C", (B1, B2, S1, S2))]
    assert mtf.counts(rows) == {"buy": 1, "sell": 1, "mixed": 1}
    rec = mtf.table_record(rows, "2026-10-09")
    assert rec["date"] == "2026-10-09"
    assert rec["timeframes"] == ["WK", "3D", "2D", "1D"]
    assert rec["rows"][0]["cells"]["WK"] == {"side": "B", "run": 1, "squeeze": False,
                                              "text": "B: 1"}
    assert rec["rows"][2]["direction"] == "mixed"
