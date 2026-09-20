# Analytical Reports

Refinement analysis of the scanner's actual notified signals — **not** live results
and **not** a performance claim. Each row simulates *"what if I had taken every
daily notification under a fixed budgeted model."* Framing: analytical refinement.

## Data source
`ledger/signals.jsonl` — every signal the daily scan notified, **2026-07-02 → 2026-09-18**
(267 usable of 268; 1 too recent to have an entry bar). 193 long / 74 short.

## Model parameters
- **Entry:** next session's open after the signal.
- **Target:** `entry ± 2.0×ATR`. **Stop:** `entry ∓ 1.25 / 1.75×ATR` (two files, a stop-width sweep).
- **Exit:** first of target / stop (stop checked first, conservatively) or **5-day max hold** (exit at close).
- **Stock budget:** $12,500 → shares = ⌊12,500 ÷ entry⌋.
- **Option budget:** $5,000 → near-ATM ~21 DTE, contracts = ⌊5,000 ÷ (premium×100)⌋.
  Realized vol used as an **IV proxy** (no historical option chains) — a known approximation.

## Files
- `analysis_4mo_stop1.25.csv` — stop 1.25×ATR
- `analysis_4mo_stop1.75.csv` — stop 1.75×ATR

Columns: symbol, signal_date, direction, conviction, entry_date, entry, atr, target_2atr,
stop_*atr, exit_date, exit_price, bars_held, outcome (win=target / loss=stop / time),
r_multiple, stock_shares/cost/pnl/ret%, opt_iv/entry_prem/exit_prem/contracts/cost/pnl/ret%.

## Key finding (stop-width sweep)
Widening the stop **1.25 → 1.75×ATR** cut stop-outs (123 → 68, i.e. 46% → 25%) — but total
P&L got **worse**, not better (stocks −$10.8k → −$15.0k; options −$74.4k → −$78.9k). With no
directional edge in the base signal, a wider stop just lets losers run into bigger *time-exit*
losses (time exits 101 → 155). **Exit width is not the lever** — the tight stop was limiting
damage, not causing it. Any edge would have to come from **selection** (which signals to take),
not from tuning the exit. Consistent with the project's standing "no demonstrated mechanical
edge" verdict — the tool's value is as a discretionary watchlist / timing aid.

## EMA21 selection filter (the promising lead)
Files: `analysis_4mo_ema21touch.csv` (strict: bar wicked to EMA21 and closed on the
correct side) and `analysis_4mo_ema21prox.csv` (relaxed: within 0.25×ATR of EMA21 **or**
wicked to it, closing on the correct side).

Both selected the **same 15 of 267 signals (6%)** — because the scanner's signals cluster
either *right at* the EMA21 (rare) or clearly *extended* (>0.25×ATR, the other 94%), with
little in between, so relaxing the band added no sample.

The 15 that passed were **net positive** (stock +$1,636 / 60% win; option +$3,486) vs the
252 rejected (stock −$12,472; option −$77,927) — the first filter to invert expectancy, and
it matches the thesis that entries *at* the EMA beat extended chases. **But:** n=15 is far too
small to trust, and the positive is **entirely short-driven** — bull (7): stock −$85 / option
−$1,546; bear (8): stock +$1,720 / option +$5,031. A lead to validate, not a proven edge.
Next test: a distance-from-EMA **gradient** across all 267 (does P&L improve monotonically as
signals get closer to the EMA?) — a full-sample test instead of a 15-trade cutoff.

## Caveats
- "Total P&L" assumes unlimited capital to take every signal at full budget; the honest
  per-signal metric is **avg P&L / trade**. A single account can't hold all 267 concurrently.
- ~2.5 months, small sample. Bear/puts looked positive but were tail-driven (one MRK trade).
- RV-as-IV means option premiums are approximate.
