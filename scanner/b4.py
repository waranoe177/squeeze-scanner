"""B4 arrows: the owner's TOS "declare upper b4" study, with a REV-RSI candle-colour gate.

The study (2026-10-22):
  bull = aggressive squeeze ON (Bollinger 2.0 inside Keltner 2.0, length 20)
         and EMA8 > EMA21 and RSI(14, Wilders) >= 50 and FullK > FullD (Stochastic 14,3,3, simple)
         and the TTM squeeze histogram rising two bars (h > h[1] > h[2]).
  bear = the mirror (EMA8 < EMA21, RSI < 50, FullK < FullD, histogram falling two bars).

Owner rules added on top:
  - an up arrow needs a GREEN REV-RSI candle and a down arrow a RED one (the chart colours a candle
    green when rev_rsi <= EMA21 of close);
  - one arrow per streak, on the first day the signal AND the colour agree, so a streak whose first day
    has the wrong colour still gets its arrow on the next agreeing day.

Kept in its own module so scanner/indicators.py and scanner/signals.py stay byte-identical to the copies
the sqz-autotrader alert bot vendors.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scanner import indicators as ind


def stoch_full(high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14,
               slowing: int = 3, d_period: int = 3) -> tuple[pd.Series, pd.Series]:
    """ThinkScript StochasticFull with simple averages: FastK over k_period bars, FullK = SMA(FastK,
    slowing), FullD = SMA(FullK, d_period). A flat window (no range) gives FastK 0, as the study does."""
    lowest = low.rolling(k_period).min()
    rng = high.rolling(k_period).max() - lowest
    fast_k = ((close - lowest) / rng * 100.0).where(rng != 0, 0.0)
    full_k = fast_k.rolling(slowing).mean()
    return full_k, full_k.rolling(d_period).mean()


def linreg_end(y: pd.Series, n: int) -> pd.Series:
    """The least-squares line's value at the last bar of each n-bar window (ThinkScript Inertia)."""
    x = np.arange(n, dtype=float)
    xm = x.mean()
    sxx = float(((x - xm) ** 2).sum())

    def end(w: np.ndarray) -> float:
        ym = w.mean()
        return ym + float(((x - xm) * (w - ym)).sum()) / sxx * (x[-1] - xm)

    return y.rolling(n).apply(end, raw=True)


def ttm_histogram(close: pd.Series, high: pd.Series, low: pd.Series, length: int = 20) -> pd.Series:
    """TTM_Squeeze().Histogram: the linear-regression value of close minus the average of the Donchian
    midline and the SMA."""
    mid = ((high.rolling(length).max() + low.rolling(length).min()) / 2 + close.rolling(length).mean()) / 2
    return linreg_end(close - mid, length)


def b4_signals(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(bull, bear) on every bar, before the colour gate and the one-arrow rule."""
    c, h, l = df["close"], df["high"], df["low"]
    e8, e21 = ind.ema(c, 8), ind.ema(c, 21)
    r = ind.rsi(c, 14)
    k, d = stoch_full(h, l, c, 14, 3, 3)
    hist = ttm_histogram(c, h, l, 20)
    h1, h2 = hist.shift(1), hist.shift(2)
    sq = ind.squeeze_on(c, h, l, 20, bb_mult=2.0, kc_mult=2.0)
    bull = sq & (e8 > e21) & (r >= 50) & (k > d) & (hist > h1) & (h1 > h2)
    bear = sq & (e8 < e21) & (r < 50) & (k < d) & (hist < h1) & (h1 < h2)
    return bull.fillna(False).astype(bool), bear.fillna(False).astype(bool)


def candle_green(df: pd.DataFrame) -> pd.Series:
    """The chart's REV-RSI candle colour: green when rev_rsi <= EMA21 of close."""
    c = df["close"]
    return (ind.rev_eng_rsi(c, 14) <= ind.ema(c, 21)).fillna(False).astype(bool)


def arrows(bull: pd.Series, bear: pd.Series, green: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Apply the colour gate, then keep one arrow per run: the first bar where signal and colour agree."""
    up_ok = bull & green
    down_ok = bear & ~green
    up = up_ok & ~up_ok.shift(1, fill_value=False)
    down = down_ok & ~down_ok.shift(1, fill_value=False)
    return up.astype(bool), down.astype(bool)


def b4_arrows(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(up, down) arrow bars for a daily or weekly OHLC frame."""
    bull, bear = b4_signals(df)
    return arrows(bull, bear, candle_green(df))
