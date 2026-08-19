# Options-vs-Equity Trade Analyzer — Design Spec

Date: 2026-08-19
Status: approved design, pre-implementation
Repo: Sqzdots (personal swing-trade squeeze scanner)

## Goal

When the user sees a signal in Telegram and decides to trade it, let them ask the
bot **"should I buy shares or calls/puts, and what are my risks and rewards?"**
and get back a **single, executable decision** — not a two-column report.

The output is a decision with supporting detail, not a comparison the reader has
to resolve. The loser is a one-line, past-tense footnote.

## Scope (v1)

- **Surface:** on-demand Telegram bot command (reuses the existing single poller).
- **Structure:** single-leg long **call** (BUY signals) or **put** (SELL signals).
  Spreads / multi-leg are explicitly out of scope (v2).
- **Confidence:** a **trader-stated probability** the target is hit. Conviction
  score only *suggests* a default; the displayed number is the user's own.
- **Expected value:** EV (3-scenario) is the **engine that picks the winner**. It
  is **not displayed** on the decision surface — it lives in a `full` detail tier.
  The decision's on-screen justification is reproducible facts only.
- **Footing:** both vehicles sized to the **same dollar risk** (loss at the price
  stop), default $500, overridable per trade.

## Command syntax

```
trade SYM [CONF] [risk=N] [dte=N] [full]
```

- `trade NVDA` — confidence defaults to the conviction→p suggestion.
- `trade NVDA 60` — a bare integer is the trader's confidence (60%).
- `trade NVDA 60 risk=1000` — risk budget $1000.
- `trade NVDA dte=45` — target days-to-expiration for contract selection.
- `trade NVDA full` — append the audit tier (raw greeks, 3-scenario EV table).

Parsing lives alongside the existing `parse_command` (bare ticker = chart). A
`trade …` message routes to the new handler; a bare ticker still returns a chart;
`go/pass` still routes to decisions. Single Telegram poller unchanged.

`trade SYM` works for **any** ticker, fired or not — like the chart command it
computes ATR levels + conviction from the daily frame, so there is always a
target/stop and a suggested confidence.

## Inputs

Resolved per request from the daily frame + live chain:

| Input | Source | Default |
|---|---|---|
| direction | bull→call, bear→put; if signal is `none`, bull when `ppo≥0 and ema8>ema21` else bear | — |
| entry (spot) | latest close (or live quote if available) | — |
| target / stop | signal payload (`prov_*` or `target_up/target_dn` + `stop`) | — |
| confidence `p` | trader-stated; else conviction→p suggestion | suggested |
| risk budget `R$` | `risk=` override | 500 |
| hold horizon `H` | days to exit (calendar) | 10 |
| target DTE | `dte=` override | 35, clamped [25,60] |
| risk-free `r` | constant | 0.043 |
| `p_neither` | sideways/time-out weight for EV engine | 0.20 |

## Data sources

- **Option chain:** `yfinance` — `Ticker(sym).options` (expiries) +
  `Ticker(sym).option_chain(expiry)` (calls/puts DataFrames: strike, bid, ask,
  lastPrice, impliedVolatility, volume, openInterest). Free, ~15-min delayed.
- **Underlying / realized vol:** the daily OHLC frame already fetched by `data`.
- **Earnings (nice-to-have):** `Ticker(sym).calendar` for an earnings-before-expiry
  IV-crush warning; graceful skip if unavailable.

## Engine

New module `scanner/options.py`, pure/deterministic given an injected chain so it
unit-tests offline.

### Black–Scholes (`black_scholes(S, K, T, r, sigma, kind) -> {price, delta, theta}`)
European BS with `math.erf` for the normal CDF (no scipy). `theta` per **day**
(annual/365). Used to reprice the chosen contract under each scenario. Simplifying
assumptions (stated to the user): European exercise, no dividends, **IV held
constant** across scenarios (no vol crush).

### Contract selection (`select_contract(chain, spot, direction, target_dte) -> Contract`)
- Expiry: listed expiry with DTE closest to `target_dte`, constrained to `[25,60]`
  when such exist and always `> H`; else the nearest listed expiry beyond `H`.
