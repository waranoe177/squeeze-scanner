"""Free-channel delayed poster (Phase 2): posts YESTERDAY's results each
morning. The free channel is the top of the funnel; same-day alerts are the
paid product."""

import argparse
import json
import os
from pathlib import Path

from scanner import notify


def format_delayed(results: dict, footer: str | None = None) -> str:
    body = notify.format_message(results)
    # swap the header line for the delayed variant
    lines = body.split("\n")
    lines[0] = f"<b>Sqzdots — yesterday's signals</b> — bar {notify._esc(results['as_of'])}"
    if results.get("fired"):
        lines.append("")
        lines.append("Same-day alerts are for members — link in footer.")
    if footer:
        lines.append("")
        lines.append(notify._esc(footer))
    return "\n".join(lines)


def main(argv=None) -> str:
    ap = argparse.ArgumentParser(description="Post yesterday's signals to the free channel")
    ap.add_argument("--results", default="out/results.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    results = json.loads(Path(args.results).read_text())
    msg = format_delayed(results, footer=os.environ.get("TELEGRAM_FOOTER"))
    print(msg)

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_FREE_CHAT_ID")
    if args.dry_run or not (token and chat_id):
        print("[not sending: dry-run or missing TELEGRAM_FREE_CHAT_ID]")
        return msg
    notify.send_message(token, chat_id, msg)
    print("[delayed post sent to free channel]")
    return msg


if __name__ == "__main__":
    main()
