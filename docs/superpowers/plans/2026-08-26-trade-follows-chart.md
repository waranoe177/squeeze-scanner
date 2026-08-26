# Trade-Follows-Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `trade` command inherit its direction and levels from the chart the user is looking at, so the trade card can never contradict the daily alert.

**Architecture:** The chart caption is the source of truth. A `trade` reply parses the replied-to caption for direction + levels; a bare `trade SYM` runs one signal eval and sends chart + card together. Only the option chain is live. Direction is single-sourced; extended names refuse the comparison.

**Tech Stack:** Python 3.12, pytest, yfinance, matplotlib. No new runtime deps. Run tests with `.venv/Scripts/python.exe -m pytest`.

**Spec:** docs/superpowers/specs/2026-08-26-trade-follows-chart-design.md

## Global Constraints

- TDD throughout: failing test first, watch it fail, minimal code, watch it pass, commit.
- No new runtime dependencies. pandas pinned 2.2.3 (Windows SAC).
- The parser operates on the **rendered** caption Telegram delivers in
  `reply_to_message.caption`: HTML tags are already stripped and entities decoded
  (`<b>V</b>` → `V`, `RSI&gt;50` → `RSI>50`). Tests must feed rendered text.
- Direction words in captions: `BUY` → bull, `SELL` → bear, `no signal` → none.
- Do NOT change the options engine (Playbook B defaults, exit-value pricing) or
  the owner-only access filter.
- optfmt must have exactly ONE direction source: `plan["direction"]`.

---

## File Structure

- Create `scanner/captionparse.py` — pure caption → levels parser.
- Create `tests/test_captionparse.py`.
- Modify `scanner/notify.py::_fired_line` — add a parseable `bar {date}` token.
- Modify `scanner/options.py::decide` — surface `direction`; add `extended` flag.
- Modify `scanner/optfmt.py` — delete `_direction`; read `plan["direction"]`; render the extended-refusal note.
- Modify `scanner/bot.py` — `parse_trade` (reply + bare), routing in `poll_once`, `handle_trade` (reply path, bare path, fallback), `_RESERVED`.
- Extend `tests/test_options.py`, `tests/test_optfmt.py`, `tests/test_bot.py`.

---

## Task 1: Unify caption format (add bar date to the fired line)

**Files:**
- Modify: `scanner/notify.py:29-57` (`_fired_line`)
- Test: `tests/test_notify.py`

**Interfaces:**
- Produces: every chart caption (fired + on-demand) contains a `bar YYYY-MM-DD`
  token, a `BUY`/`SELL` word, the symbol, `close N`, `target N` (and stop), so
  `captionparse.parse_caption` (Task 2) can recover levels from either.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notify.py  (add)
def test_fired_line_includes_parseable_bar_date():
    p = {"symbol": "V", "direction": "bull", "close": 384.14, "rsi": 71.0,
         "score": 91, "conviction_grade": "A+", "date": "2026-08-25",
         "prov_target": 401.85, "prov_stop": 373.52}
    line = notify._fired_line(p, cta=True, name="Visa Inc.")
    assert "bar 2026-08-25" in line
    assert "BUY" in line and "V" in line
    assert "close 384.14" in line
    assert "target 401.85" in line and "stop 373.52" in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_notify.py::test_fired_line_includes_parseable_bar_date -v`
Expected: FAIL — `bar 2026-08-25` not present.

- [ ] **Step 3: Add the bar-date token to the head line**

In `_fired_line`, append the bar date to the head. The payload `p` carries
`p["date"]` from `latest_signal` (fall back to `p.get("date", "")`):

```python
    if p.get("date"):
        head += f" · bar {p['date']}"
```
Insert this right after the score is appended to `head` (after the
`if p.get("score") is not None:` block, before `tail`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_notify.py -v`
Expected: PASS. Existing `_fired_line` tests still pass.

- [ ] **Step 5: Commit**

```bash
git add scanner/notify.py tests/test_notify.py
git commit -m "feat(notify): add parseable bar date to fired caption"
```

---

## Task 2: Caption parser (`scanner/captionparse.py`)

