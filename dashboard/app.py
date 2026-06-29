"""Streamlit dashboard for the squeeze scanner.

Reads out/results.json (written by `python -m scanner.run`) and shows the daily
fired signals plus an interactive chart per ticker. Phone-accessible when
deployed to Streamlit Community Cloud.

Run locally:  streamlit run dashboard/app.py
"""

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Make the `scanner` package importable when run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scanner import data, signals  # noqa: E402

RESULTS_PATH = ROOT / "out" / "results.json"

st.set_page_config(page_title="Squeeze Scanner", page_icon="📈", layout="wide")


@st.cache_data(ttl=900)
def load_results() -> dict | None:
    if RESULTS_PATH.exists():
        return json.loads(RESULTS_PATH.read_text())
    return None


@st.cache_data(ttl=900)
def load_frame(symbol: str) -> pd.DataFrame:
    frames = data.fetch_daily([symbol], period="2y")
    return frames.get(symbol, pd.DataFrame())


def chart_figure(symbol: str) -> go.Figure | None:
    df = load_frame(symbol)
    if df.empty:
        return None
    out = signals.analyze(df).tail(160)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=out.index, open=out["open"], high=out["high"],
                                 low=out["low"], close=out["close"], name=symbol,
                                 increasing_line_color="#10a050", decreasing_line_color="#d0202a"))
    for col, color in [("ema21", "#e8a020"), ("sma50", "#2a8"), ("sma200", "#39c")]:
        fig.add_trace(go.Scatter(x=out.index, y=out[col], name=col.upper(),
                                 line=dict(width=1, color=color)))
    bulls, bears = out[out["scanner_bull"]], out[out["scanner_bear"]]
    fig.add_trace(go.Scatter(x=bulls.index, y=bulls["low"] * 0.99, mode="markers",
                             marker=dict(symbol="triangle-up", color="#10a050", size=12), name="BUY"))
    fig.add_trace(go.Scatter(x=bears.index, y=bears["high"] * 1.01, mode="markers",
                             marker=dict(symbol="triangle-down", color="#d0202a", size=12), name="SELL"))
    fig.update_layout(height=520, xaxis_rangeslider_visible=False,
                      margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h"))
    return fig


st.title("📈 Squeeze Scanner")

results = load_results()
if results is None:
    st.warning("No results yet. Run `python -m scanner.run` to generate out/results.json.")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("As-of bar", results["as_of"])
c2.metric("Signals fired", results["fired_count"])
c3.metric("Universe", results["universe"])
st.caption(f"Generated {results['generated_at']}")

st.subheader("Signals fired")
if results["fired"]:
    table = pd.DataFrame(results["fired"])[
        ["symbol", "direction", "grade", "close", "rsi", "target_up", "target_dn", "stop"]
    ]
    st.dataframe(table, use_container_width=True, hide_index=True)
else:
    st.info("No signals fired on the latest bar.")

if results["watching"]:
    st.subheader("Coiled — in squeeze, not yet aligned")
    st.write(" · ".join(results["watching"]))

st.subheader("Chart")
fired_syms = [p["symbol"] for p in results["fired"]]
options = fired_syms + [s for s in results["watching"] if s not in fired_syms]
if options:
    symbol = st.selectbox("Ticker", options)
    fig = chart_figure(symbol)
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.error(f"Could not load data for {symbol}.")
