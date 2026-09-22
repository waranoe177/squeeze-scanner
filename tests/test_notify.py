"""Tests for Telegram message formatting (pure). The actual HTTP send is a thin
wrapper exercised live, not unit-tested here.
"""

from scanner import notify


def _results(fired, watching=None):
    return {
        "generated_at": "2026-06-29T21:30:00+00:00",
        "as_of": "2026-06-26",
        "universe": 34,
        "fired_count": len(fired),
        "fired": fired,
        "watching": watching or [],
    }


def _p(symbol, direction="bull"):
    return {
        "symbol": symbol, "direction": direction, "grade": "A++",
        "close": 87.18, "rsi": 62.1, "ppo": 0.9, "squeeze_on": True, "moxie_w": 1.4,
        "target_up": 88.80, "target_dn": 80.77, "stop": 84.62, "date": "2026-06-26",
    }


def test_message_lists_fired_tickers_with_levels():
    msg = notify.format_message(_results([_p("IYT", "bull")], watching=["QQQ", "SPY"]))
    assert "IYT" in msg
    assert "2026-06-26" in msg          # the bar date
    assert "84.62" in msg or "84.6" in msg  # stop level present
    assert "QQQ" in msg                 # watching list


def test_message_marks_direction():
    bull = notify.format_message(_results([_p("IYT", "bull")]))
    bear = notify.format_message(_results([_p("MMM", "bear")]))
    assert "BUY" in bull.upper()
    assert "SELL" in bear.upper() or "SHORT" in bear.upper()


def test_message_handles_no_fires():
    msg = notify.format_message(_results([], watching=["QQQ"]))
    assert "0 fired" in msg           # quiet-day header
    assert "QQQ" in msg               # still shows what's coiled


def test_message_escapes_html_special_chars():
    p = _p("A&B", "bull")
    msg = notify.format_message(_results([p]))
    assert "&amp;" in msg  # & escaped for HTML parse mode


def test_fired_line_prefers_provisional_levels():
    p = _p("IYT", "bull")
    p["prov_target"], p["prov_stop"] = 105.0, 97.0
    msg = notify.format_message(_results([p]))
    assert "105.00" in msg and "97.00" in msg
    assert "next open" in msg.lower()


def test_fired_line_includes_parseable_bar_date():
    p = {"symbol": "V", "direction": "bull", "close": 384.14, "rsi": 71.0,
         "score": 91, "conviction_grade": "A+", "date": "2026-08-25",
         "prov_target": 401.85, "prov_stop": 373.52}
    line = notify._fired_line(p, cta=True, name="Visa Inc.")
    assert "bar 2026-08-25" in line
    assert "BUY" in line and "V" in line
    assert "close 384.14" in line
    assert "target 401.85" in line and "stop 373.52" in line


def test_no_fire_message_shows_building_squeezes():
    results = _results([], watching=["QQQ", "SPY"])
    results["watching_detail"] = [
        {"symbol": "QQQ", "lit": 6, "lean": "bull"},
        {"symbol": "SPY", "lit": 4, "lean": "bear"},
    ]
    msg = notify.format_message(results)
    assert "0 fired" in msg
    assert "QQQ" in msg
    assert "6/7" in msg and "bull" in msg


def test_footer_appended_when_provided():
    msg = notify.format_message(_results([]), footer="Track record: https://example.com")
    assert msg.rstrip().endswith("Track record: https://example.com")


def test_fired_line_cta_only_when_asked():
    p = _p("TSLA", "bear")
    assert "Reply to this chart" in notify._fired_line(p, cta=True)
    assert "Reply to this chart" not in notify._fired_line(p)


def test_format_message_never_contains_cta():
    msg = notify.format_message(_results([_p("TSLA", "bear")]))
    assert "Reply to this chart" not in msg


# ---- multi-recipient broadcast --------------------------------------------

