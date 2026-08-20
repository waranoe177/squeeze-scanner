import math
from datetime import date

import numpy as np
import pandas as pd

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


def _chain():
    def row(k, bid, ask, last, iv):
        return {"strike": k, "bid": bid, "ask": ask, "last": last, "iv": iv}
    return {"expiries": [
        {"expiry": "2026-08-24",  # 5 DTE from 08-19 -> excluded (<= hold_days)
         "calls": [row(130, 1.0, 1.2, 1.1, 0.40)], "puts": []},
        {"expiry": "2026-09-23",  # 35 DTE -> chosen (nearest target, in band)
         "calls": [row(140, 1.5, 1.7, 1.6, 0.41), row(130, 4.1, 4.3, 4.2, 0.42)],
         "puts": [row(120, 2.0, 2.2, 2.1, 0.45)]},
        {"expiry": "2026-11-21",  # 94 DTE -> out of band
         "calls": [row(130, 7.0, 7.4, 7.2, 0.43)], "puts": []},
    ]}


def test_select_contract_picks_in_band_expiry_and_atm_strike():
    c = options.select_contract(_chain(), spot=123.45, direction="bull",
                                target_dte=35, hold_days=10, asof=date(2026, 8, 19))
    assert c["expiry"] == "2026-09-23"
    assert c["strike"] == 130.0            # nearest to 123.45 among {130,140}
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


def _accept_contract():
    return {"kind": "call", "strike": 130.0, "expiry": "2026-09-23",
            "dte": 35, "premium": 4.20, "iv": 0.42}


def test_size_matches_acceptance_numbers():
    s = options.size(123.45, 118.0, 130.0, _accept_contract(),
                     risk_budget=840.0, hold_days=10, r=0.043)
    assert s["shares"] == 154                       # floor(840/5.45)
    assert abs(s["equity_stop_loss"] - 839.3) < 1.0
    assert abs(s["equity_target_reward"] - 1009) < 2.0
    assert s["contracts"] == 2                       # floor(840/420)
    assert abs(s["option_max_loss"] - 840.0) < 1e-6  # 2*4.20*100
    assert 5.5 < s["v_target"] < 6.2                 # EXIT value ~5.87, not 0
    assert abs(s["option_target_reward"] - 334) < 8.0  # 2*(5.87-4.20)*100


def test_scenario_ev_shares_win_the_acceptance_case():
    c = _accept_contract()
    s = options.size(123.45, 118.0, 130.0, c, risk_budget=840.0)
    ev = options.scenario_ev(123.45, 118.0, 130.0, c, s, p=0.65)
    assert ev["equity_ev"] > ev["option_ev"]     # shares win, priced correctly
    assert abs(ev["p_stop"] - 0.15) < 1e-9        # 1 - 0.65 - 0.20
    assert ev["v_stop"] < c["premium"]            # loses value at the stop


def test_flip_point_is_between_zero_and_one():
    c = _accept_contract()
    s = options.size(123.45, 118.0, 130.0, c, risk_budget=840.0)
    fp = options.flip_point(123.45, 118.0, 130.0, c, s)
    assert fp is None or 0.0 <= fp <= 1.0


def _accept_chain():
    def row(k, bid, ask, last, iv):
        return {"strike": k, "bid": bid, "ask": ask, "last": last, "iv": iv}
    return {"expiries": [
        {"expiry": "2026-09-23",  # 35 DTE from 2026-08-19
         "calls": [row(130, 4.1, 4.3, 4.2, 0.42), row(135, 2.1, 2.3, 2.2, 0.42)],
         "puts": [row(120, 2.0, 2.2, 2.1, 0.45)]},
    ]}


def _accept_signal():
    return {"symbol": "NVDA", "direction": "bull", "entry": 123.45,
            "target": 130.0, "stop": 118.0, "score": 88, "realized_vol": 0.33}


def test_decide_acceptance_shares_win_with_exit_value_pricing():
    plan = options.decide(_accept_signal(), _accept_chain(), p=0.65,
                          risk_budget=840.0, asof=date(2026, 8, 19))
    assert plan["options_available"] is True
    assert plan["contract"]["strike"] == 130.0 and plan["contract"]["dte"] == 35
    assert 5.5 < plan["v_target"] < 6.2                 # EXIT value, not intrinsic 0
    assert abs(plan["option_target_reward"] - 334) < 8.0
    assert abs(plan["equity_target_reward"] - 1009) < 3.0
    assert plan["winner"] == "equity"                   # shares win, decisively
    assert plan["shares"] == 154 and plan["contracts"] == 2


def test_decide_defaults_p_from_conviction_and_reports_move_and_exit():
    plan = options.decide(_accept_signal(), _accept_chain(),
                          risk_budget=840.0, asof=date(2026, 8, 19))
    assert abs(plan["p"] - options.conviction_to_p(88)) < 1e-9
    assert abs(plan["move_pct"] - 5.3) < 0.2
    assert plan["exit_date"] == date(2026, 8, 29)       # asof + 10 days


def test_decide_equity_only_when_no_chain():
    plan = options.decide(_accept_signal(), {"expiries": []},
                          risk_budget=840.0, asof=date(2026, 8, 19))
    assert plan["options_available"] is False
    assert plan["winner"] == "equity" and plan["shares"] == 154


def test_fetch_chain_normalizes_injected_yfinance_shape():
    class FakeChain:
        def __init__(self, calls, puts):
            self.calls, self.puts = calls, puts

    def fake(symbol):
        # mimic yfinance: .options list + option_chain(exp) -> namedtuple-ish
        calls = [{"strike": 130.0, "bid": 4.1, "ask": 4.3, "lastPrice": 4.2,
                  "impliedVolatility": 0.42}]
        puts = [{"strike": 120.0, "bid": 2.0, "ask": 2.2, "lastPrice": 2.1,
                 "impliedVolatility": 0.45}]
        return ["2026-09-23"], {"2026-09-23": FakeChain(calls, puts)}

    ch = options.fetch_chain("NVDA", fetch=fake)
    assert ch["expiries"][0]["expiry"] == "2026-09-23"
    assert ch["expiries"][0]["calls"][0]["iv"] == 0.42
    assert ch["expiries"][0]["puts"][0]["strike"] == 120.0


def test_fetch_chain_none_on_failure():
    def boom(symbol):
        raise RuntimeError("no network")
    assert options.fetch_chain("ZZZZ", fetch=boom) is None
