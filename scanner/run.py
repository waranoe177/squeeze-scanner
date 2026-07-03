"""CLI entrypoint for the daily scan.

Usage:
    python -m scanner.run [--watchlist watchlist.csv] [--out out] [--dry-run]

Writes out/results.json + out/charts/*.png. Sends to Telegram when
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set and --dry-run is not passed.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from scanner import chart, data, notify, scan


def main(argv=None) -> dict:
    try:  # Windows consoles default to cp1252 and choke on emoji in the message
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Daily squeeze scan")
    ap.add_argument("--watchlist", default="watchlist.csv")
    ap.add_argument("--out", default="out")
    ap.add_argument("--period", default="2y")
    ap.add_argument("--dry-run", action="store_true", help="don't send to Telegram")
    ap.add_argument("--no-charts", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    (out_dir / "charts").mkdir(parents=True, exist_ok=True)

    symbols = data.load_watchlist(args.watchlist)
    print(f"scanning {len(symbols)} symbols...")
    frames = data.fetch_daily(symbols, period=args.period)
    payloads = scan.scan_frames(frames)
    as_of = max((p["date"] for p in payloads), default="")
    results = scan.build_results(payloads, as_of=as_of)

    if not args.no_charts:
        for p in results["fired"]:
            sym = p["symbol"]
            try:
                chart.render_layers(frames[sym], sym, str(out_dir / "charts" / f"{sym}.png"), lookback=90)
                p["chart"] = f"charts/{sym}.png"
            except Exception as exc:  # chart is a nicety, never fail the scan
                print(f"  [warn] chart failed for {sym}: {exc}")

    (out_dir / "results.json").write_text(json.dumps(results, indent=2))
    message = notify.format_message(results)
    print("\n" + message + "\n")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if args.dry_run or not (token and chat_id):
        reason = "dry-run" if args.dry_run else "no TELEGRAM_BOT_TOKEN/CHAT_ID set"
        print(f"[not sending: {reason}]")
        return results

    try:
        for p in results["fired"]:
            cpath = out_dir / "charts" / f"{p['symbol']}.png"
            if cpath.exists():
                notify.send_photo(token, chat_id, str(cpath), caption=notify._fired_line(p))
        notify.send_message(token, chat_id, message)
        print(f"[sent to Telegram chat {chat_id}]")
    except Exception as exc:
        # A notify failure must not fail the whole job — results are already
        # written and will still be committed for the dashboard.
        print(f"[telegram send FAILED: {exc}]")
        print("[hint: open YOUR bot in Telegram and tap Start, and check the secrets]")
    return results


if __name__ == "__main__":
    main()
