"""Options-vs-equity trade analyzer: an in-house Black-Scholes engine that
prices the chosen contract at its EXIT value (remaining time to expiry), sizes
equity and options to the same dollars, and picks the higher-EV vehicle.

Pure and deterministic given an injected chain; the only network is fetch_chain.
No scipy — the normal CDF is math.erf.
"""

import math
from datetime import date, timedelta

import numpy as np


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes(S, K, T, r, sigma, kind="call") -> dict:
    """European BS price + delta + per-day theta. T in years."""
    kind = kind.lower()
    if T <= 0 or sigma <= 0:
        intrinsic = max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)
        if kind == "call":
            delta = 1.0 if S > K else 0.0
        else:
            delta = -1.0 if S < K else 0.0
        return {"price": intrinsic, "delta": delta, "theta": 0.0}
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    disc = math.exp(-r * T)
    if kind == "call":
        price = S * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta_yr = (-(S * _norm_pdf(d1) * sigma) / (2 * sqrtT)
                    - r * K * disc * _norm_cdf(d2))
    else:
        price = K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta_yr = (-(S * _norm_pdf(d1) * sigma) / (2 * sqrtT)
                    + r * K * disc * _norm_cdf(-d2))
    return {"price": price, "delta": delta, "theta": theta_yr / 365.0}


def conviction_to_p(score) -> float:
    """Rough, clearly-labeled default probability from the conviction score.
    NOT calibrated — a starting point the trader overrides. Clamped [0.35,0.70]."""
    # 0.01 is a rough, uncalibrated slope: ~+1 point of confidence per
    # conviction point above 60.
    p = 0.45 + (float(score) - 60.0) * 0.01
    return max(0.35, min(0.70, p))


def realized_vol(close, window: int = 20) -> float:
    """Annualized realized volatility from daily closes (last `window` returns)."""
    rets = np.log(close / close.shift(1)).dropna()
    if len(rets) < 2:
        return 0.0
    return float(rets.tail(window).std(ddof=1) * math.sqrt(252))


def iv_context(iv: float, rv: float) -> str:
    if rv <= 0:
        return "n/a"
    if iv > 1.2 * rv:
        return "rich"
    if iv < 0.8 * rv:
        return "cheap"
    return "fair"


def _mid(row: dict) -> float:
    bid = row.get("bid") or 0.0
    ask = row.get("ask") or 0.0
    last = row.get("last") or 0.0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return last if last > 0 else 0.0


def select_contract(chain, spot, direction, target_dte=35, hold_days=10,
                    asof=None, rv_fallback=0.0):
    """Pick the in-band expiry nearest `target_dte` (and > hold_days) and the
    at-the-money strike with a usable premium. Returns None if nothing usable."""
    kind = "call" if direction != "bear" else "put"
    cands = []
    for exp in chain.get("expiries", []):
        y, m, d = (int(x) for x in exp["expiry"].split("-"))
        dte = (date(y, m, d) - asof).days
        if dte <= hold_days:
            continue
        cands.append((dte, exp))
    if not cands:
        return None

    def rank(c):
        dte = c[0]
        in_band = 0 if 25 <= dte <= 60 else 1
        return (in_band, abs(dte - target_dte))

    dte, exp = min(cands, key=rank)
    rows = exp["calls"] if kind == "call" else exp["puts"]
    usable = [(abs(r["strike"] - spot), r) for r in rows if _mid(r) > 0]
    if not usable:
        return None
    _, row = min(usable, key=lambda t: t[0])
    iv = row.get("iv") or 0.0
    if iv <= 0:
        iv = rv_fallback
    return {"kind": kind, "strike": float(row["strike"]), "expiry": exp["expiry"],
            "dte": dte, "premium": _mid(row), "iv": iv}


