# Sqzdots Signal Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python service on a Hostinger VPS that listens for TOS squeeze alert emails, generates branded chart images, routes them through a private Discord review window, and auto-posts approved signals to a public Discord channel while staging tweet copy for manual Twitter posting.

**Architecture:** FastAPI webhook endpoint receives Gmail Pub/Sub push notifications; a pipeline of focused modules (email_listener → chart_generator → review_bot → publisher) processes each signal; a Discord bot running in a background thread handles the operator review/approval step via emoji reactions.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, discord.py, yfinance, mplfinance, matplotlib, pandas, google-api-python-client, google-auth, python-dotenv, pytest, httpx (test client), nginx, systemd

---

## Pre-Implementation: Get TOS Alert Email Format

**STOP before Task 4.** The email parser cannot be written without seeing a real TOS alert email.
Before starting Task 4, the operator must:
1. Trigger a test alert in ThinkOrSwim (or wait for the next real alert)
2. Forward the raw email (subject + full body) to the developer
3. Developer documents the format in `docs/tos-alert-format.md` (see Task 4 setup step)

---

## File Map

```
sqzdots/
├── main.py                    # FastAPI app, webhook auth, pipeline orchestration
├── config.py                  # Loads .env, exposes typed settings
├── email_listener.py          # Gmail Pub/Sub payload parsing → signal dict
├── chart_generator.py         # yfinance fetch + TTM Squeeze compute + mplfinance render
├── review_bot.py              # Discord bot, asyncio.Queue, reaction handling
├── publisher.py               # Discord webhook post + output file writing
├── requirements.txt
├── .env.example               # Template — never commit .env
├── setup.sh                   # One-command VPS setup
├── sqzdots.service            # systemd unit file
├── nginx.conf                 # nginx reverse proxy config
├── docs/
│   └── tos-alert-format.md    # TOS email sample (filled in pre-Task-4)
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_email_listener.py
    ├── test_chart_generator.py
    ├── test_publisher.py
    └── test_main.py
```

---

## Task 1: Project Scaffold

**Files:**
- Create: `sqzdots/requirements.txt`
- Create: `sqzdots/config.py`
- Create: `sqzdots/.env.example`
- Create: `sqzdots/tests/conftest.py`
- Create: `sqzdots/tests/test_config.py`

- [ ] **Step 1: Create project directory and git repo on VPS**

SSH into VPS, then:
```bash
mkdir -p /opt/sqzdots/{output/{ready,skipped,expired,errors},logs,credentials}
cd /opt/sqzdots
git init
```

- [ ] **Step 2: Create `requirements.txt`**

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
discord.py==2.3.2
yfinance==0.2.38
mplfinance==0.12.10b0
matplotlib==3.8.4
pandas==2.2.2
numpy==1.26.4
google-api-python-client==2.127.0
google-auth==2.29.0
google-auth-httplib2==0.2.0
python-dotenv==1.0.1
httpx==0.27.0
pytest==8.1.1
pytest-asyncio==0.23.6
pytest-mock==3.14.0
requests-mock==1.11.0
PyJWT==2.8.0
cryptography==42.0.5
requests==2.31.0
```

- [ ] **Step 3: Create `.env.example`**

```env
# Gmail / Google Cloud
GMAIL_CREDENTIALS_JSON=/opt/sqzdots/credentials/gmail_service_account.json
GMAIL_USER_EMAIL=your@gmail.com
PUBSUB_TOPIC=projects/{project-id}/topics/sqzdots-alerts
PUBSUB_AUDIENCE=https://yourdomain.com/webhook

# Discord
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_REVIEW_CHANNEL_ID=123456789012345678
DISCORD_PUBLIC_CHANNEL_WEBHOOK=https://discord.com/api/webhooks/...
DISCORD_OPERATOR_ID=123456789012345678

# App
OUTPUT_DIR=/opt/sqzdots/output
LOG_FILE=/opt/sqzdots/logs/sqzdots.log
REVIEW_TIMEOUT_MINUTES=30
```

- [ ] **Step 4: Write failing test for config**

Create `tests/test_config.py`:
```python
import os
import pytest
from unittest.mock import patch


def test_settings_loads_required_env_vars():
    env = {
        "GMAIL_CREDENTIALS_JSON": "/path/to/creds.json",
        "GMAIL_USER_EMAIL": "test@gmail.com",
        "PUBSUB_TOPIC": "projects/proj/topics/topic",
        "PUBSUB_AUDIENCE": "https://example.com/webhook",
        "DISCORD_BOT_TOKEN": "token123",
        "DISCORD_REVIEW_CHANNEL_ID": "111111111111111111",
        "DISCORD_PUBLIC_CHANNEL_WEBHOOK": "https://discord.com/api/webhooks/x/y",
        "DISCORD_OPERATOR_ID": "222222222222222222",
        "OUTPUT_DIR": "/tmp/sqzdots/output",
        "LOG_FILE": "/tmp/sqzdots/logs/sqzdots.log",
        "REVIEW_TIMEOUT_MINUTES": "30",
    }
    with patch.dict(os.environ, env, clear=True):
        from config import Settings
        s = Settings()
        assert s.gmail_user_email == "test@gmail.com"
        assert s.discord_operator_id == 222222222222222222
        assert s.review_timeout_minutes == 30
        assert s.output_dir == "/tmp/sqzdots/output"


def test_settings_raises_on_missing_required_var():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(Exception):
            from importlib import reload
            import config
            reload(config)
            config.Settings()
```

- [ ] **Step 5: Run test to verify it fails**

```bash
cd /opt/sqzdots
pip install -r requirements.txt
pytest tests/test_config.py -v
```
Expected: `FAILED` — `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 6: Create `config.py`**

