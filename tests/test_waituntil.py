"""Tests for the ET-anchored wait used by the daily scan workflow.

The whole point of this module is that delivery time must NOT move when US
daylight saving flips, so the DST pair (same ET wall-clock, different UTC
offset) is the load-bearing test here. Everything is clock-injected: no test
sleeps or reads the real time.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from scanner import waituntil

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET)


# --- seconds_until ------------------------------------------------------

def test_one_hour_before_target_in_summer():
    # 2026-09-22 is EDT (UTC-4)
    assert waituntil.seconds_until("19:00", now=_et(2026, 9, 22, 18, 0)) == 3600.0


def test_one_hour_before_target_in_winter():
    # 2026-01-15 is EST (UTC-5) -- same ET wall clock must give the same wait
    assert waituntil.seconds_until("19:00", now=_et(2026, 1, 15, 18, 0)) == 3600.0


def test_dst_offsets_really_differ_but_wait_is_identical():
    """The DST guarantee: identical wait from different UTC instants."""
    summer = datetime(2026, 9, 22, 22, 0, tzinfo=timezone.utc)   # 18:00 EDT
    winter = datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)   # 18:00 EST
    assert summer.astimezone(ET).utcoffset() != winter.astimezone(ET).utcoffset()
    assert waituntil.seconds_until("19:00", now=summer) == 3600.0
    assert waituntil.seconds_until("19:00", now=winter) == 3600.0


def test_past_target_returns_zero_not_tomorrow():
    """Must NOT roll to tomorrow -- a late cron fire has to run immediately."""
    assert waituntil.seconds_until("19:00", now=_et(2026, 9, 22, 20, 12)) == 0.0


def test_exactly_at_target_returns_zero():
    assert waituntil.seconds_until("19:00", now=_et(2026, 9, 22, 19, 0)) == 0.0


def test_sub_minute_precision():
    assert waituntil.seconds_until("19:00", now=_et(2026, 9, 22, 18, 59, 30)) == 30.0


def test_accepts_naive_now_as_target_timezone():
    assert waituntil.seconds_until("19:00", now=datetime(2026, 9, 22, 18, 0)) == 3600.0


def test_bad_target_format_raises():
    for bad in ("19", "7pm", "25:00", "19:60", ""):
        with pytest.raises(ValueError):
            waituntil.seconds_until(bad, now=_et(2026, 9, 22, 18, 0))


def test_unknown_timezone_raises():
    with pytest.raises(ZoneInfoNotFoundError):
        waituntil.seconds_until("19:00", tz="Mars/Olympus", now=_et(2026, 9, 22, 18, 0))


# --- wait_until (chunked sleep + heartbeat) -----------------------------

class _FakeClock:
    """Advances only when the code under test 'sleeps'."""

    def __init__(self, start):
        self.now = start
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, secs):
        self.slept.append(secs)
        self.now += timedelta(seconds=secs)


def test_wait_until_sleeps_exactly_the_gap():
    clock = _FakeClock(_et(2026, 9, 22, 18, 0))
    total = waituntil.wait_until("19:00", now_fn=clock, sleep_fn=clock.sleep,
                                 log_fn=lambda _m: None, heartbeat=900.0)
    assert total == 3600.0
    assert sum(clock.slept) == 3600.0


def test_wait_until_chunks_sleep_by_heartbeat():
    clock = _FakeClock(_et(2026, 9, 22, 18, 0))
    waituntil.wait_until("19:00", now_fn=clock, sleep_fn=clock.sleep,
                         log_fn=lambda _m: None, heartbeat=900.0)
    assert clock.slept == [900.0, 900.0, 900.0, 900.0]


def test_wait_until_logs_a_heartbeat_per_chunk():
    clock = _FakeClock(_et(2026, 9, 22, 18, 0))
    lines: list[str] = []
    waituntil.wait_until("19:00", now_fn=clock, sleep_fn=clock.sleep,
                         log_fn=lines.append, heartbeat=900.0)
    assert len(lines) >= 4
    assert any("19:00" in ln for ln in lines)


def test_wait_until_past_target_does_not_sleep():
    clock = _FakeClock(_et(2026, 9, 22, 20, 12))
    total = waituntil.wait_until("19:00", now_fn=clock, sleep_fn=clock.sleep,
                                 log_fn=lambda _m: None)
    assert total == 0.0
    assert clock.slept == []


def test_wait_until_final_chunk_is_the_remainder():
    clock = _FakeClock(_et(2026, 9, 22, 18, 50))
    waituntil.wait_until("19:00", now_fn=clock, sleep_fn=clock.sleep,
                         log_fn=lambda _m: None, heartbeat=900.0)
    assert clock.slept == [600.0]


# --- CLI wiring ---------------------------------------------------------

def test_main_passes_parsed_args_to_wait_until(monkeypatch):
    """The workflow calls this module as a CLI, so the arg wiring is load-bearing.
    wait_until is stubbed only to keep the real clock out of the test."""
    seen = {}

    def _fake(target, tz=None, **kw):
        seen.update(target=target, tz=tz, heartbeat=kw.get("heartbeat"))
        return 0.0

    monkeypatch.setattr(waituntil, "wait_until", _fake)
    waituntil.main(["--at", "19:00", "--tz", "America/New_York", "--heartbeat", "60"])
    assert seen == {"target": "19:00", "tz": "America/New_York", "heartbeat": 60.0}


def test_main_defaults_to_eastern(monkeypatch):
    seen = {}
    monkeypatch.setattr(waituntil, "wait_until",
                        lambda target, tz=None, **kw: seen.update(tz=tz) or 0.0)
    waituntil.main(["--at", "19:00"])
    assert seen["tz"] == "America/New_York"


def test_main_rejects_a_bad_target():
    """argparse accepts any string, so validation must come from seconds_until.
    Unstubbed on purpose: it must raise before reaching any sleep."""
    with pytest.raises(ValueError):
        waituntil.main(["--at", "7pm"])


# --- max_wait safety cap -------------------------------------------------

def test_wait_until_refuses_an_absurd_gap_and_proceeds_now():
    """A workflow RE-RUN keeps event_name=schedule, so a re-run at 09:00 would
    compute a 10h sleep, blow past the CI job timeout, get CANCELLED -- and a
    cancelled job runs no `if: failure()` steps, so it would fail silently."""
    clock = _FakeClock(_et(2026, 9, 22, 9, 0))
    lines: list[str] = []
    total = waituntil.wait_until("19:00", now_fn=clock, sleep_fn=clock.sleep,
                                 log_fn=lines.append, max_wait=5 * 3600)
    assert total == 0.0
    assert clock.slept == []
    assert any("max_wait" in ln for ln in lines)


def test_wait_until_still_sleeps_a_normal_gap():
    clock = _FakeClock(_et(2026, 9, 22, 18, 0))
    total = waituntil.wait_until("19:00", now_fn=clock, sleep_fn=clock.sleep,
                                 log_fn=lambda _m: None, max_wait=5 * 3600)
    assert total == 3600.0


def test_worst_case_scheduled_gap_is_under_the_default_cap():
    """Winter on-time fire at 19:30 UTC -> 14:30 EST -> 19:00 EST is 4h30m.
    The default cap must clear that or every winter run proceeds immediately."""
    winter_fire = datetime(2026, 1, 15, 19, 30, tzinfo=timezone.utc)
    gap = waituntil.seconds_until("19:00", now=winter_fire)
    assert gap == 4.5 * 3600
    assert gap < waituntil.MAX_WAIT


# --- the wait job runs with NO pip install -------------------------------

def test_scanner_package_init_stays_empty():
    """`python -m scanner.waituntil` runs in the wait job with zero third-party
    packages. That works ONLY because scanner/__init__.py imports nothing; a
    convenience re-export there would kill the wait job at import time."""
    from pathlib import Path
    assert Path("scanner/__init__.py").read_text(encoding="utf-8").strip() == ""


def test_waituntil_imports_only_stdlib():
    from pathlib import Path
    src = Path("scanner/waituntil.py").read_text(encoding="utf-8")
    for mod in ("pandas", "numpy", "yfinance", "requests", "matplotlib", "yaml"):
        assert f"import {mod}" not in src, f"waituntil must not import {mod}"