**Files:**
- Create: `scanner/captionparse.py`
- Test: `tests/test_captionparse.py`

**Interfaces:**
- Produces: `parse_caption(text: str) -> dict | None`. On success returns
  `{"symbol", "direction", "entry", "target", "stop", "bar_date"}`. Returns
  `None` when the caption has no BUY/SELL signal or is otherwise unparseable.
- Consumes: rendered caption text (both fired and on-demand shapes).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_captionparse.py
from scanner import captionparse as cp

_ONDEMAND = (
    "🟢 BUY V · bar 2026-08-25\n"
    "score 91/100 (A+) · 7/7 lit · R:R 1.5\n"
    "close 384.14 · RSI 71\n"
    "target 401.85 / 366.45 · stop 373.52\n"
    "✅Sqz ✅RSI>50 ✅PPO≥0 ✅8>21 ✅Stack ✅MACD ✅Moxie"
)
_FIRED = (
    "🟢 BUY V — Visa Inc. · score 91/100 (A+) · bar 2026-08-25\n"
    "   close 384.14 · RSI 71\n"
    "   target 401.85 · stop 373.52 (finalize at next open)\n"
    "   ↩️ Reply to this chart: go or pass"
)
_BEAR = (
    "🔴 SELL META · bar 2026-08-25\n"
    "score 80/100 (A) · 7/7 lit · R:R 1.4\n"
    "close 200.00 · RSI 28\n"
    "target 240.00 / 180.00 · stop 210.00\n"
)

def test_parse_ondemand_bull_picks_target_up():
    r = cp.parse_caption(_ONDEMAND)
    assert r["symbol"] == "V" and r["direction"] == "bull"
    assert r["entry"] == 384.14 and r["target"] == 401.85 and r["stop"] == 373.52
    assert r["bar_date"] == "2026-08-25"

def test_parse_fired_single_target():
    r = cp.parse_caption(_FIRED)
    assert r["symbol"] == "V" and r["direction"] == "bull"
    assert r["entry"] == 384.14 and r["target"] == 401.85 and r["stop"] == 373.52

def test_parse_bear_picks_target_dn():
    r = cp.parse_caption(_BEAR)
    assert r["symbol"] == "META" and r["direction"] == "bear"
    assert r["target"] == 180.00 and r["stop"] == 210.00   # dn side for a short

def test_parse_returns_none_on_no_signal():
    assert cp.parse_caption("⚪ no signal ABC · bar 2026-08-25\nclose 10.00") is None

def test_parse_returns_none_on_plain_text():
    assert cp.parse_caption("hey how's it going") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_captionparse.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the parser**

```python
# scanner/captionparse.py
"""Parse a chart caption (the rendered text Telegram delivers in
reply_to_message.caption) back into the signal levels printed on the chart.

The trade command uses this so a `trade` reply inherits EXACTLY the direction and
levels the user is looking at. Returns None when there is no BUY/SELL signal.
"""

import re

_DIR = re.compile(r"\b(BUY|SELL)\s+([A-Z][A-Z0-9.\-=^]{0,11})\b")
_CLOSE = re.compile(r"close\s+([0-9]+(?:\.[0-9]+)?)")
# target may be single ("target 401.85 · stop ...") or dual ("target 401.85 / 366.45 · stop ...")
_TARGET = re.compile(r"target\s+([0-9]+(?:\.[0-9]+)?)(?:\s*/\s*([0-9]+(?:\.[0-9]+)?))?")
_STOP = re.compile(r"stop\s+([0-9]+(?:\.[0-9]+)?)")
_BAR = re.compile(r"bar\s+(\d{4}-\d{2}-\d{2})")


def parse_caption(text: str) -> dict | None:
    if not text:
        return None
    md = _DIR.search(text)
    if not md:
        return None
    direction = "bull" if md.group(1) == "BUY" else "bear"
    symbol = md.group(2).upper()

    mc, mt, ms = _CLOSE.search(text), _TARGET.search(text), _STOP.search(text)
    if not (mc and mt and ms):
        return None
    entry = float(mc.group(1))
    stop = float(ms.group(1))
    up = float(mt.group(1))
    dn = float(mt.group(2)) if mt.group(2) else None
    # Dual-target captions list up then dn; pick the side that matches direction.
    if dn is not None:
        target = up if direction == "bull" else dn
    else:
        target = up
    mb = _BAR.search(text)
    return {"symbol": symbol, "direction": direction, "entry": entry,
            "target": target, "stop": stop,
            "bar_date": mb.group(1) if mb else None}
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_captionparse.py -v`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add scanner/captionparse.py tests/test_captionparse.py
git commit -m "feat(captionparse): recover chart levels from a caption"
```

---

## Task 3: Golden-caption round-trip test (format contract)

**Files:**
- Test: `tests/test_captionparse.py` (add)

**Interfaces:**
- Consumes: real output of `notify._fired_line` and `bot.build_summary`.
- Locks acceptance #7: the parser and the caption builders agree.

- [ ] **Step 1: Write the round-trip (characterization) test**

```python
# tests/test_captionparse.py  (add)
from datetime import date
import pandas as pd, numpy as np
from scanner import notify, bot

