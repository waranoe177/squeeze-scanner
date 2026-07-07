"""On-demand Telegram chart bot: text a ticker, get its chart + signal read back.

Telegram allows only ONE getUpdates consumer per bot token, so this module is
the single poller. It drains updates once and dispatches each message:

  * a "go" / "pass" reply  -> the decision ledger (scanner.decisions)
  * a ticker request       -> render + send that symbol's chart

A request is a bare ticker (`NVDA`, `brk-b`, `uup`) or `/chart NVDA` / `chart
NVDA`. Any ticker is allowed, in your universe or not. The reply is the
TOS-matched chart plus a caption with the same read fired names get: direction,
conviction score, key levels, and which of the seven buy conditions are lit.

The offset state is shared with decisions (`ledger/telegram_state.json`) so
exactly-once holds across both handlers. Run it two ways:

    python -m scanner.bot            # one drain — for the frequent Actions cron
    python -m scanner.bot --serve    # long-poll loop — instant replies, local

Only ONE poller may run at a time (the single-consumer rule above).
"""

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

from scanner import chart, data, decisions, notify, score, signals

# A ticker: letter-led, then letters/digits and the few punctuation marks real
# symbols use (BRK-B, BRK.B, GC=F, ^VIX). Up to 12 chars total.
_TICKER = r"[A-Za-z][A-Za-z0-9.\-=^]{0,11}"
_CMD = re.compile(rf"^(?:/?chart\s+)?({_TICKER})$", re.IGNORECASE)
# Words that are commands/decisions, never chart requests.
_RESERVED = {"go", "pass", "skip", "chart", "help"}


def parse_command(update: dict) -> str | None:
    """Return the uppercased ticker if this update is a chart request, else None.

    Decisions ("go" / "pass" / "go SYM") and bare command words are rejected so
    they fall through to the decision handler.
    """
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    if not text:
        return None
    m = _CMD.match(text)
    if not m:
        return None
    sym = m.group(1).upper()
    if sym.lower() in _RESERVED:
        return None
    return sym


def build_summary(symbol: str, df) -> str:
    """One-caption read of the latest bar: direction, score, levels, condition
    ladder. HTML (Telegram parse_mode), comfortably under the 1024-char cap."""
    sig = signals.latest_signal(df, symbol=symbol)
    conv = score.conviction(df, symbol=symbol)
    bd = signals.condition_breakdown(df)

    direction = sig["direction"]
    arrow = {"bull": "🟢 BUY", "bear": "🔴 SELL", "none": "⚪ no signal"}[direction]
    lit = sig["lit_bear"] if direction == "bear" else sig["lit_bull"]

    checks = [
        ("Sqz", bd["squeeze_on"]), ("RSI&gt;50", bd["rsi_pass"]),
        ("PPO≥0", bd["ppo_pass"]), ("8&gt;21", bd["structure_pass"]),
        ("Stack", bd["stack_pass"]), ("MACD", bd["macd_pass"]),
        ("Moxie", bd["moxie_pass"]),
    ]
    ladder = " ".join(f"{'✅' if ok else '▫️'}{name}" for name, ok in checks)

    return "\n".join([
        f"{arrow} <b>{notify._esc(symbol)}</b> · bar {sig['date']}",
        f"score {conv['score']:.0f}/100 ({conv['grade']}) · {lit}/7 lit · R:R {conv['rr']:.1f}",
        f"close {sig['close']:.2f} · RSI {sig['rsi']:.0f}",
        f"target {sig['target_up']:.2f} / {sig['target_dn']:.2f} · stop {sig['stop']:.2f}",
        ladder,
    ])


def handle_command(symbol: str, chat_id: str, token: str, *,
                   fetcher=None, renderer=None, send_photo=None,
                   send_message=None, tmp_dir=None) -> bool:
    """Fetch one symbol, render its chart, and send it back. Returns True on a
    chart send, False if there was no data (a text reply is sent instead).

    The four collaborators are injectable so the handler is unit-testable
    without network or matplotlib.
    """
    fetcher = fetcher or (lambda syms: data.fetch_daily(syms, period="2y"))
    renderer = renderer or chart.render_layers
    send_photo = send_photo or notify.send_photo
    send_message = send_message or notify.send_message

    frames = fetcher([symbol])
    df = frames.get(symbol)
    if df is None or getattr(df, "empty", True):
        send_message(token, chat_id, f"No data for {symbol} — check the ticker?")
        return False

    caption = build_summary(symbol, df)
    out_path = Path(tmp_dir or tempfile.gettempdir()) / f"req_{symbol}.png"
    renderer(df, symbol, str(out_path), lookback=140)
    send_photo(token, chat_id, str(out_path), caption=caption)
    return True


