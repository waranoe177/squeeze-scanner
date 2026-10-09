"""End-to-end dry-run smoke test: fixture frames in, ledger + site out.
Network and Telegram are monkeypatched away."""

import json

import pandas as pd

from scanner import data, run


def _fixture_frames():
    frames = {}
    for sym in ("IYT", "QQQ", "RSP", "DIA", "XLRE"):
        df = pd.read_csv(f"tests/fixtures/{sym}.csv", index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]
        frames[sym] = df[["open", "high", "low", "close", "volume"]]
    return frames


def test_dry_run_writes_ledger_and_site(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "fetch_daily", lambda *a, **k: _fixture_frames())
    ledger_path = tmp_path / "signals.jsonl"
    site_dir = tmp_path / "site"
    out_dir = tmp_path / "out"

    results = run.main([
        "--dry-run", "--no-charts",
        "--watchlist", "watchlist.csv",          # symbols overridden by monkeypatch
        "--out", str(out_dir),
        "--ledger", str(ledger_path),
        "--site", str(site_dir),
    ])

    assert (out_dir / "results.json").exists()
    assert ledger_path.exists()                   # ledger saved even with 0 fires
    assert (site_dir / "index.html").exists()     # site regenerated every run
    # fired payloads (if any) carry provisional levels
    for p in results["fired"]:
        assert "prov_target" in p and "prov_stop" in p


def test_no_site_flag_skips_site(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "fetch_daily", lambda *a, **k: _fixture_frames())
    site_dir = tmp_path / "site"
    run.main(["--dry-run", "--no-charts", "--no-site",
              "--out", str(tmp_path / "out"),
              "--ledger", str(tmp_path / "signals.jsonl"),
              "--site", str(site_dir)])
    assert not site_dir.exists()


def test_send_failure_exits_nonzero_after_persist(tmp_path, monkeypatch):
    import pytest

    from scanner import notify

    monkeypatch.setattr(data, "fetch_daily", lambda *a, **k: _fixture_frames())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    def boom(*a, **k):
        raise RuntimeError("net down")

    monkeypatch.setattr(notify, "send_message", boom)
    monkeypatch.setattr(notify, "send_photo", boom)
    ledger_path = tmp_path / "signals.jsonl"
    site_dir = tmp_path / "site"
    with pytest.raises(SystemExit) as ei:
        run.main(["--no-charts", "--out", str(tmp_path / "out"),
                  "--ledger", str(ledger_path), "--site", str(site_dir)])
    assert ei.value.code == 1
    assert ledger_path.exists()               # persisted despite the failure
    assert (site_dir / "index.html").exists()  # site rendered despite the failure


