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

New flags: `--ledger` (append fired signals to `ledger/signals.jsonl`),
`--site` / `--no-site` (regenerate the public track-record site alongside the
scan). CI scans `universe.csv` (not `watchlist.csv`).

## Telegram setup

1. In Telegram, message **@BotFather** → `/newbot` → copy the **bot token**.
2. Message your new bot once (say "hi"), then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy your **chat id**.
3. Locally: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` env vars and run
   `scanner.run` without `--dry-run`.
4. In GitHub: repo **Settings → Secrets and variables → Actions** → add
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Conviction score (decision layer)

Every fired ticker gets a **conviction score (0-100 + grade)** — a confluence
ladder (which of your buy conditions are lit) plus strength (RSI, MACD, PPO, Moxie
percentile-ranked over the ticker's own history, squeeze freshness, R:R). Fired
tickers are ranked by it, so "which to look at first" is answered. This is
deterministic and always on (`scanner/score.py`).

An earlier, optional **LLM eval layer** has been retired from the product
pipeline — the scan runs on the deterministic engine only. The module
(`scanner/llm_eval.py`) is retained in the repo for reference but is not wired
into `scanner/run.py`. No `ANTHROPIC_API_KEY` is needed or used anywhere in
this project.

## Automate (GitHub Actions)

`.github/workflows/scan.yml` runs the scan weekdays at 21:30 UTC (after the US
close), pushes Telegram alerts, appends fired signals to the ledger,
regenerates the track-record site, and commits `out/` + `ledger/` back so the
dashboard updates. Use the **Run workflow** button to trigger it manually.
`.github/workflows/recap.yml` posts a weekly recap card to Telegram every
Sunday. `.github/workflows/free-delayed.yml` posts yesterday's signals to the
free channel once `PHASE` is set to `2`.

## Dashboard (Streamlit Community Cloud)

Point Streamlit Cloud at this repo, main file `dashboard/app.py`. It reads
`out/results.json` (kept current by the Action) and renders interactive charts.

## Backtest

```powershell
# see scanner/backtest.py — walk-forward, point-in-time (no lookahead)
```

## Product pipeline (Sqzdots Indicator)

- `universe.csv` — curated ~120-name product universe (the scan input in CI)
- `ledger/signals.jsonl` — the live signal ledger (append-oriented; closed
  records are never edited; git history is the tamper-evidence)
- `python -m scanner.backtest --universe universe.csv --period 5y` — Phase 0
  walk-forward backtest (hours for the full universe; use `--symbols A,B` to smoke)
- `python -m scanner.trackrecord --out site` — regenerate the public
  track-record site (deployed to the public repo in `vars.SITE_REPO` by CI)
- `python -m scanner.recap --dry-run` — weekly recap card
- `python -m scanner.delayed --dry-run` — Phase 2 free-channel delayed post

CI secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ADMIN_CHAT_ID`,
`TELEGRAM_FREE_CHAT_ID` (Phase 2), `SITE_DEPLOY_KEY` (SSH private key; its
public half is registered as a write deploy key on the site repo).
CI variables: `SITE_REPO`, `PHASE`, `TELEGRAM_FOOTER`, `SITE_CHANNEL_USERNAME`,
`SITE_CHANNEL_URL`.

## Decision tracking (measure your own judgment)

Reply **go** or **pass** directly to a signal's chart photo in Telegram (long-press
→ Reply). A twice-daily job (`decisions.yml`) records your call in
`ledger/signals.jsonl` (`decision` / `decided_at` / `decision_late`) next to the
mechanical outcome. Decisions made after the entry open are flagged late and kept
out of the clean stats. The weekly recap card and the dashboard's Decision Review
section report GO vs PASS performance — the gap is your measured selection alpha.
Unthreaded `go TSLA` also works. `python -m scanner.decisions` runs an ingest
manually.
