# Sqzdots — Wishlist & Notes

A living list of where this tool is headed and what to refine. Jot things down here
as you use it daily — features, bugs, and (most valuable of all) which setups actually
work in practice.

**North star:** turn Sqzdots into a tool that supports **consistent weekly cash flow**.

**Guiding principle (the honest one):** the *plumbing* is buildable anytime; what turns
this into cash flow is a **validated edge**. So far the mechanical signal has **not**
beaten baselines in testing — it's a watchlist / timing-aid / educational tool, not a
proven money-maker. Refine the setup and method first; automate money only once there's
a real, out-of-sample edge.

**Caveat added 2026-09-22:** one exception to "plumbing is cheap" — the DATA FEED is not
plumbing. A silently wrong bar corrupts every signal, level and backtest downstream, and it
does so invisibly. See item 1.

---

## Wishlist

### 1. Professional data feed (IBKR / Databento) — reliability FIRST, intraday second
Originally filed as "intraday feed so I can day-trade when I choose to." **Re-scoped
2026-09-22: the primary driver is now DATA INTEGRITY, not intraday.** Intraday is the bonus.
- **Status:** not started. **Priority raised** — see the incident below.
- **Risk level (corrected):** previously logged as "low — safe to build anytime." That was
  wrong. The *build* is low-risk; **staying on yfinance is not.** The feed is the foundation
  every signal, level and backtest sits on, and it has now been observed serving silently
  wrong data.

#### Why this moved up: the 2026-09-21 stale-data incident
The Monday 2026-09-21 post-close scan delivered signals computed from **Friday 09-18's**
close, while the message header read `bar 2026-09-21`. Nothing in the alert looked wrong.

Measured from the committed `out/results.json` history:

