"""Smoke test for chart rendering. matplotlib is available locally and in CI."""

import numpy as np
import pandas as pd

from scanner import chart


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
