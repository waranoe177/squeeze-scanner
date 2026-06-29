# Sqzdots — Signal Automation System Design
**Date:** 2026-03-31
**Status:** Approved
**Project:** B3 Signal Intelligence — automated signal workflow

---

## Overview

Sqzdots is an automated trading signal workflow that listens for ThinkOrSwim (TOS) squeeze alerts, generates branded chart images with squeeze indicator overlays, routes them through a private Discord review window, then auto-posts to a public Discord channel and stages a pre-formatted tweet for manual Twitter posting.

The system runs as a persistent `systemd` service on an existing Hostinger VPS (Ubuntu Linux).

---

## Goals

- Eliminate manual screenshot and posting work from the daily signal workflow
- Maintain a human review step before public posting to catch bad signals or rendering issues
- Auto-post approved signals to public Discord channel instantly
- Stage pre-formatted tweet copy + chart image for fast manual Twitter posting
- Archive all signals (approved, skipped, expired) for track record building

---

## Non-Goals

- Automated Twitter posting (deferred — requires $100/month Twitter API Basic tier)
- Replicating exact B3/B5 ThinkScript indicator logic (baseline TTM Squeeze used; calibration is a post-MVP task)
- Web dashboard (out of scope for MVP)
- Multi-user access (single operator for MVP)

---

## Architecture

### System Components

```
sqzdots/
├── main.py              # FastAPI app — webhook endpoint, wires modules together
├── email_listener.py    # Parses Gmail push notification, extracts ticker + timeframe
├── chart_generator.py   # Fetches OHLCV data, computes squeeze, renders chart PNG
├── review_bot.py        # Discord bot — posts preview, watches for ✅/❌ reaction
├── publisher.py         # Posts to public Discord channel, writes output files
├── .env                 # Credentials (Gmail, Discord tokens, channel IDs)
└── setup.sh             # One-command VPS setup script
```

### Data Flow

```
TOS Alert Email
    → Gmail Pub/Sub push notification (historyId payload)
    → POST /webhook (FastAPI on VPS)
    → Verify Google Bearer token (authentication)
    → users.history.list → users.messages.get → email content
    → email_listener  → {ticker, timeframe, signal_type, timestamp}
    → chart_generator → chart PNG (1200×675px)
    → review_bot      → private #b3-signal-review Discord channel (operator-only)
    → operator reacts ✅ or ❌ (30-minute window)
    → publisher (on ✅):
        → POST to public #daily-watchlist Discord channel
        → Write chart + tweet copy to /output/ready/
    → archive (on ❌ or timeout):
        → Write to /output/skipped/ or /output/expired/
```

---

## Module Design

### `email_listener.py`

**Gmail Pub/Sub push flow (important — not a direct messageId):**
Gmail push notifications contain a base64-encoded `historyId`, not a `messageId` directly.
Retrieval requires two steps:
1. `users.history.list(startHistoryId=historyId)` → get list of new message IDs
2. `users.messages.get(messageId)` → fetch full email content

**Parsing:**
TOS alert emails must be sampled during setup to define the parser. The operator must forward
one example TOS alert email so the exact subject/body format can be documented and a regex
written. Expected fields to extract:
- `ticker` — stock symbol (e.g. `AAPL`)
- `timeframe` — chart period (e.g. `Daily`, `1 Hour`, `15 Min`)
- `signal_type` — alert type (e.g. `Squeeze Fired`, `Squeeze On`)
- `timestamp` — alert time (ET)

Parser must log a structured error and return `None` (not raise) on any parse failure.
Pipeline must silently discard `None` results and log the raw email for debugging.

**Returns:** `{ticker, timeframe, signal_type, timestamp}` or `None`

### `chart_generator.py`

**yfinance timeframe mapping:**
TOS timeframe strings must be mapped to yfinance `interval` + `period` parameters:

