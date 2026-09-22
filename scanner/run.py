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


def _fold_decisions(records, path="ledger/decisions.jsonl"):
    """Apply the Fly bot's go/pass decisions (append-only decisions.jsonl) onto
    the ledger records. Idempotent: apply_decisions is write-once."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return records
    parsed = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    from scanner import decisions
    return decisions.apply_decisions(records, parsed)


def main(argv=None) -> dict:
    try:  # Windows consoles default to cp1252 and choke on emoji in the message
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Daily squeeze scan")
    ap.add_argument("--watchlist", default="watchlist.csv")
    ap.add_argument("--futures", default="futures.csv",
                    help="extra watchlist merged into the scan (e.g. =F futures); "
                         "skipped silently if the file is absent")
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

    symbols = data.load_universe([args.watchlist, args.futures])
    print(f"scanning {len(symbols)} symbols...")
    frames = data.fetch_daily(symbols, period=args.period)
    payloads = scan.scan_frames(frames)
    as_of = scan.session_date(payloads)
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
    _fold_decisions(records)          # <-- fold the Fly bot's go/pass decisions

    if not args.no_charts:
        # Native weekly bars for the fired symbols' MTF composite (one bulk call).
        weekly_frames = {}
        if results["fired"]:
            try:
                weekly_frames = data.fetch_weekly([p["symbol"] for p in results["fired"]])
            except Exception as exc:  # weekly is best-effort; composite falls back
                print(f"  [warn] weekly fetch failed: {exc}")
        for p in results["fired"]:
            sym = p["symbol"]
            try:
                chart.render_layers(frames[sym], sym, str(out_dir / "charts" / f"{sym}.png"), lookback=90)
                p["chart"] = f"charts/{sym}.png"
            except Exception as exc:  # chart is a nicety, never fail the scan
                print(f"  [warn] chart failed for {sym}: {exc}")
            # Weekly companion chart (native 1wk + monthly stepped Moxie), sent
            # as the 2nd image on `trade SYM`. (2D/3D deferred until calibrated.)
            try:
                wkf = weekly_frames.get(sym)
                if wkf is not None:
                    chart.render_layers(
                        wkf, f"{sym} Weekly (1W)",
                        str(out_dir / "charts" / f"{sym}_weekly.png"),
                        lookback=80, moxie_tf="ME")
                    p["weekly_chart"] = f"charts/{sym}_weekly.png"
            except Exception as exc:  # weekly chart is a nicety too
                print(f"  [warn] weekly chart failed for {sym}: {exc}")

    (out_dir / "results.json").write_text(json.dumps(results, indent=2))
    message = notify.format_message(results, footer=os.environ.get("TELEGRAM_FOOTER"),
                                    run_number=os.environ.get("GITHUB_RUN_NUMBER"))
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

    # Human-readable company/fund names for the fired charts (best-effort).
    names = {p["symbol"]: data.company_name(p["symbol"]) for p in results["fired"]}

    send_failed = False
    by_id = {r["id"]: r for r in records}
    for p in results["fired"]:
        cpath = out_dir / "charts" / f"{p['symbol']}.png"
        if cpath.exists():
            try:
                caption = notify._fired_line(p, cta=True, name=names.get(p["symbol"]),
                                             show_ladder=True)
                body = notify.send_photo(token, chat_id, str(cpath), caption=caption)
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

    # Broadcast a clean copy of the alert to any extra recipients. Best-effort:
    # a secondary recipient's failure is logged but never marks the day failed
    # (the primary owner send above is the trust anchor).
    # Extra recipients = broadcast list + any extra owner devices
    # (TELEGRAM_OWNER_IDS), so a second owner account gets the daily alert too.
    extras = notify.parse_chat_ids(os.environ.get("TELEGRAM_ALERT_CHAT_IDS")) \
        + notify.parse_chat_ids(os.environ.get("TELEGRAM_OWNER_IDS"))
    extras = [c for c in dict.fromkeys(extras) if c != chat_id]
    if extras:
        delivered = notify.broadcast(token, extras, results["fired"],
                                     out_dir / "charts", message, names=names)
        print(f"[broadcast to extra chats: {delivered}]")

    _persist()
    if send_failed:
        # A silently missed alert day is a trust leak: persist everything,
        # then fail the job so CI's failure step pages the operator.
        print("[exiting non-zero: Telegram delivery failed]")
        sys.exit(1)
    return results


if __name__ == "__main__":
    main()