def test_parse_chat_ids_splits_dedups_and_drops_blanks():
    assert notify.parse_chat_ids("111,222") == ["111", "222"]
    assert notify.parse_chat_ids("  111 , 222 ,, 333 ") == ["111", "222", "333"]
    assert notify.parse_chat_ids("111,111,222") == ["111", "222"]  # dedup, order kept
    assert notify.parse_chat_ids("") == []
    assert notify.parse_chat_ids(None) == []


def test_broadcast_sends_charts_and_message_to_each(tmp_path):
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "IYT.png").write_bytes(b"png")  # only IYT has a chart on disk
    fired = [_p("IYT", "bull"), _p("NODATA", "bull")]
    photos, msgs = [], []

    res = notify.broadcast(
        "T", ["111", "222"], fired, charts, "SUMMARY",
        send_photo=lambda tok, cid, path, caption="": photos.append((cid, path)),
        send_message=lambda tok, cid, text: msgs.append((cid, text)),
    )

    assert res == {"111": True, "222": True}
    assert msgs == [("111", "SUMMARY"), ("222", "SUMMARY")]
    # each recipient got IYT's photo; NODATA (no file) is skipped
    assert photos == [("111", str(charts / "IYT.png")),
                      ("222", str(charts / "IYT.png"))]


def test_fired_line_shows_company_name():
    line = notify._fired_line(_p("NVDA", "bull"), name="NVIDIA Corporation")
    assert "NVDA" in line and "NVIDIA Corporation" in line


def test_fired_line_ladder_bull_all_checked():
    line = notify._fired_line(_p("NVDA", "bull"), show_ladder=True)
    assert line.count("✅") == 7                 # all seven conditions checked
    assert "Squeeze" in line and "RSI&gt;50" in line and "Moxie" in line


def test_fired_line_ladder_bear_uses_mirror_labels():
    line = notify._fired_line(_p("MMM", "bear"), show_ladder=True)
    assert line.count("✅") == 7
    assert "RSI&lt;50" in line                   # bear mirror, not RSI>50


def test_format_message_stays_lean_no_ladder():
    # the checklist belongs on the chart caption, not the text summary
    msg = notify.format_message(_results([_p("IYT", "bull")]))
    assert "✅" not in msg


def test_broadcast_includes_company_name_in_caption(tmp_path):
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "NVDA.png").write_bytes(b"png")
    caps = []
    notify.broadcast(
        "T", ["1"], [_p("NVDA", "bull")], charts, "S",
        names={"NVDA": "NVIDIA Corporation"},
        send_photo=lambda tok, cid, path, caption="": caps.append(caption),
        send_message=lambda *a, **k: None,
    )
    assert "NVIDIA Corporation" in caps[0] and "✅" in caps[0]


def test_broadcast_is_best_effort_one_bad_recipient_does_not_stop_others(tmp_path):
    def flaky_message(tok, cid, text):
        if cid == "111":
            raise RuntimeError("chat not found — hasn't started the bot")

    res = notify.broadcast(
        "T", ["111", "222"], [], tmp_path, "SUMMARY",
        send_photo=lambda *a, **k: None,
        send_message=flaky_message,
    )
    assert res == {"111": False, "222": True}  # never raises; 222 still delivered


# --- provenance line (issue #2) -----------------------------------------
# "Did this run, and when?" must be answerable from the message alone, without
# opening GitHub. The ET conversion is the load-bearing part: generated_at is
# stored UTC, but the routine is anchored to ET and must not drift with DST.

def _results_at(generated_at, universe=159, fired=None):
    return {
        "generated_at": generated_at,
        "as_of": "2026-09-22",
        "universe": universe,
        "fired_count": len(fired or []),
        "fired": fired or [],
        "watching": [],
    }


def test_provenance_converts_utc_to_et_in_summer():
    # 23:00:14Z on 2026-09-22 is EDT (UTC-4) -> 19:00:14 ET
    line = notify.provenance_line(_results_at("2026-09-22T23:00:14+00:00"), run_number="69")
    assert "scan 19:00:14 ET" in line


