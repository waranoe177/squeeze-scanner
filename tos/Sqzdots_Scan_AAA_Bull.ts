# Sqzdots -- scan filter: BULL A++ only
# Stock Hacker > Add filter > Study > Custom > thinkScript editor
# SET THIS FILTER'S AGGREGATION TO: D        (or whatever the column uses)
# Condition: `scan is true`
#
# Paired file: Sqzdots_Scan_AAA_Bear.ts
#
# This is the exact A++ condition from Sqzdots_Col_Daily_v3.ts, hardcoded to
# the bull side with no inputs to get wrong. Set the filter's aggregation to
# match the column you are cross-checking (D for 1D_Sqz_AAA, W for
# WK_Sqz_AAA, and so on) and the hit list should equal the green `A++` cells
# in that column.
#
# A++ BULL = five structural conditions, plus MACD already confirming:
#     1  TTM squeeze ON        BB(20, 2.0) inside KC(20, 2.0)
#     2  RSI(14) > 50
#     3  PPO(10, 20) >= 0
#     4  ema8 > ema21
#     5  ema8 > ema21 > ema34  and  sma50 > sma200
#     6  MACD(12, 26, 34) diff >= 0  AND  rising      <- the A++ trigger
#
# The early `A` tier -- same 1-5 but MACD still BELOW zero and merely rising --
# is deliberately NOT included. That is the whole point of scanning A++.
#
# SIX OF SEVEN. The weekly Moxie gate is a separate filter at W aggregation:
# add Sqzdots_Scan_MoxieWeekly.ts with direction = 1 alongside this one and
# Stock Hacker ANDs them. Without it you are scanning the same 6/7 the column
# shows, which is the right thing for cross-checking and the wrong thing for
# taking a trade. See tos/README.md.
#
# Scan after 4:00 PM ET. TOS includes the forming bar; the Python drops it.

input maxRun = 0;   # 0 = off. Else require the structure run to be <= this
                    # many bars, i.e. only FRESH setups. Matches the number
                    # shown in the v3 column: maxRun = 1 is "formed today".

# ---- EMA / SMA stack -------------------------------------------------------
def ema8   = ExpAverage(close, 8);
def ema21  = ExpAverage(close, 21);
def ema34  = ExpAverage(close, 34);
def sma50  = Average(close, 50);
def sma200 = Average(close, 200);

def bullStack = ema8 > ema21 and ema21 > ema34 and sma50 > sma200;

# ---- RSI (reverse formula, Wilders) ----------------------------------------
def netChg   = MovingAverage(AverageType.WILDERS, close - close[1], 14);
def totChg   = MovingAverage(AverageType.WILDERS, AbsValue(close - close[1]), 14);
def chgRatio = if totChg != 0 then netChg / totChg else 0;
def rsiVal   = 50 * (chgRatio + 1);

# ---- PPO 10/20 -------------------------------------------------------------
def ppoSlow = ExpAverage(close, 20);
def ppoVal  = (ExpAverage(close, 10) - ppoSlow) / ppoSlow * 100;

# ---- MACD 12/26/34 ---------------------------------------------------------
# Signal length 34, not 9. See tos/README.md parity notes.
def macdLine = ExpAverage(close, 12) - ExpAverage(close, 26);
def macdSig  = ExpAverage(macdLine, 34);
def macdDiff = macdLine - macdSig;

def macdGreen = macdDiff >= 0 and macdDiff > macdDiff[1];

# ---- TTM squeeze, aggressive (BB 20/2.0 inside KC 20/2.0) ------------------
# TrueRange argument order in thinkScript is (high, close, low). Not a typo.
def len    = 20;
def basis  = Average(close, len);
def dev    = StDev(close, len);
def kcBand = Average(TrueRange(high, close, low), len);
def sqzOn  = (basis + 2.0 * dev) < (basis + 2.0 * kcBand)
         and (basis - 2.0 * dev) > (basis - 2.0 * kcBand);

# ---- The persistent structure (conditions 1-5) -----------------------------
def bullStruct = sqzOn and rsiVal > 50 and ppoVal >= 0 and ema8 > ema21 and bullStack;

# Consecutive bars the structure has held. 1 on the first bar of a run. Same
# counter as the v3 column, so maxRun here means the same thing the number
# means there.
def runBull = CompoundValue(1, if bullStruct then runBull[1] + 1 else 0,
                               if bullStruct then 1 else 0);

def freshOk = maxRun == 0 or (runBull > 0 and runBull <= maxRun);

plot scan = bullStruct and macdGreen and freshOk;
