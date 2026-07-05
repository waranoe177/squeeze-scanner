from scanner import trackrecord


def _rec(id_="IYT-2026-01-05", status="win", msg_id=123):
    return {"id": id_, "schema_version": 1, "symbol": id_.split("-")[0],
            "direction": "bull", "signal_date": "2026-01-05",
            "signal_close": 100.0, "atr": 2.0, "ema21": 99.0,
            "conviction_score": 82.0, "telegram_msg_id": msg_id,
            "status": status, "entry": 101.0, "entry_date": "2026-01-06",
            "stop": 98.0, "target": 106.0,
            "exit_price": 106.0 if status == "win" else None,
            "exit_date": "2026-01-08" if status != "open" else None,
            "r_multiple": 1.667 if status == "win" else None}


def test_render_site_writes_all_pages(tmp_path):
    trackrecord.render_site([_rec()], tmp_path)
    for name in ("index.html", "signals.html", "methodology.html",
                 "style.css", "signals.jsonl"):
        assert (tmp_path / name).exists(), name


def test_index_shows_headline_stats(tmp_path):
    trackrecord.render_site([_rec()], tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Sqzdots Indicator" in html
    assert "100.0%" in html          # win rate: 1 of 1
    assert "1.667" in html           # avg / total R visible


def test_signals_page_lists_every_record_with_receipt_link(tmp_path):
    losing = _rec(id_="QQQ-2026-01-05", status="loss", msg_id=456)
    losing["exit_price"], losing["r_multiple"] = 98.0, -1.0
    trackrecord.render_site([_rec(), losing], tmp_path,
                            channel_username="sqzdots")
    html = (tmp_path / "signals.html").read_text(encoding="utf-8")
    assert "IYT" in html and "QQQ" in html            # losses are never hidden
    assert "https://t.me/sqzdots/123" in html          # timestamped receipt


def test_methodology_contains_disclaimer_and_trade_model(tmp_path):
    trackrecord.render_site([], tmp_path)
    html = (tmp_path / "methodology.html").read_text(encoding="utf-8")
    assert "Educational tool, not investment advice." in html
    assert "2.5" in html and "1.5" in html   # ATR multiples stated plainly


def test_open_records_shown_as_open(tmp_path):
    trackrecord.render_site([_rec(status="open", msg_id=None)], tmp_path)
    html = (tmp_path / "signals.html").read_text(encoding="utf-8")
    assert "open" in html.lower()
