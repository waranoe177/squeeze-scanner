# TOS Parity Validation — how to fill in cases.csv

Goal: confirm the Python engine reproduces what your B3 setup shows in
ThinkorSwim. You report what **TOS displays**; the engine fetches the price data
and computes the indicators itself, then we compare.

## What's in the template

I pre-filled 5 live cases from today's scan so you can check them right now in
TOS (the most recent daily bar). 4 the engine says are bullish, 1 it says is a
near-miss (in a squeeze but not aligned). **You can keep these, or replace any
row with your own cases** — the assignment ideal is 3 that fired + 2 near-misses.

## Setup before you read

- Chart timeframe: **Daily** (this scanner is daily/swing).
- Note your price adjustment setting (TOS default is split-adjusted). Just tell
  me if you've changed it.

## Columns — fill the `tos_*` ones, leave `engine_signal` alone

| Column | Required? | What to enter | Where to read it in TOS |
|--------|-----------|---------------|--------------------------|
| `ticker` | (prefilled) | symbol | — |
| `date` | (prefilled) | the daily bar, YYYY-MM-DD | — |
| `engine_signal` | (prefilled) | what my code computed — for reference only | — |
| `tos_signal` | **YES** | `bull` / `bear` / `none` | B3 Super dots TOP dot (Scanner_Signal): CYAN = bull, MAGENTA = bear, BLACK/absent = none |
| `tos_close` | **Recommended** | the bar's close price TOS shows | hover the bar; rules out a data-source mismatch |
| `tos_squeeze` | helpful | `on` / `off` | squeeze dot: ORANGE = in squeeze (aggressive), DARK GRAY = no squeeze |
| `tos_rsi` | helpful | the RSI(14) value, or just `>50` / `<50` | your RSI study |
| `tos_macd_color` | helpful | `green` / `blue` / `red` / `orange` | MACD Diff color (green=pos&up, blue=pos&down, red=neg&down, orange=neg&up) |
| `tos_ema_stacked` | helpful | `yes` / `no` | Stacked EMAs: is it 8>21>34 AND 50>200 (bull) — or the bearish mirror? |
| `notes` | optional | anything odd | — |

**Minimum viable:** just fill `tos_signal` for all 5 rows. That alone tells us if
the final signal matches. The other columns let me pinpoint *which* indicator is
off if a row disagrees — filling them now saves a back-and-forth.

## When done

Save the file and tell me. I'll fetch each ticker/date, run the engine, and
build a validation test that asserts engine == TOS for all 5. Any mismatch, I
fix the parity knob (stdev type, TR averaging, price adjustment) and we re-run.
