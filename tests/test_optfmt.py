# tests/test_optfmt.py
from datetime import date

from scanner import optfmt


def _shares_win_plan():
    return {"symbol": "NVDA", "options_available": True, "winner": "equity",
            "spot": 123.45, "target": 130.0, "stop": 118.0, "move_pct": 5.3,
            "exit_date": date(2026, 8, 29), "p": 0.65,
            "shares": 154, "equity_stop_loss": 839.3, "equity_target_reward": 1008.7,
            "capital": 19011.3,
            "contract": {"kind": "call", "strike": 130.0, "expiry": "2026-09-23",
                         "dte": 35, "premium": 4.20, "iv": 0.42},
            "contracts": 2, "option_max_loss": 840.0, "option_target_reward": 334.0,
            "v_target": 5.87, "v_stop": 1.2, "v_unchanged": 3.1,
            "payout_mult": 3.02, "iv_label": "rich",
            "equity_ev": 530.0, "option_ev": 40.0, "p_stop": 0.15,
            "p_neither": 0.20, "flip": 0.72}


def test_format_trade_shares_win_surface_rules():
    msg = optfmt.format_trade(_shares_win_plan())
    assert "BUY SHARES" in msg
    assert "154" in msg and "$123.45" in msg.replace(",", "")
    assert "Kill below 118" in msg or "Kill below $118" in msg
    assert "08/29" in msg                       # exit date, not a duration
    assert "~65%" in msg                        # confidence in body
    # confidence NOT in the header line
    assert "65%" not in msg.splitlines()[0]
    # reproducibility: no EV, no R-multiples, no FLIPS on the surface
    assert "EV" not in msg and "R:R" not in msg and "FLIP" not in msg.upper()
    # option shown as the one-line SKIP, with its exit value
    assert "SKIP" in msg and "5.87" in msg


def test_format_trade_option_win_shows_exit_value_and_sideways():
    plan = _shares_win_plan()
    plan.update(winner="option", payout_mult=1.9)
    msg = optfmt.format_trade(plan)
    assert "BUY CALLS" in msg
    assert "130C" in msg and "5.87" in msg      # exit value on the surface
    assert "Flat by 08/29" in msg               # mandatory sideways branch
    assert "Max loss $840" in msg.replace(",", "")


def test_format_trade_equity_only_when_no_options():
    plan = {"symbol": "GLD", "options_available": False, "winner": "equity",
            "spot": 380.0, "target": 400.0, "stop": 370.0, "move_pct": 5.3,
            "exit_date": date(2026, 8, 29), "p": 0.6, "shares": 25,
            "equity_stop_loss": 250.0, "equity_target_reward": 500.0,
            "capital": 9500.0}
    msg = optfmt.format_trade(plan)
    assert "BUY SHARES" in msg and "no options" in msg.lower()


def test_format_trade_full_appends_audit_block_with_ev_and_flip():
    msg = optfmt.format_trade_full(_shares_win_plan())
    assert "BUY SHARES" in msg                 # includes the decision surface
    assert "AUDIT" in msg
    assert "EV" in msg                         # EV appears ONLY here
    assert "IV 42%" in msg or "IV 42.0%" in msg
    assert "flip" in msg.lower() and "72" in msg   # flip point 0.72
    assert "p_stop" in msg or "stop 15%" in msg
