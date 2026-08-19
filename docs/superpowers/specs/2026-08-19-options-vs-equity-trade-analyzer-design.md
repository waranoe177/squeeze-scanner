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
  score only *suggests* a default; the number is the user's own and appears as a
  labeled assumption line in the body, **never in the header** (it's an input, not
  a finding).
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

The option carries **no underlying stop** — its risk is the premium (defined). The
`Kill below/above <stop>` line is **equity-only**. The option's downside is managed
by the target, the exit date, and a sideways close for residual value; every option
loss is bounded by the premium.

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
  `option_ev(p*) = equity_ev(p*)` in closed form. This is a **sensitivity for the
  `full` tier only** — it never appears on the decision surface.
- **IV context:** compare option IV to realized vol of the underlying — `rich` if
  `IV > 1.2·RV`, `cheap` if `IV < 0.8·RV`, else neutral.

## Output

### Decision surface (default)

**Worked from the acceptance-test inputs** (entry $123.45, target $130.00, stop
$118.00, IV 42%, 35 DTE, hold 10 days, risk $840). Priced correctly the $130 call
only *reaches* the money by exit, so its exit value is time premium (~$5.87) and
**shares win** — this is the honest output, every number checkable on screen:
```
NVDA · BUY SHARES · exit by Fri 08/29
  assumes ~65% you're right on +5.3%

  BUY 154 sh @ $123.45
  Kill below $118 (−$840) · target $130 (+$1,009)

  WHY   3× the payout of the call at the same $840 risk
  COST  $19,011 capital; full downside below $118
  SKIP  2 × 130C 09/23 @ $4.20 → only +$334 at $130
        (call ≈ $5.87 at exit vs $4.20 paid); −$840 if it never moves
```
Checks: 154×$5.45 ≈ $840 at stop; 154×$6.55 ≈ $1,009 at target; call exit value
$5.87 (BS, 25 DTE left, ATM) → (5.87−4.20)×100×2 = $334; 1009 ÷ 334 ≈ 3×.

**When the option wins** the block inverts (schematic — placeholders, never
fabricated numbers):
```
SYM · BUY CALLS · exit by <day mm/dd>
  assumes ~NN% you're right on +M.M%

  BUY k × SYM <K>C <exp> @ $<mid> or better
  Max loss $<premium> (all you can lose)
  Target $<T> → ≈ $<gain>   (<K>C ≈ $<exit_val> vs $<mid> paid)
  Flat by <exit> → close ≈ $<flat_val> back

  WHY   ~<x>× the payout of shares at the same $<risk> risk
  COST  needs +M.M% by <exit>; IV <iv>% is <rich/cheap>
  SKIP  <n> sh → ≈ +$<eq_gain>, no clock, no decay
```

Rules enforced:
- Header: `SYM · <ACTION> · exit by <day mm/dd>` — the **decision** only. No
  confidence in the header; it's one labeled body line ("assumes ~NN% …") because
  it's the trader's input, not a finding.
- Order line is a literal, typeable order with an "or better" limit; integer size,
  actuals recomputed from the rounded position.
- The option's **estimated exit value** (`<K>C ≈ $X at exit`) is shown so the
  payout is checkable at a glance — it is the one model-derived number. **No EV, no
  R-multiples** on this surface.
- **Three outcome lines, including the most-likely sideways case:** target; the
  downside (equity kill / option's defined premium); and **flat-by-exit → close for
  residual**. The sideways branch is mandatory — it's the case the reader most
  often faces.
- `Kill below/above <stop>` is **equity-only** (the option is defined-risk premium).
- Labels: **WHY** (reproducible payout multiple) · **COST** (move % + exit date +
  IV label, or capital tied up for shares) · **SKIP** (one-line rejected vehicle).
  No FLIPS line — it's a sensitivity, moved to `full`.

### `full` audit tier (on `full`)
Appends: chosen expiry + DTE, Δ/θ/IV raw, realized vol, the three scenario
probabilities, each vehicle's payoff in every scenario, both EV numbers, and the
**flip point** `p*` (the confidence at which the decision changes) — so the pick
can be audited. This is the only place EV and the flip point appear.

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

## Implementation order (engine before format)

The layout presents numbers convincingly, so it must not be built until the numbers
are proven right (this is what bit the first draft):

1. `black_scholes` + `realized_vol` — validate vs known values and the
   acceptance-test price (`v_target ≈ $5.87`).
2. `select_contract`, `size`, `scenario_ev`, `decide`, `flip_point`,
   `conviction_to_p` — pass the full acceptance test end-to-end on an injected
   chain (calls ≈ +$334 vs shares ≈ +$1,009 → **BUY SHARES**).
3. Only once the engine reproduces the independent numbers: build `optfmt`
   (decision surface + `full` tier) from engine output, then wire
   `parse_trade` / `handle_trade` into the bot.

## Testing (TDD)

- **Acceptance test — the intrinsic-vs-exit-value guard.** Inputs: entry $123.45,
  target $130.00, stop $118.00, IV 42%, 35 DTE, hold 10 days, risk $840. The engine
  MUST produce `v_target ≈ $5.87` (ATM call, 25 DTE remaining — *exit* value, not
  intrinsic $0), **calls ≈ +$334** (2 contracts) and **shares ≈ +$1,009** (154 sh),
  and `decide()` MUST return **BUY SHARES**. This is the regression that would have
  caught the original error; it is the gate to building the formatter.
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
- `format_trade` / `format_trade_full`: order line present; **confidence not in the
  header** (labeled body line only); **three outcome lines incl. the sideways
  close**; option **exit value** shown; kill line equity-only; **no EV, no
  R-multiples, no FLIPS** on the decision surface; EV + flip point present only in
  `full`.
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