def test_roundtrip_fired_line():
    p = {"symbol": "V", "direction": "bull", "close": 384.14, "rsi": 71.0,
         "score": 91, "conviction_grade": "A+", "date": "2026-08-25",
         "prov_target": 401.85, "prov_stop": 373.52}
    r = cp.parse_caption(notify._fired_line(p, cta=True, name="Visa Inc."))
    assert r["symbol"] == "V" and r["direction"] == "bull"
    assert r["entry"] == 384.14 and r["target"] == 401.85 and r["stop"] == 373.52
    assert r["bar_date"] == "2026-08-25"
```

For `build_summary`, use a synthetic frame that yields a known bull signal, or —
if that is heavy — assert the round-trip against a `build_summary` output captured
from an injected `latest_signal`. The implementer picks the lighter path that
still exercises the real `build_summary` string. The assertion must recover
symbol, direction, entry, target (target_up), stop.

- [ ] **Step 2-4: Run, confirm parser recovers levels, adjust builder only if the contract is genuinely unmet**

Run: `.venv/Scripts/python.exe -m pytest tests/test_captionparse.py -v`
If a builder emits an unparseable shape, fix the BUILDER (the format is the
contract), not the assertion.

- [ ] **Step 5: Commit**

```bash
git add tests/test_captionparse.py
git commit -m "test(captionparse): golden round-trip locks caption format"
```

---

## Task 4: `options.decide` — surface direction + extended flag

**Files:**
- Modify: `scanner/options.py:178-225` (`decide`)
- Test: `tests/test_options.py`

**Interfaces:**
- Produces: plan gains `"direction"` (from `signal["direction"]`) and
  `"extended"` (bool). `extended` is True when the level is degenerate for the
  direction: bull with `target <= entry`, or bear with `target >= entry`.
- Consumed by optfmt (Task 5).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_options.py  (add)
def test_decide_surfaces_direction_and_extended_false_normal():
    plan = options.decide(_accept_signal(), _accept_chain(), p=0.65,
                          risk_budget=840.0, target_dte=35, hold_days=10,
                          asof=date(2026, 8, 19))
    assert plan["direction"] == "bull"
    assert plan["extended"] is False           # target 130 > entry 123.45

def test_decide_flags_extended_when_target_past_entry():
    sig = dict(_accept_signal(), target=123.00)   # target below entry 123.45 (extended bull)
    plan = options.decide(sig, _accept_chain(), p=0.65, risk_budget=840.0,
                          asof=date(2026, 8, 19))
    assert plan["direction"] == "bull"
    assert plan["extended"] is True
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -k "surfaces_direction or flags_extended" -v`
Expected: FAIL — keys missing.

- [ ] **Step 3: Implement**

In `decide`, after computing `spot/stop/target/direction`, compute:
```python
    direction = signal["direction"]
    extended = (direction != "bear" and target <= spot) or \
               (direction == "bear" and target >= spot)
```
Add `"direction": direction, "extended": extended` to the `base` dict so both the
options-available and equity-only / (new) extended returns carry them.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_options.py -v`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add scanner/options.py tests/test_options.py
git commit -m "feat(options): surface direction + extended flag on the plan"
```

