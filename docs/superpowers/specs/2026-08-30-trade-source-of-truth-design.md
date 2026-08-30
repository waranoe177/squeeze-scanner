# Trade Source-of-Truth: `trade` never contradicts the daily alert

**Date:** 2026-08-30
**Status:** reviewed (eng review complete) — ready for plan
**Supersedes behavior in:** `scanner/bot.py` bare-`trade` path (the one-eval re-derivation)
**Builds on:** 2026-08-26-trade-follows-chart-design.md (reply path stays as-is)

## Problem

`trade SYM` contradicted the daily alert again. Daily fired **BUY APD** (bar
2026-08-28, score 84). A later bare `trade APD` returned a **no-signal** card for
bar **2026-08-27** (score 49) — a day older, opposite conclusion.

Root cause (investigated, proven deterministic): the bare path re-derives from a
fresh `fetch_daily` at a DIFFERENT time than the daily cron. `drop_forming_bar`
(data.py:88-90) is behaving CORRECTLY — it drops the incomplete current-day bar —
but "latest completed bar as of NOW" run intraday/pre-market is a bar BEHIND the
post-close scan's locked bar. (Second contributor: yfinance re-adjusts historical
bars for splits/dividends, so re-fetching 2y of history days later shifts
close/EMA/ATR/score for the SAME date.) Net: re-deriving at request time cannot
reproduce the alert.

This is the **second instance of the same class**: `trade` re-derives its own
signal and diverges from the alert. trade-follows-chart fixed the REPLY path
(caption-frozen). The BARE path still re-derives, so it stayed exposed.

## Design principle

**The daily scan is the single source of truth. Every `trade` request resolves to
the signal the scan already persisted — never a fresh re-derivation.** Only the
option chain and realized vol are fetched live, because option quotes must be
current (this affects option PRICING, never direction or levels).

## Design

### Reply `trade` to a chart — unchanged
Already anchored: parses the replied-to caption for direction + levels.

### Bare `trade SYM` — anchor to the persisted scan via the SAME caption parser
1. Load the latest persisted scan snapshot: `out/results.json` (written and
   committed by the daily run; the bot always has it after a fresh checkout).
2. Look up SYM in `results.json.fired[]`.
   - **Found** → **synthesize the alert's `_fired_line` caption from that payload
     and run it through the existing `captionparse.parse_caption`** — the same
     tested anchor path the reply path uses. This is deliberate (Decision A): the
     caption carries `prov_target`/`prov_stop` (direction-aware, from
     `backtest.trade_levels`), which is exactly what the alert showed. It avoids a
     second field-extractor and the long-side-stop trap (see "Levels" below).
     Send the daily's already-committed chart `out/charts/SYM.png` (the same
     picture the alert sent), then the card from the parsed levels + a LIVE chain +
     realized vol. Prepend `follows the {scan date} scan signal`.
   - **Not found** → SYM has no active signal in the latest scan. Do NOT
     re-derive. Reply (Decision 1): "SYM has no active signal in the latest scan
     (as of {date}). To analyze it anyway, `chart SYM` then reply `trade` to the
     chart." (Off-list/exploratory is served by the caption-anchored reply path.)
3. **Remove the bare-path one-eval re-derivation** (`latest_signal` for
   direction/levels). That is the bug source.

### Levels: use `prov_target`/`prov_stop`, never the raw `latest_signal` fields
The alert caption prints `prov_target`/`prov_stop` (`notify._fired_line:44-49`),
which are direction-aware. The raw `latest_signal.stop` is ALWAYS the long-side
`close − 1.5·ATR` (signals.py:287) and `target_up/target_dn` are EMA21±2.5·ATR —
different numbers, and a SELL would get a long-side kill. Reusing `captionparse`
(Decision A) sidesteps this entirely because the synthesized caption is built from
`prov_*` exactly as the alert builds it. A golden test pins that the synthesized
caption round-trips through `parse_caption` to the alert's levels.

### Freshness guard (Decision B): stale levels vs live price
Anchoring makes weekend/Monday bare trades stale by default (entry = an old
close), but the chain prices at today's spot. If the stock already ran past the
anchored target, the shares-vs-options math prices a fictional entry. Guard: after
anchoring, fetch the live spot; if it has diverged from the anchored entry by more
than ~1 ATR (or crossed the anchored target), do NOT price a card — reply "levels
are from {date}; SYM has moved to {spot} — pull a fresh chart (`chart SYM`) and
reply `trade`." This catches the fictional-entry case the vintage line cannot.

