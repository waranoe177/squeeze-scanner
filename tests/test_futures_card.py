"""Futures trade card: contract-sized plan (points × multiplier), not shares,
with the entry price explicit so risk/reward anchor to something."""

import re
from datetime import date

import pytest

from scanner import futures_spec, optfmt, options


def test_spec_lookup_case_insensitive_and_equity_is_none():
    assert futures_spec.spec("es=f")["name"] == "E-mini S&P 500"
    assert futures_spec.is_future("ES=F") is True
    assert futures_spec.spec("AAPL") is None
    assert futures_spec.is_future("AAPL") is False


def _es_plan(direction="bull", entry=7712.50, stop=7560.0, target=7920.0):
    sig = {"symbol": "ES=F", "direction": direction, "entry": entry,
           "stop": stop, "target": target, "realized_vol": 0.14, "score": 82}
    return options.decide(sig, {"expiries": []}, asof=date(2026, 9, 19))


def test_decide_returns_contract_sized_futures_plan():
    plan = _es_plan()
    assert plan["futures"] is True
    assert plan["mult"] == 50
    # 152.5 pts × $50 = $7,625 risk ; T1 207.5 pts × $50 = $10,375 reward
    assert plan["risk_dollars"] == 7625.0
    assert plan["reward_dollars"] == 10375.0
    assert round(plan["rr"], 2) == 1.36
    # T2 = 2.0/2.5 of T1's distance: 207.5 × 0.8 = 166 pts × $50 = $8,300
    assert plan["target2"] == pytest.approx(7712.50 + 166.0)
    assert plan["reward2_dollars"] == pytest.approx(8300.0)
    assert round(plan["rr2"], 2) == 1.09
    assert plan["notional"] == 385625.0
    # micro (MES, $5/pt) scales risk to a tenth
    assert plan["micro_symbol"] == "MES=F"
    assert plan["micro_risk"] == 762.5


def test_format_futures_card_shows_entry_stop_two_targets_and_micro():
    card = re.sub("<[^>]+>", "", optfmt.format_trade(_es_plan()))
    assert "ENTER     7712.50" in card
    assert "STOP      7560.00" in card
    assert "risk   −$7,625" in card
    assert "TARGET 1  7920.00" in card
    assert "+$10,375" in card
    assert "2.5×ATR · R:R 1.4" in card
    assert "TARGET 2  7878.50" in card
    assert "+$8,300" in card
    assert "2.0×ATR · R:R 1.1" in card
    assert "1 pt = $50" in card
    assert "MES=F micro" in card
    assert "est. margin" in card


def test_format_futures_card_shorts_flip_action_and_arrows():
    # bear: entry 7712, target below (7500), stop above (7860)
    card = re.sub("<[^>]+>", "", optfmt.format_trade(
        _es_plan(direction="bear", target=7500.0, stop=7860.0)))
    assert "· SELL ·" in card
    assert "STOP      7860.00   ↑" in card    # stop above entry for a short
    assert "TARGET 1  7500.00   ↓" in card    # target below entry for a short
    assert "TARGET 2  7542.50   ↓" in card    # nearer target, still below entry


def test_silver_micro_is_one_fifth_not_one_tenth():
    sig = {"symbol": "SI=F", "direction": "bull", "entry": 66.79,
           "stop": 64.0, "target": 72.0, "realized_vol": 0.2, "score": 70}
    plan = options.decide(sig, {"expiries": []}, asof=date(2026, 9, 19))
    assert plan["mult"] == 5000 and plan["micro_mult"] == 1000  # SIL = 1/5
    assert plan["risk_dollars"] == pytest.approx(abs(66.79 - 64.0) * 5000)


def test_full_card_has_no_options_audit_for_futures():
    # format_trade_full must not append the equity/option AUDIT block
    full = optfmt.format_trade_full(_es_plan())
    assert "AUDIT" not in full
