"""Data layer: load the watchlist and pull daily OHLC bars via yfinance,
normalized into the canonical frame the engine expects (open/high/low/close/
volume, ascending DatetimeIndex).

Adjustment note (a parity knob to confirm against TOS with the 5 cases):
`adjust=True` returns split+dividend back-adjusted prices, which avoids fake
gaps on split dates and keeps indicators continuous. TOS charts are
split-adjusted; dividend adjustment shifts levels slightly but is consistent.
Flip to `adjust=False` for raw prices if validation says TOS disagrees.
"""

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CANONICAL_COLS = ["open", "high", "low", "close", "volume"]

_MARKET_TZ = ZoneInfo("America/New_York")
_MARKET_CLOSE = time(16, 0)  # 4:00pm ET


def load_watchlist(path) -> list[str]:
    """Read a watchlist file: one ticker per line, optional 'Ticker' header.
    Uppercases, drops blanks, dedupes while preserving order.
    """
    lines = Path(path).read_text().splitlines()
    seen: dict[str, None] = {}
    for raw in lines:
        sym = raw.strip().upper()
        if not sym or sym == "TICKER":
            continue
        seen.setdefault(sym, None)
    return list(seen.keys())


def normalize(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Turn a yfinance frame (single-ticker fields, or MultiIndex over tickers)
    into the canonical OHLC frame for one symbol."""
    empty = pd.DataFrame(columns=CANONICAL_COLS)
    if df is None or len(df) == 0:
        return empty

    data = df
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = df.columns.get_level_values(0)
        lvl1 = df.columns.get_level_values(1)
        if symbol in set(lvl0):
            data = df[symbol]
        elif symbol in set(lvl1):
            data = df.xs(symbol, axis=1, level=1)
        else:
            return empty

    # Case-insensitive field lookup (Open/High/Low/Close/Volume).
    fields = {str(c).lower(): c for c in data.columns}
    if not {"open", "high", "low", "close"} <= fields.keys():
        return empty

    out = pd.DataFrame(
        {
            "open": data[fields["open"]],
            "high": data[fields["high"]],
            "low": data[fields["low"]],
            "close": data[fields["close"]],
            "volume": data[fields["volume"]] if "volume" in fields else pd.NA,
        }
    )
    out = out.dropna(subset=["open", "high", "low", "close"]).sort_index()
    out.index = pd.to_datetime(out.index)
    return out


def drop_forming_bar(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Drop the last bar if it is the current trading day and the market hasn't
    closed yet. yfinance appends an in-progress bar intraday; a swing scanner
    must only evaluate completed daily sessions.

    `now` is injectable for testing; defaults to the current US/Eastern time.
    """
    if df.empty:
        return df
    if now is None:
        now = datetime.now(_MARKET_TZ)
    now = now.astimezone(_MARKET_TZ)

    last_date = df.index[-1].date()
    if last_date == now.date() and now.time() < _MARKET_CLOSE:
        return df.iloc[:-1]
    return df


def fetch_daily(
    symbols: list[str],
    period: str = "2y",
    adjust: bool = True,
    drop_forming: bool = True,
) -> dict[str, pd.DataFrame]:
    """Download daily bars for each symbol. Returns {symbol: canonical frame}.
    Symbols that return no data are omitted (logged to stdout). By default the
    current-day forming bar is dropped so signals reflect completed sessions."""
    import yfinance as yf

    raw = yf.download(
        tickers=symbols,
        period=period,
        interval="1d",
        auto_adjust=adjust,
        group_by="ticker",
        progress=False,
        threads=True,
    )

    result: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        frame = normalize(raw, sym)
        if drop_forming:
            frame = drop_forming_bar(frame)
        if frame.empty:
            print(f"  [warn] no data for {sym}")
            continue
        result[sym] = frame
    return result
