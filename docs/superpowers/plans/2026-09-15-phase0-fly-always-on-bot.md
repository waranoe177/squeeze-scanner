# Phase 0: Always-On Fly.io Telegram Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the existing `bot.py serve()` long-poll loop on a single always-on Fly.io machine so `trade`/`chart` replies land in 1-3s instead of the 15-60min GitHub-cron-throttle latency.

**Architecture:** Code is baked into a container image (ships via `fly deploy`). The bot reads `out/results.json` + `out/charts/SYM.png` over raw.githubusercontent HTTPS and appends go/pass to a NEW append-only `ledger/decisions.jsonl` via the GitHub Contents API — it never git-clones and never writes `signals.jsonl`. The daily scan stays the sole writer of `signals.jsonl` and folds `decisions.jsonl` in each run. A healthchecks.io deadman fires if the bot goes dark.

**Tech Stack:** Python 3.12, `requests`, Fly.io (single machine + tiny volume), GitHub Contents API, raw.githubusercontent.com, healthchecks.io, pytest.

**Spec:** GitHub issue #1 (waranoe177/squeeze-scanner) + its "Engineering Review Outcome (2026-09-15)" comment, which holds the REVISED architecture this plan implements. The archived spec is at `~/.gstack/projects/waranoe177-squeeze-scanner/specs/20260915-151233-1821-phase0-fly-always-on-bot.md`.

## Global Constraints