```python
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self):
        self.gmail_credentials_json = self._require("GMAIL_CREDENTIALS_JSON")
        self.gmail_user_email = self._require("GMAIL_USER_EMAIL")
        self.pubsub_topic = self._require("PUBSUB_TOPIC")
        self.pubsub_audience = self._require("PUBSUB_AUDIENCE")
        self.discord_bot_token = self._require("DISCORD_BOT_TOKEN")
        self.discord_review_channel_id = int(self._require("DISCORD_REVIEW_CHANNEL_ID"))
        self.discord_public_channel_webhook = self._require("DISCORD_PUBLIC_CHANNEL_WEBHOOK")
        self.discord_operator_id = int(self._require("DISCORD_OPERATOR_ID"))
        self.output_dir = os.getenv("OUTPUT_DIR", "/opt/sqzdots/output")
        self.log_file = os.getenv("LOG_FILE", "/opt/sqzdots/logs/sqzdots.log")
        self.review_timeout_minutes = int(os.getenv("REVIEW_TIMEOUT_MINUTES", "30"))

    def _require(self, key: str) -> str:
        val = os.getenv(key)
        if not val:
            raise EnvironmentError(f"Required env var missing: {key}")
        return val


settings = Settings()
```

Create `tests/conftest.py`:
```python
import os
import pytest

@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    monkeypatch.setenv("GMAIL_CREDENTIALS_JSON", "/tmp/creds.json")
    monkeypatch.setenv("GMAIL_USER_EMAIL", "test@gmail.com")
    monkeypatch.setenv("PUBSUB_TOPIC", "projects/proj/topics/topic")
    monkeypatch.setenv("PUBSUB_AUDIENCE", "https://example.com/webhook")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token123")
    monkeypatch.setenv("DISCORD_REVIEW_CHANNEL_ID", "111111111111111111")
    monkeypatch.setenv("DISCORD_PUBLIC_CHANNEL_WEBHOOK", "https://discord.com/api/webhooks/x/y")
    monkeypatch.setenv("DISCORD_OPERATOR_ID", "222222222222222222")
    monkeypatch.setenv("OUTPUT_DIR", "/tmp/sqzdots/output")
    monkeypatch.setenv("LOG_FILE", "/tmp/sqzdots/logs/sqzdots.log")
    monkeypatch.setenv("REVIEW_TIMEOUT_MINUTES", "30")
```

- [ ] **Step 7: Run test to verify it passes**

```bash
pytest tests/test_config.py -v
```
Expected: `PASSED`

- [ ] **Step 8: Commit**

```bash
git add requirements.txt config.py .env.example tests/conftest.py tests/test_config.py
git commit -m "feat: project scaffold with config and env loading"
```

---

## Task 2: `publisher.py` — Output Filing + Discord Webhook

**Files:**
- Create: `sqzdots/publisher.py`
- Create: `sqzdots/tests/test_publisher.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_publisher.py`:
```python
import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime


SAMPLE_SIGNAL = {
    "ticker": "AAPL",
    "timeframe": "Daily",
    "signal_type": "Squeeze Fired",
    "timestamp": "2026-03-31T09:30:00",
}
SAMPLE_CHART_PATH = "/tmp/AAPL_Daily_2026-03-31.png"


def test_approve_writes_files_to_ready(tmp_path):
    from publisher import Publisher
    pub = Publisher(output_dir=str(tmp_path))
    # Create a dummy chart file
    chart = tmp_path / "AAPL_Daily_2026-03-31.png"
    chart.write_bytes(b"fake_png_data")

    pub.approve(SAMPLE_SIGNAL, str(chart))

    ready_dir = tmp_path / "ready"
    files = list(ready_dir.iterdir())
    png_files = [f for f in files if f.suffix == ".png"]
    txt_files = [f for f in files if f.suffix == ".txt"]
    assert len(png_files) == 1
    assert len(txt_files) == 1
    tweet_text = txt_files[0].read_text()
    assert "$AAPL" in tweet_text
    assert "#FinTwit" in tweet_text


def test_approve_tweet_text_format():
    import tempfile
    from publisher import Publisher
    with tempfile.TemporaryDirectory() as tmp:
        pub = Publisher(output_dir=tmp)
        chart = Path(tmp) / "chart.png"
        chart.write_bytes(b"fake")
        pub.approve(SAMPLE_SIGNAL, str(chart))
        txt = list((Path(tmp) / "ready").glob("*.txt"))[0].read_text()
        assert "$AAPL" in txt
        assert "Daily" in txt
        assert "Squeeze Fired" in txt
        assert "#B3Signals" in txt


def test_skip_writes_to_skipped(tmp_path):
    from publisher import Publisher
    pub = Publisher(output_dir=str(tmp_path))
    pub.skip(SAMPLE_SIGNAL, reason="operator skipped")
    skipped = list((tmp_path / "skipped").iterdir())
    assert len(skipped) == 1
    data = json.loads(skipped[0].read_text())
    assert data["ticker"] == "AAPL"
    assert data["reason"] == "operator skipped"


def test_expire_writes_to_expired(tmp_path):
    from publisher import Publisher
    pub = Publisher(output_dir=str(tmp_path))
    pub.expire(SAMPLE_SIGNAL)
    expired = list((tmp_path / "expired").iterdir())
    assert len(expired) == 1


def test_error_writes_to_errors(tmp_path):
    from publisher import Publisher
    pub = Publisher(output_dir=str(tmp_path))
    pub.error(SAMPLE_SIGNAL, reason="yfinance returned empty data")
    errors = list((tmp_path / "errors").iterdir())
    assert len(errors) == 1
    data = json.loads(errors[0].read_text())
    assert data["reason"] == "yfinance returned empty data"


def test_approve_posts_to_discord_webhook(tmp_path, requests_mock):
    from publisher import Publisher
    webhook_url = "https://discord.com/api/webhooks/test/token"
    requests_mock.post(webhook_url, json={"id": "123"})
    pub = Publisher(output_dir=str(tmp_path), discord_webhook_url=webhook_url)
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"fake_png_data")
    pub.approve(SAMPLE_SIGNAL, str(chart))
    assert requests_mock.called
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_publisher.py -v
```
Expected: `FAILED` — `ModuleNotFoundError: No module named 'publisher'`

- [ ] **Step 3: Implement `publisher.py`**

