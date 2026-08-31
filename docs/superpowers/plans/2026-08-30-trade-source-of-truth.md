# Trade Source-of-Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bare `trade SYM` anchor to the daily scan's persisted signal (via the same caption parser the reply path uses) so it can never contradict the daily alert, and refuse when the anchored levels are stale against live price.

**Architecture:** The daily scan is the single source of truth. Bare `trade SYM` reads `out/results.json.fired[]`, synthesizes the alert's own `_fired_line` caption from that payload, renders + parses it through `captionparse` (one anchor path shared with the reply path), and builds the card from those frozen levels + a live chain. No `latest_signal` re-derivation for direction/levels. Off-list symbols refuse+redirect; stale-vs-live-price refuses.

**Tech Stack:** Python 3.12, pytest. No new runtime deps. Run tests with `.venv/Scripts/python.exe -m pytest`.

**Spec:** docs/superpowers/specs/2026-08-30-trade-source-of-truth-design.md

## Global Constraints

- TDD throughout: failing test first, watch it fail, minimal code, watch it pass, commit.
- No new runtime dependencies. pandas pinned 2.2.3.
- Direction/entry/target/stop/score for an in-scan symbol come ONLY from the
  persisted `results.json` payload, via the synthesized caption + `parse_caption`.
  The bare path must NOT call `signals.latest_signal` for direction/levels.
- Anchored levels use `prov_target`/`prov_stop` (what the alert showed), achieved
  automatically by synthesizing `notify._fired_line(payload)` — never the raw
  `latest_signal` `stop`/`target_up`.
- The live df fetch is allowed ONLY for the freshness guard (live spot) and
  `realized_vol` (IV fallback) — never to re-derive direction/levels.
- Do NOT change the reply path, the options engine, the daily scan, or the owner filter.

---

## File Structure

- Modify `scanner/captionparse.py` — add `render_html(text)` (strip tags + unescape),
  the production version of the test-only render step.
- Modify `scanner/bot.py` — add `_load_results()`, `_anchor_payload(symbol)`,
  `_anchor_caption(payload)`; rewrite `_handle_trade_bare` to the anchored flow with
  the freshness guard and refuse+redirect; keep `_handle_trade_reply` untouched.
- Test: `tests/test_captionparse.py`, `tests/test_bot.py`.

---

## Task 1: `captionparse.render_html`

**Files:**
- Modify: `scanner/captionparse.py`
- Test: `tests/test_captionparse.py`

**Interfaces:**
- Produces: `render_html(text: str) -> str` — strips HTML tags and unescapes
  entities, turning `_fired_line`/`build_summary` raw HTML into the rendered text
  Telegram delivers (and that `parse_caption` expects).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_captionparse.py  (add)
def test_render_html_strips_tags_and_unescapes():
    assert cp.render_html("🟢 BUY <b>V</b> · RSI&gt;50") == "🟢 BUY V · RSI>50"
    assert cp.render_html("plain text") == "plain text"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_captionparse.py::test_render_html_strips_tags_and_unescapes -v`
Expected: FAIL — `render_html` missing.

- [ ] **Step 3: Implement**

```python
# scanner/captionparse.py  (add near the top, after imports)
import html as _html

_TAG = re.compile(r"<[^>]+>")

def render_html(text: str) -> str:
    """Turn raw caption HTML (from _fired_line/build_summary) into the rendered
    text Telegram delivers in reply_to_message.caption — tags stripped, entities
    decoded. parse_caption operates on rendered text."""
    if not text:
        return ""
    return _html.unescape(_TAG.sub("", text))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_captionparse.py -v`
Expected: PASS. If the file has a test-only `_render` helper, refactor its uses to
`cp.render_html` so there is one render impl.

- [ ] **Step 5: Commit**

```bash
git add scanner/captionparse.py tests/test_captionparse.py
git commit -m "feat(captionparse): render_html (strip tags + unescape) for anchoring"
```

---

## Task 2: results.json loader + anchor lookup

**Files:**
- Modify: `scanner/bot.py`
- Test: `tests/test_bot.py`

**Interfaces:**
- Produces: `_load_results(path=None) -> dict | None` (reads `out/results.json`,
  None on missing/unreadable) and `_anchor_payload(symbol, results) -> dict | None`
  (returns the `fired[]` payload for symbol, else None).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bot.py  (add)
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
```

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement**

```python
# scanner/bot.py  (add; near the other module-level helpers)
import json as _json

_RESULTS_PATH = "out/results.json"

def _load_results(path=None):
    """Latest daily-scan snapshot, or None if absent/unreadable. Never raises."""
    try:
        with open(path or _RESULTS_PATH, encoding="utf-8") as fh:
            return _json.load(fh)
    except Exception:
        return None

def _anchor_payload(symbol, results):
    """The fired[] payload for symbol from a results snapshot, else None."""
    if not results:
        return None
    for p in results.get("fired", []):
        if str(p.get("symbol", "")).upper() == symbol.upper():
            return p
    return None
```

