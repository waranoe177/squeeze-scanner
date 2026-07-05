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


def extended_stats(trades: list[dict]) -> dict:
    """Max consecutive losing trades and max drawdown of cumulative R,
    with trades ordered by entry_date (then signal_date as tiebreak)."""
    ordered = sorted(trades, key=lambda t: (t["entry_date"], t["signal_date"]))
    cum = peak = max_dd = 0.0
    streak = max_streak = 0
    for t in ordered:
        r = t["r_multiple"]
        cum += r
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        streak = streak + 1 if r < 0 else 0
        max_streak = max(max_streak, streak)
    return {"max_losing_streak": max_streak, "max_drawdown_r": round(max_dd, 3)}


def run_universe(universe_path: str, period: str = "5y", max_hold: int = 5,
                 symbols: list[str] | None = None) -> list[dict]:
    """Walk-forward backtest across every symbol in the universe file.
    Symbols that error are skipped with a warning (one bad ticker must not
    kill a multi-hour run)."""
    from scanner import data

    syms = symbols or data.load_watchlist(universe_path)
    frames = data.fetch_daily(syms, period=period)
    trades: list[dict] = []
    for sym, df in frames.items():
        try:
            trades.extend(backtest(df, sym, max_hold=max_hold))
            print(f"  {sym}: done ({len(trades)} trades total)")
        except Exception as exc:
            print(f"  [warn] backtest failed for {sym}: {exc}")
    return trades


def main(argv=None) -> dict:
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser(description="Universe walk-forward backtest (Phase 0)")
    ap.add_argument("--universe", default="universe.csv")
    ap.add_argument("--period", default="5y")
    ap.add_argument("--max-hold", type=int, default=5)
    ap.add_argument("--out", default="out/backtest.json")
    ap.add_argument("--symbols", default=None,
                    help="comma-separated subset for a quick smoke run")
    args = ap.parse_args(argv)

    subset = args.symbols.split(",") if args.symbols else None
    trades = run_universe(args.universe, period=args.period,
                          max_hold=args.max_hold, symbols=subset)
    summary = {**summarize(trades), **extended_stats(trades)}
    doc = {"summary": summary, "trades": trades}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2))
    print(json.dumps(summary, indent=2))
    return doc


if __name__ == "__main__":
    main()
