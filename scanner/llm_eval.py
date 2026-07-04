"""LLM eval agent: a qualitative layer on top of the deterministic quant score.

For a fired ticker, it gathers recent news headlines (free, via yfinance),
asks Claude for a qualitative read (stance, catalysts, risks, a 0-100 qualitative
score), and combines that with the quant conviction score into a final GO / WATCH
/ PASS recommendation.

The deterministic engine stays the source of truth for the *signal*; the LLM only
adds context/judgment — it never recomputes indicators. Requires
ANTHROPIC_API_KEY (env var / GitHub secret); returns None when it's absent so the
pipeline degrades gracefully to the quant score alone.
"""

import json
import os

MODEL = "claude-opus-4-8"

# Structured-output schema for the qualitative read.
_QUAL_SCHEMA = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["bullish", "neutral", "bearish"]},
        "qual_score": {"type": "integer"},
        "catalysts": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["stance", "qual_score", "catalysts", "risks", "rationale", "confidence"],
    "additionalProperties": False,
}

_STANCE_PENALTY = {"bullish": 1.0, "neutral": 0.9, "bearish": 0.65}


def gather_news(symbol: str, limit: int = 8) -> list[dict]:
    """Pull recent news headlines for a ticker (best-effort, never raises)."""
    try:
        import yfinance as yf
        raw = yf.Ticker(symbol).news or []
    except Exception:
        return []
    return normalize_news(raw)[:limit]


def normalize_news(raw: list[dict]) -> list[dict]:
    """Normalize yfinance news (new nested 'content' shape or legacy flat)."""
    out = []
    for item in raw:
        c = item.get("content", item)
        pub = c.get("provider", {})
        publisher = pub.get("displayName") if isinstance(pub, dict) else item.get("publisher", "")
        title = c.get("title") or item.get("title")
        if not title:
            continue
        out.append({
            "title": title,
            "publisher": publisher or item.get("publisher", ""),
            "summary": c.get("summary", "") or item.get("summary", ""),
            "published": c.get("pubDate", "") or "",
        })
    return out


def build_prompt(symbol: str, quant: dict, news: list[dict]):
    """Assemble (system, user) for the qualitative read."""
    system = (
        "You are a sell-side equity analyst assistant. A deterministic technical "
        "system has already fired a swing-trade signal on a ticker (multi-day hold). "
        "Your job is the QUALITATIVE read from recent news and context: is the "
        "near-term narrative supportive, neutral, or a warning? Do NOT recompute or "
        "second-guess the technicals. Be specific and concise. Score the qualitative "
        "picture 0-100 (100 = strong supportive catalysts, 50 = neutral, 0 = clear "
        "negative catalysts). Give a stance for a multi-day long, key catalysts, key "
        "risks, a one-paragraph rationale, and your confidence 0-1."
    )
    lines = [
        f"Ticker: {symbol}",
        f"Technical signal: {quant.get('direction', 'bull')} "
        f"(conviction {quant.get('score')}/100, grade {quant.get('grade', '')}, "
        f"R:R {quant.get('rr', '')}, ATR% {quant.get('atr_pct', '')})",
        "",
        "Recent news headlines:",
    ]
    if news:
        for n in news:
            lines.append(f"- {n['title']} ({n.get('publisher', '')})"
                         + (f" — {n['summary'][:160]}" if n.get("summary") else ""))
    else:
        lines.append("(no recent news found)")
    return system, "\n".join(lines)


def qualitative_eval(symbol: str, quant: dict, news: list[dict],
                     model: str = MODEL, client=None) -> dict | None:
    """Ask Claude for the qualitative read. Returns None if no API key."""
    if client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    system, user = build_prompt(symbol, quant, news)
    resp = client.messages.create(
        model=model,
        max_tokens=1200,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": _QUAL_SCHEMA}},
    )
    text = next(b.text for b in resp.content if getattr(b, "type", None) == "text")
    data = json.loads(text)
    data["qual_score"] = max(0, min(100, int(data.get("qual_score", 50))))
    data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    return data


def combine(quant_score: float, qual: dict) -> dict:
    """Blend quant conviction with the qualitative read into a final call."""
    q = qual["qual_score"]
    stance = qual.get("stance", "neutral")
    blend = 0.6 * quant_score + 0.4 * q
    final = round(blend * _STANCE_PENALTY.get(stance, 0.9), 1)
    rec = "GO" if final >= 70 else "WATCH" if final >= 55 else "PASS"
    return {
        "final_score": final,
        "recommendation": rec,
        "quant_score": round(quant_score, 1),
        "qual_score": q,
        "stance": stance,
        "confidence": qual.get("confidence", 0.5),
    }


def evaluate(quant: dict, symbol: str) -> dict:
    """Full pipeline for one fired ticker: news -> qualitative -> combined call.
    Returns the quant dict enriched with the qualitative read (or a note that the
    LLM layer was skipped when no API key is configured)."""
    news = gather_news(symbol)
    qual = qualitative_eval(symbol, quant, news)
    result = dict(quant)
    if qual is None:
        result["llm"] = None
        result["recommendation"] = None
        return result
    result["llm"] = qual
    result.update(combine(quant["score"], qual))
    return result
