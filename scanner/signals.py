"""The B3 Scanner_Signal confluence, assembled from the indicator primitives.

`Scanner_Signal` (the strongest B3 dot) fires bullish only when all six agree on
the daily bar:
    aggressive squeeze ON, RSI > 50, PPO >= 0, EMA8 > EMA21,
    full EMA stack (8 > 21 > 34 and SMA50 > SMA200), and MACD Diff rising.
Bearish is the mirror.

Moxie (higher-timeframe momentum) is not part of the scanner gate; it's used to
grade a firing signal (A++ when the weekly Moxie agrees) so the dashboard can
rank the strongest setups first. ATR Stop levels are attached for execution.
"""

import numpy as np
import pandas as pd

from scanner import indicators as ind


def macd_rising(diff: pd.Series) -> pd.Series:
    """B3 macdBull: green or orange MACD color.

    (diff >= 0 and diff > diff[1]) or (diff < 0 and diff >= diff[1]).
    """
    prev = diff.shift(1)
    return ((diff >= 0) & (diff > prev)) | ((diff < 0) & (diff >= prev))


def macd_falling(diff: pd.Series) -> pd.Series:
    """B3 macdBear: blue or red MACD color (the complement of macd_rising)."""
    prev = diff.shift(1)
    return ((diff >= 0) & (diff <= prev)) | ((diff < 0) & (diff < prev))


def macd_green(diff: pd.Series) -> pd.Series:
    """Green MACD: at/above zero AND rising. The user's buy requires this
    ('macd line is green crossing/above zero line')."""
    return (diff >= 0) & (diff > diff.shift(1))


def macd_red(diff: pd.Series) -> pd.Series:
    """Red MACD: at/below zero AND falling (mirror of macd_green)."""
    return (diff <= 0) & (diff < diff.shift(1))