- [ ] **Step 4: Run to verify pass** — `tests/test_bot.py` green.

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat(bot): results.json loader + fired-payload anchor lookup"
```

---

## Task 3: synthesize + parse the anchor caption (golden round-trip)

**Files:**
- Modify: `scanner/bot.py`
- Test: `tests/test_bot.py`

**Interfaces:**
- Produces: `_anchor_caption(payload) -> str` — `render_html(notify._fired_line(payload))`,
  the rendered caption identical to what the alert sent, ready for `parse_caption`.

- [ ] **Step 1: Write the failing test (the acceptance-#6 golden round-trip)**

```python
# tests/test_bot.py  (add)
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
```

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement**

```python
# scanner/bot.py  (add)
def _anchor_caption(payload):
    """Reproduce the alert's caption from a fired payload and render it to the
    text parse_caption expects. Reuses notify._fired_line so the direction-aware
    prov_target/prov_stop levels are what get parsed — one anchor path with the
    reply flow, and the long-side-stop trap can't appear."""
    return captionparse.render_html(notify._fired_line(payload))
```

- [ ] **Step 4: Run to verify pass** — round-trip recovers prov levels.

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat(bot): synthesize+render the anchor caption from a fired payload"
```

---

## Task 4: rewrite `_handle_trade_bare` to anchor (no re-derivation) + refuse+redirect

**Files:**
- Modify: `scanner/bot.py:346-378` (`_handle_trade_bare`)
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `_load_results`, `_anchor_payload`, `_anchor_caption`,
  `captionparse.parse_caption`, `_decide_and_format`.
- Behavior: anchor lookup → **not found** send refuse+redirect (return False);
  **found** parse the synthesized caption, send the committed `out/{chart}` png
  (if present), then the card from parsed direction/entry/target/stop/score + live
  chain + realized vol, prepend the vintage line. NO `latest_signal` for
  direction/levels. (Freshness guard added in Task 5.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bot.py  (add)
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
                     send_message=lambda t,c,m: sent.setdefault("m", m),
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
                     send_message=lambda t,c,m: sent.setdefault("m", m),
                     asof=date(2026, 8, 30))
    assert "no active signal" in sent["m"].lower() and "chart TSLA" in sent["m"]

def test_bare_trade_missing_results_refuses(monkeypatch):
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: None)
    sent = {}
    bot.handle_trade({"symbol": "APD", "p": None, "risk": 500.0, "dte": None,
                      "full": False, "caption": None}, "chat", "tok",
                     send_message=lambda t,c,m: sent.setdefault("m", m),
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
                     send_message=lambda t,c,m: sent.setdefault("m", m),
                     send_photo=lambda *a, **k: None, asof=date(2026, 8, 30))
    assert "BUY" in sent["m"]   # latest_signal never called -> no AssertionError
```

`_fake_df_fetcher(spot=...)` returns `{SYM: <tiny frame whose last close == spot>}`
sufficient for `options.realized_vol` and the Task-5 guard. Reuse an existing frame
helper in `tests/` if present; otherwise build a minimal ascending-close frame.

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement** — replace `_handle_trade_bare` body:

```python
def _handle_trade_bare(opts, chat_id, token, *, fetcher, chain_fetcher,
                       send_message, renderer, send_photo, tmp_dir, asof) -> bool:
    """Bare `trade SYM`: anchor to the daily scan's persisted signal. Direction and
    levels come ONLY from results.json (via the synthesized caption), never a fresh
    latest_signal eval — so the card can't contradict the alert."""
    symbol = opts["symbol"]
    results = _load_results()
    payload = _anchor_payload(symbol, results)
    if payload is None:
        as_of = (results or {}).get("as_of", "the latest scan")
        send_message(token, chat_id,
                     f"{symbol} has no active signal in the latest scan (as of "
                     f"{as_of}). To analyze it anyway, `chart {symbol}` then reply "
                     f"`trade` to the chart.")
        return False

    parsed = captionparse.parse_caption(_anchor_caption(payload))
    direction, entry = parsed["direction"], parsed["entry"]
    target, stop, bar_date = parsed["target"], parsed["stop"], parsed["bar_date"]

    # live df for realized-vol (IV fallback) + the Task-5 freshness guard only
    frames = fetcher([symbol])
    df = frames.get(symbol)
    rv = options.realized_vol(df["close"]) if df is not None and not getattr(df, "empty", True) else 0.0

    # [Task 5 inserts the freshness guard here]

    # send the committed daily chart (same picture the alert sent), best-effort
    chart_rel = payload.get("chart")
    if chart_rel:
        cpath = Path("out") / chart_rel
        if cpath.exists():
            try:
                send_photo(token, chat_id, str(cpath), caption=_anchor_caption(payload))
            except Exception as exc:
                print(f"  [bot] anchor chart send failed for {symbol}: {exc}")

    msg = _decide_and_format(opts, symbol, direction, entry, target, stop, rv,
                             chain_fetcher=chain_fetcher, asof=asof,
                             conv_score=parsed.get("score"))
    vintage = f"follows the {bar_date} scan signal" if bar_date else "follows the latest scan signal"
    send_message(token, chat_id, vintage + "\n" + msg)
    return True
