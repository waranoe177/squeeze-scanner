# Sqzdots in thinkScript

A TOS port of the Python scanner, so the setup can be run inside thinkorswim.
Source of truth is still `scanner/signals.py` + `scanner/indicators.py` — this
is a translation, and the parity notes below say exactly where it can drift.

| File | What | Aggregation |
|---|---|---|
| `Sqzdots_Scan_Daily.ts` | The 6 daily conditions | **D** |
| `Sqzdots_Scan_MoxieWeekly.ts` | The weekly Moxie gate | **W** |
| `Sqzdots_Chart.ts` | Chart study: dots + condition counter | Daily chart |

---

## Why the scan is split across two filters

The signal is 7 conditions. Six are computed on daily bars. The seventh —
Moxie — is computed on **weekly** bars and then read on the daily bar.

thinkScript cannot do that correctly inside one daily study. Writing
`ExpAverage(close(period = AggregationPeriod.WEEK), 12)` on a daily chart
gives you a 12-**day** average of a weekly step-series, not a 12-**week** EMA.
It compiles, it plots, and it is wrong — the most dangerous kind of wrong,
because the number looks plausible.

Stock Hacker sets aggregation **per filter**, so the fix is structural: run
the Moxie filter at W, where `close` *is* the weekly close and `ExpAverage` is
a real weekly EMA. The scan ANDs the two filters together. No workaround, no
repaint surprise, exact parity with the Python.

## Setup

1. **Scan tab → Stock Hacker → Add filter → Study → Custom → thinkScript editor.**
   Paste `Sqzdots_Scan_Daily.ts`. Name it `Sqzdots_Daily`. Set the filter's
   aggregation dropdown to **D**. Condition: `scan is true`.
2. **Add a second filter**, same route. Paste `Sqzdots_Scan_MoxieWeekly.ts`.
   Name it `Sqzdots_MoxieW`. Set aggregation to **W**. Condition: `scan is true`.
3. Set the universe (watchlist, or an index) and scan.

### Getting A++ vs A separately

`Sqzdots_Scan_Daily` takes two inputs:

| Input | Values | Meaning |
|---|---|---|
| `direction` | `1` bull, `-1` bear | Which side to scan |
| `grade` | `1` = A++ only, `0` = A only, `-1` = both | MACD state |

`grade = 1` requires MACD already green (at/above zero and rising) — the full
7/7. `grade = 0` requires MACD still below zero but rising: the early signal
that fires one bar before A++ can. They are mutually exclusive by construction.
Default is `-1` (both), matching what the daily alert sends.

For a bear scan set `direction = -1` on **both** filters.

---

## Parity notes — the non-obvious bits

These are the places a naive translation silently diverges. All four are
already handled in the files.

**`TrueRange` argument order is `(high, close, low)`.** Not `(high, low, close)`.
Getting this wrong shifts the Keltner width and quietly changes which bars are
in squeeze.

**MACD signal length is 34, not 9.** `ExpAverage(macdLine, 34)`. This is what
B3 uses and it is the single biggest source of "why doesn't this match".

**RSI is written out longhand** rather than calling `RSI(14)`. TOS's RSI uses
the reverse formula `50 * (ChgRatio + 1)` with Wilders smoothing, which is what
`indicators.rsi()` reproduces. Writing it inline makes the match provable
instead of assumed.

**`StDev` is population standard deviation** in thinkScript, matching pandas
`std(ddof=0)` in `indicators.bollinger()`. If you swap in a sample stdev the
Bollinger bands widen slightly and marginal squeezes flip off.

### Where it will still differ from the Python

- **Price adjustment.** The Python pulls `auto_adjust=True` — split *and*
  dividend adjusted. TOS charts are split-adjusted only. On a dividend payer
  the two histories differ slightly, so a marginal signal can land on one side
  in TOS and the other in Python. On non-payers they agree.
- **Forming bar.** The Python drops the in-progress daily bar before market
  close (`data.drop_forming_bar`). TOS includes it. Scanning intraday will show
  names that have not actually completed the setup. Scan after 4:00 PM ET.
- **Futures.** Stock Hacker scans equities. ES/NQ/YM/RTY/GC/SI/CL/BTC from
  `futures.csv` are not scannable there — put the chart study on those symbols
  directly instead.
- **Mid-week Moxie.** The weekly filter reads the week-to-date bar, so a
  Monday result can change by Friday. The Python does the same thing on
  purpose (`bfill` to the containing week) because that is what TOS does. Not
  a bug in either, but it means a Tuesday scan is a provisional answer.

## The chart study

`Sqzdots_Chart.ts` is a lower-panel study. It plots a histogram of how many of
the 6 daily conditions are lit (dashed line at 6), colored green/red by lean,
plus dots when the daily side fires: solid green/red for A++, white/gray for
the early A.

**It deliberately stops at 6 of 7.** The Moxie gate is left out for the reason
in the first section — on a daily chart there is no honest way to compute it.
The label says `Moxie: check weekly`. Confirm it by putting the Moxie study on
a weekly chart, or just trust the scan, where it is exact.

So: a chart dot means "6 daily conditions aligned, Moxie unverified." A scan
hit means all 7. The scan is the authority.
