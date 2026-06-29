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
    assert "no" in msg.lower()  # "no signals" / "nothing fired"
    assert "QQQ" in msg         # still shows what's coiled


def test_message_escapes_html_special_chars():
    p = _p("A&B", "bull")
    msg = notify.format_message(_results([p]))
    assert "&amp;" in msg  # & escaped for HTML parse mode
