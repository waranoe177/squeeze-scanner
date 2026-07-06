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

from scanner import backtest, chart, data, ledger, notify, scan, trackrecord


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
    ap.add_argument("--ledger", default=ledger.DEFAULT_PATH)
    ap.add_argument("--site", default="site")
    ap.add_argument("--no-site", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    (out_dir / "charts").mkdir(parents=True, exist_ok=True)

    symbols = data.load_watchlist(args.watchlist)
    print(f"scanning {len(symbols)} symbols...")
    frames = data.fetch_daily(symbols, period=args.period)
    payloads = scan.scan_frames(frames)
    as_of = max((p["date"] for p in payloads), default="")
    results = scan.build_results(payloads, as_of=as_of)

    # Provisional entry-anchored levels for the alert (finalized at next open).
    for p in results["fired"]:
        target, stop = backtest.trade_levels(
            close=p["close"], ema21=p["ema21"], atr=p["atr"],
            entry=p["close"], direction=p["direction"], mode="entry",
        )
        p["prov_target"], p["prov_stop"] = round(target, 2), round(stop, 2)

    # Ledger: record new fires, backfill entries, close finished positions.
    records = ledger.load(args.ledger)
    ledger.append_fired(records, results["fired"])
    ledger.update(records, frames)

    if not args.no_charts:
        for p in results["fired"]:
            sym = p["symbol"]
            try:
                chart.render_layers(frames[sym], sym, str(out_dir / "charts" / f"{sym}.png"), lookback=90)
                p["chart"] = f"charts/{sym}.png"
            except Exception as exc:  # chart is a nicety, never fail the scan
                print(f"  [warn] chart failed for {sym}: {exc}")

    (out_dir / "results.json").write_text(json.dumps(results, indent=2))
    message = notify.format_message(results, footer=os.environ.get("TELEGRAM_FOOTER"))
    print("\n" + message + "\n")

    def _persist():
        ledger.save(args.ledger, records)
        if not args.no_site:
            trackrecord.render_site(
                records, args.site,
                channel_username=os.environ.get("SITE_CHANNEL_USERNAME"),
                channel_url=os.environ.get("SITE_CHANNEL_URL"),
            )

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if args.dry_run or not (token and chat_id):
        reason = "dry-run" if args.dry_run else "no TELEGRAM_BOT_TOKEN/CHAT_ID set"
        print(f"[not sending: {reason}]")
        _persist()
        return results

    send_failed = False
    by_id = {r["id"]: r for r in records}
    for p in results["fired"]:
        cpath = out_dir / "charts" / f"{p['symbol']}.png"
        if cpath.exists():
            try:
                body = notify.send_photo(token, chat_id, str(cpath),
                                         caption=notify._fired_line(p, cta=True))
                rec = by_id.get(f"{p['symbol']}-{p['date']}")
                if rec is not None and rec.get("telegram_msg_id") is None:
                    rec["telegram_msg_id"] = body["result"]["message_id"]
            except Exception as exc:
                # A failure on one photo must not skip the remaining photos
                # or the summary message.
                print(f"[photo send failed for {p['symbol']}: {exc}]")
                send_failed = True

    try:
        notify.send_message(token, chat_id, message)
        print(f"[sent to Telegram chat {chat_id}]")
    except Exception as exc:
        print(f"[telegram send FAILED: {exc}]")
        print("[hint: open YOUR bot in Telegram and tap Start, and check the secrets]")
        send_failed = True
    _persist()
    if send_failed:
        # A silently missed alert day is a trust leak: persist everything,
        # then fail the job so CI's failure step pages the operator.
        print("[exiting non-zero: Telegram delivery failed]")
        sys.exit(1)
    return results


if __name__ == "__main__":
    main()
