from scanner import recap


def _rec(id_, signal_date, exit_date=None, r=None, status="open"):
    return {"id": id_, "schema_version": 1, "symbol": id_.split("-")[0],
            "direction": "bull", "signal_date": signal_date,
            "signal_close": 100.0, "atr": 2.0, "ema21": 99.0,
            "conviction_score": 80.0, "telegram_msg_id": None,
            "status": status, "entry": 101.0, "entry_date": signal_date,
            "stop": 98.0, "target": 106.0,
            "exit_price": 106.0 if status == "win" else None,
            "exit_date": exit_date, "r_multiple": r}


def test_week_slice_filters_by_dates():
    records = [
        _rec("A-1", "2026-06-22", "2026-06-25", 1.5, "win"),    # in week
        _rec("B-1", "2026-06-24"),                               # fired in week
        _rec("C-1", "2026-06-10", "2026-06-12", -1.0, "loss"),  # old
    ]
    wk = recap.week_slice(records, week_ending="2026-06-28")
    assert [r["id"] for r in wk["fired"]] == ["A-1", "B-1"]
    assert [r["id"] for r in wk["closed"]] == ["A-1"]


def test_render_card_writes_png(tmp_path):
    records = [_rec("A-1", "2026-06-22", "2026-06-25", 1.5, "win")]
    out = tmp_path / "recap.png"
    path = recap.render_card(records, "2026-06-28", out)
    assert out.exists() and out.stat().st_size > 5000
    assert str(out) == path


def test_render_card_handles_empty_week(tmp_path):
    out = tmp_path / "recap.png"
    recap.render_card([], "2026-06-28", out)
    assert out.exists()
