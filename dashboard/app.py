"""Streamlit dashboard for the squeeze scanner.

Reads out/results.json (written by `python -m scanner.run`) and shows the daily
fired signals ranked by conviction, the GO/WATCH/PASS verdict with catalysts and
risks, and the full TOS-matched chart per ticker. Phone-accessible on Streamlit
Community Cloud.

Set ANTHROPIC_API_KEY in Streamlit secrets to enable the on-demand "Re-run LLM
eval" button (the daily Action already runs it if the secret is set there).

Run locally:  streamlit run dashboard/app.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Surface the Streamlit secret as an env var so llm_eval picks it up.
# st.secrets raises if no secrets file exists — guard it.
try:
    if not os.environ.get("ANTHROPIC_API_KEY") and "ANTHROPIC_API_KEY" in st.secrets:
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass

from scanner import chart, data, decisions, ledger, llm_eval, score  # noqa: E402

RESULTS_PATH = ROOT / "out" / "results.json"

st.set_page_config(page_title="Squeeze Scanner", page_icon="📈", layout="wide")


@st.cache_data(ttl=900)
def load_results() -> dict | None:
    if RESULTS_PATH.exists():
        return json.loads(RESULTS_PATH.read_text())
    return None


@st.cache_data(ttl=900, show_spinner=False)
def render_chart_png(symbol: str, lookback: int = 90) -> bytes | None:
    """Render the full layered chart for a symbol and return PNG bytes."""
    frames = data.fetch_daily([symbol], period="2y")
    df = frames.get(symbol)
    if df is None or df.empty:
        return None
    out = Path(tempfile.gettempdir()) / f"sqz_{symbol}.png"
    chart.render_layers(df, symbol, str(out), lookback=lookback)
    return out.read_bytes()


def _rec_badge(rec: str | None) -> str:
    return {"GO": "🟢 GO", "WATCH": "🟡 WATCH", "PASS": "🔴 PASS"}.get(rec, "—")


st.title("📈 Squeeze Scanner")

results = load_results()
if results is None:
    st.warning("No results yet. Run `python -m scanner.run` to generate out/results.json.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("As-of bar", results["as_of"])
c2.metric("Signals fired", results["fired_count"])
c3.metric("Universe", results["universe"])
c4.metric("Watching", len(results["watching"]))
st.caption(f"Generated {results['generated_at']}")

# ---- fired table -----------------------------------------------------------
st.subheader("Signals fired — ranked by conviction")
fired = results["fired"]
if fired:
    table = pd.DataFrame([
        {
            "symbol": p["symbol"],
            "dir": p["direction"],
            "score": p.get("score"),
            "grade": p.get("conviction_grade", ""),
            "verdict": p.get("recommendation") or "—",
            "news": p.get("stance") or "—",
            "close": round(p["close"], 2),
            "target↑": p["target_up"],
            "stop": p["stop"],
            "R:R": (p.get("score_parts") or {}).get("rr"),
        }
        for p in fired
    ])
    st.dataframe(table, use_container_width=True, hide_index=True)
else:
    st.info("No signals fired on the latest bar.")

if results["watching"]:
    st.subheader("Coiled — in squeeze, not yet aligned")
    st.write(" · ".join(results["watching"]))

# ---- decision review -------------------------------------------------------
LEDGER_PATH = ROOT / "ledger" / "signals.jsonl"
led_records = ledger.load(LEDGER_PATH)
dec_rows = decisions.decision_table(led_records)
if dec_rows:
    st.subheader("Decision review — your calls vs the machine")
    d = ledger.stats(led_records)["decisions"]

    def _b(b):
        return f"{b['n']} · {b['avg_r']:+.2f}R" if b["n"] and b["avg_r"] is not None else f"{b['n']}"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("GO (clean)", _b(d["go"]))
    m2.metric("PASS (clean)", _b(d["pass"]))
    alpha = d["selection_alpha"]
    m3.metric("Selection alpha", f"{alpha:+.3f}R" if alpha is not None else "—")
    m4.metric("Late / undecided", f"{d['late_n']} / {d['undecided']['n']}")

    closed_rows = [r for r in dec_rows if r["r_multiple"] is not None and not r["late"]]
    if len(closed_rows) >= 2:
        curves = {}
        for bucket in ("go", "pass"):
            cum, series = 0.0, {}
            for r in sorted((x for x in closed_rows if x["decision"] == bucket),
                            key=lambda x: x["exit_date"]):
                cum += r["r_multiple"]
                series[r["exit_date"]] = cum
            curves[bucket.upper()] = series
        st.line_chart(pd.DataFrame(curves).ffill())

    n_go, n_pass = d["go"]["n"], d["pass"]["n"]
    if n_go >= 30 and n_pass >= 30:
        import math
        rs_go = [r["r_multiple"] for r in closed_rows if r["decision"] == "go"]
        rs_pa = [r["r_multiple"] for r in closed_rows if r["decision"] == "pass"]
        se = math.sqrt(pd.Series(rs_go).var(ddof=1) / n_go
                       + pd.Series(rs_pa).var(ddof=1) / n_pass)
        st.caption(f"alpha t-stat ≈ {alpha / se:+.2f} (Welch, clean closed only)")
    else:
        st.caption(f"t-stat shown once both buckets reach 30 clean decisions "
                   f"(now {n_go} / {n_pass})")

    st.dataframe(pd.DataFrame(dec_rows), use_container_width=True, hide_index=True)

# ---- per-ticker detail -----------------------------------------------------
st.subheader("Ticker detail")
fired_syms = [p["symbol"] for p in fired]
options = fired_syms + [s for s in results["watching"] if s not in fired_syms]
if not options:
    st.stop()

symbol = st.selectbox("Ticker", options)
payload = next((p for p in fired if p["symbol"] == symbol), None)

left, right = st.columns([3, 2])

with left:
    png = render_chart_png(symbol)
    if png:
        st.image(png, use_container_width=True)
    else:
        st.error(f"Could not load data for {symbol}.")

with right:
    if payload:
        st.metric("Conviction", f"{payload.get('score')}/100  ({payload.get('conviction_grade','')})")
        parts = payload.get("score_parts") or {}
        sub = parts.get("parts") or {}
        st.caption(
            f"confluence {parts.get('confluence','?')}/60 · strength {parts.get('strength','?')}/40  "
            f"| momentum {sub.get('momentum','?')} · moxie {sub.get('moxie','?')} "
            f"· fresh {sub.get('freshness','?')} · R:R {sub.get('risk_reward','?')}"
        )
        st.caption(f"R:R {parts.get('rr','?')} · ATR% {parts.get('atr_pct','?')}")

        llm = payload.get("llm")
        if payload.get("recommendation"):
            st.markdown(f"### {_rec_badge(payload['recommendation'])}  "
                        f"(final {payload.get('final_score','?')})")
        if llm:
            st.markdown(f"**News stance:** {llm.get('stance','?')} "
                        f"(qual {llm.get('qual_score','?')}/100, conf {llm.get('confidence','?')})")
            if llm.get("rationale"):
                st.write(llm["rationale"])
            if llm.get("catalysts"):
                st.markdown("**Catalysts**")
                for c in llm["catalysts"]:
                    st.markdown(f"- {c}")
            if llm.get("risks"):
                st.markdown("**Risks**")
                for r in llm["risks"]:
                    st.markdown(f"- {r}")
        elif payload.get("recommendation") is None:
            st.info("LLM eval not run for this scan. Add ANTHROPIC_API_KEY to enable it.")
    else:
        st.caption("Watching (not fired). Chart on the left.")

    # On-demand live LLM eval (needs ANTHROPIC_API_KEY in Streamlit secrets)
    if os.environ.get("ANTHROPIC_API_KEY"):
        if st.button(f"🧠 Re-run LLM eval on {symbol} (live)"):
            with st.spinner("Fetching news + asking Claude…"):
                frames = data.fetch_daily([symbol], period="2y")
                df = frames.get(symbol)
                if df is not None and not df.empty:
                    quant = score.conviction(df, symbol=symbol)
                    res = llm_eval.evaluate(quant, symbol)
                    st.json({k: res.get(k) for k in
                             ("recommendation", "final_score", "stance", "llm")})
    else:
        st.caption("Set ANTHROPIC_API_KEY in Streamlit secrets to enable live LLM eval.")
