# Sqzdots — weekly Moxie gate (the 7th condition)
# Stock Hacker > Add filter > Study > Custom > thinkScript editor
# SET THIS FILTER'S AGGREGATION TO: W        <-- this is the whole point
#
# Because the filter itself runs on weekly bars, `close` IS the weekly close
# and ExpAverage is a genuine weekly EMA. No secondary-aggregation tricks,
# nothing to repaint incorrectly.
#
# Mirrors scanner/indicators.py::moxie + the moxie_up / moxie_dn gate in
# scanner/signals.py::analyze.
#
# input direction: 1 = bull (Moxie above zero and rising), -1 = bear

input direction = 1;

# Watkins Moxie: (vc1 - EMA(vc1, 9)) * 3, vc1 = EMA(12) - EMA(26)
def vc1   = ExpAverage(close, 12) - ExpAverage(close, 26);
def va1   = ExpAverage(vc1, 9);
def moxie = (vc1 - va1) * 3;

# Above zero AND rising (>= on the slope, matching moxie_green in signals.py).
def moxieUp = moxie > 0 and moxie >= moxie[1];
def moxieDn = moxie < 0 and moxie <= moxie[1];

plot scan = if direction > 0 then moxieUp else moxieDn;
