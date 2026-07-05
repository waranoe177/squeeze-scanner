# Sqzdots — Subscription Business Design
**Date:** 2026-07-04
**Status:** Approved
**Project:** Sqzdots Indicator — from personal scanner to subscription product

---

## Overview

Sqzdots evolves from a personal swing-trade scanner into a **transparency-first signal
subscription**: a systematic squeeze scanner that publishes every signal it fires —
wins and losses — before the move happens, backed by a public, auto-updated,
timestamped track record.

One-line pitch: *"A systematic squeeze scanner with receipts."*

It is sold as a **tool priced like software** ($29/mo), not mentorship priced like
access. The moat is the accumulated, timestamped, git-verifiable track record, which
compounds daily and cannot be faked or copied by a new entrant.

The owner's goal is **validate first, then low-touch side income** ($1–5K MRR,
mostly automated). The March 2026 "90-Day Launch Plan" (daily FinTwit content,
$199/$799 tiers, 45–60 min/day) is explicitly superseded by this design.

---

## Goals

- Prove (or disprove) the signal's mechanical edge cheaply before anything public
- Build a public, verifiable live track record that markets itself
- Convert demand into recurring revenue with near-zero ongoing operator effort
- Design stickiness into the product so churn stays manageable without community management
- Keep a clean path to V2 (per-user watchlists) without rework

## Non-Goals

- Daily manual content creation, live sessions, 1-on-1s, or community moderation
- LLM evaluation layer (removed from the product pipeline for now; `llm_eval.py`
  remains in the repo unused)
- A logged-in SaaS web app, user accounts, or per-user state (deferred to V2)
- Automated Twitter/X posting
- Intraday or real-time signals (the product is a daily post-close scan)
- Investment advice of any kind (see Positioning & Legal)

---

## Phases and Gates

### Phase 0 — Private backtest (2–4 weeks)

Extend the existing `backtest.py` walk-forward harness to the full curated
~150-name universe over 3–5 years, using the exact live trade model (see Trade
Model). Nothing public happens in this phase.

**Gate to Phase 1:** positive expectancy (avg R > 0) across the universe and a
drawdown/losing-streak profile the owner would personally tolerate.
**On failure:** stop or revise the signal. Nothing was spent; nothing public was risked.

### Phase 1 — Public live record (90 days)

- Free **public** Telegram channel receives full same-day signals (it *is* the
  product during this phase, and it *is* the waitlist — joining the channel is
  joining the waitlist; no separate email-capture funnel).
- Public track-record page (GitHub Pages) auto-updates from the signal ledger every run.
- Weekly recap card (auto-generated PNG) posts to the channel every Sunday.
- Seeding: one personal (non-broadcast) message to 20–50 trading friends and
  professional contacts, with a single ask — "if it's useful, forward it to one
  person who trades."
- Around day 60 (~30 closed signals), 3–4 one-time "radical transparency" posts
  (e.g., r/swingtrading, r/thinkorswim, LinkedIn): "I published every signal for
  90 days — here's the full record including losses." No content treadmill.

**Gate to Phase 2:** ≥30 closed signals AND (win rate ≥55% OR avg R > 0)
AND ≥150 free-channel members.

### Phase 2 — Monetize

- **Private** Telegram channel gated by Whop: $29/mo or $290/yr.
- Founding-member rate: $19/mo locked for life, first 50 subscribers, offered to
  existing free-channel members first.
- Free channel remains as the top of funnel but degrades to **delayed signals**
  (posted next morning) + the weekly recap card.
- Referral program (Whop native): 1 month free per converted referral.

**Revenue math:** 70 subs ≈ $2K MRR; 170 subs ≈ $5K MRR. At 5–8% monthly churn
(typical for trading tools), $2K MRR requires ~4–6 new subscribers/month, fed by
the free channel without operator-created content.

**Operator steady-state:** no daily approvals (pipeline is autonomous), glance at
the channel a few times a week, occasional support email.

---

## Product Definition

| Tier | Price | Contents |
|---|---|---|
| Free channel | $0 | Next-morning delayed signals (Phase 2+; same-day during Phase 1), weekly recap card, link to track record |
| Paid channel | $29/mo · $290/yr · $19/mo founding | Same-day alerts at scan time: ticker, direction, entry/stop/target levels, conviction score (0–100 + grade), chart image |

The signal itself is the existing validated confluence (squeeze ON · RSI > 50 ·
PPO ≥ 0 · EMA8 > EMA21 · full stack · MACD green · Moxie > 0 and rising; SELL =
strict mirror), computed deterministically. No LLM layer.

### Universe

- **V1:** `universe.csv` — curated ~150 liquid names: major index/sector ETFs +
  most-traded large/mid caps, curated once by the owner. Replaces `watchlist.csv`
  as the product scan input; the personal `watchlist.csv` remains usable via CLI flag.
- **V2 vision (explicit direction, not built now):** users pick their own tickers
  from a supported universe — the main long-term value proposition. Protected by
  the ledger schema contract (below), so V2 imports history rather than restarting.

