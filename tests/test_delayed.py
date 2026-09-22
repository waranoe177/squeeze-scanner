from scanner import delayed


def _results(fired):
    return {"generated_at": "2026-07-03T21:35:00+00:00", "as_of": "2026-07-03",
            "universe": 120, "fired_count": len(fired), "fired": fired,
            "watching": [], "watching_detail": []}


def _p(symbol):
    return {"symbol": symbol, "direction": "bull", "grade": "A", "close": 87.18,
            "rsi": 62.1, "ppo": 0.9, "squeeze_on": True, "moxie_w": 1.4,
            "target_up": 88.8, "target_dn": 80.77, "stop": 84.62,
            "date": "2026-07-03", "prov_target": 92.18, "prov_stop": 84.18}


def test_delayed_message_is_marked_as_yesterdays():
    msg = delayed.format_delayed(_results([_p("IYT")]))
    assert "yesterday" in msg.lower()
    assert "IYT" in msg
    assert "members" in msg.lower()   # upgrade nudge present when fired


def test_delayed_no_fire_has_no_upgrade_nudge():
    msg = delayed.format_delayed(_results([]))
    assert "yesterday" in msg.lower()
    assert "members" not in msg.lower()


def test_delayed_footer():
    msg = delayed.format_delayed(_results([]), footer="https://example.com")
    assert msg.rstrip().endswith("https://example.com")


def test_free_channel_omits_provenance():
    """Guards the coupling: format_delayed reuses format_message, so a
    provenance line added for the owner's digest must not leak to the public
    free channel."""
    from scanner import delayed
    results = {
        "generated_at": "2026-09-22T23:00:14+00:00", "as_of": "2026-09-22",
        "universe": 159, "fired_count": 0, "fired": [], "watching": [],
    }
    body = delayed.format_delayed(results)
    assert "scan 19:00:14 ET" not in body
    # "· 159 names" is the provenance format. Bare "Scanned 159 names. 0 fired."
    # is the long-standing zero-fired copy and must stay.
    assert "· 159 names" not in body
    assert "Scanned 159 names" in body


def test_free_channel_omits_stale_suppression_notice():
    """Operator diagnostics must not reach the PUBLIC channel. A marketing post
    announcing "our data source returned an old session" is not the message."""
    from scanner import delayed
    results = {
        "generated_at": "2026-09-22T23:00:14+00:00", "as_of": "2026-09-22",
        "universe": 158, "fired_count": 0, "fired": [], "watching": [],
        "stale_fired": [{"symbol": "RIVN", "date": "2026-09-18"}],
    }
    body = delayed.format_delayed(results)
    assert "suppressed" not in body
    assert "stale" not in body.lower()
