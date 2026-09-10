"""Smoke test for chart rendering. matplotlib is available locally and in CI."""

import numpy as np
import pandas as pd

from scanner import chart  # sets the Agg backend on import
import matplotlib.pyplot as plt  # noqa: E402


def _ohlc(n=260, seed=2):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-06-03", periods=n)
    close = pd.Series(100 + np.arange(n) * 0.1 + rng.normal(0, 1.0, n), index=idx)
    return pd.DataFrame({"open": close.shift(1).fillna(close.iloc[0]),
                         "high": close + 1.0, "low": close - 1.0, "close": close}, index=idx)


def test_render_chart_writes_a_png(tmp_path):
    out = tmp_path / "DEMO.png"
    path = chart.render(_ohlc(), "DEMO", str(out), lookback=120)
    assert out.exists()
    assert out.stat().st_size > 1000  # a real image, not an empty file
    assert path == str(out)


def test_render_layers_writes_a_multipanel_png(tmp_path):
    out = tmp_path / "DEMO_layers.png"
    path = chart.render_layers(_ohlc(), "DEMO", str(out), lookback=120)
    assert out.exists()
    assert out.stat().st_size > 5000  # bigger multi-panel figure
    assert path == str(out)


def _zigzag(n=200, amp=10.0, period=5.0):
    idx = pd.bdate_range("2024-01-01", periods=n)
    base = 100 + amp * np.sin(np.arange(n) / period)
    return pd.DataFrame({"open": base, "high": base + 1.0,
                         "low": base - 1.0, "close": base}, index=idx)


def test_major_pivot_segments_end_at_next_pivot_not_right_edge():
    # Each pivot's S/R line runs only to the NEXT same-type pivot (TOS step
    # behavior); only the most recent peak/valley reaches the right edge.
    from scanner import signals
    full = signals.analyze(_zigzag())
    n_display = 180
    fig, ax = plt.subplots()
    segs = chart._draw_major_pivots(ax, full, n_display)
    plt.close(fig)
    peaks = [s for s in segs if s["kind"] == "peak"]
    assert len(peaks) >= 2, "zig-zag should surface multiple peaks"
    assert sum(1 for s in peaks if s["end"] == n_display - 1) == 1  # only the last to the edge
    assert peaks[0]["end"] < n_display - 1                          # earlier ones bounded


def test_left_edge_pivot_uses_prewindow_history():
    # A pivot near the left of the DISPLAY can only be found if detection runs on
    # history from BEFORE the window. Proof: a segment reaches the left edge.
    from scanner import signals
    full = signals.analyze(_zigzag(n=240))
    n_display = 60  # show only the last 60 bars; pivots depend on prior history
    fig, ax = plt.subplots()
    segs = chart._draw_major_pivots(ax, full, n_display)
    plt.close(fig)
    assert any(s["start"] == 0 for s in segs), "a level should carry in from the left edge"


def test_draw_major_pivots_empty_on_flat_series():
    from scanner import signals
    n = 200
    idx = pd.bdate_range("2024-01-01", periods=n)
    flat = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5,
                         "close": 100.0}, index=idx)
    full = signals.analyze(flat)
    fig, ax = plt.subplots()
    segs = chart._draw_major_pivots(ax, full, 180)
    plt.close(fig)
    assert segs == []


def test_render_layers_with_pivots_writes_a_png(tmp_path):
    out = tmp_path / "ZIG_layers.png"
    path = chart.render_layers(_zigzag(), "ZIG", str(out), lookback=180)
    assert out.exists()
    assert out.stat().st_size > 5000
    assert path == str(out)


def test_render_b3_dots_writes_a_png(tmp_path):
    out = tmp_path / "DEMO_dots.png"
    path = chart.render_b3_dots(_ohlc(), "DEMO", str(out), lookback=60)
    assert out.exists()
    assert out.stat().st_size > 3000
    assert path == str(out)
