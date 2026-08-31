"""Tests for the on-demand Telegram chart bot.

Covers command parsing (bare ticker + /chart prefix, decision/noise rejection),
the reply summary, the single-symbol handler, and the unified poll dispatch that
routes go/pass to decisions and ticker requests to the chart handler.
"""

from datetime import date
from pathlib import Path

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


def test_build_summary_bear_stop_is_above_close_not_below():
    # Regression: latest_signal's "stop" field is always the below-price
    # long-side figure (close - 1.5*ATR). For a bear signal the real kill
    # level is above price (close + 1.5*ATR), matching what the trade card
    # prints ("Kill above") and what a later reply must inherit.
    sig = {"symbol": "TEST", "date": "2026-08-19", "direction": "bear",
           "grade": "A", "close": 100.0, "rsi": 30.0, "ppo": -0.8,
           "squeeze_on": True, "moxie_w": -1.0, "atr": 2.0, "ema21": 101.0,
           "lit_bull": 0, "lit_bear": 6, "target_up": 108.5, "target_dn": 96.0,
           "stop": 97.0}  # latest_signal's own (wrong-for-display) below-price figure
    conv = {"score": 80.0, "grade": "A", "rr": 2.0}
    caption = bot.build_summary("TEST", _ohlc_up(), sig=sig, conv=conv)
    assert "stop 103.00" in caption          # close + 1.5*ATR, above price
    assert "stop 97.00" not in caption       # not latest_signal's below-price figure


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


def test_handle_trade_sends_decision_message(monkeypatch):
    # Anchored bare path: direction/levels come from results.json, not a
    # fresh latest_signal eval.
    results = {"as_of": "2026-08-19", "fired": [
        {"symbol": "TEST", "direction": "bull", "close": 88.28, "rsi": 62.0,
         "date": "2026-08-19", "score": 84, "conviction_grade": "A", "atr": 1.2,
         "prov_target": 95.0, "prov_stop": 86.48, "chart": "charts/TEST.png"}]}
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: results)
    sent = []

    def fake_chain(symbol):
        return {"expiries": [{"expiry": "2026-09-23",
                "calls": [{"strike": 88.0, "bid": 3.0, "ask": 3.2, "last": 3.1,
                           "iv": 0.42}], "puts": []}]}

    ok = bot.handle_trade(
        {"symbol": "TEST", "p": 0.6, "risk": 840.0, "dte": 35, "full": False,
         "caption": None},
        chat_id="1", token="T",
        fetcher=lambda syms: {"TEST": _ohlc_up()},   # last close 88.28 == anchored entry
        chain_fetcher=fake_chain,
        send_message=lambda tok, cid, text: sent.append(text),
        send_photo=lambda tok, cid, path, caption="": None,
        asof=date(2026, 8, 19),
    )
    assert ok is True
    assert "TEST" in sent[0] and ("BUY" in sent[0] or "SHARES" in sent[0])


def test_handle_trade_bare_offlist_symbol_refuses(monkeypatch):
    # Renamed from test_handle_trade_no_data_replies_text: this doesn't test
    # "no data" (it never reaches the fetcher) — it's the off-list refuse+
    # redirect path. Explicitly mocks _load_results so it doesn't depend on
    # ZZZZ happening to be absent from the real out/results.json.
    results = {"as_of": "2026-08-19", "fired": [
        {"symbol": "TEST", "direction": "bull", "close": 88.28, "rsi": 62.0,
         "date": "2026-08-19", "score": 84, "conviction_grade": "A", "atr": 1.2,
         "prov_target": 95.0, "prov_stop": 86.48, "chart": "charts/TEST.png"}]}
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: results)
    sent = []
    ok = bot.handle_trade(
        {"symbol": "ZZZZ", "p": None, "risk": None, "dte": None, "full": False},
        chat_id="1", token="T",
        fetcher=lambda syms: {},
        chain_fetcher=lambda s: None,
        send_message=lambda tok, cid, text: sent.append(text),
        asof=date(2026, 8, 19),
    )
    assert ok is False and "ZZZZ" in sent[0] and "no active signal" in sent[0].lower()


