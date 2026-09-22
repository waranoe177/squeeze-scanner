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


def test_shipped_universe_has_macro_proxies():
    """Gold/dollar/bitcoin exposure: GLD + UUP + BITO must be in the universe."""
    from pathlib import Path

    universe = Path(__file__).resolve().parent.parent / "universe.csv"
    syms = set(data.load_watchlist(universe))
    assert {"GLD", "UUP", "BITO"} <= syms


# ---------------------------------------------------------------------------
# Company name lookup (for human-readable alerts)
# ---------------------------------------------------------------------------

def test_company_name_uses_fetch_and_caches():
    data._NAME_CACHE.clear()
    calls = []

    def fake(sym):
        calls.append(sym)
        return "NVIDIA Corporation"

    assert data.company_name("NVDA", fetch=fake) == "NVIDIA Corporation"
    assert data.company_name("NVDA", fetch=fake) == "NVIDIA Corporation"  # cached
    assert calls == ["NVDA"]  # fetched only once


def test_company_name_none_on_failure():
    data._NAME_CACHE.clear()

    def boom(sym):
        raise RuntimeError("no network")

    assert data.company_name("ZZZZ", fetch=boom) is None


def test_company_name_none_when_blank():
    data._NAME_CACHE.clear()
    assert data.company_name("ZZZZ", fetch=lambda s: "  ") is None


def test_load_watchlist_drops_blanks_and_dedupes_preserving_order(tmp_path):
    f = tmp_path / "wl.csv"
    f.write_text("Ticker\nAAPL\n\n  MSFT  \nAAPL\n")
    assert data.load_watchlist(f) == ["AAPL", "MSFT"]


def test_load_universe_merges_and_dedupes_across_files(tmp_path):
    wl = tmp_path / "wl.csv"
    wl.write_text("Ticker\nAAPL\nMSFT\n")
    fut = tmp_path / "fut.csv"
    fut.write_text("Ticker\nES=F\nAAPL\n")  # AAPL duplicated across files
    assert data.load_universe([wl, fut]) == ["AAPL", "MSFT", "ES=F"]


def test_load_universe_skips_missing_files(tmp_path):
    wl = tmp_path / "wl.csv"
    wl.write_text("Ticker\nSPY\n")
    missing = tmp_path / "nope.csv"
    assert data.load_universe([wl, missing]) == ["SPY"]


def test_shipped_futures_watchlist_has_index_and_macro():
    """The futures watchlist ships the index + macro basket (=F symbols)."""
    from pathlib import Path

    fut = Path(__file__).resolve().parent.parent / "futures.csv"
    syms = set(data.load_watchlist(fut))
    assert {"ES=F", "NQ=F", "YM=F", "RTY=F",
            "GC=F", "SI=F", "CL=F", "BTC=F"} <= syms


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


# --- stale-source handling (2026-09-21 incident) -------------------------

def _multi(spec):
    """Build a yfinance-shaped group_by='ticker' frame. spec = {ticker: [dates]};
    a date a ticker lacks comes back as NaN OHLC, which is exactly how the real
    partial failure presented."""
    all_dates = sorted({d for ds in spec.values() for d in ds})
    idx = pd.to_datetime(all_dates)
    cols, cells = [], {}
    for t, ds in spec.items():
        have = set(ds)
        for f in ("Open", "High", "Low", "Close", "Volume"):
            cols.append((t, f))
            cells[(t, f)] = [1.0 if d in have else np.nan for d in all_dates]
    df = pd.DataFrame(cells, index=idx)
    df.columns = pd.MultiIndex.from_tuples(cols)
    return df


def test_normalize_warns_when_the_newest_bar_is_dropped(capsys):
    """dropna silently discarding the newest row is what made the incident
    invisible. It must be loud."""
    idx = pd.to_datetime(["2026-09-18", "2026-09-21"])
    df = pd.DataFrame({"Open": [1.0, np.nan], "High": [2.0, np.nan],
                       "Low": [0.5, np.nan], "Close": [1.5, np.nan],
                       "Volume": [10, 20]}, index=idx)
    out = data.normalize(df, "TEST")
    assert out.index[-1].strftime("%Y-%m-%d") == "2026-09-18"
    printed = capsys.readouterr().out
    assert "TEST" in printed and "2026-09-21" in printed


def test_fetch_daily_refetches_symbols_that_came_back_stale():
    """The bulk threaded download fails PARTIALLY. Re-requesting just the stale
    names usually succeeds -- that is the difference between losing a night's
    real signals and a scan that takes 40s longer."""
    calls = []

    def fake_download(**kw):
        calls.append(tuple(kw["tickers"]))
        if len(calls) == 1:
            return _multi({"GOOD": ["2026-09-18", "2026-09-21"],
                           "STALE": ["2026-09-18"]})
        return _multi({"STALE": ["2026-09-18", "2026-09-21"]})

    frames = data.fetch_daily(["GOOD", "STALE"], download=fake_download,
                              drop_forming=False)
    assert len(calls) == 2
    assert calls[1] == ("STALE",)
    assert frames["STALE"].index[-1].strftime("%Y-%m-%d") == "2026-09-21"


def test_fetch_daily_does_not_refetch_when_all_symbols_are_current():
    calls = []

    def fake_download(**kw):
        calls.append(tuple(kw["tickers"]))
        return _multi({"A": ["2026-09-18", "2026-09-21"],
                       "B": ["2026-09-18", "2026-09-21"]})

    data.fetch_daily(["A", "B"], download=fake_download, drop_forming=False)
    assert len(calls) == 1


def test_fetch_daily_keeps_the_stale_frame_when_the_refetch_also_fails():
    """Degrade, never crash -- and leave the old bar date visible so the
    downstream stale guard can suppress the signal."""
    def fake_download(**kw):
        return _multi({"GOOD": ["2026-09-18", "2026-09-21"],
                       "STALE": ["2026-09-18"]})

    frames = data.fetch_daily(["GOOD", "STALE"], download=fake_download,
                              drop_forming=False)
    assert frames["STALE"].index[-1].strftime("%Y-%m-%d") == "2026-09-18"


def test_fetch_daily_does_not_refetch_a_whole_universe_divergence():
    """If most of the batch is behind, that is a calendar divergence or a total
    outage -- never the per-symbol NaN glitch the retry exists for. Re-requesting
    150 symbols serially can never recover a bar that does not exist."""
    calls = []

    def fake_download(**kw):
        calls.append(tuple(kw["tickers"]))
        return _multi({"FUT": ["2026-07-02", "2026-07-03"],
                       "E1": ["2026-07-02"], "E2": ["2026-07-02"],
                       "E3": ["2026-07-02"], "E4": ["2026-07-02"]})

    data.fetch_daily(["FUT", "E1", "E2", "E3", "E4"], download=fake_download,
                     drop_forming=False)
    assert len(calls) == 1, "should bail, not serially refetch 4/5 of the batch"


def test_refetch_is_rejected_when_it_returns_a_shorter_history():
    """A newer last bar is not enough. A truncated frame can drop the symbol
    under scan_frames' 205-bar floor and it vanishes with no log."""
    def fake_download(**kw):
        if len(kw["tickers"]) > 1:
            return _multi({"GOOD": ["2026-09-17", "2026-09-18", "2026-09-21"],
                           "SHORT": ["2026-09-17", "2026-09-18"]})
        return _multi({"SHORT": ["2026-09-21"]})       # newer, but 1 bar

    frames = data.fetch_daily(["GOOD", "SHORT"], download=fake_download,
                              drop_forming=False)
    assert len(frames["SHORT"]) == 2, "short retry frame must be rejected"
