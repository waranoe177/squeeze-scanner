"""Scan runner: run the engine across a watchlist and assemble the daily result.

`scan_frames` turns {symbol: ohlc} into a list of signal payloads. `build_results`
splits them into fired vs watching (coiled but not fired) and ranks the fires.
The output dict is what gets written to results.json for the dashboard and what
the Telegram notifier formats.
"""

from datetime import datetime, timezone

import pandas as pd

from scanner import signals


def scan_frames(frames: dict[str, pd.DataFrame]) -> list[dict]:
    """Run latest_signal for each symbol. Skips frames too short to analyze."""
    payloads = []
    for symbol, df in frames.items():
        if df is None or len(df) < 205:  # need ~200 bars for SMA200
            continue
        payloads.append(signals.latest_signal(df, symbol=symbol))
    return payloads


def rank_fired(payloads: list[dict]) -> list[dict]:
    """Rank fired signals: bulls first, then by RSI distance from 50 (strength)."""
    def key(p):
        direction_rank = 0 if p["direction"] == "bull" else 1
        strength = -abs(p["rsi"] - 50)  # larger distance = stronger -> sorts first
        return (direction_rank, strength)

    return sorted(payloads, key=key)


def build_results(payloads: list[dict], as_of: str) -> dict:
    """Split payloads into fired / watching and assemble the results document."""
    fired = rank_fired([p for p in payloads if p["direction"] != "none"])
    watching = [
        p["symbol"]
        for p in payloads
        if p["direction"] == "none" and p.get("squeeze_on")
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of,
        "universe": len(payloads),
        "fired_count": len(fired),
        "fired": fired,
        "watching": watching,
    }
