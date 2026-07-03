"""Render a glanceable price chart with the 21/50/200 EMAs and signal markers.

Used for the Telegram snapshot. Uses the non-interactive Agg backend so it runs
headless in GitHub Actions.
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from scanner import indicators as ind  # noqa: E402
from scanner import signals  # noqa: E402

GREEN, RED, BLUE, ORANGE = "#10a050", "#d0202a", "#1a2fd0", "#e8901a"


def _macd_colors(diff):
    """Green/blue/red/orange per the MACD study coloring (vs prior bar)."""
    prev = diff.shift(1)
    out = []
    for d, p in zip(diff, prev):
        if d >= 0:
            out.append(GREEN if d > p else BLUE)
        else:
            out.append(RED if d < p else ORANGE)
    return out


def _draw_candles(ax, pos, e, colors):
    """Candlesticks on an ordinal x-axis (no weekend/holiday gaps)."""
    for xi, o, h, low, c, col in zip(pos, e["open"], e["high"], e["low"], e["close"], colors):
        ax.vlines(xi, low, h, color=col, lw=0.7, zorder=3)
        lower, height = min(o, c), max(abs(c - o), 1e-4)
        ax.add_patch(Rectangle((xi - 0.3, lower), 0.6, height,
                               facecolor=col, edgecolor=col, lw=0.4, zorder=3))


def _colored_line(ax, x, y, colors, lw=2.6):
    """A line whose segments take per-point colors (like TOS AssignValueColor)."""
    pts = np.array([x, y]).T.reshape(-1, 1, 2)
    segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
    ax.add_collection(LineCollection(segs, colors=colors[1:], linewidths=lw, zorder=3))


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


def _macd_bar_colors(diff):
    """Green/blue/red/orange per the MACD study coloring (vs prior bar)."""
    prev = diff.shift(1)
    colors = []
    for d, p in zip(diff, prev):
        if d >= 0:
            colors.append("#10a050" if d > p else "#3a7bd5")   # green / blue
        else:
            colors.append("#d0202a" if d < p else "#e8901a")   # red / orange
    return colors


# B3 Super dots rows: (column, y-level, marker, {state: color})
_B3_SPEC = [
    ("scanner",   5, "o", {"bull": "#00e5ff", "bear": "#ff2bd6"}),
    ("sqz",       4, "^", {"bull": "#00e5ff", "bear": "#ff2bd6"}),
    ("sqzstack",  3, "o", {"bull": "#ffffff", "bear": "#9aa0a6"}),
    ("stack1",    2, "o", {"bull": "#1fd655", "bear": "#ff3b3b", "neutral": "#f4d03f"}),
    ("structure", 1, "o", {"bull": "#1fd655", "bear": "#ff3b3b", "neutral": "#f4d03f"}),
]
_B3_LABELS = {
    "scanner": "Scanner", "sqz": "Sqz", "sqzstack": "Sqz+Stack",
    "stack1": "Stack", "structure": "Structure",
}


def _draw_b3_dots(ax, rows, size=40):
    """Draw the 7 B3 rows on `ax` (black background), on an ordinal x-axis."""
    n = len(rows)
    pos = np.arange(n)
    ax.set_facecolor("#000")
    for col, y, marker, cmap in _B3_SPEC:
        vals = rows[col].to_numpy()
        for state, color in cmap.items():
            mask = vals == state
            if mask.any():
                ax.scatter(pos[mask], np.full(mask.sum(), y), marker=marker,
                           c=color, s=size, edgecolors="none", zorder=3)
    yvals = [s[1] for s in _B3_SPEC]
    ax.set_ylim(min(yvals) - 0.6, max(yvals) + 0.6)
    ax.set_xlim(-1, n)
    ax.set_yticks(yvals)
    ax.set_yticklabels([_B3_LABELS[s[0]] for s in _B3_SPEC], color="#ddd", fontsize=8)
    for spine in ax.spines.values():
        spine.set_color("#333")


def render_b3_dots(df, symbol: str, out_path: str, lookback: int = 60) -> str:
    """Render the B3 Super dots multi-row box (black background, colored dots),
    matching the TOS lower study."""
    rows = signals.b3_rows(df).tail(lookback)
    n = len(rows)
    fig, ax = plt.subplots(figsize=(11, 3.2), dpi=110)
    fig.patch.set_facecolor("#000")
    _draw_b3_dots(ax, rows, size=42)

    step = max(1, n // 8)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([rows.index[i].strftime("%b %d") for i in ticks], color="#ddd")
    ax.tick_params(colors="#ddd")
    bd = signals.condition_breakdown(df)
    ax.set_title(f"{symbol} — B3 Super dots · bar {bd['date']}",
                 color="#fff", fontsize=12, fontweight="bold")
    ax.grid(axis="x", color="#222", lw=0.5)
    fig.tight_layout()
    fig.savefig(out_path, facecolor="#000")
    plt.close(fig)
    return out_path


def render_layers(df, symbol: str, out_path: str, lookback: int = 140) -> str:
    """Multi-panel diagnostic: each buy condition on its own row so it can be
    cross-checked layer-by-layer against the TOS studies.
    """
    full = signals.analyze(df)
    full["rev_rsi"] = ind.rev_eng_rsi(full["close"], 14)
    e = full.tail(lookback).copy()
    n = len(e)
    pos = np.arange(n)  # ordinal x -> no weekend/holiday gaps
    rows = signals.b3_rows(df).tail(lookback)
    fig, ax = plt.subplots(
        4, 1, sharex=True, figsize=(11, 12.5), dpi=110,
        gridspec_kw={"height_ratios": [4.2, 1.3, 2.0, 1.6]},  # price panel dominant
    )
    fig.patch.set_facecolor("#000")

    def _dark(a):
        a.set_facecolor("#000")
        a.tick_params(colors="#bbb")
        for s in a.spines.values():
            s.set_color("#444")
        a.yaxis.label.set_color("#ddd")
        a.grid(alpha=0.18, color="#333")

    sqz = e["squeeze_on"].fillna(False).to_numpy()

    # 0) Price candles (REV RSI) + EMA21 / SMA50 (cyan) / SMA200 (forest green)
    #    + ATR Stop bands + squeeze shading + BUY/SELL
    _dark(ax[0])
    colors = [RED if (r > em) else GREEN for r, em in zip(e["rev_rsi"], e["ema21"])]
    _draw_candles(ax[0], pos, e, colors)
    for col, c, ls in [("ema21", "#f0b030", "-"), ("sma50", "#00e5ff", ":"), ("sma200", "#228b22", ":")]:
        ax[0].plot(pos, e[col].to_numpy(), lw=1.6, color=c, ls=ls, label=col.upper())
    # ATR Stop bands (ATR Stop.docx): EMA21 +/- ATR(14)*2.5 — both purple
    atr = e["atr"].to_numpy()
    ema21v = e["ema21"].to_numpy()
    atr_top, atr_bot = ema21v + 2.5 * atr, ema21v - 2.5 * atr
    ax[0].plot(pos, atr_top, color="#a35bff", lw=1.1, label="ATR+")
    ax[0].plot(pos, atr_bot, color="#a35bff", lw=1.1, label="ATR-")
    bear = e["scanner_bear"].to_numpy()
    highs = e["high"].to_numpy()
    ax[0].scatter(pos[bear], highs[bear] * 1.015, marker="v", color="#ff2bd6", s=75, zorder=6, label="SELL")
    lo = min(e["low"].min(), atr_bot.min(), e["sma200"].min())
    hi = max(e["high"].max(), atr_top.max())
    pad = (hi - lo) * 0.04
    ax[0].set_xlim(-1, n); ax[0].set_ylim(lo - pad, hi + pad)
    ax[0].set_ylabel("Price · candles=REV RSI")
    leg = ax[0].legend(loc="upper left", fontsize=7.5, ncol=7, frameon=False)
    for t in leg.get_texts():
        t.set_color("#ccc")

    # 1) B3 Super dots box (moved below the price chart)
    _draw_b3_dots(ax[1], rows, size=22)
    ax[1].set_ylabel("B3 dots")

    # 2) MACD line + zero line + zero-cross arrows + squeeze + CMF-RSI cloud bg
    _dark(ax[2])
    diff = e["macd_diff"].to_numpy()
    md = max(abs(diff.min()), abs(diff.max()), 1e-3)
    # Mobius RSI cloud: the study's rescaled RSI centers "up/down" on RSI 50
    # (its neutral point). Green when RSI > 50, red when RSI < 50 — stable and
    # independent of how much history is loaded.
    rsi_v = e["rsi"].to_numpy()
    ax[2].fill_between(pos, -md * 1.35, md * 1.35, where=(rsi_v > 50),
                       color="#10a050", alpha=0.14, step="mid", zorder=0)
    ax[2].fill_between(pos, -md * 1.35, md * 1.35, where=(rsi_v < 50),
                       color="#d0202a", alpha=0.14, step="mid", zorder=0)
    _colored_line(ax[2], pos, diff, _macd_colors(e["macd_diff"]), lw=2.6)
    ax[2].axhline(0, color="#888", lw=1.0)
    ax[2].scatter(pos, np.zeros(n), c=[ORANGE if v else "#555" for v in sqz],
                  marker="s", s=16, zorder=2)  # squeeze on/off, on the zero line
    prev = np.concatenate([[diff[0]], diff[:-1]])
    up_x, dn_x = pos[(prev < 0) & (diff >= 0)], pos[(prev >= 0) & (diff < 0)]
    ax[2].scatter(up_x, np.full(len(up_x), md * 0.18), marker="^", color=GREEN, s=110, zorder=5)
    ax[2].scatter(dn_x, np.full(len(dn_x), -md * 0.18), marker="v", color=RED, s=110, zorder=5)
    ax[2].set_xlim(-1, n); ax[2].set_ylim(-md * 1.35, md * 1.35)
    ax[2].set_ylabel("MACD + Sqz + RSI cloud")

    # 3) Weekly Moxie — thick line colored by rising/falling + zero line
    _dark(ax[3])
    mox = e["moxie_w"].to_numpy()
    mprev = np.concatenate([[mox[0]], mox[:-1]])
    mcolors = [GREEN if a0 >= b0 else RED for a0, b0 in zip(mox, mprev)]
    _colored_line(ax[3], pos, mox, mcolors, lw=2.8)
    ax[3].axhline(0, color="#888", lw=1.0)
    # dotted vertical lines at zero crossings: green = crossed up, red = crossed down
    up_c = np.where((mprev < 0) & (mox >= 0))[0]
    dn_c = np.where((mprev >= 0) & (mox < 0))[0]
    for i in up_c:
        ax[3].axvline(i, color=GREEN, ls=":", lw=1.4, alpha=0.9, zorder=1)
    for i in dn_c:
        ax[3].axvline(i, color=RED, ls=":", lw=1.4, alpha=0.9, zorder=1)
    mm = max(abs(np.nanmin(mox)), abs(np.nanmax(mox)), 1e-3)
    ax[3].set_xlim(-1, n); ax[3].set_ylim(-mm * 1.2, mm * 1.2)
    ax[3].set_ylabel("Moxie (weekly)")

    # date tick labels on the ordinal axis
    step = max(1, n // 9)
    ticks = list(range(0, n, step))
    ax[-1].set_xticks(ticks)
    ax[-1].set_xticklabels([e.index[i].strftime("%b %d") for i in ticks], color="#ccc")

    bd = signals.condition_breakdown(df)
    verdict = {"bull": "BUY", "bear": "SELL", "none": "no signal"}[bd["direction"]]
    fig.suptitle(f"{symbol} — indicator layers · bar {bd['date']} · {verdict}",
                 fontsize=13, fontweight="bold", color="#fff", y=0.997)
    fig.tight_layout(rect=[0, 0, 1, 0.988])
    fig.savefig(out_path, facecolor="#000")
    plt.close(fig)
    return out_path