- Python 3.12; existing suite is 266 tests green and MUST stay green. TDD throughout.
- Owner-only access stays (multi-user is Phase 1). Do not touch `_from_owner`.
- Exactly ONE Telegram `getUpdates` consumer at all times (else HTTP 409). The Fly machine is `min=max=1`, no autoscaling, in-place deploy. `bot.yml` is retained but `workflow_dispatch`-ONLY (no cron) so it never auto-polls.
- The bot NEVER writes `ledger/signals.jsonl`. `ledger.save()` full-rewrites and `ledger.update()` mutates in place — a second concurrent writer corrupts the public track record. The bot's only repo write is appending to `ledger/decisions.jsonl` (append-only).
- No secrets in code or committed files. Secrets are Fly secrets / GitHub Actions secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GITHUB_TOKEN` (fine-grained PAT, Contents:read+write on this repo only), `HEALTHCHECK_URL`, and `SQZDOTS_REPO=waranoe177/squeeze-scanner`.
- matplotlib Agg backend is already set at `chart.py:9` — do not change it.
- Do not add new heavy dependencies; `requests` is already used (`decisions.py:88`).

---

### Task 1: `scanner/ghsync.py` — GitHub data plane (raw fetch + Contents-API append)

Pure module, HTTP client injectable for tests. This is the whole "how the Fly bot talks to the repo without a clone."

**Files:**
- Create: `scanner/ghsync.py`
- Test: `tests/test_ghsync.py`

**Interfaces:**
- Produces:
  - `fetch_results(repo: str, ref: str = "main", *, http=None, ttl: float = 300.0) -> dict | None`
  - `fetch_chart(repo: str, symbol: str, dest_path: str, ref: str = "main", *, http=None) -> str | None`
  - `append_decision(repo: str, decision: dict, token: str, *, http=None, path: str = "ledger/decisions.jsonl", retries: int = 3) -> bool`
- Consumes: `requests` (default when `http` is None).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ghsync.py
import base64
import json
import pytest
from scanner import ghsync


class FakeResp:
    def __init__(self, status, body=None, content=b""):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body
        self.content = content
        self.text = ""

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeHTTP:
    """Records calls; returns queued responses by (method, url-substring)."""
    def __init__(self):
        self.queue = []          # list of (method, url_sub, FakeResp)
        self.calls = []

    def add(self, method, url_sub, resp):
        self.queue.append((method, url_sub, resp))

    def _match(self, method, url):
        for i, (m, sub, resp) in enumerate(self.queue):
            if m == method and sub in url:
                del self.queue[i]
                return resp
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        return self._match("GET", url)

    def put(self, url, **kw):
        self.calls.append(("PUT", url, kw.get("json")))
        return self._match("PUT", url)


def test_fetch_results_returns_parsed_json():
    http = FakeHTTP()
    http.add("GET", "raw.githubusercontent.com", FakeResp(200, body={"fired": [{"symbol": "COST"}]}))
    ghsync._RESULTS_CACHE.clear()
    out = ghsync.fetch_results("owner/repo", http=http)
    assert out == {"fired": [{"symbol": "COST"}]}


def test_fetch_results_none_on_404():
    http = FakeHTTP()
    http.add("GET", "raw.githubusercontent.com", FakeResp(404))
    ghsync._RESULTS_CACHE.clear()
    assert ghsync.fetch_results("owner/repo", http=http) is None


def test_fetch_results_uses_cache_within_ttl():
    http = FakeHTTP()
    http.add("GET", "raw.githubusercontent.com", FakeResp(200, body={"as_of": "2026-09-15"}))
    ghsync._RESULTS_CACHE.clear()
    a = ghsync.fetch_results("owner/repo", http=http, ttl=999)
    b = ghsync.fetch_results("owner/repo", http=http, ttl=999)  # no 2nd GET queued
    assert a == b == {"as_of": "2026-09-15"}
    assert sum(1 for c in http.calls if c[0] == "GET") == 1


def test_fetch_chart_saves_bytes(tmp_path):
    http = FakeHTTP()
    http.add("GET", "raw.githubusercontent.com", FakeResp(200, content=b"\x89PNGdata"))
    dest = tmp_path / "COST.png"
    out = ghsync.fetch_chart("owner/repo", "COST", str(dest), http=http)
    assert out == str(dest)
    assert dest.read_bytes() == b"\x89PNGdata"


def test_fetch_chart_none_on_missing(tmp_path):
    http = FakeHTTP()
    http.add("GET", "raw.githubusercontent.com", FakeResp(404))
    assert ghsync.fetch_chart("owner/repo", "COST", str(tmp_path / "x.png"), http=http) is None


def test_append_decision_creates_file_when_absent():
    http = FakeHTTP()
    http.add("GET", "api.github.com", FakeResp(404))                 # file absent
    http.add("PUT", "api.github.com", FakeResp(201, body={"content": {}}))
    dec = {"decision": "go", "decided_at": "2026-09-15T14:00:00+00:00",
           "reply_to_msg_id": 42, "symbol": None}
    assert ghsync.append_decision("owner/repo", dec, "tok", http=http) is True
    put = [c for c in http.calls if c[0] == "PUT"][0]
    sent = json.loads(base64.b64decode(put[2]["content"]).decode())
    assert sent == dec


def test_append_decision_appends_to_existing():
    http = FakeHTTP()
    existing = base64.b64encode(b'{"decision":"pass"}\n').decode()
    http.add("GET", "api.github.com", FakeResp(200, body={"content": existing, "sha": "abc"}))
    http.add("PUT", "api.github.com", FakeResp(200, body={"content": {}}))
    dec = {"decision": "go", "decided_at": "t", "reply_to_msg_id": 1, "symbol": None}
    assert ghsync.append_decision("owner/repo", dec, "tok", http=http) is True
    put = [c for c in http.calls if c[0] == "PUT"][0]
    decoded = base64.b64decode(put[2]["content"]).decode()
    assert decoded.splitlines()[-1] == json.dumps(dec)
    assert put[2]["sha"] == "abc"


def test_append_decision_retries_on_sha_conflict():
    http = FakeHTTP()
    http.add("GET", "api.github.com", FakeResp(200, body={"content": "", "sha": "old"}))
    http.add("PUT", "api.github.com", FakeResp(409))                 # someone pushed
    http.add("GET", "api.github.com", FakeResp(200, body={"content": "", "sha": "new"}))
    http.add("PUT", "api.github.com", FakeResp(200, body={"content": {}}))
    dec = {"decision": "go", "decided_at": "t", "reply_to_msg_id": 1, "symbol": None}
    assert ghsync.append_decision("owner/repo", dec, "tok", http=http, retries=3) is True


def test_append_decision_gives_up_after_retries():
    http = FakeHTTP()
    for _ in range(3):
        http.add("GET", "api.github.com", FakeResp(200, body={"content": "", "sha": "s"}))
        http.add("PUT", "api.github.com", FakeResp(409))
    dec = {"decision": "go", "decided_at": "t", "reply_to_msg_id": 1, "symbol": None}
    assert ghsync.append_decision("owner/repo", dec, "tok", http=http, retries=3) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ghsync.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scanner.ghsync'`

