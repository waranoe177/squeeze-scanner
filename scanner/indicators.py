"""Indicator primitives that mirror the ThinkScript in Sqzdots/Indicators.

Each function reproduces a specific TOS construct so the scanner agrees with the
chart. Parity choices that aren't obvious are called out inline:

- EMA  -> ThinkScript ExpAverage: standard EMA, alpha = 2/(len+1), seeded at the
  first bar. pandas ewm(span, adjust=False) reproduces this exactly.
- ATR  -> WildersAverage of TrueRange (alpha = 1/len), used by the ATR Stop study.
- RSI  -> ThinkScript's reverse formula RSI = 50 * (ChgRatio + 1), Wilders-smoothed.
- TTM Squeeze -> Bollinger(close) contained inside Keltner(close, simple-avg TR).
  The B3 code uses the *aggressive* squeeze: bb_mult = kc_mult = 2.0, length 20.
- Moxie -> (EMA(p,12) - EMA(p,26) - EMA(that, 9)) * 3, computed on the higher
  timeframe (weekly for a daily chart).
"""

import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    """ThinkScript ExpAverage: EMA seeded at the first value, alpha = 2/(len+1)."""
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    """Simple trailing moving average."""
    return series.rolling(length).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Wilders TrueRange. First bar (no prior close) reduces to high - low."""
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)  # skipna -> first bar uses high-low only


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    """Average True Range, Wilders-smoothed (matches ATR Stop's WILDERS setting)."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / length, adjust=False).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """ThinkScript RSI = 50 * (ChgRatio + 1), Wilders-smoothed net / total change."""
    chg = close.diff()
    net = chg.ewm(alpha=1.0 / length, adjust=False).mean()
    tot = chg.abs().ewm(alpha=1.0 / length, adjust=False).mean()
    ratio = (net / tot).where(tot != 0, 0.0)
    return 50.0 * (ratio + 1.0)


def macd_diff(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 34) -> pd.Series:
    """MACD histogram (Diff) = MACD line - signal line, all EMA-based."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return macd_line - signal_line


def ppo(close: pd.Series, fast: int = 10, slow: int = 20) -> pd.Series:
    """Percentage Price Oscillator: ((fastEMA - slowEMA) / slowEMA) * 100."""
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    return (fast_ema - slow_ema) / slow_ema * 100.0


def bollinger(close: pd.Series, length: int = 20, mult: float = 2.0):
    """Bollinger bands. ThinkScript StDev is population std (ddof=0)."""
    basis = sma(close, length)
    dev = close.rolling(length).std(ddof=0)
    return basis + mult * dev, basis - mult * dev


def keltner(close: pd.Series, high: pd.Series, low: pd.Series, length: int = 20, mult: float = 2.0):
    """Keltner channels as used inside TTM Squeeze: SMA(close) basis, simple
    average of TrueRange for the band width."""
    basis = sma(close, length)
    band = true_range(high, low, close).rolling(length).mean()
    return basis + mult * band, basis - mult * band


def squeeze_on(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    length: int = 20,
    bb_mult: float = 2.0,
    kc_mult: float = 2.0,
) -> pd.Series:
    """TTM squeeze ON: Bollinger bands sit inside the Keltner channels."""
    bb_u, bb_l = bollinger(close, length, bb_mult)
    kc_u, kc_l = keltner(close, high, low, length, kc_mult)
    return (bb_u < kc_u) & (bb_l > kc_l)


def rescaled_rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Mobius 'Chaikin Money Flow RSI' cloud driver: standard Wilders RSI
    rescaled across the series' full range to span -100..+100. Background is
    green where this is > 0, red where < 0.
    """
    r = rsi(close, length)
    lo, hi = r.min(), r.max()
    if hi == lo:
        return r - r
    return 200.0 * (r - lo) / (hi - lo) - 100.0


def rev_eng_rsi(close: pd.Series, length: int = 14, rsi_value: float = 50.0) -> pd.Series:
    """Reverse-engineered RSI (REV RSI study): the price level at which RSI would
    equal `rsi_value` (default 50), Wilders-smoothed. Used to color candles:
    the study paints the bar red when RevEngRSI > EMA21, green otherwise.
    """
    coeff = rsi_value / (100 - rsi_value)
    chg = close.diff()
    up = chg.clip(lower=0)
    dn = (-chg).clip(lower=0)
    avg_up = up.ewm(alpha=1.0 / length, adjust=False).mean()
    avg_dn = dn.ewm(alpha=1.0 / length, adjust=False).mean()
    diff = (length - 1) * (avg_dn * coeff - avg_up)
    value = close + diff.where(diff >= 0, diff / coeff)
    return value.shift(1)  # ThinkScript compoundValue(1, value[1], NaN)


def moxie(price: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """Watkins Moxie: (vc1 - EMA(vc1, signal)) * 3, vc1 = EMA(p,fast) - EMA(p,slow).

    Pass the *higher timeframe* price (e.g. weekly close) to match B3 usage.
    The B3 Super dots study uses a slow Moxie (12/26) and a fast Moxie (3/8).
    """
    vc1 = ema(price, fast) - ema(price, slow)
    va1 = ema(vc1, signal)
    return (vc1 - va1) * 3.0


def resample_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample a daily OHLC frame (DatetimeIndex) to weekly bars."""
    weekly = df.resample("W").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    return weekly.dropna(how="all")


# ---------------------------------------------------------------------------
# Major Pivots (major_pivots_count_occur: usethinkscript.com/threads/15354)
#
# A "major" pivot is a centered fractal swing high/low that price later
# re-approaches ("retests"). len=13 -> `len - 1` = 12 bars on each side, matching
# the ThinkScript (Lowest(low[1], len-1) on the left; Lowest(low, len) evaluated
# `len-1` bars forward on the right). The last `len-1` bars can't be confirmed
# yet because they have no future window — TOS repaints the same way.
# ---------------------------------------------------------------------------

def find_fractal_pivots(high, low, length: int = 13):
    """Centered fractal pivots. Returns (peak_positions, valley_positions) as
    lists of integer positions. A peak is strictly above the prior `length-1`
    highs and is the max over the +/-(length-1) window; valley is symmetric."""
    h = pd.Series(high).to_numpy(dtype=float)
    l = pd.Series(low).to_numpy(dtype=float)
    half = length - 1
    n = len(h)
    peaks: list[int] = []
    valleys: list[int] = []
    for i in range(half, n - half):
        left_h = h[i - half:i]
        win_h = h[i - half:i + half + 1]
        if h[i] > left_h.max() and h[i] == win_h.max():
            peaks.append(i)
        left_l = l[i - half:i]
        win_l = l[i - half:i + half + 1]
        if l[i] < left_l.min() and l[i] == win_l.min():
            valleys.append(i)
    return peaks, valleys


def count_retests(level: float, high, low, start: int, tol: float) -> int:
    """Number of times price re-enters the zone `[level-tol, level+tol]` after
    leaving it, counting bars strictly after `start`. A stretch of consecutive
    in-zone bars is ONE event, so a level price hovers near for weeks counts a
    handful of retests, not dozens."""
    h = pd.Series(high).to_numpy(dtype=float)
    l = pd.Series(low).to_numpy(dtype=float)
    touches = 0
    in_zone = False
    for j in range(start + 1, len(h)):
        hit = (h[j] >= level - tol) and (l[j] <= level + tol)
        if hit and not in_zone:
            touches += 1
        in_zone = hit
    return touches


def major_pivots(
    high,
    low,
    atr=None,
    length: int = 13,
    min_retests: int = 1,
    tol_atr: float = 0.4,
    tol_pct: float = 0.0035,
) -> list[dict]:
    """Centered fractal pivots that were later retested at least `min_retests`
    times. Returns dicts sorted by position:
    ``{"kind": "peak"|"valley", "pos": int, "level": float, "retests": int}``.

    The retest tolerance is `max(level*tol_pct, tol_atr*ATR[at the pivot])` when
    an `atr` series is supplied, else the percentage band alone — so the zone
    scales with the stock's volatility."""
    peaks, valleys = find_fractal_pivots(high, low, length)
    h = pd.Series(high).to_numpy(dtype=float)
    l = pd.Series(low).to_numpy(dtype=float)
    atr_arr = pd.Series(atr).to_numpy(dtype=float) if atr is not None else None

    out: list[dict] = []
    for kind, positions, levels in (("peak", peaks, h), ("valley", valleys, l)):
        for i in positions:
            level = levels[i]
            tol = abs(level) * tol_pct
            if atr_arr is not None:
                tol = max(tol, tol_atr * atr_arr[i])
            retests = count_retests(level, high, low, start=i, tol=tol)
            if retests >= min_retests:
                out.append({"kind": kind, "pos": int(i),
                            "level": float(level), "retests": int(retests)})
    out.sort(key=lambda p: p["pos"])
    return out
