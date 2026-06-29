# Parity Validation Results — 2026-06-29

Validated the engine against TOS using 5 ground-truth cases on the **6/26 bar**
(anchored by matching the close price to the penny).

## Signal definition (your actual process, not the strict B3 top dot)

**BUY** when ALL of: squeeze ON · RSI > 50 · PPO ≥ 0 · EMA8 > EMA21 ·
full stack (8>21>34 and 50>200) · MACD green (Diff ≥ 0 and rising) ·
Moxie > 0 and green (rising, weekly). **SELL** = strict mirror.

## Outcome: 5/5 match your rules

| Ticker | Engine | TOS (you) | Note |
|--------|--------|-----------|------|
| RSP | none | none | MACD not green; Moxie +2.37 but falling |
| DIA | none | none | MACD positive but fading (not green) |
| IYT | none | none | Moxie +1.39 but falling — your "moxie red" |
| XLRE | none | none | Moxie +0.30 but falling |
| QQQ | none | (you read bear) | Not a clean short: Moxie still +27.6 (above zero). Discretionary call, not a mechanical signal under the strict-mirror rule. |

## Bugs found and fixed

1. **Forming-bar bug:** yfinance appends an incomplete current-day bar (tiny
   volume). The engine was evaluating it instead of the last completed session.
   Fixed: `data.drop_forming_bar` (default on).
2. **Data source confirmed good:** yfinance closes matched TOS to the penny on
   completed bars.

## Caveat / next validation step

All 5 cases resolved to **none** — this proves the engine doesn't *false-fire*
(the original bug), but it has not yet been checked against a confirmed **firing**
signal. Recommended: capture one ticker+date where your TOS setup genuinely
showed a cyan (buy) dot, add it to `cases.csv`, and extend `test_parity.py`.
