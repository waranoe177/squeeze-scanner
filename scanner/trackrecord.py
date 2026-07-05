"""Public track-record site generator.

Plain fast HTML/CSS regenerated from the ledger on every run — no framework,
must load instantly on a phone. Every signal ever fired is listed, losses
included; each row links to its timestamped Telegram post (the receipt).
"""

import argparse
import html
import json
from pathlib import Path

from scanner import ledger

BRAND = "Sqzdots Indicator"
DISCLAIMER = ("Educational tool, not investment advice. "
              "Past performance does not guarantee future results.")

CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0 auto;
       max-width: 780px; padding: 1rem; line-height: 1.5; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; }
nav a { margin-right: 1rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
th, td { text-align: left; padding: 0.3rem 0.5rem; border-bottom: 1px solid #8884; }
.win { color: #1a7f37; } .loss { color: #cf222e; } .time { color: #9a6700; }
.stats { display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 1rem 0; }
.stat b { display: block; font-size: 1.3rem; }
footer { margin-top: 2rem; font-size: 0.75rem; opacity: 0.7; }
svg { max-width: 100%; }
"""


def _esc(x) -> str:
    return html.escape(str(x))


def _page(title: str, body: str, channel_url: str | None) -> str:
    channel = f'<a href="{_esc(channel_url)}">Telegram channel</a>' if channel_url else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} — {BRAND}</title><link rel="stylesheet" href="style.css"></head>
<body><nav><a href="index.html">Record</a><a href="signals.html">All signals</a>
<a href="methodology.html">Methodology</a>{channel}</nav>
{body}
<footer>{_esc(DISCLAIMER)} · Ledger: <a href="signals.jsonl">signals.jsonl</a></footer>
</body></html>"""


def _equity_svg(curve: list) -> str:
    """Cumulative-R sparkline as inline SVG. Empty string when < 2 points."""
    if len(curve) < 2:
        return ""
    ys = [pt[1] for pt in curve]
    lo, hi = min(ys + [0.0]), max(ys + [0.0])
    span = (hi - lo) or 1.0
    w, h = 700, 160
    step = w / (len(ys) - 1)
    pts = " ".join(f"{i * step:.1f},{h - (y - lo) / span * h:.1f}"
                   for i, y in enumerate(ys))
    zero_y = h - (0.0 - lo) / span * h
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="equity curve in R">'
            f'<line x1="0" y1="{zero_y:.1f}" x2="{w}" y2="{zero_y:.1f}" '
            f'stroke="#8888" stroke-dasharray="4"/>'
            f'<polyline points="{pts}" fill="none" stroke="#2f81f7" stroke-width="2"/></svg>')


def _row(rec: dict, channel_username: str | None) -> str:
    status = rec["status"]
    r = rec["r_multiple"]
    r_txt = f"{r:+.2f}R" if r is not None else "—"
    receipt = ""
    if channel_username and rec.get("telegram_msg_id"):
        receipt = (f'<a href="https://t.me/{_esc(channel_username)}/'
                   f'{rec["telegram_msg_id"]}">post</a>')
    return (f'<tr><td>{_esc(rec["signal_date"])}</td>'
            f'<td><b>{_esc(rec["symbol"])}</b></td>'
            f'<td>{_esc(rec["direction"])}</td>'
            f'<td>{rec["entry"] if rec["entry"] is not None else "—"}</td>'
            f'<td>{rec["stop"] if rec["stop"] is not None else "—"}</td>'
            f'<td>{rec["target"] if rec["target"] is not None else "—"}</td>'
            f'<td class="{_esc(status)}">{_esc(status)}</td>'
            f'<td>{r_txt}</td><td>{receipt}</td></tr>')


def render_site(records: list[dict], out_dir, channel_username: str | None = None,
                channel_url: str | None = None) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    s = ledger.stats(records)

    win_rate = f"{s['win_rate'] * 100:.1f}%" if s["win_rate"] is not None else "—"
    avg_r = f"{s['avg_r']:+.3f}" if s["avg_r"] is not None else "—"
    index_body = f"""<h1>{BRAND}</h1>
<p>A systematic squeeze scanner that publishes every signal it fires — wins and
losses — before the move happens. This page regenerates automatically from the
signal ledger after every scan.</p>
<div class="stats">
<div class="stat"><b>{s['n_closed']}</b>closed signals</div>
<div class="stat"><b>{win_rate}</b>win rate</div>
<div class="stat"><b>{avg_r}</b>avg R</div>
<div class="stat"><b>{s['total_r']:+.3f}</b>total R</div>
<div class="stat"><b>{s['max_losing_streak']}</b>worst losing streak</div>
<div class="stat"><b>{s['n_open']}</b>open</div>
</div>
{_equity_svg(s['equity_curve'])}
<p>This system has historically had losing streaks; the edge is in the average.
See <a href="methodology.html">methodology</a> for the exact rules and
<a href="signals.html">all signals</a> for the full record.</p>"""

    rows = "".join(_row(r, channel_username)
                   for r in sorted(records, key=lambda r: r["signal_date"], reverse=True))
    signals_body = f"""<h1>All signals</h1>
<p>Every signal ever fired, newest first. Nothing is removed or edited after the
fact — the <a href="signals.jsonl">raw ledger</a> and its git history are public.</p>
<table><tr><th>Signal</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Stop</th>
<th>Target</th><th>Status</th><th>R</th><th>Receipt</th></tr>{rows}</table>"""

    methodology_body = f"""<h1>Methodology</h1>
<p>BUY when ALL of: squeeze ON · RSI &gt; 50 · PPO ≥ 0 · EMA8 &gt; EMA21 ·
full EMA stack (8&gt;21&gt;34, 50&gt;200) · MACD green (rising, ≥ 0) ·
weekly Moxie &gt; 0 and rising. SELL is the strict mirror. Signals are computed
once daily on completed bars — never intraday, never revised.</p>
<h2>Trade model (fixed, mechanical)</h2>
<p>Entry: next day's open after the signal. Target: entry + 2.5 × ATR(14).
Stop: entry − 1.5 × ATR(14). Time exit: close of the 5th bar if neither level
is touched. When one bar touches both stop and target, it counts as a stop
(conservative). The public record and our backtests use the same code.</p>
<h2>Disclaimer</h2><p>{_esc(DISCLAIMER)}</p>"""

    (out / "style.css").write_text(CSS, encoding="utf-8")
    (out / "index.html").write_text(_page("Record", index_body, channel_url), encoding="utf-8")
    (out / "signals.html").write_text(_page("All signals", signals_body, channel_url), encoding="utf-8")
    (out / "methodology.html").write_text(_page("Methodology", methodology_body, channel_url), encoding="utf-8")
    (out / "signals.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Regenerate the track-record site")
    ap.add_argument("--ledger", default=ledger.DEFAULT_PATH)
    ap.add_argument("--out", default="site")
    ap.add_argument("--channel-username", default=None)
    ap.add_argument("--channel-url", default=None)
    args = ap.parse_args(argv)
    render_site(ledger.load(args.ledger), args.out,
                channel_username=args.channel_username, channel_url=args.channel_url)
    print(f"site written to {args.out}/")


if __name__ == "__main__":
    main()