| TOS Timeframe | yfinance interval | yfinance period |
|---|---|---|
| `Daily` | `1d` | `90d` |
| `Weekly` | `1wk` | `2y` |
| `1 Hour` | `1h` | `60d` |
| `4 Hour` | `1h`* | `60d` |
| `30 Min` | `30m` | `60d` |
| `15 Min` | `15m` | `60d` |
| `5 Min` | `5m` | `60d` |

*Note: yfinance does not support a native `4h` interval. For 4-hour charts, fetch `1h` data
and resample to 4-hour OHLCV bars using pandas `resample('4H')` before plotting.

**Chart rendering:**
- Accepts signal dict from email_listener
- Fetches OHLCV data via `yfinance` (60 bars after resampling if applicable)
- Computes TTM Squeeze indicator:
  - Bollinger Bands (period=20, std=2.0)
  - Keltner Channels (period=20, multiplier=1.5)
  - Squeeze state: red dot = squeeze on, green dot = squeeze fired
  - Momentum: linear regression delta of (close - midpoint of BB/KC)
- Renders chart using `mplfinance` + `matplotlib`:
  - Panel 1: Candlestick (60 bars) + ticker/timeframe label
  - Panel 2: Volume bars
  - Panel 3: Squeeze momentum histogram (green/red) + squeeze dots on zero line
  - B3 Signal Intelligence watermark (bottom-right, low opacity)
- Outputs: `{ticker}_{timeframe}_{date}.png` at 1200×675px

**Error handling:**
- If `yfinance` returns empty data (ticker not found, market closed, rate-limited):
  → Post error message to `#b3-signal-review`: `⚠️ Chart failed for $TICKER — no data returned`
  → Archive signal to `/output/errors/` with reason
  → Do NOT proceed to review flow
- If `mplfinance` raises during rendering:
  → Same error post + archive as above
- Note: TTM Squeeze parameters will be calibrated against TOS output post-MVP

### `review_bot.py`

**Event loop integration:**
The Discord bot (`discord.py`) runs its own async event loop. To coexist with FastAPI/uvicorn,
the bot runs in a dedicated background thread started at application startup. Inter-thread
communication uses an `asyncio.Queue` — the FastAPI webhook handler puts signal payloads onto
the queue; the bot's event loop consumes from it and posts the review message.

**Review flow:**
- Posts to private `#b3-signal-review` channel
- Channel permissions: only the operator Discord account can see or react
- Bot filters reactions: only reacts from the operator user ID (`DISCORD_OPERATOR_ID` in `.env`) are processed; reactions from any other user are ignored
- Message format:
  ```
  📡 NEW SIGNAL — $TICKER | Timeframe | YYYY-MM-DD HH:MM ET
  [chart image]
  React ✅ to post publicly | ❌ to skip
  Expires in 30 minutes
  ```
- On ✅ from operator: calls `publisher.approve(signal)`
- On ❌ from operator: calls `publisher.skip(signal)`
- On timeout (30 min): calls `publisher.expire(signal)`

### `publisher.py`

- `approve(signal)`:
  - Posts chart to public `#daily-watchlist` Discord channel via webhook URL
  - Writes chart PNG + pre-formatted tweet text to `/output/ready/`
  - Tweet template: `$TICKER — {Timeframe} Squeeze Fired 🔴\n#FinTwit #Squeeze #B3Signals`
- `skip(signal)`: Archives to `/output/skipped/` with reason log
- `expire(signal)`: Archives to `/output/expired/` with timestamp log
- `error(signal, reason)`: Archives to `/output/errors/` with error detail

### `main.py`

- FastAPI application, single POST `/webhook` endpoint
- **Webhook authentication:** Validates Google-signed Bearer token on every request
  - Fetch Google's public certs from `https://www.googleapis.com/oauth2/v3/certs`
  - Verify JWT signature, audience (`PUBSUB_AUDIENCE` in `.env`), and expiry
  - Reject with HTTP 401 on any validation failure
- On valid request: decodes base64 `historyId`, passes to `email_listener`
- Orchestrates the full pipeline
- Runs via `uvicorn` on port 8000, behind nginx reverse proxy with HTTPS

---