def test_handle_trade_bare_empty_live_df_refuses_no_card(monkeypatch):
    # I3: an anchored symbol whose live fetch comes back empty must refuse
    # (freshness can't be checked, rv=0.0 would degrade option pricing) —
    # never price a degenerate card.
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: _APD_RESULTS)
    sent = []
    ok = bot.handle_trade(
        {"symbol": "APD", "p": None, "risk": 500.0, "dte": None, "full": False,
         "caption": None},
        chat_id="1", token="T",
        fetcher=lambda syms: {},   # empty dict -> df is None
        chain_fetcher=lambda s: None,
        send_message=lambda tok, cid, text: sent.append(text),
        send_photo=lambda *a, **k: None,
        asof=date(2026, 8, 30),
    )
    assert ok is False
    msg = sent[0]
    assert "couldn't fetch live price" in msg.lower() and "APD" in msg
    assert "BUY" not in msg and "SKIP" not in msg


def test_handle_trade_bare_empty_frame_also_refuses(monkeypatch):
    # Same as above but the fetcher returns an empty (not missing) frame.
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: _APD_RESULTS)
    sent = []
    ok = bot.handle_trade(
        {"symbol": "APD", "p": None, "risk": 500.0, "dte": None, "full": False,
         "caption": None},
        chat_id="1", token="T",
        fetcher=lambda syms: {"APD": pd.DataFrame(columns=["open", "high", "low", "close", "volume"])},
        chain_fetcher=lambda s: None,
        send_message=lambda tok, cid, text: sent.append(text),
        send_photo=lambda *a, **k: None,
        asof=date(2026, 8, 30),
    )
    assert ok is False and "couldn't fetch live price" in sent[0].lower()


def test_handle_trade_bare_chart_send_guarded_on_missing_file(monkeypatch):
    # m4: a payload whose chart doesn't exist on disk must not call
    # send_photo — the card still sends.
    results = {"as_of": "2026-08-28", "fired": [
        {"symbol": "APD", "direction": "bull", "close": 308.09, "rsi": 58.0,
         "date": "2026-08-28", "score": 84, "conviction_grade": "A", "atr": 6.0,
         "prov_target": 323.69, "prov_stop": 298.73,
         "chart": "charts/DOES_NOT_EXIST_XYZ.png"}]}
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: results)
    sent, photos = [], []
    ok = bot.handle_trade(
        {"symbol": "APD", "p": None, "risk": 500.0, "dte": None, "full": False,
         "caption": None},
        chat_id="1", token="T",
        fetcher=_fake_df_fetcher(spot=308.0),
        chain_fetcher=lambda s: None,
        send_message=lambda tok, cid, text: sent.append(text),
        send_photo=lambda *a, **k: photos.append(a),
        asof=date(2026, 8, 30),
    )
    assert ok is True
    assert photos == []          # never sent — file doesn't exist
    assert "BUY" in sent[0]      # card still sent


def test_handle_trade_bare_chart_sent_when_file_exists(monkeypatch):
    # m4 (b): when the chart file exists, send_photo IS called. Creates its
    # own throwaway file under out/charts/ rather than relying on a
    # pre-existing committed chart (the cpath prefix is the hardcoded "out/",
    # not injectable, so this is the only way to control existence cleanly).
    chart_dir = Path("out/charts")
    chart_dir.mkdir(parents=True, exist_ok=True)
    chart_path = chart_dir / "TEST_M4_EXISTS.png"
    chart_path.write_bytes(b"\x89PNG\r\n")
    try:
        results = {"as_of": "2026-08-28", "fired": [
            {"symbol": "APD", "direction": "bull", "close": 308.09, "rsi": 58.0,
             "date": "2026-08-28", "score": 84, "conviction_grade": "A", "atr": 6.0,
             "prov_target": 323.69, "prov_stop": 298.73,
             "chart": "charts/TEST_M4_EXISTS.png"}]}
        monkeypatch.setattr(bot, "_load_results", lambda *a, **k: results)
        sent, photos = [], []
        ok = bot.handle_trade(
            {"symbol": "APD", "p": None, "risk": 500.0, "dte": None, "full": False,
             "caption": None},
            chat_id="1", token="T",
            fetcher=_fake_df_fetcher(spot=308.0),
            chain_fetcher=lambda s: None,
            send_message=lambda tok, cid, text: sent.append(text),
            send_photo=lambda *a, **k: photos.append(a),
            asof=date(2026, 8, 30),
        )
        assert ok is True
        assert len(photos) == 1
        assert "TEST_M4_EXISTS" in photos[0][2]   # (token, chat_id, path, ...)
    finally:
        chart_path.unlink(missing_ok=True)


