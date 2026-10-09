"""The multi-timeframe table as PNG image(s), dark like the TOS watchlist (spec §5):
a grid of Symbol | WK | 3D | 2D | 1D, green buys, red sells, a gray background on
cells whose run is 1, and a heavier divider wherever the sort's leading group changes."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

from scanner import mtf
from scanner.chart import plt  # chart.py selects the Agg backend before importing pyplot

ROWS_PER_IMAGE = 40
STEM = "mtf_table"
COLUMNS = ("Symbol",) + tuple(tf for tf, _ in mtf.TIMEFRAMES)
WIDTHS = (1.3, 1.2, 1.2, 1.2, 1.2)
ROW_H_IN = 0.34                      # inches per table row
BG, GRID, HEAVY, FIRST_BG = "#0b0b0b", "#2b2b2b", "#8a8a8a", "#3d3d3d"
GREEN, RED, MUTED, TEXT = "#2ecc40", "#ff4b4b", "#7a7a7a", "#d8d8d8"
FOOTER = "* = squeeze · B/S: n = bars the signal has held · gray = started this bar"
QUIET = "No ticker has a signal on 2+ timeframes today."


def cell_style(cell: mtf.Cell) -> tuple[str, str, str | None]:
    """(text, text color, background or None) for one cell."""
    if cell.side is None:
        return mtf.EMPTY, MUTED, None
    color = GREEN if cell.side == "B" else RED
    return cell.text(), color, (FIRST_BG if cell.run == 1 else None)


def _tickers(n: int) -> str:
    return f"{n} ticker" if n == 1 else f"{n} tickers"


def header_text(as_of: str, rows: list[mtf.Row]) -> str:
    c = mtf.counts(rows)
    parts = [f"{c['buy']} buy", f"{c['sell']} sell"]
    if c["mixed"]:
        parts.append(f"{c['mixed']} mixed")
    day = pd.Timestamp(as_of).strftime("%a %Y-%m-%d")
    return f"Sqzdots MTF · v5+REV · {day} · {_tickers(len(rows))} ({' · '.join(parts)})"


def caption_text(rows: list[mtf.Row]) -> str:
    return f"Multi-timeframe: {_tickers(len(rows))} on 2+ timeframes"


def _symbol_color(row: mtf.Row) -> str:
    return {"buy": GREEN, "sell": RED}.get(row.direction, TEXT)


def _render_page(rows: list[mtf.Row], title: str, page_label: str, path: Path) -> Path:
    total_w = sum(WIDTHS)
    n_lines = len(rows) + 1                       # + the column-header row
    table_h = ROW_H_IN * n_lines
    fig_h = table_h + 0.9                         # room for the title and footer
    fig = plt.figure(figsize=(total_w * 1.15, fig_h), facecolor=BG)
    ax = fig.add_axes([0.02, 0.4 / fig_h, 0.96, table_h / fig_h])
    ax.set_xlim(0, total_w)
    ax.set_ylim(n_lines, 0)
    ax.axis("off")
    xs = np.concatenate([[0.0], np.cumsum(WIDTHS)])

    def centre(j):
        return (xs[j] + xs[j + 1]) / 2

    for j, name in enumerate(COLUMNS):
        x, ha = (xs[0] + 0.1, "left") if j == 0 else (centre(j), "center")
        ax.text(x, 0.5, name, color=TEXT, ha=ha, va="center", fontsize=10.5, fontweight="bold")

    for i, row in enumerate(rows, start=1):
        ax.text(xs[0] + 0.1, i + 0.5, row.symbol, color=_symbol_color(row),
                ha="left", va="center", fontsize=10.5, fontweight="bold")
        for j, cell in enumerate(row.cells, start=1):
            text, color, bg = cell_style(cell)
            if bg:
                ax.add_patch(Rectangle((xs[j], i), WIDTHS[j], 1, facecolor=bg, edgecolor="none"))
            ax.text(centre(j), i + 0.5, text, color=color, ha="center", va="center",
                    fontsize=10.5, family="DejaVu Sans Mono")

    for y in range(n_lines + 1):
        ax.plot([0, total_w], [y, y], color=GRID, lw=0.7, clip_on=False)
    for x in xs:
        ax.plot([x, x], [0, n_lines], color=GRID, lw=0.7, clip_on=False)
    for i in range(1, len(rows)):
        if mtf.lead(rows[i]) != mtf.lead(rows[i - 1]):
            ax.plot([0, total_w], [i + 1, i + 1], color=HEAVY, lw=1.8)

    label = f"   {page_label}" if page_label else ""
    fig.text(0.02, 1 - 0.12 / fig_h, title + label, color=TEXT, fontsize=10.5, va="top")
    fig.text(0.02, 0.1 / fig_h, FOOTER, color=MUTED, fontsize=8.5, va="bottom")
    fig.savefig(path, dpi=150, facecolor=BG)
    plt.close(fig)
    return path


def render_table(rows: list[mtf.Row], as_of: str, out_dir) -> list[Path]:
    """Write the table as out_dir/mtf_table.png (+ _2, _3 ... past 40 rows) and
    return the paths. Old pages are deleted first, so a shorter day never leaves
    yesterday's extra pages behind. No rows -> no image, []."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob(f"{STEM}*.png"):
        old.unlink()
    if not rows:
        return []
    pages = [rows[i:i + ROWS_PER_IMAGE] for i in range(0, len(rows), ROWS_PER_IMAGE)]
    title = header_text(as_of, rows)
    paths = []
    for k, page in enumerate(pages, start=1):
        name = f"{STEM}.png" if k == 1 else f"{STEM}_{k}.png"
        label = f"{k}/{len(pages)}" if len(pages) > 1 else ""
        paths.append(_render_page(page, title, label, out / name))
    return paths
