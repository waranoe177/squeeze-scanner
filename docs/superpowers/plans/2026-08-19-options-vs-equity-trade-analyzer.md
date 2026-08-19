# Options-vs-Equity Trade Analyzer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An on-demand Telegram `trade SYM` command that returns a single, executable equity-vs-options decision with reproducible support, priced by an in-house Black–Scholes engine.

**Architecture:** A pure, offline-testable engine (`scanner/options.py`) prices the chosen contract at its **exit value** (BS with remaining time) under target/stop/sideways scenarios, sizes both vehicles to the same dollars (shares via the stop, option via the premium), and picks the higher-EV vehicle. A formatter (`scanner/optfmt.py`) renders the decision surface. The existing single Telegram poller (`scanner/bot.py`) gains a `trade` command alongside the chart and go/pass handlers. **Engine is built and proven against the acceptance test before the formatter exists.**

**Tech Stack:** Python 3.12, pandas/numpy (already pinned), yfinance (option chains), `math.erf` for the normal CDF (no scipy), pytest. Telegram Bot API via the existing `notify` helpers.

**Spec:** `docs/superpowers/specs/2026-08-19-options-vs-equity-trade-analyzer-design.md`

## Global Constraints

- pandas pinned `2.2.3`, numpy `>=1.26,<2.3` (Windows Smart App Control) — do not add scipy or any new runtime dependency; the normal CDF uses `math.erf`.
- All engine functions are **pure and deterministic given an injected chain** — no network inside them; live fetch is isolated in `fetch_chain`, smoke-tested only.
- **No hand-authored display numbers ever ship** — every figure the user sees is computed by the engine.
- Black–Scholes uses **remaining time to expiry** (exit value), never intrinsic-at-target.
- Option risk basis = **premium** (defined-risk); shares risk basis = **loss at the stop**. The kill line is **equity-only**.
- Decision surface shows **no EV, no R-multiples, no flip point**; those live only in the `full` tier.
- TDD throughout: failing test first, watch it fail, minimal code, watch it pass, commit. Run the full suite (`.venv/Scripts/python.exe -m pytest -q`) before each commit.
- Run commands from the repo root `c:\Users\waran\Documents\Claude\Sqzdots`.

---

### Task 1: Black–Scholes pricing (`black_scholes`)

**Files:**
- Create: `scanner/options.py`
- Test: `tests/test_options.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `black_scholes(S, K, T, r, sigma, kind="call") -> {"price": float, "delta": float, "theta": float}` — `T` in years, `r` annual, `sigma` annual vol, `theta` per **day**. Also module-private `_norm_cdf(x)`, `_norm_pdf(x)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_options.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scanner.options'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scanner/options.py
"""Options-vs-equity trade analyzer: an in-house Black-Scholes engine that
prices the chosen contract at its EXIT value (remaining time to expiry), sizes
equity and options to the same dollars, and picks the higher-EV vehicle.

Pure and deterministic given an injected chain; the only network is fetch_chain.
No scipy — the normal CDF is math.erf.
"""

import math


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scanner/options.py tests/test_options.py
git commit -m "feat(options): Black-Scholes exit-value pricing engine"
```

---

### Task 2: Confidence default + realized vol + IV context

**Files:**
- Modify: `scanner/options.py`
- Test: `tests/test_options.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `conviction_to_p(score) -> float` clamped to `[0.35, 0.70]`.
  - `realized_vol(close, window=20) -> float` annualized (pandas Series in).
  - `iv_context(iv, rv) -> str` in `{"rich","cheap","fair","n/a"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_options.py  (append)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -k "conviction or realized or iv_context" -q`
Expected: FAIL — `AttributeError: module 'scanner.options' has no attribute 'conviction_to_p'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scanner/options.py  (append; add `import numpy as np` at top)
def conviction_to_p(score) -> float:
    """Rough, clearly-labeled default probability from the conviction score.
    NOT calibrated — a starting point the trader overrides. Clamped [0.35,0.70]."""
    p = 0.45 + (float(score) - 60.0) * 0.005
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/options.py tests/test_options.py
git commit -m "feat(options): conviction->p default, realized vol, IV context"
```

