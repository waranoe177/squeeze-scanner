"""Weekly recap card: one shareable PNG, auto-posted every Sunday.

This is the word-of-mouth artifact — members forward it. Machine-made,
zero operator effort. 1200x675 (Telegram/Twitter-friendly)."""

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from scanner import ledger

BRAND = "Sqzdots Indicator"


def week_slice(records: list[dict], week_ending: str) -> dict:
    end = datetime.strptime(week_ending, "%Y-%m-%d").date()
    start = end - timedelta(days=6)

    def in_week(d):
        return d is not None and start <= datetime.strptime(d, "%Y-%m-%d").date() <= end

    return {
        "fired": [r for r in records if in_week(r["signal_date"])],
        "closed": [r for r in records if r["status"] in ledger.CLOSED
                   and in_week(r["exit_date"])],
    }


def render_card(records: list[dict], week_ending: str, out_path) -> str:
    wk = week_slice(records, week_ending)
    s = ledger.stats(records)
    fig = plt.figure(figsize=(12, 6.75), dpi=100)
    fig.patch.set_facecolor("#0d1117")
    ax = fig.add_axes([0.55, 0.12, 0.4, 0.35])

    def txt(x, y, string, size=16, color="#e6edf3", weight="normal"):
        fig.text(x, y, string, fontsize=size, color=color, weight=weight)

    txt(0.05, 0.90, BRAND, 26, "#2f81f7", "bold")
    txt(0.05, 0.84, f"Week ending {week_ending}", 14, "#8b949e")

    lines = []
    for r in wk["closed"]:
        mark = {"win": "+", "loss": "-", "time": "~"}[r["status"]]
        lines.append(f"{mark} {r['symbol']}  {r['r_multiple']:+.2f}R ({r['status']})")
    for r in wk["fired"]:
        if r["status"] not in ledger.CLOSED:
            lines.append(f"* {r['symbol']}  fired {r['signal_date']} ({r['status']})")
    if not lines:
        lines = ["No signals this week — 0 fired.",
                 "The squeeze is a waiting game; the scan ran every day."]
    for i, line in enumerate(lines[:9]):
        txt(0.05, 0.74 - i * 0.055, line, 15)

    win_rate = f"{s['win_rate'] * 100:.0f}%" if s["win_rate"] is not None else "—"
    avg_r = f"{s['avg_r']:+.2f}" if s["avg_r"] is not None else "—"
    txt(0.55, 0.84, "Running record", 14, "#8b949e")
    txt(0.55, 0.74, f"{s['n_closed']} closed · {win_rate} win rate · "
                    f"avg {avg_r}R · total {s['total_r']:+.1f}R", 15)

    curve = s["equity_curve"]
    if len(curve) >= 2:
        ys = [pt[1] for pt in curve]
        ax.plot(range(len(ys)), ys, color="#2f81f7", linewidth=2)
        ax.axhline(0, color="#8b949e", linewidth=0.7, linestyle="--")
        ax.set_facecolor("#0d1117")
        for spine in ax.spines.values():
            spine.set_color("#30363d")
        ax.tick_params(colors="#8b949e", labelsize=8)
        ax.set_title("Cumulative R", color="#8b949e", fontsize=10)
    else:
        ax.axis("off")

    txt(0.05, 0.04, "Every signal published before the move — wins and losses. "
                    "Educational tool, not investment advice.", 10, "#8b949e")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=fig.get_facecolor())
    plt.close(fig)
    return str(out)


def main(argv=None) -> str:
    from scanner import notify

    ap = argparse.ArgumentParser(description="Render + post the weekly recap card")
    ap.add_argument("--ledger", default=ledger.DEFAULT_PATH)
    ap.add_argument("--out", default="out/recap.png")
    ap.add_argument("--week-ending",
                    default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    records = ledger.load(args.ledger)
    path = render_card(records, args.week_ending, args.out)
    print(f"recap card written to {path}")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if args.dry_run or not (token and chat_id):
        print("[not sending: dry-run or missing TELEGRAM env]")
        return path
    notify.send_photo(token, chat_id, path,
                      caption=f"<b>{BRAND}</b> — weekly recap, week ending "
                              f"{args.week_ending}")
    print("[recap sent]")
    return path


if __name__ == "__main__":
    main()