- [ ] **Step 3: Write the implementation**

```python
# scanner/ghsync.py
"""GitHub data plane for the always-on Fly bot.

The bot never git-clones. It READS out/results.json + out/charts/SYM.png over
raw.githubusercontent.com (public repo, no auth) and appends go/pass decisions
to ledger/decisions.jsonl via the GitHub Contents API (the ONLY repo write it
does). signals.jsonl is left untouched — the daily scan is its sole writer.
"""

import base64
import json
import time

_RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
_API = "https://api.github.com/repos/{repo}/contents/{path}"
_RESULTS_CACHE: dict = {}   # repo -> (fetched_at, data)


def _client(http):
    if http is not None:
        return http
    import requests
    return requests


def fetch_results(repo, ref="main", *, http=None, ttl=300.0):
    """Latest scan snapshot from raw.githubusercontent, cached `ttl` seconds.
    None on any failure (never raises) — callers already handle a missing scan."""
    hit = _RESULTS_CACHE.get(repo)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    client = _client(http)
    try:
        r = client.get(_RAW.format(repo=repo, ref=ref, path="out/results.json"), timeout=15)
        if not r.ok:
            return None
        data = r.json()
    except Exception:
        return None
    _RESULTS_CACHE[repo] = (time.time(), data)
    return data


def fetch_chart(repo, symbol, dest_path, ref="main", *, http=None):
    """Download out/charts/SYM.png to dest_path. None if absent/failed."""
    client = _client(http)
    try:
        r = client.get(_RAW.format(repo=repo, ref=ref, path=f"out/charts/{symbol}.png"),
                        timeout=15)
        if not r.ok or not r.content:
            return None
        with open(dest_path, "wb") as fh:
            fh.write(r.content)
        return dest_path
    except Exception:
        return None


def append_decision(repo, decision, token, *, http=None, path="ledger/decisions.jsonl",
                    retries=3):
    """Append one decision dict as a JSON line to decisions.jsonl via the Contents
    API. Optimistic concurrency on the blob sha; retries on 409 (a concurrent
    push). Returns True on success, False if it can't land after `retries`."""
    client = _client(http)
    url = _API.format(repo=repo, path=path)
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    line = (json.dumps(decision) + "\n").encode()
    for _ in range(retries):
        try:
            g = client.get(url, params={"ref": "main"}, headers=headers, timeout=15)
            if g.status_code == 404:
                cur, sha = b"", None                     # create on first write
            elif g.ok:
                body = g.json()
                cur = base64.b64decode(body.get("content", "") or "")
                sha = body.get("sha")
            else:
                continue
            payload = {"message": "bot: append decision",
                       "content": base64.b64encode(cur + line).decode()}
            if sha:
                payload["sha"] = sha
            p = client.put(url, json=payload, headers=headers, timeout=15)
            if p.ok:
                return True
            # 409 sha conflict (or transient) -> re-read and retry
        except Exception:
            continue
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ghsync.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add scanner/ghsync.py tests/test_ghsync.py
git commit -m "feat(ghsync): GitHub data plane for the Fly bot (raw fetch + Contents-API append)"
```

---

### Task 2: `scanner/bot.py` — route data + decisions through ghsync; heartbeat

**Files:**
- Modify: `scanner/bot.py` (`_load_results` / `_handle_trade_bare` chart send; `poll_once` decisions; `serve` heartbeat)
- Test: `tests/test_bot.py` (extend)

**Interfaces:**
- Consumes (Task 1): `ghsync.fetch_results`, `ghsync.fetch_chart`, `ghsync.append_decision`.
- Env read: `SQZDOTS_REPO` (default `"waranoe177/squeeze-scanner"`), `GITHUB_TOKEN`, `HEALTHCHECK_URL` (optional).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bot.py (append)
from scanner import bot


def test_load_results_reads_via_ghsync(monkeypatch):
    monkeypatch.setattr(bot.ghsync, "fetch_results", lambda repo, **k: {"fired": [{"symbol": "COST"}]})
    assert bot._load_results() == {"fired": [{"symbol": "COST"}]}


