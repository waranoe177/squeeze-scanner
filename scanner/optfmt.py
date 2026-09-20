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


def format_trade(plan) -> str:
    if plan.get("extended"):
        return _extended_note(plan)

    if plan.get("futures"):
        return _format_futures(plan)

    d = plan["direction"]
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
        # Flat ("never moves") loss is premium MINUS the residual time value still
        # on the contract at exit — not the full premium (that's the max loss).
        residual = plan["v_unchanged"] * plan["contracts"] * 100.0
        flat_loss = max(0.0, plan["option_max_loss"] - residual)
        skip = (f"  SKIP  {plan['contracts']} × {strike} {exp} @ ${c['premium']:.2f} → "
                f"only +${plan['option_target_reward']:.0f} at {plan['target']:.0f} "
                f"({strike} ≈ ${plan['v_target']:.2f} vs ${c['premium']:.2f} paid); "
                f"−${flat_loss:,.0f} if it never moves (keeps ~${residual:,.0f})")
        why = _why_equity(plan)
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
        _why_option(plan),
        f"  COST  needs {move} by {exit_date:%m/%d}; IV {c['iv'] * 100:.0f}% is {plan['iv_label']}",
        f"  SKIP  {plan['shares']} sh → ≈ +${plan['equity_target_reward']:,.0f}, no clock, no decay",
    ])


def _dollars_pt(mult) -> str:
    return f"${mult:,.0f}" if mult >= 1 else f"${mult:.2f}"


def _format_futures(plan) -> str:
    """Futures card: entry front and center, then stop and two targets as
    price → distance → dollars → R:R (T1 = 2.5×ATR, T2 = 2.0×ATR, same stop),
    a per-point value, an est. margin/notional line, and a micro downsize option."""
    sym = notify._esc(plan["symbol"])
    name = notify._esc(plan["name"])
    action = "BUY" if plan["direction"] != "bear" else "SELL"
    exit_date = plan["exit_date"]
    spot, stop = plan["spot"], plan["stop"]
    t1, t2 = plan["target"], plan["target2"]
    stop_arrow = "↓" if stop < spot else "↑"
    tgt_arrow = "↑" if t1 > spot else "↓"

    def row(label, price, rest=""):
        return f"  {label:<8}  {price:.2f}{rest}"

    lines = [
        f"<b>{sym} · {action} · {name} · exit by {exit_date:%a %m/%d}</b>",
        f"  {_pct(plan['p'])} confidence · 1 contract · 1 pt = {_dollars_pt(plan['mult'])}",
        "",
        row("ENTER", spot),
        row("STOP", stop,
            f"   {stop_arrow} {plan['pt_risk']:.1f} pts   risk   −${plan['risk_dollars']:,.0f}"),
        row("TARGET 1", t1,
            f"   {tgt_arrow} {plan['pt_reward']:.1f} pts   +${plan['reward_dollars']:,.0f}"
            f"   2.5×ATR · R:R {plan['rr']:.1f}"),
        row("TARGET 2", t2,
            f"   {tgt_arrow} {plan['pt_reward2']:.1f} pts   +${plan['reward2_dollars']:,.0f}"
            f"   2.0×ATR · R:R {plan['rr2']:.1f}"),
        "",
        f"  Notional ≈ ${plan['notional'] / 1000:,.0f}k · est. margin ≈ "
        f"${plan['margin_est'] / 1000:,.0f}k (varies by broker)",
    ]
    if plan.get("micro_symbol"):
        lines.append(
            f"  Smaller? {notify._esc(plan['micro_symbol'])} micro "
            f"({_dollars_pt(plan['micro_mult'])}/pt) → risk −${plan['micro_risk']:,.0f}"
            f" · T1 +${plan['micro_reward']:,.0f} / T2 +${plan['micro_reward2']:,.0f}")
    return "\n".join(lines)


def _extended_note(plan) -> str:
    d = plan["direction"]
    verb = "BUY" if d != "bear" else "SHORT"
    side = "below" if d != "bear" else "above"
    sym = notify._esc(plan["symbol"])
    return "\n".join([
        f"<b>{sym} · {verb} signal — but extended</b>",
        f"  target {plan['target']:.0f} sits {side} the current "
        f"{plan['spot']:.0f}, so there's no room left to size.",
        "  This is a chase. Wait for a pullback toward EMA21, or set your own target.",
    ])


def _why_equity(plan) -> str:
    """WHY line when shares win. Only claims 'same $X risk' when the vehicles
    are actually risk-matched; otherwise discloses both real risk figures."""
    if plan.get("payout_mult") is None:
        return "  WHY   shares keep the edge"
    if plan.get("risk_matched", True):
        return (f"  WHY   {plan['payout_mult']:.0f}× the payout of the call "
                f"at the same ${plan['option_max_loss']:,.0f} risk")
    return (f"  WHY   1 contract (the minimum) risks ${plan['option_max_loss']:,.0f} "
            f"vs ${plan['equity_stop_loss']:,.0f} on the shares — "
            f"not risk-matched, and it still earns less")


def _why_option(plan) -> str:
    """WHY line when the option wins. Same risk-matched honesty as _why_equity."""
    if plan.get("payout_mult") is None:
        return "  WHY   convex payoff beats shares at your odds"
    if plan.get("risk_matched", True):
        return (f"  WHY   ~{plan['payout_mult']:.1f}× the payout of shares "
                f"at the same ${plan['option_max_loss']:,.0f} risk")
    return (f"  WHY   convex payoff wins, but 1 contract risks "
            f"${plan['option_max_loss']:,.0f} vs ${plan['equity_stop_loss']:,.0f} "
            f"on the shares — not risk-matched")


def _fmt_expiry(iso):
    if not iso:
        return ""
    y, m, d = iso.split("-")
    return f"{m}/{d}"


def format_trade_full(plan) -> str:
    surface = format_trade(plan)
    if plan.get("extended") or not plan.get("options_available"):
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
