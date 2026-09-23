# Sqzdots — daily conditions (6 of 7)
# Stock Hacker > Add filter > Study > Custom > thinkScript editor
# SET THIS FILTER'S AGGREGATION TO: D
#
# Mirrors scanner/signals.py::confluence + scanner/indicators.py.
# The 7th condition (weekly Moxie) is a SEPARATE filter at W aggregation --
# see Sqzdots_Scan_MoxieWeekly.ts. thinkScript cannot compute a weekly EMA
# correctly from inside a daily study (see tos/README.md).
#
# input direction: 1 = bull, -1 = bear
# input grade:     1 = A++ (MACD already green), 0 = A (MACD early), -1 = either

input direction = 1;
input grade = -1;

# ---- EMA / SMA stack -------------------------------------------------------
def ema8   = ExpAverage(close, 8);
def ema21  = ExpAverage(close, 21);
def ema34  = ExpAverage(close, 34);
def sma50  = Average(close, 50);
def sma200 = Average(close, 200);

def bullStack = ema8 > ema21 and ema21 > ema34 and sma50 > sma200;
def bearStack = ema8 < ema21 and ema21 < ema34 and sma50 < sma200;

# ---- RSI (ThinkScript reverse formula, Wilders) ----------------------------
# Written out rather than calling RSI() so it provably matches indicators.rsi().
def netChg   = MovingAverage(AverageType.WILDERS, close - close[1], 14);
def totChg   = MovingAverage(AverageType.WILDERS, AbsValue(close - close[1]), 14);
def chgRatio = if totChg != 0 then netChg / totChg else 0;
def rsiVal   = 50 * (chgRatio + 1);

# ---- PPO 10/20 -------------------------------------------------------------
def ppoSlow = ExpAverage(close, 20);
def ppoVal  = (ExpAverage(close, 10) - ppoSlow) / ppoSlow * 100;

# ---- MACD 12/26/34 ---------------------------------------------------------
# NOTE the signal length: 34, not the usual 9. That is what B3 uses.
def macdLine = ExpAverage(close, 12) - ExpAverage(close, 26);
def macdSig  = ExpAverage(macdLine, 34);
def macdDiff = macdLine - macdSig;

def macdGreen        = macdDiff >= 0 and macdDiff >  macdDiff[1];   # A++ buy
def macdRisingBelow  = macdDiff <  0 and macdDiff >  macdDiff[1];   # A   buy
def macdRed          = macdDiff <= 0 and macdDiff <  macdDiff[1];   # A++ sell
def macdFallingAbove = macdDiff >  0 and macdDiff <  macdDiff[1];   # A   sell

# ---- TTM squeeze, aggressive (BB 20/2.0 inside KC 20/2.0) ------------------
# TrueRange argument order in thinkScript is (high, close, low). Not a typo.
def len     = 20;
def basis   = Average(close, len);
def dev     = StDev(close, len);
def kcBand  = Average(TrueRange(high, close, low), len);
def sqzOn   = (basis + 2.0 * dev) < (basis + 2.0 * kcBand)
          and (basis - 2.0 * dev) > (basis - 2.0 * kcBand);

# ---- Confluence ------------------------------------------------------------
def bullBase = sqzOn and rsiVal > 50 and ppoVal >= 0 and ema8 > ema21 and bullStack;
def bearBase = sqzOn and rsiVal < 50 and ppoVal <  0 and ema8 < ema21 and bearStack;

def bullMacd = if grade == 1 then macdGreen
               else if grade == 0 then macdRisingBelow
               else macdGreen or macdRisingBelow;
def bearMacd = if grade == 1 then macdRed
               else if grade == 0 then macdFallingAbove
               else macdRed or macdFallingAbove;

plot scan = if direction > 0 then bullBase and bullMacd else bearBase and bearMacd;
