"""Multi-timeframe (WK / 3D / 2D / 1D) v5 + REV-gate table: the Python twin of the
TOS watchlist column tos/Sqzdots_Col_V5_RevGate_compact.ts with its default inputs.
Spec: docs/superpowers/specs/2026-10-09-mtf-table-design.md (§2-§4). Pure functions.

  BUY   rsi_exp(14) > 66  AND  MACD diff >= 0 and rising   AND  RevEngRSI < EMA21
  SELL  rsi_exp(14) < 34  AND  MACD diff <= 0 and falling  AND  RevEngRSI > EMA21

A cell is the run of consecutive signal bars ending at the latest bar; `*` marks a
squeeze on that bar (a marker, never a filter).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from scanner.chart import agg_multiday
from scanner.indicators import ema, macd_diff, rev_eng_rsi, rsi_exp, squeeze_on

TIMEFRAMES: tuple[tuple[str, int], ...] = (("WK", 5), ("3D", 3), ("2D", 2), ("1D", 1))
RSI_LENGTH = 14
OVER_BOUGHT = 66
OVER_SOLD = 34
REV_MA_LENGTH = 21
EMPTY = "—"


@dataclass(frozen=True)
class Cell:
    side: str | None    # "B", "S" or None
    run: int            # bars the signal has held; 0 when side is None
    squeeze: bool

    def text(self) -> str:
        if self.side is None:
            return EMPTY
        return f"{'* ' if self.squeeze else ''}{self.side}: {self.run}"


@dataclass(frozen=True)
class Row:
    symbol: str                 # display name, e.g. "PM" or "/ES"
    cells: tuple[Cell, ...]     # in TIMEFRAMES order: WK, 3D, 2D, 1D

    @property
    def direction(self) -> str:
        nb = sum(c.side == "B" for c in self.cells)
        ns = sum(c.side == "S" for c in self.cells)
        if nb >= 2 and ns >= 2:
            return "mixed"
        return "buy" if nb >= 2 else "sell"


def bar_side(rsi, d0, d1, rev, leg) -> str | None:
    """One bar's verdict -- the same rule as sqz-autotrader signal.decide.
    Any NaN input means no signal."""
    if any(pd.isna(v) for v in (rsi, d0, d1, rev, leg)):
        return None
    if rsi > OVER_BOUGHT and d0 >= 0 and d0 > d1 and rev < leg:
        return "B"
    if rsi < OVER_SOLD and d0 <= 0 and d0 < d1 and rev > leg:
        return "S"
    return None


def signal_flags(bars: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Vectorised bar_side over every bar: (buy, sell) boolean Series.
    NaN comparisons are False, so warm-up bars carry no signal."""
    close = bars["close"]
    r = rsi_exp(close, RSI_LENGTH)
    d = macd_diff(close)
    d1 = d.shift(1)
    rev = rev_eng_rsi(close)
    leg = ema(close, REV_MA_LENGTH)
    buy = (r > OVER_BOUGHT) & (d >= 0) & (d > d1) & (rev < leg)
    sell = (r < OVER_SOLD) & (d <= 0) & (d < d1) & (rev > leg)
    return buy, sell


def run_length(flags: pd.Series) -> int:
    """Consecutive True values ending at the last element."""
    n = 0
    for v in reversed(flags.tolist()):
        if not v:
            break
        n += 1
    return n


def cell_from(buy: pd.Series, sell: pd.Series, squeeze: bool) -> Cell:
    nb, ns = run_length(buy), run_length(sell)
    if nb:
        return Cell("B", nb, squeeze)
    if ns:
        return Cell("S", ns, squeeze)
    return Cell(None, 0, squeeze)


def cell_for(bars: pd.DataFrame) -> Cell:
    """The latest bar's cell on one timeframe. Too little history -> empty."""
    if len(bars) == 0:
        return Cell(None, 0, False)
    buy, sell = signal_flags(bars)
    sq = squeeze_on(bars["close"], bars["high"], bars["low"])
    return cell_from(buy, sell, bool(sq.iloc[-1]))


def timeframe_bars(daily: pd.DataFrame, n: int) -> pd.DataFrame:
    ohlc = daily[["open", "high", "low", "close"]]
    return ohlc if n == 1 else agg_multiday(ohlc, n)


def qualifies(cells) -> bool:
    """At least two timeframes signal in the SAME direction."""
    return (sum(c.side == "B" for c in cells) >= 2
            or sum(c.side == "S" for c in cells) >= 2)


def lead(row: Row) -> tuple[int, str]:
    """(timeframe index, side) of the first non-empty cell in WK, 3D, 2D, 1D order."""
    for i, c in enumerate(row.cells):
        if c.side is not None:
            return i, c.side
    return len(row.cells), ""


def sort_key(row: Row) -> tuple:
    """Like the TOS watchlist sorted by the WK column: WK buys (shortest run first),
    WK sells, then rows led by 3D, 2D, 1D the same way; ties by symbol."""
    i, side = lead(row)
    run = row.cells[i].run if i < len(row.cells) else 0
    return i, 0 if side == "B" else 1, run, row.symbol


def display_name(symbol: str) -> str:
    """TOS-style futures root: 'ES=F' -> '/ES'."""
    return "/" + symbol[:-2] if symbol.endswith("=F") else symbol


def stale_symbols(frames: dict[str, pd.DataFrame], as_of: str | None) -> list[str]:
    """Symbols build_table leaves out: an empty frame, or a last bar older than as_of."""
    cutoff = pd.Timestamp(as_of) if as_of else None
    return [s for s, f in frames.items()
            if f.empty or (cutoff is not None and f.index[-1] < cutoff)]


def build_table(frames: dict[str, pd.DataFrame], as_of: str | None) -> list[Row]:
    """Qualifying rows, sorted. A frame that is empty, or whose last bar is older
    than `as_of` (a stale download), is left out."""
    cutoff = pd.Timestamp(as_of) if as_of else None
    rows = []
    for sym, daily in frames.items():
        if daily.empty or (cutoff is not None and daily.index[-1] < cutoff):
            continue
        cells = tuple(cell_for(timeframe_bars(daily, n)) for _, n in TIMEFRAMES)
        if qualifies(cells):
            rows.append(Row(display_name(sym), cells))
    return sorted(rows, key=sort_key)


def counts(rows: list[Row]) -> dict[str, int]:
    out = {"buy": 0, "sell": 0, "mixed": 0}
    for r in rows:
        out[r.direction] += 1
    return out


def table_record(rows: list[Row], as_of: str | None) -> dict:
    """The JSON record written to out/mtf_table.json."""
    return {
        "date": as_of,
        "timeframes": [tf for tf, _ in TIMEFRAMES],
        "counts": counts(rows),
        "rows": [
            {"symbol": r.symbol, "direction": r.direction,
             "cells": {tf: {"side": c.side, "run": c.run, "squeeze": c.squeeze,
                            "text": c.text()}
                       for (tf, _), c in zip(TIMEFRAMES, r.cells)}}
            for r in rows
        ],
    }
