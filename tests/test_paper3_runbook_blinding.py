"""Blinding of the operator's view of a launch (design 7.3: nobody looks at a timing, energy, power or cost aggregate until every round is done).

The bring-up's SF1 smoke preflight prints `[PASS    ] run.baseline: run_experiment.py exit 0 in 4217s` (a whole suite's wall time), the input
generators and package managers print their own seconds, and run_campaign.py's round logs print more. Those logs are copied off the host and archived
unread; the operator-facing output of the driver is built from an allow-list (tools/paper3_campaign/p3_bringup_view.py) and from a progress line that
never echoes a stage's last log line (P3_QUIET_LOG in tools/paper3_probes/p3_stage_lib.sh, forced on by the driver). The probe drivers keep their default.

No cloud and no study query: the driver runs against the fake host of test_paper3_runbook_ops.py, whose bring-up and round scripts are replaced here by
ones that print timing lines on purpose.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools" / "paper3_campaign"
PROBES = REPO / "tools" / "paper3_probes"
for path in (str(TOOLS), str(REPO / "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

import p3_bringup_view as V  # noqa: E402
from test_paper3_runbook_ops import (BASH, BRINGUP_TEMPLATE, FAKE_PLAN, STAGE_TEMPLATE, ctl, drive, sh, world, write_record)  # noqa: E402,F401

PREFLIGHT_LOG = """\
2026-11-22T21:00:00Z STEP checkout begin
2026-11-22T21:00:31Z STEP checkout ok
2026-11-22T21:00:31Z STEP setup begin
Installed 41 packages in 1.2s
2026-11-22T21:09:02Z STEP setup ok
2026-11-22T21:09:02Z STEP generate ok
[PASS    ] env.gpu: NVIDIA H100 80GB HBM3, driver 580.105.08, 81559 MiB
[PASS    ] inputs.generate_sf1: generate_data.py exit 0 in 13s (generate_data.py --root /data --scales 1)
[PASS    ] run.baseline: run_experiment.py exit 0 in 4217s; log rerun/preflight-bringup/run-baseline.log
[WARN    ] env.virtualenv: not running inside a virtualenv
[INFO    ] cpu_energy: package 181.3 W idle, 349.2 W busy
[FAIL    ] run.complex: run_experiment.py exit 1 in 3571s; log rerun/preflight-bringup/run-complex.log
[FAIL    ] session:cx_q13: window_seconds 12.3456 is not the sum of its runs' seconds
[FAIL    ] env.disk_data: 100 GiB free at /data (need >= 250)
report: rerun/preflight-bringup/report.json
  gate G1: fail (2 failing check(s))