def confluence(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the user's trading process and add scanner_bull / scanner_bear.

    Expects columns: squeeze_on, rsi, ppo, ema8, ema21, ema34, sma50, sma200,
    macd_diff, moxie_up, moxie_dn.

    BUY  = squeeze + RSI>50 + PPO>=0 + EMA8>EMA21 + full stack + MACD green + Moxie up.
    SELL = the mirror.
    """
    out = df.copy()

    bull_stacked = (
        (out["ema8"] > out["ema21"])
        & (out["ema21"] > out["ema34"])
        & (out["sma50"] > out["sma200"])
    )
    bear_stacked = (
        (out["ema8"] < out["ema21"])
        & (out["ema21"] < out["ema34"])
        & (out["sma50"] < out["sma200"])
    )
    out["macd_green"] = macd_green(out["macd_diff"])
    out["macd_red"] = macd_red(out["macd_diff"])

    out["scanner_bull"] = (
        out["squeeze_on"]
        & (out["rsi"] > 50)
        & (out["ppo"] >= 0)
        & (out["ema8"] > out["ema21"])
        & bull_stacked
        & out["macd_green"]
        & out["moxie_up"]
    )
    out["scanner_bear"] = (
        out["squeeze_on"]
        & (out["rsi"] < 50)
        & (out["ppo"] < 0)
        & (out["ema8"] < out["ema21"])
        & bear_stacked
        & out["macd_red"]
        & out["moxie_dn"]
    )
    return out


def analyze(daily: pd.DataFrame) -> pd.DataFrame:
    """Compute every indicator on a daily OHLC frame, align weekly Moxie, and
    apply the confluence. Returns the frame enriched with signal columns.

    `daily` must have columns open/high/low/close and a DatetimeIndex.
    """
    out = daily.copy()
    close, high, low = out["close"], out["high"], out["low"]

    out["ema8"] = ind.ema(close, 8)
    out["ema21"] = ind.ema(close, 21)
    out["ema34"] = ind.ema(close, 34)
    out["sma50"] = ind.sma(close, 50)
    out["sma200"] = ind.sma(close, 200)
    out["rsi"] = ind.rsi(close, 14)
    out["ppo"] = ind.ppo(close, 10, 20)
    out["macd_diff"] = ind.macd_diff(close, 12, 26, 34)
    out["atr"] = ind.atr(high, low, close, 14)
    out["squeeze_on"] = ind.squeeze_on(close, high, low, 20, 2.0, 2.0)

    # Higher-timeframe Moxie: compute on weekly close. Color is a weekly
    # bar-over-bar change (green = rising), so derive up/down on the weekly
    # series, then forward-fill the value and the gates onto daily bars.
    weekly = ind.resample_to_weekly(out[["open", "high", "low", "close"]])
    moxie_w = ind.moxie(weekly["close"])
    moxie_green = (moxie_w > 0) & (moxie_w >= moxie_w.shift(1))  # above zero AND rising
    moxie_red = (moxie_w < 0) & (moxie_w <= moxie_w.shift(1))    # below zero AND falling

    # TOS uses the CONTAINING (current) week's higher-timeframe Moxie and repaints
    # it across that week's days -> backfill each daily bar to its containing week
    # (not the prior completed week). For the live latest bar and the walk-forward
    # backtest this is week-to-date (no lookahead); on historical mid-week bars it
    # repaints, exactly as TOS does. Both the signal gate and the display use this.
    def _gate(flags: pd.Series) -> pd.Series:
        r = flags.reindex(out.index, method="bfill")
        return r.where(r.notna(), False).astype(bool)

    out["moxie_w"] = moxie_w.reindex(out.index, method="bfill")
    out["moxie_up"] = _gate(moxie_green)
    out["moxie_dn"] = _gate(moxie_red)
    out["moxie_rising"] = _gate(moxie_w >= moxie_w.shift(1))

    out = confluence(out)

    grade = pd.Series("", index=out.index)
    grade = grade.mask(out["scanner_bull"] | out["scanner_bear"], "A++")
    out["grade"] = grade
    return out


B3_ROWS = ["scanner", "mo_aaa", "mix", "sqz", "sqzstack", "stack1", "structure"]


def b3_rows(daily: pd.DataFrame) -> pd.DataFrame:
    """Reproduce the 7 rows of the B3 Super dots study as a per-bar state frame.

    Each column holds 'bull' / 'bear' / 'none' (or 'neutral' for the two stack
    rows). This is the study AS WRITTEN — it uses the original B3 Scanner_Signal
    (MACD rising, no Moxie-green gate), so it is independent of the customized
    buy signal in `analyze`.
    """
    close, high, low = daily["close"], daily["high"], daily["low"]
    ema8, ema21, ema34 = ind.ema(close, 8), ind.ema(close, 21), ind.ema(close, 34)
    sma50, sma200 = ind.sma(close, 50), ind.sma(close, 200)
    ppo = ind.ppo(close, 10, 20)
    rsi = ind.rsi(close, 14)
    diff = ind.macd_diff(close, 12, 26, 34)
    sqz = ind.squeeze_on(close, high, low, 20, 2.0, 2.0)

    rsiup, rsidn = rsi > 50, rsi < 50
    bulltrend, beartrend = ppo >= 0, ppo < 0
    bullstruct, bearstruct = ema8 > ema21, ema8 < ema21
    bullstack = (ema8 > ema21) & (ema21 > ema34) & (sma50 > sma200)
    bearstack = (ema8 < ema21) & (ema21 < ema34) & (sma50 < sma200)
    bullstack1 = (ema8 > ema21) & (ema21 > ema34) & (ema34 > sma50)
    bearstack1 = (ema8 < ema21) & (ema21 < ema34) & (ema34 < sma50)
    macdbull, macdbear = macd_rising(diff), macd_falling(diff)

    # Higher-timeframe (weekly) Moxie: slow (12/26) and fast (3/8), ffilled to daily.
    wk = ind.resample_to_weekly(daily[["open", "high", "low", "close"]])
    mox = ind.moxie(wk["close"])
    mox_fast = ind.moxie(wk["close"], fast=3, slow=8, signal=9)

    def _ff_bool(s):
        # forward-fill = last COMPLETED weekly bar (matches TOS higher-timeframe,
        # which holds the prior week's value until the current week closes).
        r = s.reindex(daily.index, method="ffill")
        return r.where(r.notna(), False).astype(bool)

    def _ff_val(s):
        return s.reindex(daily.index, method="ffill")

    moxw, moxfw = _ff_val(mox), _ff_val(mox_fast)
    # Per "b3 stripped down": macdCol = Moxie > 0 (NOT Moxie rising).
    macd_col1 = moxw > 0
    macd_col4 = moxfw > 0
    buyzone = ppo >= 0

    bullfull1 = macd_col1 & macd_col4 & buyzone
    bearfull1 = (moxw <= 0) & (moxfw <= 0) & (~buyzone)
    bullmoxie, bearmoxie = (moxw > 0) & (moxfw > 0), (moxw <= 0) & (moxfw <= 0)
    bullsqz, bearsqz = sqz & rsiup & bulltrend & bullstruct, sqz & rsidn & beartrend & bearstruct
    bullsqzst, bearsqzst = bullsqz & bullstack, bearsqz & bearstack
    scanbull = sqz & rsiup & bulltrend & bullstruct & bullstack & macdbull
    scanbear = sqz & rsidn & beartrend & bearstruct & bearstack & macdbear

    def st(bull, bear, neutral="none"):
        return np.where(bull, "bull", np.where(bear, "bear", neutral))

    out = pd.DataFrame(index=daily.index)
    out["scanner"] = st(scanbull, scanbear)
    out["mo_aaa"] = st(bullfull1, bearfull1)
    out["mix"] = st(bullmoxie, bearmoxie)
    out["sqz"] = st(bullsqz, bearsqz)
    out["sqzstack"] = st(bullsqzst, bearsqzst)
    out["stack1"] = st(bullstack1, bearstack1, neutral="neutral")
    out["structure"] = st(bullstruct, bearstruct, neutral="neutral")
    return out


def condition_breakdown(daily: pd.DataFrame, on_date=None) -> dict:
    """Per-indicator breakdown for one bar: each of the 7 buy conditions with its
    raw value and pass/fail, so it can be cross-checked layer-by-layer vs TOS.
    """
    out = analyze(daily)
    row = out.iloc[-1] if on_date is None else out.loc[on_date]
    stack_pass = bool(
        row["ema8"] > row["ema21"] > row["ema34"] and row["sma50"] > row["sma200"]
    )
    direction = "bull" if row["scanner_bull"] else "bear" if row["scanner_bear"] else "none"
    return {
        "symbol": None,
        "date": row.name.strftime("%Y-%m-%d"),
        "close": float(row["close"]),
        "squeeze_on": bool(row["squeeze_on"]),
        "rsi": float(row["rsi"]), "rsi_pass": bool(row["rsi"] > 50),
        "ppo": float(row["ppo"]), "ppo_pass": bool(row["ppo"] >= 0),
        "ema8": float(row["ema8"]), "ema21": float(row["ema21"]),
        "ema34": float(row["ema34"]), "sma50": float(row["sma50"]),
        "sma200": float(row["sma200"]),
        "structure_pass": bool(row["ema8"] > row["ema21"]),
        "stack_pass": stack_pass,
        "macd_diff": float(row["macd_diff"]),
        "macd_pass": bool(row["macd_green"]),
        "moxie_w": float(row["moxie_w"]) if pd.notna(row["moxie_w"]) else None,
        "moxie_pass": bool(row["moxie_up"]),
        "direction": direction,
    }


def latest_signal(daily: pd.DataFrame, symbol: str | None = None) -> dict:
    """Evaluate the most recent bar and return the scanner payload for one symbol.

    Levels follow the ATR Stop study: target = EMA21 +/- ATR*2.5, stop = close - ATR*1.5.
    """
    out = analyze(daily)
    last = out.iloc[-1]

    if bool(last["scanner_bull"]):
        direction = "bull"
    elif bool(last["scanner_bear"]):
        direction = "bear"
    else:
        direction = "none"

    ema21 = float(last["ema21"])
    atr = float(last["atr"])
    close = float(last["close"])

    return {
        "symbol": symbol,
        "date": out.index[-1].strftime("%Y-%m-%d"),
        "direction": direction,
        "grade": str(last["grade"]),
        "close": close,
        "rsi": float(last["rsi"]),
        "ppo": float(last["ppo"]),
        "squeeze_on": bool(last["squeeze_on"]),
        "moxie_w": float(last["moxie_w"]) if pd.notna(last["moxie_w"]) else None,
        "target_up": round(ema21 + atr * 2.5, 4),
        "target_dn": round(ema21 - atr * 2.5, 4),
        "stop": round(close - atr * 1.5, 4),
    }