### Why this kills the bug (scoped honestly)
The found path takes direction/entry/target/stop/score from the persisted snapshot
via the caption parser — byte-for-byte what the alert showed — so **those cannot
change run-to-run or land on a different bar**. `drop_forming_bar` timing no longer
affects any anchored trade's direction or levels. The live chain + realized vol
still vary (correct — that is option pricing), so the guarantee is scoped to
direction/entry/target/stop/score, not the whole card.

## Decisions (locked in eng review — Claude + outside voice)

- **A — anchor method:** reuse `captionparse` (synthesize the `_fired_line`
  caption from the results.json payload, parse it). ONE anchor path shared with
  the reply path; kills the long-side-stop trap for free.
- **B — freshness guard:** ADD the price-moved guard (refuse when live spot
  diverged > ~1 ATR from anchored entry, or crossed the target).
- **1 — off-list/no-signal:** refuse + redirect to `chart SYM` then reply `trade`.
  No re-derivation on the bare path.
- **2 — anchor source:** `out/results.json.fired[]` (the last alert's snapshot),
  not the ledger.
- **3 — `drop_forming_bar`:** leave it (it is correct), add a code comment naming
  the request-time-vs-scan-time skew so no future re-derivation reintroduces it.

## Staleness / vintage

- results.json is the last scan's snapshot; the vintage line makes a days-old
  anchor explicit, and the freshness guard (B) refuses when price has moved.
- **Post-close / pre-scan window:** between today's 16:00 ET close and the scan's
  push (~21:30 UTC, often late), today's bar is complete but results.json is still
  yesterday's, so a bare `trade` returns yesterday's signal. It does not
  contradict an ALERT (today's has not been sent), so it is consistent with the
  principle; the vintage line + guard B cover user expectation. Accepted, named.

## Edge cases

- `results.json` missing/unreadable (fresh clone before first scan) → treat as
  no-signal (Decision 1); never crash the poller.
- SYM in `watching[]` but not `fired[]` → no active signal (Decision 1).
- `out/charts/SYM.png` missing for a found signal → send the card without the
  chart; never block the card on the image.
- realized-vol live fetch (for IV fallback) still runs `drop_forming` on a
  clock-dependent bar — acceptable, affects only option pricing, not levels.
- Owner-only filter, go/pass handling, exactly-once offset → unchanged.

## Non-goals

- No change to the signal math, the options engine (Playbook B, exit-value
  pricing), or the reply path.
- No change to how the daily scan selects its bar.
- Not opening `trade` to non-owner accounts (separate deferred item).
- Not persisting realized_vol into `fired[]` (deferred; live rv is acceptable
  since it never affects direction/levels).

## Acceptance criteria

1. After a daily **BUY APD**, bare `trade APD` returns a **BUY** card whose
   direction, entry, target, stop, score equal what the alert showed (via the
   synthesized-caption anchor), never a contradictory card, regardless of when it
   runs.
2. The bare path performs **no `latest_signal` re-derivation** for direction/levels
   of an in-scan symbol (test: a stubbed live fetch on a different bar cannot
   change the anchored direction/entry/target/stop/score).
3. Bare `trade SYM` for a symbol absent from `results.json.fired[]` returns the
   refuse+redirect message, never a fabricated card.
4. When live spot has moved > ~1 ATR past the anchored entry (or crossed target),
   the freshness guard refuses with the "levels are from {date}" message.
5. `results.json` missing → graceful no-signal reply; poller survives.
6. A golden test proves the synthesized caption round-trips through `parse_caption`
   to the alert's `prov_target`/`prov_stop` levels (locks the anchor format).
7. Full suite green; tests cover found-anchor, not-found, missing-file, the
   freshness guard, and the no-re-derivation guarantee.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clean | 6 findings, 0 critical gaps; 2 new decisions locked, 3 confirmed |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | n/a | No UI scope (Telegram text card) — exited early |

**Outside voice (Claude subagent, Codex not installed):** confirmed all 3 flagged
decisions; caught prov_* vs raw-field trap (→ Decision A reuse captionparse),
scoped the "no re-derivation" guarantee (rv still live), surfaced the stale-price
case (→ Decision B guard), and named the post-close/pre-scan window. Agreed the
"deterministic-asof re-derivation" shortcut is INSUFFICIENT (yfinance re-adjusts
history + alert uses prov_* not latest_signal fields), so anchoring is warranted.

**CROSS-MODEL:** no tension — both reviewers agree on all decisions.

**VERDICT:** ENG CLEARED — ready to write the implementation plan.

NO UNRESOLVED DECISIONS