```python
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

TWEET_TEMPLATE = (
    "${ticker} — {timeframe} {signal_type} 🔴\n"
    "#FinTwit #Squeeze #B3Signals"
)


class Publisher:
    def __init__(self, output_dir: str, discord_webhook_url: str = ""):
        self.output_dir = Path(output_dir)
        self.discord_webhook_url = discord_webhook_url
        for folder in ("ready", "skipped", "expired", "errors"):
            (self.output_dir / folder).mkdir(parents=True, exist_ok=True)

    def approve(self, signal: dict, chart_path: str) -> None:
        slug = self._slug(signal)
        ready = self.output_dir / "ready"

        # Copy chart image
        dest_png = ready / f"{slug}.png"
        shutil.copy2(chart_path, dest_png)

        # Write tweet copy
        tweet = TWEET_TEMPLATE.format(**signal).replace("${ticker}", f"${signal['ticker']}")
        (ready / f"{slug}.txt").write_text(tweet)

        # Post to Discord webhook
        if self.discord_webhook_url:
            self._post_discord(signal, chart_path)

        logger.info("approved signal=%s chart=%s", slug, dest_png)

    def skip(self, signal: dict, reason: str = "") -> None:
        self._archive("skipped", signal, reason=reason)
        logger.info("skipped signal=%s reason=%s", self._slug(signal), reason)

    def expire(self, signal: dict) -> None:
        self._archive("expired", signal, reason="timeout")
        logger.info("expired signal=%s", self._slug(signal))

    def error(self, signal: dict, reason: str) -> None:
        self._archive("errors", signal, reason=reason)
        logger.warning("error signal=%s reason=%s", self._slug(signal), reason)

    def _archive(self, folder: str, signal: dict, reason: str = "") -> None:
        slug = self._slug(signal)
        data = {**signal, "reason": reason, "archived_at": datetime.utcnow().isoformat()}
        (self.output_dir / folder / f"{slug}.json").write_text(
            json.dumps(data, indent=2)
        )

    def _post_discord(self, signal: dict, chart_path: str) -> None:
        content = (
            f"**${signal['ticker']}** — {signal['timeframe']} {signal['signal_type']}\n"
            f"#FinTwit #Squeeze #B3Signals"
        )
        try:
            with open(chart_path, "rb") as f:
                resp = requests.post(
                    self.discord_webhook_url,
                    data={"content": content},
                    files={"file": (Path(chart_path).name, f, "image/png")},
                    timeout=10,
                )
            resp.raise_for_status()
        except Exception as exc:
            logger.error("discord webhook post failed: %s", exc)

    @staticmethod
    def _slug(signal: dict) -> str:
        date = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
        return f"{signal['ticker']}_{signal['timeframe'].replace(' ', '')}_{date}"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_publisher.py -v
```
Expected: all `PASSED`

- [ ] **Step 5: Commit**

```bash
git add publisher.py tests/test_publisher.py
git commit -m "feat: publisher — output filing and Discord webhook posting"
```

---

## Task 3: `chart_generator.py` — Squeeze Chart Generation

**Files:**
- Create: `sqzdots/chart_generator.py`
- Create: `sqzdots/tests/test_chart_generator.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_chart_generator.py`:
```python
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


def make_fake_ohlcv(n=100) -> pd.DataFrame:
    """Synthetic OHLCV data for testing — no network required."""
    idx = pd.date_range("2026-01-01", periods=n, freq="1D")
    close = 150 + np.cumsum(np.random.randn(n))
    df = pd.DataFrame({
        "Open":   close - 0.5,
        "High":   close + 1.0,
        "Low":    close - 1.0,
        "Close":  close,
        "Volume": np.random.randint(1_000_000, 5_000_000, n),
    }, index=idx)
    return df


def test_timeframe_map_returns_valid_yfinance_params():
    from chart_generator import timeframe_to_yfinance
    interval, period = timeframe_to_yfinance("Daily")
    assert interval == "1d"
    assert period == "90d"

    interval, period = timeframe_to_yfinance("1 Hour")
    assert interval == "1h"

    interval, period = timeframe_to_yfinance("4 Hour")
    assert interval == "1h"  # fetched as 1h, resampled later
    assert period == "60d"


def test_timeframe_map_raises_on_unknown():
    from chart_generator import timeframe_to_yfinance
    with pytest.raises(ValueError, match="Unknown timeframe"):
        timeframe_to_yfinance("3 Hour")


def test_compute_squeeze_returns_expected_columns():
    from chart_generator import compute_squeeze
    df = make_fake_ohlcv(100)
    result = compute_squeeze(df)
    assert "squeeze_on" in result.columns
    assert "momentum" in result.columns
    assert result["squeeze_on"].dtype == bool


def test_compute_squeeze_minimum_bars():
    from chart_generator import compute_squeeze
    df = make_fake_ohlcv(19)  # less than period=20
    with pytest.raises(ValueError, match="Insufficient data"):
        compute_squeeze(df)


def test_resample_4h_reduces_bar_count():
    from chart_generator import resample_to_4h
    idx = pd.date_range("2026-01-01", periods=200, freq="1h")
    df = pd.DataFrame({
        "Open": 100.0, "High": 101.0, "Low": 99.0,
        "Close": 100.5, "Volume": 1000,
    }, index=idx)
    result = resample_to_4h(df)
    assert len(result) == len(df) // 4


def test_generate_chart_returns_png_path(tmp_path):
    from chart_generator import ChartGenerator
    gen = ChartGenerator(output_dir=str(tmp_path))
    df = make_fake_ohlcv(80)
    signal = {"ticker": "AAPL", "timeframe": "Daily",
               "signal_type": "Squeeze Fired", "timestamp": "2026-03-31T09:30:00"}
    with patch("chart_generator.yf.download", return_value=df):
        path = gen.generate(signal)
    assert path.endswith(".png")
    assert (tmp_path / Path(path).name).exists()


def test_generate_chart_returns_none_on_empty_data(tmp_path):
    from chart_generator import ChartGenerator
    gen = ChartGenerator(output_dir=str(tmp_path))
    signal = {"ticker": "INVALID", "timeframe": "Daily",
               "signal_type": "Squeeze Fired", "timestamp": "2026-03-31T09:30:00"}
    with patch("chart_generator.yf.download", return_value=pd.DataFrame()):
        path = gen.generate(signal)
    assert path is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_chart_generator.py -v
```
Expected: `FAILED` — `ModuleNotFoundError: No module named 'chart_generator'`

