"""Weekly-timeframe scan: the higher-TF Moxie steps up to monthly (htf_rule="ME")
and the weekly runner writes its own ledger + results."""

import numpy as np
import pandas as pd

from scanner import data, signals, weekrun


def _ohlc(n=400, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    high = np.maximum(close, close + rng.uniform(0.2, 1.0, n))
    low = np.minimum(close, close - rng.uniform(0.2, 1.0, n))
    openp = close + rng.normal(0, 0.3, n)
    return pd.DataFrame({"open": openp, "high": high, "low": low, "close": close,
                         "volume": rng.integers(1_000_000, 5_000_000, n)}, index=idx)


def test_analyze_htf_rule_changes_the_moxie_band():
    f = _ohlc()
    w = signals.analyze(f, htf_rule="W")     # weekly Moxie (daily default)
    m = signals.analyze(f, htf_rule="ME")    # monthly Moxie (weekly scan)
    assert "moxie_w" in w.columns and "moxie_w" in m.columns
    # the monthly band is a different series than the weekly one
    assert not w["moxie_w"].equals(m["moxie_w"])


def test_default_htf_rule_is_weekly_unchanged():
    f = _ohlc()
    assert signals.analyze(f)["moxie_w"].equals(signals.analyze(f, htf_rule="W")["moxie_w"])


def test_weekrun_dry_run_writes_results_and_weekly_ledger(tmp_path, monkeypatch):
    frames = {"AAA": _ohlc(seed=1), "BBB": _ohlc(seed=2)}
    monkeypatch.setattr(data, "fetch_weekly", lambda *a, **k: frames)
    monkeypatch.setattr(data, "company_name", lambda *a, **k: None)
    ledger_path = tmp_path / "signals_weekly.jsonl"
    out_dir = tmp_path / "out_weekly"

    results = weekrun.main([
        "--dry-run", "--no-charts",
        "--out", str(out_dir), "--ledger", str(ledger_path),
    ])

    assert (out_dir / "results.json").exists()
    assert ledger_path.exists()          # weekly ledger persisted even with 0 fires
    assert "fired" in results
