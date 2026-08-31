"""Parse a chart caption (the rendered text Telegram delivers in
reply_to_message.caption) back into the signal levels printed on the chart.

The trade command uses this so a `trade` reply inherits EXACTLY the direction and
levels the user is looking at. Returns None when there is no BUY/SELL signal.
"""

import html as _html
import re

_TAG = re.compile(r"<[^>]+>")


def render_html(text: str) -> str:
    """Turn raw caption HTML (from _fired_line/build_summary) into the rendered
    text Telegram delivers in reply_to_message.caption — tags stripped, entities
    decoded. parse_caption operates on rendered text."""
    if not text:
        return ""
    return _html.unescape(_TAG.sub("", text))


_DIR = re.compile(r"\b(BUY|SELL)\s+([A-Z][A-Z0-9.\-=^]{0,11})\b")
_CLOSE = re.compile(r"close\s+([0-9]+(?:\.[0-9]+)?)")
# target may be single ("target 401.85 · stop ...") or dual ("target 401.85 / 366.45 · stop ...")
_TARGET = re.compile(r"target\s+([0-9]+(?:\.[0-9]+)?)(?:\s*/\s*([0-9]+(?:\.[0-9]+)?))?")
_STOP = re.compile(r"stop\s+([0-9]+(?:\.[0-9]+)?)")
_BAR = re.compile(r"bar\s+(\d{4}-\d{2}-\d{2})")
_SCORE = re.compile(r"score\s+([0-9]+(?:\.[0-9]+)?)\s*/\s*100")


def parse_caption(text: str) -> dict | None:
    if not text:
        return None
    md = _DIR.search(text)
    if not md:
        return None
    direction = "bull" if md.group(1) == "BUY" else "bear"
    symbol = md.group(2).upper()

    mc, mt, ms = _CLOSE.search(text), _TARGET.search(text), _STOP.search(text)
    if not (mc and mt and ms):
        return None
    entry = float(mc.group(1))
    stop = float(ms.group(1))
    up = float(mt.group(1))
    dn = float(mt.group(2)) if mt.group(2) else None
    # Dual-target captions list up then dn; pick the side that matches direction.
    if dn is not None:
        target = up if direction == "bull" else dn
    else:
        target = up
    mb = _BAR.search(text)
    ms_score = _SCORE.search(text)
    return {"symbol": symbol, "direction": direction, "entry": entry,
            "target": target, "stop": stop,
            "bar_date": mb.group(1) if mb else None,
            "score": float(ms_score.group(1)) if ms_score else None}
