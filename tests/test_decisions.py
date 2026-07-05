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


def _rec(symbol="TSLA", signal_date="2026-01-05", entry_date="2026-01-06",
         msg_id=123, status="open"):
    return {"id": f"{symbol}-{signal_date}", "schema_version": 1, "symbol": symbol,
            "direction": "bull", "signal_date": signal_date, "signal_close": 100.0,
            "atr": 2.0, "ema21": 99.0, "conviction_score": 80.0,
            "telegram_msg_id": msg_id, "status": status, "entry": 101.0,
            "entry_date": entry_date, "stop": 98.0, "target": 106.0,
            "exit_price": None, "exit_date": None, "r_multiple": None}


def _parsed(decision="go", decided_at="2026-01-05T23:00:00+00:00",
            reply_to=123, symbol=None):
    return {"decision": decision, "decided_at": decided_at,
            "reply_to_msg_id": reply_to, "symbol": symbol}


def test_apply_matches_by_msg_id_and_sets_fields():
    recs = [_rec(msg_id=123), _rec(symbol="NVDA", msg_id=124)]
    decisions.apply_decisions(recs, [_parsed()])
    assert recs[0]["decision"] == "go"
    assert recs[0]["decided_at"] == "2026-01-05T23:00:00+00:00"
    assert recs[0]["decision_late"] is False   # decided evening before entry
    assert "decision" not in recs[1]


def test_apply_symbol_fallback_picks_latest_undecided():
    older = _rec(signal_date="2026-01-02", msg_id=50)
    newer = _rec(signal_date="2026-01-05", msg_id=51)
    decisions.apply_decisions([older, newer],
                              [_parsed(reply_to=None, symbol="TSLA")])
    assert "decision" not in older and newer["decision"] == "go"


def test_apply_write_once_first_decision_stands():
    rec = _rec()
    decisions.apply_decisions([rec], [_parsed(decision="go")])
    decisions.apply_decisions([rec], [_parsed(
        decision="pass", decided_at="2026-01-06T01:00:00+00:00")])
    assert rec["decision"] == "go"
    assert rec["decided_at"] == "2026-01-05T23:00:00+00:00"


def test_apply_late_boundary():
    # entry 2026-01-06; 09:30 America/New_York (EST) == 14:30 UTC
    early = _rec()
    late = _rec(symbol="NVDA", msg_id=124)
    decisions.apply_decisions(
        [early, late],
        [_parsed(decided_at="2026-01-06T14:29:00+00:00", reply_to=123),
         _parsed(decided_at="2026-01-06T14:31:00+00:00", reply_to=124)])
    assert early["decision_late"] is False
    assert late["decision_late"] is True


def test_apply_pending_entry_never_late():
    rec = _rec(entry_date=None, status="pending_entry")
    decisions.apply_decisions([rec], [_parsed(
        decided_at="2026-03-01T00:00:00+00:00")])
    assert rec["decision_late"] is False


def test_apply_no_match_is_skipped():
    rec = _rec()
    decisions.apply_decisions([rec], [_parsed(reply_to=999),
                                      _parsed(reply_to=None, symbol="ZZZZ")])
    assert "decision" not in rec


from scanner import ledger


def test_state_roundtrip_and_bootstrap(tmp_path):
    path = tmp_path / "state.json"
    assert decisions.load_state(path) == {"offset": 0}
    decisions.save_state(path, {"offset": 42})
    assert decisions.load_state(path) == {"offset": 42}


def test_ingest_applies_and_advances_offset(tmp_path, monkeypatch):
    lpath, spath = tmp_path / "signals.jsonl", tmp_path / "state.json"
    ledger.save(lpath, [_rec(msg_id=123)])

    calls = []
    def fake_fetch(token, offset):
        calls.append(offset)
        if offset == 0:
            return [_update("go", reply_to=123, uid=7)], 8
        return [], offset
    monkeypatch.setattr(decisions, "fetch_updates", fake_fetch)

    n = decisions.ingest(lpath, spath, token="T")
    assert n == 1
    assert ledger.load(lpath)[0]["decision"] == "go"
    assert decisions.load_state(spath) == {"offset": 8}

    # second run: nothing new, nothing re-applied
    assert decisions.ingest(lpath, spath, token="T") == 0
    assert calls == [0, 8]


def test_ingest_same_update_twice_is_harmless(tmp_path, monkeypatch):
    # crash-between-saves simulation: offset not advanced, update replayed
    lpath, spath = tmp_path / "signals.jsonl", tmp_path / "state.json"
    ledger.save(lpath, [_rec(msg_id=123)])
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda t, o: ([_update("go", reply_to=123, uid=7)], 8))
    decisions.ingest(lpath, spath, token="T")
    n = decisions.ingest(lpath, spath, token="T")  # replays same update
    assert n == 0                                   # write-once absorbed it
    assert ledger.load(lpath)[0]["decision"] == "go"


def test_ingest_without_token_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert decisions.ingest(tmp_path / "l.jsonl", tmp_path / "s.json") == 0
