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


from datetime import date


def _chain():
    def row(k, bid, ask, last, iv):
        return {"strike": k, "bid": bid, "ask": ask, "last": last, "iv": iv}
    return {"expiries": [
        {"expiry": "2026-08-24",  # 5 DTE from 08-19 -> excluded (<= hold_days)
         "calls": [row(130, 1.0, 1.2, 1.1, 0.40)], "puts": []},
        {"expiry": "2026-09-23",  # 35 DTE -> chosen (nearest target, in band)
         "calls": [row(120, 6.0, 6.4, 6.2, 0.41), row(130, 4.1, 4.3, 4.2, 0.42)],
         "puts": [row(120, 2.0, 2.2, 2.1, 0.45)]},
        {"expiry": "2026-11-21",  # 94 DTE -> out of band
         "calls": [row(130, 7.0, 7.4, 7.2, 0.43)], "puts": []},
    ]}


def test_select_contract_picks_in_band_expiry_and_atm_strike():
    c = options.select_contract(_chain(), spot=123.45, direction="bull",
                                target_dte=35, hold_days=10, asof=date(2026, 8, 19))
    assert c["expiry"] == "2026-09-23"
    assert c["strike"] == 130.0            # nearest to 123.45 among {120,130}
    assert c["kind"] == "call"
    assert abs(c["premium"] - 4.2) < 1e-9  # mid (4.1+4.3)/2
    assert c["dte"] == 35


def test_select_contract_iv_fallback_and_bear_uses_puts():
    ch = _chain()
    ch["expiries"][1]["puts"][0]["iv"] = 0.0     # force fallback
    c = options.select_contract(ch, spot=121.0, direction="bear",
                                hold_days=10, asof=date(2026, 8, 19), rv_fallback=0.33)
    assert c["kind"] == "put" and c["iv"] == 0.33


def test_select_contract_none_when_no_expiry_beyond_hold():
    near = {"expiries": [{"expiry": "2026-08-24", "calls": [
        {"strike": 130, "bid": 1, "ask": 1.2, "last": 1.1, "iv": 0.4}], "puts": []}]}
    assert options.select_contract(near, 130, "bull", hold_days=10,
                                   asof=date(2026, 8, 19)) is None