def test_poll_once_appends_decision_via_ghsync(monkeypatch):
    # one go/pass reply update -> ghsync.append_decision called, ledger untouched
    calls = []
    monkeypatch.setattr(bot.ghsync, "append_decision",
                        lambda repo, dec, token, **k: calls.append(dec) or True)
    monkeypatch.setattr(bot.decisions, "fetch_updates",
                        lambda token, offset, timeout=0: (
                            [{"update_id": 5, "message": {"date": 1, "text": "go",
                              "chat": {"id": "1"}, "reply_to_message": {"message_id": 42}}}], 6))
    monkeypatch.setattr(bot.decisions, "save_state", lambda p, s: None)
    monkeypatch.setattr(bot.decisions, "load_state", lambda p: {"offset": 0})
    res = bot.poll_once(token="t", chat_id="1", state_path="x",
                        command_handler=lambda s: False, trade_handler=lambda o: False)
    assert calls == [{"decision": "go", "decided_at": bot.decisions.parse_decision(
        {"message": {"date": 1, "text": "go", "reply_to_message": {"message_id": 42}}})["decided_at"],
        "reply_to_msg_id": 42, "symbol": None}]
    assert res["decisions"] == 1


def test_serve_pings_healthcheck_after_successful_poll(monkeypatch):
    pings = []
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/uuid")
    monkeypatch.setattr(bot, "_ping_healthcheck", lambda url: pings.append(url))
    seq = iter([None, KeyboardInterrupt()])

    def fake_poll(**k):
        v = next(seq)
        if isinstance(v, BaseException):
            raise v
        return {"updates": 0, "decisions": 0, "charts": 0}

    monkeypatch.setattr(bot, "poll_once", fake_poll)
    bot.serve(token="t", chat_id="1", poll_timeout=0)
    assert pings == ["https://hc.example/uuid"]  # pinged once, only after the success


def test_serve_does_not_ping_when_poll_raises(monkeypatch):
    pings = []
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/uuid")
    monkeypatch.setattr(bot, "_ping_healthcheck", lambda url: pings.append(url))
    seq = iter([RuntimeError("boom"), KeyboardInterrupt()])

    def fake_poll(**k):
        v = next(seq)
        raise v

    monkeypatch.setattr(bot, "poll_once", fake_poll)
    bot.serve(token="t", chat_id="1", poll_timeout=0)
    assert pings == []  # a crash-looping poll must NOT report healthy
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bot.py -k "ghsync or healthcheck or appends_decision" -q`
Expected: FAIL — `bot.ghsync` / `bot._ping_healthcheck` do not exist; `poll_once` still uses the ledger.

- [ ] **Step 3: Implement the changes**

Add the import and helper near the top of `scanner/bot.py`:

```python
from scanner import captionparse, chart, data, decisions, ghsync, notify, optfmt, options, score, signals

_REPO = os.environ.get("SQZDOTS_REPO", "waranoe177/squeeze-scanner")


def _ping_healthcheck(url):
    """Best-effort deadman ping. Never raises — a ping failure must not kill serve."""
    try:
        import requests
        requests.get(url, timeout=10)
    except Exception:
        pass
```

Replace `_load_results` (bot.py:37-43) so it sources from ghsync:

```python
def _load_results(path=None):
    """Latest daily-scan snapshot via raw.githubusercontent (cached). Never raises."""
    return ghsync.fetch_results(_REPO)
```

In `_handle_trade_bare`, replace the committed-chart block (bot.py:429-440) with a raw fetch:

```python
    # send the daily chart (same picture the alert sent) via raw HTTPS, best-effort
    chart_rel = payload.get("chart")
    if chart_rel:
        cpath = Path(tmp_dir or tempfile.gettempdir()) / f"anchor_{symbol}.png"
        if ghsync.fetch_chart(_REPO, symbol, str(cpath)):
            try:
                send_photo(token, chat_id, str(cpath), caption=notify._fired_line(payload))
            except Exception as exc:
                print(f"  [bot] anchor chart send failed for {symbol}: {exc}")
```

In `poll_once`, replace the "1) decisions" block (bot.py:196-200) so decisions go to `decisions.jsonl` via ghsync instead of the local ledger:

```python
    # 1) decisions -> append to the append-only decisions.jsonl via the Contents
    #    API. The bot NEVER writes signals.jsonl (the daily scan folds these in).
    gh_token = os.environ.get("GITHUB_TOKEN")
    parsed = [p for p in (decisions.parse_decision(u) for u in owned) if p]
    for p in parsed:
        if gh_token and not ghsync.append_decision(_REPO, p, gh_token):
            print(f"  [bot] decision append failed: {p}")
