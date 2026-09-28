# Sqzdots -- watchlist column v3, daily conditions (6 of 7)
# Watchlist > right-click any column header > Customize > scroll to "Custom"
# > pencil icon > thinkScript editor. SET THE COLUMN'S AGGREGATION.
#
# v3 = v2 with one fix: cells with no signal are now BLANK instead of "NaN".
#   Cause: AddLabel(show, ...) adds no label at all when show is false, and
#   with no label TOS falls back to rendering the plot value -- which is
#   Double.NaN. The label has to be always-visible and carry an empty string.
#   Same fix applied to Sqzdots_Col_MoxieW.ts.
# v2 = v1 with the run counter. v1 counted bars SINCE a past fire; v2 and v3
#   count CONSECUTIVE bars the setup has held, so the number reads the way the
#   B3_Sqz columns do:
#
#     A: 1      the setup formed on THIS bar        <- the trigger
#     A: 4      it has now held for four bars
#     A++: 2    held two bars AND MACD is green right now
#     .: 7      structure has held seven bars, MACD not confirming yet
#     (blank)   structure is not in place
#
# For A++ only -- the look/decide/execute tier -- set showTier = 1.
#
# WHAT THE NUMBER COUNTS -- and why it is not the literal 6/6.
# Measured over the 157-name universe, 2 years of daily bars:
#
#       run base                    runs  median  max  %len=1  %>=15
#       5 structural (no MACD)      1431       3   33   21.5%   4.5%
#       full 6/6 incl. macd_green    788       2    9   44.3%   0.0%
#
# macd_green is `diff >= 0 AND diff > diff[1]`. That second half is a
# bar-to-bar slope test, so the full 6/6 shatters into 1s and 2s and can
# never reach a number like 15. The five STRUCTURAL conditions -- squeeze,
# RSI, PPO, ema8>ema21, full stack -- are the ones that persist, so the run
# counts those, and MACD sets the LETTER on top of the current bar.
#
# Set runBase = 1 to count the literal 6/6 anyway. It is honest, it just
# tops out at 9.
#
# Still 6 of 7: the weekly Moxie gate is a separate column at W aggregation
# (Sqzdots_Col_MoxieW.ts). See tos/README.md.
#
# DAILY ONLY. Measured on 5m and 15m bars this setup underperforms the
# unconditional baseline at every horizon tested. See tos/README.md.

input runBase   = 0;    # 0 = 5 structural conditions   1 = literal 6/6
input showTier  = -1;   # -1 = A++, A and waiting   1 = A++ only   0 = A++ and A
input maxRun    = 0;    # 0 = no cap; else blank the cell past this many bars

# ---- EMA / SMA stack -------------------------------------------------------
def ema8   = ExpAverage(close, 8);
def ema21  = ExpAverage(close, 21);
def ema34  = ExpAverage(close, 34);
def sma50  = Average(close, 50);
def sma200 = Average(close, 200);

def bullStack = ema8 > ema21 and ema21 > ema34 and sma50 > sma200;
def bearStack = ema8 < ema21 and ema21 < ema34 and sma50 < sma200;

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

def macdGreen        = macdDiff >= 0 and macdDiff >  macdDiff[1];   # A++ buy
def macdRisingBelow  = macdDiff <  0 and macdDiff >  macdDiff[1];   # A   buy
def macdRed          = macdDiff <= 0 and macdDiff <  macdDiff[1];   # A++ sell
def macdFallingAbove = macdDiff >  0 and macdDiff <  macdDiff[1];   # A   sell

# ---- TTM squeeze, aggressive (BB 20/2.0 inside KC 20/2.0) ------------------
# TrueRange argument order in thinkScript is (high, close, low). Not a typo.
def len    = 20;
def basis  = Average(close, len);
def dev    = StDev(close, len);
def kcBand = Average(TrueRange(high, close, low), len);
def sqzOn  = (basis + 2.0 * dev) < (basis + 2.0 * kcBand)
         and (basis - 2.0 * dev) > (basis - 2.0 * kcBand);

# ---- The persistent structure (5 conditions) -------------------------------
def bullStruct = sqzOn and rsiVal > 50 and ppoVal >= 0 and ema8 > ema21 and bullStack;
def bearStruct = sqzOn and rsiVal < 50 and ppoVal <  0 and ema8 < ema21 and bearStack;

# ---- Tier on the CURRENT bar -----------------------------------------------
# 2 = A++ (MACD confirming), 1 = A (MACD early), 0 = structure only, waiting.
def bullTier = if !bullStruct then 0
          else if macdGreen then 2
          else if macdRisingBelow then 1
          else 0;
def bearTier = if !bearStruct then 0
          else if macdRed then 2
          else if macdFallingAbove then 1
          else 0;

# ---- What the run counts ---------------------------------------------------
def bullOn = if runBase == 1 then bullTier > 0 else bullStruct;
def bearOn = if runBase == 1 then bearTier > 0 else bearStruct;

# Consecutive bars the condition has been true. 1 on the FIRST bar of a run,
# 0 when it is not true. CompoundValue seeds the series cleanly so the very
# first bar of history cannot poison it with NaN.
def runBull = CompoundValue(1, if bullOn then runBull[1] + 1 else 0,
                               if bullOn then 1 else 0);
def runBear = CompoundValue(1, if bearOn then runBear[1] + 1 else 0,
                               if bearOn then 1 else 0);

# bullStruct and bearStruct are mutually exclusive (ema8>ema21 vs ema8<ema21),
# so at most one run is live on any bar.
def isBull = runBull > 0;
def n      = if isBull then runBull else runBear;
def tier   = if isBull then bullTier else bearTier;

def tierOk = if showTier ==  1 then tier == 2
        else if showTier ==  0 then tier >= 1
        else yes;

def show = n > 0 and tierOk and (maxRun == 0 or n <= maxRun);

# ---- Output ----------------------------------------------------------------
# Sort key: A++ above A above waiting, then by run length. Bulls positive,
# bears negative, so one click groups the sides. Never displayed -- the label
# below always wins -- and NaN keeps the blank rows grouped at one end.
plot Rank = if !show then Double.NaN
            else (if isBull then 1 else -1) * (tier * 1000 + n);

# The label is ALWAYS visible and carries an empty string when there is no
# signal. Hiding the label instead makes TOS render the plot value, and the
# cell reads "NaN". This is the v3 fix.
AddLabel(yes,
         if !show then ""
         else (if tier == 2 then "A++: " else if tier == 1 then "A: " else ". ")
              + Round(n, 0),
         if tier == 0 then Color.GRAY
         else if isBull then Color.GREEN
         else Color.RED);

# Highlight the trigger bar -- the one where the run just started.
AssignBackgroundColor(if show and n == 1 then Color.DARK_GRAY else Color.CURRENT);