def test_handle_trade_bear_stop_is_above_entry_and_kill_above(monkeypatch):
    # Anchored bare path, bear direction: levels come from results.json.
    results = {"as_of": "2026-08-19", "fired": [
        {"symbol": "TEST", "direction": "bear", "close": 44.7, "rsi": 38.0,
         "date": "2026-08-19", "score": 84, "conviction_grade": "A", "atr": 1.0,
         "prov_target": 43.0, "prov_stop": 46.2, "chart": "charts/TEST.png"}]}
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: results)
    sent = []

    def fake_chain(symbol):
        return {"expiries": [{"expiry": "2026-09-23", "calls": [],
                "puts": [{"strike": 44.0, "bid": 3.0, "ask": 3.2, "last": 3.1,
                          "iv": 0.42}]}]}

    ok = bot.handle_trade(
        {"symbol": "TEST", "p": 0.6, "risk": 840.0, "dte": 35, "full": False,
         "caption": None},
        chat_id="1", token="T",
        fetcher=_fake_df_fetcher(spot=44.7),   # last close == anchored entry
        chain_fetcher=fake_chain,
        send_message=lambda tok, cid, text: sent.append(text),
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


def _fake_df_fetcher(spot=None):
    """Generic fetcher usable for any requested symbol.

    With no `spot`, returns the shared uptrending fixture — reply-path
    handle_trade only needs *a* frame for options.realized_vol. With `spot=`,
    returns a tiny ascending-close frame whose LAST close == spot, for the
    bare-anchor path where the live close also feeds the Task-5 freshness
    guard (`df["close"].iloc[-1]`).
    """
    if spot is None:
        return lambda syms: {sym: _ohlc_up() for sym in syms}

    def _mk():
        idx = pd.bdate_range("2026-07-01", periods=30)
        close = pd.Series(np.linspace(spot - 6, spot, 30), index=idx)
        return pd.DataFrame({"open": close, "high": close + 0.3, "low": close - 0.3,
                             "close": close, "volume": 1_000_000}, index=idx)
    return lambda syms: {sym: _mk() for sym in syms}


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


def test_handle_trade_reply_sizes_from_captions_own_score_not_default():
    # Regression: a `trade` REPLY must size using the score printed IN the
    # caption (score 91/100), not the conviction_to_p(50) default — otherwise
    # a reply to a 91/100 chart sizes at ~35% while a bare `trade V` on the
    # SAME chart sizes at ~70%: two confidences for one signal.
    sent = {}
    opts = {"symbol": None, "p": None, "risk": 500.0, "dte": None, "full": False,
            "caption": "🟢 BUY V · bar 2026-08-25\nscore 91/100 (A+) · 7/7 lit · R:R 1.5\n"
                       "close 384.14\ntarget 401.85 / 366.45 · stop 373.52"}
    bot.handle_trade(opts, "chat", "tok", fetcher=_fake_df_fetcher(),
                     chain_fetcher=lambda s: None,
                     send_message=lambda t, c, m: sent.setdefault("m", m),
                     asof=date(2026, 8, 19))
    from scanner import options as opt_mod
    assert f"~{round(opt_mod.conviction_to_p(91.0) * 100)}%" in sent["m"]
    assert f"~{round(opt_mod.conviction_to_p(50) * 100)}%" not in sent["m"]


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


def test_handle_trade_bare_sizes_from_real_conviction_score_not_default(monkeypatch):
    # Regression: the anchored bare path must feed options.decide the REAL
    # conviction score from results.json (via the parsed anchor caption's
    # `score`) when no `p=` override is given. A 95/100 A+ setup must NOT get
    # sized as if it were the score=50 default (conviction_to_p(50) = 0.35 vs
    # conviction_to_p(95) clamped to 0.70 — unmistakably different confidence
    # lines on the card).
    results = {"as_of": "2026-08-19", "fired": [
        {"symbol": "TEST", "direction": "bull", "close": 88.28, "rsi": 62.0,
         "date": "2026-08-19", "score": 95, "conviction_grade": "A+", "atr": 1.2,
         "prov_target": 95.0, "prov_stop": 86.48, "chart": "charts/TEST.png"}]}
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: results)
    sent = []

    ok = bot.handle_trade(
        {"symbol": "TEST", "p": None, "risk": None, "dte": None, "full": False,
         "caption": None},
        chat_id="1", token="T",
        fetcher=lambda syms: {"TEST": _ohlc_up()},   # last close 88.28 == anchored entry
        chain_fetcher=lambda s: None,
        send_message=lambda tok, cid, text: sent.append(text),
        send_photo=lambda tok, cid, path, caption="": None,
        asof=date(2026, 8, 19),
    )
    assert ok is True
    from scanner import options as opt_mod
    assert f"~{round(opt_mod.conviction_to_p(95.0) * 100)}%" in sent[0]
    assert f"~{round(opt_mod.conviction_to_p(50) * 100)}%" not in sent[0]


