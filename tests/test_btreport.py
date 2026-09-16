"""Tests for the Markdown backtest-findings report (uploadable to a Claude
Project). format_report is pure; main() writes the .md file."""

import json
from datetime import date

from scanner import btreport


def _doc(trades=None):
    trades = trades if trades is not None else [
        {"symbol": "AAA", "signal_date": "2024-01-05", "option_return": 0.5,
         "option_outcome": "win", "entry_premium": 2.0},
        {"symbol": "AAA", "signal_date": "2024-02-01", "option_return": -0.3,
         "option_outcome": "loss", "entry_premium": 2.0},
        {"symbol": "BBB", "signal_date": "2024-03-10", "option_return": 0.1,
         "option_outcome": "win", "entry_premium": 2.0},
    ]
    return {
        "summary": {
            "exit": "hold", "hold_days": 3,
            "equity": {"n": 3, "win_rate": 0.5, "expectancy_r": 0.2,
                       "max_losing_streak": 2, "max_drawdown_r": 1.5},
            "option": {"n": 3, "win_rate": 2 / 3, "expectancy_pct": 0.1,
                       "median_pct": 0.1, "total_cash": 1234.0},
        },
        "trades": trades,
    }


def test_report_is_findings_forward_and_dated():
    md = btreport.format_report(_doc(), generated=date(2026, 9, 16))
    assert md.startswith("# Sqzdots Backtest — Findings")
    assert "Generated: 2026-09-16" in md
    assert "3-day hold" in md
    assert "66.7%" in md           # option win rate 2/3
    assert "Win rate" in md


def test_report_states_universe_and_date_range():
    md = btreport.format_report(_doc(), generated=date(2026, 9, 16))
    assert "2 symbols" in md
    assert "2024-01-05" in md and "2024-03-10" in md


def test_report_computes_tail_from_trades():
    md = btreport.format_report(_doc(), generated=date(2026, 9, 16))
    assert "-30.0%" in md   # worst / avg-loss single trade
    assert "50.0%" in md    # best single trade


def test_report_bakes_in_the_exit_reasoning_and_caveats():
    md = btreport.format_report(_doc(), generated=date(2026, 9, 16))
    assert "no stop" in md.lower()
    assert "realized vol" in md.lower()      # RV-as-IV caveat
    assert "position sizing" in md.lower()   # risk framing


def test_report_handles_empty_trades():
    doc = _doc(trades=[])
    doc["summary"]["option"] = {"n": 0, "win_rate": None, "expectancy_pct": None,
                                "median_pct": None, "total_cash": 0.0}
    md = btreport.format_report(doc, generated=date(2026, 9, 16))
    assert isinstance(md, str)
    assert "No priced option trades" in md


def test_main_writes_md(tmp_path):
    p = tmp_path / "bt.json"
    p.write_text(json.dumps(_doc()))
    out = tmp_path / "rep.md"
    btreport.main(["--in", str(p), "--out", str(out)])
    assert out.exists()
    assert "# Sqzdots Backtest — Findings" in out.read_text(encoding="utf-8")
