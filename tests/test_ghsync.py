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


def test_fetch_chart_suffix_selects_variant(tmp_path):
    http = FakeHTTP()
    http.add("GET", "raw.githubusercontent.com", FakeResp(200, content=b"\x89PNGmtf"))
    dest = tmp_path / "COST_mtf.png"
    out = ghsync.fetch_chart("owner/repo", "COST", str(dest), http=http, suffix="_mtf")
    assert out == str(dest)
    assert any("out/charts/COST_mtf.png" in c[1] for c in http.calls)


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