- [ ] **Step 3: Implement `chart_generator.py`**

```python
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # headless — no display required
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import mplfinance as mpf
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# TOS timeframe string → (yfinance interval, yfinance period)
TIMEFRAME_MAP = {
    "Daily":   ("1d",  "90d"),
    "Weekly":  ("1wk", "2y"),
    "1 Hour":  ("1h",  "60d"),
    "4 Hour":  ("1h",  "60d"),  # fetched as 1h, resampled to 4h
    "30 Min":  ("30m", "60d"),
    "15 Min":  ("15m", "60d"),
    "5 Min":   ("5m",  "60d"),
}

CHART_BARS = 60


def timeframe_to_yfinance(timeframe: str) -> tuple[str, str]:
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Unknown timeframe: {timeframe!r}. Expected one of {list(TIMEFRAME_MAP)}")
    return TIMEFRAME_MAP[timeframe]


def resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    # pandas 2.2+ uses lowercase offset aliases
    resampled = df.resample("4h").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna()
    return resampled


def compute_squeeze(df: pd.DataFrame, period: int = 20, bb_mult: float = 2.0,
                    kc_mult: float = 1.5) -> pd.DataFrame:
    if len(df) < period:
        raise ValueError(f"Insufficient data: need at least {period} bars, got {len(df)}")

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    # Bollinger Bands
    bb_mid = close.rolling(period).mean()
    bb_std = close.rolling(period).std()
    bb_upper = bb_mid + bb_mult * bb_std
    bb_lower = bb_mid - bb_mult * bb_std

    # Keltner Channels (using ATR approximation: mean of True Range)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    kc_mid = close.rolling(period).mean()
    kc_range = tr.rolling(period).mean() * kc_mult
    kc_upper = kc_mid + kc_range
    kc_lower = kc_mid - kc_range

    # Squeeze: BB inside KC
    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)

    # Momentum: linear regression of delta(close, midpoint)
    midpoint = (bb_upper + bb_lower + kc_upper + kc_lower) / 4
    delta = close - midpoint

    def linreg_last(series: pd.Series, n: int = period) -> float:
        if len(series) < n:
            return np.nan
        y = series.iloc[-n:].values
        x = np.arange(n)
        m, b = np.polyfit(x, y, 1)
        return m * (n - 1) + b

    momentum = delta.rolling(period).apply(
        lambda s: linreg_last(pd.Series(s), period), raw=False
    )

    result = df.copy()
    result["squeeze_on"] = squeeze_on
    result["momentum"] = momentum
    return result


class ChartGenerator:
    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, signal: dict) -> Optional[str]:
        ticker = signal["ticker"]
        timeframe = signal["timeframe"]

        try:
            df = self._fetch(ticker, timeframe)
        except Exception as exc:
            logger.error("fetch failed ticker=%s timeframe=%s: %s", ticker, timeframe, exc)
            return None

        if df is None or df.empty:
            logger.warning("empty data ticker=%s timeframe=%s", ticker, timeframe)
            return None

        try:
            df = compute_squeeze(df)
        except ValueError as exc:
            logger.error("squeeze compute failed: %s", exc)
            return None

        # Trim to last CHART_BARS bars
        df = df.iloc[-CHART_BARS:]

        try:
            path = self._render(df, signal)
        except Exception as exc:
            logger.error("render failed ticker=%s: %s", ticker, exc)
            return None

        return path

    def _fetch(self, ticker: str, timeframe: str) -> Optional[pd.DataFrame]:
        interval, period = timeframe_to_yfinance(timeframe)
        df = yf.download(ticker, interval=interval, period=period,
                         auto_adjust=True, progress=False)
        if timeframe == "4 Hour" and not df.empty:
            df = resample_to_4h(df)
        return df

    def _render(self, df: pd.DataFrame, signal: dict) -> str:
        ticker = signal["ticker"]
        timeframe = signal["timeframe"]
        signal_type = signal["signal_type"]

        # Build momentum panel colors
        momentum = df["momentum"].fillna(0)
        mom_colors = ["green" if v >= 0 else "red" for v in momentum]

        # Squeeze dot colors on zero line: red=on, green=fired
        squeeze_dots = pd.Series(0.0, index=df.index)
        dot_colors = ["red" if sq else "limegreen" for sq in df["squeeze_on"]]

        apds = [
            mpf.make_addplot(momentum, panel=2, type="bar", color=mom_colors,
                             ylabel="Momentum"),
            mpf.make_addplot(squeeze_dots, panel=2, type="scatter",
                             markersize=8, marker="o", color=dot_colors),
        ]

        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"{ticker}_{timeframe.replace(' ', '')}_{ts}.png"
        filepath = str(self.output_dir / filename)

        fig, axes = mpf.plot(
            df,
            type="candle",
            style="charles",
            title=f"  ${ticker} | {timeframe} | {signal_type}",
            volume=True,
            addplot=apds,
            figsize=(12, 6.75),
            returnfig=True,
            panel_ratios=(4, 1, 2),
        )

        # Watermark
        fig.text(0.98, 0.02, "B3 Signal Intelligence",
                 fontsize=9, color="gray", alpha=0.4,
                 ha="right", va="bottom", style="italic")

        fig.savefig(filepath, dpi=100, bbox_inches="tight")
        plt.close(fig)
        logger.info("chart saved path=%s", filepath)
        return filepath
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_chart_generator.py -v
```
Expected: all `PASSED`
Note: `test_generate_chart_returns_png_path` uses a mock for `yf.download` so no network call is made.

- [ ] **Step 5: Commit**

```bash
git add chart_generator.py tests/test_chart_generator.py
git commit -m "feat: chart_generator — TTM Squeeze computation and mplfinance chart rendering"
```

---

## Task 4: `email_listener.py` — Gmail Push Notification Parsing

