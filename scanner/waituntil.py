"""Wait until a fixed wall-clock time in a named timezone.

GitHub deprioritizes `schedule` events: this repo's daily cron fires 1h38m to
2h42m LATE, every run, with a 64-minute spread (measured over 14 consecutive
runs). No cron time can correct that, and cron is UTC-only so any fixed time
also drifts an hour across US daylight saving.

So the workflow fires deliberately EARLY and then waits here for the real
target. The wait is computed in the target's own timezone, which makes delivery
DST-stable: 19:00 ET is 19:00 ET in January and July. If the cron fires *after*
the target (a delay past our headroom), the wait is zero and the scan runs
immediately -- degraded to the old behavior, never worse.

Usage (from the workflow):
    python -m scanner.waituntil --at 19:00 --tz America/New_York
"""

import argparse
import time
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/New_York"
HEARTBEAT = 900.0          # log every 15 min so the Actions log proves liveness
# Safety cap. The longest LEGITIMATE wait is 4h30m (winter: an on-time 19:30 UTC
# fire is 14:30 EST, target 19:00 EST). Anything beyond this means we are not in
# the scheduled path at all -- the classic case is "Re-run all jobs", which keeps
# event_name=schedule, so a re-run at 09:00 would compute a 10h sleep, exceed the
# CI job timeout and get the job CANCELLED. A cancelled job runs no `if: failure()`
# steps, so that failure would be completely silent. Proceed immediately instead.
MAX_WAIT = 5 * 3600.0


def _parse_hm(target_hm) -> tuple[int, int]:
    """Parse "HH:MM" into (hour, minute). Raises ValueError on anything else."""
    parts = str(target_hm).split(":")
    if len(parts) != 2:
        raise ValueError(f"target must be HH:MM, got {target_hm!r}")
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"target must be HH:MM, got {target_hm!r}") from None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"target out of range, got {target_hm!r}")
    return hh, mm


def seconds_until(target_hm: str, tz: str = DEFAULT_TZ, now=None) -> float:
    """Seconds from `now` until TODAY'S `target_hm` (HH:MM) in `tz`.

    Returns 0.0 when the target has already passed -- deliberately never rolls
    to tomorrow, so a late trigger proceeds at once instead of sleeping ~23h.
    `now` is injectable for tests; naive datetimes are read as `tz` local.
    """
    hh, mm = _parse_hm(target_hm)
    zone = ZoneInfo(tz)
    if now is None:
        now = datetime.now(zone)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=zone)
    local = now.astimezone(zone)
    target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return max(0.0, (target - local).total_seconds())


def wait_until(target_hm: str, tz: str = DEFAULT_TZ, *, now_fn=None,
               sleep_fn=time.sleep, log_fn=print, heartbeat: float = HEARTBEAT,
               max_wait: float | None = MAX_WAIT) -> float:
    """Sleep until `target_hm` in `tz`, in `heartbeat`-sized chunks.

    Chunking keeps a heartbeat in the CI log so a multi-hour wait is visibly
    alive rather than looking hung. Returns the total seconds slept (0.0 if the
    target already passed). The clock and sleep are injectable so tests never
    touch the real ones.

    `max_wait` refuses an implausibly long sleep and proceeds immediately (see
    MAX_WAIT): oversleeping a CI job timeout gets the job CANCELLED, and a
    cancelled job fires no failure alert. Delivering late beats delivering
    nothing and saying nothing.
    """
    first = seconds_until(target_hm, tz=tz, now=now_fn() if now_fn is not None else None)
    if max_wait is not None and first > max_wait:
        log_fn(f"[waituntil] gap {first:.0f}s exceeds max_wait {max_wait:.0f}s for "
               f"{target_hm} {tz} — NOT sleeping, proceeding immediately "
               f"(re-run or misconfigured schedule?)")
        return 0.0
    total = 0.0
    while True:
        now = now_fn() if now_fn is not None else None
        remaining = seconds_until(target_hm, tz=tz, now=now)
        if remaining <= 0:
            return total
        chunk = min(remaining, heartbeat)
        log_fn(f"[waituntil] {remaining:.0f}s until {target_hm} {tz}; sleeping {chunk:.0f}s")
        sleep_fn(chunk)
        total += chunk


def main(argv=None) -> float:
    ap = argparse.ArgumentParser(description="Sleep until a wall-clock time in a timezone")
    ap.add_argument("--at", required=True, help="target time as HH:MM (24h)")
    ap.add_argument("--tz", default=DEFAULT_TZ, help=f"IANA timezone (default {DEFAULT_TZ})")
    ap.add_argument("--heartbeat", type=float, default=HEARTBEAT,
                    help="max seconds per sleep chunk (log cadence)")
    args = ap.parse_args(argv)
    slept = wait_until(args.at, tz=args.tz, heartbeat=args.heartbeat)
    print(f"[waituntil] done; slept {slept:.0f}s (target {args.at} {args.tz})")
    return slept


if __name__ == "__main__":
    main()
