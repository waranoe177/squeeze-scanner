"""Tests for the chart-caption parser: recover signal levels from rendered
caption text so a `trade` reply follows the chart it was replying to.
"""

from scanner import captionparse as cp

_ONDEMAND = (
    "🟢 BUY V · bar 2026-08-25\n"
    "score 91/100 (A+) · 7/7 lit · R:R 1.5\n"
    "close 384.14 · RSI 71\n"
    "target 401.85 / 366.45 · stop 373.52\n"
    "✅Sqz ✅RSI>50 ✅PPO≥0 ✅8>21 ✅Stack ✅MACD ✅Moxie"
)
_FIRED = (
    "🟢 BUY V — Visa Inc. · score 91/100 (A+) · bar 2026-08-25\n"
    "   close 384.14 · RSI 71\n"
    "   target 401.85 · stop 373.52 (finalize at next open)\n"
    "   ↩️ Reply to this chart: go or pass"
)
_BEAR = (
    "🔴 SELL META · bar 2026-08-25\n"
    "score 80/100 (A) · 7/7 lit · R:R 1.4\n"
    "close 200.00 · RSI 28\n"
    "target 240.00 / 180.00 · stop 210.00\n"
)


def test_parse_ondemand_bull_picks_target_up():
    r = cp.parse_caption(_ONDEMAND)
    assert r["symbol"] == "V" and r["direction"] == "bull"
    assert r["entry"] == 384.14 and r["target"] == 401.85 and r["stop"] == 373.52
    assert r["bar_date"] == "2026-08-25"


def test_parse_fired_single_target():
    r = cp.parse_caption(_FIRED)
    assert r["symbol"] == "V" and r["direction"] == "bull"
    assert r["entry"] == 384.14 and r["target"] == 401.85 and r["stop"] == 373.52


def test_parse_bear_picks_target_dn():
    r = cp.parse_caption(_BEAR)
    assert r["symbol"] == "META" and r["direction"] == "bear"
    assert r["target"] == 180.00 and r["stop"] == 210.00   # dn side for a short


def test_parse_returns_none_on_no_signal():
    assert cp.parse_caption("⚪ no signal ABC · bar 2026-08-25\nclose 10.00") is None


def test_parse_returns_none_on_plain_text():
    assert cp.parse_caption("hey how's it going") is None
