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

import statistics

import pandas as pd

from scanner import options, signals


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


def option_leg(entry_underlying: float, exit_underlying: float, direction: str,
               iv: float, bars_held: int, *, target_dte: int = 21,
               r: float = 0.043) -> dict:
    """Model a Playbook-B option over a trade: buy a near-ATM contract (strike =
    entry underlying, ~`target_dte` DTE) at entry, reprice it at exit on the
    actual underlying with the time decayed by `bars_held`. Returns the entry/
    exit premium, the return on premium, and a win/loss/flat outcome.

    IV is supplied by the caller (the backtest uses realized vol as the proxy —
    there is no historical option chain). Decay counts bars_held as days, which
    slightly understates calendar theta but does so uniformly across trades.
    """
    kind = "call" if direction != "bear" else "put"
    K = entry_underlying  # exact ATM
    entry_prem = options.black_scholes(
        entry_underlying, K, target_dte / 365.0, r, iv, kind)["price"]
    exit_prem = options.black_scholes(
        exit_underlying, K, max(target_dte - bars_held, 0) / 365.0, r, iv, kind)["price"]
    ret = (exit_prem - entry_prem) / entry_prem if entry_prem > 0 else 0.0
    outcome = ("win" if exit_prem > entry_prem
               else "loss" if exit_prem < entry_prem else "flat")
    return {"kind": kind, "strike": K, "dte": target_dte,
            "entry_premium": entry_prem, "exit_premium": exit_prem,
            "option_return": ret, "option_outcome": outcome}


def simulate_hold(bars: pd.DataFrame, hold_days: int) -> tuple[int, float]:
    """Fixed-hold exit (the E3 rule): exit at the close of bar `hold_days`,
    ignoring target/stop. Near the data end (fewer bars than hold_days) exit at
    the last available close. Returns (bars_held, exit_price)."""
    h = bars.iloc[:hold_days]
    return len(h), float(h["close"].iloc[-1])


def backtest(df: pd.DataFrame, symbol: str, *, exit: str = "hold",
             hold_days: int = 3, max_hold: int = 5, warmup: int = 210,
             window=None, level_mode: str = "entry", target_dte: int = 21,
             iv_window: int = 20, r: float = 0.043,
             risk_budget: float = 500.0) -> list[dict]:
    """Walk-forward backtest with an option P&L leg. Entry is next bar's open.

    exit="hold" (default): fixed `hold_days`-day hold, no stop (the validated E3
    rule). exit="targets": the original ATR target/stop within `max_hold` bars.
    Either way the modeled Playbook-B option is priced entry->exit. Each trade
    record carries both the equity result and the option leg. `window`
    optionally restricts to (start_date, end_date) for the signal date.
    """
    enriched = signals.analyze(df)
    hist = signal_history(df, warmup=warmup)
    closes = df["close"]
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
        if len(hold) == 0:
            continue

        if exit == "targets":
            equity = simulate_trade(hold, entry_price, target, stop, direction)
            bars_held, exit_price = equity["bars_held"], equity["exit_price"]
        else:  # fixed hold (E3)
            bars_held, exit_price = simulate_hold(hold, hold_days)
            long = direction == "bull"
            signed = (exit_price - entry_price) if long else (entry_price - exit_price)
            risk = abs(entry_price - stop)
            equity = {
                "outcome": "win" if signed > 0 else "loss" if signed < 0 else "flat",
                "exit_price": float(exit_price), "bars_held": bars_held,
                "r_multiple": signed / risk if risk else 0.0,
                "return_pct": signed / entry_price if entry_price else 0.0,
            }

        iv = options.realized_vol(closes.iloc[: loc + 1], window=iv_window)
        leg = option_leg(entry_price, float(exit_price), direction, iv,
                         bars_held, target_dte=target_dte, r=r)
        entry_prem = leg["entry_premium"]
        contracts = (max(1, int(risk_budget // (entry_prem * 100.0)))
                     if entry_prem > 0 else 0)
        option_cash = contracts * (leg["exit_premium"] - entry_prem) * 100.0

        trades.append({
            "symbol": symbol,
            "signal_date": sig_date.strftime("%Y-%m-%d"),
            "entry_date": df.index[entry_idx].strftime("%Y-%m-%d"),
            "direction": direction,
            "entry": round(entry_price, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            **equity,
            "iv": round(iv, 4),
            "strike": round(leg["strike"], 2),
            "dte": leg["dte"],
            "entry_premium": round(entry_prem, 4),
            "exit_premium": round(leg["exit_premium"], 4),
            "option_return": leg["option_return"],
            "option_outcome": leg["option_outcome"],
            "contracts": contracts,
            "option_cash": round(option_cash, 2),
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


def summarize_option(trades: list[dict]) -> dict:
    """Option-vehicle win rate, expectancy (mean return on premium), median
    return, and total cash. Skips trades with no priced premium."""
    rows = [t for t in trades if t.get("entry_premium", 0) > 0]
    n = len(rows)
    if n == 0:
        return {"n": 0, "win_rate": None, "expectancy_pct": None,
                "median_pct": None, "total_cash": 0.0}
    rets = [t["option_return"] for t in rows]
    wins = sum(1 for r in rets if r > 0)
    return {"n": n, "win_rate": wins / n, "expectancy_pct": sum(rets) / n,
            "median_pct": statistics.median(rets),
            "total_cash": sum(t.get("option_cash", 0.0) for t in rows)}


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
                 symbols: list[str] | None = None, *, exit: str = "hold",
                 hold_days: int = 3) -> list[dict]:
    """Walk-forward backtest across every symbol in the universe file.
    Symbols that error are skipped with a warning (one bad ticker must not
    kill a multi-hour run)."""
    from scanner import data

    syms = symbols or data.load_watchlist(universe_path)
    frames = data.fetch_daily(syms, period=period)
    trades: list[dict] = []
    for sym, df in frames.items():
        try:
            trades.extend(backtest(df, sym, exit=exit, hold_days=hold_days,
                                   max_hold=max_hold))
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
    ap.add_argument("--exit", choices=["hold", "targets"], default="hold",
                    help="hold = fixed N-day exit (E3, default); targets = ATR target/stop")
    ap.add_argument("--hold-days", type=int, default=3,
                    help="days held when --exit hold (validated optimum = 3)")
    ap.add_argument("--max-hold", type=int, default=5)
    ap.add_argument("--out", default="out/backtest.json")
    ap.add_argument("--symbols", default=None,
                    help="comma-separated subset for a quick smoke run")
    args = ap.parse_args(argv)

    subset = args.symbols.split(",") if args.symbols else None
    trades = run_universe(args.universe, period=args.period,
                          max_hold=args.max_hold, symbols=subset,
                          exit=args.exit, hold_days=args.hold_days)
    summary = {
        "exit": args.exit, "hold_days": args.hold_days,
        "equity": {**summarize(trades), **extended_stats(trades)},
        "option": summarize_option(trades),
    }
    doc = {"summary": summary, "trades": trades}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2))
    print(json.dumps(summary, indent=2))
    return doc


if __name__ == "__main__":
    main()
