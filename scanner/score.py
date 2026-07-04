"""Conviction score + expected value for a ticker.

Decodes the user's "read the dots bottom-to-top, then eyeball the EMAs" process
into a number:

- Confluence base (0-60): which rungs of the ladder are lit (Structure -> Stack
  -> Squeeze -> Sqz+Stack -> MACD green -> Moxie green -> Scanner). Every FIRED
  ticker clears the ladder (60); watching tickers score partial.
- Strength (0-40): what separates two fired tickers. Each momentum measure is
  percentile-ranked over the ticker's OWN 1y history, so different-priced names
  compare fairly.

Also returns R:R, ATR%, and (via the backtest) a historical expected value.
"""

import pandas as pd

from scanner import backtest as bt
from scanner import signals

# Confluence ladder weights (sum = CONFLUENCE_MAX). Higher rungs = more confluence.
_LADDER = {
    "structure_pass": 6,
    "stack_pass": 10,
    "squeeze_on": 10,
    "sqz_stack": 8,      # squeeze AND stack
    "macd_pass": 8,
    "moxie_pass": 12,
    "scanner": 6,        # full buy confluence
}
CONFLUENCE_MAX = sum(_LADDER.values())  # 60


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def confluence_points(flags: dict):
    """Sum the ladder points for the rungs that are lit. `flags` uses the
    condition_breakdown keys plus 'scanner'; 'sqz_stack' is derived."""
    lit = dict(flags)
    lit["sqz_stack"] = bool(flags.get("squeeze_on") and flags.get("stack_pass"))
    total = sum(pts for key, pts in _LADDER.items() if lit.get(key))
    return float(total), lit


def grade_for(sc: float) -> str:
    if sc >= 85:
        return "A+"
    if sc >= 70:
        return "A"
    if sc >= 55:
        return "B"
    return "C"


def pct_rank(series: pd.Series, value: float) -> float:
    """Fraction of history at or below `value` (0..1). Cross-ticker comparable."""
    s = series.dropna()
    if len(s) == 0:
        return 0.5
    return float((s <= value).mean())


def _squeeze_freshness(scanner_bull: pd.Series) -> float:
    """1.0 if the signal just fired, decaying to 0 over ~5 bars of a sustained run."""
    b = scanner_bull.to_numpy()
    if not b[-1]:
        return 0.0
    run = 0
    for v in b[::-1]:
        if v:
            run += 1
        else:
            break
    return _clamp(1.0 - (run - 1) / 5.0)


def conviction(df: pd.DataFrame, symbol: str | None = None, hist: int = 252) -> dict:
    """Full conviction score for the latest bar."""
    out = signals.analyze(df)
    last = out.iloc[-1]
    bd = signals.condition_breakdown(df)
    payload = signals.latest_signal(df, symbol=symbol)

    flags = {
        "structure_pass": bd["structure_pass"],
        "stack_pass": bd["stack_pass"],
        "squeeze_on": bd["squeeze_on"],
        "macd_pass": bd["macd_pass"],
        "moxie_pass": bd["moxie_pass"],
        "scanner": bd["direction"] == "bull",
    }
    confluence, _ = confluence_points(flags)

    tail = out.tail(hist)
    rsi_str = _clamp((last["rsi"] - 50) / 20.0)                  # 50->0, 70->1
    macd_pr = pct_rank(tail["macd_diff"], last["macd_diff"])
    ppo_pr = pct_rank(tail["ppo"], last["ppo"])
    moxie_pr = pct_rank(tail["moxie_w"], last["moxie_w"])
    fresh = _squeeze_freshness(out["scanner_bull"])

    entry = payload["close"]
    stop = payload["stop"]
    target = payload["target_up"] if bd["direction"] != "bear" else payload["target_dn"]
    risk = abs(entry - stop)
    rr = abs(target - entry) / risk if risk else 0.0
    atr_pct = (risk / 1.5) / entry * 100 if entry else 0.0   # ATR as % of price

    momentum = (rsi_str * 8) + (macd_pr * 8) + (ppo_pr * 4)     # 0-20
    moxie_s = (moxie_pr * 8) + (4 if bd["moxie_pass"] else 0)   # 0-12
    fresh_s = fresh * 4                                          # 0-4
    rr_s = _clamp(rr / 3.0) * 4                                  # 0-4
    strength = momentum + moxie_s + fresh_s + rr_s              # 0-40

    total = round(confluence + strength, 1)
    return {
        "symbol": symbol,
        "date": bd["date"],
        "direction": bd["direction"],
        "score": total,
        "grade": grade_for(total),
        "confluence": round(confluence, 1),
        "strength": round(strength, 1),
        "rr": round(rr, 2),
        "atr_pct": round(atr_pct, 2),
        "parts": {
            "momentum": round(momentum, 1),
            "moxie": round(moxie_s, 1),
            "freshness": round(fresh_s, 1),
            "risk_reward": round(rr_s, 1),
        },
    }


def expected_value(df: pd.DataFrame, symbol: str, max_hold: int = 5) -> dict:
    """Rough historical expectancy of this ticker's past fires (walk-forward
    backtest). Small samples are noisy — n is returned so it can be caveated."""
    trades = bt.backtest(df, symbol, max_hold=max_hold, level_mode="ema21")
    s = bt.summarize(trades)
    return {
        "ev_r": round(s["expectancy_r"], 3) if s["expectancy_r"] is not None else None,
        "win_rate": round(s["win_rate"], 2) if s["win_rate"] is not None else None,
        "n": s["n"],
    }