---

## Task 5: `optfmt` — single direction source + extended refusal

**Files:**
- Modify: `scanner/optfmt.py` (remove `_direction`; read `plan["direction"]`; add extended note)
- Test: `tests/test_optfmt.py`

**Interfaces:**
- Consumes: `plan["direction"]`, `plan["extended"]`.
- Produces: `format_trade(plan)` returns the extended-refusal note when
  `plan["extended"]`; otherwise the normal card, labeled by `plan["direction"]`
  ONLY (never re-inferred from target vs spot).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_optfmt.py  (add)
def test_direction_comes_from_plan_not_target_vs_spot():
    # A degenerate target (<= spot) must NOT flip a BUY into SHORT.
    plan = _shares_win_plan()
    plan.update(direction="bull", extended=False, target=123.40, move_pct=-0.0)
    msg = optfmt.format_trade(plan)
    assert "BUY SHARES" in msg and "SHORT" not in msg

def test_extended_refuses_with_reason_not_a_card():
    plan = _shares_win_plan()
    plan.update(direction="bull", extended=True, target=380.0, spot=384.14)
    msg = optfmt.format_trade(plan)
    assert "extended" in msg.lower()
    assert "BUY SHARES" not in msg and "SKIP" not in msg     # no comparison card
    assert "SHORT" not in msg                                # never inverts
```

Keep the existing shares-win / option-win tests green by adding
`direction=...`, `extended=False` to their fixtures (`_shares_win_plan`,
`_bear_plan` set `direction` to match their intent: "bull"/"bear").

- [ ] **Step 2: Run to verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optfmt.py -v`
Expected: FAIL — `_direction` still used / no extended branch.

- [ ] **Step 3: Implement**

- Delete `_direction`. In `format_trade`, replace `d = _direction(plan)` with
  `d = plan["direction"]`.
- At the top of `format_trade`, before the options-available branches:
```python
    if plan.get("extended"):
        return _extended_note(plan)
```
- Add the helper:
```python
def _extended_note(plan) -> str:
    d = plan["direction"]
    verb = "BUY" if d != "bear" else "SHORT"
    side = "below" if d != "bear" else "above"
    sym = notify._esc(plan["symbol"])
    return "\n".join([
        f"<b>{sym} · {verb} signal — but extended</b>",
        f"  target {plan['target']:.0f} sits {side} the current "
        f"{plan['spot']:.0f}, so there's no room left to size.",
        "  This is a chase. Wait for a pullback toward EMA21, or set your own target.",
    ])
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optfmt.py -v`
Expected: PASS, existing card tests still green.

- [ ] **Step 5: Commit**

```bash
git add scanner/optfmt.py tests/test_optfmt.py
git commit -m "feat(optfmt): single direction source + extended refusal"
```

---

## Task 6: `bot` — parse `trade` reply + bare, routing, reserved word

**Files:**
- Modify: `scanner/bot.py` (`parse_trade`, `poll_once`, `_RESERVED`)
- Test: `tests/test_bot.py`

**Interfaces:**
- Produces: `parse_trade(update)` returns `{symbol|None, p, risk, dte, full, caption}`
  where `caption` is the replied-to caption text when the update is a reply, else
  None; `symbol` is None for a reply (comes from the caption). Bare `trade SYM`
  returns the symbol as today.
- `_RESERVED` includes `"trade"` so a bare `trade` reply is not a chart request.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bot.py  (add)
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
```

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement**

- Add `"trade"` to `_RESERVED`.
- In `parse_trade`, detect a reply: read
  `update["message"].get("reply_to_message", {}).get("caption")`. If the text is
  (case-insensitive) `trade` optionally followed by overrides (no symbol token),
  and a caption is present, return `{"symbol": None, ..., "caption": caption}`.
  Otherwise parse the bare `trade SYM ...` form as today and set
  `"caption": None`.
- In `poll_once`, the trade branch already runs before the chart branch; ensure a
  reply whose caption parses as a trade is routed to `trade_handler`.

- [ ] **Step 4: Run to verify pass** — `tests/test_bot.py` green.

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat(bot): parse trade reply (caption) + reserve the word"
```

