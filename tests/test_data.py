"""Tests for the data layer: watchlist parsing and normalizing yfinance frames
into the canonical OHLC shape the engine expects.

The live network fetch (fetch_daily) is intentionally not unit-tested here -- it
is exercised by a separate smoke run. These tests pin the pure transforms so a
yfinance shape change or a messy watchlist can't silently corrupt signals.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from scanner import data

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Watchlist parsing
# ---------------------------------------------------------------------------

def test_load_watchlist_skips_header_and_uppercases(tmp_path):
    f = tmp_path / "wl.csv"
    f.write_text("Ticker\nqqq\nspy\n")
    assert data.load_watchlist(f) == ["QQQ", "SPY"]


def test_load_watchlist_drops_blanks_and_dedupes_preserving_order(tmp_path):
    f = tmp_path / "wl.csv"
    f.write_text("Ticker\nAAPL\n\n  MSFT  \nAAPL\n")
    assert data.load_watchlist(f) == ["AAPL", "MSFT"]


# ---------------------------------------------------------------------------
# Normalizing a single-ticker yfinance frame (fields as columns)
# ---------------------------------------------------------------------------

def _single_ticker_frame():
    idx = pd.to_datetime(["2024-01-03", "2024-01-02"])  # deliberately unsorted
    return pd.DataFrame(
        {
            "Open": [101.0, 100.0],
            "High": [102.0, 101.0],
            "Low": [99.0, 98.0],
            "Close": [101.5, 100.5],
            "Adj Close": [101.5, 100.5],
            "Volume": [1000, 1100],
        },
        index=idx,
    )


def test_normalize_single_ticker_lowercases_and_sorts():
    out = data.normalize(_single_ticker_frame(), "QQQ")
    assert list(out.columns[:4]) == ["open", "high", "low", "close"]
    assert out.index.is_monotonic_increasing
    assert out["close"].iloc[0] == pytest.approx(100.5)  # earliest date first


def test_normalize_drops_rows_with_missing_values():
    df = _single_ticker_frame()
    df.loc[df.index[0], "Close"] = np.nan
    out = data.normalize(df, "QQQ")
    assert len(out) == 1  # the NaN-close row is dropped


# ---------------------------------------------------------------------------
# Normalizing a multi-ticker yfinance frame (MultiIndex columns)
# ---------------------------------------------------------------------------

def test_normalize_extracts_symbol_from_multiindex_columns():
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    cols = pd.MultiIndex.from_product(
        [["QQQ", "SPY"], ["Open", "High", "Low", "Close", "Volume"]]
    )
    raw = pd.DataFrame(np.arange(len(idx) * len(cols)).reshape(len(idx), len(cols)),
                       index=idx, columns=cols, dtype=float)
    out = data.normalize(raw, "SPY")
    assert list(out.columns[:4]) == ["open", "high", "low", "close"]
    # SPY columns are the second block; close should match raw[("SPY","Close")]
    assert out["close"].iloc[-1] == pytest.approx(raw[("SPY", "Close")].iloc[-1])


def test_normalize_empty_frame_returns_empty_canonical():
    out = data.normalize(pd.DataFrame(), "QQQ")
    assert out.empty
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Forming-bar drop: yfinance appends an incomplete current-day bar intraday.
# The swing scanner must evaluate only completed daily sessions.
# ---------------------------------------------------------------------------

def _three_day_frame(last_day):
    idx = pd.to_datetime(["2026-06-25", "2026-06-26", last_day])
    return pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=idx)


def test_drop_forming_bar_removes_todays_bar_before_close():
    df = _three_day_frame("2026-06-29")
    now = datetime(2026, 6, 29, 10, 0, tzinfo=ET)  # mid-session
    out = data.drop_forming_bar(df, now=now)
    assert out.index[-1].date() == date(2026, 6, 26)
    assert len(out) == 2


def test_drop_forming_bar_keeps_todays_bar_after_close():
    df = _three_day_frame("2026-06-29")
    now = datetime(2026, 6, 29, 16, 30, tzinfo=ET)  # after the 4pm close
    out = data.drop_forming_bar(df, now=now)
    assert out.index[-1].date() == date(2026, 6, 29)
    assert len(out) == 3


def test_drop_forming_bar_keeps_when_last_bar_is_a_prior_day():
    df = _three_day_frame("2026-06-26")  # last bar already complete
    now = datetime(2026, 6, 29, 10, 0, tzinfo=ET)
    out = data.drop_forming_bar(df, now=now)
    assert len(out) == 3
