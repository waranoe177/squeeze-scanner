"""Options-vs-equity trade analyzer: an in-house Black-Scholes engine that
prices the chosen contract at its EXIT value (remaining time to expiry), sizes
equity and options to the same dollars, and picks the higher-EV vehicle.

Pure and deterministic given an injected chain; the only network is fetch_chain.
No scipy — the normal CDF is math.erf.
"""

import math

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
