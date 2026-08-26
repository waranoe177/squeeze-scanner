"""Tests for the on-demand Telegram chart bot.

Covers command parsing (bare ticker + /chart prefix, decision/noise rejection),
the reply summary, the single-symbol handler, and the unified poll dispatch that
routes go/pass to decisions and ticker requests to the chart handler.
"""

from datetime import date

import numpy as np
import pandas as pd

from scanner import bot, decisions, ledger


def _update(text, uid=1, chat_id=1, reply_to=None, date=1767625200):
    """A minimal Telegram update (2026-01-05 15:00 UTC by default)."""
    msg = {"message_id": 900 + uid, "date": date, "text": text, "chat": {"id": chat_id}}
    if reply_to is not None:
        msg["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": uid, "message": msg}


def _ohlc(n=320, step=0.12, noise=0.3, seed=1):
    """Synthetic uptrending daily OHLC with enough history for SMA200/Moxie."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    close = pd.Series(50 + np.arange(n) * step + rng.normal(0, noise, n), index=idx)
    return pd.DataFrame(
        {"open": close.shift(1).fillna(close.iloc[0]), "high": close + 0.6,
         "low": close - 0.6, "close": close, "volume": 1_000_000}, index=idx)


# ---- parse_command --------------------------------------------------------

def test_parse_bare_ticker():
    assert bot.parse_command(_update("nvda")) == "NVDA"
    assert bot.parse_command(_update("BRK-B")) == "BRK-B"
    assert bot.parse_command(_update("  uup ")) == "UUP"


def test_parse_with_chart_prefix():
    assert bot.parse_command(_update("/chart tsla")) == "TSLA"
    assert bot.parse_command(_update("chart tsla")) == "TSLA"


def test_parse_ignores_decisions_and_noise():
    for t in ["go", "pass", "skip", "go tsla", "chart", "/chart", "help",
              "looks great!", "buy this now"]:
        assert bot.parse_command(_update(t)) is None, t
    assert bot.parse_command({"update_id": 5}) is None  # no message at all


# ---- build_summary --------------------------------------------------------

def test_build_summary_has_key_facts():
    s = bot.build_summary("TEST", _ohlc())
    assert "TEST" in s
    assert "score" in s.lower()
    assert "close" in s.lower()
    assert any(m in s for m in ["BUY", "SELL", "no signal"])


# ---- handle_command -------------------------------------------------------

def test_handle_command_sends_chart():
    sent = {}

    def fake_photo(token, chat, path, caption=""):
        sent["path"], sent["caption"] = path, caption
        return {"ok": True}

    ok = bot.handle_command(
        "TEST", chat_id="1", token="T",
        fetcher=lambda syms: {"TEST": _ohlc()},
        renderer=lambda df, sym, path, lookback=140: open(path, "wb").close(),
        send_photo=fake_photo,
        send_message=lambda *a, **k: None,
    )
    assert ok is True
    assert "TEST" in sent["caption"]


def test_handle_command_no_data_replies_text():
    msgs, drew = [], []
    ok = bot.handle_command(
        "ZZZZ", chat_id="1", token="T",
        fetcher=lambda syms: {},
        renderer=lambda *a, **k: drew.append(1),
        send_photo=lambda *a, **k: drew.append(1),
        send_message=lambda token, chat, text: msgs.append(text),
    )
    assert ok is False
    assert drew == []                 # never rendered or sent a photo
    assert "ZZZZ" in msgs[0]


# ---- poll_once (unified dispatch) -----------------------------------------

def _rec(symbol="TSLA", signal_date="2026-01-05", msg_id=123):
    return {"id": f"{symbol}-{signal_date}", "schema_version": 1, "symbol": symbol,
            "direction": "bull", "signal_date": signal_date, "signal_close": 100.0,
            "atr": 2.0, "ema21": 99.0, "conviction_score": 80.0,
            "telegram_msg_id": msg_id, "status": "open", "entry": 101.0,
            "entry_date": "2026-01-06", "stop": 98.0, "target": 106.0,
            "exit_price": None, "exit_date": None, "r_multiple": None}


def test_poll_once_dispatches_decision_and_chart(tmp_path, monkeypatch):
    lpath, spath = tmp_path / "signals.jsonl", tmp_path / "state.json"
    ledger.save(lpath, [_rec(msg_id=123)])
    updates = [_update("go", uid=7, reply_to=123), _update("nvda", uid=8)]
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda token, offset, timeout=0: (updates, 9))

    handled = []
    res = bot.poll_once(token="T", chat_id="1", ledger_path=lpath, state_path=spath,
                        command_handler=lambda sym: handled.append(sym) or True)

    assert handled == ["NVDA"]                              # chart request routed
    assert ledger.load(lpath)[0]["decision"] == "go"        # go/pass still works
    assert decisions.load_state(spath) == {"offset": 9}     # offset advanced
    assert res["charts"] == 1 and res["decisions"] == 1


def test_poll_once_ignores_foreign_chat(tmp_path, monkeypatch):
    lpath, spath = tmp_path / "signals.jsonl", tmp_path / "state.json"
    ledger.save(lpath, [_rec(msg_id=123)])
    foreign = _update("nvda", uid=8, chat_id=999)
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda token, offset, timeout=0: ([foreign], 9))

    handled = []
    bot.poll_once(token="T", chat_id="1", ledger_path=lpath, state_path=spath,
                  command_handler=lambda sym: handled.append(sym) or True)

    assert handled == []                                    # foreign request dropped
    assert decisions.load_state(spath) == {"offset": 9}     # but still consumed


def test_poll_once_without_token_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    res = bot.poll_once(token=None, ledger_path=tmp_path / "l.jsonl",
                        state_path=tmp_path / "s.json")
    assert res["charts"] == 0 and res["decisions"] == 0


# ---- parse_trade ------------------------------------------------------------

def test_parse_trade_basic_and_overrides():
    assert bot.parse_trade(_update("trade nvda"))["symbol"] == "NVDA"
    t = bot.parse_trade(_update("trade nvda 65 risk=1000 dte=45 full"))
    assert t["symbol"] == "NVDA" and abs(t["p"] - 0.65) < 1e-9
    assert t["risk"] == 1000.0 and t["dte"] == 45 and t["full"] is True
    assert bot.parse_trade(_update("/trade tsla p=70"))["p"] == 0.70


def test_parse_trade_rejects_non_trade_and_bad_symbol():
    assert bot.parse_trade(_update("nvda")) is None          # bare ticker = chart
    assert bot.parse_trade(_update("trade")) is None          # no symbol
    assert bot.parse_trade(_update("go tsla")) is None
    assert bot.parse_trade({"update_id": 5}) is None          # no message


def test_parse_trade_reply_carries_caption_no_symbol():
    upd = {"message": {"text": "trade risk=1000",
                       "reply_to_message": {"caption": "🟢 BUY V · bar 2026-08-25\nclose 384.14\ntarget 401.85 / 366.45 · stop 373.52"}}}
    t = bot.parse_trade(upd)
    assert t is not None and t["symbol"] is None
    assert t["risk"] == 1000 and t["caption"].startswith("🟢 BUY V")


def test_parse_trade_bare_symbol_unchanged():
    upd = {"message": {"text": "trade V 65"}}
    t = bot.parse_trade(upd)
    assert t["symbol"] == "V" and t["p"] == 0.65 and t["caption"] is None


def test_trade_is_reserved_word():
    assert bot.parse_command({"message": {"text": "trade"}}) is None


# ---- handle_trade + poll dispatch -------------------------------------------

def _ohlc_up(n=320):
    idx = pd.bdate_range("2023-01-02", periods=n)
    close = pd.Series(50 + np.arange(n) * 0.12, index=idx)
    return pd.DataFrame({"open": close, "high": close + 0.6, "low": close - 0.6,
                         "close": close, "volume": 1_000_000}, index=idx)


def _fixed_signal(direction, close=88.28, atr=1.2, ema21=87.0):
    """A `latest_signal`-shaped payload with a forced direction, so bare-path
    card tests can check bull/bear formatting without hand-tuning raw OHLC to
    satisfy all seven live squeeze conditions."""
    return {"symbol": "TEST", "date": "2026-08-19", "direction": direction,
            "grade": "A", "close": close, "rsi": 62.0 if direction == "bull" else 38.0,
            "ppo": 0.8 if direction == "bull" else -0.8, "squeeze_on": True,
            "moxie_w": 1.0, "atr": atr, "ema21": ema21, "lit_bull": 6, "lit_bear": 0,
            "target_up": round(ema21 + atr * 2.5, 4), "target_dn": round(ema21 - atr * 2.5, 4),
            "stop": round(close - atr * 1.5, 4)}


def test_handle_trade_sends_decision_message(monkeypatch):
    sent, photos = [], []
    monkeypatch.setattr(bot.signals, "latest_signal",
                        lambda df, symbol=None: _fixed_signal("bull"))

    def fake_chain(symbol):
        return {"expiries": [{"expiry": "2026-09-23",
                "calls": [{"strike": 88.0, "bid": 3.0, "ask": 3.2, "last": 3.1,
                           "iv": 0.42}], "puts": []}]}

    ok = bot.handle_trade(
        {"symbol": "TEST", "p": 0.6, "risk": 840.0, "dte": 35, "full": False,
         "caption": None},
        chat_id="1", token="T",
        fetcher=lambda syms: {"TEST": _ohlc_up()},
        chain_fetcher=fake_chain,
        send_message=lambda tok, cid, text: sent.append(text),
        renderer=lambda df, sym, path, lookback=140: open(path, "wb").close(),
        send_photo=lambda tok, cid, path, caption="": photos.append(caption),
        asof=date(2026, 8, 19),
    )
    assert ok is True
    assert "TEST" in photos[0]                              # chart sent from the one eval
    assert "TEST" in sent[0] and ("BUY" in sent[0] or "SHARES" in sent[0])


def test_handle_trade_no_data_replies_text():
    sent = []
    ok = bot.handle_trade(
        {"symbol": "ZZZZ", "p": None, "risk": None, "dte": None, "full": False},
        chat_id="1", token="T",
        fetcher=lambda syms: {},
        chain_fetcher=lambda s: None,
        send_message=lambda tok, cid, text: sent.append(text),
        asof=date(2026, 8, 19),
    )
    assert ok is False and "ZZZZ" in sent[0]


def _ohlc_down(n=320):
    idx = pd.bdate_range("2023-01-02", periods=n)
    close = pd.Series(200 - np.arange(n) * 0.12, index=idx)
    return pd.DataFrame({"open": close, "high": close + 0.6, "low": close - 0.6,
                         "close": close, "volume": 1_000_000}, index=idx)


def test_handle_trade_bear_stop_is_above_entry_and_kill_above(monkeypatch):
    sent = []
    monkeypatch.setattr(bot.signals, "latest_signal",
                        lambda df, symbol=None: _fixed_signal(
                            "bear", close=44.7, atr=1.0, ema21=45.5))

    def fake_chain(symbol):
        return {"expiries": [{"expiry": "2026-09-23", "calls": [],
                "puts": [{"strike": 44.0, "bid": 3.0, "ask": 3.2, "last": 3.1,
                          "iv": 0.42}]}]}

    ok = bot.handle_trade(
        {"symbol": "TEST", "p": 0.6, "risk": 840.0, "dte": 35, "full": False,
         "caption": None},
        chat_id="1", token="T",
        fetcher=lambda syms: {"TEST": _ohlc_down()},
        chain_fetcher=fake_chain,
        send_message=lambda tok, cid, text: sent.append(text),
        renderer=lambda df, sym, path, lookback=140: open(path, "wb").close(),
        send_photo=lambda tok, cid, path, caption="": None,
        asof=date(2026, 8, 19),
    )
    assert ok is True
    msg = sent[0]
    assert "TEST" in msg
    assert "Kill above" in msg
    assert "Kill below" not in msg
    if "SHORT SHARES" in msg or "BUY PUTS" in msg:
        pass  # direction-appropriate action rendered
    else:
        raise AssertionError(f"expected a short-side action in: {msg}")


def _fake_df_fetcher():
    """Generic fetcher usable for any requested symbol — reply-path
    handle_trade only needs the df for options.realized_vol."""
    return lambda syms: {sym: _ohlc_up() for sym in syms}


def test_handle_trade_reply_uses_caption_direction_never_inverts():
    sent = {}

    def fake_send(token, chat, msg):
        sent["msg"] = msg

    opts = {"symbol": None, "p": None, "risk": 500.0, "dte": None, "full": False,
            "caption": "🟢 BUY V · bar 2026-08-25\nclose 384.14\ntarget 401.85 / 366.45 · stop 373.52"}
    bot.handle_trade(opts, "chat", "tok", fetcher=_fake_df_fetcher(),
                     chain_fetcher=lambda s: None, send_message=fake_send,
                     asof=date(2026, 8, 19))
    assert "BUY" in sent["msg"] and "SHORT" not in sent["msg"]
    assert "follows your V chart · bar 2026-08-25" in sent["msg"]


def test_handle_trade_reply_unparseable_caption_sends_fallback():
    sent = {}
    opts = {"symbol": None, "p": None, "risk": 500.0, "dte": None, "full": False,
            "caption": "hello there"}
    bot.handle_trade(opts, "chat", "tok", send_message=lambda t, c, m: sent.setdefault("m", m),
                     asof=date(2026, 8, 19))
    assert "trade SYM" in sent["m"]


def test_acceptance_buy_caption_never_yields_short_card():
    # The V/MA regression: a BUY caption must never produce a SHORT card.
    sent = {}
    opts = {"symbol": None, "p": None, "risk": 500.0, "dte": None, "full": False,
            "caption": "🟢 BUY V — Visa Inc. · score 91/100 (A+) · bar 2026-08-25\n"
                       "close 384.14 · RSI 71\ntarget 401.85 · stop 373.52 (finalize at next open)"}
    bot.handle_trade(opts, "chat", "tok", fetcher=_fake_df_fetcher(),
                     chain_fetcher=lambda s: None,
                     send_message=lambda t, c, m: sent.setdefault("m", m),
                     asof=date(2026, 8, 19))
    m = sent["m"]
    assert "SHORT" not in m and "SELL" not in m
    assert "BUY" in m and "401" in m and "373" in m    # inherited target + stop


def test_poll_once_routes_trade(tmp_path, monkeypatch):
    lpath, spath = tmp_path / "l.jsonl", tmp_path / "s.json"
    ledger.save(lpath, [])
    monkeypatch.setattr(decisions, "fetch_updates",
                        lambda token, offset, timeout=0: ([_update("trade nvda 60", uid=3)], 4))
    routed = []
    bot.poll_once(token="T", chat_id="1", ledger_path=lpath, state_path=spath,
                  command_handler=lambda sym: routed.append(("chart", sym)) or True,
                  trade_handler=lambda opts: routed.append(("trade", opts["symbol"])) or True)
    assert ("trade", "NVDA") in routed
    assert not any(r[0] == "chart" for r in routed)   # trade did NOT fall through to chart
