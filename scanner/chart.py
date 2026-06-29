"""Render a glanceable price chart with the 21/50/200 EMAs and signal markers.

Used for the Telegram snapshot. Uses the non-interactive Agg backend so it runs
headless in GitHub Actions.
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from scanner import signals  # noqa: E402


def render(df, symbol: str, out_path: str, lookback: int = 120) -> str:
    """Render the last `lookback` bars of `df` to a PNG at `out_path`."""
    enriched = signals.analyze(df).tail(lookback)
    idx = enriched.index

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=110)
    ax.plot(idx, enriched["close"], color="#111", lw=1.3, label="Close")
    ax.plot(idx, enriched["ema21"], color="#e8a020", lw=1.0, label="EMA21")
    ax.plot(idx, enriched["sma50"], color="#2a8", lw=1.0, ls="--", label="SMA50")
    ax.plot(idx, enriched["sma200"], color="#39c", lw=1.0, ls="--", label="SMA200")

    # shade bars that are in a squeeze
    sqz = enriched["squeeze_on"].fillna(False).to_numpy()
    ax.fill_between(idx, enriched["low"].min(), enriched["high"].max(),
                    where=sqz, color="#f4a300", alpha=0.06, step="mid")

    bulls = enriched[enriched["scanner_bull"]]
    bears = enriched[enriched["scanner_bear"]]
    ax.scatter(bulls.index, bulls["low"] * 0.995, marker="^", color="#10a050",
               s=90, zorder=5, label="BUY")
    ax.scatter(bears.index, bears["high"] * 1.005, marker="v", color="#d0202a",
               s=90, zorder=5, label="SELL")

    last = enriched.iloc[-1]
    ax.set_title(f"{symbol}  ·  {idx[-1].date()}  ·  close {last['close']:.2f}",
                 fontsize=12, fontweight="bold")
    ax.legend(loc="upper left", fontsize=8, ncol=5, frameon=False)
    ax.grid(alpha=0.15)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