def test_owner_ids_included_in_daily_broadcast(tmp_path, monkeypatch):
    """A second owner (TELEGRAM_OWNER_IDS) receives the daily alert too — its
    chat id is folded into the broadcast recipients."""
    from scanner import notify

    monkeypatch.setattr(data, "fetch_daily", lambda *a, **k: _fixture_frames())
    monkeypatch.setattr(data, "company_name", lambda *a, **k: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("TELEGRAM_OWNER_IDS", "444")
    monkeypatch.setattr(notify, "send_message", lambda *a, **k: {"result": {"message_id": 1}})
    monkeypatch.setattr(notify, "send_photo", lambda *a, **k: {"result": {"message_id": 1}})
    captured = {}
    monkeypatch.setattr(
        notify, "broadcast",
        lambda token, chat_ids, *a, **k: captured.update(ids=list(chat_ids)) or {})
    run.main(["--no-charts", "--no-site", "--out", str(tmp_path / "out"),
              "--ledger", str(tmp_path / "l.jsonl")])
    assert "444" in captured["ids"]
    assert "1" not in captured["ids"]   # primary owner isn't double-sent


def test_fold_decisions_applies_to_records(tmp_path):
    rec = {"id": "COST-2026-09-10", "symbol": "COST", "signal_date": "2026-09-10",
           "telegram_msg_id": 42, "status": "open", "entry_date": None}
    dpath = tmp_path / "decisions.jsonl"
    dpath.write_text(json.dumps({"decision": "go", "decided_at": "2026-09-11T00:00:00+00:00",
                                 "reply_to_msg_id": 42, "symbol": None}) + "\n")
    run._fold_decisions([rec], str(dpath))
    assert rec["decision"] == "go"


def test_fold_decisions_noop_when_file_absent(tmp_path):
    rec = {"id": "X", "symbol": "X", "signal_date": "2026-09-10", "telegram_msg_id": 1,
           "status": "open"}
    run._fold_decisions([rec], str(tmp_path / "missing.jsonl"))
    assert "decision" not in rec


# ---- multi-timeframe table (spec 2026-10-09-mtf-table-design.md §6-§7) ------

from pathlib import Path  # noqa: E402


def _mtf_row():
    from scanner import mtf
    b1, b2, e = mtf.Cell("B", 1, True), mtf.Cell("B", 2, False), mtf.Cell(None, 0, False)
    return mtf.Row("PM", (b1, b2, e, e))


def _wire_telegram(monkeypatch, calls, extras="777", photo_fail_chats=()):
    from scanner import notify
    monkeypatch.setattr(data, "fetch_daily", lambda *a, **k: _fixture_frames())
    monkeypatch.setattr(data, "company_name", lambda *a, **k: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_IDS", extras)
    monkeypatch.delenv("TELEGRAM_OWNER_IDS", raising=False)

    def send_message(token, chat_id, text):
        calls.append(("msg", chat_id, text))
        return {"result": {"message_id": 1}}

    def send_photo(token, chat_id, path, caption=""):
        if chat_id in photo_fail_chats:
            raise RuntimeError("bot was blocked")
        calls.append(("photo", chat_id, Path(path).name))
        return {"result": {"message_id": 1}}

    monkeypatch.setattr(notify, "send_message", send_message)
    monkeypatch.setattr(notify, "send_photo", send_photo)
    monkeypatch.setattr(notify, "broadcast",
                        lambda token, chat_ids, *a, **k:
                        calls.append(("broadcast", tuple(chat_ids), None)) or {})


def _run(tmp_path, *extra):
    return run.main(["--no-charts", "--no-site", "--out", str(tmp_path / "out"),
                     "--ledger", str(tmp_path / "l.jsonl"), *extra])


def test_mtf_table_sent_after_summary_in_every_chat(tmp_path, monkeypatch):
    from scanner import mtf
    calls = []
    _wire_telegram(monkeypatch, calls)
    monkeypatch.setattr(mtf, "build_table", lambda frames, as_of: [_mtf_row()])
    _run(tmp_path)
    primary = [c for c in calls if c[1] == "1"]
    assert primary[-2][0] == "msg"                          # the daily summary
    assert primary[-1] == ("photo", "1", "mtf_table.png")   # then the table
    i = calls.index(("broadcast", ("777",), None))
    assert calls[i + 1] == ("photo", "777", "mtf_table.png")


def test_mtf_quiet_day_sends_the_text_line(tmp_path, monkeypatch):
    from scanner import mtf, mtf_render
    calls = []
    _wire_telegram(monkeypatch, calls)
    monkeypatch.setattr(mtf, "build_table", lambda frames, as_of: [])
    _run(tmp_path)
    assert ("msg", "1", mtf_render.QUIET) in calls
    assert ("msg", "777", mtf_render.QUIET) in calls
    assert not any(c[0] == "photo" for c in calls)


def test_mtf_failure_alerts_primary_only_and_keeps_exit_status(tmp_path, monkeypatch):
    from scanner import mtf
    calls = []
    _wire_telegram(monkeypatch, calls)

    def boom(frames, as_of):
        raise RuntimeError("yahoo <down> & out")

    monkeypatch.setattr(mtf, "build_table", boom)
    results = _run(tmp_path)                                 # no SystemExit
    assert "fired" in results
    assert ("msg", "1", "⚠️ MTF table failed: RuntimeError: yahoo &lt;down&gt; &amp; out") in calls
    assert not any(c[0] == "msg" and c[1] == "777" for c in calls)
    assert sum(c[0] == "msg" and c[1] == "1" for c in calls) == 2   # summary + warning


def test_one_extra_chat_failing_does_not_stop_the_others(tmp_path, monkeypatch):
    from scanner import mtf
    calls = []
    _wire_telegram(monkeypatch, calls, extras="777,888", photo_fail_chats=("777",))
    monkeypatch.setattr(mtf, "build_table", lambda frames, as_of: [_mtf_row()])
    _run(tmp_path)                                           # no SystemExit
    assert ("photo", "888", "mtf_table.png") in calls
    assert ("photo", "1", "mtf_table.png") in calls


def test_mtf_record_written_on_dry_run(tmp_path, monkeypatch):
    import json
    from scanner import mtf
    monkeypatch.setattr(data, "fetch_daily", lambda *a, **k: _fixture_frames())
    monkeypatch.setattr(mtf, "build_table", lambda frames, as_of: [_mtf_row()])
    out = tmp_path / "out"
    run.main(["--dry-run", "--no-charts", "--no-site", "--out", str(out),
              "--ledger", str(tmp_path / "l.jsonl")])
    rec = json.loads((out / "mtf_table.json").read_text(encoding="utf-8"))
    assert rec["rows"][0]["symbol"] == "PM"
    assert (out / "mtf_table.png").exists()


def test_mtf_fetch_is_unadjusted_5y(tmp_path, monkeypatch):
    seen = []

    def fake_fetch(symbols, **kw):
        seen.append(kw)
        return _fixture_frames()

    monkeypatch.setattr(data, "fetch_daily", fake_fetch)
    run.main(["--dry-run", "--no-charts", "--no-site", "--out", str(tmp_path / "out"),
              "--ledger", str(tmp_path / "l.jsonl")])
    assert {"period": "5y", "adjust": False} in seen
