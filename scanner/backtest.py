"""Walk-forward backtest harness for the signal engine.

Two pieces:
- `signal_history`: recompute the signal at each historical bar using ONLY data
  up to that bar (point-in-time, no lookahead). This is the honest way to test a
  signal that uses higher-timeframe (weekly Moxie) inputs.
- `simulate_trade` / `backtest` / `summarize`: model a swing trade from each
  fired signal using the ATR Stop levels, and tally win rate / expectancy.

Intrabar order-of-touch is unknowable from daily bars; when a single bar touches
both the stop and the target we count it as a stop (conservative).
"""

import pandas as pd

from scanner import signals


def signal_history(df: pd.DataFrame, warmup: int = 210) -> pd.Series:
    """Direction at each bar, computed walk-forward (point-in-time).

    Returns a Series indexed by date with values in {'bull', 'bear', 'none'},
    starting after `warmup` bars (need ~200 for SMA200 + weekly Moxie).
    """
    directions = {}
    for i in range(warmup, len(df)):
        row = signals.analyze(df.iloc[: i + 1]).iloc[-1]
        directions[df.index[i]] = (
            "bull" if row["scanner_bull"] else "bear" if row["scanner_bear"] else "none"
        )
    return pd.Series(directions, dtype="object")


def trade_levels(close: float, ema21: float, atr: float, entry: float,
                 direction: str, mode: str = "entry",
                 target_mult: float = 2.5, stop_mult: float = 1.5):
    """Compute (target, stop) for a trade.

    mode="entry": target/stop relative to the entry price (sane R:R for breakout
      entries). mode="ema21": reproduces the ATR Stop study (anchored to EMA21);
      this can put the target below entry when price is extended.
    """
    long = direction == "bull"
    anchor = entry if mode == "entry" else ema21
    if long:
        return anchor + target_mult * atr, entry - stop_mult * atr if mode == "entry" else close - stop_mult * atr
    return anchor - target_mult * atr, entry + stop_mult * atr if mode == "entry" else close + stop_mult * atr


def simulate_trade(bars: pd.DataFrame, entry_price: float, target: float,
                   stop: float, direction: str) -> dict:
    """Simulate one trade over the given hold-window bars (high/low/close).

    Long: target above, stop below. Short: target below, stop above.
    Returns outcome ('win'/'loss'/'time'), exit price, bars held, R multiple,
    and return pct.
    """
    long = direction == "bull"
    risk = abs(entry_price - stop)
    outcome, exit_price, bars_held = "time", float(bars["close"].iloc[-1]), len(bars)

    for n, (_, b) in enumerate(bars.iterrows(), start=1):
        hit_stop = b["low"] <= stop if long else b["high"] >= stop
        hit_target = b["high"] >= target if long else b["low"] <= target
        if hit_stop:  # conservative: stop checked before target
            outcome, exit_price, bars_held = "loss", stop, n
            break
        if hit_target:
            outcome, exit_price, bars_held = "win", target, n
            break

    signed = (exit_price - entry_price) if long else (entry_price - exit_price)
    return {
        "outcome": outcome,
        "exit_price": float(exit_price),
        "bars_held": bars_held,
        "r_multiple": signed / risk if risk else 0.0,
        "return_pct": (exit_price - entry_price) / entry_price * (1 if long else -1),
    }


def backtest(df: pd.DataFrame, symbol: str, max_hold: int = 5,
             warmup: int = 210, window=None, level_mode: str = "entry") -> list[dict]:
    """Run the walk-forward backtest. Entry is next bar's open after a signal.
    `window` optionally restricts to (start_date, end_date) for the signal date.
    """
    enriched = signals.analyze(df)
    hist = signal_history(df, warmup=warmup)
    trades = []

    for sig_date, direction in hist.items():
        if direction == "none":
            continue
        if window and not (window[0] <= sig_date <= window[1]):
            continue
        loc = df.index.get_loc(sig_date)
        if loc + 1 >= len(df):  # no next bar to enter on
            continue

        sig_row = enriched.loc[sig_date]
        entry_idx = loc + 1
        entry_price = float(df["open"].iloc[entry_idx])
        target, stop = trade_levels(
            close=float(sig_row["close"]), ema21=float(sig_row["ema21"]),
            atr=float(sig_row["atr"]), entry=entry_price,
            direction=direction, mode=level_mode,
        )

        hold = df.iloc[entry_idx: entry_idx + max_hold][["high", "low", "close"]]
        result = simulate_trade(hold, entry_price, target, stop, direction)
        trades.append({
            "symbol": symbol,
            "signal_date": sig_date.strftime("%Y-%m-%d"),
            "entry_date": df.index[entry_idx].strftime("%Y-%m-%d"),
            "direction": direction,
            "entry": round(entry_price, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            **result,
        })
    return trades


def summarize(trades: list[dict]) -> dict:
    """Win rate and expectancy (in R) across a list of trades."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "win_rate": None, "expectancy_r": None}
    wins = sum(1 for t in trades if t["outcome"] == "win")
    avg_r = sum(t["r_multiple"] for t in trades) / n
    return {"n": n, "win_rate": wins / n, "expectancy_r": avg_r}