- Strike: nearest listed strike to spot (ATM) in v1.
- Premium: `mid = (bid+ask)/2` when both > 0, else `lastPrice`. Reject the strike
  if neither is usable; try the next-nearest strike; if none usable → equity-only.
- IV: `impliedVolatility` at that strike; if missing/≤0, fall back to realized vol
  from the daily frame and flag it.

### Risk-matched sizing (`size(entry, stop, target, contract, R$, H, r) -> Sizing`)
Risk basis = **R$ actually at risk on each vehicle** — a single, unambiguous
figure both are sized to. Shares put R$ at risk via the stop; a long option puts
R$ at risk via the premium (its defined max loss). This matches how options are
sized in practice ("I'll risk $500 on this call") and makes "same risk" literally
true and reproducible.

- **Equity:** `shares = floor(R$ / |entry-stop|)`; `stop_loss = shares·|entry-stop|`
  (≈ R$); `target_reward = shares·|target-entry|`.
- **Option:** `contracts = floor(R$ / (premium·100))` (min 1);
  `max_loss = contracts·premium·100` (≈ R$ — the capped worst case, the option's
  real gap-risk edge); `target_reward = contracts·(v_target - premium)·100`,
  `v_target = BS(S=target, T=(DTE-H)/365, …)`.

The `Kill below/above <stop>` level is a **discretionary exit** for the option — it
can only reduce the loss below the premium, never increase it — shown for trade
management, not for sizing.

Integer rounding means actual size ≠ budget exactly; **display the actuals** of the
integer position, never the theoretical budget.

### 3-scenario EV (`scenario_ev(...) -> {equity_ev, option_ev, ...}`) — engine only
After `H` days: target hit (`p`), stop hit (`p_stop = clamp(1 - p - p_neither, 0,
1)`), neither (`p_neither`). The option is repriced in every scenario, so its loss
is naturally capped at the premium.
- Equity EV `= p·target_reward - p_stop·stop_loss` (neither ≈ 0).
- Option EV, per contract ×100: `p·(v_target - premium) + p_stop·(v_stop -
  premium) + p_neither·(v_unchanged - premium)`, where `v_stop = BS(S=stop, …)`,
  `v_unchanged = BS(S=entry, …)`, all at `T=(DTE-H)/365`. Every downside term is
  bounded below by `-premium`.

### Decision + flip point (`decide(...) -> TradePlan`)
- Winner = higher EV vehicle.
- **Payout multiple** (reproducible WHY) `= option_target_reward /
  equity_target_reward` at equal stop-risk.
- **Flip point** `p*`: EV is linear in `p` (with `p_neither` fixed), so solve
  `option_ev(p*) = equity_ev(p*)` in closed form → "FLIPS to shares below p*%".
- **IV context:** compare option IV to realized vol of the underlying — `rich` if
  `IV > 1.2·RV`, `cheap` if `IV < 0.8·RV`, else neutral.

## Output

### Decision surface (default)
Layout illustration — every hand-computable figure ties to `risk=$500`
(entry $123.45, target $130.00, stop $118.00); `target ≈ $1,150` is the one
model-derived number (BS repricing) and is marked `≈`.
```
NVDA · BUY CALLS · your call 65% · exit by Fri 08/29

  BUY 2 × NVDA 128C exp 09/23 @ $2.50 or better
  Max loss $500 (premium) · target ≈ $1,150 if $130 hit
  Kill below $118

  WHY   ~1.9× the payout of shares at the same $500 risk
  COST  needs +5.3% ($123.45→$130) by 08/29; IV 42% is rich
  FLIPS to shares below 58% confidence
  SKIP  91 shares → ≈ +$596 at target, no clock, no decay
```
(Shares 91×$5.45 ≈ $500 at the stop, 91×$6.55 ≈ $596 at target; option 2×$2.50×100
= $500 premium; WHY = 1150 ÷ 596 ≈ 1.9×.)

Rules enforced:
- Header: `SYM · <ACTION> · your call NN% · exit by <day mm/dd>`.
- Order line is a literal, typeable order with an "or better" limit.
- `target ≈ $X` is the **only** model-derived number, singular and marked `≈`.
  No EV, no R-multiples on this surface.
