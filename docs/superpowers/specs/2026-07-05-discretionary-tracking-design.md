# Sqzdots — Discretionary Decision Tracking
**Date:** 2026-07-05
**Status:** Approved
**Project:** Sqzdots (personal tool) — measure the owner's judgment layer

---

## Overview

Phase 0 research (2026-07-05) proved the mechanical signal carries no entry-timing
alpha; whatever edge exists lives in the owner's discretionary layer. This
extension measures that layer: every Telegram alert asks for a GO/PASS reply,
the reply is recorded in the signal ledger next to the mechanical outcome, and
the recap card + dashboard report the difference between the signals the owner
took and the signals the owner skipped.

The one number this feature exists to produce: **selection alpha = avg R of GO
signals − avg R of PASS signals**, measured over the owner's real decisions.

---

## Goals

- Capture a GO/PASS decision per fired signal with a two-tap Telegram gesture
- Record decisions immutably beside mechanical outcomes in the existing ledger
- Report GO-vs-PASS performance automatically (recap card + dashboard)
- Keep the honest-timing distinction: decisions made after the entry open are
  tracked but flagged, so hindsight can't pollute the clean sample

## Non-Goals

- Reactions (👍/👎) or inline buttons as inputs (may be added later)
- Decision reminders/nags, position sizing, portfolio tracking
- Any persistent server or bot listener — batch ingestion only
- Changing alert content beyond one call-to-action line per chart caption

---

## User Interaction

Each fired ticker already arrives as its own chart photo. Its caption gains a
final line:

```
↩️ Reply to this chart: go or pass
```

**Primary gesture:** long-press the specific chart photo → Reply → type `go` or
`pass` → send. The reply's `reply_to_message.message_id` equals the
`telegram_msg_id` stored in that signal's ledger record — exact match, no ticker
typing, unambiguous even when several signals fire the same day.

**Fallback:** a plain (unthreaded) message `go TSLA` / `pass TSLA` matches the
most recent record for that symbol that has no decision yet.

**Accepted tokens** (case-insensitive): `go`; `pass` or `skip`. Anything else is
ignored and logged — never guessed.

**Semantics:** the decision is always "would I take this trade?" — `go` on a
SELL alert means "I would short this."

**Decision states:** `go`, `pass`, or absent (= *undecided*, its own reporting
bucket, excluded from selection-alpha math).

**Write-once:** the first decision for a record stands; later replies to the
same signal are ignored (logged). No revising after the fact.

---

## Data Model

Additive, optional, write-once fields on the existing record in
`ledger/signals.jsonl` (schema stays v1; absent fields = undecided):

| Field | Type | Meaning |
|---|---|---|
| `decision` | `"go"` \| `"pass"` | The owner's call |
| `decided_at` | ISO-8601 UTC | Telegram reply timestamp (decision time, not ingestion time) |
| `decision_late` | bool | true when `decided_at` ≥ 09:30 ET on `entry_date` — the owner could already see the entry behaving |

Outcome fields remain immutable per the existing ledger rules. Decision fields
may be written onto a record in any status (a decision arriving after close is
recorded with `decision_late: true`).

New state file `ledger/telegram_state.json` (committed):
`{"offset": <last processed update_id + 1>}` — guarantees each Telegram update
is processed exactly once across runs.

---

## Architecture

### `scanner/decisions.py` (new module)

- `fetch_updates(token, offset) -> (updates, new_offset)` — Telegram
  `getUpdates` wrapper (long-poll timeout 0; message updates only).
- `parse_decision(update) -> {"decision", "decided_at", "reply_to_msg_id"|None, "symbol"|None} | None`
  — extracts go/pass token; returns None (logged) for anything unparseable.
- `apply_decisions(records, parsed) -> list[dict]` — matching order:
  1. `reply_to_msg_id` == a record's `telegram_msg_id` (exact)
  2. else `symbol` → most recent record for that symbol without a decision
  Skips (with log) when: no match, record already decided. Computes
  `decision_late` from `decided_at` vs the entry morning (09:30 America/New_York
  on `entry_date`; records still `pending_entry` are never late).