---

## Task 7: `bot.handle_trade` — reply path, bare path, fallback

**Files:**
- Modify: `scanner/bot.py:260-299` (`handle_trade`)
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `opts` from `parse_trade` (`symbol` or `caption`), `captionparse`,
  `options.decide`, `optfmt`.
- Behavior:
  - **Reply (opts["caption"] set):** `parse_caption`; if None →
    send the fallback message and return. Else build the signal from the parsed
    direction/entry/target/stop (stop from the caption, direction from the
    caption), fetch df ONLY for `realized_vol`, fetch the live chain, `decide`,
    format, send. Prepend a vintage line `follows your {sym} chart · bar {date}`.
  - **Bare (opts["symbol"] set):** ONE `latest_signal` eval. Send the chart
    (reuse `handle_command`'s render+caption) THEN build the card from the SAME
    `sig` (no second eval), send. If direction is "none", send "no active signal
    on {sym}" and skip the card.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bot.py  (add)
def test_handle_trade_reply_uses_caption_direction_never_inverts(monkeypatch):
    sent = {}
    def fake_send(token, chat, msg): sent["msg"] = msg
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
    bot.handle_trade(opts, "chat", "tok", send_message=lambda t,c,m: sent.setdefault("m", m),
                     asof=date(2026, 8, 19))
    assert "trade SYM" in sent["m"]
```

`_fake_df_fetcher` returns `{ "V": <small synthetic frame> }` sufficient for
`options.realized_vol`; the chain is None so the card is equity-only, which is
enough to assert direction + vintage. The implementer builds the minimal frame.

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement** the reply/bare/fallback branches per the interface
  above. For the reply path, direction and levels come ONLY from the parsed
  caption; never call `latest_signal` for direction. For the bare path, render
  chart + card from a single `latest_signal`.

- [ ] **Step 4: Run to verify pass** — `tests/test_bot.py` green; full suite green.

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat(bot): trade follows the chart (reply + bare + fallback)"
```

---

## Task 8: End-to-end acceptance

**Files:**
- Test: `tests/test_bot.py` (add)

- [ ] **Step 1: Write the acceptance test**

```python
# tests/test_bot.py  (add)
def test_acceptance_buy_caption_never_yields_short_card():
    # The V/MA regression: a BUY caption must never produce a SHORT card.
    sent = {}
    opts = {"symbol": None, "p": None, "risk": 500.0, "dte": None, "full": False,
            "caption": "🟢 BUY V — Visa Inc. · score 91/100 (A+) · bar 2026-08-25\n"
                       "close 384.14 · RSI 71\ntarget 401.85 · stop 373.52 (finalize at next open)"}
    bot.handle_trade(opts, "chat", "tok", fetcher=_fake_df_fetcher(),
                     chain_fetcher=lambda s: None,
                     send_message=lambda t,c,m: sent.setdefault("m", m),
                     asof=date(2026, 8, 19))
    m = sent["m"]
    assert "SHORT" not in m and "SELL" not in m
    assert "BUY" in m and "401" in m and "373" in m    # inherited target + stop
```

- [ ] **Step 2-4: Run, confirm the regression is dead, full suite green**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_bot.py
git commit -m "test(bot): acceptance — BUY caption never yields a SHORT card"
```

---

## Self-review notes

- **Spec coverage:** caption-parse (T2/T3), single direction (T4/T5), extended
  refusal (T4/T5), reply + bare paths (T6/T7), fallback (T7), vintage label (T7),
  golden format lock (T1/T3), acceptance regression (T8). All spec sections map to
  a task.
- **Type consistency:** `parse_caption` keys (`symbol/direction/entry/target/stop/
  bar_date`) are consumed verbatim by `handle_trade`; `decide` adds
  `direction/extended`; `optfmt` reads exactly those.
- **No placeholders:** every code step carries real code except the two synthetic
  data frames (Task 3 build_summary, Task 7 `_fake_df_fetcher`), which are left to
  the implementer because the minimal frame shape depends on existing test
  helpers in `tests/`; the implementer must reuse those helpers.
