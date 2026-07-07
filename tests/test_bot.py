"""Tests for the on-demand Telegram chart bot.

Covers command parsing (bare ticker + /chart prefix, decision/noise rejection),
the reply summary, the single-symbol handler, and the unified poll dispatch that
routes go/pass to decisions and ticker requests to the chart handler.
"""

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
