"""Telegram notification: format the daily scan into a message and send it.

Messages use Telegram's HTML parse mode (simpler than MarkdownV2 — only &, <, >
need escaping). The HTTP send is a thin wrapper over the Bot API; configure with
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.
"""

import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# The routine is anchored to US market time, so the stamp the user reads is ET
# regardless of where the runner lives (Actions runners are UTC).
_ET = "America/New_York"


def _esc(text) -> str:
    return html.escape(str(text))


def provenance_line(results: dict, run_number=None, tz: str = _ET,
                    label: str = "ET") -> str:
    """One-line "did this run, and when?" stamp for the digest.

    Answers it from the message alone, without opening GitHub -- the scan's own
    wall-clock time in ET (converted from the stored UTC `generated_at`), the
    bar it read, how many symbols it covered, and which CI run produced it.
    Every field is optional: a missing one is dropped rather than rendered as
    None, so a local/dry run still produces a sensible line.
    """
    bits = []
    gen = results.get("generated_at")
    if gen:
        try:
            dt = datetime.fromisoformat(str(gen).replace("Z", "+00:00"))
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is None:            # legacy naive stamps were UTC
                dt = dt.replace(tzinfo=timezone.utc)
            try:
                local = dt.astimezone(ZoneInfo(tz))
            except Exception:
                # format_message was a TOTAL function before this line existed.
                # A missing tz database must not crash the scan before it sends;
                # drop the stamp and keep the rest of the digest.
                local = None
            if local is not None:
                bits.append(f"scan {local.strftime('%H:%M:%S')} {label}")
    if results.get("as_of"):
        bits.append(f"bar {_esc(results['as_of'])}")
    if results.get("universe") is not None:
        # `is not None`, not truthiness: a universe of 0 means the watchlist
        # failed to load, which is exactly when you want to see it.
        bits.append(f"{results['universe']} names")
    if run_number:
        bits.append(f"run #{_esc(run_number)}")
    return " · ".join(bits)


# The seven buy conditions, bottom-to-top, as the user reads them off the B3
# panel. A FIRED signal has all seven met by construction, so the chart caption
# shows them all checked — a plain-English "why this fired" for a new reader.
# HTML parse mode: > and < must be escaped in label text.
_LADDER_BULL = ["Squeeze", "RSI&gt;50", "PPO≥0", "EMA8&gt;21", "Stack", "MACD↑", "Moxie↑"]
_LADDER_BEAR = ["Squeeze", "RSI&lt;50", "PPO&lt;0", "EMA8&lt;21", "Stack↓", "MACD↓", "Moxie↓"]


def _ladder(direction: str, grade: str | None = None) -> str:
    labels = _LADDER_BEAR if direction == "bear" else _LADDER_BULL
    # The early 'A' tier meets all conditions EXCEPT MACD (index 5): for a buy it
    # is rising-but-not-green (🔼), for a sell falling-but-not-red (🔽). Show that
    # one distinctly so the caption is honest.
    a_macd = "🔽" if direction == "bear" else "🔼"
    marks = [
        (f"{a_macd} {lbl}" if (grade == "A" and i == 5) else f"✅ {lbl}")
        for i, lbl in enumerate(labels)
    ]
    return f"\n   {'  '.join(marks[:4])}\n   {'  '.join(marks[4:])}"


def _fired_line(p: dict, cta: bool = False, name: str | None = None,
                show_ladder: bool = False) -> str:
    grade = str(p.get("grade", ""))
    if grade == "A":
        arrow = "⚪ A-BUY" if p["direction"] == "bull" else "⚪ A-SELL"   # early, 6/7
    elif p["direction"] == "bull":
        arrow = "🟢 BUY"
    else:
        arrow = "🔴 SELL"
    head = f"{arrow} <b>{_esc(p['symbol'])}</b>"
    if name:
        head += f" — {_esc(name)}"
    if p.get("score") is not None:
        head += f" · score {p['score']:.0f}/100 ({_esc(p.get('conviction_grade', ''))})"
    if p.get("date"):
        head += f" · bar {p['date']}"
    tail = ""
    if p.get("recommendation"):
        extra = f" (final {p['final_score']:.0f}, news {_esc(p.get('stance', ''))})" \
            if p.get("final_score") is not None else ""
        tail = f"\n   🧠 <b>{_esc(p['recommendation'])}</b>{extra}"
    if p.get("prov_target") is not None:
        levels = (f"   target {p['prov_target']:.2f} · stop {p['prov_stop']:.2f}"
                  f" (finalize at next open)")
    else:
        levels = (f"   target {p['target_up']:.2f} / {p['target_dn']:.2f}"
                  f" · stop {p['stop']:.2f}")
    if grade == "A":
        _mv = "falling, not yet red" if p["direction"] == "bear" else "rising, not yet green"
        a_note = f"\n   ⚪ <b>early A</b> — MACD {_mv}"
    else:
        a_note = ""
    ladder = _ladder(p["direction"], grade) if show_ladder else ""
    cta_line = "\n   ↩️ Reply to this chart: go or pass" if cta else ""
    return (
        f"{head}\n"
        f"   close {p['close']:.2f} · RSI {p['rsi']:.0f}\n"
        f"{levels}"
        f"{a_note}"
        f"{tail}"
        f"{ladder}"
        f"{cta_line}"
    )


