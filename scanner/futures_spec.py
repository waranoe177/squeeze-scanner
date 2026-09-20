"""Contract specs for the futures the scanner watches.

`mult` is the point value: dollars per 1.00 move in the quoted price (so risk =
points × mult). `micro`/`micro_mult` are the smaller sibling contract used to
scale risk down. `margin_pct` is a BALLPARK initial margin as a fraction of
notional — CME/broker margins change often and vary by broker, so it is only
ever surfaced as an estimate, never a quote. Keyed by the yfinance `=F` root.
"""

FUTURES = {
    "ES=F":  {"name": "E-mini S&P 500",      "mult": 50,   "micro": "MES=F", "micro_mult": 5,    "margin_pct": 0.04},
    "NQ=F":  {"name": "E-mini Nasdaq 100",   "mult": 20,   "micro": "MNQ=F", "micro_mult": 2,    "margin_pct": 0.045},
    "YM=F":  {"name": "E-mini Dow",          "mult": 5,    "micro": "MYM=F", "micro_mult": 0.5,  "margin_pct": 0.04},
    "RTY=F": {"name": "E-mini Russell 2000", "mult": 50,   "micro": "M2K=F", "micro_mult": 5,    "margin_pct": 0.05},
    "GC=F":  {"name": "Gold",                "mult": 100,  "micro": "MGC=F", "micro_mult": 10,   "margin_pct": 0.035},
    "SI=F":  {"name": "Silver",              "mult": 5000, "micro": "SIL=F", "micro_mult": 1000, "margin_pct": 0.05},
    "CL=F":  {"name": "Crude Oil (WTI)",     "mult": 1000, "micro": "MCL=F", "micro_mult": 100,  "margin_pct": 0.07},
    "BTC=F": {"name": "Bitcoin",             "mult": 5,    "micro": "MBT=F", "micro_mult": 0.5,  "margin_pct": 0.35},
}


def spec(symbol):
    """Return the contract spec for a futures symbol (case-insensitive), or None
    if it isn't a tracked future (e.g. an equity like AAPL)."""
    if not symbol:
        return None
    return FUTURES.get(symbol.upper())


def is_future(symbol) -> bool:
    return spec(symbol) is not None
