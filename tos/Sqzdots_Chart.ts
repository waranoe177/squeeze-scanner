# Sqzdots — chart study (daily conditions)
# Charts > Studies > Edit Studies > Create > paste > apply to a DAILY chart.
#
# Plots an arrow when the 6 DAILY conditions align, and a condition counter
# so you can see how close a name is to firing.
#
# READ THIS: the 7th condition (weekly Moxie) is NOT evaluated here, on
# purpose. On a daily chart, ExpAverage(close(period = AggregationPeriod.WEEK), 12)
# computes a 12-DAY average of a weekly step series -- it is not a weekly EMA
# and it does not match scanner/indicators.py. Rather than ship a number that
# looks right and is not, this study stops at 6 and labels the Moxie gate as
# unconfirmed. Confirm it on a weekly chart, or use the scan (which evaluates
# Moxie at W aggregation where it is exact). See tos/README.md.

declare lower;

# ---- EMA / SMA stack -------------------------------------------------------
def ema8   = ExpAverage(close, 8);
def ema21  = ExpAverage(close, 21);
def ema34  = ExpAverage(close, 34);
def sma50  = Average(close, 50);
def sma200 = Average(close, 200);

def bullStack = ema8 > ema21 and ema21 > ema34 and sma50 > sma200;
def bearStack = ema8 < ema21 and ema21 < ema34 and sma50 < sma200;

# ---- RSI (ThinkScript reverse formula, Wilders) ----------------------------
def netChg   = MovingAverage(AverageType.WILDERS, close - close[1], 14);
def totChg   = MovingAverage(AverageType.WILDERS, AbsValue(close - close[1]), 14);
def chgRatio = if totChg != 0 then netChg / totChg else 0;
def rsiVal   = 50 * (chgRatio + 1);

# ---- PPO 10/20 -------------------------------------------------------------
def ppoSlow = ExpAverage(close, 20);
def ppoVal  = (ExpAverage(close, 10) - ppoSlow) / ppoSlow * 100;

# ---- MACD 12/26/34 (signal 34, not 9) --------------------------------------
def macdLine = ExpAverage(close, 12) - ExpAverage(close, 26);
def macdSig  = ExpAverage(macdLine, 34);
def macdDiff = macdLine - macdSig;

def macdGreen        = macdDiff >= 0 and macdDiff > macdDiff[1];
def macdRisingBelow  = macdDiff <  0 and macdDiff > macdDiff[1];
def macdRed          = macdDiff <= 0 and macdDiff < macdDiff[1];
def macdFallingAbove = macdDiff >  0 and macdDiff < macdDiff[1];

# ---- TTM squeeze, aggressive (BB 20/2.0 inside KC 20/2.0) ------------------
def len    = 20;
def basis  = Average(close, len);
def dev    = StDev(close, len);
def kcBand = Average(TrueRange(high, close, low), len);
def sqzOn  = (basis + 2.0 * dev) < (basis + 2.0 * kcBand)
         and (basis - 2.0 * dev) > (basis - 2.0 * kcBand);

# ---- Condition counter (6 daily conditions) --------------------------------
def litBull = sqzOn
            + (rsiVal > 50)
            + (ppoVal >= 0)
            + (ema8 > ema21)
            + bullStack
            + (macdGreen or macdRisingBelow);

def litBear = sqzOn
            + (rsiVal < 50)
            + (ppoVal < 0)
            + (ema8 < ema21)
            + bearStack
            + (macdRed or macdFallingAbove);

plot Lit = Max(litBull, litBear);
Lit.SetPaintingStrategy(PaintingStrategy.HISTOGRAM);
Lit.SetLineWeight(3);
Lit.AssignValueColor(
    if litBull == 6 then Color.GREEN
    else if litBear == 6 then Color.RED
    else if litBull >= litBear then Color.DARK_GREEN
    else Color.DARK_RED);

plot Six = 6;
Six.SetDefaultColor(Color.GRAY);
Six.SetStyle(Curve.SHORT_DASH);

# ---- Fire markers ----------------------------------------------------------
def bullBase = sqzOn and rsiVal > 50 and ppoVal >= 0 and ema8 > ema21 and bullStack;
def bearBase = sqzOn and rsiVal < 50 and ppoVal <  0 and ema8 < ema21 and bearStack;

plot BuyAPP = if bullBase and macdGreen        then 0 else Double.NaN;
plot BuyA   = if bullBase and macdRisingBelow  then 0 else Double.NaN;
plot SellAPP= if bearBase and macdRed          then 0 else Double.NaN;
plot SellA  = if bearBase and macdFallingAbove then 0 else Double.NaN;

BuyAPP.SetPaintingStrategy(PaintingStrategy.POINTS);
BuyAPP.SetDefaultColor(Color.GREEN);
BuyAPP.SetLineWeight(5);

BuyA.SetPaintingStrategy(PaintingStrategy.POINTS);
BuyA.SetDefaultColor(Color.WHITE);
BuyA.SetLineWeight(5);

SellAPP.SetPaintingStrategy(PaintingStrategy.POINTS);
SellAPP.SetDefaultColor(Color.RED);
SellAPP.SetLineWeight(5);

SellA.SetPaintingStrategy(PaintingStrategy.POINTS);
SellA.SetDefaultColor(Color.LIGHT_GRAY);
SellA.SetLineWeight(5);

# ---- Label: 6/6 daily, Moxie unconfirmed -----------------------------------
AddLabel(yes,
    (if litBull >= litBear then "BULL " + litBull else "BEAR " + litBear) + "/6 daily"
    + "  |  MACD " + Round(macdDiff, 3)
    + "  |  Moxie: check weekly",
    if litBull == 6 then Color.GREEN
    else if litBear == 6 then Color.RED
    else Color.GRAY);
