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
