# Trade-Follows-Chart: options-vs-equity anchored to a visible chart

**Date:** 2026-08-26
**Status:** approved (design decisions locked)
**Supersedes behavior in:** `scanner/bot.py::handle_trade`, `scanner/optfmt.py::_direction`

## Problem

`trade SYM` re-derives its own signal (a fresh `fetch_daily` + `latest_signal`)
that is *separate* from any chart the user has seen. When that re-derivation
diverges from the daily alert, the card contradicts the alert.

Observed live (2026-08-26): the daily alert fired **BUY V** (Visa, score 91,
target 401.85, stop 373.52) and **BUY MA** (Mastercard, score 93, target 627,
stop 582.79). Requesting `trade V` and `trade MA` returned **SHORT SHARES** for
both, with a target ≈ entry ("assumes ~70% you're right on -0.0%"). The message
was even internally self-contradictory: "SHORT SHARES" over "Kill above 374",
which is a *long* stop.

Trading is psychologically loaded. A card that contradicts the alert the user
just acted on destroys trust and paralyzes the decision. This is the failure we
are eliminating.

## Root causes (all three fixed here)

1. **Re-derivation, not anchoring.** `trade` recomputes the signal instead of
   inheriting the one the user saw. Any data-timing / extension difference flips
   the answer.
2. **Direction inferred in two places that disagree.** `handle_trade` computes a
   direction to size the stop; `optfmt._direction` independently re-infers it
   from `target >= spot` to pick the BUY/SHORT label. When they disagree you get
   a short headline over a long stop.
3. **No guard on degenerate levels.** `target_up = EMA21 + 2.5*ATR` can sit at or
   below the current price for an *extended* name (V/MA at highs, RSI 71/73).
   Then move ≈ 0 and root cause #2 flips the label to short.

## Design principle

**The chart is the single source of truth.** `trade` inherits the chart's
direction and price levels *verbatim*. Only the option chain (implied vol,
contract prices) is fetched live at trade time, because option quotes must be
current. Direction is decided **once**, at the chart, and threaded through the
plan; `optfmt` never re-infers it.

This makes the trade card *incapable* of contradicting a chart the user is
looking at.

## Two entry paths (both anchored, both single-computation)

### Path 1 — Reply `trade` to a chart (primary, WYSIWYG)
The user long-presses any chart the bot sent and replies
`trade [CONF] [risk=N] [dte=N] [full]`. The bot:
1. Reads the replied-to message from `reply_to_message` — Telegram includes the
   original message's full **caption** in the update.
2. **Parses that caption** for the chart's own symbol, bar date, direction
   (BUY→bull / SHORT→bear), entry (close), target, and stop — the exact numbers
   printed on the chart the user is looking at.
3. Builds the options-vs-equity card from those **frozen, on-screen levels** + a
   **live** option chain.

No stored state is involved: the levels ride along inside the reply, so a reply
to any chart works regardless of age — fired daily charts *and* on-demand charts,
any ticker, any prior day. This is the case the user asked for: `chart TSLA`
today, reply `trade` to it, even though TSLA never fired in a scan.

### Path 2 — Bare `trade SYM` (convenience)
The user types `trade SYM [CONF] [risk=N] [dte=N] [full]` as a normal message
(not a reply). The bot performs **one** signal evaluation and sends **both** the
chart and the trade card from that single computation. Because both outputs come
from one eval, they cannot diverge. The chart is always shown, so the card stays
tied to a visible chart.

## Chart-level recovery: caption parsing (Decision 1B)

`trade` replies recover levels by **parsing the caption of the replied-to
message**, not from any stored map. Telegram delivers the original message's
caption inside `reply_to_message`, and every chart caption already prints the
levels (`BUY V — Visa Inc · … · close 384.14 · target 401.85 · stop 373.52`).

Consequences:
- **No new committed state.** Nothing to write on chart-send, nothing to rotate,
  nothing to bloat the repo.
- **No "context not found" for real charts.** The data is in the reply itself.
- **The caption format becomes a contract.** Both caption builders — the daily
  fired line (`notify._fired_line`) and the on-demand summary
  (`bot.build_summary`) — must emit direction, close, target, and stop in a
  parseable, stable shape. A single **golden-caption test** pins the format so a
  wording change can't silently break parsing. The parser handles both caption
  shapes.

