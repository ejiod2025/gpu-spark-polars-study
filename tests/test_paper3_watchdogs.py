"""tools/paper3_probes/lambda_watchdog.sh and gcp_watchdog.sh: the deadline is a wall-clock epoch, not one long sleep.

No real waiting: the clock and the sleep are injected (P3_WATCHDOG_NOW / P3_WATCHDOG_SLEEP are scripts over a clock file), and
the cloud CLIs are fakes that record their calls. A machine that sleeps is a jump of the clock inside one sleep call.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

PROBES = Path(__file__).resolve().parents[1] / "tools" / "paper3_probes"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is needed")

NOW = '#!/bin/bash\ncat "$FAKE_DIR/clock"\n'
SLEEP = r'''#!/bin/bash
# advances the fake clock by the requested seconds; on call number $JUMP_ON_CALL it advances by $JUMP more (the machine slept)
echo "$1" >> "$FAKE_DIR/sleeps.log"
calls=$(wc -l < "$FAKE_DIR/sleeps.log" | tr -d ' ')
t=$(( $(cat "$FAKE_DIR/clock") + ${1%.*} ))
[ "$calls" = "${JUMP_ON_CALL:-0}" ] && t=$(( t + ${JUMP:-0} ))
echo "$t" > "$FAKE_DIR/clock"
'''
LAMBDA = r'''#!/bin/bash
# fake lambda.sh: list shows the instance until $LISTS_UNTIL_GONE list calls have been made after the first terminate
echo "$1 $2" >> "$FAKE_DIR/lambda.calls"
case "$1" in
  terminate) echo "[]" ;;
  list)
    if [ -n "${LIST_FAILS:-}" ] && [ "$(grep -c '^list' "$FAKE_DIR/lambda.calls")" -le "$LIST_FAILS" ]; then exit 3; fi
    if [ "$(grep -c '^terminate' "$FAKE_DIR/lambda.calls")" -ge 1 ] && [ "$(grep -c '^list' "$FAKE_DIR/lambda.calls")" -gt "${LISTS_UNTIL_GONE:-0}" ]; then exit 0; fi
    printf 'abc123\tp3-probe\tactive\tgpu\tregion\n' ;;
esac
'''
GCLOUD = r'''#!/bin/bash
echo "$*" >> "$FAKE_DIR/gcloud.calls"
case "$3" in
  list)
    n=$(grep -c ' list ' "$FAKE_DIR/gcloud.calls")
    [ "$n" -le "${LIST_FAILS:-0}" ] && { echo "ERROR: transient API failure" >&2; exit 1; }
    [ -f "$FAKE_DIR/deleted" ] || [ "${ALREADY_GONE:-}" = 1 ] || echo "${FAKE_NAME}"
    ;;
  delete)
    n=$(grep -c ' delete ' "$FAKE_DIR/gcloud.calls")
    [ "$n" -le "${DELETE_FAILS:-0}" ] && { echo "ERROR: delete failed" >&2; exit 1; }
    touch "$FAKE_DIR/deleted"
    ;;
esac
'''


@pytest.fixture
def clock(tmp_path):
    fake = tmp_path / "fake"
    fake.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    probes = tmp_path / "probes"
    probes.mkdir()
    for directory, name, text in ((fake, "now.sh", NOW), (fake, "sleep.sh", SLEEP), (probes, "lambda.sh", LAMBDA), (bin_dir, "gcloud", GCLOUD)):
        (directory / name).write_text(text)
        (directory / name).chmod(0o755)
    for name in ("lambda_watchdog.sh", "gcp_watchdog.sh"):
        shutil.copy(PROBES / name, probes / name)
    (fake / "clock").write_text("1000000\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_DIR": str(fake), "P3_WATCHDOG_NOW": str(fake / "now.sh"),
           "P3_WATCHDOG_SLEEP": str(fake / "sleep.sh"), "P3_STATE_DIR": str(tmp_path / "state"), "FAKE_NAME": "p3-probe-x-owner-ejiod-expires-fake"}
    return type("Clock", (), {"fake": fake, "probes": probes, "env": env, "state": tmp_path / "state"})


def run(clock, script, *args, **env):
    done = subprocess.run([BASH, str(clock.probes / script), *args], capture_output=True, text=True, timeout=60,
                          env={**clock.env, **{k: str(v) for k, v in env.items()}})
    done.sleeps = [float(x) for x in (clock.fake / "sleeps.log").read_text().split()] if (clock.fake / "sleeps.log").exists() else []
    done.now = int((clock.fake / "clock").read_text())
    return done


def calls(clock, name):
    path = clock.fake / name
    return path.read_text().splitlines() if path.exists() else []


# ---- lambda_watchdog.sh -------------------------------------------------------------------------------------------------

def test_the_deadline_is_a_recorded_epoch_and_the_watchdog_ticks_until_it_passes(clock):
    done = run(clock, "lambda_watchdog.sh", "abc123", "100", LISTS_UNTIL_GONE=1)
    assert done.returncode == 0, done.stderr
    assert "watchdog armed for abc123: deadline_epoch=1000100" in done.stdout
    assert (clock.state / "abc123.watchdog_deadline").read_text().strip() == "1000100"        # recorded for whoever looks
    ticks = [s for s in done.sleeps if s == 30]
    assert len(ticks) == 4                                                                     # 4 x 30 s = 120 s >= 100 s; no single long sleep
    assert max(done.sleeps) == 30 and "terminate abc123" in calls(clock, "lambda.calls")
    assert "(20 s past it); terminating" in done.stdout and "asleep" not in done.stdout


def test_a_machine_that_sleeps_through_the_deadline_fires_it_at_the_first_wake(clock):
    """The B200 case: armed with 5400 s, the Mac slept for hours. A sleep of 5400 s would have run on after the wake; the
    wall-clock deadline fires at the first check past it."""
    done = run(clock, "lambda_watchdog.sh", "abc123", "5400", JUMP_ON_CALL=1, JUMP=11000, LISTS_UNTIL_GONE=1)
    assert done.returncode == 0, done.stderr
    assert done.sleeps[0] == 30 and len([s for s in done.sleeps if s == 30]) == 1               # one tick, then the clock was hours later
    assert "(5630 s past it); terminating" in done.stdout
    assert "the deadline passed while this machine was asleep or suspended; acting at the first check after it woke" in done.stdout
    assert calls(clock, "lambda.calls")[0] == "terminate abc123"
    assert done.now - 1000000 < 11100 + 100                                                      # no further waiting after the wake


def test_the_terminate_and_confirm_logic_is_unchanged(clock):
    done = run(clock, "lambda_watchdog.sh", "abc123", "10", LISTS_UNTIL_GONE=5)
    assert done.returncode == 0 and "abc123 is no longer listed among p3- instances (terminated)" in done.stdout
    recorded = calls(clock, "lambda.calls")
    assert recorded.count("terminate abc123") >= 2                                                # re-issued every 30 s while still listed
    assert sum(1 for c in recorded if c.startswith("list")) == 6


def test_a_listing_that_fails_is_retried_not_read_as_gone(clock):
    done = run(clock, "lambda_watchdog.sh", "abc123", "10", LIST_FAILS=3, LISTS_UNTIL_GONE=4)
    assert done.returncode == 0 and done.stderr.count("list query failed (rc=3); will retry") == 3


def test_an_instance_that_never_goes_ends_with_a_warning_after_ten_minutes_of_the_clock(clock):
    done = run(clock, "lambda_watchdog.sh", "abc123", "10", LISTS_UNTIL_GONE=100000)
    assert done.returncode == 1 and "still listed after 10 minutes of polling; manual check required" in done.stderr
    waited = done.now - 1000000
    assert 600 < waited < 700 and calls(clock, "lambda.calls").count("terminate abc123") >= 15


def test_the_lambda_watchdog_needs_no_real_time(clock):
    started = __import__("time").monotonic()
    run(clock, "lambda_watchdog.sh", "abc123", "7200", LISTS_UNTIL_GONE=1)                      # two hours of clock
    assert __import__("time").monotonic() - started < 15


# ---- gcp_watchdog.sh ----------------------------------------------------------------------------------------------------

def test_the_gcp_watchdog_refuses_a_name_that_is_not_ours(clock):
    done = run(clock, "gcp_watchdog.sh", "production-db", "us-east4-a", "100")
    assert done.returncode == 1 and "refusing: production-db is not named p3-*" in done.stderr
    assert not calls(clock, "gcloud.calls") and not done.sleeps


def test_the_gcp_watchdog_deletes_only_a_labelled_p3_instance_at_the_wall_clock_deadline(clock):
    name = clock.env["FAKE_NAME"]
    done = run(clock, "gcp_watchdog.sh", name, "us-east4-a", "100")
    assert done.returncode == 0 and f"deleted {name}" in done.stdout
    assert f"watchdog armed for {name}: deadline_epoch=1000100" in done.stdout and len([s for s in done.sleeps if s == 30]) == 4
    assert (clock.state / f"{name}.watchdog_deadline").read_text().strip() == "1000100"
    listing = calls(clock, "gcloud.calls")[0]
    assert f"--filter=name={name} AND labels.purpose=paper3-power-probe" in listing             # the label guard is part of the lookup
    assert calls(clock, "gcloud.calls")[1] == f"compute instances delete {name} --zone=us-east4-a --quiet"


def test_the_gcp_watchdog_fires_at_the_first_wake_past_the_deadline(clock):
    name = clock.env["FAKE_NAME"]
    done = run(clock, "gcp_watchdog.sh", name, "us-east4-a", "4500", JUMP_ON_CALL=2, JUMP=20000)
    assert done.returncode == 0 and f"deleted {name}" in done.stdout
    assert len([s for s in done.sleeps if s == 30]) == 2 and "asleep or suspended" in done.stdout and re.search(r"\((1[5-9]\d{3}) s past it\)", done.stdout)


def test_the_gcp_watchdog_retries_failed_lookups_and_deletes(clock):
    name = clock.env["FAKE_NAME"]
    done = run(clock, "gcp_watchdog.sh", name, "us-east4-a", "10", LIST_FAILS=2, DELETE_FAILS=1)
    assert done.returncode == 0 and f"deleted {name}" in done.stdout
    assert done.stderr.count("list query failed") == 2 and len(re.findall(r"delete attempt \d failed", done.stderr)) == 1       # the 6 attempts are shared


def test_a_transient_failure_is_never_read_as_already_deleted_and_an_empty_listing_is(clock):
    name = clock.env["FAKE_NAME"]
    done = run(clock, "gcp_watchdog.sh", name, "us-east4-a", "10", LIST_FAILS=6)
    assert done.returncode == 1 and "WARNING: could not confirm deletion" in done.stderr and "already deleted" not in done.stdout
    assert not any(" delete " in c for c in calls(clock, "gcloud.calls"))
    (clock.fake / "gcloud.calls").unlink()
    done = run(clock, "gcp_watchdog.sh", name, "us-east4-a", "10", ALREADY_GONE=1)
    assert done.returncode == 0 and f"{name} not found (already deleted)" in done.stdout