def test_provenance_converts_utc_to_et_in_winter():
    # 00:00:14Z on 2026-01-16 is EST (UTC-5) -> 19:00:14 ET on 2026-01-15
    line = notify.provenance_line(_results_at("2026-01-16T00:00:14+00:00"), run_number="69")
    assert "scan 19:00:14 ET" in line


def test_provenance_accepts_z_suffix():
    line = notify.provenance_line(_results_at("2026-09-22T23:00:14Z"), run_number="69")
    assert "scan 19:00:14 ET" in line


def test_provenance_has_bar_names_and_run():
    line = notify.provenance_line(_results_at("2026-09-22T23:00:14+00:00"), run_number="69")
    assert "bar 2026-09-22" in line
    assert "159 names" in line
    assert "run #69" in line


def test_provenance_omits_run_when_absent():
    line = notify.provenance_line(_results_at("2026-09-22T23:00:14+00:00"))
    assert "run #" not in line
    assert "159 names" in line


def test_provenance_survives_missing_generated_at():
    line = notify.provenance_line(_results_at(None))
    assert "159 names" in line
    assert "scan" not in line


def test_message_carries_provenance_when_zero_fired():
    msg = notify.format_message(_results_at("2026-09-22T23:00:14+00:00"), run_number="69")
    assert "scan 19:00:14 ET" in msg
    assert "run #69" in msg


def test_message_carries_provenance_when_fired():
    """The universe count used to appear ONLY in the zero-fired branch."""
    msg = notify.format_message(
        _results_at("2026-09-22T23:00:14+00:00", fired=[_p("NVDA")]), run_number="69")
    assert "scan 19:00:14 ET" in msg
    assert "159 names" in msg


def test_format_message_still_works_without_run_number():
    """Backward compatibility: weekrun.py calls format_message(results, title=...)."""
    msg = notify.format_message(_results_at("2026-09-22T23:00:14+00:00"),
                                title="Sqzdots WEEKLY Scan")
    assert "Sqzdots WEEKLY Scan" in msg
    assert "159 names" in msg


def test_provenance_shows_zero_universe():
    """0 names means the watchlist failed to load -- the case you most need to see.
    Guards against a truthiness check silently dropping the field."""
    line = notify.provenance_line(_results_at("2026-09-22T23:00:14+00:00", universe=0))
    assert "0 names" in line


def test_provenance_survives_a_broken_timezone_database():
    """format_message was a total function before the provenance line. ZoneInfo
    raising on the critical path would crash the scan BEFORE any send."""
    r = _results_at("2026-09-22T23:00:14+00:00")
    line = notify.provenance_line(r, run_number="69", tz="Mars/Olympus")
    assert "159 names" in line      # degrades, does not raise
    assert "scan" not in line


def test_provenance_can_be_suppressed():
    """delayed.py posts to the PUBLIC free channel the next morning; last
    night's clock time reads as a bug there, and universe size is not public."""
    msg = notify.format_message(_results_at("2026-09-22T23:00:14+00:00"),
                                run_number="69", provenance=False)
    assert "scan 19:00:14 ET" not in msg
    assert "run #69" not in msg


def test_message_announces_suppressed_stale_signals():
    """Silence must never be ambiguous: if signals were withheld, say so."""
    r = _results_at("2026-09-22T23:00:14+00:00")
    r["stale_fired"] = [{"symbol": "RIVN", "date": "2026-09-18"},
                        {"symbol": "BA", "date": "2026-09-18"}]
    msg = notify.format_message(r)
    assert "2 signal(s) suppressed" in msg
    assert "2026-09-18" in msg


def test_message_says_nothing_when_no_signals_were_suppressed():
    msg = notify.format_message(_results_at("2026-09-22T23:00:14+00:00"))
    assert "suppressed" not in msg


def test_suppression_list_says_how_many_it_truncated():
    r = _results_at("2026-09-22T23:00:14+00:00")
    r["stale_fired"] = [{"symbol": f"S{i}", "date": "2026-09-18"} for i in range(12)]
    msg = notify.format_message(r)
    assert "12 signal(s) suppressed" in msg
    assert "+4 more" in msg