def format_message(results: dict, footer: str | None = None,
                   title: str = "Sqzdots Scan", run_number=None,
                   provenance: bool = True) -> str:
    """Build the HTML message body for a results document. `title` lets the
    weekly scan use a distinct header (e.g. '📅 Sqzdots WEEKLY Scan').

    `run_number` (GITHUB_RUN_NUMBER) feeds the provenance stamp; omitted for
    local runs so a dry-run message stays clean. `provenance=False` drops the
    stamp entirely -- the free/delayed channel posts YESTERDAY's results the
    next morning, where last night's clock time reads as a bug and the universe
    size isn't public."""
    lines = [f"<b>{_esc(title)}</b> — bar {_esc(results['as_of'])}"]
    fired = results.get("fired", [])

    if fired:
        lines.append(f"{len(fired)} signal(s) fired:")
        lines.append("")
        lines.extend(_fired_line(p) for p in fired)
        watching = results.get("watching", [])
        if watching:
            lines.append("")
            lines.append("👀 Coiled (in squeeze, not yet aligned):")
            lines.append(_esc(", ".join(watching)))
    else:
        lines.append(f"Scanned {results.get('universe', 0)} names. 0 fired.")
        detail = results.get("watching_detail", [])
        # older results.json files have only the plain `watching` symbol list
        names = [d["symbol"] for d in detail] or results.get("watching", [])
        if names:
            lines.append(f"{len(names)} squeezes building: "
                         f"{_esc(', '.join(names[:12]))}")
            if detail:
                top = detail[0]
                lines.append(f"Closest to trigger: <b>{_esc(top['symbol'])}</b> "
                             f"({top['lit']}/7 conditions lit, leaning {_esc(top['lean'])})")
        else:
            lines.append("No squeezes building today.")

    # Provenance stamp on BOTH branches: the universe count used to appear only
    # when nothing fired, so a fired-day message couldn't tell you the coverage
    # or the scan's wall-clock time.
    prov = provenance_line(results, run_number=run_number) if provenance else ""
    if prov:
        lines.append("")
        lines.append(prov)

    if footer:
        lines.append("")
        lines.append(_esc(footer))
    return "\n".join(lines)


def _check(resp) -> dict:
    """Raise a clear error that includes Telegram's own description on failure."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not resp.ok or not body.get("ok", False):
        desc = body.get("description", (resp.text or "")[:300])
        raise RuntimeError(f"Telegram API {resp.status_code}: {desc}")
    return body


def send_message(token: str, chat_id: str, text: str) -> dict:
    """Send a text message via the Telegram Bot API."""
    import requests

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30,
    )
    return _check(resp)


def send_photo(token: str, chat_id: str, photo_path: str, caption: str = "") -> dict:
    """Send a chart image with an optional caption."""
    import requests

    with open(photo_path, "rb") as fh:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
            files={"photo": fh},
            timeout=60,
        )
    return _check(resp)


def parse_chat_ids(value) -> list[str]:
    """Parse a comma/space-separated chat-id string into a clean, de-duplicated
    list (order preserved). Blank/None -> []. Used for extra alert recipients."""
    if not value:
        return []
    seen: dict[str, None] = {}
    for tok in str(value).replace(",", " ").split():
        seen.setdefault(tok, None)
    return list(seen.keys())


def broadcast(token: str, chat_ids, fired: list[dict], charts_dir, message: str,
              *, names=None, send_photo=send_photo, send_message=send_message) -> dict:
    """Best-effort copy of the daily alert (fired charts + summary) to each of
    `chat_ids`. Secondary recipients only — the primary owner send stays in
    run.py with its ledger/message-id capture and go/pass CTA.

    Chart captions carry the company name (from `names`, a {symbol: name} map)
    and the ✅ condition ladder, so a non-expert recipient can read the setup.
    Never raises: a recipient that fails (e.g. hasn't started the bot) is logged
    and marked False so it can't break the critical primary path. Returns
    {chat_id: delivered_bool}. The `send_*` params are injectable for testing.
    """
    from pathlib import Path

    names = names or {}
    results: dict[str, bool] = {}
    for cid in chat_ids:
        try:
            for p in fired:
                cpath = Path(charts_dir) / f"{p['symbol']}.png"
                if cpath.exists():
                    caption = _fired_line(p, name=names.get(p["symbol"]),
                                          show_ladder=True)
                    send_photo(token, cid, str(cpath), caption=caption)
            send_message(token, cid, message)
            results[cid] = True
        except Exception as exc:
            print(f"[warn] broadcast to {cid} failed (non-fatal): {exc}")
            results[cid] = False
    return results
