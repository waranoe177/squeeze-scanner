"""Discretionary decision tracking: GO/PASS replies -> ledger fields.

The owner replies "go" or "pass" to a signal's chart photo in Telegram (or
sends "go SYM" unthreaded). A twice-daily batch job pulls getUpdates, matches
each reply to its ledger record, and writes three write-once fields:
decision / decided_at / decision_late. No server; exactly-once via a committed
offset state file. Everything here is deliberately conservative: anything
unparseable or unmatchable is logged and skipped, never guessed.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TOKENS = {"go": "go", "pass": "pass", "skip": "pass"}
_FREEFORM = re.compile(r"^(go|pass|skip)\s+([A-Za-z][A-Za-z.\-]{0,9})$", re.IGNORECASE)
_MARKET_TZ = ZoneInfo("America/New_York")
DEFAULT_STATE_PATH = "ledger/telegram_state.json"


def parse_decision(update: dict) -> dict | None:
    """Extract a decision from one Telegram update, or None if it isn't one."""
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    if not text or "date" not in msg:
        return None
    decided_at = datetime.fromtimestamp(msg["date"], tz=timezone.utc).isoformat()
    reply_to = (msg.get("reply_to_message") or {}).get("message_id")

    token = TOKENS.get(text.lower())
    if token:
        return {"decision": token, "decided_at": decided_at,
                "reply_to_msg_id": reply_to, "symbol": None}
    m = _FREEFORM.match(text)
    if m:
        return {"decision": TOKENS[m.group(1).lower()], "decided_at": decided_at,
                "reply_to_msg_id": reply_to, "symbol": m.group(2).upper()}
    return None
