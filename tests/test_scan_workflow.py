"""Structural guards on the daily scan workflow (issue #2).

These assert the parts that are easy to regress by hand-editing YAML and whose
breakage is silent-but-expensive:

  * the cron must stay EARLY, because GitHub fires this repo's schedule events
    1h38m-2h42m late and the wait job absorbs that delay;
  * `concurrency` must live on the SCAN job, not the workflow. At workflow
    level it would also cover the multi-hour sleeping `wait` job and block
    weekly.yml / bot.yml (same `sqzdots-ledger` group) for hours;
  * the wait must be gated at STEP level, not job level -- a skipped `wait`
    job would cascade and skip `scan` through `needs`.
"""

from pathlib import Path

import yaml

WORKFLOW = Path(".github/workflows/scan.yml")


def _doc():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(doc):
    # PyYAML follows YAML 1.1, where the bare key `on` is the boolean True.
    return doc.get("on", doc.get(True))


def _steps(doc, job):
    return doc["jobs"][job].get("steps", [])


def test_cron_fires_early_enough_to_absorb_github_delay():
    assert _triggers(_doc())["schedule"][0]["cron"] == "30 19 * * 1-5"


def test_manual_dispatch_still_available():
    assert "workflow_dispatch" in _triggers(_doc())


def test_no_workflow_level_concurrency():
    """At workflow level it would gate the sleeping wait job too."""
    assert "concurrency" not in _doc()


def test_scan_job_keeps_ledger_concurrency_group():
    conc = _doc()["jobs"]["scan"]["concurrency"]
    assert conc["group"] == "sqzdots-ledger"
    assert conc["cancel-in-progress"] is False


def test_wait_job_has_no_concurrency_group():
    assert "concurrency" not in _doc()["jobs"]["wait"]


def test_scan_depends_on_wait():
    needs = _doc()["jobs"]["scan"]["needs"]
    assert "wait" in ([needs] if isinstance(needs, str) else needs)


def test_wait_job_itself_is_not_conditional():
    """A skipped job cascades through `needs` and would skip the scan."""
    assert "if" not in _doc()["jobs"]["wait"]


def test_sleep_step_is_gated_on_schedule_event():
    gated = [s for s in _steps(_doc(), "wait")
             if "schedule" in str(s.get("if", "")) and "waituntil" in str(s.get("run", ""))]
    assert len(gated) == 1


def test_sleep_step_targets_1900_et():
    step = next(s for s in _steps(_doc(), "wait") if "waituntil" in str(s.get("run", "")))
    assert "--at 19:00" in step["run"]
    assert "America/New_York" in step["run"]


def test_wait_job_has_a_timeout_under_the_six_hour_ceiling():
    timeout = _doc()["jobs"]["wait"]["timeout-minutes"]
    assert 0 < timeout <= 330


def test_healthcheck_ping_runs_only_on_success():
    steps = _steps(_doc(), "scan")
    ping = [s for s in steps if "HEALTHCHECK_URL_SCAN" in str(s)]
    assert len(ping) == 1
    assert "success()" in str(ping[0].get("if", ""))


def test_scan_still_passes_futures_watchlist():
    """Regression: the owner-only futures list must stay in the scan command."""
    runs = " ".join(str(s.get("run", "")) for s in _steps(_doc(), "scan"))
    assert "--futures futures.csv" in runs


def test_deadman_ping_cannot_fail_the_scan():
    """A healthchecks.io outage must not page the operator about a scan that
    succeeded. `curl ... && echo` as the trailing statement exits non-zero under
    `bash -e` (verified: exit 6), which trips `Alert operator on failure`. Using
    curl as an `if` CONDITION keeps the step at exit 0 on any ping failure.
    Same rule as scanner/bot.py::_ping_healthcheck.
    """
    step = next(s for s in _steps(_doc(), "scan") if "HEALTHCHECK_URL_SCAN" in str(s))
    run = str(step["run"])
    assert "if curl" in run, "curl must be an if-condition, not a bare && chain"
    assert "&& echo" not in run, "the && form makes a ping failure fail the step"


def test_wait_job_alerts_on_its_own_failure():
    """If `wait` fails, `scan` is skipped via `needs` and its alert step never
    runs -- so `wait` needs its own, or the failure is silent until healthchecks
    notices ~60min later."""
    alerts = [s for s in _steps(_doc(), "wait") if "failure()" in str(s.get("if", ""))]
    assert len(alerts) == 1
    assert "sendMessage" in str(alerts[0]["run"])


def test_sleep_step_is_not_the_last_step_in_wait():
    """Guards the ordering: the failure alert must come after the sleep."""
    names = [s.get("name", "") for s in _steps(_doc(), "wait")]
    assert names.index("Wait until 19:00 ET") < names.index("Alert operator on failure")


def test_sleep_step_skipped_on_rerun():
    """"Re-run all jobs" KEEPS event_name=schedule. A re-run at 09:00 would try
    to sleep 10h, exceed the timeout, get CANCELLED -- and a cancelled job runs
    no failure() steps, so it would be silent."""
    step = next(s for s in _steps(_doc(), "wait") if "waituntil" in str(s.get("run", "")))
    assert "run_attempt" in str(step["if"])


def test_sleep_timeout_is_on_the_step_not_only_the_job():
    """A JOB-level timeout CANCELS the job and skips failure() alerts. A STEP
    timeout fails the step, so the job fails normally and the alert fires."""
    step = next(s for s in _steps(_doc(), "wait") if "waituntil" in str(s.get("run", "")))
    assert 0 < step["timeout-minutes"] <= 290


def test_scan_fails_loudly_when_telegram_credentials_are_missing():
    """run.py exits 0 when creds are absent, so without this the deadman would
    ping green on a run that delivered nothing."""
    step = next(s for s in _steps(_doc(), "scan") if "Verify Telegram" in str(s.get("name", "")))
    assert "exit 1" in step["run"]


def test_operator_alerts_surface_a_failed_send():
    """`curl -s` exits 0 on HTTP 404/400, so a rotated bot token would silently
    drop the page. -f makes the step fail instead."""
    for job in ("wait", "scan"):
        alert = next(s for s in _steps(_doc(), job) if "sendMessage" in str(s.get("run", "")))
        assert "curl -fsS" in alert["run"], f"{job} alert must use -f"


def test_commit_date_uses_market_time_not_utc():
    """19:00 EST is EXACTLY 00:00 UTC, so `date -u` stamps TOMORROW for ~5
    months a year."""
    commit = next(s for s in _steps(_doc(), "scan") if "git commit" in str(s.get("run", "")))
    assert "TZ=America/New_York date" in commit["run"]
    assert "date -u" not in commit["run"]


def test_scan_checks_out_main_not_the_trigger_sha():
    """The wait job fires ~3.5h before the scan runs. A default checkout pins
    the code to main as of 15:30 ET and silently drops anything pushed during
    the trading day."""
    checkout = next(s for s in _steps(_doc(), "scan") if "checkout" in str(s.get("uses", "")))
    assert checkout.get("with", {}).get("ref") == "main"
