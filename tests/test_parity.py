"""TOS parity regression test.

Locks in the validation from 2026-06-29: the engine, running the user's
documented process, must reproduce what TOS showed on the 6/26 bar for the five
ground-truth tickers. Data is frozen in tests/fixtures/ so this is deterministic
and offline (no yfinance dependency).

This specifically guards against the forming-bar bug and the missing Moxie/MACD
gates that originally made IYT/XLRE false-fire.
"""

from pathlib import Path

import pandas as pd
import pytest

from scanner import signals

FIXTURES = Path(__file__).parent / "fixtures"

# Ground truth from the user's TOS, 6/26/2026 bar (strict-mirror sell rule).
# All five resolve to "none": the four bull candidates fail the Moxie/MACD-green
# gate; QQQ is not a clean short (Moxie still above zero).
EXPECTED = {
    "RSP": "none",
    "DIA": "none",
    "IYT": "none",
    "XLRE": "none",
    "QQQ": "none",
}
VALIDATION_BAR = "2026-06-26"


def _load(symbol: str) -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / f"{symbol}.csv", index_col=0, parse_dates=True)
    return df


@pytest.mark.parametrize("symbol,expected", EXPECTED.items())
def test_engine_matches_tos_on_validation_bar(symbol, expected):
    out = signals.analyze(_load(symbol))
    row = out.loc[VALIDATION_BAR]
    direction = (
        "bull" if row["scanner_bull"] else "bear" if row["scanner_bear"] else "none"
    )
    assert direction == expected, (
        f"{symbol} on {VALIDATION_BAR}: engine={direction}, TOS={expected}"
    )


def test_validation_bar_close_matches_fixture():
    # Sanity: confirm we're evaluating the same candle the user read (6/26 close).
    expected_close = {"RSP": 210.31, "DIA": 517.75, "IYT": 87.18, "XLRE": 45.24, "QQQ": 706.52}
    for symbol, close in expected_close.items():
        df = _load(symbol)
        assert df.loc[VALIDATION_BAR, "close"] == pytest.approx(close, abs=0.01)