# ---- anchored bare trade: no re-derivation, refuse+redirect off-list -------

_APD_RESULTS = {"as_of": "2026-08-28", "fired": [
    {"symbol": "APD", "direction": "bull", "close": 308.09, "rsi": 58.0,
     "date": "2026-08-28", "score": 84, "conviction_grade": "A", "atr": 6.0,
     "prov_target": 323.69, "prov_stop": 298.73, "chart": "charts/APD.png"}]}


def test_bare_trade_anchors_buy_never_contradicts(monkeypatch):
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: _APD_RESULTS)
    sent = {}
    bot.handle_trade({"symbol": "APD", "p": None, "risk": 500.0, "dte": None,
                      "full": False, "caption": None},
                     "chat", "tok", fetcher=_fake_df_fetcher(spot=308.0),
                     chain_fetcher=lambda s: None,
                     send_message=lambda t, c, m: sent.setdefault("m", m),
                     send_photo=lambda *a, **k: None,
                     asof=date(2026, 8, 30))
    m = sent["m"]
    assert "BUY" in m and "SHORT" not in m and "SELL" not in m
    assert "323" in m and "298" in m                    # anchored prov levels
    assert "follows the 2026-08-28 scan signal" in m    # vintage


def test_bare_trade_offlist_refuses_and_redirects(monkeypatch):
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: _APD_RESULTS)
    sent = {}
    bot.handle_trade({"symbol": "TSLA", "p": None, "risk": 500.0, "dte": None,
                      "full": False, "caption": None}, "chat", "tok",
                     send_message=lambda t, c, m: sent.setdefault("m", m),
                     asof=date(2026, 8, 30))
    assert "no active signal" in sent["m"].lower() and "chart TSLA" in sent["m"]


def test_bare_trade_missing_results_refuses(monkeypatch):
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: None)
    sent = {}
    bot.handle_trade({"symbol": "APD", "p": None, "risk": 500.0, "dte": None,
                      "full": False, "caption": None}, "chat", "tok",
                     send_message=lambda t, c, m: sent.setdefault("m", m),
                     asof=date(2026, 8, 30))
    assert "no active signal" in sent["m"].lower()


