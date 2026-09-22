"""Weekly-timeframe scan: run the same 7-condition confluence (A++ and the
early 'A' tier) on NATIVE WEEKLY bars, with the Moxie stepped up to MONTHLY
(htf_rule="ME"), track fired signals in a separate weekly ledger, render weekly
charts, and push a distinct '📅 WEEKLY Scan' digest to Telegram.

Runs Saturday after Friday's close (weekly bar complete). Standalone from the
daily pipeline: its own ledger (ledger/signals_weekly.jsonl), its own out dir,
no go/pass decisions and no public site.

    python -m scanner.weekrun [--universe universe.csv] [--futures futures.csv]
                              [--out out_weekly] [--dry-run] [--no-charts]
"""

import argparse
import json
import os
import sys
from pathlib import Path

from scanner import backtest, chart, data, ledger, notify, scan

WEEKLY_LEDGER = "ledger/signals_weekly.jsonl"
WEEKLY_MAX_HOLD = 5   # hold window in WEEKLY bars (~5 weeks)
TITLE = "📅 Sqzdots WEEKLY Scan"


def main(argv=None) -> dict:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Weekly-timeframe squeeze scan")
    ap.add_argument("--universe", default="universe.csv")
    ap.add_argument("--futures", default="futures.csv")
    ap.add_argument("--out", default="out_weekly")
    ap.add_argument("--ledger", default=WEEKLY_LEDGER)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-charts", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    (out_dir / "charts").mkdir(parents=True, exist_ok=True)

    symbols = data.load_universe([args.universe, args.futures])
    print(f"weekly-scanning {len(symbols)} symbols...")
    frames = data.fetch_weekly(symbols)               # native 1wk bars
    payloads = scan.scan_frames(frames, htf_rule="ME")  # weekly confluence, monthly Moxie
    as_of = max((p["date"] for p in payloads), default="")
    results = scan.build_results(payloads, as_of=as_of)

    # Provisional entry-anchored levels (weekly ATR -> wider, swing-scale).
    for p in results["fired"]:
        target, stop = backtest.trade_levels(
            close=p["close"], ema21=p["ema21"], atr=p["atr"],
            entry=p["close"], direction=p["direction"], mode="entry",
        )
        p["prov_target"], p["prov_stop"] = round(target, 2), round(stop, 2)

    # Weekly ledger: record new fires, resolve outcomes over weekly bars.
    records = ledger.load(args.ledger)
    ledger.append_fired(records, results["fired"])
    ledger.update(records, frames, max_hold=WEEKLY_MAX_HOLD)

    if not args.no_charts:
        for p in results["fired"]:
            sym = p["symbol"]
            try:
                chart.render_layers(
                    frames[sym], f"{sym} Weekly (1W)",
                    str(out_dir / "charts" / f"{sym}.png"),
                    lookback=80, moxie_tf="ME")
                p["chart"] = f"charts/{sym}.png"
            except Exception as exc:
                print(f"  [warn] weekly chart failed for {sym}: {exc}")

    (out_dir / "results.json").write_text(json.dumps(results, indent=2))
    message = notify.format_message(
        results, footer=os.environ.get("TELEGRAM_FOOTER"), title=TITLE,
        run_number=os.environ.get("GITHUB_RUN_NUMBER"))
    print("\n" + message + "\n")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if args.dry_run or not (token and chat_id):
        reason = "dry-run" if args.dry_run else "no TELEGRAM_BOT_TOKEN/CHAT_ID"
        print(f"[not sending: {reason}]")
        ledger.save(args.ledger, records)
        return results

    names = {p["symbol"]: data.company_name(p["symbol"]) for p in results["fired"]}

    send_failed = False
    for p in results["fired"]:
        cpath = out_dir / "charts" / f"{p['symbol']}.png"
        if cpath.exists():
            try:
                caption = notify._fired_line(p, cta=False, name=names.get(p["symbol"]),
                                             show_ladder=True)
                notify.send_photo(token, chat_id, str(cpath), caption=caption)
            except Exception as exc:
                print(f"[weekly photo send failed for {p['symbol']}: {exc}]")
                send_failed = True

    try:
        notify.send_message(token, chat_id, message)
        print(f"[weekly scan sent to chat {chat_id}]")
    except Exception as exc:
        print(f"[weekly telegram send FAILED: {exc}]")
        send_failed = True

    # Also deliver to extra owners + broadcast recipients (same as the daily run).
    extras = notify.parse_chat_ids(os.environ.get("TELEGRAM_ALERT_CHAT_IDS")) \
        + notify.parse_chat_ids(os.environ.get("TELEGRAM_OWNER_IDS"))
    extras = [c for c in dict.fromkeys(extras) if c != chat_id]
    if extras:
        delivered = notify.broadcast(token, extras, results["fired"],
                                     out_dir / "charts", message, names=names)
        print(f"[weekly broadcast to extras: {delivered}]")

    ledger.save(args.ledger, records)
    if send_failed:
        print("[exiting non-zero: weekly Telegram delivery failed]")
        sys.exit(1)
    return results


if __name__ == "__main__":
    main()
