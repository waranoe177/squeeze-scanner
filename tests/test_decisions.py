"""Decision-tracking tests: parsing, matching, ingestion, reporting."""

from scanner import decisions


def _update(text, reply_to=None, date=1767625200, uid=1):  # 2026-01-05 15:00 UTC
    msg = {"message_id": 900, "date": date, "text": text, "chat": {"id": 1}}
    if reply_to is not None:
        msg["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": uid, "message": msg}


def test_parse_threaded_go():
    p = decisions.parse_decision(_update("go", reply_to=123))
    assert p == {"decision": "go", "decided_at": "2026-01-05T15:00:00+00:00",
                 "reply_to_msg_id": 123, "symbol": None}


def test_parse_pass_and_skip_alias_case_insensitive():
    assert decisions.parse_decision(_update("PASS", reply_to=5))["decision"] == "pass"
    assert decisions.parse_decision(_update("Skip", reply_to=5))["decision"] == "pass"


def test_parse_freeform_symbol():
    p = decisions.parse_decision(_update("go tsla"))
    assert p["decision"] == "go" and p["symbol"] == "TSLA"
    assert p["reply_to_msg_id"] is None


def test_parse_garbage_returns_none():
    assert decisions.parse_decision(_update("looks great!")) is None
    assert decisions.parse_decision(_update("gopher", reply_to=5)) is None
    assert decisions.parse_decision({"update_id": 2}) is None  # no message
    assert decisions.parse_decision(_update("go now then")) is None  # 3 words