- `ingest(ledger_path, state_path) -> int` — glue: load state → fetch → parse →
  apply → save ledger + state; returns count applied. CLI:
  `python -m scanner.decisions [--ledger ...] [--state ...]`.

### `scanner/notify.py` (one-line change)

`_fired_line` caption gains the final call-to-action line shown above.

### `scanner/ledger.py` — `stats()` extension

Adds a `"decisions"` sub-dict computed over closed records:

```
{"go":        {"n", "wins", "win_rate", "avg_r", "total_r"},
 "pass":      {...same...},
 "undecided": {...same...},
 "late_n":    int,          # decided late (reported, excluded from clean split)
 "selection_alpha": go.avg_r - pass.avg_r | None}   # clean (non-late) only
```

### `dashboard/app.py` — Decision Review section

- Table: every decided signal — date, symbol, direction, decision, outcome, R,
  late flag
- Two cumulative-R curves on one chart: GOs vs PASSes (clean decisions only)
- Headline: selection alpha + t-stat (shown once clean n ≥ 30 per bucket)

### `scanner/recap.py` — "Your decisions" block

One block on the weekly card, e.g.
`Your calls — GO: 12 · 42% win · +0.31 avgR | PASS: 18 · −0.12 avgR | edge +0.43R`.
Omitted entirely while no decisions exist yet.

### `.github/workflows/decisions.yml` (new)

- Cron `0 6,18 * * *` (twice daily, every day — Telegram discards unfetched
  updates after ~24h; weekend replies must not be lost) + `workflow_dispatch`.
- Steps: checkout → setup python → `python -m scanner.decisions` → commit
  `ledger/` if changed → failure alert to `TELEGRAM_ADMIN_CHAT_ID` (same curl
  pattern as scan.yml). Runtime ~30s.
- The daily scan workflow also runs `python -m scanner.decisions` as its first
  step, so weekday decisions land before each new scan.
- Concurrency guard: both workflows share
  `concurrency: group: sqzdots-ledger` (`cancel-in-progress: false`) so ledger
  commits never race.

### Data flow

```
evening: alert photos arrive (each captioned with the go/pass CTA)
owner: long-press TSLA chart → Reply → "go"
06:00/18:00 UTC (and before each scan):
  getUpdates(offset) → parse → match via telegram_msg_id (or symbol fallback)
  → write decision/decided_at/decision_late onto the record (write-once)
  → save ledger + offset state → commit
Sunday: recap card renders the "Your decisions" block
anytime: dashboard Decision Review shows table + GO/PASS curves + alpha
```

---

## Error Handling

- `getUpdates` failure → non-zero exit → workflow failure alert to admin chat.
- Unparseable reply / unmatched reply / already-decided record → log line,
  update still consumed (offset advances; one bad message can't wedge the queue).
- Ambiguous symbol fallback (no undecided record for that symbol) → logged, ignored.
- State file missing → start from offset 0; write-once + exactly-once offset
  semantics make reprocessing harmless.

---

## Testing

Same TDD pattern as the existing suite (`tests/test_decisions.py` + extensions):

- `parse_decision`: threaded reply, `go SYM` freeform, `skip` alias, case
  variants, garbage → None
- `apply_decisions`: msg-id match, symbol fallback, no-match skip, write-once
  (second decision ignored), late-flag boundary (decided_at just before / after
  09:30 ET on entry_date), pending_entry never late
- `ingest`: offset persistence (same update never applied twice), state-file
  bootstrap
- `stats`: decision splits + selection alpha against a fixture ledger; late
  decisions excluded from the clean split
- recap block renders with and without decisions; dashboard section smoke test

---

## Success Criteria

- Replying `go` to a chart photo results (within one ingest cycle) in that
  record carrying `decision: "go"` with the reply's timestamp
- A second reply to the same chart changes nothing
- Weekend replies are never lost (twice-daily ingest)
- Recap card and dashboard show GO/PASS splits and selection alpha once
  decisions exist; undecided and late decisions are visibly separate
- After ~50–100 clean decisions the owner can read a statistically framed
  answer to "does my judgment add alpha?"