def _from_owner(update: dict, allowed_chat) -> bool:
    """Only act on messages from the configured owner chat (None = no filter)."""
    if not allowed_chat:
        return True
    chat = ((update.get("message") or {}).get("chat") or {}).get("id")
    if str(chat) != str(allowed_chat):
        print(f"  [bot] update from foreign chat {chat} ignored")
        return False
    return True


def poll_once(token=None, chat_id=None, ledger_path=None,
              state_path=decisions.DEFAULT_STATE_PATH, timeout: int = 0,
              command_handler=None) -> dict:
    """Drain updates once and dispatch: go/pass -> ledger, tickers -> charts.

    The ledger is saved BEFORE the offset (a crash between the two replays the
    batch, which write-once decisions and idempotent-enough chart resends
    absorb). Returns {updates, decisions, charts}.
    """
    from scanner import ledger

    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token:
        print("[bot] no TELEGRAM_BOT_TOKEN — skipping")
        return {"updates": 0, "decisions": 0, "charts": 0}

    ledger_path = ledger_path or ledger.DEFAULT_PATH
    command_handler = command_handler or (
        lambda sym: handle_command(sym, chat_id, token))

    state = decisions.load_state(state_path)
    updates, next_offset = decisions.fetch_updates(token, state["offset"], timeout=timeout)
    owned = [u for u in updates if _from_owner(u, chat_id)]

    # 1) decisions
    records = ledger.load(ledger_path)
    parsed = [p for p in (decisions.parse_decision(u) for u in owned) if p]
    decisions.apply_decisions(records, parsed)
    ledger.save(ledger_path, records)

    # 2) chart requests (anything not already a decision)
    charts = 0
    for u in owned:
        if decisions.parse_decision(u):
            continue
        sym = parse_command(u)
        if not sym:
            continue
        try:
            if command_handler(sym):
                charts += 1
        except Exception as exc:  # a bad request must not stall the poller
            print(f"  [bot] chart failed for {sym}: {exc}")
            try:
                notify.send_message(token, chat_id, f"Couldn't chart {sym}: {exc}")
            except Exception:
                pass

    decisions.save_state(state_path, {"offset": next_offset})
    print(f"[bot] {len(updates)} update(s), {len(parsed)} decision(s), {charts} chart(s)")
    return {"updates": len(updates), "decisions": len(parsed), "charts": charts}


def serve(token=None, chat_id=None, ledger_path=None,
          state_path=decisions.DEFAULT_STATE_PATH, poll_timeout: int = 25) -> None:
    """Long-poll loop for instant local replies. Ctrl-C to stop. getUpdates
    blocks up to `poll_timeout` seconds server-side, so this paces itself."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[bot] no TELEGRAM_BOT_TOKEN — cannot serve")
        return
    print("[bot] serve mode — long-polling for chart requests. Ctrl-C to stop.")
    while True:
        try:
            poll_once(token=token, chat_id=chat_id, ledger_path=ledger_path,
                      state_path=state_path, timeout=poll_timeout)
        except KeyboardInterrupt:
            print("\n[bot] stopped.")
            return
        except Exception as exc:  # transient network error — keep serving
            print(f"[bot] poll error (continuing): {exc}")


def main(argv=None) -> None:
    try:  # emoji in captions/prints choke cp1252 consoles otherwise
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Telegram chart-request bot")
    ap.add_argument("--serve", action="store_true",
                    help="long-poll loop for instant replies (local)")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--state", default=decisions.DEFAULT_STATE_PATH)
    args = ap.parse_args(argv)
    if args.serve:
        serve(ledger_path=args.ledger, state_path=args.state)
    else:
        poll_once(ledger_path=args.ledger, state_path=args.state)


if __name__ == "__main__":
    main()