---

## Architecture

**Principle: one engine, three outputs.** The existing scanner remains the single
source of truth. Everything new is downstream, lives in the same repo, and runs on
free GitHub Actions. No servers until V2.

```
scanner/            existing engine (indicators, signals, score, data, chart, notify, run)
  ledger.py         NEW — signal ledger: fire → open → close lifecycle
  trackrecord.py    NEW — static track-record site generator
  recap.py          NEW — weekly recap card (PNG) renderer
universe.csv        NEW — curated ~150-name product universe
ledger/signals.jsonl  NEW — append-oriented signal ledger (committed; git = tamper-evidence)
out/site/           NEW — generated static site, published via GitHub Pages
.github/workflows/  extended — daily scan job; morning delayed-post job (Phase 2); Sunday recap job
```

### Trade Model (shared with backtest — single code path)

Reuses `backtest.trade_levels` and the `simulate_trade` touch logic:

- Entry: next bar's **open** after the signal date
- Target: entry + 2.5 × ATR (mirrored for shorts)
- Stop: entry − 1.5 × ATR (mirrored for shorts)
- Time exit: close of bar 5 if neither level is touched
- When one bar touches both stop and target: counted as a **stop** (conservative)

The live ledger and the Phase 0 backtest use the same functions, enforced by a
parity test. The public track record and the backtest are the same math — a
credibility feature, stated on the methodology page.

### `scanner/ledger.py` — signal ledger (the heart of the product)

Append-oriented JSONL at `ledger/signals.jsonl`, committed to the repo so git
history provides tamper-evidence.

Record schema (`schema_version: 1` — this is the V2 import contract):

```
id, symbol, direction (bull|bear), signal_date, entry_date, entry, stop, target,
conviction_score, telegram_msg_id, status (pending_entry|open|win|loss|time),
exit_price, exit_date, r_multiple, schema_version
```

