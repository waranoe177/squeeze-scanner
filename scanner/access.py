"""Pure authorization layer for the shared bot: who may use it, who is the
owner (may log go/pass), and a per-user rate limit. No I/O — unit-testable.

Chat IDs are compared as strings everywhere, matching bot._from_owner's
str(chat) != str(allowed) convention (Telegram ids arrive as ints or strs)."""

import time


def parse_allowlist(raw):
    """Comma-separated chat ids -> set[str]. None/'' -> empty set. Tolerates
    surrounding whitespace and blank entries."""
    if not raw:
        return set()
    return {tok.strip() for tok in raw.split(",") if tok.strip()}


def is_owner(chat_id, owner_id) -> bool:
    """True iff chat_id is the configured owner (string-compared). No owner
    configured (owner_id None/'') -> False."""
    if owner_id is None or owner_id == "":
        return False
    return str(chat_id) == str(owner_id)


def is_allowed(chat_id, owner_id, allowlist) -> bool:
    """True iff chat_id is the owner OR present in the allowlist set."""
    return is_owner(chat_id, owner_id) or str(chat_id) in allowlist


class RateLimiter:
    """In-memory sliding-window limiter keyed by chat id. `limit` events per
    `window_seconds`. State is a dict[str, list[float]] of hit timestamps;
    single-process serve() means no locking is needed."""

    def __init__(self, limit: int = 20, window_seconds: int = 3600):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, chat_id, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        key = str(chat_id)
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True
