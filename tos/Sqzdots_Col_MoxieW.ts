# Sqzdots -- watchlist column, the weekly Moxie gate (condition 7 of 7)
# SET THIS COLUMN'S AGGREGATION TO: W
#
# Name it WK_Moxie and park it next to the Sqzdots_Col_Daily columns. A daily
# 'A++' cell is only a real signal when this cell agrees in the same direction.
#
# Cell reads:
#     UP 0.42     weekly Moxie above zero and rising  -> bull gate open
#     DN -0.31    below zero and falling              -> bear gate open
#     * UP 0.08   the * means Moxie crossed zero THIS week (the B-tier trigger)
#     0.05        neither gate open; the number is there so you can see how close
#
# Caveat, same as the scan: this reads the WEEK-TO-DATE bar. A Monday reading
# is provisional and can flip by Friday. That is what TOS does natively and
# what the Python mirrors on purpose; it is not a bug in either.

input showValue = yes;

# Moxie: MACD 12/26/9 histogram, scaled x3, computed on WEEKLY bars.
# Correct here only because the COLUMN is set to W aggregation -- `close` is
# the weekly close. Do not paste this into a daily-aggregation column.
def vc1   = ExpAverage(close, 12) - ExpAverage(close, 26);
def va1   = ExpAverage(vc1, 9);
def moxie = (vc1 - va1) * 3;

def moxieUp = moxie > 0 and moxie >= moxie[1];
def moxieDn = moxie < 0 and moxie <= moxie[1];

def crossUp = moxie >= 0 and moxie[1] < 0;
def crossDn = moxie <= 0 and moxie[1] > 0;
def crossed = crossUp or crossDn;

plot Rank = if IsNaN(moxie) then Double.NaN else moxie;

# The cell text is built INLINE, on purpose. thinkScript's `def` holds doubles
# only -- there is no string variable -- so `def txt = "UP " + ...` fails with
# "Expected double", and every later use of it then mismatches as
# "double vs java.lang.String". Strings live inside AddLabel or nowhere.
#
# Always visible, empty string when there is nothing to say. If the label is
# hidden instead, TOS falls back to rendering the plot value and the cell
# reads "NaN".
#
# `"" + Round(...)` forces that branch to a String so both sides of the
# showValue ternary have the same type.
AddLabel(yes,
         if IsNaN(moxie) then ""
         else (if crossed then "* " else "")
            + (if moxieUp then "UP " else if moxieDn then "DN " else "")
            + (if showValue then "" + Round(moxie, 2) else ""),
         if moxieUp then Color.GREEN
         else if moxieDn then Color.RED
         else Color.GRAY);

AssignBackgroundColor(if crossed then Color.DARK_GRAY else Color.CURRENT);
