"""Tests for the chart-caption parser: recover signal levels from rendered
caption text so a `trade` reply follows the chart it was replying to.
"""

import html as _html
import re
from pathlib import Path

import pandas as pd

from scanner import bot, captionparse as cp, notify, signals

_TAG = re.compile(r"<[^>]+>")


def _render(text: str) -> str:
    """Strip HTML tags and decode entities, mirroring what a Telegram client
    shows the user. `parse_caption` reads rendered text, not raw HTML source
    (see the module docstring / global caption-format contract)."""
    return _html.unescape(_TAG.sub("", text))

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


# ---- golden round-trip: parser vs. the real caption builders --------------
# Locks acceptance #7: the parser and the caption builders agree. Both
# builders emit HTML (Telegram parse_mode); parse_caption reads the RENDERED
# text (tags stripped, entities decoded), so tests render before parsing —
# exactly what a Telegram client does before the user (or the trade command)
# ever sees the caption.

def test_roundtrip_fired_line():
    p = {"symbol": "V", "direction": "bull", "close": 384.14, "rsi": 71.0,
         "score": 91, "conviction_grade": "A+", "date": "2026-08-25",
         "prov_target": 401.85, "prov_stop": 373.52}
    r = cp.parse_caption(_render(notify._fired_line(p, cta=True, name="Visa Inc.")))
    assert r["symbol"] == "V" and r["direction"] == "bull"
    assert r["entry"] == 384.14 and r["target"] == 401.85 and r["stop"] == 373.52
    assert r["bar_date"] == "2026-08-25"


def test_roundtrip_build_summary():
    """bot.build_summary's real caption, parsed back. Reuses the IYT fixture
    already relied on in tests/test_signals.py (iyt_frame) — its last bar is
    a known, deterministic bull fire, so no hand-rolled synthetic frame is
    needed to get a clean BULL signal end to end."""
    fixtures = Path(__file__).parent / "fixtures"
    df = pd.read_csv(fixtures / "IYT.csv", index_col=0, parse_dates=True)
    expected = signals.latest_signal(df, symbol="IYT")
    assert expected["direction"] == "bull"  # sanity: fixture is a bull fire

    caption = bot.build_summary("IYT", df)
    r = cp.parse_caption(_render(caption))
    assert r is not None, f"build_summary emitted an unparseable caption:\n{caption}"
    assert r["symbol"] == "IYT" and r["direction"] == "bull"
    assert r["entry"] == round(expected["close"], 2)
    assert r["target"] == round(expected["target_up"], 2)
    assert r["stop"] == round(expected["stop"], 2)