---

### Task 3: Contract selection (`select_contract`)

**Files:**
- Modify: `scanner/options.py`
- Test: `tests/test_options.py`

**Interfaces:**
- Consumes: normalized chain dict `{"expiries": [{"expiry": "YYYY-MM-DD", "calls": [row], "puts": [row]}]}`, each `row = {"strike","bid","ask","last","iv"}`.
- Produces:
  - `select_contract(chain, spot, direction, target_dte=35, hold_days=10, asof=None, rv_fallback=0.0) -> dict | None` returning `{"kind","strike","expiry","dte","premium","iv"}`. `asof` is a `datetime.date`.
  - module-private `_mid(row) -> float`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_options.py  (append)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -k select_contract -q`
Expected: FAIL — `AttributeError: ... 'select_contract'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scanner/options.py  (append; add `from datetime import date, timedelta` at top)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -k select_contract -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/options.py tests/test_options.py
git commit -m "feat(options): in-band expiry + ATM strike selection"
```

---

### Task 4: Risk-matched sizing (`size`)

**Files:**
- Modify: `scanner/options.py`
- Test: `tests/test_options.py`

**Interfaces:**
- Consumes: `black_scholes`, a contract dict from Task 3.
- Produces: `size(entry, stop, target, contract, risk_budget=500.0, hold_days=10, r=0.043) -> dict` with keys `shares, equity_stop_loss, equity_target_reward, contracts, option_max_loss, option_target_reward, v_target, t_remaining`.

- [ ] **Step 1: Write the failing test (this is the sizing half of the acceptance test)**

```python
# tests/test_options.py  (append)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -k size_matches -q`
Expected: FAIL — `AttributeError: ... 'size'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scanner/options.py  (append)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -k size_matches -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/options.py tests/test_options.py
git commit -m "feat(options): risk-matched sizing (shares via stop, option via premium)"
```

---

### Task 5: Scenario EV + flip point

**Files:**
- Modify: `scanner/options.py`
- Test: `tests/test_options.py`

**Interfaces:**
- Consumes: `black_scholes`, a contract dict, a sizing dict from Task 4.
- Produces:
  - `scenario_ev(entry, stop, target, contract, sizing, p, p_neither=0.20, hold_days=10, r=0.043) -> dict` with `equity_ev, option_ev, p, p_stop, p_neither, v_stop, v_unchanged`.
  - `flip_point(entry, stop, target, contract, sizing, p_neither=0.20, hold_days=10, r=0.043) -> float | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_options.py  (append)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -k "scenario_ev or flip_point" -q`
Expected: FAIL — `AttributeError: ... 'scenario_ev'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scanner/options.py  (append)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -k "scenario_ev or flip_point" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/options.py tests/test_options.py
git commit -m "feat(options): 3-scenario EV engine + flip point"
```

---

### Task 6: `decide` — the full plan + acceptance test (gate)