```

- [ ] **Step 4: Run to verify pass** — full suite green; existing bare-path tests
  updated to the anchored contract (they asserted the old re-derivation).

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat(bot): bare trade anchors to the scan signal; off-list refuses"
```

---

## Task 5: freshness guard (stale levels vs live price)

**Files:**
- Modify: `scanner/bot.py` (`_handle_trade_bare`, the marked insertion point)
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: the anchored `payload["atr"]`, `entry`, `target`, `direction`, and the
  live df's last close (`live_spot`).
- Behavior: if `live_spot` has moved more than 1 ATR past `entry`, OR crossed the
  anchored target (bull: `live_spot >= target`; bear: `live_spot <= target`), do
  NOT price a card — send the "levels are from {date}; price has moved" refusal.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot.py  (add)
def test_bare_trade_refuses_when_price_moved_past_target(monkeypatch):
    monkeypatch.setattr(bot, "_load_results", lambda *a, **k: _APD_RESULTS)
    sent = {}
    # anchored entry 308.09, target 323.69, atr 6.0; live spot 330 => crossed target
    bot.handle_trade({"symbol": "APD", "p": None, "risk": 500.0, "dte": None,
                      "full": False, "caption": None}, "chat", "tok",
                     fetcher=_fake_df_fetcher(spot=330.0), chain_fetcher=lambda s: None,
                     send_message=lambda t,c,m: sent.setdefault("m", m),
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
                     send_message=lambda t,c,m: sent.setdefault("m", m),
                     send_photo=lambda *a, **k: None, asof=date(2026, 8, 30))
    assert "BUY" in sent["m"]                            # within 1 ATR -> card priced
```

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement** — at the marked insertion point in `_handle_trade_bare`:

```python
    live_spot = float(df["close"].iloc[-1]) if df is not None and not getattr(df, "empty", True) else None
    atr = float(payload.get("atr") or 0.0)
    if live_spot is not None:
        crossed = (direction != "bear" and live_spot >= target) or \
                  (direction == "bear" and live_spot <= target)
        moved = atr > 0 and abs(live_spot - entry) > atr
        if crossed or moved:
            send_message(token, chat_id,
                         f"levels are from {bar_date}; {symbol} has moved to "
                         f"{live_spot:.2f} — pull a fresh chart (`chart {symbol}`) "
                         f"and reply `trade`.")
            return False
```

- [ ] **Step 4: Run to verify pass** — full suite green.

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat(bot): freshness guard — refuse anchored trade when price moved past levels"
```

---

## Task 6: `drop_forming` comment + end-to-end acceptance

**Files:**
- Modify: `scanner/data.py` (comment only), `tests/test_bot.py`
- Test: `tests/test_bot.py`

- [ ] **Step 1: Add the documenting comment** (Decision 3) to `drop_forming_bar`,
  naming the request-time-vs-scan-time skew so no future re-derivation reintroduces
  it. No behavior change.

```python
    # NOTE: this is correct (drops the incomplete current-day bar), but its result
    # depends on `now`. Any code that RE-DERIVES a signal at request time (vs the
    # post-close daily scan) can therefore land on an earlier bar than the scan —
    # that skew, plus yfinance re-adjusting history, is why `trade` anchors to the
    # persisted scan snapshot instead of re-deriving. Do not re-derive on the hot path.
```

- [ ] **Step 2: Write the acceptance test** (the APD regression, end to end)

```python
# tests/test_bot.py  (add)
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
                     send_message=lambda t,c,m: sent.setdefault("m", m),
                     send_photo=lambda *a, **k: None, asof=date(2026, 8, 30))
    assert "BUY" in sent["m"] and "323" in sent["m"]   # anchored to the alert
```

- [ ] **Step 3-4: Run** — `.venv/Scripts/python.exe -m pytest -q` all green.

- [ ] **Step 5: Commit**

```bash
git add scanner/data.py tests/test_bot.py
git commit -m "docs(data): name drop_forming request-time skew; acceptance: APD anchor"
```

---

## Self-review notes

- **Spec coverage:** anchor via captionparse (T1/T3), results lookup (T2), bare
  rewrite + refuse+redirect + no-re-derivation (T4), freshness guard (T5),
  drop_forming comment + acceptance (T6). AC #1-7 all map. Golden round-trip = T3
  (AC#6); no-re-derivation guarantee = T4/T6 (AC#2); freshness guard = T5 (AC#4).
- **Type consistency:** `parse_caption` keys (symbol/direction/entry/target/stop/
  bar_date/score) consumed verbatim by the bare handler; `_anchor_payload` returns
  the raw results.json fired payload; `_anchor_caption` feeds `render_html`→`parse_caption`.
- **No placeholders:** every code step carries real code except `_fake_df_fetcher`
  (a tiny frame whose last close = a passed spot), left to the implementer to build
  from existing `tests/` frame helpers.
- **Reply path untouched** — only `_handle_trade_bare` changes.
