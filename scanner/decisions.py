"""Discretionary decision tracking: GO/PASS replies -> ledger fields.

The owner replies "go" or "pass" to a signal's chart photo in Telegram (or
sends "go SYM" unthreaded). A twice-daily batch job pulls getUpdates, matches
each reply to its ledger record, and writes three write-once fields:
decision / decided_at / decision_late. No server; exactly-once via a committed
offset state file. Everything here is deliberately conservative: anything
unparseable or unmatchable is logged and skipped, never guessed.
"""

import argparse
import json
import os
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


def _is_late(decided_at: str, entry_date: str | None) -> bool:
    """Late = decided at/after 09:30 America/New_York on the entry date.
    No entry date yet (pending_entry) -> decided before the entry, never late."""
    if not entry_date:
        return False
    decided = datetime.fromisoformat(decided_at)
    y, m, d = (int(x) for x in entry_date.split("-"))
    entry_open = datetime(y, m, d, 9, 30, tzinfo=_MARKET_TZ)
    return decided >= entry_open


def apply_decisions(records: list[dict], parsed: list[dict]) -> list[dict]:
    """Write each parsed decision onto its matching record (write-once)."""
    by_msg = {r["telegram_msg_id"]: r for r in records
              if r.get("telegram_msg_id") is not None}
    for p in parsed:
        rec = None
        if p["reply_to_msg_id"] is not None:
            rec = by_msg.get(p["reply_to_msg_id"])
        if rec is None and p["symbol"]:
            candidates = [r for r in records
                          if r["symbol"] == p["symbol"] and not r.get("decision")]
            rec = max(candidates, key=lambda r: r["signal_date"], default=None)
        if rec is None:
            print(f"  [decisions] no match for {p} — skipped")
            continue
        if rec.get("decision"):
            print(f"  [decisions] {rec['id']} already decided — reply ignored")
            continue
        rec["decision"] = p["decision"]
        rec["decided_at"] = p["decided_at"]
        rec["decision_late"] = _is_late(p["decided_at"], rec.get("entry_date"))
        print(f"  [decisions] {rec['id']} -> {p['decision']}"
              f"{' (late)' if rec['decision_late'] else ''}")
    return records


def fetch_updates(token: str, offset: int, timeout: int = 0) -> tuple[list[dict], int]:
    """One getUpdates batch. Returns (updates, next_offset).

    `timeout` is Telegram's long-poll seconds: 0 = return immediately (cron
    mode), >0 = hold the connection until an update arrives (local --serve).
    """
    import requests

    resp = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"offset": offset, "timeout": timeout,
                "allowed_updates": json.dumps(["message"])},
        timeout=timeout + 10,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not resp.ok or not body.get("ok", False):
        desc = body.get("description", (resp.text or "")[:300])
        raise RuntimeError(f"Telegram getUpdates {resp.status_code}: {desc}")
    updates = body.get("result", [])
    next_offset = max((u["update_id"] for u in updates), default=offset - 1) + 1 \
        if updates else offset
    return updates, next_offset


def load_state(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"offset": 0}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state) + "\n", encoding="utf-8")


def ingest(ledger_path=None, state_path=DEFAULT_STATE_PATH, token=None) -> int:
    """Pull replies, apply decisions, persist. Returns applied count.

    Ledger is saved BEFORE the offset state: a crash between the two replays
    updates on the next run, which write-once absorbs harmlessly.
    """
    from scanner import ledger

    ledger_path = ledger_path or ledger.DEFAULT_PATH
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[decisions] no TELEGRAM_BOT_TOKEN — skipping ingest")
        return 0

    state = load_state(state_path)
    updates, next_offset = fetch_updates(token, state["offset"])
    records = ledger.load(ledger_path)
    before = sum(1 for r in records if r.get("decision"))

    allowed_chat = os.environ.get("TELEGRAM_CHAT_ID")

    def _from_owner(u):
        if not allowed_chat:
            return True  # local/dev: no filter configured
        chat = ((u.get("message") or {}).get("chat") or {}).get("id")
        if str(chat) != str(allowed_chat):
            print(f"  [decisions] update from foreign chat {chat} ignored")
            return False
        return True

    parsed = [p for p in (parse_decision(u) for u in updates if _from_owner(u)) if p]
    apply_decisions(records, parsed)
    applied = sum(1 for r in records if r.get("decision")) - before

    ledger.save(ledger_path, records)
    save_state(state_path, {"offset": next_offset})
    print(f"[decisions] {len(updates)} update(s), {len(parsed)} decision reply(ies), "
          f"{applied} applied")
    return applied


def decision_table(records: list[dict]) -> list[dict]:
    """Rows for the dashboard Decision Review table (decided records only)."""
    rows = [
        {"signal_date": r["signal_date"], "symbol": r["symbol"],
         "direction": r["direction"], "decision": r["decision"],
         "status": r["status"], "r_multiple": r.get("r_multiple"),
         "late": bool(r.get("decision_late")), "exit_date": r.get("exit_date")}
        for r in records if r.get("decision")
    ]
    return sorted(rows, key=lambda r: r["signal_date"], reverse=True)


def main(argv=None) -> int:
    from scanner import ledger

    ap = argparse.ArgumentParser(description="Ingest GO/PASS Telegram replies")
    ap.add_argument("--ledger", default=ledger.DEFAULT_PATH)
    ap.add_argument("--state", default=DEFAULT_STATE_PATH)
    args = ap.parse_args(argv)
    return ingest(args.ledger, args.state)


if __name__ == "__main__":
    main()