def size(entry, stop, target, contract, risk_budget=500.0, hold_days=10, r=0.043):
    """Size both vehicles to the same dollars: shares via the stop, the option
    via its premium (defined risk). Option value at target is its EXIT value
    (remaining time to expiry), never intrinsic."""
    stop_dist = abs(entry - stop)
    shares = int(risk_budget // stop_dist) if stop_dist > 0 else 0
    equity_stop_loss = shares * stop_dist
    equity_target_reward = shares * abs(target - entry)

    t_remaining = max(contract["dte"] - hold_days, 0) / 365.0
    K, iv, kind, prem = (contract["strike"], contract["iv"],
                         contract["kind"], contract["premium"])
    v_target = black_scholes(target, K, t_remaining, r, iv, kind)["price"]
    contracts = max(1, int(risk_budget // (prem * 100.0)))
    option_max_loss = contracts * prem * 100.0
    option_target_reward = contracts * (v_target - prem) * 100.0
    return {"shares": shares, "equity_stop_loss": equity_stop_loss,
            "equity_target_reward": equity_target_reward, "contracts": contracts,
            "option_max_loss": option_max_loss,
            "option_target_reward": option_target_reward,
            "v_target": v_target, "t_remaining": t_remaining}


def scenario_ev(entry, stop, target, contract, sizing, p, p_neither=0.20,
                hold_days=10, r=0.043):
    """3-scenario EV after the hold horizon: target (p), stop (p_stop), sideways
    (p_neither). The option is repriced in each, so its loss is capped at premium.
    Engine only — never displayed on the decision surface."""
    p_stop = max(0.0, 1.0 - p - p_neither)
    K, iv, kind, prem = (contract["strike"], contract["iv"],
                         contract["kind"], contract["premium"])
    t_rem = sizing["t_remaining"]
    v_target = sizing["v_target"]
    v_stop = black_scholes(stop, K, t_rem, r, iv, kind)["price"]
    v_unch = black_scholes(entry, K, t_rem, r, iv, kind)["price"]
    equity_ev = p * sizing["equity_target_reward"] - p_stop * sizing["equity_stop_loss"]
    c = sizing["contracts"]
    option_ev = c * 100.0 * (p * (v_target - prem)
                             + p_stop * (v_stop - prem)
                             + p_neither * (v_unch - prem))
    return {"equity_ev": equity_ev, "option_ev": option_ev, "p": p,
            "p_stop": p_stop, "p_neither": p_neither,
            "v_stop": v_stop, "v_unchanged": v_unch}


def flip_point(entry, stop, target, contract, sizing, p_neither=0.20,
               hold_days=10, r=0.043):
    """Confidence p at which option_ev == equity_ev. EV is linear in p, so
    interpolate between p=0 and p=1. Returns None if the two never cross."""
    def diff(p):
        ev = scenario_ev(entry, stop, target, contract, sizing, p, p_neither,
                         hold_days, r)
        return ev["option_ev"] - ev["equity_ev"]
    d0, d1 = diff(0.0), diff(1.0)
    if d0 == d1:
        return None
    return max(0.0, min(1.0, -d0 / (d1 - d0)))


def decide(signal, chain, *, p=None, risk_budget=500.0, target_dte=35,
           hold_days=10, r=0.043, p_neither=0.20, asof=None):
    """Full equity-vs-option decision for one signal. Pure given `chain`.
    `winner` is the higher-EV vehicle; EV/flip are engine-only fields."""
    asof = asof or date.today()
    spot = signal["entry"]
    stop = signal["stop"]
    target = signal["target"]
    rv = signal.get("realized_vol", 0.0)
    if p is None:
        p = conviction_to_p(signal.get("score", 50))
    move_pct = (target - spot) / spot * 100.0 if spot else 0.0
    exit_date = asof + timedelta(days=hold_days)

    base = {"symbol": signal["symbol"], "spot": spot, "target": target,
            "stop": stop, "move_pct": move_pct, "exit_date": exit_date, "p": p}

    contract = select_contract(chain, spot, signal["direction"], target_dte,
                               hold_days, asof, rv_fallback=rv)
    s = size(spot, stop, target, contract, risk_budget, hold_days, r) if contract \
        else _equity_only_size(spot, stop, target, risk_budget)

    if contract is None:
        return {**base, "options_available": False, "winner": "equity",
                "shares": s["shares"], "equity_stop_loss": s["equity_stop_loss"],
                "equity_target_reward": s["equity_target_reward"],
                "capital": s["shares"] * spot}

    ev = scenario_ev(spot, stop, target, contract, s, p, p_neither, hold_days, r)
    winner = "option" if ev["option_ev"] > ev["equity_ev"] else "equity"
    etr, otr = s["equity_target_reward"], s["option_target_reward"]
    if winner == "option" and etr > 0:
        payout_mult = otr / etr
    elif winner == "equity" and otr != 0:
        payout_mult = etr / otr
    else:
        payout_mult = None
    return {**base, "options_available": True, "winner": winner,
            "shares": s["shares"], "equity_stop_loss": s["equity_stop_loss"],
            "equity_target_reward": etr, "capital": s["shares"] * spot,
            "contract": contract, "contracts": s["contracts"],
            "option_max_loss": s["option_max_loss"], "option_target_reward": otr,
            "v_target": s["v_target"], "v_stop": ev["v_stop"],
            "v_unchanged": ev["v_unchanged"], "payout_mult": payout_mult,
            "iv_label": iv_context(contract["iv"], rv),
            "equity_ev": ev["equity_ev"], "option_ev": ev["option_ev"],
            "p_stop": ev["p_stop"], "p_neither": ev["p_neither"],
            "flip": flip_point(spot, stop, target, contract, s, p_neither, hold_days, r)}


def _equity_only_size(entry, stop, target, risk_budget):
    dist = abs(entry - stop)
    shares = int(risk_budget // dist) if dist > 0 else 0
    return {"shares": shares, "equity_stop_loss": shares * dist,
            "equity_target_reward": shares * abs(target - entry)}


def _rows_from_df(df):
    """Normalize a yfinance calls/puts DataFrame (or list of dicts) to our rows."""
    records = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
    out = []
    for rec in records:
        out.append({"strike": float(rec.get("strike")),
                    "bid": float(rec.get("bid") or 0.0),
                    "ask": float(rec.get("ask") or 0.0),
                    "last": float(rec.get("lastPrice") or 0.0),
                    "iv": float(rec.get("impliedVolatility") or 0.0)})
    return out


def fetch_chain(symbol, fetch=None, max_expiries=8):
    """Live option chain via yfinance, normalized. Best-effort -> None on failure.

    `fetch(symbol) -> (expiries_list, {expiry: chain_obj})` is injectable, where
    chain_obj has `.calls` and `.puts` (DataFrames or lists of dicts).
    """
    try:
        if fetch is not None:
            expiries, chains = fetch(symbol)
        else:
            import yfinance as yf
            tk = yf.Ticker(symbol)
            expiries = list(tk.options or [])[:max_expiries]
            chains = {e: tk.option_chain(e) for e in expiries}
        out = []
        for e in expiries[:max_expiries]:
            ch = chains[e]
            out.append({"expiry": e, "calls": _rows_from_df(ch.calls),
                        "puts": _rows_from_df(ch.puts)})
        return {"expiries": out} if out else None
    except Exception as exc:  # network/shape hiccup — never break the bot
        print(f"  [warn] no option chain for {symbol}: {exc}")
        return None