## Environment Variables (`.env` inventory)

```env
# Gmail / Google Cloud
GMAIL_CREDENTIALS_JSON=/opt/sqzdots/credentials/gmail_service_account.json
GMAIL_USER_EMAIL=your@gmail.com
PUBSUB_TOPIC=projects/{project-id}/topics/sqzdots-alerts
PUBSUB_AUDIENCE=https://yourdomain.com/webhook

# Discord
DISCORD_BOT_TOKEN=...
DISCORD_REVIEW_CHANNEL_ID=...       # private #b3-signal-review channel
DISCORD_PUBLIC_CHANNEL_WEBHOOK=...  # webhook URL for #daily-watchlist
DISCORD_OPERATOR_ID=...             # your Discord user ID (snowflake)

# App
OUTPUT_DIR=/opt/sqzdots/output
LOG_FILE=/opt/sqzdots/logs/sqzdots.log
REVIEW_TIMEOUT_MINUTES=30
```

---

## Output Folder Structure

```
/opt/sqzdots/
├── .env
├── credentials/
│   └── gmail_service_account.json   # Google service account key (never commit)
├── output/
│   ├── ready/      # Approved: chart PNG + tweet .txt — awaiting Twitter post
│   ├── skipped/    # Manually skipped by operator
│   ├── expired/    # No reaction within 30-minute window
│   └── errors/     # Chart generation or parse failures
└── logs/
    └── sqzdots.log # Full signal history with timestamps
```

---

## External Dependencies

| Service | Purpose | Cost |
|---|---|---|
| Gmail API + Google Pub/Sub | Push notifications for TOS alert emails | Free |
| yfinance | OHLCV price data | Free |
| Discord bot + webhook | Review channel + public posting | Free |
| Hostinger VPS (Ubuntu) | Hosts the service 24/7 | Already subscribed |
| Twitter API v2 Basic | **Deferred** — not in MVP | ($100/mo when added) |

---

## Deployment

### One-Time Setup (automated via `setup.sh`)
1. Install Python 3.11, pip dependencies
2. Configure Google Cloud project → enable Gmail API → create Pub/Sub topic → register VPS HTTPS endpoint as push subscriber
3. **Forward one TOS alert email to document the format** — required before parser can be written
4. Create Discord bot → add to server → note token, channel IDs, operator user ID
5. Create Discord channels: `#b3-signal-review` (private, operator-only), `#daily-watchlist` (public)
6. Configure TOS alerts to send to Gmail address
7. Populate `.env` with all credentials (see inventory above)
8. Install and start `systemd` service (`sqzdots.service`)
9. Configure nginx reverse proxy with Let's Encrypt SSL for webhook endpoint

### Runtime
- Service auto-starts on boot
- Auto-restarts on crash (systemd `Restart=always`)
- Logs to `/opt/sqzdots/logs/sqzdots.log`

---

## MVP Success Criteria

- [ ] Webhook receives Gmail push → chart generated and preview posted to review channel in under 15 seconds from webhook receipt
- [ ] Preview posted to private Discord review channel with chart image attached
- [ ] Only reactions from the operator Discord account trigger approve/skip
- [ ] ✅ reaction → chart posted to public Discord channel automatically
- [ ] ❌ reaction → signal archived to skipped/, nothing posted publicly
- [ ] 30-minute timeout → signal auto-archived to expired/
- [ ] Chart generation failure → error posted to review channel, archived to errors/
- [ ] `/output/ready/` contains chart PNG + tweet text after approval
- [ ] System survives VPS reboot (systemd auto-start)
- [ ] All signals logged with timestamps

---

## Post-MVP Calibration Tasks

1. Compare generated squeeze charts against TOS output for same ticker/timeframe
2. Adjust TTM Squeeze parameters (Keltner multiplier, BB period) to match TOS visuals
3. Add higher-timeframe confluence panel to chart
4. Add Twitter API Basic integration once MRR justifies the cost
5. Add lightweight password-protected web page to browse `/output/ready/` from any device
