"""Tests for the LLM eval agent's pure logic: news normalization, prompt
building, quant/qual combination, and the response parser (with a fake client).
The live Claude call itself is exercised by a separate smoke run.
"""

import json

from scanner import llm_eval


def test_normalize_news_handles_nested_content_shape():
    raw = [
        {"content": {"title": "Deal announced", "provider": {"displayName": "Reuters"},
                     "summary": "A big deal.", "pubDate": "2026-07-01T00:00:00Z"}},
        {"title": "Old flat shape", "publisher": "AP"},  # legacy shape
    ]
    out = llm_eval.normalize_news(raw)
    assert out[0]["title"] == "Deal announced"
    assert out[0]["publisher"] == "Reuters"
    assert out[1]["title"] == "Old flat shape"


def test_build_prompt_includes_symbol_score_and_news():
    quant = {"symbol": "IYT", "score": 89.2, "grade": "A+", "direction": "bull",
             "rr": 0.6, "atr_pct": 1.8}
    news = [{"title": "Freight demand rising", "publisher": "WSJ", "summary": "..."}]
    system, user = llm_eval.build_prompt("IYT", quant, news)
    assert "analyst" in system.lower()
    assert "IYT" in user
    assert "89.2" in user
    assert "Freight demand rising" in user


def test_build_prompt_notes_when_no_news():
    system, user = llm_eval.build_prompt("XYZ", {"symbol": "XYZ", "score": 70}, [])
    assert "no recent news" in user.lower()


# ---------------------------------------------------------------------------
# combine
# ---------------------------------------------------------------------------

def _qual(stance="bullish", qual_score=80, confidence=0.8):
    return {"stance": stance, "qual_score": qual_score, "confidence": confidence,
            "catalysts": [], "risks": [], "rationale": "x"}


def test_combine_bullish_high_scores_is_go():
    r = llm_eval.combine(88, _qual("bullish", 85))
    assert r["recommendation"] == "GO"
    assert r["final_score"] >= 70


def test_combine_bearish_news_penalizes_and_downgrades():
    bull = llm_eval.combine(88, _qual("bullish", 85))
    bear = llm_eval.combine(88, _qual("bearish", 85))
    assert bear["final_score"] < bull["final_score"]  # same numbers, bearish penalized


def test_combine_recommendation_thresholds():
    assert llm_eval.combine(40, _qual("neutral", 40))["recommendation"] == "PASS"
    assert llm_eval.combine(62, _qual("neutral", 60))["recommendation"] == "WATCH"


# ---------------------------------------------------------------------------
# parse response (fake client, no network)
# ---------------------------------------------------------------------------

class _Block:
    type = "text"
    def __init__(self, text): self.text = text

class _Resp:
    def __init__(self, text): self.content = [_Block(text)]

class _FakeMessages:
    def __init__(self, payload): self._payload = payload
    def create(self, **kwargs): return _Resp(json.dumps(self._payload))

class _FakeClient:
    def __init__(self, payload): self.messages = _FakeMessages(payload)


def test_qualitative_eval_parses_and_clamps(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    payload = {"stance": "bullish", "qual_score": 130, "confidence": 0.9,
               "catalysts": ["earnings beat"], "risks": ["fuel costs"], "rationale": "ok"}
    client = _FakeClient(payload)
    out = llm_eval.qualitative_eval("IYT", {"symbol": "IYT", "score": 89}, [], client=client)
    assert out["stance"] == "bullish"
    assert out["qual_score"] == 100  # clamped from 130


def test_qualitative_eval_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_eval.qualitative_eval("IYT", {"symbol": "IYT"}, []) is None