```

Delete the now-unused `from scanner import ledger` line inside `poll_once` (bot.py:178) and the `ledger_path`/`ledger.load`/`ledger.save` usage; drop the `ledger_path` parameter's body use (keep the param for signature stability, mark unused).

In `serve` (bot.py:246-254), ping the deadman ONLY after a successful `poll_once`:

```python
    hc = os.environ.get("HEALTHCHECK_URL")
    while True:
        try:
            poll_once(token=token, chat_id=chat_id, ledger_path=ledger_path,
                      state_path=state_path, timeout=poll_timeout)
            if hc:
                _ping_healthcheck(hc)          # only reached on a clean poll
        except KeyboardInterrupt:
            print("\n[bot] stopped.")
            return
        except Exception as exc:               # transient error — keep serving, do NOT ping
            print(f"[bot] poll error (continuing): {exc}")
```

- [ ] **Step 4: Run the full bot suite**

Run: `python -m pytest tests/test_bot.py -q`
Expected: PASS. Fix any existing poll_once tests that asserted on ledger writes — they now assert on `ghsync.append_decision` (update them, do not delete coverage).

- [ ] **Step 5: Commit**

```bash
git add scanner/bot.py tests/test_bot.py
git commit -m "feat(bot): source data + persist decisions via ghsync; deadman ping after clean poll"
```

---

### Task 3: `scanner/run.py` — fold `decisions.jsonl` into `signals.jsonl`

The daily scan is the SOLE writer of `signals.jsonl`; here it absorbs the bot's decisions idempotently (write-once via `apply_decisions`).

**Files:**
- Modify: `scanner/run.py:54-57` (after `ledger.update`, before `_persist`)
- Test: `tests/test_run.py` (add)

**Interfaces:**
- Consumes: `decisions.apply_decisions(records, parsed)` (decisions.py:56), `ledger.load`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run.py (add)
import json
from scanner import run as run_mod


def test_fold_decisions_applies_to_records(tmp_path):
    rec = {"id": "COST-2026-09-10", "symbol": "COST", "signal_date": "2026-09-10",
           "telegram_msg_id": 42, "status": "open", "entry_date": None}
    dpath = tmp_path / "decisions.jsonl"
    dpath.write_text(json.dumps({"decision": "go", "decided_at": "2026-09-11T00:00:00+00:00",
                                 "reply_to_msg_id": 42, "symbol": None}) + "\n")
    run_mod._fold_decisions([rec], str(dpath))
    assert rec["decision"] == "go"


def test_fold_decisions_noop_when_file_absent(tmp_path):
    rec = {"id": "X", "symbol": "X", "signal_date": "2026-09-10", "telegram_msg_id": 1,
           "status": "open"}
    run_mod._fold_decisions([rec], str(tmp_path / "missing.jsonl"))
    assert "decision" not in rec
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_run.py -k fold -q`
Expected: FAIL — `run._fold_decisions` does not exist.

- [ ] **Step 3: Implement**

Add to `scanner/run.py` (top-level) and call it after `ledger.update(records, frames)` (run.py:57):

```python
def _fold_decisions(records, path="ledger/decisions.jsonl"):
    """Apply the Fly bot's go/pass decisions (append-only decisions.jsonl) onto
    the ledger records. Idempotent: apply_decisions is write-once."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return records
    parsed = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    from scanner import decisions
    return decisions.apply_decisions(records, parsed)
```

Call site (after line 57):

```python
    ledger.append_fired(records, results["fired"])
    ledger.update(records, frames)
    _fold_decisions(records)          # <-- fold the Fly bot's go/pass decisions
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_run.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scanner/run.py tests/test_run.py
git commit -m "feat(run): fold bot decisions.jsonl into signals.jsonl in the daily scan"
```

---

### Task 4: `.gitattributes` — union-merge the append-only decisions file

**Files:**
- Create (or append): `.gitattributes`

- [ ] **Step 1: Create the file**

```gitattributes
ledger/decisions.jsonl merge=union
```

- [ ] **Step 2: Verify**

Run: `git check-attr merge ledger/decisions.jsonl`
Expected: `ledger/decisions.jsonl: merge: union`

- [ ] **Step 3: Commit**

```bash
git add .gitattributes
git commit -m "chore: union-merge ledger/decisions.jsonl (append-only, belt-and-suspenders)"
```

