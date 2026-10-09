# Multi-timeframe table notification — design

**Status:** design approved in conversation 2026-10-09, section by section. This file awaits the
owner's review before the implementation plan is written.
**Repo:** Sqzdots (`squeeze-scanner`), branch `feat/mtf-table`. Nothing is pushed without the
owner's word.

## 1. Intent

The owner runs the TOS watchlist column `tos/Sqzdots_Col_V5_RevGate_compact.ts` at four
aggregations (WK, 3D, 2D, 1D) and sorts it to spot multi-timeframe movers. This feature sends the
same view to Telegram every evening, **after** the daily individual charts, so the owner sees it
without opening TOS.

**Done means:** on one evening, every cell of the table matches the owner's TOS watchlist for the
same tickers (§9).

This is a watchlist tool. It makes no claim that multi-timeframe alignment is an edge; the
mechanical scanner signal was found to have none on its own (2026-09-16/17).

## 2. The signal, per timeframe

Exactly the TOS column with its default inputs (`rsiMode = 0`, `useRevGate = yes`,
`showSell = yes`, `maxRun = 0`):

- **BUY** when all three hold on the bar:
  - `rsi_exp(close, 14) > 66` (thinkScript RSI with `AverageType.EXPONENTIAL`);
  - MACD 12/26/34 histogram `>= 0` and rising (`diff > diff[1]`);
  - `RevEngRSI < EMA21` (REV RSI green).
- **SELL**, the mirror:
  - `rsi_exp < 34`;
  - histogram `<= 0` and falling;
  - `RevEngRSI > EMA21`.
- **Run count:** the number of consecutive bars the signal has held, ending at the latest bar. It
  is 1 on the bar where a run starts, and 0 means no signal. Buy and sell cannot both hold on one
  bar (RSI cannot be above 66 and below 34).
- **Squeeze marker `*`:** shown when `indicators.squeeze_on(close, high, low)` (20, BB 2.0, KC 2.0)
  is true on the latest bar. It is a marker only and never filters.
- **Code:**
  - `rsi_exp` is ported from `sqz-autotrader/autotrader/indicators.py` (tested there).
  - `macd_diff`, `rev_eng_rsi`, `ema` and `squeeze_on` are the scanner's existing functions.
  - The buy/sell rule is the same as `sqz-autotrader/autotrader/signal.py::decide`.
- **Insufficient data:** if any input is NaN on a bar, that bar has no signal. A timeframe with
  too little history shows `—`.

## 3. Bars

**One download:** `fetch_daily(symbols, period="5y", adjust=False)`.
- It covers the scanner's universe: `universe.csv` (151 stocks) plus `futures.csv` (8 futures).
- Prices are **unadjusted**, as TOS shows them; the daily scan keeps its adjusted prices,
  unchanged.
- Five years gives about 260 weekly bars, enough warm-up for the 34-bar MACD signal line.

**The TOS aggregation rule.** It was confirmed 2026-10-09 against the owner's SPY 2D and 3D
charts: all 20 bar-start dates on each chart matched, and so did the O/H/L/C, EMA21, SMA50 and
SMA200 to the cent.

- An N-day bar is **every N weekdays (Mon–Fri) counted from Monday 1970-01-05**. Exchange holidays
  **count as days**, so a bar that spans a holiday has fewer trading days. For example, the 2D bar
  starting Fri 2026-09-04 contains only that Friday, because Mon 09-07 was Labor Day.
- Group index = `numpy.busday_count("1970-01-05", day) // N`. Each bar is dated by its **first**
  day, as TOS dates it.
- The timeframes are 1D = the daily bars, **2D** = N 2, **3D** = N 3, and **WK** = N 5, which is
  exactly the Mon–Fri calendar week.
- **The current, unfinished bar is included**, as in the TOS columns. On Fri 2026-10-09 the
  current 3D bar holds Thu 10-08 and Fri 10-09, and it will also take in Mon 10-12.

This replaces `chart.agg_multiday` and its `MTF_ANCHOR = 2026-09-16`, which counts trading days
and does not match TOS. `agg_multiday` keeps its name and signature, apart from the anchor
argument, which is removed. It is re-implemented with this rule, so `render_mtf_composite` is
corrected too, and its existing test is rewritten to the new rule.

## 4. Rows and order

- **A ticker gets a row** when **at least two** of WK/3D/2D/1D show a signal in the **same
  direction**.
- All four cells are shown, including any that point the other way. A row with 2+ buys and 2+
  sells counts as *mixed*.
- **Order: like the TOS watchlist sorted by the WK column.**
  1. Rows with a WK cell come first: WK buys from the shortest run up (B: 1, B: 2, …), then WK
     sells the same way.
  2. Rows without a WK cell follow, ordered the same way by their 3D cell, then by 2D, then by 1D.
  3. Remaining ties are ordered by symbol.
  4. The sort key is the first non-empty cell in WK, 3D, 2D, 1D order: (timeframe rank,
     buy-before-sell, run length, symbol).
