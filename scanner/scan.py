"""Scan runner: run the engine across a watchlist and assemble the daily result.

`scan_frames` turns {symbol: ohlc} into a list of signal payloads. `build_results`
splits them into fired vs watching (coiled but not fired) and ranks the fires.
The output dict is what gets written to results.json for the dashboard and what
the Telegram notifier formats.
"""

from datetime import datetime, timezone

import pandas as pd

from scanner import score, signals


def scan_frames(frames: dict[str, pd.DataFrame], htf_rule: str = "W") -> list[dict]:
    """Run latest_signal + conviction score for each symbol. Skips short frames.
    `htf_rule` = "W" for the daily scan, "ME" for a weekly-timeframe scan (picks
    the Moxie band); all other indicators use the frames as given."""
    payloads = []
    for symbol, df in frames.items():
        if df is None or len(df) < 205:  # need ~200 bars for SMA200
            continue
        payload = signals.latest_signal(df, symbol=symbol, htf_rule=htf_rule)
        sc = score.conviction(df, symbol=symbol, htf_rule=htf_rule)
        payload["score"] = sc["score"]
        payload["conviction_grade"] = sc["grade"]
        payload["score_parts"] = sc
        payloads.append(payload)
    return payloads


def rank_fired(payloads: list[dict]) -> list[dict]:
    """Rank fired signals: bulls first, full 'A++' above early 'A', then by
    conviction score (desc)."""
    def key(p):
        direction_rank = 0 if p["direction"] == "bull" else 1
        grade_rank = 0 if p.get("grade") == "A++" else 1
        return (direction_rank, grade_rank, -p.get("score", 0))

    return sorted(payloads, key=key)


def build_results(payloads: list[dict], as_of: str) -> dict:
    """Split payloads into fired / watching and assemble the results document."""
    fired = rank_fired([p for p in payloads if p["direction"] != "none"])
    watch_payloads = [
        p for p in payloads if p["direction"] == "none" and p.get("squeeze_on")
    ]
    watching_detail = sorted(
        (
            {
                "symbol": p["symbol"],
                "lit": max(p.get("lit_bull", 0), p.get("lit_bear", 0)),
                "lean": "bull" if p.get("lit_bull", 0) >= p.get("lit_bear", 0) else "bear",
            }
            for p in watch_payloads
        ),
        key=lambda d: -d["lit"],
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of,
        "universe": len(payloads),
        "fired_count": len(fired),
        "fired": fired,
        "watching": [d["symbol"] for d in watching_detail],
        "watching_detail": watching_detail,
    }