The parser extracts: symbol, bar date, direction, entry (close), target, stop.
`realized_vol` and the option chain are still fetched live (option quotes must be
current); only the signal and levels come from the caption.

## Single direction source

- The signal handed to `options.decide` already carries `direction`.
- `options.decide` surfaces `direction` on the returned plan.
- `optfmt._direction` is **removed**; `optfmt` reads `plan["direction"]` for the
  action label and kill wording.
- Net: one direction, set at the chart, used everywhere. Root cause #2 gone.

## Extended-signal handling: refuse the comparison, explain why (Decision 2B)

When the chart's levels are degenerate for its direction — bull with
`target <= entry`, or bear with `target >= entry` — the name is *extended*: there
is no upside left between price and the mechanical target, so the entire
options-vs-equity comparison (which is driven by the reward at the target) is
meaningless.

In that case the bot does **not** emit a shares-vs-options card. It replies with
a short, honest, direction-correct note, e.g.:

    V · BUY signal — but extended.
    Target 398 sits below the current 402, so there's no upside left to size.
    This is a chase. Wait for a pullback toward EMA21, or give your own target.

Rules:
- Keep the chart's real direction (a BUY signal never renders as SHORT).
- State plainly why there's no card (target below entry, or above entry for a
  short).
- Never show a fabricated "+$X at target" or a negative "you're right on -0.0%"
  move dressed up as a setup.

Anchoring to a real chart makes this rare (the chart's target was sensible when
drawn), but the refusal is mandatory: a comparison card with no real target is
exactly the convincing-but-empty output we are eliminating.

*Future (not v1):* a `trade V target=410` override lets the user supply their own
target and get a full card on an extended name.

## Vintage labeling

The card labels the levels' bar date: `follows your V chart · bar 08/25`. A reply
to a days-old chart is then transparent about how fresh its levels are. Bare
`trade SYM` shows the current bar date.

## Reply-keyword routing

`trade` becomes a reserved reply keyword alongside `go`/`pass`. Routing order for
an incoming update:
1. Is it a `go`/`pass` decision? → decisions ledger (unchanged).
2. Is it a `trade` reply or bare `trade SYM`? → this feature.
3. Otherwise a chart request (`chart SYM` / bare ticker) → unchanged.

A bare `trade` reply with no symbol and no parseable caption is the fallback case
below, not a chart request for a ticker named "TRADE" (fixes a known minor).

## Edge cases

- **Reply `trade` to a message whose caption can't be parsed** (Decision 3): a
  plain-text or non-chart message → reply
  `Can't find that chart's signal — send \`trade SYM\` and I'll pull a fresh one.`
  With caption parsing this only happens on replies to non-charts, never to real
  charts.
- **Bare `trade SYM`, no price data:** existing `No data for SYM` path.
- **Owner filter:** unchanged — only the owner's messages are processed (the
  multi-account question is a separate, deferred decision).
- **go/pass vs trade on the same reply:** a reply is parsed as a decision first;
  only if it is not `go`/`pass` is it considered a `trade`. No double-handling.
- **Exactly-once:** the shared getUpdates offset (`ledger/telegram_state.json`)
  already guarantees each update is processed once across all handlers.

## Non-goals

- No change to the signal math or the options engine — Playbook B defaults and
  exit-value pricing stay exactly as shipped.
- No change to the owner-only access posture (multi-account requests are a
  separate deferred item).
- No new committed bot state: `trade` replies parse the on-screen caption rather
  than reading a stored map.
- No `target=` override in v1 (noted as a future add-on for extended names).

## Acceptance criteria

1. Replying `trade` to the daily **BUY V** chart returns a **BUY** card (calls or
   shares) with entry/target/stop parsed from that chart's caption, never SHORT.
2. Bare `trade V` sends V's chart and a card whose direction and levels match that
   chart exactly (one signal evaluation feeds both).
3. `optfmt` contains no independent direction inference; the action label always
   equals `plan["direction"]`.
4. An extended chart (target past entry) produces no comparison card — it returns
   the direction-correct "extended, no upside to size" note, never an
   inverted-direction card.
5. A `trade` reply to a message whose caption isn't parseable returns the clear
   fallback message, not a "TRADE" chart.
6. The full suite stays green; new tests cover paths 1 and 2, the single-direction
   guarantee, the extended refusal, and the no-caption fallback.
7. A golden-caption test pins the exact caption format that the `trade`-reply
   parser depends on, for both the fired-line and on-demand-summary builders.