Lifecycle:
1. Signal fires → record appended with `status=pending_entry`; alert posted
   (levels computed from the signal bar's ATR; entry price unknown until next open).
2. Next run backfills `entry` and `entry_date` from the actual open → `status=open`.
3. Each daily run evaluates open records against the day's high/low/close using
   the shared touch logic → closes as `win`/`loss`/`time` with `exit_price`,
   `exit_date`, `r_multiple`.
4. Closed records are **never edited**. Corrections, if ever needed, are new
   correction records; git history makes any rewrite visible.

### `scanner/trackrecord.py` — public track-record site

Regenerated from the ledger on every run; plain fast HTML/CSS (no Streamlit for
the public page — must load fast and look credible on a phone). Published via
GitHub Pages, custom-domain ready.

Pages:
- **Home:** headline stats (n signals, win rate, avg R, equity curve in R),
  including the backtest's historical losing-streak/drawdown numbers (actual
  figures come from the Phase 0 run) with plain wording ("this system has
  historically had losing streaks of N signals; the edge is in the average").
- **All signals:** every signal ever fired, losses included, each row linking to
  its original timestamped Telegram post (the receipt).
- **Methodology:** the rules stated plainly, the trade model, and the disclaimer.

The existing Streamlit dashboard remains the owner's private tool.

### `scanner/recap.py` — weekly recap card

Sunday job renders one shareable PNG (matplotlib): the week's signals and results,
running win rate, best call with mini-chart, channel link. Auto-posts to the free
channel. This is the primary word-of-mouth artifact — machine-made, forwarded by
members, zero operator effort.

### `scanner/notify.py` — channel-aware delivery

Extended with a tier config:
- **Paid channel:** full alerts at scan time (levels, conviction score, chart).
- **Free channel:** Phase 1 = full same-day alerts; Phase 2 = delayed next-morning
  posts + weekly recap.
- **No-signal days still post** (see Stickiness): "Scanned 150 names. 0 fired.
  12 squeezes building: [tickers]. Closest to trigger: XYZ (5/7 conditions lit)."

### Data fetching (`data.py`)

Batching + throttling + retry for ~150 tickers/day on yfinance. The fetch layer is
already isolated; if yfinance degrades, swapping in a paid data API (~$30–50/mo)
is a one-module change.

### GitHub Actions

- **Daily post-close (21:30 UTC weekdays):** scan → append new fires to ledger →
  backfill entries → update open outcomes → post alerts → regenerate site →
  commit ledger + site + charts. (Cron jitter of 5–15 min is acceptable for a
  post-close daily scan.)
- **Morning job (Phase 2 only):** post yesterday's fires to the free channel.
- **Sunday job:** render + post the weekly recap card.
- All job failures alert the owner's private Telegram admin chat.

### Whop (Phase 2 — configuration, not code)

Checkout, private-channel gating (auto-invite/kick on pay/cancel), founding-member
coupon, referral program, cancellation survey. No custom billing code.

---

## Stickiness & Churn Design

Churn in signal products concentrates at two moments; each gets a countermeasure:

1. **Drawdowns → expectation-setting.** The track record page and onboarding copy
   promise imperfection up front (historical losing streaks shown). The weekly
   recap always reports the running record, red weeks included. Subscribers
   promised imperfection don't churn at the first losing streak.
2. **Quiet weeks → the watching state becomes content.** No-signal days still
   deliver the "squeezes building" post (one message template, zero ongoing
   effort). Subscribers wait for something specific instead of staring at silence.
3. **Annual plan as structural churn defense.** $290/yr (2 months free), pitched
   at signup and again at month 3. Each annual conversion removes 12 monthly
   churn decisions.
4. **V2 hook seeded early.** Paid members get a "request a ticker" form (pinned
   message + simple form). Requests inform universe curation and plant the
   personal-watchlist story; V2 becomes the retention upgrade.
5. **Exit ramp.** Whop cancellation survey + a "pause instead of cancel" offer.

---

## Waitlist / Growth Mechanics (low-touch by construction)

- The free Telegram channel **is** the waitlist — no separate funnel.
- Every alert footer carries the channel/site link; forwarding a Telegram post is
  one tap — each share is a recruitment vector.
- The weekly recap card is the shareable object (word of mouth dies without one).
- Timestamped Telegram posts + git-committed ledger = receipts a stranger can verify.
- One-time seeding: personal notes to 20–50 network contacts (Phase 1, week 1);
  3–4 transparency posts (Phase 1, ~day 60). Referral program at Phase 2.
- No daily content creation by the operator, ever.

---

## Error Handling

Three failure classes, all routed to the owner's private Telegram admin chat:

- **Data failures:** single-ticker fetch failure → skip, note in run log.
  Whole-universe failure → no public post, admin alert. Never silently post
  partial results.
- **Pipeline failures:** Actions job failure → GitHub notification + admin
  Telegram alert. The ledger's append-only design means a failed run never
  corrupts history; the next run self-heals by re-checking open positions
  (evaluating each open record against all bars since its last check, so a missed
  day cannot skip a stop/target touch).
- **Delivery failures:** Telegram API errors → retry with backoff, then admin alert.

**Rule:** the system must never silently miss a day. If a scheduled post cannot
happen, the free channel gets a brief "no scan today (technical issue), back
tomorrow" message once the operator is alerted — a missed day with no explanation
is a trust leak on a transparency product.

---

## Testing

Base: existing 68-test pytest suite (including TOS parity fixtures). New tests:

- Ledger lifecycle: fire → pending_entry → open (entry backfill) → each close
  path (win / loss / time)
- Ledger immutability: closed records never change on subsequent runs
- Backtest/ledger parity: same trade through `backtest.py` and the live ledger
  produces identical levels and R (shared code path enforced by test)
- Track-record stats correctness against a fixture ledger
- Recap card renders without error from a fixture ledger
- Channel-tier routing: paid vs free content per phase, no-signal-day template
- Missed-day self-heal: open positions evaluated correctly across a gap

Phase 0's full-universe backtest doubles as the deepest integration test of the
engine at product scale.

---

## Positioning & Legal Constraints

Everything stays in **impersonal publisher** territory:

- Identical signals to all subscribers; no individualized advice; no "you should
  buy"; no performance promises.
- Visible disclaimer on the site, both channels, and checkout: educational tool,
  not investment advice; past performance does not guarantee future results.
- V2 stays on the right side of the line: per-user *filtering* of the same
  signals is acceptable; per-user *recommendations* would not be.
- Never cherry-pick: the ledger and track record include every fired signal by
  construction.

Public brand: **"Sqzdots Indicator"** for launch (channel and domain naming follow
it); a rename later is acceptable and cheap while the audience is small.

---

## External Dependencies & Costs

| Item | Phase | Cost |
|---|---|---|
| GitHub Actions + Pages | 0+ | Free |
| yfinance | 0+ | Free (paid API fallback ~$30–50/mo if needed) |
| Telegram bot + channels | 1+ | Free |
| Domain | 1 | ~$12/yr |
| Whop | 2 | No fixed fee; ~3% + Stripe fees per transaction |

Total pre-revenue spend: ~$12.

---

## Success Criteria

- **Phase 0:** backtest across universe runs clean; expectancy and drawdown
  numbers produced; go/no-go decision made on the stated gate.
- **Phase 1:** pipeline runs autonomously for 90 days with zero silently missed
  days; ledger, site, and recap update without operator action; gate metrics
  tracked (closed signals, win rate/avg R, channel members).
- **Phase 2:** Whop checkout → auto-invite works end to end; free channel flips
  to delayed mode; first 10 paying subscribers; churn and MRR visible in Whop.

---

## Out of Scope / Future (V2+)

- User accounts + per-user watchlists (the stated main long-term value prop) —
  backend imports `ledger/signals.jsonl` (schema v1) as its history
- Self-serve screener web app
- Additional universes (full S&P 500, international)
- Email digest delivery
- Automated X/Twitter posting
