# Sqzdots in thinkScript

A TOS port of the Python scanner, so the setup can be run inside thinkorswim.
Source of truth is still `scanner/signals.py` + `scanner/indicators.py` — this
is a translation, and the parity notes below say exactly where it can drift.

| File | What | Aggregation |
|---|---|---|
| `Sqzdots_Scan_Daily.ts` | The 6 daily conditions, both sides, `direction`/`grade` inputs | **D** |
| `Sqzdots_Scan_AAA_Bull.ts` | Bull A++ only, hardcoded — cross-checks the column | **match the column** |
| `Sqzdots_Scan_AAA_Bear.ts` | Bear A++ only, hardcoded — cross-checks the column | **match the column** |
| `Sqzdots_Scan_MoxieWeekly.ts` | The weekly Moxie gate | **W** |
| `Sqzdots_Chart.ts` | Chart study: dots + condition counter | Daily chart |
| `Sqzdots_Col_Daily_v3.ts` | Watchlist column: tier + run length | **per column** |
| `Sqzdots_Col_MoxieW.ts` | Watchlist column: the Moxie gate | **W** |

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

## Cross-checking the column with a scan

`Sqzdots_Scan_AAA_Bull.ts` and `Sqzdots_Scan_AAA_Bear.ts` are the A++
condition from `Sqzdots_Col_Daily_v3.ts`, hardcoded to one side with no
`direction` or `grade` input to set wrong. They exist to answer "is the column
telling me the truth", so they take exactly the same 6 of 7 the column does —
**no Moxie gate**.

Set the filter's aggregation to match the column you are checking. The hit
list should equal the `A++` cells in that column: green for the bull file, red
for the bear file. A disagreement is worth chasing; start with the forming
bar (TOS includes it, the Python drops it) and dividend adjustment.

| Input | Default | What it does |
|---|---|---|
| `maxRun` | `0` | `0` = every A++. Otherwise require the structure run ≤ this, so `maxRun = 1` is "formed today". Same counter as the number in the column. |

To turn either one into a tradeable scan rather than a cross-check, add
`Sqzdots_Scan_MoxieWeekly.ts` at **W** aggregation as a second filter —
`direction = 1` beside the bull file, `direction = -1` beside the bear file.
Stock Hacker ANDs them, and that is the full 7 of 7.

One asymmetry that looks like a typo and is not: the bull side uses
`ppo >= 0`, the bear side `ppo < 0`. Zero belongs to the bull side, which is
what `scanner/signals.py` does, and it keeps the two mutually exclusive.

## The watchlist columns

Same split, same reason. `Sqzdots_Col_Daily.ts` carries the 6 daily
conditions; the Moxie gate is its own column at W. A green `A++` cell is
**not** a signal until the Moxie column agrees in the same direction.

**Setup.** Watchlist → right-click a column header → Customize → scroll to
`Custom` in the left list → pencil icon → paste → name it → set the
**aggregation dropdown** in that same dialog. Add `Sqzdots_Col_Daily_v3` once
per aggregation you want, the same way `WK/3D/2D/1D_B3_Sqz` are four copies of
one study:

| Column name | Aggregation |
|---|---|
| `1D_Sqz_AAA` | D |
| `2D_Sqz_AAA` | 2 Days |
| `3D_Sqz_AAA` | 3 Days |
| `WK_Sqz_AAA` | W |
| `WK_Moxie` | W |

**Reading a cell.** The number is a **consecutive run counter**, the same way
the `B3_Sqz` columns read: `1` is the bar the setup formed, `4` means it has
now held four bars. The letter is the MACD state *on the current bar*.

| Cell | Meaning |
|---|---|
| `A: 1` | setup formed on this bar, MACD early (below zero, rising) |
| `A++: 2` | held two bars, MACD green right now — the full 6/6 |
| `. 7` | structure has held seven bars, MACD not confirming yet |
| blank | structure is not in place |

Green is bull, red is bear, gray is waiting-on-MACD. The cell background goes
dark on `1`, so the trigger bar is the one that stands out.

The tier names come from the Python, not from B3: `A++` is the 7/7 signal and
`A` is the anticipatory one bar earlier. There is no `A+` — if you want the
column to read that way, change the label strings in the `AddLabel` call.

### What the number counts, and why it isn't the literal 6/6

The run counts the **five structural conditions** — squeeze, RSI, PPO,
`ema8 > ema21`, full stack. MACD is deliberately excluded from the run and
used only for the letter. Measured over the 157-name universe, two years of
daily bars:

| run base | runs | median | max | % len=1 | % ≥15 |
|---|---|---|---|---|---|
| 5 structural (no MACD) | 1431 | 3 | 33 | 21.5% | 4.5% |
| literal 6/6 incl. `macd_green` | 788 | 2 | **9** | 44.3% | **0.0%** |

`macd_green` is `diff >= 0 AND diff > diff[1]`. That second half is a
bar-to-bar slope test, so the full 6/6 shatters into 1s and 2s and **cannot
produce a number like 15** — it tops out at 9. Set `runBase = 1` to count it
anyway if you want the strict reading; the cap is real, not a bug.

| Input | Default | What it does |
|---|---|---|
| `runBase` | `0` | `0` = count the 5 structural conditions. `1` = count the literal 6/6. |
| `showTier` | `-1` | `-1` = A++, A and waiting. `1` = A++ only. `0` = A++ and A, hide waiting. |
| `maxRun` | `0` | `0` = no cap; otherwise blank the cell once the run passes this. |

Sorting works: the column plots a rank that puts A++ above A above waiting,
then by run length, bulls positive and bears negative. Click the header.

**One cosmetic thing to check on first paste.** The number is wrapped in
`Round(n, 0)`, but some TOS builds still render it as `5.00`. Harmless; if it
bothers you it is the `Round` call in the `AddLabel` line.

**Why the label is always visible.** `AddLabel(yes, if !show then "" else ...)`
rather than `AddLabel(show, ...)`. If the label is hidden, TOS falls back to
rendering the plot value and every empty cell reads `NaN`. That was the v2 bug
and it is the only difference between v2 and v3.

**Why the cell text is built inline.** `def` holds **doubles only** —
thinkScript has no string variable. `def txt = "UP " + ...` fails with
`Expected double`, and every later use of that name then mismatches as
`different types after then and else: double vs class java.lang.String`. Build
label text inside the `AddLabel` call or not at all. Where a ternary has one
numeric branch, force both to String: `if showValue then "" + Round(x, 2) else ""`.

Everything in "Where it will still differ from the Python" applies to the
columns too — in particular the **forming bar**. Columns update live, so an
`A: 1` at 11am is a bar that has not finished, and it can vanish by the close.
Trust it after 4:00 PM ET.

**Untested ground.** Running these conditions at 2D and 3D is new. The Python
only ever computes them on daily bars, so every measurement behind this
project — the A++ vs A split, chop40, the closed-trade record — is daily-bar
evidence. A `3D` cell is a wide net for eyeballing, not a validated signal.

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