def test_bare_trade_does_not_rederive_direction(monkeypatch):
    # A live fetch on a DIFFERENT (bearish) bar must not change the anchored BUY.
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: _APD_RESULTS)
    monkeypatch.setattr(bot.signals, "latest_signal",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-derived!")))
    sent = {}
    bot.handle_trade({"symbol": "APD", "p": None, "risk": 500.0, "dte": None,
                      "full": False, "caption": None}, "chat", "tok",
                     fetcher=_fake_df_fetcher(spot=308.0), chain_fetcher=lambda s: None,
                     send_message=lambda t, c, m: sent.setdefault("m", m),
                     send_photo=lambda *a, **k: None, asof=date(2026, 8, 30))
    assert "BUY" in sent["m"]   # latest_signal never called -> no AssertionError


# ---- anchored bare trade: freshness guard -----------------------------------

def test_bare_trade_refuses_when_price_moved_past_target(monkeypatch):
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: _APD_RESULTS)
    sent = {}
    # anchored entry 308.09, target 323.69, atr 6.0; live spot 330 => crossed target
    bot.handle_trade({"symbol": "APD", "p": None, "risk": 500.0, "dte": None,
                      "full": False, "caption": None}, "chat", "tok",
                     fetcher=_fake_df_fetcher(spot=330.0), chain_fetcher=lambda s: None,
                     send_message=lambda t, c, m: sent.setdefault("m", m),
                     send_photo=lambda *a, **k: None, asof=date(2026, 8, 30))
    m = sent["m"]
    assert "2026-08-28" in m and "moved" in m.lower()
    assert "BUY" not in m and "SKIP" not in m           # no card priced


def test_bare_trade_prices_card_when_price_near_entry(monkeypatch):
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: _APD_RESULTS)
    sent = {}
    bot.handle_trade({"symbol": "APD", "p": None, "risk": 500.0, "dte": None,
                      "full": False, "caption": None}, "chat", "tok",
                     fetcher=_fake_df_fetcher(spot=309.0), chain_fetcher=lambda s: None,
                     send_message=lambda t, c, m: sent.setdefault("m", m),
                     send_photo=lambda *a, **k: None, asof=date(2026, 8, 30))
    assert "BUY" in sent["m"]                            # within 1 ATR -> card priced


# ---- acceptance: APD anchors, no stale-bar contradiction --------------------

def test_acceptance_apd_bare_trade_matches_alert_not_stale_bar(monkeypatch):
    # The reported bug: daily BUY APD (bar 08-28) but bare trade said no-signal (08-27).
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: _APD_RESULTS)
    # even if a live eval would say "bear"/"none", anchoring must return BUY:
    monkeypatch.setattr(bot.signals, "latest_signal",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-derived!")))
    sent = {}
    bot.handle_trade({"symbol": "APD", "p": None, "risk": 500.0, "dte": None,
                      "full": False, "caption": None}, "chat", "tok",
                     fetcher=_fake_df_fetcher(spot=309.0), chain_fetcher=lambda s: None,
                     send_message=lambda t, c, m: sent.setdefault("m", m),
                     send_photo=lambda *a, **k: None, asof=date(2026, 8, 30))
    assert "BUY" in sent["m"] and "323" in sent["m"]   # anchored to the alert


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


def test_anchor_payload_found_and_missing(tmp_path):
    results = {"as_of": "2026-08-28", "fired": [
        {"symbol": "APD", "direction": "bull", "close": 308.09, "date": "2026-08-28",
         "score": 84, "conviction_grade": "A", "atr": 6.0, "ema21": 300.0,
         "target_up": 320.0, "target_dn": 286.0, "stop": 299.0,
         "prov_target": 323.69, "prov_stop": 298.73, "chart": "charts/APD.png"}]}
    assert bot._anchor_payload("APD", results)["prov_target"] == 323.69
    assert bot._anchor_payload("TSLA", results) is None
    assert bot._anchor_payload("APD", None) is None


def test_load_results_missing_file_returns_none(tmp_path):
    assert bot._load_results(str(tmp_path / "nope.json")) is None


def test_anchor_caption_roundtrips_to_prov_levels():
    from scanner import captionparse as cp
    p = {"symbol": "APD", "direction": "bull", "close": 308.09, "rsi": 58.0,
         "date": "2026-08-28", "score": 84, "conviction_grade": "A",
         "prov_target": 323.69, "prov_stop": 298.73}
    parsed = cp.parse_caption(bot._anchor_caption(p))
    assert parsed["symbol"] == "APD" and parsed["direction"] == "bull"
    assert parsed["entry"] == 308.09
    assert parsed["target"] == 323.69 and parsed["stop"] == 298.73  # prov_*, not raw
    assert parsed["bar_date"] == "2026-08-28"
    assert parsed["score"] == 84.0