**Files:**
- Modify: `scanner/options.py`
- Test: `tests/test_options.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `decide(signal, chain, *, p=None, risk_budget=500.0, target_dte=35, hold_days=10, r=0.043, p_neither=0.20, asof=None) -> dict`. `signal = {"symbol","direction","entry","target","stop","score","realized_vol"}`. Returns a plan dict:
  `{symbol, options_available, winner, spot, target, stop, move_pct, exit_date (date), p, shares, equity_stop_loss, equity_target_reward, capital, contract, contracts, option_max_loss, option_target_reward, v_target, v_stop, v_unchanged, payout_mult, iv_label, equity_ev, option_ev, p_stop, p_neither, flip}`. When no contract is available: `{symbol, options_available: False, ...equity fields..., winner: "equity"}`.

- [ ] **Step 1: Write the failing test (THE ACCEPTANCE TEST — the intrinsic-vs-exit-value guard)**

```python
# tests/test_options.py  (append)
def _accept_chain():
    def row(k, bid, ask, last, iv):
        return {"strike": k, "bid": bid, "ask": ask, "last": last, "iv": iv}
    return {"expiries": [
        {"expiry": "2026-09-23",  # 35 DTE from 2026-08-19
         "calls": [row(125, 6.9, 7.1, 7.0, 0.42), row(130, 4.1, 4.3, 4.2, 0.42)],
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -k decide -q`
Expected: FAIL — `AttributeError: ... 'decide'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scanner/options.py  (append)
def decide(signal, chain, *, p=None, risk_budget=500.0, target_dte=35,
           hold_days=10, r=0.043, p_neither=0.20, asof=None):
    """Full equity-vs-option decision for one signal. Pure given `chain`.
    `winner` is the higher-EV vehicle; EV/flip are engine-only fields."""
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
    s = size(spot, stop, target, contract or _EMPTY_CONTRACT, risk_budget,
             hold_days, r) if contract else _equity_only_size(spot, stop, target, risk_budget)

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


_EMPTY_CONTRACT = {"kind": "call", "strike": 0.0, "expiry": "", "dte": 0,
                   "premium": 0.0, "iv": 0.0}


def _equity_only_size(entry, stop, target, risk_budget):
    dist = abs(entry - stop)
    shares = int(risk_budget // dist) if dist > 0 else 0
    return {"shares": shares, "equity_stop_loss": shares * dist,
            "equity_target_reward": shares * abs(target - entry)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -q`
Expected: PASS (all options tests, including the acceptance test).

- [ ] **Step 5: Commit**

```bash
git add scanner/options.py tests/test_options.py
git commit -m "feat(options): decide() + acceptance test (shares win, exit-value priced)"
```

---

### Task 7: Live chain fetch (`fetch_chain`) — smoke only

**Files:**
- Modify: `scanner/options.py`
- Test: `tests/test_options.py`

**Interfaces:**
- Consumes: yfinance (network) unless `fetch` injected.
- Produces: `fetch_chain(symbol, fetch=None, max_expiries=8) -> dict | None` returning the normalized chain `{"expiries": [...]}` consumed by `select_contract`. Best-effort: returns `None` on any failure.

- [ ] **Step 1: Write the failing test (injected fetch — no network)**

```python
# tests/test_options.py  (append)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -k fetch_chain -q`
Expected: FAIL — `AttributeError: ... 'fetch_chain'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scanner/options.py  (append)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -k fetch_chain -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/options.py tests/test_options.py
git commit -m "feat(options): best-effort yfinance chain fetch + normalization"
```

---

### Task 8: Decision-surface formatter (`format_trade`)

**Files:**
- Create: `scanner/optfmt.py`
- Test: `tests/test_optfmt.py`

**Interfaces:**
- Consumes: a plan dict from `options.decide`.
- Produces: `format_trade(plan) -> str` (Telegram HTML). Enforces the spec's decision-surface rules.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_optfmt.py
from datetime import date

from scanner import optfmt


def _shares_win_plan():
    return {"symbol": "NVDA", "options_available": True, "winner": "equity",
            "spot": 123.45, "target": 130.0, "stop": 118.0, "move_pct": 5.3,
            "exit_date": date(2026, 8, 29), "p": 0.65,
            "shares": 154, "equity_stop_loss": 839.3, "equity_target_reward": 1008.7,
            "capital": 19011.3,
            "contract": {"kind": "call", "strike": 130.0, "expiry": "2026-09-23",
                         "dte": 35, "premium": 4.20, "iv": 0.42},
            "contracts": 2, "option_max_loss": 840.0, "option_target_reward": 334.0,
            "v_target": 5.87, "v_stop": 1.2, "v_unchanged": 3.1,
            "payout_mult": 3.02, "iv_label": "rich",
            "equity_ev": 530.0, "option_ev": 40.0, "p_stop": 0.15,
            "p_neither": 0.20, "flip": 0.72}


def test_format_trade_shares_win_surface_rules():
    msg = optfmt.format_trade(_shares_win_plan())
    assert "BUY SHARES" in msg
    assert "154" in msg and "$123.45" in msg.replace(",", "")
    assert "Kill below 118" in msg or "Kill below $118" in msg
    assert "08/29" in msg                       # exit date, not a duration
    assert "~65%" in msg                        # confidence in body
    # confidence NOT in the header line
    assert "65%" not in msg.splitlines()[0]
    # reproducibility: no EV, no R-multiples, no FLIPS on the surface
    assert "EV" not in msg and "R:R" not in msg and "FLIP" not in msg.upper()
    # option shown as the one-line SKIP, with its exit value
    assert "SKIP" in msg and "5.87" in msg


def test_format_trade_option_win_shows_exit_value_and_sideways():
    plan = _shares_win_plan()
    plan.update(winner="option", payout_mult=1.9)
    msg = optfmt.format_trade(plan)
    assert "BUY CALLS" in msg
    assert "130C" in msg and "5.87" in msg      # exit value on the surface
    assert "Flat by 08/29" in msg               # mandatory sideways branch
    assert "Max loss $840" in msg.replace(",", "")


def test_format_trade_equity_only_when_no_options():
    plan = {"symbol": "GLD", "options_available": False, "winner": "equity",
            "spot": 380.0, "target": 400.0, "stop": 370.0, "move_pct": 5.3,
            "exit_date": date(2026, 8, 29), "p": 0.6, "shares": 25,
            "equity_stop_loss": 250.0, "equity_target_reward": 500.0,
            "capital": 9500.0}
    msg = optfmt.format_trade(plan)
    assert "BUY SHARES" in msg and "no options" in msg.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optfmt.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scanner.optfmt'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scanner/optfmt.py
"""Render an options.decide() plan into the Telegram decision surface.

Decision-first: one executable order, the loser as a one-line footnote. The
surface shows only reproducible facts — no EV, no R-multiples, no flip point
(those live in format_trade_full). Confidence appears as a labeled body line,
never in the header.
"""

from scanner import notify

_ACTION = {("equity", "bull"): "BUY SHARES", ("equity", "bear"): "SHORT SHARES",
           ("option", "bull"): "BUY CALLS", ("option", "bear"): "BUY PUTS"}


def _hdr(sym, action, exit_date):
    return f"<b>{notify._esc(sym)} · {action} · exit by {exit_date:%a %m/%d}</b>"


def _pct(p):
    return f"~{round(p * 100)}%"


def _direction(plan):
    # infer from stop vs target (bull when target above spot)
    return "bull" if plan["target"] >= plan["spot"] else "bear"


def format_trade(plan) -> str:
    d = _direction(plan)
    sym = plan["symbol"]
    exit_date = plan["exit_date"]
    conf = _pct(plan["p"])
    move = f"{plan['move_pct']:+.1f}%"

    if not plan.get("options_available"):
        action = _ACTION[("equity", d)]
        return "\n".join([
            _hdr(sym, action, exit_date),
            f"  assumes {conf} you're right on {move}",
            "",
            f"  BUY {plan['shares']} sh @ ${plan['spot']:.2f}",
            f"  Kill below {plan['stop']:.0f} (−${plan['equity_stop_loss']:.0f}) · "
            f"target {plan['target']:.0f} (+${plan['equity_target_reward']:.0f})",
            "",
            f"  COST  ${plan['capital']:,.0f} capital; full downside below {plan['stop']:.0f}",
            f"  NOTE  no options listed for {notify._esc(sym)} — shares only",
        ])

    c = plan["contract"]
    exp = _fmt_expiry(c["expiry"])
    strike = f"{c['strike']:.0f}{'C' if c['kind'] == 'call' else 'P'}"
    winner = plan["winner"]

    if winner == "equity":
        action = _ACTION[("equity", d)]
        skip = (f"  SKIP  {plan['contracts']} × {strike} {exp} @ ${c['premium']:.2f} → "
                f"only +${plan['option_target_reward']:.0f} at {plan['target']:.0f} "
                f"({strike} ≈ ${plan['v_target']:.2f} vs ${c['premium']:.2f} paid); "
                f"−${plan['option_max_loss']:.0f} if it never moves")
        why = (f"  WHY   {plan['payout_mult']:.0f}× the payout of the call "
               f"at the same ${plan['option_max_loss']:.0f} risk"
               if plan.get("payout_mult") else "  WHY   shares keep the edge")
        return "\n".join([
            _hdr(sym, action, exit_date),
            f"  assumes {conf} you're right on {move}",
            "",
            f"  BUY {plan['shares']} sh @ ${plan['spot']:.2f}",
            f"  Kill below {plan['stop']:.0f} (−${plan['equity_stop_loss']:.0f}) · "
            f"target {plan['target']:.0f} (+${plan['equity_target_reward']:.0f})",
            "",
            why,
            f"  COST  ${plan['capital']:,.0f} capital; full downside below {plan['stop']:.0f}",
            skip,
        ])

    # option wins
    action = _ACTION[("option", d)]
    return "\n".join([
        _hdr(sym, action, exit_date),
        f"  assumes {conf} you're right on {move}",
        "",
        f"  BUY {plan['contracts']} × {sym} {strike} {exp} @ ${c['premium']:.2f} or better",
        f"  Max loss ${plan['option_max_loss']:,.0f} (all you can lose)",
        f"  Target {plan['target']:.0f} → ≈ +${plan['option_target_reward']:,.0f}  "
        f"({strike} ≈ ${plan['v_target']:.2f} vs ${c['premium']:.2f} paid)",
        f"  Flat by {exit_date:%m/%d} → close ≈ ${plan['v_unchanged'] * plan['contracts'] * 100:,.0f} back",
        "",
        f"  WHY   ~{plan['payout_mult']:.1f}× the payout of shares at the same "
        f"${plan['option_max_loss']:,.0f} risk" if plan.get("payout_mult")
        else "  WHY   convex payoff beats shares at your odds",
        f"  COST  needs {move} by {exit_date:%m/%d}; IV {c['iv'] * 100:.0f}% is {plan['iv_label']}",
        f"  SKIP  {plan['shares']} sh → ≈ +${plan['equity_target_reward']:,.0f}, no clock, no decay",
    ])


def _fmt_expiry(iso):
    if not iso:
        return ""
    y, m, d = iso.split("-")
    return f"{m}/{d}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optfmt.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/optfmt.py tests/test_optfmt.py
git commit -m "feat(optfmt): decision-surface formatter (reproducible facts only)"
```

---

### Task 9: Audit tier formatter (`format_trade_full`)

**Files:**
- Modify: `scanner/optfmt.py`
- Test: `tests/test_optfmt.py`

**Interfaces:**
- Consumes: a plan dict from `options.decide`.
- Produces: `format_trade_full(plan) -> str` — `format_trade` output plus the audit block (greeks, scenario probabilities, both EVs, flip point). The **only** place EV and the flip point appear.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_optfmt.py  (append)
def test_format_trade_full_appends_audit_block_with_ev_and_flip():
    msg = optfmt.format_trade_full(_shares_win_plan())
    assert "BUY SHARES" in msg                 # includes the decision surface
    assert "AUDIT" in msg
    assert "EV" in msg                         # EV appears ONLY here
    assert "IV 42%" in msg or "IV 42.0%" in msg
    assert "flip" in msg.lower() and "72" in msg   # flip point 0.72
    assert "p_stop" in msg or "stop 15%" in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optfmt.py -k full -q`
Expected: FAIL — `AttributeError: module 'scanner.optfmt' has no attribute 'format_trade_full'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scanner/optfmt.py  (append)
def format_trade_full(plan) -> str:
    surface = format_trade(plan)
    if not plan.get("options_available"):
        return surface
    c = plan["contract"]
    flip = plan.get("flip")
    flip_line = (f"flips to shares below {round(flip * 100)}% confidence"
                 if flip is not None else "no flip in range")
    audit = "\n".join([
        "",
        "— AUDIT —",
        f"contract {c['strike']:.0f}{'C' if c['kind'] == 'call' else 'P'} "
        f"{c['expiry']} · {c['dte']}DTE · IV {c['iv'] * 100:.0f}%",
        f"exit values: target ${plan['v_target']:.2f} · stop ${plan['v_stop']:.2f} "
        f"· flat ${plan['v_unchanged']:.2f}",
        f"scenarios: target {round(plan['p'] * 100)}% · "
        f"stop {round(plan['p_stop'] * 100)}% · flat {round(plan['p_neither'] * 100)}%",
        f"EV: shares ${plan['equity_ev']:,.0f} · option ${plan['option_ev']:,.0f}",
        flip_line,
    ])
    return surface + "\n" + audit
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optfmt.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/optfmt.py tests/test_optfmt.py
git commit -m "feat(optfmt): full audit tier (EV + flip point live only here)"
```

---

### Task 10: Parse the `trade` command (`parse_trade`)

**Files:**
- Modify: `scanner/bot.py`
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: a Telegram update dict.
- Produces: `parse_trade(update) -> dict | None` returning `{"symbol","p","risk","dte","full"}` (`p` a float 0–1 or None; `risk`/`dte` floats/ints or None; `full` bool). Returns None for non-`trade` messages (so bare tickers still route to the chart handler).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot.py  (append; reuse the existing _update helper)
def test_parse_trade_basic_and_overrides():
    assert bot.parse_trade(_update("trade nvda"))["symbol"] == "NVDA"
    t = bot.parse_trade(_update("trade nvda 65 risk=1000 dte=45 full"))
    assert t["symbol"] == "NVDA" and abs(t["p"] - 0.65) < 1e-9
    assert t["risk"] == 1000.0 and t["dte"] == 45 and t["full"] is True
    assert bot.parse_trade(_update("/trade tsla p=70"))["p"] == 0.70


def test_parse_trade_rejects_non_trade_and_bad_symbol():
    assert bot.parse_trade(_update("nvda")) is None          # bare ticker = chart
    assert bot.parse_trade(_update("trade")) is None          # no symbol
    assert bot.parse_trade(_update("go tsla")) is None
    assert bot.parse_trade({"update_id": 5}) is None          # no message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -k parse_trade -q`
Expected: FAIL — `AttributeError: module 'scanner.bot' has no attribute 'parse_trade'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scanner/bot.py  (append; `import re` already present)
_TRADE_SYM = re.compile(r"^[A-Za-z][A-Za-z0-9.\-=^]{0,11}$")


def _to_p(tok):
    try:
        v = float(tok)
    except ValueError:
        return None
    v = v / 100.0 if v > 1 else v
    return max(0.01, min(0.99, v))


def parse_trade(update: dict) -> dict | None:
    """Parse `trade SYM [CONF] [risk=N] [dte=N] [full]`. None if not a trade."""
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    if not text:
        return None
    parts = text.split()
    if parts[0].lower() not in ("trade", "/trade") or len(parts) < 2:
        return None
    if not _TRADE_SYM.match(parts[1]):
        return None
    opts = {"symbol": parts[1].upper(), "p": None, "risk": None,
            "dte": None, "full": False}
    for tok in parts[2:]:
        low = tok.lower()
        if low == "full":
            opts["full"] = True
        elif low.startswith("risk="):
            try:
                opts["risk"] = float(tok[5:])
            except ValueError:
                pass
        elif low.startswith("dte="):
            try:
                opts["dte"] = int(tok[4:])
            except ValueError:
                pass
        elif low.startswith("p="):
            opts["p"] = _to_p(tok[2:])
        elif re.fullmatch(r"\d{1,3}", tok):
            opts["p"] = _to_p(tok)
    return opts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -k parse_trade -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat(bot): parse the trade command with overrides"
```

---

### Task 11: `handle_trade` + poll dispatch wiring

**Files:**
- Modify: `scanner/bot.py`
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `parse_trade`, `options.decide`, `options.fetch_chain`, `options.realized_vol`, `optfmt.format_trade`/`format_trade_full`, `signals.latest_signal`, `score.conviction`, `data.fetch_daily`, `notify.send_message`.
- Produces:
  - `handle_trade(opts, chat_id, token, *, fetcher=None, chain_fetcher=None, send_message=None, asof=None) -> bool`.
  - Wire into `poll_once`: after the chart-request branch, a `trade` message routes to `handle_trade`. A bare ticker still routes to the chart handler; `go/pass` still routes to decisions.

- [ ] **Step 1: Write the failing test (engine injected — no network)**

```python
# tests/test_bot.py  (append)
import numpy as np
import pandas as pd
from datetime import date


def _ohlc_up(n=320):
    idx = pd.bdate_range("2023-01-02", periods=n)
    close = pd.Series(50 + np.arange(n) * 0.12, index=idx)
    return pd.DataFrame({"open": close, "high": close + 0.6, "low": close - 0.6,
                         "close": close, "volume": 1_000_000}, index=idx)


def test_handle_trade_sends_decision_message():
    sent = []

    def fake_chain(symbol):
        return {"expiries": [{"expiry": "2026-09-23",
                "calls": [{"strike": 88.0, "bid": 3.0, "ask": 3.2, "last": 3.1,
                           "iv": 0.42}], "puts": []}]}

    ok = bot.handle_trade(
        {"symbol": "TEST", "p": 0.6, "risk": 840.0, "dte": 35, "full": False},
        chat_id="1", token="T",
        fetcher=lambda syms: {"TEST": _ohlc_up()},
        chain_fetcher=fake_chain,
        send_message=lambda tok, cid, text: sent.append(text),
        asof=date(2026, 8, 19),
    )
    assert ok is True
    assert "TEST" in sent[0] and ("BUY" in sent[0] or "SHARES" in sent[0])


def test_handle_trade_no_data_replies_text():
    sent = []
    ok = bot.handle_trade(
        {"symbol": "ZZZZ", "p": None, "risk": None, "dte": None, "full": False},
        chat_id="1", token="T",
        fetcher=lambda syms: {},
        chain_fetcher=lambda s: None,
        send_message=lambda tok, cid, text: sent.append(text),
        asof=date(2026, 8, 19),
    )
    assert ok is False and "ZZZZ" in sent[0]


def test_poll_once_routes_trade(tmp_path, monkeypatch):
    lpath, spath = tmp_path / "l.jsonl", tmp_path / "s.json"
    ledger.save(lpath, [])
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda token, offset, timeout=0: ([_update("trade nvda 60", uid=3)], 4))
    routed = []
    bot.poll_once(token="T", chat_id="1", ledger_path=lpath, state_path=spath,
                  command_handler=lambda sym: routed.append(("chart", sym)) or True,
                  trade_handler=lambda opts: routed.append(("trade", opts["symbol"])) or True)
    assert ("trade", "NVDA") in routed
    assert not any(r[0] == "chart" for r in routed)   # trade did NOT fall through to chart
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -k "handle_trade or routes_trade" -q`
Expected: FAIL — `AttributeError: module 'scanner.bot' has no attribute 'handle_trade'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scanner/bot.py  (append; add `from datetime import date` and
# `from scanner import optfmt, options, score, signals` imports at top)
def handle_trade(opts, chat_id, token, *, fetcher=None, chain_fetcher=None,
                 send_message=None, asof=None) -> bool:
    """Compute + send the equity-vs-options decision for one ticker.

    Collaborators are injectable so this is unit-testable without network.
    Returns True on a decision send, False when there's no price data.
    """
    fetcher = fetcher or (lambda syms: data.fetch_daily(syms, period="2y"))
    chain_fetcher = chain_fetcher or options.fetch_chain
    send_message = send_message or notify.send_message
    asof = asof or date.today()

    symbol = opts["symbol"]
    frames = fetcher([symbol])
    df = frames.get(symbol)
    if df is None or getattr(df, "empty", True):
        send_message(token, chat_id, f"No data for {symbol} — check the ticker?")
        return False

    sig = signals.latest_signal(df, symbol=symbol)
    conv = score.conviction(df, symbol=symbol)
    direction = sig["direction"]
    if direction == "none":
        direction = "bull" if (sig["ppo"] >= 0 and sig.get("moxie_w", 0) is not None) else "bear"
    signal = {"symbol": symbol, "direction": direction, "entry": sig["close"],
              "target": sig["target_up"] if direction != "bear" else sig["target_dn"],
              "stop": sig["stop"], "score": conv["score"],
              "realized_vol": options.realized_vol(df["close"])}

    chain = chain_fetcher(symbol)
    plan = options.decide(
        signal, chain or {"expiries": []},
        p=opts.get("p"), risk_budget=opts.get("risk") or 500.0,
        target_dte=opts.get("dte") or 35, asof=asof)
    msg = (optfmt.format_trade_full(plan) if opts.get("full")
           else optfmt.format_trade(plan))
    send_message(token, chat_id, msg)
    return True
```

Then modify `poll_once` — add a `trade_handler` parameter and route trades before chart requests:

```python
# scanner/bot.py  — in poll_once signature, add: trade_handler=None
    trade_handler = trade_handler or (
        lambda opts: handle_trade(opts, chat_id, token))
```

And in the per-update dispatch loop (inside `poll_once`, in the chart-requests
section), route trades first:

```python
    for u in owned:
        if decisions.parse_decision(u):
            continue
        t = parse_trade(u)
        if t:
            try:
                if trade_handler(t):
                    charts += 1
            except Exception as exc:
                print(f"  [bot] trade failed for {t['symbol']}: {exc}")
                try:
                    notify.send_message(token, chat_id, f"Couldn't analyze {t['symbol']}: {exc}")
                except Exception:
                    pass
            continue
        sym = parse_command(u)
        if not sym:
            continue
        try:
            if command_handler(sym):
                charts += 1
        except Exception as exc:
            print(f"  [bot] chart failed for {sym}: {exc}")
            try:
                notify.send_message(token, chat_id, f"Couldn't chart {sym}: {exc}")
            except Exception:
                pass
```

(Replace the existing chart-only loop with the combined loop above; keep the
`charts` counter and the trailing `save_state`/return unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot.py -q`
Expected: PASS (existing bot tests + the three new ones).

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat(bot): handle_trade + route the trade command in the poller"
```

---

### Task 12: Docs + full-suite green + push

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a README section**

Add under the on-demand section:

```markdown
## Options vs equity on demand

Text `trade NVDA` and the bot replies with a single, executable decision —
buy shares or a call/put — sized to the same dollar risk, with the option
priced at its **exit value** (Black–Scholes, remaining time), not intrinsic.
Overrides: `trade NVDA 65` (your confidence %), `risk=1000`, `dte=45`, `full`
(adds the EV/greeks audit tier). Options are defined-risk (premium); the kill
line is shares-only. It will honestly say "buy shares" when a modest move
doesn't clear the option's premium — that's the point.
```

- [ ] **Step 2: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all prior tests + the new options/optfmt/bot tests).

- [ ] **Step 3: Commit and push**

```bash
git add README.md
git commit -m "docs: options-vs-equity trade command"
git pull --rebase && git push
```

---

## Self-Review

**1. Spec coverage:**
- Surface / `trade` command syntax → Tasks 10, 11 (+ README Task 12). ✓
- Single-leg call/put selection → Task 3. ✓
- Trader-stated confidence, conviction suggests default → Tasks 2, 6, 10. ✓
- EV engine (3-scenario, hidden) → Task 5; shown only in `full` → Task 9. ✓
- Same-dollar-risk footing (shares via stop, option via premium) → Task 4. ✓
- yfinance chain data, best-effort → Task 7. ✓
- Black–Scholes exit-value pricing → Task 1; acceptance test → Task 6. ✓
- Decision surface rules (no EV/R/flip, confidence out of header, sideways branch, exit value, equity-only kill) → Task 8. ✓
- `full` audit tier (EV + flip only here) → Task 9. ✓
- Error handling (no chain → equity-only; no data → text reply) → Tasks 6, 8, 11. ✓
- Engine-before-format order → Tasks 1–7 precede 8–11. ✓

**2. Placeholder scan:** No TBD/TODO; every code step has real code; the poll_once edit shows the full replacement loop. ✓

**3. Type consistency:** `decide()` returns the exact keys `format_trade`/`format_trade_full` read (`options_available`, `winner`, `contract`, `contracts`, `option_max_loss`, `option_target_reward`, `v_target`, `v_unchanged`, `payout_mult`, `iv_label`, `equity_ev`, `option_ev`, `p_stop`, `p_neither`, `flip`, `exit_date`, `move_pct`, `capital`, `shares`, `equity_stop_loss`, `equity_target_reward`). `select_contract`/`size`/`scenario_ev` field names match their consumers. `parse_trade` returns the keys `handle_trade` reads (`symbol`, `p`, `risk`, `dte`, `full`). ✓