**Pre-requisite:** Complete the TOS email format step described at the top of this plan before writing the parser regex.

**Files:**
- Create: `sqzdots/email_listener.py`
- Create: `sqzdots/tests/test_email_listener.py`
- Create: `sqzdots/docs/tos-alert-format.md` (document real TOS email sample here)

- [ ] **Step 1: Document TOS alert email format**

Before writing any code, create `docs/tos-alert-format.md` with the actual TOS email subject and body. Example (replace with real values):
```
Subject: TOS Alert: B3 Scanner - AAPL Daily Squeeze Fired
Body:
  Alert: B3 Scanner
  Symbol: AAPL
  Timeframe: Daily
  Condition: Squeeze Fired
  Time: 09:30 ET
```

Identify which fields carry `ticker`, `timeframe`, `signal_type`, `timestamp` — then update the regex in Step 3 to match.

- [ ] **Step 2: Write failing tests**

Create `tests/test_email_listener.py` — update the sample subject/body strings to match your actual TOS format after Step 1:
```python
import base64
import json
import pytest
from unittest.mock import MagicMock, patch


# UPDATE THESE to match your actual TOS alert email format (see docs/tos-alert-format.md)
SAMPLE_SUBJECT = "TOS Alert: B3 Scanner - AAPL Daily Squeeze Fired"
SAMPLE_BODY = (
    "Alert: B3 Scanner\n"
    "Symbol: AAPL\n"
    "Timeframe: Daily\n"
    "Condition: Squeeze Fired\n"
    "Time: 09:30 ET\n"
)

SAMPLE_PUSH_PAYLOAD = {
    "message": {
        "data": base64.b64encode(json.dumps({"historyId": "12345"}).encode()).decode(),
        "messageId": "abc123",
    },
    "subscription": "projects/proj/subscriptions/sqzdots-sub",
}


def test_parse_signal_from_email_returns_dict():
    from email_listener import parse_signal_from_email
    result = parse_signal_from_email(SAMPLE_SUBJECT, SAMPLE_BODY)
    assert result is not None
    assert result["ticker"] == "AAPL"
    assert result["timeframe"] == "Daily"
    assert result["signal_type"] == "Squeeze Fired"
    assert "timestamp" in result


def test_parse_signal_returns_none_on_unrecognized_format():
    from email_listener import parse_signal_from_email
    result = parse_signal_from_email("Random email subject", "Some random body text")
    assert result is None


def test_decode_history_id_from_push_payload():
    from email_listener import decode_history_id
    history_id = decode_history_id(SAMPLE_PUSH_PAYLOAD)
    assert history_id == "12345"


def test_decode_history_id_raises_on_invalid_payload():
    from email_listener import decode_history_id
    with pytest.raises(ValueError, match="Invalid Pub/Sub payload"):
        decode_history_id({"unexpected": "structure"})


def test_fetch_new_messages_calls_gmail_api(mocker):
    from email_listener import fetch_new_messages
    mock_service = MagicMock()
    mock_service.users().history().list().execute.return_value = {
        "history": [{"messagesAdded": [{"message": {"id": "msg1"}}]}]
    }
    mock_service.users().messages().get().execute.return_value = {
        "payload": {
            "headers": [
                {"name": "Subject", "value": SAMPLE_SUBJECT},
                {"name": "Date", "value": "Mon, 31 Mar 2026 09:30:00 -0400"},
            ],
            "body": {"data": base64.b64encode(SAMPLE_BODY.encode()).decode()},
        }
    }
    messages = fetch_new_messages(mock_service, user="me", history_id="12345")
    assert len(messages) == 1
    assert messages[0]["subject"] == SAMPLE_SUBJECT
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_email_listener.py -v
```
Expected: `FAILED` — `ModuleNotFoundError: No module named 'email_listener'`

- [ ] **Step 4: Implement `email_listener.py`**

Update the regex patterns in `_SUBJECT_PATTERN` and `_BODY_PATTERNS` to match your actual TOS email format documented in Step 1.

```python
import base64
import json
import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# --- UPDATE THESE PATTERNS to match your actual TOS alert email format ---
# See docs/tos-alert-format.md for the sample email used to derive these.
_SUBJECT_PATTERN = re.compile(
    r"TOS Alert:.*?(?P<ticker>[A-Z]{1,5})\s+(?P<timeframe>Daily|Weekly|1 Hour|4 Hour|30 Min|15 Min|5 Min)\s+(?P<signal_type>Squeeze Fired|Squeeze On)",
    re.IGNORECASE,
)
_BODY_TICKER = re.compile(r"Symbol:\s*(?P<ticker>[A-Z]{1,5})", re.IGNORECASE)
_BODY_TIMEFRAME = re.compile(
    r"Timeframe:\s*(?P<timeframe>Daily|Weekly|1 Hour|4 Hour|30 Min|15 Min|5 Min)",
    re.IGNORECASE,
)
_BODY_SIGNAL = re.compile(r"Condition:\s*(?P<signal_type>Squeeze Fired|Squeeze On)", re.IGNORECASE)
# -------------------------------------------------------------------------


def decode_history_id(payload: dict) -> str:
    try:
        data_b64 = payload["message"]["data"]
        decoded = json.loads(base64.b64decode(data_b64).decode("utf-8"))
        return str(decoded["historyId"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid Pub/Sub payload: {exc}") from exc


def fetch_new_messages(gmail_service, user: str, history_id: str) -> list[dict]:
    messages = []
    try:
        history_resp = (
            gmail_service.users()
            .history()
            .list(userId=user, startHistoryId=history_id, historyTypes=["messageAdded"])
            .execute()
        )
        for record in history_resp.get("history", []):
            for added in record.get("messagesAdded", []):
                msg_id = added["message"]["id"]
                msg = (
                    gmail_service.users()
                    .messages()
                    .get(userId=user, id=msg_id, format="full")
                    .execute()
                )
                subject = ""
                for header in msg.get("payload", {}).get("headers", []):
                    if header["name"].lower() == "subject":
                        subject = header["value"]
                        break
                body_data = msg.get("payload", {}).get("body", {}).get("data", "")
                body = base64.b64decode(body_data + "==").decode("utf-8", errors="replace")
                messages.append({"subject": subject, "body": body, "raw": msg})
    except Exception as exc:
        logger.error("gmail fetch failed history_id=%s: %s", history_id, exc)
    return messages


def parse_signal_from_email(subject: str, body: str) -> Optional[dict]:
    # Try subject line first
    m = _SUBJECT_PATTERN.search(subject)
    if m:
        return {
            "ticker": m.group("ticker").upper(),
            "timeframe": m.group("timeframe"),
            "signal_type": m.group("signal_type"),
            "timestamp": datetime.utcnow().isoformat(),
        }

    # Fall back to body parsing
    ticker_m = _BODY_TICKER.search(body)
    tf_m = _BODY_TIMEFRAME.search(body)
    sig_m = _BODY_SIGNAL.search(body)

    if ticker_m and tf_m and sig_m:
        return {
            "ticker": ticker_m.group("ticker").upper(),
            "timeframe": tf_m.group("timeframe"),
            "signal_type": sig_m.group("signal_type"),
            "timestamp": datetime.utcnow().isoformat(),
        }

    logger.warning("could not parse signal from email subject=%r", subject)
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_email_listener.py -v
```
Expected: all `PASSED`
Note: If tests fail because the sample subject/body don't match the regex, update `_SUBJECT_PATTERN` and `_BODY_*` in `email_listener.py` to match the real TOS format you documented in Step 1.

