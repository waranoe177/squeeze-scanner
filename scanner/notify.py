"""Telegram notification: format the daily scan into a message and send it.

Messages use Telegram's HTML parse mode (simpler than MarkdownV2 — only &, <, >
need escaping). The HTTP send is a thin wrapper over the Bot API; configure with
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.
"""

import html


def _esc(text) -> str:
    return html.escape(str(text))


def _fired_line(p: dict) -> str:
    arrow = "🟢 BUY" if p["direction"] == "bull" else "🔴 SELL"
    head = f"{arrow} <b>{_esc(p['symbol'])}</b>"
    if p.get("score") is not None:
        head += f" · score {p['score']:.0f}/100 ({_esc(p.get('conviction_grade', ''))})"
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
    return (
        f"{head}\n"
        f"   close {p['close']:.2f} · RSI {p['rsi']:.0f}\n"
        f"{levels}"
        f"{tail}"
    )


def format_message(results: dict, footer: str | None = None) -> str:
    """Build the HTML message body for a results document."""
    lines = [f"<b>Sqzdots Scan</b> — bar {_esc(results['as_of'])}"]
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
