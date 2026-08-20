import math
from scanner import options


def test_black_scholes_atm_call_exit_value():
    # ATM 130 call, 25 days left, 42% IV -> ~time premium, NOT intrinsic (0)
    px = options.black_scholes(130.0, 130.0, 25 / 365, 0.043, 0.42, "call")["price"]
    assert 5.5 < px < 6.2          # ~5.87


def test_black_scholes_put_call_parity():
    S, K, T, r, sig = 123.0, 130.0, 30 / 365, 0.043, 0.40
    c = options.black_scholes(S, K, T, r, sig, "call")["price"]
    p = options.black_scholes(S, K, T, r, sig, "put")["price"]
    assert abs((c - p) - (S - K * math.exp(-r * T))) < 1e-6


def test_black_scholes_delta_bounds_and_theta_sign():
    call = options.black_scholes(123.0, 130.0, 30 / 365, 0.043, 0.40, "call")
    put = options.black_scholes(123.0, 130.0, 30 / 365, 0.043, 0.40, "put")
    assert 0.0 <= call["delta"] <= 1.0
    assert -1.0 <= put["delta"] <= 0.0
    assert call["theta"] < 0.0     # long options bleed time


def test_black_scholes_monotonic_in_vol():
    lo = options.black_scholes(130.0, 130.0, 30 / 365, 0.043, 0.20, "call")["price"]
    hi = options.black_scholes(130.0, 130.0, 30 / 365, 0.043, 0.60, "call")["price"]
    assert hi > lo


import numpy as np
import pandas as pd


def test_conviction_to_p_monotonic_and_clamped():
    assert options.conviction_to_p(40) == 0.35          # clamped floor
    assert options.conviction_to_p(100) == 0.70         # clamped ceiling
    assert options.conviction_to_p(80) > options.conviction_to_p(60)


def test_realized_vol_flat_series_is_zero_and_positive_when_noisy():
    flat = pd.Series([100.0] * 30)
    assert options.realized_vol(flat) == 0.0
    rng = np.random.default_rng(0)
    noisy = pd.Series(100 + np.cumsum(rng.normal(0, 1, 60)))
    assert options.realized_vol(noisy) > 0.0


def test_iv_context_labels():
    assert options.iv_context(0.50, 0.30) == "rich"     # > 1.2x rv
    assert options.iv_context(0.20, 0.40) == "cheap"    # < 0.8x rv
    assert options.iv_context(0.42, 0.40) == "fair"
    assert options.iv_context(0.42, 0.0) == "n/a"