PREFLIGHT: FAILED {'PASS': 3, 'FAIL': 3}
VERDICT: fail
2026-11-22T21:40:00Z STEP preflight FAILED rc=1
"""
TIMES = ("4217", "3571", "12.3456", "1.2s", "41 packages", "181.3", "349.2", "13s", "2026-11-22T21:09")


# ---- the allow-list view -------------------------------------------------------------------------------------------------------

def test_only_step_results_fail_lines_and_the_verdict_get_through_and_no_time_does():
    shown = V.view(PREFLIGHT_LOG)
    text = "\n".join(shown)
    assert shown[-1] == "VERDICT: fail"
    assert [l for l in shown if l.startswith("STEP")] == ["STEP checkout ok", "STEP setup ok", "STEP generate ok", "STEP preflight FAILED rc=1"]
    assert [l.split(":")[0] for l in shown if l.startswith("[FAIL")] == ["[FAIL    ] run.complex", "[FAIL    ] session", "[FAIL    ] env.disk_data"]
    assert "in <time>" in text and "window_seconds <value>" in text and "100 GiB free at /data (need >= 250)" in text      # what a person needs to act stays
    for leaked in TIMES:
        assert leaked not in text, leaked
    for dropped in ("[PASS", "[WARN", "[INFO", "report:", "gate G1", "PREFLIGHT:", "Installed", "begin"):
        assert dropped not in text, dropped


@pytest.mark.parametrize("status", ["PASS    ", "WARN    ", "INFO    ", "SKIP    ", "XFAIL   ", "COVERAGE"])
def test_a_line_that_is_not_a_fail_never_reaches_the_operator_whatever_it_says(status):
    line = f"[{status}] run.baseline: run_experiment.py exit 0 in 7777s; window 123.456789 s"
    assert V.view(line + "\n") == ["no VERDICT line: the preflight did not reach its end (see the STEP lines)"]


@pytest.mark.parametrize("detail, kept", [
    ("run_experiment.py exit 1 in 3571s; log x", "run_experiment.py exit 1 in <time>; log x"),
    ("took 12.5 s", "took <time>"),
    ("waited 3 min for it", "waited <time> for it"),
    ("after 2.5 hours", "after <time>"),
    ("t_hat 0.123456 s", "t_hat <time>"),
    ("elapsed 17", "elapsed <value>"),
    ("energy_j: 4.5e3", "energy_j: <value>"),
    ("window_seconds 12.3456 is not the sum", "window_seconds <value> is not the sum"),
    ("mean 0.4217385 off", "mean <number> off"),
    ("polars 1.44.2 and pyspark 3.5.9 on Python 3.12, driver 580.105.08", "polars 1.44.2 and pyspark 3.5.9 on Python 3.12, driver 580.105.08"),
    ("100 GiB free (need >= 250) on 26 threads, 3 sessions, SF300", "100 GiB free (need >= 250) on 26 threads, 3 sessions, SF300"),
])
def test_times_and_timing_values_are_redacted_and_ordinary_numbers_are_not(detail, kept):
    assert V.view(f"[FAIL    ] x: {detail}\n")[0] == f"[FAIL    ] x: {kept}"


def test_the_last_verdict_wins_and_a_log_without_one_says_so():
    assert V.view("VERDICT: fail\nsomething\nVERDICT: pass\n")[-1] == "VERDICT: pass"
    assert "no VERDICT line" in V.view("2026-11-22T21:00:00Z STEP setup FAILED rc=3\n")[-1]
    assert V.view("VERDICT: pass: in 55s\n")[-1].startswith("no VERDICT line")                    # only the exact form passes


def test_a_flood_of_fail_lines_is_capped_and_the_cap_is_said():
    shown = V.view("".join(f"[FAIL    ] c{i}: bad\n" for i in range(100)) + "VERDICT: fail\n")
    assert len([l for l in shown if l.startswith("[FAIL")]) == V.MAX_FAIL_LINES and "60 more [FAIL] line(s)" in shown[-2]


def test_the_command_line_reads_one_file_and_says_nothing_else(tmp_path):
    log = tmp_path / "bringup-a1.log"
    log.write_text(PREFLIGHT_LOG)
    done = subprocess.run([sys.executable, str(TOOLS / "p3_bringup_view.py"), str(log)], capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout.splitlines()[-1] == "VERDICT: fail" and "4217" not in done.stdout + done.stderr
    assert subprocess.run([sys.executable, str(TOOLS / "p3_bringup_view.py")], capture_output=True, text=True).returncode == 2
    missing = subprocess.run([sys.executable, str(TOOLS / "p3_bringup_view.py"), str(tmp_path / "none.log")], capture_output=True, text=True)
    assert missing.returncode == 1 and "stays on the host" in missing.stderr


# ---- the stage library's progress line -------------------------------------------------------------------------------------------

HARNESS = r'''
set -u
p3_log() { echo "$*"; }
P3_SSH=("$FAKE_SSH_SCRIPT")
. "$PROBES/p3_stage_lib.sh"
P3_SLEEP=true P3_POLL_SECONDS=1 P3_STAGE_TIMEOUT=4 P3_PROGRESS_EVERY=2 P3_RETRY_PAUSE=0
p3_stage_wait demo || true
'''


@pytest.fixture
def stage_harness(tmp_path):
    fake = tmp_path / "ssh.sh"
    fake.write_text('#!/bin/bash\nprintf "RC=\\nSTARTED=1\\nRUNNING=1\\nLOG=[PASS    ] run.baseline: run_experiment.py exit 0 in 4217s\\n"\n')
    fake.chmod(0o755)

    def run(**env):
        import os
        e = {**os.environ, "PROBES": str(PROBES), "FAKE_SSH_SCRIPT": str(fake), **env}
        e.pop("P3_QUIET_LOG", None) if "P3_QUIET_LOG" not in env else None
        return subprocess.run([BASH, "-c", HARNESS], capture_output=True, text=True, env=e, timeout=60)
    return run


def test_the_probe_drivers_default_still_echoes_the_last_log_line(stage_harness):
    done = stage_harness()
    assert done.returncode == 0 and "last log line: [PASS    ] run.baseline: run_experiment.py exit 0 in 4217s" in done.stdout
    assert "withheld" not in done.stdout
    assert "last log line: [PASS" in stage_harness(P3_QUIET_LOG="0").stdout                        # 0 is off, as for any switch


def test_with_quiet_log_the_progress_line_keeps_the_state_and_drops_the_log_line(stage_harness):
    done = stage_harness(P3_QUIET_LOG="1")
    assert done.returncode == 0 and "stage demo running (poll 1/4); last log line withheld (P3_QUIET_LOG)" in done.stdout
    assert "4217" not in done.stdout + done.stderr and "PASS" not in done.stdout + done.stderr


# ---- the driver, end to end on the fake host -----------------------------------------------------------------------------------------

BRINGUP = r'''#!/usr/bin/env bash
rc=0; [ -s "$HOME/ctl/rc-bringup" ] && rc=$(cat "$HOME/ctl/rc-bringup")
note() { echo "$(date -u +%FT%TZ) $*"; }
mkdir -p "$HOME/study/rerun/setup-logs" "$HOME/study/rerun/preflight-bringup" "$HOME/study/rerun/bringup-artifacts"
echo setup > "$HOME/study/rerun/setup-logs/setup.log"; echo '{"result": "OK"}' > "$HOME/study/rerun/verify-sf1-30-ref.json"
echo '{"allow_list_items": ["timer:a.timer"]}' > "$HOME/study/rerun/bringup-artifacts/exclusivity-after.json"
note "STEP setup begin"; echo "Installed 41 packages in 1.2s"; note "STEP setup ok"
note "STEP preflight begin"
echo "[PASS    ] run.baseline: run_experiment.py exit 0 in 4217s; log rerun/preflight-bringup/run-baseline.log"
echo "[PASS    ] inputs.generate_sf1: generate_data.py exit 0 in 13s"
if [ "$rc" = 0 ]; then echo "VERDICT: pass"; note "STEP preflight ok"
else echo "[FAIL    ] run.complex: run_experiment.py exit 1 in 3571s; log rerun/preflight-bringup/run-complex.log"; echo "VERDICT: fail"; note "STEP preflight FAILED rc=$rc"; fi
exit $rc
'''
STAGE = STAGE_TEMPLATE.replace("set -u\n", 'set -u\necho "[PASS    ] run.baseline: run_experiment.py exit 0 in 7777s; window 123.456789 s"\n', 1)
SECRETS = ("4217", "3571", "7777", "123.456789", "1.2s", "in 13s")


@pytest.fixture
def blind(drive):
    world = drive.world
    plan = FAKE_PLAN.replace(repr(BRINGUP_TEMPLATE), repr(BRINGUP)).replace(repr(STAGE_TEMPLATE), repr(STAGE))
    assert plan != FAKE_PLAN and repr(BRINGUP) in plan and repr(STAGE) in plan
    (world.tmp / "fake_plan.py").write_text(plan)
    world.env.update(FAKE_ROUNDS="1", P3_PROGRESS_EVERY="2")
    return drive


def everything_the_operator_sees(done):
    return done.stdout + done.stderr + done.log


def test_a_launch_shows_the_operator_the_verdict_and_no_time_while_the_logs_are_archived_whole(blind):
    world = blind.world
    ctl(world, "sleep", "2")                                  # keep the round running long enough for several progress lines
    done = blind(write_record(world), "--no-teardown")
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    seen = everything_the_operator_sees(done)
    assert "BRING-UP STEP setup ok" in seen and "BRING-UP STEP preflight ok" in seen and "BRING-UP VERDICT: pass" in seen
    assert "stage campaign-r1-a1 running (poll" in seen and "last log line withheld (P3_QUIET_LOG)" in seen          # progress is still shown, as states
    assert "last log line:" not in seen
    for secret in SECRETS:
        assert secret not in seen, secret
    assert "[PASS" not in seen
    archived = (done.dest / "bringup-a1.log").read_text() + (done.dest / "campaign-r1-a1.log").read_text()
    assert "run_experiment.py exit 0 in 4217s" in archived and "in 7777s" in archived                                  # the full logs are there, for after the campaign


def test_a_failed_bringup_shows_the_fail_lines_without_their_times_and_holds_the_host(blind):
    world = blind.world
    ctl(world, "rc-bringup", "1")
    done = blind(write_record(world))
    assert done.returncode == 20 and "HELD: bring-up ended with 1" in done.log
    seen = everything_the_operator_sees(done)
    assert "BRING-UP [FAIL    ] run.complex: run_experiment.py exit 1 in <time>; log rerun/preflight-bringup/run-complex.log" in seen
    assert "BRING-UP STEP preflight FAILED rc=1" in seen and "BRING-UP VERDICT: fail" in seen
    for secret in SECRETS:
        assert secret not in seen, secret
    assert "in 3571s" in (done.dest / "bringup-a1.log").read_text()                                                     # archived whole


def test_the_driver_forces_the_quiet_log_even_when_the_environment_says_otherwise(blind):
    world = blind.world
    ctl(world, "sleep", "2")
    done = blind(write_record(world), "--no-teardown", P3_QUIET_LOG="0")
    assert done.returncode == 0 and "last log line:" not in everything_the_operator_sees(done)
    assert not any(s in everything_the_operator_sees(done) for s in SECRETS)
    assert re.search(r"^export P3_QUIET_LOG=1\b", (TOOLS / "p3_driver.sh").read_text(), flags=re.M)


def test_the_status_view_never_shows_a_log_line_either(blind):
    world = blind.world
    record = write_record(world)
    done = blind(record, "--no-teardown")
    assert done.returncode == 0
    status = sh(world, TOOLS / "p3_status.sh", record)
    assert status.returncode == 0 and not any(s in status.stdout + status.stderr for s in SECRETS) and "PASS" not in status.stdout


# ---- the one exception: the log of a stage that failed and held the driver ------------------------------------------------------------

OPEN = TOOLS / "p3_open_log.sh"


def opened(world, record, name, *args, dest):
    return sh(world, OPEN, record, name, "--dest", dest, *args)


def test_a_failed_bringup_is_listed_as_held_and_only_its_log_can_be_opened_and_every_opening_is_recorded(blind):
    world = blind.world
    ctl(world, "rc-bringup", "1")
    record = write_record(world)
    done = blind(record)
    assert done.returncode == 20
    assert (done.dest / ".held-logs").read_text().split() == ["bringup-a1.log"]
    assert f"p3_open_log.sh {record} bringup-a1.log" in done.log and "each opening is logged" in done.log
    assert "in 3571s" not in everything_the_operator_sees(done)                              # the hold message itself shows no time
    first = opened(world, record, "bringup-a1.log", dest=done.dest)
    assert first.returncode == 0 and "run_experiment.py exit 1 in 3571s" in first.stdout and "opening bringup-a1.log" in first.stderr
    again = opened(world, record, "bringup-a1.log", "--tail", "2", dest=done.dest)
    assert again.returncode == 0 and len(again.stdout.splitlines()) == 2
    lines = (done.dest / "log-openings.log").read_text().splitlines()
    assert len(lines) == 2 and all("OPENED bringup-a1.log" in l and "design 7.4" in l for l in lines)
    events = [json.loads(l) for l in (world.ledger / "ledger.jsonl").read_text().splitlines()]
    opens = [e for e in events if e["event"] == "held-log-opened"]
    assert len(opens) == 2 and opens[0]["log"] == "bringup-a1.log" and opens[0]["lease_name"] == json.loads(record.read_text())["lease_name"]


@pytest.mark.parametrize("name", ["campaign-r1-a1.log", "bringup-a2.log", "../bringup-a1.log", "/etc/passwd", "bringup-a1.log/../driver.log", "bringup-a1", ".held-logs", "p3-x.json"])
def test_no_other_log_or_file_can_be_opened(blind, name):
    world = blind.world
    ctl(world, "rc-bringup", "1")
    record = write_record(world)
    done = blind(record)
    (done.dest / "campaign-r1-a1.log").write_text("[PASS    ] run.baseline: exit 0 in 7777s\n")                     # a log that exists but did not fail
    refused = opened(world, record, name, dest=done.dest)
    assert refused.returncode == 2 and "7777" not in refused.stdout + refused.stderr
    assert not (done.dest / "log-openings.log").exists()


def test_a_failed_round_lists_its_log_and_the_logs_of_stages_that_succeeded_stay_closed(blind):
    world = blind.world
    ctl(world, "rc-round-1", "9\n")
    record = write_record(world)
    done = blind(record)
    assert done.returncode == 20 and "round 1 ended with exit 9" in done.log
    assert (done.dest / ".held-logs").read_text().split() == ["campaign-r1-a1.log"]
    assert opened(world, record, "campaign-r1-a1.log", dest=done.dest).returncode == 0
    closed = opened(world, record, "bringup-a1.log", dest=done.dest)                                                 # the bring-up succeeded: closed
    assert closed.returncode == 2 and "not the log of a stage that failed" in closed.stderr and "campaign-r1-a1.log" in closed.stderr and "4217" not in closed.stdout


def test_a_stage_that_is_only_slow_is_not_a_failure_and_a_clean_launch_lists_nothing(blind):
    world = blind.world
    ctl(world, "sleep", "5")
    record = write_record(world)
    slow = blind(record, P3_ROUND_TIMEOUT=1)
    assert slow.returncode == 20 and "still running after its poll budget" in slow.log
    assert not (slow.dest / ".held-logs").exists() and opened(world, record, "campaign-r1-a1.log", dest=slow.dest).returncode == 2
    ctl(world, "sleep", "0")
    ok = blind(record, "--no-teardown", P3_ROUND_TIMEOUT=60)
    assert ok.returncode == 0 and not (ok.dest / ".held-logs").exists()
    assert opened(world, record, "bringup-a1.log", dest=ok.dest).returncode == 2


def test_the_mac_helper_opens_a_log_only_through_the_logging_script_and_validates_its_arguments(tmp_path):
    from test_paper3_controller import CONTROLLER_NAME, FAKE_GCLOUD_IAP  # noqa: F401  (the helper's fake gcloud)
    # a fresh world: reuse the fixtures through a tiny inline setup
    import os
    net, bin_dir = tmp_path / "net", tmp_path / "bin"
    net.mkdir(); bin_dir.mkdir()
    (bin_dir / "gcloud").write_text(FAKE_GCLOUD_IAP)
    (bin_dir / "gcloud").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_NET": str(net), "P3_CONTROLLER": CONTROLLER_NAME, "P3_CONTROLLER_ZONE": "us-central1-a"}
    lease = "p3-campaign-h100-l1-owner-ejiod-expires-20261124T1400Z"
    done = subprocess.run([BASH, str(TOOLS / "p3_ctl.sh"), "open-log", lease, "bringup-a1.log", "50"], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    command = shlex.split((net / "ctl.commands").read_text().splitlines()[-1])[-1]                                  # the script bash -c receives
    assert "p3_open_log.sh" in command and f"{lease}.json" in command and "bringup-a1.log --tail 50" in command and " cat " not in command and "tail -n" not in command
    calls = len((net / "ctl.commands").read_text().splitlines())
    for args in (("open-log", lease), ("open-log", "siriusmed", "x.log"), ("open-log", lease, "../x.log"), ("open-log", lease, "x.log", "many"), ("open-log", lease, "x.log;ls")):
        assert subprocess.run([BASH, str(TOOLS / "p3_ctl.sh"), *args], capture_output=True, text=True, env=env).returncode == 2
    assert len((net / "ctl.commands").read_text().splitlines()) == calls
