# tests/test_access.py
from scanner import access


def test_parse_allowlist_handles_blanks_and_whitespace():
    assert access.parse_allowlist(" 111, 222 ,, 333 ") == {"111", "222", "333"}
    assert access.parse_allowlist("") == set()
    assert access.parse_allowlist(None) == set()


def test_is_owner_string_compares():
    assert access.is_owner(111, "111") is True     # int vs str
    assert access.is_owner("222", "111") is False
    assert access.is_owner("111", None) is False    # no owner configured


def test_is_owner_accepts_a_set_of_owners():
    owners = {"111", "444"}                          # two owner devices
    assert access.is_owner("111", owners) is True
    assert access.is_owner(444, owners) is True       # int vs str
    assert access.is_owner("999", owners) is False
    assert access.is_owner("111", set()) is False     # empty -> no owner


def test_owner_set_merges_primary_and_extras():
    assert access.owner_set("111", "444, 555") == {"111", "444", "555"}
    assert access.owner_set("111", None) == {"111"}
    assert access.owner_set("111", "") == {"111"}
    assert access.owner_set(None, "444") == {"444"}   # extras only
    assert access.owner_set(None, None) == set()


def test_is_allowed_owner_and_listed_and_unlisted():
    allow = {"222", "333"}
    assert access.is_allowed("111", "111", allow) is True   # owner
    assert access.is_allowed("222", "111", allow) is True   # listed
    assert access.is_allowed(333, "111", allow) is True     # listed, int in
    assert access.is_allowed("999", "111", allow) is False  # unlisted


def test_is_allowed_accepts_owner_set():
    # a second owner (in the owner set) is allowed even if not in the allowlist
    assert access.is_allowed("444", {"111", "444"}, set()) is True
    assert access.is_allowed("999", {"111", "444"}, set()) is False


def test_rate_limiter_under_and_over_cap_with_injected_clock():
    rl = access.RateLimiter(limit=2, window_seconds=100)
    assert rl.allow("u", now=0.0) is True
    assert rl.allow("u", now=1.0) is True
    assert rl.allow("u", now=2.0) is False          # 3rd within window -> blocked
    # a different user is independent
    assert rl.allow("v", now=2.0) is True


def test_rate_limiter_prunes_old_hits_outside_window():
    rl = access.RateLimiter(limit=1, window_seconds=100)
    assert rl.allow("u", now=0.0) is True
    assert rl.allow("u", now=50.0) is False          # still inside window
    assert rl.allow("u", now=101.0) is True          # first hit aged out