- [ ] **Step 6: Commit**

```bash
git add email_listener.py tests/test_email_listener.py docs/tos-alert-format.md
git commit -m "feat: email_listener — Gmail push payload decoding and TOS alert parsing"
```

---

## Task 5: `review_bot.py` — Discord Bot with Reaction Approval

**Files:**
- Create: `sqzdots/review_bot.py`

Note: `discord.py` bots are inherently async and event-driven. Unit tests require significant mocking overhead with limited value. This module is verified through integration testing in Task 7. The implementation is tested manually against a real Discord server during setup.

- [ ] **Step 1: Implement `review_bot.py`**

```python
import asyncio
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord

logger = logging.getLogger(__name__)


class ReviewBot(discord.Client):
    """
    Discord bot that:
    - Receives signal dicts via asyncio.Queue (put from FastAPI thread)
    - Posts chart preview to private review channel
    - Watches for operator ✅/❌ reaction for REVIEW_TIMEOUT_MINUTES
    - Calls publisher.approve / skip / expire accordingly
    """

    def __init__(self, settings, publisher, signal_queue: asyncio.Queue):
        intents = discord.Intents.default()
        intents.reactions = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.settings = settings
        self.publisher = publisher
        self.signal_queue = signal_queue
        # track pending reviews: message_id → (signal, chart_path, expire_at)
        self._pending: dict[int, tuple[dict, str, datetime]] = {}

    async def on_ready(self):
        logger.info("review bot ready user=%s", self.user)
        self.loop.create_task(self._process_queue())

    async def _process_queue(self):
        while True:
            item = await self.signal_queue.get()
            signal, chart_path = item
            await self._post_review(signal, chart_path)

    async def _post_review(self, signal: dict, chart_path: Optional[str]):
        channel = self.get_channel(self.settings.discord_review_channel_id)
        if channel is None:
            logger.error("review channel not found id=%s", self.settings.discord_review_channel_id)
            self.publisher.error(signal, reason="review channel not found")
            return

        ticker = signal["ticker"]
        timeframe = signal["timeframe"]
        signal_type = signal["signal_type"]
        ts = signal.get("timestamp", datetime.utcnow().isoformat())

        content = (
            f"📡 **NEW SIGNAL** — `${ticker}` | {timeframe} | {ts}\n"
            f"**{signal_type}**\n\n"
            f"React ✅ to post publicly | ❌ to skip\n"
            f"_Expires in {self.settings.review_timeout_minutes} minutes_"
        )

        if chart_path and Path(chart_path).exists():
            file = discord.File(chart_path, filename=Path(chart_path).name)
            msg = await channel.send(content=content, file=file)
        else:
            msg = await channel.send(content=content + "\n⚠️ _Chart unavailable_")

        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        expire_at = datetime.now(timezone.utc).timestamp() + (
            self.settings.review_timeout_minutes * 60
        )
        self._pending[msg.id] = (signal, chart_path or "", expire_at)
        self.loop.create_task(self._expire_after(msg.id))
        logger.info("review posted msg_id=%s ticker=%s", msg.id, ticker)

    async def _expire_after(self, msg_id: int):
        await asyncio.sleep(self.settings.review_timeout_minutes * 60)
        entry = self._pending.pop(msg_id, None)
        if entry:
            signal, chart_path, _ = entry
            self.publisher.expire(signal)
            logger.info("signal expired msg_id=%s ticker=%s", msg_id, signal["ticker"])

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Ignore reactions not on a pending message
        if payload.message_id not in self._pending:
            return
        # Ignore reactions from anyone other than the operator
        if payload.user_id != self.settings.discord_operator_id:
            return
        # Ignore bot's own reactions
        if payload.user_id == self.user.id:
            return

        entry = self._pending.pop(payload.message_id, None)
        if entry is None:
            return

        signal, chart_path, _ = entry
        emoji = str(payload.emoji)

        if emoji == "✅":
            self.publisher.approve(signal, chart_path)
            logger.info("signal approved ticker=%s", signal["ticker"])
        elif emoji == "❌":
            self.publisher.skip(signal, reason="operator skipped")
            logger.info("signal skipped ticker=%s", signal["ticker"])


def start_bot_thread(settings, publisher) -> asyncio.Queue:
    """
    Start the Discord bot in a background daemon thread.
    Returns the asyncio.Queue used to submit signals from the FastAPI thread.
    """
    loop = asyncio.new_event_loop()
    signal_queue: asyncio.Queue = asyncio.Queue()

    bot = ReviewBot(settings=settings, publisher=publisher, signal_queue=signal_queue)

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot.start(settings.discord_bot_token))

    thread = threading.Thread(target=run, daemon=True, name="discord-review-bot")
    thread.start()
    logger.info("discord bot thread started")
    return signal_queue, loop
```

