"""Render an options.decide() plan into the Telegram decision surface.

Decision-first: one executable order, the loser as a one-line footnote. The
surface shows only reproducible facts — no EV, no R-multiples, no flip point
(those live in format_trade_full). Confidence appears as a labeled body line,
never in the header.
"""

from scanner import notify

_ACTION = {("equity", "bull"): "BUY SHARES", ("equity", "bear"): "SHORT SHARES",
           ("option", "bull"): "BUY CALLS", ("option", "bear"): "BUY PUTS"}


def _hdr(sym, action, exit_date):
    return f"<b>{notify._esc(sym)} · {action} · exit by {exit_date:%a %m/%d}</b>"


def _pct(p):
    return f"~{round(p * 100)}%"


def _direction(plan):
    # infer from target vs spot (bull when target above spot)
    return "bull" if plan["target"] >= plan["spot"] else "bear"


def format_trade(plan) -> str:
    d = _direction(plan)
    sym = plan["symbol"]
    exit_date = plan["exit_date"]
    conf = _pct(plan["p"])
    move = f"{plan['move_pct']:+.1f}%"

    kill_word = "below" if d != "bear" else "above"

    if not plan.get("options_available"):
        action = _ACTION[("equity", d)]
        return "\n".join([
            _hdr(sym, action, exit_date),
            f"  assumes {conf} you're right on {move}",
            "",
            f"  BUY {plan['shares']} sh @ ${plan['spot']:.2f}",
            f"  Kill {kill_word} {plan['stop']:.0f} (−${plan['equity_stop_loss']:.0f}) · "
            f"target {plan['target']:.0f} (+${plan['equity_target_reward']:.0f})",
            "",
            f"  COST  ${plan['capital']:,.0f} capital; full downside {kill_word} {plan['stop']:.0f}",
            f"  NOTE  no options listed for {notify._esc(sym)} — shares only",
        ])

    c = plan["contract"]
    exp = _fmt_expiry(c["expiry"])
    strike = f"{c['strike']:.0f}{'C' if c['kind'] == 'call' else 'P'}"
    winner = plan["winner"]

    if winner == "equity":
        action = _ACTION[("equity", d)]
        skip = (f"  SKIP  {plan['contracts']} × {strike} {exp} @ ${c['premium']:.2f} → "
                f"only +${plan['option_target_reward']:.0f} at {plan['target']:.0f} "
                f"({strike} ≈ ${plan['v_target']:.2f} vs ${c['premium']:.2f} paid); "
                f"−${plan['option_max_loss']:,.0f} if it never moves")
        why = (f"  WHY   {plan['payout_mult']:.0f}× the payout of the call "
               f"at the same ${plan['option_max_loss']:,.0f} risk"
               if plan.get("payout_mult") is not None else "  WHY   shares keep the edge")
        return "\n".join([
            _hdr(sym, action, exit_date),
            f"  assumes {conf} you're right on {move}",
            "",
            f"  BUY {plan['shares']} sh @ ${plan['spot']:.2f}",
            f"  Kill {kill_word} {plan['stop']:.0f} (−${plan['equity_stop_loss']:.0f}) · "
            f"target {plan['target']:.0f} (+${plan['equity_target_reward']:.0f})",
            "",
            why,
            f"  COST  ${plan['capital']:,.0f} capital; full downside {kill_word} {plan['stop']:.0f}",
            skip,
        ])

    # option wins
    action = _ACTION[("option", d)]
    return "\n".join([
        _hdr(sym, action, exit_date),
        f"  assumes {conf} you're right on {move}",
        "",
        f"  BUY {plan['contracts']} × {notify._esc(sym)} {strike} {exp} @ ${c['premium']:.2f} or better",
        f"  Max loss ${plan['option_max_loss']:,.0f} (all you can lose)",
        f"  Target {plan['target']:.0f} → ≈ +${plan['option_target_reward']:,.0f}  "
        f"({strike} ≈ ${plan['v_target']:.2f} vs ${c['premium']:.2f} paid)",
        f"  Flat by {exit_date:%m/%d} → close ≈ ${plan['v_unchanged'] * plan['contracts'] * 100:,.0f} back",
        "",
        f"  WHY   ~{plan['payout_mult']:.1f}× the payout of shares at the same "
        f"${plan['option_max_loss']:,.0f} risk" if plan.get("payout_mult") is not None
        else "  WHY   convex payoff beats shares at your odds",
        f"  COST  needs {move} by {exit_date:%m/%d}; IV {c['iv'] * 100:.0f}% is {plan['iv_label']}",
        f"  SKIP  {plan['shares']} sh → ≈ +${plan['equity_target_reward']:,.0f}, no clock, no decay",
    ])


def _fmt_expiry(iso):
    if not iso:
        return ""
    y, m, d = iso.split("-")
    return f"{m}/{d}"


def format_trade_full(plan) -> str:
    surface = format_trade(plan)
    if not plan.get("options_available"):
        return surface
    c = plan["contract"]
    flip = plan.get("flip")
    flip_line = (f"flips to shares below {round(flip * 100)}% confidence"
                 if flip is not None else "no flip in range")
    audit = "\n".join([
        "",
        "— AUDIT —",
        f"contract {c['strike']:.0f}{'C' if c['kind'] == 'call' else 'P'} "
        f"{c['expiry']} · {c['dte']}DTE · IV {c['iv'] * 100:.0f}%",
        f"exit values: target ${plan['v_target']:.2f} · stop ${plan['v_stop']:.2f} "
        f"· flat ${plan['v_unchanged']:.2f}",
        f"scenarios: target {round(plan['p'] * 100)}% · "
        f"stop {round(plan['p_stop'] * 100)}% · flat {round(plan['p_neither'] * 100)}%",
        f"EV: shares ${plan['equity_ev']:,.0f} · option ${plan['option_ev']:,.0f}",
        flip_line,
    ])
    return surface + "\n" + audit
