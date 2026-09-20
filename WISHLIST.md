# Sqzdots — Wishlist & Notes

A living list of where this tool is headed and what to refine. Jot things down here
as you use it daily — features, bugs, and (most valuable of all) which setups actually
work in practice.

**North star:** turn Sqzdots into a tool that supports **consistent weekly cash flow**.

**Guiding principle (the honest one):** the *plumbing* is buildable anytime; what turns
this into cash flow is a **validated edge**. So far the mechanical signal has **not**
beaten baselines in testing — it's a watchlist / timing-aid / educational tool, not a
proven money-maker. Refine the setup and method first; automate money only once there's
a real, out-of-sample edge.

---

## Wishlist

### 1. Real-time data → "day-trader mode"
Intraday futures feed so day-trading is possible when I choose to.
- **Status:** not started
- **How:** swap the yfinance layer for IBKR (~$10–40/mo non-pro, `ib_insync`) or Databento;
  add intraday bars + always-on intraday polling.
- **Risk level:** low — a better feed just makes me a sharper discretionary trader; no money
  risked by the tooling itself. Safe to build anytime.

### 2. AI autotrade
After `trade SYM` + confirming the instructions, auto-submit the order to a brokerage.
- **Status:** ON HOLD — deliberately, until there's a proven edge.
- **How (when ready):** broker API (Tradovate / IBKR / Tradestation for futures; Alpaca for
  equities).
- **Gate (do NOT skip):** validated out-of-sample edge → paper-trade the full loop → tiny live
  size → hard risk controls (max daily loss, position caps, kill switch). Automating orders on
  a no-edge signal just automates the losses, fast, with real money and no undo.

### 3. Trade workstation webapp
A screen showing tickers in action + "bots in various roles" to consult, drill into the trade
thesis, and execute.
- **Status:** not started (Streamlit dashboard skeleton exists as a starting point).
- **How:** agent-orchestration vision — signal agent + qualitative/eval agents → thesis + chart
  → decision.
- **Risk level:** low — a better cockpit sharpens judgment; safe to build anytime.

---

## Refinement log (append as I go)

### Setups that actually work (real-world observations)
_The most important notes — which signals pay off in practice. This is what could eventually
unlock the edge and item #2._
- …

### Feature ideas
- …

### Bugs / parity issues (app chart vs TOS, etc.)
- …

---

_Maintained by the user; Claude reads this when working on Sqzdots. Last structured update: 2026-09-20._