---

### Task 5: `Dockerfile` — baked-code image

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
# git not needed at runtime (no clone); build tools only for wheels if any
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scanner/ ./scanner/
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh
RUN chmod +x ./deploy/entrypoint.sh
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["./deploy/entrypoint.sh"]
```

```gitignore
# .dockerignore
.git
out/
site/
tests/
docs/
*.png
.venv/
```

- [ ] **Step 2: Verify it builds (if Docker is available locally; else deferred to first `fly deploy`)**

Run: `docker build -t sqzdots-bot . 2>&1 | tail -5` (skip with a note if Docker absent — Fly builds remotely).

- [ ] **Step 3: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat(deploy): container image with baked code"
```

---

### Task 6: `fly.toml` — single 1 GB machine + tiny volume

**Files:**
- Create: `fly.toml`

- [ ] **Step 1: Write fly.toml** (app name is a placeholder set at `fly launch` time)

```toml
app = "sqzdots-bot"
primary_region = "iad"

[build]

[env]
  SQZDOTS_REPO = "waranoe177/squeeze-scanner"
  SQZDOTS_STATE_PATH = "/data/telegram_state.json"

# No [http_service] — this is an outbound long-poll worker, no inbound traffic.

[[vm]]
  size = "shared-cpu-1x"
  memory = "1gb"
  cpus = 1

[[mounts]]
  source = "sqzdots_data"
  destination = "/data"

[deploy]
  strategy = "immediate"   # single machine: stop-then-start in place, never two pollers

# Set min=max=1 (no autoscaling) via: fly scale count 1
```

- [ ] **Step 2: Verify syntax**

Run: `fly config validate` (from the repo root, after `fly launch --no-deploy`; skip with a note if flyctl not yet installed — done during the guided deploy).

- [ ] **Step 3: Commit**

```bash
git add fly.toml
git commit -m "feat(deploy): fly.toml — single 1GB machine, /data volume, no inbound"
```

---

### Task 7: `deploy/entrypoint.sh` — start serve with offset on the volume

**Files:**
- Create: `deploy/entrypoint.sh`

- [ ] **Step 1: Write the entrypoint**

```bash
#!/usr/bin/env sh
set -eu
# Offset lives on the /data volume so restarts don't re-fetch ~24h of updates.
exec python -m scanner.bot --serve --state "${SQZDOTS_STATE_PATH:-/data/telegram_state.json}"
```

- [ ] **Step 2: Verify it's executable and parses**

Run: `sh -n deploy/entrypoint.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add deploy/entrypoint.sh
git commit -m "feat(deploy): entrypoint runs serve with offset on the /data volume"
```

---

### Task 8: `deploy/README.md` — operator runbook

**Files:**
- Create: `deploy/README.md`

- [ ] **Step 1: Write the runbook** (no code to test; content must be complete and correct)

Include, as numbered steps: install flyctl; `fly launch --no-deploy` (name the app, decline a DB); `fly volumes create sqzdots_data --size 1 --region iad`; create the fine-grained GitHub PAT (repo `waranoe177/squeeze-scanner`, Contents: read+write) and note its ~1-year expiry (rotate reminder); `fly secrets set TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... GITHUB_TOKEN=... HEALTHCHECK_URL=...`; create a free healthchecks.io check (period ~15m, grace ~15m) and paste its ping URL as `HEALTHCHECK_URL`; set a Fly org spend cap (Billing → spending limit, e.g. $10/mo); `fly deploy`; `fly scale count 1` (enforce single machine); verify with `fly logs` and by sending `trade COST` (expect a reply in 1-3s). Break-glass: if the deadman alerts that the bot is dark, trigger the manual fallback poller (GitHub → Actions → "Telegram Chart Bot" → Run workflow) to drain the queue until Fly is back; only trigger it when Fly is confirmed down (else two pollers briefly 409). Rollback: `git revert` the bot.yml cron change + re-enable the schedule, then `fly scale count 0`.

- [ ] **Step 2: Verify links + commands are copy-pasteable** (self-review read-through).

- [ ] **Step 3: Commit**

```bash
git add deploy/README.md
git commit -m "docs(deploy): operator runbook for the Fly bot"
```

---

### Task 9: `.github/workflows/scan.yml` — rebase-retry on push