- `Kill below/above <stop>` sits in the order block (read at entry).
- WHY = reproducible payout multiple. COST = move % + exit date + IV label.
  FLIPS = decision boundary in the trader's own units. SKIP = one-line footnote
  for the rejected vehicle.
- When the winner is **shares**, the shape inverts: BUY shares is the order, the
  option becomes the SKIP footnote, WHY explains why the clock/decay/IV lost.

### `full` audit tier (on `full`)
Appends: chosen expiry + DTE, Δ/θ/IV raw, realized vol, the three scenario
probabilities, each vehicle's payoff in every scenario, and both EV numbers — so
the pick can be audited. This is the only place EV appears.

## Error handling

- No options listed (`Ticker.options` empty) → reply with the **equity plan only**
  and a note "no options listed for SYM."
- Chain fetch error / all strikes illiquid → equity-only, graceful note. Never
  raises into the poller (mirrors `notify.broadcast` best-effort discipline).
- `per_contract_stop_loss ≤ 0` → size option by premium, min 1 contract, flag it.
- Missing IV → realized-vol fallback, flagged.
- Bad/oversized `p` or `risk=` → clamp and note.

## Modules

- `scanner/options.py` — `black_scholes`, `realized_vol` (or reuse `indicators`),
  `select_contract`, `size`, `scenario_ev`, `decide`, `conviction_to_p`,
  `flip_point`. Pure; chain injected.
- `scanner/optfmt.py` — `format_trade(plan)` (decision surface) +
  `format_trade_full(plan)` (audit tier). Pure string builders (HTML parse mode).
- `scanner/bot.py` — `parse_trade(update)`, `handle_trade(...)`, dispatch wiring.
  `fetch_chain(symbol)` (live, best-effort) lives here or in `data`.

## Testing (TDD)

- `black_scholes`: known-value checks; put-call parity; `0≤delta_call≤1`, put delta
  in `[-1,0]`; `theta<0` for long options; monotonic in `sigma`.
- `conviction_to_p`: monotonic, clamped to a sane band.
- `select_contract`: on a synthetic chain, picks expiry nearest target DTE (and
  `> H`) and the ATM strike; rejects illiquid strikes; realized-vol IV fallback.
- `size`: shares/contracts math; actuals recomputed from integer positions;
  max-loss = premium; per-contract-stop fallback path.
- `scenario_ev` / `decide`: EV signs and ordering on crafted inputs; payout
  multiple; **flip point** ties to where the decision changes; IV rich/cheap label.
- `parse_trade`: `trade nvda`, `trade nvda 60`, `risk=`, `dte=`, `full`; rejects
  non-trade and bare tickers (still chart).
- `format_trade` / `format_trade_full`: contains the order line, kill level, dates,
  no EV/R on the decision surface, EV present only in `full`.
- Live `fetch_chain` and `handle_trade` end-to-end: smoke only (like `fetch_daily`).

## Stated simplifications / non-goals (surfaced to the user)

- Constant IV across scenarios (no vol crush); European BS, no dividends/early
  exercise; 3-scenario (not a full distribution); yfinance ~15-min delay.
- Confidence `p` is the trader's own input, not a calibrated model probability; the
  conviction→p map is a rough suggested default only.
- Out of scope v1: spreads/multi-leg, position management after entry, an
  auto-attached options block on every alert, dashboard payoff curves.
- Nice-to-have (attempt, graceful skip): earnings-before-expiry IV-crush warning.

## Expected behavior (not a bug)

Because options are risk-matched by premium, for a **modest** target (a typical
+5% ATR swing) against near-the-money, weeks-out, or high-IV contracts, the honest
EV recommendation is frequently **shares** — you only fit a contract or two, and a
small move barely clears the premium. Options win when the expected **move is
large** relative to premium, the option is **cheap** (lower IV / shorter DTE /
further OTM → more contracts), or **confidence is high** (payoff convexity). A tool
that says "actually, just buy the shares" for a small target is working correctly —
that truthfulness is the point.

## Open follow-ups (not v1)

- Spreads (debit verticals) as a third vehicle when IV is rich.
- IV percentile/rank via historical IV if a source is added.
- Auto-suggest `trade` inline on a fired alert's CTA.
