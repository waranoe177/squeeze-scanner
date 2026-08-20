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
    usable = [r for r in rows if _mid(r) > 0]
    if not usable:
        return None
    row = max(usable, key=lambda r: r["strike"])
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
