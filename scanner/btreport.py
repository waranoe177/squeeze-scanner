"""Backtest-findings report -> a self-contained Markdown file.

Reads out/backtest.json (produced by scanner.backtest) and renders findings-
forward Markdown: win rate up top, the E3 (3-day hold, no stop) reasoning and
the methodology/caveats baked in, so the file needs no external context. Meant
to be reviewed in the terminal AND uploaded to a Claude Project as knowledge
(re-run -> regenerated .md -> re-upload to refresh the project).
"""

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np


def _pct(x, nd=1):
    return f"{x * 100:.{nd}f}%" if x is not None else "—"


def _num(x, nd=2, sign=False):
    if x is None:
        return "—"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def _tail(trades):
    rets = np.array([t["option_return"] for t in trades
                     if t.get("entry_premium", 0) > 0], dtype=float)
    if rets.size == 0:
        return {"worst": None, "best": None, "avg_loss": None, "p05": None}
    losses = rets[rets < 0]
    return {"worst": float(rets.min()), "best": float(rets.max()),
            "avg_loss": float(losses.mean()) if losses.size else 0.0,
            "p05": float(np.percentile(rets, 5))}


def format_report(doc, *, generated=None) -> str:
    generated = generated or date.today()
    summ = doc.get("summary", {}) or {}
    opt = summ.get("option", {}) or {}
    eq = summ.get("equity", {}) or {}
    trades = doc.get("trades", []) or []
    symbols = sorted({t["symbol"] for t in trades})
    dates = sorted(t["signal_date"] for t in trades)
    date_range = f"{dates[0]} – {dates[-1]}" if dates else "—"
    hold_days = summ.get("hold_days", 3)
    exit_rule = summ.get("exit", "hold")
    tail = _tail(trades)

    L = []
    L.append("# Sqzdots Backtest — Findings")
    L.append("")
    L.append(f"_Generated: {generated.isoformat()} · Data: {date_range} · "
             f"Universe: {len(symbols)} symbols · {opt.get('n', 0)} signals · "
             f"Exit: {exit_rule} ({hold_days}-day hold, no stop)_")
    L.append("")

    if opt.get("n"):
        L.append("## Key findings (option P&L — how the strategy is traded)")
        L.append("")
        L.append(f"- **Win rate: {_pct(opt.get('win_rate'))}** across {opt.get('n')} signals.")
        L.append(f"- **Median trade: {_pct(opt.get('median_pct'))}** return on premium "
                 "(the typical trade).")
        L.append(f"- **Expectancy: {_pct(opt.get('expectancy_pct'))}** mean return on premium.")
        L.append(f"- **Total P&L: ${_num(opt.get('total_cash'), 0)}** "
                 "(per $500 sized risk per trade).")
        if tail["best"] is not None:
            L.append(f"- Best / worst single trade: {_pct(tail['best'])} / {_pct(tail['worst'])}.")
        L.append("")
        L.append("## Risk / left tail")
        L.append("")
        L.append(f"- Average losing trade: {_pct(tail['avg_loss'])} of premium.")
        L.append(f"- 5th-percentile trade: {_pct(tail['p05'])} (worst 1-in-20).")
        L.append("- Long options are **defined-risk**: the most a trade can lose is the "
                 "premium paid. Risk is managed by **position sizing**, not exit stops "
                 "(stops were tested and backfire — see below).")
        L.append("")
    else:
        L.append("## Key findings")
        L.append("")
        L.append("- No priced option trades in this backtest (no signals, or no usable premiums).")
        L.append("")

    L.append("## Why this exit — 3-day hold, no stop")
    L.append("")
    L.append("A universe-wide sweep found a **fixed 3-day hold with no stop** maximizes "
             "win rate and the typical (median) trade:")
    L.append("")
    L.append("- **Hold length 1–5 days:** win rate and median return both **peak at 3 days**. "
             "Longer holds raise total P&L only via a few fat-tail runners, at a ~coin-flip "
             "win rate.")
    L.append("- **Stops backfire:** underlying ATR stops rarely fire in 3 days or cut "
             "recoverable trades; option-premium %-stops fail to cap the worst case (gaps blow "
             "through intraday) and fatten the loss tail by locking in would-be winners.")
    L.append("- Beats the old target/stop rule on win rate and the typical trade, at "
             "essentially the same total P&L.")
    L.append("")

    L.append("## Equity reference (underlying move, same exit)")
    L.append("")
    L.append(f"- Win rate: {_pct(eq.get('win_rate'))} · "
             f"Expectancy: {_num(eq.get('expectancy_r'), 2, sign=True)}R · "
             f"Max drawdown: {_num(eq.get('max_drawdown_r'), 2)}R · "
             f"Worst losing streak: {eq.get('max_losing_streak', '—')}.")
    L.append("")

    L.append("## Methodology & assumptions")
    L.append("")
    L.append("- **Signal:** the daily squeeze / Moxie confluence, computed walk-forward "
             "(point-in-time, no lookahead).")
    L.append("- **Entry:** next day's open after a signal fires.")
    L.append(f"- **Option:** near-ATM (strike = entry price), ~21 DTE, exited after a "
             f"{hold_days}-day hold, priced with Black-Scholes.")
    L.append("- **Implied volatility:** modeled as **20-day realized volatility** (no historical "
             "option chains exist). This makes absolute returns **optimistic** — it ignores the "
             "vol-risk premium, vol crush, bid/ask, and skew. The *relative* ranking of exit "
             "rules is robust to this, since every variant shares the assumption.")
    L.append("- **Decay:** counted in trading days (slightly understates calendar theta), "
             "applied uniformly.")
    L.append("")
    L.append("_Educational tool, not investment advice. Past performance does not guarantee "
             "future results._")
    L.append("")
    return "\n".join(L)


def main(argv=None) -> str:
    ap = argparse.ArgumentParser(
        description="Render the backtest findings report (Markdown)")
    ap.add_argument("--in", dest="inp", default="out/backtest.json")
    ap.add_argument("--out", default="out/backtest_report.md")
    args = ap.parse_args(argv)

    doc = json.loads(Path(args.inp).read_text())
    md = format_report(doc)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n[report written to {out}]")
    return str(out)


if __name__ == "__main__":
    main()
