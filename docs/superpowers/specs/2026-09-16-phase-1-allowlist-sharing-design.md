# Phase 1: Allowlist Sharing — share the bot with friends & family

**Date:** 2026-09-16
**Status:** draft — awaiting user review, then plan
**Builds on:** Phase 0 always-on Fly.io bot (`scanner/bot.py serve`, `scanner/ghsync.py`, `scanner/decisions.py`)
**Defers:** Phase 2 (real/reliable data feed for exact TOS indicator parity) — separate spec, not required for sharing

## Problem

The always-on Fly bot is owner-only: `_from_owner()` ([scanner/bot.py:164](../../../scanner/bot.py#L164))
drops every update whose chat ID is not `TELEGRAM_CHAT_ID`, and every reply is
hard-coded to that single owner `chat_id`. To share the tool with a small,
trusted group (friends & family), the bot must (a) authorize a fixed set of
users, (b) reply to each requester in *their own* chat, and (c) fence off the
owner-only capability (logging go/pass decisions to the ledger) and add basic
guardrails so sharing is safe and cheap.

This is a **watchlist / alert / educational** tool, not a proven trading system
(the honest backtests and event study found no directional or volatility edge
over baseline). Sharing must therefore lead with an educational, not-financial-
advice disclaimer that sets correct expectations.

## Design principle

**Config, not code, admits a user. The owner's behavior is byte-for-byte
unchanged when the allowlist is empty.** Authorization is a pure, testable layer
(`scanner/access.py`); the bot module only wires it into the poll loop and routes
each reply to the update's own chat. No data-plane change: signals.jsonl stays
single-writer, decisions.jsonl stays owner-written, no new git commits per user.

## Roles

- **Owner** — the `TELEGRAM_CHAT_ID` chat. Unrestricted: any ticker for
  `chart`/`trade`, logs go/pass to the ledger, exempt from rate limits and the
  universe restriction. Exactly today's behavior.
- **Allowed (shared) user** — a chat ID in `TELEGRAM_ALLOWLIST`. May request
  `chart SYM` (any ticker) and `trade SYM` (scanned universe only). May NOT log
  go/pass. Subject to the per-user rate limit and the first-use disclaimer.
- **Unknown chat** — ignored silently (as foreign chats are today), but logged to
  stdout with the chat ID so the owner can choose to add them.

## Configuration

- **New secret `TELEGRAM_ALLOWLIST`** — comma-separated Telegram chat IDs, e.g.
  `TELEGRAM_ALLOWLIST=111,222,333`. Whitespace tolerated; blanks ignored. The
  owner's `TELEGRAM_CHAT_ID` is *always* implicitly allowed even if absent here.
  Unset/empty ⇒ owner-only (today's behavior) — a safe default, **not** a
  startup refusal.
- Lives in a **Fly secret**, NOT a committed file: the repo is public, and chat
  IDs identify real people. Set with `fly secrets set TELEGRAM_ALLOWLIST=...`;
  adding/removing a user is a secret update + machine restart, no code deploy.
- The existing `TELEGRAM_ALERT_CHAT_IDS` (daily fired-alert broadcast) is
  **unchanged and independent**. Allowlist governs *on-demand requests* only;
  the daily broadcast list is a separate opt-in the owner curates.

## Design

### 1. `scanner/access.py` — pure authorization layer (new)

Kept separate from the I/O-heavy bot module so it is unit-testable without
network or Telegram.

```python
def parse_allowlist(raw: str | None) -> set[str]:
    """Comma-separated chat IDs -> set of str ids. None/'' -> empty set.
    Tolerates whitespace and blank entries."""

def is_owner(chat_id, owner_id) -> bool:
    """True iff chat_id == owner_id (string-compared, as _from_owner does)."""

def is_allowed(chat_id, owner_id, allowlist: set[str]) -> bool:
    """True iff chat_id is the owner OR in the allowlist. Owner is always allowed."""

class RateLimiter:
    """In-memory sliding-window limiter, keyed by chat_id.
    limit N events per window_seconds. Owner is never passed in (exempt)."""
    def __init__(self, limit: int = 20, window_seconds: int = 3600): ...
    def allow(self, chat_id: str, now: float | None = None) -> bool:
        """Record an attempt; return True if under the cap, False if over.
        `now` injectable for deterministic tests."""
```

Notes:
- Chat IDs compared as **strings** throughout, matching `_from_owner`'s
  `str(chat) != str(allowed_chat)`.
- `RateLimiter` prunes timestamps older than the window on each call; state is a
  `dict[str, list[float]]`. Single `serve()` process ⇒ no locking needed.

### 2. `_update_chat_id(update)` helper in bot.py

One place that extracts the requester's chat ID from an update, mirroring
`_from_owner`'s path:

```python
def _update_chat_id(update: dict) -> str | None:
    return _safe_str(((update.get("message") or {}).get("chat") or {}).get("id"))
```

Returns `None` for malformed updates (which are then skipped).

### 3. `poll_once` — authorize + route per requester (modify)

Today: `owned = [u for u in updates if _from_owner(u, chat_id)]`, then every
handler is called with the env `chat_id` (owner). Change to:

- Read `owner_id = chat_id` (the env `TELEGRAM_CHAT_ID`) and
  `allowlist = access.parse_allowlist(os.environ.get("TELEGRAM_ALLOWLIST"))`.
- For each update:
  1. `cid = _update_chat_id(update)`; skip if `None`.
  2. If not `access.is_allowed(cid, owner_id, allowlist)` → log
     `"update from unlisted chat {cid} ignored"` and skip.
  3. **Rate limit** (non-owner only): if not `rate.allow(cid)` → reply to `cid`
     "⚠️ you're sending requests too fast — try again in a bit." and skip.
  4. **First-use disclaimer** (non-owner only): if `cid` not in
     `seen_disclaimer` → send the disclaimer text to `cid`, add to the set, and
     continue to serve the request in the same poll.
  5. Dispatch, passing **`cid`** as the reply target:
     - **decision (go/pass)**: only if `access.is_owner(cid, owner_id)` →
       `ghsync.append_decision` (today's logic, unchanged). If a non-owner sends
       go/pass → reply to `cid` "Only the owner can log decisions." and skip.
     - **trade**: `handle_trade(t, cid, token, is_owner=<bool>)`.
     - **chart**: `handle_command(sym, cid, token)`.
- The offset-hold-on-persistence-failure logic is **unchanged** — it still keys
  off failed decision appends (owner-only decisions), so nothing about
  multi-user changes the ledger safety guarantee.

`poll_once` gains two injected collaborators for testability, defaulting to
process-lifetime singletons created in `serve()`: `rate` (a `RateLimiter`) and
`seen_disclaimer` (a `set`). Passing them in keeps `poll_once` a pure function of
its inputs in tests.

### 4. `handle_trade` — universe restriction for non-owners (modify)

Add an `is_owner: bool = True` parameter (default True keeps every existing
caller and test unchanged). When `is_owner` is False and the requested symbol is
**not** in the persisted scan universe (`out/results.json`, the same snapshot the
bare-`trade` path already anchors to), reply:

> `{SYM} isn't in the tracked universe. Try `chart {SYM}` for a chart.`

and return without producing trade instructions. `chart SYM` is unrestricted for
everyone. The owner (`is_owner=True`) keeps unrestricted `trade`.

The universe = the set of symbols the scan knows about. `out/results.json`
already lists `fired[]`; the full tracked universe is the watchlist. Decision:
**restrict to the watchlist symbols** (broader than just `fired[]`, so a shared
user can ask about any tracked name, not only today's fired ones). Read the
watchlist the same way `run.py` does (`data.load_watchlist`). If the watchlist
file isn't present in the bot's checkout, fall back to `results.json` symbols.

### 5. First-use disclaimer text

Short, matches the track-record site's tone (educational / not advice):

> 📊 Sqzdots — this is an educational watchlist & timing-aid tool. It flags a
> technical setup; it is **not financial advice** and has **no proven edge**.
> Do your own research and manage your own risk. Charts on request: send a
> ticker (e.g. `NVDA`).

### 6. `serve()` startup (modify)

- Construct the process-lifetime `RateLimiter` and `seen_disclaimer` set, pass to
  `poll_once`.
- Startup validation unchanged: still refuse without `TELEGRAM_BOT_TOKEN` /
  `GITHUB_TOKEN` / `TELEGRAM_CHAT_ID`. `TELEGRAM_ALLOWLIST` unset is fine
  (owner-only). Log the parsed allowlist size at startup:
  `"[bot] serving owner + N allowlisted user(s)"`.

## Per-user state (explicit decision)

Rate-limit windows and the seen-disclaimer set live **in memory** in the single
`serve()` process. Trade-off, accepted for F&F scale: a Fly restart/redeploy
resets rate windows and re-sends the disclaimer once per active user. No new
infra, no `/data` writes. If the re-send ever becomes annoying, a follow-up can
persist just the seen-disclaimer set to the existing `/data` volume — explicitly
out of scope now (YAGNI).

## Out of scope (Phase 1)

- Self-service registration / approval flow (owner edits the secret by hand).
- Persisting rate-limit or disclaimer state across restarts.
- Any allowlisted user writing to the ledger (go/pass stays owner-only).
- Phase 2 data-feed / indicator-parity work.
- Changes to the daily scan, `signals.jsonl`, `decisions.jsonl`, or `ghsync.py`.

## Files

- **Create** `scanner/access.py` — `parse_allowlist`, `is_owner`, `is_allowed`,
  `RateLimiter`.
- **Create** `tests/test_access.py` — allowlist parsing (blanks/whitespace),
  owner-always-allowed, allowed vs unlisted, rate limiter under/over cap with
  injected clock and window pruning.
- **Modify** `scanner/bot.py` — `_update_chat_id`; `poll_once` authorize + route
  per requester + rate limit + disclaimer + owner-only decisions; `handle_trade`
  gains `is_owner` + universe check; `serve()` constructs state + logs allowlist
  size.
- **Modify** `tests/test_bot.py` — reply routes to the requester's chat; unlisted
  chat ignored; non-owner go/pass declined (no `append_decision` call); non-owner
  off-universe `trade` declined; non-owner in-universe `trade` served; disclaimer
  sent once per new chat; owner path unchanged.
- **Modify** `deploy/README.md` — `fly secrets set TELEGRAM_ALLOWLIST=...`, how a
  friend finds their Telegram chat ID, and how to add/remove a user.

## Test scenarios (acceptance)

1. Owner texts `chart NVDA` → chart to owner chat (unchanged).
2. Allowlisted friend texts `chart NVDA` → chart to **their** chat in ~2s.
3. Allowlisted friend's **first** message → disclaimer to their chat once, then
   the request is served; second message → no disclaimer.
4. Allowlisted friend texts `trade <off-universe>` → universe nudge, no
   instructions.
5. Allowlisted friend texts `trade <in-universe>` → normal trade card.
6. Allowlisted friend texts `go` → "only the owner can log decisions"; no ledger
   append.
7. Unlisted chat texts anything → ignored (logged), no reply.
8. Friend exceeds the rate cap → throttle message, no chart.
9. `TELEGRAM_ALLOWLIST` unset → identical to today's owner-only behavior.

## Global constraints

- Existing suite is green; TDD throughout. No new runtime dependencies.
- Chat IDs compared as strings everywhere (parity with `_from_owner`).
- Owner path unchanged when the allowlist is empty (regression guard).
- Public repo: no chat IDs committed to the repo; allowlist is a Fly secret only.
