# tests/test_optfmt.py
from datetime import date

from scanner import optfmt


def _shares_win_plan():
    return {"symbol": "NVDA", "options_available": True, "winner": "equity",
            "direction": "bull", "extended": False,
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


def test_direction_comes_from_plan_not_target_vs_spot():
    # A degenerate target (<= spot) must NOT flip a BUY into SHORT.
    plan = _shares_win_plan()
    plan.update(direction="bull", extended=False, target=123.40, move_pct=-0.0)
    msg = optfmt.format_trade(plan)
    assert "BUY SHARES" in msg and "SHORT" not in msg


def test_extended_refuses_with_reason_not_a_card():
    plan = _shares_win_plan()
    plan.update(direction="bull", extended=True, target=380.0, spot=384.14)
    msg = optfmt.format_trade(plan)
    assert "extended" in msg.lower()
    assert "BUY SHARES" not in msg and "SKIP" not in msg     # no comparison card
    assert "SHORT" not in msg                                # never inverts


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
    # reproducibility: no EV, no R-multiples, no FLIPS on the option-win surface either
    assert "EV" not in msg and "R:R" not in msg and "FLIP" not in msg.upper()


def test_format_trade_equity_only_when_no_options():
    plan = {"symbol": "GLD", "options_available": False, "winner": "equity",
            "direction": "bull", "extended": False,
            "spot": 380.0, "target": 400.0, "stop": 370.0, "move_pct": 5.3,
            "exit_date": date(2026, 8, 29), "p": 0.6, "shares": 25,
            "equity_stop_loss": 250.0, "equity_target_reward": 500.0,
            "capital": 9500.0}
    msg = optfmt.format_trade(plan)
    assert "BUY SHARES" in msg and "no options" in msg.lower()


def test_format_trade_equity_only_bear_kill_above():
    plan = {"symbol": "META", "options_available": False, "winner": "equity",
            "direction": "bear", "extended": False,
            "spot": 200.0, "target": 180.0, "stop": 210.0, "move_pct": -10.0,
            "exit_date": date(2026, 8, 29), "p": 0.6, "shares": 50,
            "equity_stop_loss": 500.0, "equity_target_reward": 1000.0,
            "capital": 10000.0}
    msg = optfmt.format_trade(plan)
    assert "SHORT SHARES" in msg
    assert "Kill above 210" in msg or "Kill above $210" in msg
    assert "Kill below" not in msg


def _bear_plan():
    return {"symbol": "META", "options_available": True, "winner": "equity",
            "direction": "bear", "extended": False,
            "spot": 200.0, "target": 180.0, "stop": 210.0, "move_pct": -10.0,
            "exit_date": date(2026, 8, 29), "p": 0.6,
            "shares": 50, "equity_stop_loss": 500.0, "equity_target_reward": 1000.0,
            "capital": 10000.0,
            "contract": {"kind": "put", "strike": 180.0, "expiry": "2026-09-23",
                         "dte": 35, "premium": 5.00, "iv": 0.55},
            "contracts": 3, "option_max_loss": 1500.0, "option_target_reward": 600.0,
            "v_target": 8.00, "v_stop": 1.5, "v_unchanged": 4.0,
            "payout_mult": 1.5, "iv_label": "rich",
            "equity_ev": 400.0, "option_ev": 100.0, "p_stop": 0.2,
            "p_neither": 0.2, "flip": 0.55}


def test_format_trade_bear_side_short_and_puts():
    equity_plan = _bear_plan()
    msg_equity = optfmt.format_trade(equity_plan)
    assert "SHORT SHARES" in msg_equity
    assert "Kill above 210" in msg_equity or "Kill above $210" in msg_equity
    assert "Kill below" not in msg_equity
    assert "EV" not in msg_equity and "R:R" not in msg_equity and \
        "FLIP" not in msg_equity.upper()

    option_plan = _bear_plan()
    option_plan.update(winner="option")
    msg_option = optfmt.format_trade(option_plan)
    assert "BUY PUTS" in msg_option
    assert "180P" in msg_option
    assert "EV" not in msg_option and "R:R" not in msg_option and \
        "FLIP" not in msg_option.upper()


def test_skip_line_shows_real_flat_loss_not_full_premium():
    # Item (b): "if it never moves" must be premium - residual, not full premium.
    # 2 contracts, $4.20 premium, $3.10 flat value -> flat loss 2*100*(4.20-3.10)=220,
    # residual 3.10*2*100=620. Full premium ($840) is the MAX loss, not the flat loss.
    msg = optfmt.format_trade(_shares_win_plan())
    assert "220" in msg                     # real flat loss (premium - residual)
    assert "620" in msg                     # residual still on the contract at exit
    assert "never moves" in msg
    assert "840 if it never moves" not in msg.replace(",", "")


def test_why_keeps_parity_when_risk_matched():
    plan = _shares_win_plan()
    plan["risk_matched"] = True
    msg = optfmt.format_trade(plan)
    assert "same $840 risk" in msg.replace(",", "")


def test_why_discloses_both_risks_when_not_matched():
    # Item (a): 1-contract floor exceeds budget -> no false "same $X risk" claim.
    plan = _shares_win_plan()
    plan.update(risk_matched=False, contracts=1, option_max_loss=835.0,
                equity_stop_loss=497.0)
    msg = optfmt.format_trade(plan)
    assert "not risk-matched" in msg
    assert "835" in msg and "497" in msg
    assert "at the same" not in msg          # false parity claim dropped


def test_option_win_discloses_both_risks_when_not_matched():
    plan = _shares_win_plan()
    plan.update(winner="option", payout_mult=1.6, risk_matched=False,
                contracts=1, option_max_loss=835.0, equity_stop_loss=497.0)
    msg = optfmt.format_trade(plan)
    assert "not risk-matched" in msg
    assert "at the same" not in msg


def test_format_trade_full_appends_audit_block_with_ev_and_flip():
    msg = optfmt.format_trade_full(_shares_win_plan())
    assert "BUY SHARES" in msg                 # includes the decision surface
    assert "AUDIT" in msg
    assert "EV" in msg                         # EV appears ONLY here
    assert "IV 42%" in msg or "IV 42.0%" in msg
    assert "flip" in msg.lower() and "72" in msg   # flip point 0.72
    assert "p_stop" in msg or "stop 15%" in msg
