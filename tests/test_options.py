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
