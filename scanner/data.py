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


def load_universe(paths) -> list[str]:
    """Load and merge several watchlist files into one deduped, order-preserving
    list (first file's order wins, later files append only their new symbols).
    A missing/unreadable file is skipped, so an optional futures.csv is safe to
    omit from a checkout without breaking the scan."""
    seen: dict[str, None] = {}
    for path in paths:
        try:
            syms = load_watchlist(path)
        except OSError:  # missing file, permission, etc. -> skip this source
            continue
        for sym in syms:
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
    out = out.sort_index()
    out.index = pd.to_datetime(out.index)
    kept = out.dropna(subset=["open", "high", "low", "close"])
    # Dropping the NEWEST row is the 2026-09-21 failure mode: the source returns
    # a partially-filled bar, dropna removes it, and the symbol's "latest
    # completed session" silently falls back days. Never do that quietly.
    if not out.empty and not kept.empty and kept.index.max() < out.index.max():
        print(f"  [warn] {symbol}: source returned NaN OHLC for "
              f"{out.index.max().strftime('%Y-%m-%d')} — newest usable bar is "
              f"{kept.index.max().strftime('%Y-%m-%d')}")
    return kept


def drop_forming_bar(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Drop the last bar if it is the current trading day and the market hasn't
    closed yet. yfinance appends an in-progress bar intraday; a swing scanner
    must only evaluate completed daily sessions.

    `now` is injectable for testing; defaults to the current US/Eastern time.

    NOTE: this is correct (drops the incomplete current-day bar), but its result
    depends on `now`. Any code that RE-DERIVES a signal at request time (vs the
    post-close daily scan) can therefore land on an earlier bar than the scan —
    that skew, plus yfinance re-adjusting history, is why `trade` anchors to the
    persisted scan snapshot instead of re-deriving. Do not re-derive on the hot path.
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


_NAME_CACHE: dict[str, str | None] = {}


def company_name(symbol: str, fetch=None) -> str | None:
    """Best-effort human name for a ticker (e.g. 'NVIDIA Corporation', 'SPDR
    Gold Shares') so alerts read for a non-expert. Cached per process; returns
    None if unavailable — callers must degrade to the bare ticker.

    Network via yfinance unless `fetch` is injected (fetch(symbol) -> name|None),
    which keeps this unit-testable and offline.
    """
    if symbol in _NAME_CACHE:
        return _NAME_CACHE[symbol]
    name = None
    try:
        if fetch is not None:
            name = fetch(symbol)
        else:
            import yfinance as yf

            info = yf.Ticker(symbol).info or {}
            # longName is the full, un-truncated name ("Invesco DB US Dollar
            # Index Bullish Fund"); shortName is often cut mid-word.
            name = info.get("longName") or info.get("shortName")
    except Exception as exc:  # network/parse hiccup — never break the alert
        print(f"  [warn] no company name for {symbol}: {exc}")
        name = None
    name = str(name).strip() if name else ""
    if len(name) > 42:  # keep the caption tidy on a phone
        name = name[:41].rstrip() + "…"
    result = name or None
    _NAME_CACHE[symbol] = result
    return result


def _frames_from(raw, symbols, drop_forming: bool) -> dict[str, pd.DataFrame]:
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


def fetch_daily(
    symbols: list[str],
    period: str = "2y",
    adjust: bool = True,
    drop_forming: bool = True,
    *,
    download=None,
    retry_stale: bool = True,
) -> dict[str, pd.DataFrame]:
    """Download daily bars for each symbol. Returns {symbol: canonical frame}.
    Symbols that return no data are omitted (logged to stdout). By default the
    current-day forming bar is dropped so signals reflect completed sessions.

    RETRY (2026-09-21 incident): the bulk threaded request fails PARTIALLY --
    some symbols come back without the latest session, and the scan then
    computes their signals from an older bar. Any symbol whose newest bar is
    behind the best session in the batch is re-requested on its own, unthreaded.
    Re-requesting the few laggards usually succeeds, which is the difference
    between losing a night's real signals and a slightly slower scan. If the
    retry also fails the stale frame is kept as-is, so the downstream guard in
    scan.build_results can suppress the signal rather than present a bad one.

    `download` is injectable for tests (defaults to yfinance.download).
    """
    if download is None:
        import yfinance as yf
        download = yf.download

    raw = download(tickers=symbols, period=period, interval="1d",
                   auto_adjust=adjust, group_by="ticker", progress=False,
                   threads=True)
    result = _frames_from(raw, symbols, drop_forming)
    if not (retry_stale and result):
        return result

    session = max(f.index[-1] for f in result.values())
    stale = sorted(s for s, f in result.items() if f.index[-1] < session)
    if not stale:
        return result

    # Cap: the retry exists for a per-symbol NaN glitch affecting a FEW tickers.
    # When most of the batch is behind, it is a calendar divergence (futures
    # trade on days equities do not: 2025-01-09, 2025-07-04) or a total outage.
    # Serially re-requesting 150 symbols cannot conjure a bar that never existed;
    # it just burns ~30s and floods the log.
    if len(stale) > max(1, len(result) // 4):
        print(f"  [warn] {len(stale)}/{len(result)} symbols behind "
              f"{session.strftime('%Y-%m-%d')} — NOT refetching (looks like a "
              f"market-calendar divergence or an outage, not a per-symbol glitch)")
        return result

    print(f"  [warn] {len(stale)} symbol(s) behind the session "
          f"({session.strftime('%Y-%m-%d')}): {', '.join(stale)} — refetching")
    try:
        # threads=False on purpose: the threaded bulk call is the suspect.
        raw2 = download(tickers=stale, period=period, interval="1d",
                        auto_adjust=adjust, group_by="ticker", progress=False,
                        threads=False)
        retried = _frames_from(raw2, stale, drop_forming)
    except Exception as exc:          # never let a retry break the scan
        print(f"  [warn] refetch failed: {exc}")
        return result

    # A newer last bar is not sufficient: a truncated retry frame would push the
    # symbol under scan_frames' 205-bar floor, where it silently vanishes from
    # the scan. Require the history to be intact too.
    recovered = [
        s for s, f in retried.items()
        if f.index[-1] > result[s].index[-1] and len(f) >= len(result[s])
    ]
    rejected = [s for s, f in retried.items()
                if f.index[-1] > result[s].index[-1] and len(f) < len(result[s])]
    if rejected:
        print(f"  [warn] refetch returned a SHORTER history for {', '.join(sorted(rejected))}"
              f" — keeping the original frame")
    for s in recovered:
        result[s] = retried[s]
    still = sorted(set(stale) - set(recovered))
    print(f"  [warn] refetch recovered {len(recovered)}/{len(stale)}"
          + (f"; STILL STALE: {', '.join(still)}" if still else ""))
    return result


def fetch_weekly(
    symbols: list[str],
    period: str = "10y",
    adjust: bool = True,
) -> dict[str, pd.DataFrame]:
    """Download native weekly bars (interval='1wk') for each symbol -> {symbol:
    canonical frame}. Used for the MTF composite's weekly panel, which matches
    TOS better than resampling daily. The current (forming) week is KEPT — it is
    the live week-to-date bar the chart should show."""
    import yfinance as yf

    raw = yf.download(
        tickers=symbols, period=period, interval="1wk", auto_adjust=adjust,
        group_by="ticker", progress=False, threads=True,
    )
    result: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        frame = normalize(raw, sym)
        if not frame.empty:
            result[sym] = frame
    return result
