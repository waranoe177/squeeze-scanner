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
from datetime import date
from pathlib import Path

from scanner import captionparse, chart, data, decisions, notify, optfmt, options, score, signals

# A ticker: letter-led, then letters/digits and the few punctuation marks real
# symbols use (BRK-B, BRK.B, GC=F, ^VIX). Up to 12 chars total.
_TICKER = r"[A-Za-z][A-Za-z0-9.\-=^]{0,11}"
_CMD = re.compile(rf"^(?:/?chart\s+)?({_TICKER})$", re.IGNORECASE)
# Words that are commands/decisions, never chart requests.
_RESERVED = {"go", "pass", "skip", "chart", "help", "trade"}


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


def build_summary(symbol: str, df, sig=None, conv=None) -> str:
    """One-caption read of the latest bar: direction, score, levels, condition
    ladder. HTML (Telegram parse_mode), comfortably under the 1024-char cap.

    `sig`/`conv` are optionally precomputed by the caller (e.g. handle_trade's
    bare path, which needs the same latest_signal/conviction read for its
    card) so this doesn't silently re-run those evals a second time.
    """
    sig = sig if sig is not None else signals.latest_signal(df, symbol=symbol)
    conv = conv if conv is not None else score.conviction(df, symbol=symbol)
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

    # latest_signal's own "stop" field is always the below-price long-side
    # figure (close - 1.5*ATR); a bear signal's actual kill level is above
    # price (close + 1.5*ATR). Display-only — latest_signal itself is left
    # unchanged since it feeds the ledger/daily scan, not just this caption.
    display_stop = (sig["close"] + sig["atr"] * 1.5 if direction == "bear"
                    else sig["stop"])

    return "\n".join([
        f"{arrow} <b>{notify._esc(symbol)}</b> · bar {sig['date']}",
        f"score {conv['score']:.0f}/100 ({conv['grade']}) · {lit}/7 lit · R:R {conv['rr']:.1f}",
        f"close {sig['close']:.2f} · RSI {sig['rsi']:.0f}",
        f"target {sig['target_up']:.2f} / {sig['target_dn']:.2f} · stop {display_stop:.2f}",
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
              command_handler=None, trade_handler=None) -> dict:
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
    trade_handler = trade_handler or (
        lambda opts: handle_trade(opts, chat_id, token))

    state = decisions.load_state(state_path)
    updates, next_offset = decisions.fetch_updates(token, state["offset"], timeout=timeout)
    owned = [u for u in updates if _from_owner(u, chat_id)]

    # 1) decisions
    records = ledger.load(ledger_path)
    parsed = [p for p in (decisions.parse_decision(u) for u in owned) if p]
    decisions.apply_decisions(records, parsed)
    ledger.save(ledger_path, records)

    # 2) trade requests + chart requests (anything not already a decision)
    charts = 0
    for u in owned:
        if decisions.parse_decision(u):
            continue
        t = parse_trade(u)
        if t:
            try:
                if trade_handler(t):
                    charts += 1
            except Exception as exc:  # a bad request must not stall the poller
                print(f"  [bot] trade failed for {t['symbol']}: {exc}")
                try:
                    notify.send_message(token, chat_id, f"Couldn't analyze {t['symbol']}: {exc}")
                except Exception:
                    pass
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


_TRADE_SYM = re.compile(r"^[A-Za-z][A-Za-z0-9.\-=^]{0,11}$")


def _to_p(tok):
    try:
        v = float(tok)
    except ValueError:
        return None
    v = v / 100.0 if v > 1 else v
    return max(0.01, min(0.99, v))


def _is_override_token(tok: str) -> bool:
    """True for a `trade` argument that's an override (conf/risk/dte/full),
    never a symbol — used to tell a reply's bare overrides from a bare SYM."""
    low = tok.lower()
    return (low == "full" or low.startswith("risk=") or low.startswith("dte=")
            or low.startswith("p=") or bool(re.fullmatch(r"\d{1,3}", tok)))


def _apply_overrides(opts: dict, tokens) -> None:
    for tok in tokens:
        low = tok.lower()
        if low == "full":
            opts["full"] = True
        elif low.startswith("risk="):
            try:
                opts["risk"] = float(tok[5:])
            except ValueError:
                pass
        elif low.startswith("dte="):
            try:
                opts["dte"] = int(tok[4:])
            except ValueError:
                pass
        elif low.startswith("p="):
            opts["p"] = _to_p(tok[2:])
        elif re.fullmatch(r"\d{1,3}", tok):
            opts["p"] = _to_p(tok)


def parse_trade(update: dict) -> dict | None:
    """Parse `trade SYM [CONF] [risk=N] [dte=N] [full]` (bare) or a `trade
    [CONF] [risk=N] [dte=N] [full]` reply to a chart (caption-anchored, no
    symbol token — the symbol/direction/levels come from the caption).
    None if not a trade at all.
    """
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    if not text:
        return None
    parts = text.split()
    if parts[0].lower() not in ("trade", "/trade"):
        return None
    rest = parts[1:]
    caption = (msg.get("reply_to_message") or {}).get("caption")

    if caption and all(_is_override_token(t) for t in rest):
        opts = {"symbol": None, "p": None, "risk": None, "dte": None,
                "full": False, "caption": caption}
        _apply_overrides(opts, rest)
        return opts

    if not rest or not _TRADE_SYM.match(rest[0]):
        return None
    opts = {"symbol": rest[0].upper(), "p": None, "risk": None,
            "dte": None, "full": False, "caption": None}
    _apply_overrides(opts, rest[1:])
    return opts


_TRADE_FALLBACK = ("Can't find that chart's signal — send `trade SYM` and "
                   "I'll pull a fresh one.")


def _decide_and_format(opts, symbol, direction, entry, target, stop, rv, *,
                       chain_fetcher, asof, conv_score=None):
    signal = {"symbol": symbol, "direction": direction, "entry": entry,
              "target": target, "stop": stop, "realized_vol": rv}
    if conv_score is not None:
        # Feeds options.decide's conviction_to_p fallback when no `p=`
        # override is given — omitting this silently sizes every trade
        # at conviction_to_p(50) regardless of the real score.
        signal["score"] = conv_score
    chain = chain_fetcher(symbol)
    plan = options.decide(
        signal, chain or {"expiries": []},
        p=opts.get("p"), risk_budget=opts.get("risk") or 500.0,
        target_dte=opts.get("dte") or 21, asof=asof)
    return (optfmt.format_trade_full(plan) if opts.get("full")
            else optfmt.format_trade(plan))


def _handle_trade_reply(opts, chat_id, token, caption_text, *, fetcher,
                        chain_fetcher, send_message, asof) -> bool:
    """Caption-anchored path: direction/entry/target/stop come ONLY from the
    replied-to chart's caption — never re-inferred from a fresh signal eval.
    That's the whole point: a BUY caption can never yield a SHORT card."""
    parsed = captionparse.parse_caption(caption_text)
    if parsed is None:
        send_message(token, chat_id, _TRADE_FALLBACK)
        return False

    symbol = parsed["symbol"]
    direction, entry = parsed["direction"], parsed["entry"]
    target, stop, bar_date = parsed["target"], parsed["stop"], parsed["bar_date"]

    frames = fetcher([symbol])
    df = frames.get(symbol)
    rv = options.realized_vol(df["close"]) if df is not None and not getattr(df, "empty", True) else 0.0

    msg = _decide_and_format(opts, symbol, direction, entry, target, stop, rv,
                             chain_fetcher=chain_fetcher, asof=asof,
                             conv_score=parsed.get("score"))
    vintage = f"follows your {symbol} chart · bar {bar_date} · target {target:.2f} / stop {stop:.2f}" \
              if bar_date else f"follows your {symbol} chart · target {target:.2f} / stop {stop:.2f}"
    send_message(token, chat_id, vintage + "\n" + msg)
    return True


def _handle_trade_bare(opts, chat_id, token, *, fetcher, chain_fetcher,
                       send_message, renderer, send_photo, tmp_dir,
                       asof) -> bool:
    """Bare `trade SYM`: ONE latest_signal eval feeds both the chart caption
    and the card — no second, possibly-divergent re-derivation."""
    symbol = opts["symbol"]
    frames = fetcher([symbol])
    df = frames.get(symbol)
    if df is None or getattr(df, "empty", True):
        send_message(token, chat_id, f"No data for {symbol} — check the ticker?")
        return False

    sig = signals.latest_signal(df, symbol=symbol)
    conv = score.conviction(df, symbol=symbol)
    caption = build_summary(symbol, df, sig=sig, conv=conv)
    out_path = Path(tmp_dir or tempfile.gettempdir()) / f"req_{symbol}.png"
    renderer(df, symbol, str(out_path), lookback=140)
    send_photo(token, chat_id, str(out_path), caption=caption)

    direction = sig["direction"]
    if direction == "none":
        send_message(token, chat_id, f"no active signal on {symbol}")
        return True

    stop = (sig["close"] - sig["atr"] * 1.5 if direction != "bear"
            else sig["close"] + sig["atr"] * 1.5)
    target = sig["target_up"] if direction != "bear" else sig["target_dn"]
    msg = _decide_and_format(opts, symbol, direction, sig["close"], target, stop,
                             options.realized_vol(df["close"]),
                             chain_fetcher=chain_fetcher, asof=asof,
                             conv_score=conv["score"])
    send_message(token, chat_id, msg)
    return True


def handle_trade(opts, chat_id, token, *, fetcher=None, chain_fetcher=None,
                 send_message=None, asof=None, renderer=None, send_photo=None,
                 tmp_dir=None) -> bool:
    """Compute + send the equity-vs-options decision for one ticker, either
    from a `trade` reply to a chart (caption-anchored) or a bare `trade SYM`
    (one fresh signal eval, chart + card from the same read).

    Collaborators are injectable so this is unit-testable without network.
    Returns True on a decision send, False when there's no price data (bare)
    or the replied-to caption couldn't be parsed (reply).
    """
    fetcher = fetcher or (lambda syms: data.fetch_daily(syms, period="2y"))
    chain_fetcher = chain_fetcher or options.fetch_chain
    send_message = send_message or notify.send_message
    renderer = renderer or chart.render_layers
    send_photo = send_photo or notify.send_photo
    asof = asof or date.today()

    caption_text = opts.get("caption")
    if caption_text:
        return _handle_trade_reply(opts, chat_id, token, caption_text,
                                   fetcher=fetcher, chain_fetcher=chain_fetcher,
                                   send_message=send_message, asof=asof)
    return _handle_trade_bare(opts, chat_id, token, fetcher=fetcher,
                              chain_fetcher=chain_fetcher, send_message=send_message,
                              renderer=renderer, send_photo=send_photo,
                              tmp_dir=tmp_dir, asof=asof)


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
