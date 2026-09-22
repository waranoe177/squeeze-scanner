"""Scan runner: run the engine across a watchlist and assemble the daily result.

`scan_frames` turns {symbol: ohlc} into a list of signal payloads. `build_results`
splits them into fired vs watching (coiled but not fired) and ranks the fires.
The output dict is what gets written to results.json for the dashboard and what
the Telegram notifier formats.
"""

from datetime import datetime, timezone

import pandas as pd

from scanner import futures_spec, score, signals


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


def session_date(payloads: list[dict]) -> str:
    """The trading session this scan should be evaluating.

    MAX over EQUITIES, deliberately excluding futures. Verified in 2y of live
    data: 2025-01-09 (national day of mourning) and 2025-07-04 have CME futures
    bars and NO equity bars, and both are weekdays the cron runs. Letting 8
    futures set the session would push every one of ~150 equity signals into
    `stale_fired` -- an empty alert and an empty ledger on a real session.

    MAX and not the modal date: the failure this guards against (2026-09-21) was
    a SUBSET of symbols falling behind. A modal session would follow a stale
    majority down and the guard would never fire.

    Falls back to all payloads when the universe has no equities.
    """
    dated = [(p.get("symbol") or "", str(p["date"])) for p in payloads if p.get("date")]
    equity = [d for sym, d in dated if not futures_spec.is_future(sym)]
    pool = equity or [d for _, d in dated]
    return max(pool, default="")


def build_results(payloads: list[dict], as_of: str) -> dict:
    """Split payloads into fired / watching and assemble the results document.

    STALE GUARD (2026-09-21 incident): a signal whose bar is not the session is
    suppressed into `stale_fired` instead of `fired`. yfinance can return NaN
    OHLC for a subset of symbols; normalize's dropna then silently discards
    those rows and the symbol's "latest" bar falls back days. Because `as_of` is
    the MAX bar date across the universe, the symbols that did update made the
    header look current while the alert carried old signals -- invisible by
    construction. Their ATR-derived entry/stop/target come from the wrong
    session, so the risk printed on the card is not the real risk. Suppressed
    signals are kept (with their true date) for diagnosis and announced in the
    message; they must never reach the alert or the ledger.
    """
    # `< as_of`, not `!=`: BEHIND the session means bad data, AHEAD does not.
    # Futures trade on days equities are closed (2025-01-09, 2025-07-04), so an
    # `=F` bar dated later than the equity session is legitimate, not stale.
    # NOTE: local name is `fired_signals`, not `signals` -- this module imports a
    # `signals` module at the top and shadowing it here is a latent UnboundLocalError.
    fired_signals = [p for p in payloads if p["direction"] != "none"]
    stale_fired = [p for p in fired_signals if str(p.get("date") or "") < as_of]
    fresh = [p for p in fired_signals if str(p.get("date") or "") >= as_of]
    fired = rank_fired(fresh)
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
        "stale_fired": stale_fired,
        "stale_count": len(stale_fired),
        "watching": [d["symbol"] for d in watching_detail],
        "watching_detail": watching_detail,
    }