| | Monday night (run #68) | Tuesday 09:53 (run #69, SAME bar) |
|---|---|---|
| Fired | ARKK, HOOD, **RIVN, BA** | ARKK, HOOD, **SMCI, ANET, NVDA** |
| Bar date | 2026-09-18 | 2026-09-21 |

Real cost of one bad night: **2 wrong signals acted on** (RIVN and BA never fired on Monday's
data) and **3 real signals missed entirely** (SMCI, ANET, NVDA). ARKK/HOOD did fire both days
but Monday's card carried Friday's ATR-derived entry/stop/target — so the dollar risk printed
on the card was not the real risk.

- **Mechanism:** `fetch_daily` pulls all 158 tickers in ONE bulk `yf.download(threads=True)`
  (`scanner/data.py`). Some symbols came back with NaN OHLC for Monday; `normalize`'s
  `dropna(subset=[open,high,low,close])` silently discarded those rows, so their "latest
  completed bar" fell back to Friday. `as_of` is `max(bar date)` across the universe, so the
  symbols that DID have Monday made the header look current. The failure was invisible by
  construction.
- **Measured rate:** 1 stale run in 68 with fired signals (~1.5%), i.e. **roughly 4 nights a
  year**. Not a timing bug — 8 of 9 runs that crossed midnight UTC were clean, so the
  "UTC rollover" theory was tested and FALSIFIED. It is transient yfinance flakiness and can
  hit at any hour.
- **Why yfinance is structurally shaky:** unofficial scrape of Yahoo, no SLA, no support, no
  correctness guarantee; bulk multi-ticker requests fail *partially* rather than loudly; the
  library has broken outright several times when Yahoo changed their endpoints.

#### The futures constraint (this narrows the field a lot)
The watchlist includes ES/NQ/YM/RTY/GC/SI/CL/BTC. Most cheap equity APIs do not carry futures,
so this is not a drop-in swap:

| Source | Cost | Futures | Verdict |
|---|---|---|---|
| yfinance (current) | free | yes | Unreliable, no SLA |
| Stooq | free | **no** | Equities cross-check only |
| Tiingo | free/~$10 | **no** | Good EOD, equities only |
| Polygon | ~$29+ | **no** (cheap tiers) | Reliable equities |
| **IBKR** | **~$10–40/mo** | **yes** | Covers everything + intraday. Front-runner |
| Databento | pay-as-you-go | yes | Excellent, priciest |

Only **IBKR** and **Databento** cover both. IBKR also unlocks the original intraday goal and is
the same account that would later serve autotrade (item 2) — one move, three problems.

#### What's already mitigating this (shipped 2026-09-22)
Freshness guard (suppress + announce any signal whose bar ≠ the session), loud `dropna`, honest
`as_of`, and a re-fetch retry for stale symbols. **These make the failure visible and stop me
acting on bad levels — they do NOT make the data correct.** If a retry also fails, that night's
real signals are simply gone.

#### Decision trigger — revisit when ANY of these is true
1. The retry fails to recover stale symbols more than **twice in a quarter** (i.e. nights where
   signals are genuinely lost, not just delayed).
2. I start sizing real positions off these alerts as income rather than as a watchlist.
3. I want intraday / futures day-trading (the original item-1 goal).
4. yfinance breaks outright again, as it has historically.

**Framing for future me:** I treat this tool as something my livelihood depends on. A $10–40/mo
feed against a system making position-sizing decisions is not a cost question, it is an
insurance question. The only reason to wait is to first confirm the free mitigations are
sufficient — not to save the money.

### 2. AI autotrade
After `trade SYM` + confirming the instructions, auto-submit the order to a brokerage.
- **Status:** ON HOLD — deliberately, until there's a proven edge.
- **How (when ready):** broker API (Tradovate / IBKR / Tradestation for futures; Alpaca for
  equities).
- **Gate (do NOT skip):** validated out-of-sample edge → paper-trade the full loop → tiny live
  size → hard risk controls (max daily loss, position caps, kill switch). Automating orders on
  a no-edge signal just automates the losses, fast, with real money and no undo.

### 3. Trade workstation webapp
A screen showing tickers in action + "bots in various roles" to consult, drill into the trade
thesis, and execute.
- **Status:** not started (Streamlit dashboard skeleton exists as a starting point).
- **How:** agent-orchestration vision — signal agent + qualitative/eval agents → thesis + chart
  → decision.
- **Risk level:** low — a better cockpit sharpens judgment; safe to build anytime.

### 4. 2D & 3D chart rendering (match TOS screen)
Add 2-day and 3-day aggregated charts so the multi-timeframe view matches what I see on TOS.
- **Status:** PARTIALLY built, deferred. `chart.render_mtf_composite` (2x2 daily/2D/3D/weekly)
  exists in code; weekly (1W + monthly Moxie) already validated and shipped. 2D/3D were pulled
  because the aggregation/indicators looked "off and unacceptable" vs TOS.
- **How:** fix the 2D/3D bar aggregation + anchoring (pin the N-day grouping to a FIXED reference
  date, not the last bar, so pairing stays stable as bars are added) and calibrate the indicators
  (squeeze/MACD/Moxie) on the aggregated bars to match TOS; then wire the composite back into the
  `trade SYM` / weekly output. Validate each TF side-by-side against a TOS screenshot.
- **Risk level:** low — display/visual only; no signal-logic or money risk.

### 5. Catalyst footnote on `trade SYM` (earnings + key economic dates)
When I ask for `trade SYM`, warn me about scheduled events that land inside the holding window,
so I know the technical setup is about to meet a catalyst it can't price.
- **Status:** not started
- **Why it matters:** the trade card plans a 2.5ATR target / 1.5ATR stop over ~5 days. An earnings
  release inside that window makes the stop meaningless — price gaps straight through it overnight,
  so the "risk" number on the card is not the real risk. Same for macro prints on the index/futures
  side (FOMC, CPI, NFP, PCE), which move ES/NQ regardless of the chart.
- **How:** a footnote line on the card, e.g. `⚠️ earnings 2026-10-28 (in 4 days — gap risk through
  the stop)` or `⚠️ FOMC 2026-11-05 (day 3 of hold)`. Only show it when the event falls inside the
  max-hold window; stay silent otherwise so it doesn't become noise.
  - Earnings: yfinance exposes `Ticker.get_earnings_dates()` / `.calendar`. Treat it as
    best-effort — dates are often "estimated" and shift, so label confirmed vs estimated and never
    let a lookup failure break the card (same best-effort rule as the charts in `run.py`).
  - Economic dates: no good free API. Options are a small hand-maintained YAML of FOMC/CPI/NFP/PCE
    dates (they're published a year ahead), or scrape one calendar source. Start with the YAML —
    boring, accurate, and no runtime dependency.
- **Risk level:** low to build, but it's the first feature that would change my sizing decisions,
  so the honest failure mode is a WRONG or stale date giving false confidence. A missing warning
  must fail loud (say "earnings date unavailable"), never silently imply "all clear".

---

## Refinement log (append as I go)

### Setups that actually work (real-world observations)
_The most important notes — which signals pay off in practice. This is what could eventually
unlock the edge and item #2._
- …

### Feature ideas
- …

### Bugs / parity issues (app chart vs TOS, etc.)
- …

---

_Maintained by the user; Claude reads this when working on Sqzdots. Last structured update: 2026-09-22._