- [ ] **Step 2: Commit**

```bash
git add review_bot.py
git commit -m "feat: review_bot — Discord bot with operator reaction approval and 30-min timeout"
```

---

## Task 6: `main.py` — FastAPI App + Webhook Authentication

**Files:**
- Create: `sqzdots/main.py`
- Create: `sqzdots/tests/test_main.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_main.py`:
```python
import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient


VALID_PAYLOAD = {
    "message": {
        "data": base64.b64encode(json.dumps({"historyId": "99999"}).encode()).decode(),
        "messageId": "test-msg-id",
    },
    "subscription": "projects/proj/subscriptions/sqzdots",
}


@pytest.mark.asyncio
async def test_webhook_returns_401_on_missing_auth():
    with patch("main.verify_google_token", side_effect=ValueError("missing token")):
        from main import app
        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post("/webhook", json=VALID_PAYLOAD)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_returns_200_on_valid_payload():
    with patch("main.verify_google_token", return_value=True), \
         patch("main.process_push_notification", new_callable=AsyncMock) as mock_process:
        from main import app
        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/webhook",
                json=VALID_PAYLOAD,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        mock_process.assert_called_once()


@pytest.mark.asyncio
async def test_health_check_returns_ok():
    from main import app
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_main.py -v
```
Expected: `FAILED` — `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Implement `main.py`**

```python
import asyncio
import logging
import logging.handlers
from contextlib import asynccontextmanager

import requests as http_requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from chart_generator import ChartGenerator
from config import settings
from email_listener import decode_history_id, fetch_new_messages, parse_signal_from_email
from publisher import Publisher
from review_bot import start_bot_thread

