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
