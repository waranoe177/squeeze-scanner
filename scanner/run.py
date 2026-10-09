"""CLI entrypoint for the daily scan.

Usage:
    python -m scanner.run [--watchlist watchlist.csv] [--out out] [--dry-run]

Writes out/results.json + out/charts/*.png. Sends to Telegram when
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set and --dry-run is not passed.
"""

import argparse
import html
import json
import os
import sys
from pathlib import Path

from scanner import backtest, chart, data, ledger, mtf, mtf_render, notify, scan, trackrecord


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


def _build_mtf(symbols, out_dir, as_of) -> dict:
    """Compute, record and render the multi-timeframe table (spec 2026-10-09 §3-§6).
    Never raises: a failure comes back as outcome["error"] so it can never touch
    the daily charts, the summary or the run's exit status."""
    try:
        frames = data.fetch_daily(symbols, period="5y", adjust=False)
        if not frames:
            raise RuntimeError("no data downloaded")
        stale = mtf.stale_symbols(frames, as_of)
        if len(stale) == len(frames):
            # Never let a download that is a day behind read as a quiet day.
            raise RuntimeError(f"all {len(frames)} symbols stale (last bar before {as_of})")
        day = as_of or max(f.index[-1] for f in frames.values()).strftime("%Y-%m-%d")
        rows = mtf.build_table(frames, as_of)
        record = mtf.table_record(rows, day)
        record["stale"] = stale
        (Path(out_dir) / "mtf_table.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8")
        paths = mtf_render.render_table(rows, day, out_dir)
        caption = mtf_render.caption_text(rows)
        if stale:
            caption += f" · {len(stale)} stale skipped"
        print(f"[mtf table: {len(rows)} rows, {len(paths)} image(s), {len(stale)} stale]")
        return {"paths": [str(p) for p in paths], "caption": caption, "error": None}
    except Exception as exc:
        print(f"[mtf table FAILED: {exc!r}]")
        return {"paths": [], "caption": "", "error": f"{type(exc).__name__}: {exc}"}


def _send_mtf(token, chat_id, outcome, *, alert_on_error: bool) -> None:
    """Send the table (or the quiet-day line) to one chat. Never raises. Only the
    primary chat (alert_on_error=True) hears about a failure."""
    def warn(reason):
        if alert_on_error:
            notify.send_message(token, chat_id,
                                f"⚠️ MTF table failed: {html.escape(reason, quote=False)}")

    try:
        if outcome["error"]:
            warn(outcome["error"])
            return
        if not outcome["paths"]:
            notify.send_message(token, chat_id, mtf_render.QUIET)
            return
        for i, p in enumerate(outcome["paths"]):
            notify.send_photo(token, chat_id, p, caption=outcome["caption"] if i == 0 else "")
    except Exception as exc:
        print(f"[mtf table send to {chat_id} failed (non-fatal): {exc}]")
        try:
            warn(f"{type(exc).__name__}: {exc}")
        except Exception:
            pass


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
        _build_mtf(symbols, out_dir, as_of)    # the table is still recorded on a dry run
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

    # Multi-timeframe table: built only AFTER the primary alert is out, so its 5y
    # download can never delay the daily charts or summary (spec §7).
    mtf_outcome = _build_mtf(symbols, out_dir, as_of)
    _send_mtf(token, chat_id, mtf_outcome, alert_on_error=True)

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
        for cid in extras:
            _send_mtf(token, cid, mtf_outcome, alert_on_error=False)

    _persist()
    if send_failed:
        # A silently missed alert day is a trust leak: persist everything,
        # then fail the job so CI's failure step pages the operator.
        print("[exiting non-zero: Telegram delivery failed]")
        sys.exit(1)
    return results


if __name__ == "__main__":
    main()