# Logging setup
handler = logging.handlers.RotatingFileHandler(
    settings.log_file, maxBytes=5_000_000, backupCount=3
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[handler, logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Lazy-initialized globals (set in lifespan)
_gmail_service = None
_signal_queue = None
_bot_loop = None
_publisher: Publisher = None
_chart_gen: ChartGenerator = None


def _build_gmail_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        settings.gmail_credentials_json,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        subject=settings.gmail_user_email,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def verify_google_token(authorization: str) -> bool:
    """Verify the Bearer token in the Authorization header is a valid Google-signed JWT."""
    if not authorization or not authorization.startswith("Bearer "):
        raise ValueError("Missing or malformed Authorization header")
    token = authorization.split(" ", 1)[1]
    # Fetch Google public certs and verify JWT
    certs_url = "https://www.googleapis.com/oauth2/v3/certs"
    certs_resp = http_requests.get(certs_url, timeout=5)
    certs_resp.raise_for_status()
    import jwt  # PyJWT
    try:
        jwt.decode(
            token,
            certs_resp.json(),
            algorithms=["RS256"],
            audience=settings.pubsub_audience,
            options={"verify_exp": True},
        )
    except Exception as exc:
        raise ValueError(f"Token verification failed: {exc}") from exc
    return True


async def process_push_notification(payload: dict) -> None:
    history_id = decode_history_id(payload)
    messages = fetch_new_messages(_gmail_service, user="me", history_id=history_id)
    for msg in messages:
        signal = parse_signal_from_email(msg["subject"], msg["body"])
        if signal is None:
            logger.info("non-signal email skipped subject=%r", msg["subject"])
            continue
        chart_path = _chart_gen.generate(signal)
        if chart_path is None:
            _publisher.error(signal, reason="chart generation failed")
            # Still post error notification via bot queue
        await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(
                _signal_queue.put((signal, chart_path)),
                _bot_loop,
            )
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _gmail_service, _signal_queue, _bot_loop, _publisher, _chart_gen
    _publisher = Publisher(
        output_dir=settings.output_dir,
        discord_webhook_url=settings.discord_public_channel_webhook,
    )
    _chart_gen = ChartGenerator(output_dir=settings.output_dir + "/charts")
    _gmail_service = _build_gmail_service()
    _signal_queue, _bot_loop = start_bot_thread(settings, _publisher)
    logger.info("sqzdots service started")
    yield
    logger.info("sqzdots service shutting down")


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@app.post("/webhook")
async def webhook(request: Request):
    auth = request.headers.get("Authorization", "")
    try:
        verify_google_token(auth)
    except ValueError as exc:
        logger.warning("webhook auth failed: %s", exc)
        raise HTTPException(status_code=401, detail="Unauthorized")

    payload = await request.json()
    await process_push_notification(payload)
    return JSONResponse({"status": "accepted"})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_main.py -v
```
Expected: all `PASSED`

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v --tb=short
```
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: main — FastAPI webhook endpoint with Google JWT auth and pipeline orchestration"
```

---

## Task 7: `setup.sh` + Deployment Files

**Files:**
- Create: `sqzdots/setup.sh`
- Create: `sqzdots/sqzdots.service`
- Create: `sqzdots/nginx.conf`

- [ ] **Step 1: Create `sqzdots.service` systemd unit**

```ini
[Unit]
Description=Sqzdots Signal Automation Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/sqzdots
EnvironmentFile=/opt/sqzdots/.env
ExecStart=/opt/sqzdots/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
StandardOutput=append:/opt/sqzdots/logs/sqzdots.log
StandardError=append:/opt/sqzdots/logs/sqzdots.log

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create `nginx.conf` reverse proxy config**

Replace `yourdomain.com` with your actual VPS domain or IP:
```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location /webhook {
        proxy_pass http://127.0.0.1:8000/webhook;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 30s;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000/health;
    }
}
```

- [ ] **Step 3: Create `setup.sh`**

```bash
#!/usr/bin/env bash
set -e

echo "==> Installing system dependencies"
apt-get update -q
apt-get install -y -q python3.11 python3.11-venv python3-pip nginx certbot python3-certbot-nginx

echo "==> Creating directories"
mkdir -p /opt/sqzdots/{output/{ready,skipped,expired,errors,charts},logs,credentials}

echo "==> Setting up Python virtualenv"
cd /opt/sqzdots
python3.11 -m venv venv
./venv/bin/pip install --quiet -r requirements.txt

echo "==> Checking .env file"
if [ ! -f /opt/sqzdots/.env ]; then
  echo "ERROR: /opt/sqzdots/.env not found."
  echo "Copy .env.example to .env and fill in all values before running setup."
  exit 1
fi

echo "==> Installing nginx config"
cp nginx.conf /etc/nginx/sites-available/sqzdots
ln -sf /etc/nginx/sites-available/sqzdots /etc/nginx/sites-enabled/sqzdots
nginx -t && systemctl reload nginx

echo "==> Obtaining SSL certificate (requires domain DNS to resolve to this server)"
read -p "Enter your domain name: " DOMAIN
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@"$DOMAIN"

echo "==> Installing and starting systemd service"
cp sqzdots.service /etc/systemd/system/sqzdots.service
systemctl daemon-reload
systemctl enable sqzdots
systemctl start sqzdots

echo "==> Verifying service is running"
sleep 3
systemctl status sqzdots --no-pager

echo ""
echo "✅ Setup complete. Test the health endpoint:"
echo "   curl https://$DOMAIN/health"
```

- [ ] **Step 4: Create `.gitignore`**

```bash
cat > .gitignore << 'EOF'
.env
credentials/
__pycache__/
*.pyc
.pytest_cache/
output/
logs/
test_webhook.py
EOF
```

- [ ] **Step 5: Make setup script executable and commit**

```bash
chmod +x setup.sh
git add setup.sh sqzdots.service nginx.conf .gitignore
git commit -m "feat: deployment — systemd service, nginx reverse proxy, setup script"
```

---

## Task 8: End-to-End Integration Test

This task verifies the full pipeline works on the VPS with real credentials.

- [ ] **Step 1: Copy project to VPS**

```bash
# From your local machine
scp -r /path/to/sqzdots/ root@your-vps-ip:/opt/sqzdots/
```

- [ ] **Step 2: Populate `.env` on VPS**

```bash
ssh root@your-vps-ip
cp /opt/sqzdots/.env.example /opt/sqzdots/.env
nano /opt/sqzdots/.env  # fill in all values
```

- [ ] **Step 3: Run setup script**

```bash
cd /opt/sqzdots
bash setup.sh
```

- [ ] **Step 4: Verify health endpoint**

```bash
curl https://yourdomain.com/health
```
Expected: `{"status":"ok"}`

- [ ] **Step 5: Send a test push notification**

Use this Python script on the VPS to get a recent historyId AND fire the webhook in one step.
It generates a valid Google-signed token using the service account credentials already present:

```bash
python3 /opt/sqzdots/test_webhook.py
```

Create `/opt/sqzdots/test_webhook.py` once, then delete after testing:
```python
"""One-shot integration test: fetches historyId and fires a signed webhook POST."""
import base64
import json
import os
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

CREDS_FILE = "/opt/sqzdots/credentials/gmail_service_account.json"
GMAIL_USER = os.environ["GMAIL_USER_EMAIL"]
WEBHOOK_URL = os.environ["PUBSUB_AUDIENCE"]  # e.g. https://yourdomain.com/webhook
PUBSUB_AUDIENCE = os.environ["PUBSUB_AUDIENCE"]

# 1. Build Gmail service and get current historyId
creds = service_account.Credentials.from_service_account_file(
    CREDS_FILE,
    scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    subject=GMAIL_USER,
)
svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
profile = svc.users().getProfile(userId="me").execute()
history_id = profile["historyId"]
print(f"historyId: {history_id}")

# 2. Get a Google-signed Bearer token for the Pub/Sub service account
token_creds = service_account.IDTokenCredentials.from_service_account_file(
    CREDS_FILE,
    target_audience=PUBSUB_AUDIENCE,
)
token_creds.refresh(GoogleRequest())
bearer_token = token_creds.token

# 3. Build the Pub/Sub push payload (properly JSON-encoded + base64)
pubsub_data = base64.b64encode(json.dumps({"historyId": history_id}).encode()).decode()
payload = {
    "message": {"data": pubsub_data, "messageId": "test-integration"},
    "subscription": "projects/test/subscriptions/sqzdots",
}

# 4. POST to the webhook
resp = requests.post(
    WEBHOOK_URL,
    json=payload,
    headers={"Authorization": f"Bearer {bearer_token}"},
    timeout=20,
)
print(f"Response: {resp.status_code} {resp.text}")
```

- [ ] **Step 6: Verify Discord preview appears**

Check the `#b3-signal-review` Discord channel — a preview message with chart image and ✅/❌ reactions should appear within 15 seconds of the webhook call.

- [ ] **Step 7: React ✅ and verify public post**

React ✅ on the review message. Verify:
1. Chart appears in the public `#daily-watchlist` channel
2. `/opt/sqzdots/output/ready/` contains a `.png` and `.txt` file

- [ ] **Step 8: Verify reboot persistence**

```bash
reboot
# After reboot:
systemctl status sqzdots
curl https://yourdomain.com/health
```
Expected: service is running, health returns `{"status":"ok"}`

- [ ] **Step 9: Clean up test script and final commit**

```bash
# Delete the test webhook script — do NOT commit credentials or test tooling
rm /opt/sqzdots/test_webhook.py

git add .
git commit -m "chore: end-to-end integration verified on VPS"
```

---

## MVP Checklist

Cross these off as each integration test step passes:

- [ ] Webhook receives Gmail push → chart generated and preview posted in under 15 seconds
- [ ] Preview posted to private Discord review channel with chart image attached
- [ ] Only operator reactions trigger approve/skip
- [ ] ✅ reaction → chart posted to public Discord channel automatically
- [ ] ❌ reaction → signal archived to `skipped/`, nothing posted publicly
- [ ] 30-minute timeout → signal auto-archived to `expired/`
- [ ] Chart generation failure → error posted to review channel, archived to `errors/`
- [ ] `/output/ready/` contains chart PNG + tweet text after approval
- [ ] System survives VPS reboot
- [ ] All signals logged with timestamps
