"""The launch-on-capacity watcher (tools/paper3_campaign/p3_launch_on_capacity.sh): polls Lambda for a host's instance type, takes a fresh price snapshot, launches ONCE with
p3_launch.sh and starts the driver.

No cloud: the fake `curl` of test_paper3_runbook_ops.py answers /instance-types and the launch call, the clock is a file that only moves when the watcher sleeps, and a
per-sleep scenario (what happens at the Nth sleep) lets capacity appear, a launch fail once, and so on. The watcher writes its log, snapshots and driver output under a test directory,
never under rerun/paper3.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools" / "paper3_campaign"
for path in (str(TOOLS), str(REPO / "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

from test_paper3_runbook_ops import BASH, COMMIT, wait_for, world, write_record  # noqa: E402,F401

WATCHER = TOOLS / "p3_launch_on_capacity.sh"
H100 = "gpu_1x_h100_sxm5"


def instance_types(h100_regions=(), others=("us-east-1",)):
    """What GET /instance-types answers: the four study types with their prices and the regions that list capacity."""
    def entry(name, cents, regions):
        return {"instance_type": {"name": name, "price_cents_per_hour": cents}, "regions_with_capacity_available": [{"name": r} for r in regions]}
    return json.dumps({"data": {H100: entry(H100, 489, h100_regions), "gpu_1x_a100_sxm4": entry("gpu_1x_a100_sxm4", 199, others), "gpu_1x_gh200": entry("gpu_1x_gh200", 259, ()),
                                "gpu_1x_b200_sxm6": entry("gpu_1x_b200_sxm6", 799, ())}})


@pytest.fixture
def watch(world):
    tmp, net = world.tmp, world.net
    out = tmp / "watchout"
    (tmp / "wclock").mkdir()
    (tmp / "wclock" / "clock").write_text("1000000\n")
    (tmp / "wclock" / "now.sh").write_text('#!/bin/bash\ncat "' + str(tmp / "wclock" / "clock") + '"\n')
    # the Nth sleep: advance the clock, then run scenario/N if the test wrote one (it changes what the fake API says next)
    (tmp / "wclock" / "sleep.sh").write_text('#!/bin/bash\nc="' + str(tmp / "wclock" / "clock") + '"\nn=$(( $(cat "' + str(tmp / "wclock" / "count") + '" 2>/dev/null || echo 0) + 1 ))\n'
                                             'echo $n > "' + str(tmp / "wclock" / "count") + '"\necho "$1" >> "' + str(tmp / "wclock" / "sleeps.log") + '"\n'
                                             'echo $(( $(cat "$c") + ${1%.*} )) > "$c"\n[ -f "' + str(net) + '/scenario/$n" ] && bash "' + str(net) + '/scenario/$n"\nexit 0\n')
    (net / "scenario").mkdir()
    (tmp / "driver.sh").write_text('#!/bin/bash\necho "$@" > "$FAKE_NET/driver.args"\nexec sleep 40\n')
    (tmp / "watchdog.sh").write_text('#!/bin/bash\nexec sleep 40\n')
    for f in ("wclock/now.sh", "wclock/sleep.sh", "driver.sh", "watchdog.sh"):
        (tmp / f).chmod(0o755)
    (net / "instance_types").write_text(instance_types())
    world.env.update(P3_WATCH_OUTDIR=str(out), P3_WATCH_NOW=str(tmp / "wclock" / "now.sh"), P3_WATCH_SLEEP=str(tmp / "wclock" / "sleep.sh"), P3_WATCH_DRIVER=str(tmp / "driver.sh"),
                     P3_WATCH_DRIVER_GRACE="0.4", P3_LAMBDA_WATCHDOG=str(tmp / "watchdog.sh"))

    def run(*args, **env):
        done = subprocess.run([BASH, str(WATCHER), "--commit", COMMIT, *map(str, args)], capture_output=True, text=True, timeout=120, env={**world.env, **{k: str(v) for k, v in env.items()}})
        return done
    world.run, world.out = run, out
    world.sleeps = lambda: (tmp / "wclock" / "sleeps.log").read_text().split() if (tmp / "wclock" / "sleeps.log").exists() else []
    world.at = lambda n, script: (net / "scenario" / str(n)).write_text(script)                    # at the Nth sleep, run this bash
    world.log = lambda: (out / "watch-h100-l1.log").read_text() if (out / "watch-h100-l1.log").exists() else ""
    world.records = lambda: sorted(world.ledger.glob("p3-campaign-h100-l1-*.json")) if world.ledger.exists() else []
    world.calls = lambda: (net / "curl.argv").read_text().splitlines() if (net / "curl.argv").exists() else []
    return world


def capacity_at(n, regions):
    return f'echo \'{instance_types(regions)}\' > "$FAKE_NET/instance_types"'


def test_it_waits_launches_in_the_first_region_with_capacity_snapshots_first_and_starts_the_driver(watch):
    watch.at(3, capacity_at(3, ["us-south-3"]))                                  # the third sleep ends with capacity in us-south-3
    done = watch.run()
    assert done.returncode == 0, done.stdout + done.stderr + watch.log()
    assert watch.sleeps() == ["30", "30", "30"]                                  # polls 1, 2, 3 found nothing; poll 4 found capacity (the driver's start check sleeps in real time)
    (record,) = watch.records()
    data = json.loads(record.read_text())
    assert data["host"] == "h100" and data["launch"] == 1 and data["region"] == "us-south-3" and data["campaign_commit"] == COMMIT and data["credit_funded"] == "yes"
    assert data["instance_type"] == H100 and data["lease_name"].startswith("p3-campaign-h100-l1-owner-ejiod-expires-") and data["price_usd_h"] == 4.89
    snaps = sorted((watch.out / "snapshots").iterdir())
    assert len(snaps) == 1 and snaps[0].name.endswith("-h100-l1") and json.loads((snaps[0] / "prices.json").read_text())["lambda"][H100]["price_usd_per_hour"] == 4.89
    assert data["price_snapshot_utc"] == json.loads((snaps[0] / "prices.json").read_text())["snapshot_utc"]                  # the launch used THAT snapshot
    assert wait_for(watch.net / "driver.args").read_text().split() == [str(record)]                                          # the driver on the new record
    log = watch.log()
    assert "CAPACITY for gpu_1x_h100_sxm5 in us-south-3" in log and "launching in us-south-3" in log and f"launched: record {record}" in log and "driver started: pid" in log
    launches = [c for c in watch.calls() if "instance-operations/launch" in c]
    assert len(launches) == 1 and data["lease_name"] in launches[0] and "gpu_1x_h100_sxm5" in launches[0] and "us-south-3" in launches[0]


def test_it_launches_at_most_once_even_when_it_is_started_again(watch):
    watch.at(1, capacity_at(1, ["us-south-2"]))
    assert watch.run().returncode == 0
    again = watch.run()
    assert again.returncode == 2 and "exists already" in again.stderr and "not launching a second one" in again.stderr
    assert len([c for c in watch.calls() if "instance-operations/launch" in c]) == 1 and len(watch.records()) == 1


def test_the_preferred_region_wins_and_any_other_listed_region_is_the_fallback(watch):
    (watch.net / "instance_types").write_text(instance_types(["us-west-1", "us-south-2", "europe-central-1"]))
    assert watch.run().returncode == 0
    assert json.loads(watch.records()[0].read_text())["region"] == "us-south-2"                                              # the planner's own order
    for record in watch.records():
        record.unlink()
    (watch.net / "instance_types").write_text(instance_types(["us-west-1", "europe-central-1"]))
    other = watch.run()
    assert other.returncode == 0 and json.loads(watch.records()[0].read_text())["region"] == "europe-central-1"                # none preferred: the first by name
    for record in watch.records():
        record.unlink()
    custom = watch.run("--regions", "us-west-1")
    assert custom.returncode == 0 and json.loads(watch.records()[0].read_text())["region"] == "us-west-1"


def test_a_launch_that_fails_for_capacity_is_logged_and_the_watching_goes_on_until_one_works(watch):
    (watch.net / "instance_types").write_text(instance_types(["us-south-2"]))
    (watch.net / "launch_response").write_text(json.dumps({"error": {"code": "instance-operations/launch/insufficient-capacity", "message": "Not enough capacity"}}))
    watch.at(1, f"echo '{json.dumps({'data': {'instance_ids': ['inst-001']}})}' > \"$FAKE_NET/launch_response\"")                # the second try works
    done = watch.run()
    assert done.returncode == 0, done.stdout + watch.log()
    launches = [c for c in watch.calls() if "instance-operations/launch" in c]
    assert len(launches) == 2 and watch.sleeps()[:1] == ["30"]
    assert "the launch call failed" in watch.log() and len(watch.records()) == 1                                                  # one record: only the success wrote one
    assert len(list((watch.out / "snapshots").iterdir())) == 2                                                                    # a fresh snapshot for each attempt


def test_a_refusal_of_the_launcher_stops_the_watching_instead_of_repeating_it(watch):
    (watch.net / "instance_types").write_text(instance_types(["us-south-2"]))
    done = watch.run(P3_SNAPSHOT_MAX_AGE_S="-1")                                     # p3_launch.sh refuses every snapshot (exit 2): trying again would repeat it
    assert done.returncode == 2, done.stdout + done.stderr
    assert "REFUSED" in watch.log() and "p3_launch.sh REFUSED (exit 2)" in watch.log() and not watch.records()
    assert len([c for c in watch.calls() if "instance-operations/launch" in c]) == 0 and watch.sleeps() == []                    # no retry loop


def test_an_unusable_api_answer_is_logged_and_polling_continues(watch):
    (watch.net / "instance_types").write_text("<html>502 Bad Gateway</html>")
    watch.at(2, capacity_at(2, ["us-south-2"]))
    done = watch.run()
    assert done.returncode == 0 and "the API answer has no .data" in watch.log() and len(watch.records()) == 1


def test_it_ends_without_a_launch_at_its_time_limit(watch):
    done = watch.run("--max-hours", 1)
    assert done.returncode == 1 and "--max-hours reached" in watch.log() and len(watch.sleeps()) == 120 and not watch.records()      # an hour of 30-second polls
    assert not [c for c in watch.calls() if "instance-operations" in c]


def test_it_ends_without_a_launch_when_the_stop_file_appears(watch):
    watch.at(2, f'touch "{watch.ledger}/watch-h100-l1/stop"')
    stopped = watch.run()
    assert stopped.returncode == 1 and "stop file present" in watch.log() and watch.sleeps() == ["30", "30"] and not watch.records()


def test_a_second_watcher_is_refused_while_the_first_one_lives_and_a_stale_lock_is_taken_over(watch):
    state = watch.ledger / "watch-h100-l1" / "lock"
    state.mkdir(parents=True)
    holder = subprocess.Popen(["sleep", "30"])
    (state / "pid").write_text(str(holder.pid))
    try:
        refused = watch.run()
        assert refused.returncode == 2 and "another watcher" in refused.stderr and not watch.calls()
    finally:
        holder.kill()
        holder.wait()
    stale = watch.run("--max-hours", 0)
    assert stale.returncode == 1 and "--max-hours reached" in watch.log()                                                       # the lock of a dead pid does not block


def test_a_host_whose_gpu_was_not_ready_may_be_launched_again_but_a_real_launch_may_not(watch):
    write_record(watch, host="h100", launch="1", lease_name="p3-campaign-h100-l1-owner-ejiod-expires-20261120T0000Z", terminate_confirmed_utc="2026-11-23T20:00:00Z",
                 infrastructure_failure={"kind": "cuda-not-ready", "rounds_started": 0})
    (watch.net / "instance_types").write_text(instance_types(["us-south-2"]))
    done = watch.run()
    assert done.returncode == 0, done.stdout + done.stderr + watch.log()
    assert "infrastructure failure before round 1" in watch.log() and len(watch.records()) == 2
    for path in watch.records():
        path.unlink()
    write_record(watch, host="h100", launch="1", lease_name="p3-campaign-h100-l1-owner-ejiod-expires-20261121T0000Z", terminate_confirmed_utc="2026-11-23T20:00:00Z")   # finished or lost: not a relaunch case
    refused = watch.run()
    assert refused.returncode == 2 and "exists already" in refused.stderr
    forced = watch.run("--allow-relaunch")
    assert forced.returncode == 0 and len(watch.records()) == 2


def test_a_driver_that_does_not_start_is_reported_loudly_with_the_command_to_run(watch):
    (watch.tmp / "driver.sh").write_text("#!/bin/bash\nexit 7\n")
    (watch.net / "instance_types").write_text(instance_types(["us-south-2"]))
    done = watch.run()
    assert done.returncode == 3, done.stdout + done.stderr
    log = watch.log()
    assert "LAUNCHED BUT THE DRIVER DID NOT START" in log and "The instance exists and bills" in log and "nohup" in log and str(watch.records()[0]) in log


def test_it_never_terminates_lists_instances_or_puts_the_key_on_a_command_line(watch):
    (watch.net / "instance_types").write_text(instance_types(["us-south-2"]))
    done = watch.run()
    assert done.returncode == 0
    calls = (watch.net / "curl.argv").read_text()
    assert "terminate" not in calls and "/instances" not in calls.replace("instance-operations", "").replace("/instance-types", "")        # no listing of instances, no terminate
    assert "SECRET-API-KEY" not in calls + done.stdout + done.stderr + watch.log()
    assert not (watch.net / "lambda.calls").exists() or "terminate" not in (watch.net / "lambda.calls").read_text()


@pytest.mark.parametrize("args, message", [
    (("--interval", "5"), "at least 15"),
    (("--interval", "soon"), "at least 15"),
    (("--host", "c-gcp"), "Lambda host"),
    (("--host", "h100", "--launch", "3"), "--launch is 1 or 2"),
    (("--credit-funded", "maybe"), "yes or no"),
    (("--driver-args", "--fresh; rm -rf /"), "letters, digits"),
    (("--max-hours", "forever"), "whole number"),
])
def test_bad_options_are_refused_before_anything_is_called(watch, args, message):
    done = watch.run(*args)
    assert done.returncode == 2 and message in done.stderr and not watch.calls()


def test_the_commit_must_be_a_full_hash_and_driver_args_reach_the_driver(watch):
    bad = subprocess.run([BASH, str(WATCHER), "--commit", "main"], capture_output=True, text=True, env=watch.env)
    assert bad.returncode == 2 and "40-character" in bad.stderr
    (watch.net / "instance_types").write_text(instance_types(["us-south-2"]))
    done = watch.run("--driver-args", "--stop-after-bringup")
    assert done.returncode == 0
    assert wait_for(watch.net / "driver.args").read_text().split() == [str(watch.records()[0]), "--stop-after-bringup"]


def test_the_dry_run_polls_and_launches_nothing(watch):
    done = watch.run("--dry-run")
    assert done.returncode == 0 and "DRY RUN" in watch.log() and not watch.calls() and not watch.records()


# ---- excluded regions (2026-11-23: both H100 hosts that never answered CUDA were in us-south-3) ----------------------------------------------------------

def launched_region(watch):
    return json.loads(watch.records()[0].read_text())["region"]


def test_an_excluded_region_is_never_launched_in_even_when_it_is_the_only_one_listed_or_the_preferred_one(watch):
    (watch.net / "instance_types").write_text(instance_types(["us-south-3", "us-west-1"]))
    done = watch.run("--regions", "us-south-3 us-south-2", "--exclude-regions", "us-south-3")
    assert done.returncode == 0, done.stdout + done.stderr + watch.log()
    assert launched_region(watch) == "us-west-1"                                                    # the preferred us-south-3 is excluded: the next region that lists capacity wins
    assert "excluded regions: us-south-3" in watch.log() and "preferred region us-south-3 is excluded" in done.stderr
    assert not [c for c in watch.calls() if "instance-operations/launch" in c and "us-south-3" in c]


def test_capacity_only_in_an_excluded_region_keeps_the_watcher_waiting_and_launches_nothing(watch):
    (watch.net / "instance_types").write_text(instance_types(["us-south-3"]))
    done = watch.run("--exclude-regions", "us-south-3", "--max-hours", 1)
    assert done.returncode == 1 and "--max-hours reached" in watch.log() and not watch.records()
    assert "no region lists gpu_1x_h100_sxm5 with capacity" in watch.log()
    assert not [c for c in watch.calls() if "instance-operations" in c]
    assert not (watch.out / "snapshots").exists() or not list((watch.out / "snapshots").iterdir())                                          # no snapshot either


def test_the_environment_and_the_option_both_exclude_and_add_up(watch):
    (watch.net / "instance_types").write_text(instance_types(["us-south-3", "us-west-1", "europe-central-1"]))
    done = watch.run("--exclude-regions", "us-west-1", P3_EXCLUDE_REGIONS="us-south-3")
    assert done.returncode == 0 and launched_region(watch) == "europe-central-1"
    assert "excluded regions: us-south-3 us-west-1" in watch.log()


@pytest.mark.parametrize("bad", ["us-south", "US-SOUTH-3", "us-south-3;ls", "$(id)", "3"])
def test_an_excluded_region_must_look_like_a_region_name(watch, bad):
    done = watch.run("--exclude-regions", bad)
    assert done.returncode == 2 and "not a Lambda region name" in done.stderr and not watch.calls()
    envbad = watch.run(P3_EXCLUDE_REGIONS=bad)
    assert envbad.returncode == 2 and "not a Lambda region name" in envbad.stderr and not watch.calls()


def test_the_help_text_documents_the_exclusion():
    header = subprocess.run([BASH, str(WATCHER), "--bogus"], capture_output=True, text=True).stdout
    assert "--exclude-regions" in header and "P3_EXCLUDE_REGIONS" in header and "us-south-3" in header