- **Display names:** futures show TOS-style, e.g. `ES=F` → `/ES`.

## 5. The image

A matplotlib-rendered PNG with a dark theme like the TOS watchlist, drawn as a real grid.

- **Columns:** `Symbol | WK | 3D | 2D | 1D`.
- **Cells:**
  - `* B: 1`, `B: 7`, `S: 2` or `—`;
  - green text for buy, red for sell, gray for `—`;
  - a gray background when the run is 1 (the signal started this bar).
- **Dividers:** thin dark lines between all rows and columns. A heavier divider wherever the
  sort's leading group changes (for example from WK buys to WK sells).
- **Header line:**
  `Sqzdots MTF · v5+REV · Fri 2026-10-09 · 23 tickers (14 buy · 9 sell)`. A `· m mixed` part is
  added only when m > 0.
- **Footer line:** `* = squeeze · B/S: n = bars the signal has held · gray = started this bar`.
- **Splitting:** more than 40 rows splits into several images of up to 40 rows each, marked
  `1/2`, `2/2`.

## 6. Delivery

- **Where it runs:** inside the existing daily run (`scanner/run.py`, GitHub Actions
  `scan.yml`, 19:00 ET on weekdays). There is no new schedule.
- **Order in every chat:** the individual charts, then the summary, then the **table**. This holds
  for the primary chat (`TELEGRAM_CHAT_ID`) and every extra chat the run already sends to
  (`TELEGRAM_ALERT_CHAT_IDS` + `TELEGRAM_OWNER_IDS`).
- **Caption:** `Multi-timeframe: 23 tickers on 2+ timeframes`. With several images, the caption
  goes on the first.
- **Quiet day:** a text message `No ticker has a signal on 2+ timeframes today.` instead of an
  image.
- **Record:** the run also writes `out/mtf_table.png` (plus `out/mtf_table_2.png` and so on when
  split) and `out/mtf_table.json`, which holds the rows with their four cells, the date and the
  counts. They are committed with the day's other `out/` files.
  - Note: this repo is **public**, so these files are public, like the existing daily results and
    charts.

## 7. When things go wrong

- **Isolation:** any failure in the table step is caught (download, computation, rendering or
  sending), and it never affects the charts, the summary or the commit of the day's results.
- **On failure:**
  - the run logs the error;
  - it sends `⚠️ MTF table failed: <reason>` to the primary chat only;
  - its exit status is unchanged.
- **A ticker missing from the download** is left out of the table, as the daily scan leaves it
  out.
- **A timeframe too short to compute** shows `—` for that ticker.

## 8. Build shape

| Unit | New or changed | Job |
|---|---|---|
| `scanner/indicators.py` | changed | Add `rsi_exp`. |
| `scanner/chart.py` | changed | `agg_multiday` re-implemented with the TOS weekday rule (§3); `MTF_ANCHOR` removed. |
| `scanner/mtf.py` | new | Per-symbol, per-timeframe cells (direction, run, squeeze); the row filter and sort (§2–§4); JSON record. Pure functions. |
| `scanner/mtf_render.py` | new | The table image(s) (§5). |
| `scanner/run.py` | changed | Fetch, compute, render and send after the summary, with failure isolation (§6–§7). |

**Testing** (on fixed, stored price data; the existing suite stays green):
- **`rsi_exp`:** against hand-computed values.
- **The buy/sell rule:** the same verdicts as `sqz-autotrader`'s `decide` on shared cases.
- **Run counts:** a run starts at 1, grows, and resets after a break.
- **Aggregation:**
  - the SPY 2D and 3D bar-start dates from the owner's screenshots (fixture of unadjusted SPY
    daily bars);
  - the Labor Day single-day bar;
  - WK = Mon–Fri;
  - the unfinished last bar is kept.
- **Rows:** the 2+-same-direction filter, the mixed case, and the sort order.
- **Image:**
  - a file is created;
  - more than 40 rows splits;
  - the run-1 cells get a gray background.
- **Delivery:**
  - the order is charts, summary, then table, in every chat;
  - a quiet day sends the text line;
  - an injected failure leaves the charts, the summary and the exit status unchanged, and sends
    the ⚠️ line to the primary chat.

## 9. Owner tasks

1. **Parity check, one evening after deploy.** Compare the table cell by cell with the TOS
   watchlist (WK, 3D, 2D, 1D). The feature is done only when all four columns match. A mismatch
   in one column is fixed before the feature counts as done.
2. **Futures caveat.** The scanner's futures are Yahoo continuous contracts (`ES=F`). TOS shows
   the front month (`/ES[Z26]`). Small differences around a roll date are expected and are not a
   parity failure.

## 10. Not in scope

- A bot command to get the table on demand. It is parked in `WISHLIST.md`.
- /MES and /MNQ (/ES and /NQ stand in; same price).
- Intraday timeframes.
- Any change to the existing daily signal (A/A++), its charts or its adjusted prices.
- Backtesting multi-timeframe alignment.
