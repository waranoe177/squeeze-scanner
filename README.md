# Squeeze Scanner

A personal swing-trade scanner that reproduces a B3 Squeeze / Watkins-Moxie
confluence (decoded from ThinkorSwim) in Python, runs once daily after the close,
and pushes alerts to Telegram with a chart. A Streamlit dashboard shows the daily
results and interactive charts on your phone.

**Personal tool — not financial advice.** The signal is validated against TOS;
mechanical-system edge is unproven (see `validation/RESULTS.md`). Use it as an
alert tool and apply your own judgment.

## The signal (your buy process)

BUY when ALL of: squeeze ON · RSI > 50 · PPO ≥ 0 · EMA8 > EMA21 ·
full stack (8>21>34 and 50>200) · MACD green (Diff ≥ 0 and rising) ·
Moxie > 0 and green (rising, weekly). SELL = strict mirror.

## Layout

```
scanner/        signal engine + data + notify + chart + run
  indicators.py   EMA/SMA/ATR/RSI/MACD/PPO/Bollinger/Keltner/Squeeze/Moxie
  signals.py      confluence -> scanner_bull/bear + latest_signal payload
  data.py         watchlist + yfinance fetch + forming-bar drop
  scan.py         run across watchlist, rank, assemble results
  notify.py       Telegram message format + send
  chart.py        matplotlib snapshot for Telegram
  run.py          CLI entrypoint
  backtest.py     walk-forward backtest harness
  score.py        conviction score (0-100) + expected value
  llm_eval.py     qualitative LLM read (news) + GO/WATCH/PASS (optional)
dashboard/app.py  Streamlit dashboard (interactive charts)
tests/            68 tests (pytest), incl. TOS parity fixtures
watchlist.csv     your tickers
out/              generated: results.json + charts/ (committed for the dashboard)
```

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest        # 68 tests
```

> Windows note: Smart App Control blocks pandas 3.0.x DLLs; requirements pin
> pandas 2.2.3, which it trusts.

## Run the scan

```powershell
# dry run (no Telegram): writes out/results.json + out/charts, prints the message
$env:PYTHONPATH="."; .venv\Scripts\python.exe -m scanner.run --dry-run
```

## Telegram setup

1. In Telegram, message **@BotFather** → `/newbot` → copy the **bot token**.
2. Message your new bot once (say "hi"), then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy your **chat id**.
3. Locally: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` env vars and run
   `scanner.run` without `--dry-run`.
4. In GitHub: repo **Settings → Secrets and variables → Actions** → add
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Conviction score + LLM eval (decision layer)

Every fired ticker gets a **conviction score (0-100 + grade)** — a confluence
ladder (which of your buy conditions are lit) plus strength (RSI, MACD, PPO, Moxie
percentile-ranked over the ticker's own history, squeeze freshness, R:R). Fired
tickers are ranked by it, so "which to look at first" is answered. This is
deterministic and always on (`scanner/score.py`).

Optionally, an **LLM eval layer** (`scanner/llm_eval.py`) adds a qualitative read:
it pulls recent news (yfinance) and asks Claude (`claude-opus-4-8`) for a stance
(bullish/neutral/bearish), catalysts, risks, and a 0-100 qualitative score, then
combines quant + qualitative into a **GO / WATCH / PASS** call. It runs only when
`ANTHROPIC_API_KEY` is set (add it as a GitHub Actions secret); without it the
scan degrades gracefully to the quant score. Cost is a few cents/day (only fired
tickers are evaluated). The deterministic engine remains the source of truth for
the *signal* — the LLM only adds context.

## Automate (GitHub Actions)

`.github/workflows/scan.yml` runs the scan weekdays at 21:30 UTC (after the US
close), pushes Telegram alerts, and commits `out/` back so the dashboard updates.
Use the **Run workflow** button to trigger it manually.

## Dashboard (Streamlit Community Cloud)

Point Streamlit Cloud at this repo, main file `dashboard/app.py`. It reads
`out/results.json` (kept current by the Action) and renders interactive charts.

## Backtest

```powershell
# see scanner/backtest.py — walk-forward, point-in-time (no lookahead)
```