The scan and the bot's Contents-API commit both push to `main`. They touch DIFFERENT files (signals.jsonl vs decisions.jsonl) so there is no content conflict, but the ref update can still be rejected — retry with rebase.

**Files:**
- Modify: `.github/workflows/scan.yml:45-52`

- [ ] **Step 1: Replace the commit/push step**

```yaml
      - name: Commit results + ledger
        if: always()
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add out/ ledger/
          git diff --staged --quiet && exit 0
          git commit -m "scan results $(date -u +%Y-%m-%d)"
          for i in 1 2 3; do
            git pull --rebase --autostash origin main && git push && exit 0
            echo "push rejected, retry $i"; sleep 3
          done
          echo "push failed after retries" >&2; exit 1
```

- [ ] **Step 2: Verify YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/scan.yml'))" && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/scan.yml
git commit -m "fix(ci): scan push uses pull --rebase + retry (race with the Fly bot's Contents-API commit)"
```

---

### Task 10: `.github/workflows/bot.yml` — manual fallback only (strip cron)

**Files:**
- Modify: `.github/workflows/bot.yml`

- [ ] **Step 1: Remove the `schedule:` block, keep `workflow_dispatch: {}`**

Change the `on:` block to:

```yaml
on:
  # Manual break-glass ONLY. The always-on Fly.io bot is the sole poller; run
  # this to drain the queue if Fly is down. Do NOT re-add a cron schedule
  # (two getUpdates consumers = Telegram 409).
  workflow_dispatch: {}
```

Add `GITHUB_TOKEN` + `SQZDOTS_REPO` to the poll step's `env:` so the fallback can append decisions via the Contents API too:

```yaml
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          SQZDOTS_REPO: waranoe177/squeeze-scanner
```

- [ ] **Step 2: Verify YAML + no `schedule:` remains**

Run: `python -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/bot.yml')); assert 'schedule' not in d['on']; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/bot.yml
git commit -m "chore(ci): bot.yml is manual-fallback only (strip cron; keep workflow_dispatch)"
```

---

### Task 11: Full-suite green + final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, count >= 266 + the new tests (ghsync 9, bot +4, run +2).

- [ ] **Step 2: Grep for the invariant**

Run: `python -c "import re,sys; s=open('scanner/bot.py').read(); assert 'ledger.save' not in s, 'bot must not write signals.jsonl'; print('invariant OK')"`
Expected: `invariant OK`

- [ ] **Step 3: Commit any test fixes**

```bash
git add -A && git commit -m "test: phase 0 suite green" || echo "nothing to commit"
```

---

## Self-Review

**1. Spec coverage (issue #1 revised comment):**
- Single 1 GB machine, in-place deploy → Task 6 (`fly.toml`) + runbook `fly scale count 1`.
- Code baked in image → Task 5.
- Read results.json/charts via raw HTTPS → Task 1 (`fetch_results`/`fetch_chart`) + Task 2 wiring.
- go/pass → append-only decisions.jsonl via Contents API → Task 1 (`append_decision`) + Task 2 (`poll_once`).
- Scan folds decisions.jsonl → Task 3.
- Offset on /data volume → Task 6 env + Task 7 entrypoint `--state`.
- Deadman after successful poll + existing getUpdates timeout → Task 2 (`serve`), timeout confirmed at decisions.py:82.
- Secrets → Task 6 env + Task 8 runbook.
- bot.yml manual-fallback only → Task 10.
- scan.yml rebase-retry → Task 9.
- `.gitattributes merge=union` → Task 4.
No gaps.

**2. Placeholder scan:** every code step has real code; the only prose-only task is the runbook (Task 8), which is documentation content, not code. No TBD/TODO.

**3. Type consistency:** `decision` dict shape `{decision, decided_at, reply_to_msg_id, symbol}` is produced by `decisions.parse_decision` (decisions.py:36-41), written by `ghsync.append_decision` (Task 1), and consumed by `decisions.apply_decisions` (decisions.py:56) via `run._fold_decisions` (Task 3) — consistent end to end. `_REPO` env name `SQZDOTS_REPO` matches across bot.py, fly.toml, bot.yml.

## NOT in scope (deferred)
- Webhooks (long-poll first); multi-user allowlist (Phase 1); yfinance caching / real data API (Phase 2 — fixed-Fly-IP throttling risk); state → DB (Phase 3).
