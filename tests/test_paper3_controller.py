"""The always-on campaign controller (docs/PAPER3_CONTROLLER.md): the opt-in launch knobs of the planner, the GCP watchdog's stop mode, the
launcher's pass-through, the stateless Lambda lease reaper, the Mac-side controller helper, the systemd units and the bootstrap script.

No cloud, no secret, no study query. The cloud tools are the fakes of test_paper3_runbook_ops.py (and a richer `gcloud` where a status is
needed); nothing here starts systemd or a VM. The bootstrap script and the units were also run in an Ubuntu 24.04 container (see the document).
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools" / "paper3_campaign"
CONTROLLER = TOOLS / "controller"
for path in (str(REPO), str(TOOLS), str(REPO / "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

import p3_plan as P  # noqa: E402
import probe_power  # noqa: E402
from test_paper3_runbook_ops import (BASH, COMMIT, NOW_ARG, launch, sh, snapshot, wait_for, world, write_record)  # noqa: E402,F401  (world is a fixture)

NOW = datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc)
GCP_NAME = "p3-campaign-cgcp-l1-owner-ejiod-expires-20261118t0300z"


# ---- the planner's opt-in knobs ----------------------------------------------------------------------------------------------

def test_the_defaults_are_the_runbooks_request_and_the_environment_cannot_change_the_generated_blocks(monkeypatch):
    spec = P.launch_spec("c-gcp", "1", "us-east4-c", NOW)
    assert "--instance-termination-action=STOP" in spec["command"] and spec["termination_action"] == "STOP"          # the lead's decision: data-safe by default
    assert spec["command"][-3:] == ["--no-service-account", "--no-scopes", "--metadata=block-project-ssh-keys=TRUE"]      # the hardening is the default, not a knob
    assert spec["access"] == {"service_account": None, "scopes": None, "block_project_ssh_keys": True, "os_login": False}
    assert P.launch_spec("h100", "1", "us-south-2", NOW)["body"]["ssh_key_names"] == ["Key22"]
    for name, value in (("P3_GCP_TERMINATION_ACTION", "STOP"), ("P3_GCP_EXTRA_FLAGS", "--tags=x"), ("P3_LAMBDA_SSH_KEY_NAME", "other"), ("P3_GCP_OS_LOGIN", "1")):
        monkeypatch.setenv(name, value)
    assert P.launch_spec("c-gcp", "1", "us-east4-c", NOW) == spec                        # the function never reads the environment: `docs --check` is stable
    assert P.docs(REPO / "docs" / "PAPER3_RUNBOOK.md", write=False) == []


def test_stop_keeps_the_disk_and_delete_is_the_only_other_termination_action():
    stop = P.launch_spec("c-gcp", "1", "us-east4-c", NOW)
    assert "--instance-termination-action=STOP" in stop["command"] and "--instance-termination-action=DELETE" not in stop["command"]
    assert stop["termination_action"] == "STOP" and f"--max-run-duration={stop['max_run_duration']}" in stop["command"]
    fallback = P.launch_spec("c-gcp", "1", "us-east4-c", NOW, termination_action="DELETE")                      # if a metal host rejects STOP
    assert "--instance-termination-action=DELETE" in fallback["command"] and "--instance-termination-action=STOP" not in fallback["command"]
    with pytest.raises(P.PlanError, match="termination action"):
        P.launch_spec("c-gcp", "1", "us-east4-c", NOW, termination_action="SUSPEND")


@pytest.mark.parametrize("host, zone", [("c-gcp", "us-east4-a"), ("c-gcp", "us-east4-c"), ("c-gcp", "us-central1-a"), ("c-vm", "us-east4-a"), ("c-vm", "us-central1-a")])
def test_every_gcp_campaign_host_has_no_service_account_and_trusts_no_project_wide_ssh_key(host, zone):
    for itype in (None, "c3-standard-192-metal", "c3-highcpu-192-metal"):
        if itype and host != "c-gcp":
            continue
        command = P.launch_spec(host, "1", zone, NOW, instance_type=itype)["command"]
        assert "--no-service-account" in command and "--no-scopes" in command
        assert not [c for c in command if c.startswith(("--service-account", "--scopes"))]
        metadata = [c for c in command if c.startswith("--metadata")]
        assert metadata == ["--metadata=block-project-ssh-keys=TRUE"]


def test_os_login_is_an_opt_in_that_keeps_the_block_on_project_keys():
    on = P.launch_spec("c-gcp", "1", "us-east4-c", NOW, os_login=True)
    assert "--metadata=block-project-ssh-keys=TRUE,enable-oslogin=TRUE" in on["command"] and on["access"]["os_login"] is True
    assert "--no-service-account" in on["command"]
    assert P.launch_spec("h100", "1", "us-south-2", NOW, os_login=True) == P.launch_spec("h100", "1", "us-south-2", NOW)       # Lambda has no such thing


def test_extra_flags_are_appended_and_cannot_override_the_planners_guard_rails():
    flags = ["--tags=p3-campaign", "--network=p3-campaign-net"]
    spec = P.launch_spec("c-gcp", "1", "us-east4-c", NOW, extra_flags=flags)
    assert spec["command"][-2:] == flags and spec["command"][4] == spec["lease_name"]
    for bad in (["--max-run-duration=1000h"], ["--labels=project=somebody-else"], ["--zone=us-west1-a"], ["--machine-type=n2-standard-8"],
                ["--instance-termination-action=STOP"], ["--project=somebody-elses-production"], ["somebody-elses-vm"], ["-q"], ["--"],
                ["--service-account=editor@siriusmedai.iam.gserviceaccount.com"], ["--scopes=cloud-platform"], ["--no-service-account"], ["--no-scopes"],
                ["--metadata=block-project-ssh-keys=FALSE"], ["--metadata=enable-oslogin=FALSE"], ["--metadata-from-file=ssh-keys=keys.txt"]):
        with pytest.raises(P.PlanError):
            P.launch_spec("c-gcp", "1", "us-east4-c", NOW, extra_flags=bad)


def test_the_lambda_ssh_key_name_can_be_the_controllers_own_key():
    spec = P.launch_spec("h100", "1", "us-south-2", NOW, ssh_key_name="p3-controller-key")
    assert spec["body"]["ssh_key_names"] == ["p3-controller-key"]
    for bad in ("", "key with spaces", "k;rm -rf /", "x" * 70):
        with pytest.raises(P.PlanError, match="SSH key name"):
            P.launch_spec("h100", "1", "us-south-2", NOW, ssh_key_name=bad)


def test_the_command_line_reads_the_knobs_from_the_environment(world):
    env = {"P3_GCP_TERMINATION_ACTION": "DELETE", "P3_GCP_EXTRA_FLAGS": "--tags=p3-campaign --network=p3-campaign-net", "P3_GCP_OS_LOGIN": "1"}
    done = subprocess.run([sys.executable, str(TOOLS / "p3_plan.py"), "launch-spec", "c-gcp", "1", "--place", "us-east4-c", "--now", "2026-11-17T06:00:00Z"],
                          capture_output=True, text=True, env={**world.env, **env})
    assert done.returncode == 0, done.stderr
    command = json.loads(done.stdout)["command"]
    assert "--instance-termination-action=DELETE" in command and command[-2:] == ["--tags=p3-campaign", "--network=p3-campaign-net"]
    assert "--metadata=block-project-ssh-keys=TRUE,enable-oslogin=TRUE" in command and "--no-service-account" in command
    plain = subprocess.run([sys.executable, str(TOOLS / "p3_plan.py"), "launch-spec", "c-gcp", "1", "--place", "us-east4-c", "--now", "2026-11-17T06:00:00Z"],
                           capture_output=True, text=True, env={k: v for k, v in world.env.items() if not k.startswith("P3_GCP_")})
    assert "--metadata=block-project-ssh-keys=TRUE" in json.loads(plain.stdout)["command"] and "--instance-termination-action=STOP" in json.loads(plain.stdout)["command"]
    lam = subprocess.run([sys.executable, str(TOOLS / "p3_plan.py"), "launch-spec", "h100", "1", "--place", "us-south-2", "--now", "2026-11-17T06:00:00Z"],
                         capture_output=True, text=True, env={**world.env, "P3_LAMBDA_SSH_KEY_NAME": "p3-controller-key"})
    assert json.loads(lam.stdout)["body"]["ssh_key_names"] == ["p3-controller-key"]
    bad = subprocess.run([sys.executable, str(TOOLS / "p3_plan.py"), "launch-spec", "c-gcp", "1", "--place", "us-east4-c"], capture_output=True, text=True,
                         env={**world.env, "P3_GCP_EXTRA_FLAGS": "--max-run-duration=9999h"})
    assert bad.returncode == 2 and "not allowed" in bad.stderr


# ---- the launcher passes the knobs through -----------------------------------------------------------------------------------

WATCHDOG_STUB = '#!/bin/bash\necho "$P3_GCP_WATCHDOG_ACTION $*" > "$FAKE_NET/watchdog.args"\nexec sleep 30\n'


def test_the_fallback_to_delete_creates_with_delete_arms_a_watchdog_that_deletes_and_records_it(world):
    watchdog = world.tmp / "gwd.sh"
    watchdog.write_text(WATCHDOG_STUB)
    watchdog.chmod(0o755)
    args = ["c-gcp", "1", "--place", "us-east4-c", "--commit", COMMIT, "--price-snapshot", snapshot(world), "--credit-funded", "no", "--yes"]
    done = launch(world, *args, P3_GCP_WATCHDOG=watchdog, P3_GCP_TERMINATION_ACTION="DELETE",
                  P3_GCP_EXTRA_FLAGS="--tags=p3-campaign", P3_GCP_OS_LOGIN="1")
    assert done.returncode == 0, done.stdout + done.stderr
    record = json.loads(next(world.ledger.glob("p3-campaign-cgcp-l1-*.json")).read_text())
    assert record["termination_action"] == "DELETE" and record["access"] == {"service_account": None, "scopes": None, "block_project_ssh_keys": True, "os_login": True}
    create = (world.net / "gcloud.log").read_text().splitlines()[0]
    assert "--instance-termination-action=DELETE" in create and "--no-service-account --no-scopes" in create and create.endswith("--tags=p3-campaign")
    assert "--metadata=block-project-ssh-keys=TRUE,enable-oslogin=TRUE" in create
    assert wait_for(world.net / "watchdog.args").read_text().split()[0] == "delete"
    subprocess.run(["kill", str(record["watchdog_pid"])], capture_output=True)


def test_a_default_launch_stops_and_a_lambda_launch_can_use_the_controllers_key(world):
    watchdog = world.tmp / "gwd.sh"
    watchdog.write_text(WATCHDOG_STUB)
    watchdog.chmod(0o755)
    done = launch(world, "c-vm", "1", "--place", "us-east4-a", "--commit", COMMIT, "--price-snapshot", snapshot(world), "--credit-funded", "no", "--yes", P3_GCP_WATCHDOG=watchdog)
    assert done.returncode == 0, done.stdout + done.stderr
    record = json.loads(next(world.ledger.glob("p3-campaign-cvm-l1-*.json")).read_text())
    assert record["termination_action"] == "STOP" and wait_for(world.net / "watchdog.args").read_text().split()[0] == "stop"
    assert "--instance-termination-action=STOP" in (world.net / "gcloud.log").read_text().splitlines()[0]
    assert record["access"] == {"service_account": None, "scopes": None, "block_project_ssh_keys": True, "os_login": False}          # the default hardening is on the record
    assert "--no-service-account --no-scopes --metadata=block-project-ssh-keys=TRUE" in (world.net / "gcloud.log").read_text().splitlines()[0]
    subprocess.run(["kill", str(record["watchdog_pid"])], capture_output=True)
    lam = world.tmp / "wd.sh"
    lam.write_text('#!/bin/bash\nexec sleep 30\n')
    lam.chmod(0o755)
    done = launch(world, "h100", "1", "--place", "us-south-2", "--commit", COMMIT, "--price-snapshot", snapshot(world), "--yes", "--now", NOW_ARG,
                  P3_LAMBDA_WATCHDOG=lam, P3_LAMBDA_SSH_KEY_NAME="p3-controller-key")
    assert done.returncode == 0, done.stdout + done.stderr
    body = [line for line in (world.net / "curl.argv").read_text().splitlines() if "instance-operations/launch" in line][0]
    assert "p3-controller-key" in body and "Key22" not in body
    lrecord = json.loads(next(world.ledger.glob("p3-campaign-h100-l1-*.json")).read_text())
    assert lrecord["termination_action"] is None and lrecord["access"] is None
    subprocess.run(["kill", str(lrecord["watchdog_pid"])], capture_output=True)


# ---- the GCP watchdog's stop mode --------------------------------------------------------------------------------------------

FAKE_GCLOUD_STATUS = r'''#!/bin/bash
# fake gcloud with a status: list prints "name<TAB>status" while the instance exists; stop moves it to TERMINATED; delete removes it
n="$FAKE_NET"
echo "$*" >> "$n/gcloud.log"
case "$1 $2 $3" in
  "compute instances list") [ -f "$n/instance" ] && [[ "$*" == *"labels.project=paper3-energy"* ]] && printf '%s\t%s\n' "$FAKE_INSTANCE_NAME" "$(cat "$n/status")"; exit 0 ;;
  "compute instances stop") echo stop >> "$n/actions.log"; [ "${GCLOUD_STOP_RC:-0}" = 0 ] && echo TERMINATED > "$n/status"; exit "${GCLOUD_STOP_RC:-0}" ;;
  "compute instances delete") echo delete >> "$n/actions.log"; rm -f "$n/instance"; exit 0 ;;
esac
echo "fake gcloud: unsupported $*" >&2; exit 2
'''


@pytest.fixture
def status_world(world, tmp_path):
    (world.bin / "gcloud").write_text(FAKE_GCLOUD_STATUS)
    (world.bin / "gcloud").chmod(0o755)
    fake = tmp_path / "clock"
    fake.mkdir()
    (fake / "now.sh").write_text('#!/bin/bash\ncat "$FAKE_CLOCK/clock"\n')
    (fake / "sleep.sh").write_text('#!/bin/bash\necho $(( $(cat "$FAKE_CLOCK/clock") + ${1%.*} )) > "$FAKE_CLOCK/clock"\n')
    for f in ("now.sh", "sleep.sh"):
        (fake / f).chmod(0o755)
    (fake / "clock").write_text("1000000\n")
    world.env.update(FAKE_CLOCK=str(fake), P3_WATCHDOG_NOW=str(fake / "now.sh"), P3_WATCHDOG_SLEEP=str(fake / "sleep.sh"), P3_STATE_DIR=str(tmp_path / "wstate"),
                     FAKE_INSTANCE_NAME=GCP_NAME)
    return world


def actions(world):
    path = world.net / "actions.log"
    return path.read_text().split() if path.exists() else []


def test_in_stop_mode_the_watchdog_stops_a_running_host_and_never_deletes_it(status_world):
    world = status_world
    (world.net / "instance").write_text("up")
    (world.net / "status").write_text("RUNNING")
    done = sh(world, TOOLS / "p3_gcp_watchdog.sh", GCP_NAME, "us-east4-c", 100, P3_GCP_WATCHDOG_ACTION="stop")
    assert done.returncode == 0 and "stopped " in done.stdout and "the disk is kept" in done.stdout
    assert actions(world) == ["stop"] and (world.net / "instance").exists()
    assert "--zone=us-east4-c" in (world.net / "gcloud.log").read_text()


def test_in_stop_mode_a_host_the_provider_already_stopped_is_left_alone(status_world):
    world = status_world
    (world.net / "instance").write_text("up")
    (world.net / "status").write_text("TERMINATED")
    done = sh(world, TOOLS / "p3_gcp_watchdog.sh", GCP_NAME, "us-east4-c", 100, P3_GCP_WATCHDOG_ACTION="stop")
    assert done.returncode == 0 and "already TERMINATED" in done.stdout and actions(world) == []


def test_in_stop_mode_a_failing_stop_is_retried_and_ends_with_a_warning_not_a_delete(status_world):
    world = status_world
    (world.net / "instance").write_text("up")
    (world.net / "status").write_text("RUNNING")
    done = sh(world, TOOLS / "p3_gcp_watchdog.sh", GCP_NAME, "us-east4-c", 10, P3_GCP_WATCHDOG_ACTION="stop", GCLOUD_STOP_RC=1)
    assert done.returncode == 1 and done.stderr.count("stop attempt") == 6 and "manual check required" in done.stderr
    assert set(actions(world)) == {"stop"}                                                # six stops, no delete, ever


def test_the_default_mode_still_deletes_and_reads_the_tab_separated_listing(status_world):
    world = status_world
    (world.net / "instance").write_text("up")
    (world.net / "status").write_text("RUNNING")
    done = sh(world, TOOLS / "p3_gcp_watchdog.sh", GCP_NAME, "us-east4-c", 100)
    assert done.returncode == 0 and "deleted " in done.stdout and actions(world) == ["delete"]


def test_the_watchdog_refuses_an_unknown_action_and_acts_only_on_our_label(status_world):
    world = status_world
    refused = sh(world, TOOLS / "p3_gcp_watchdog.sh", GCP_NAME, "us-east4-c", 100, P3_GCP_WATCHDOG_ACTION="suspend")
    assert refused.returncode == 1 and "delete or stop" in refused.stderr and not (world.net / "gcloud.log").exists()
    gone = sh(world, TOOLS / "p3_gcp_watchdog.sh", GCP_NAME, "us-east4-c", 100, P3_GCP_WATCHDOG_ACTION="stop")        # nothing listed under our label
    assert gone.returncode == 0 and "not found among instances labelled project=paper3-energy" in gone.stdout and actions(world) == []


# ---- the stateless lease reaper ------------------------------------------------------------------------------------------------

FAKE_LAMBDA_LIST = r'''#!/bin/bash
n="$FAKE_NET"
echo "$*" >> "$n/lambda.calls"
case "$1" in
  list) [ -f "$n/list_fails" ] && { echo "curl: (6) could not resolve host" >&2; exit 1; }; cat "$n/lambda_list" ;;
  terminate) echo "$2" >> "$n/terminated.log"; exit "$(cat "$n/terminate_rc" 2>/dev/null || echo 0)" ;;
esac
'''
H100 = "p3-campaign-h100-l1-owner-ejiod-expires-20261124T1400Z"
EXPIRY = int(datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def reaper(world):
    (world.tmp / "lambda_list.sh").write_text(FAKE_LAMBDA_LIST)
    (world.tmp / "lambda_list.sh").chmod(0o755)
    (world.tmp / "now.sh").write_text('#!/bin/bash\ncat "$FAKE_NET/now"\n')
    (world.tmp / "now.sh").chmod(0o755)
    world.env.update(P3_LAMBDA_SH=str(world.tmp / "lambda_list.sh"), P3_REAPER_NOW=str(world.tmp / "now.sh"))

    def run(listing, minutes_past, *args, **env):
        """minutes_past: how long after the lease in the name `now` is (negative: before)."""
        (world.net / "lambda_list").write_text("".join("\t".join(row) + "\n" for row in listing))
        (world.net / "now").write_text(f"{EXPIRY + minutes_past * 60}\n")
        return sh(world, TOOLS / "p3_reaper.sh", *args, **env)

    world.reap = run
    world.terminated = lambda: (world.net / "terminated.log").read_text().split() if (world.net / "terminated.log").exists() else []
    return world


ROW = ("id-1", H100, "active", "gpu_1x_h100_sxm5", "us-south-2")


def test_an_instance_past_its_lease_and_the_grace_is_terminated_and_written_to_the_ledger(reaper):
    done = reaper.reap([ROW], 31, "--terminate")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "OVERDUE" in done.stdout and "terminating" in done.stdout and reaper.terminated() == ["id-1"]
    event = json.loads((reaper.ledger / "ledger.jsonl").read_text().splitlines()[-1])
    assert event["event"] == "reaper-terminate-requested" and event["instance_id"] == "id-1" and event["lease_name"] == H100


@pytest.mark.parametrize("minutes_past", [-600, -1, 0, 29, 30])
def test_an_instance_inside_its_lease_or_its_grace_is_left_alone(reaper, minutes_past):
    done = reaper.reap([ROW], minutes_past, "--terminate")
    assert done.returncode == 0 and " OK " in done.stdout and reaper.terminated() == []


def test_without_terminate_it_only_reports_and_says_so_with_exit_3(reaper):
    done = reaper.reap([ROW], 120)
    assert done.returncode == 3 and "OVERDUE" in done.stdout and "would terminate" in done.stdout and reaper.terminated() == []
    quiet = reaper.reap([ROW], -5)
    assert quiet.returncode == 0 and "(report only)" in quiet.stdout


def test_the_grace_is_an_option_and_a_non_number_is_refused(reaper):
    assert reaper.reap([ROW], 31, "--terminate", "--grace-min", "60").returncode == 0 and reaper.terminated() == []
    assert reaper.reap([ROW], 61, "--terminate", "--grace-min", "60").returncode == 0 and reaper.terminated() == ["id-1"]
    bad = reaper.reap([ROW], 61, "--terminate", "--grace-min", "soon")
    assert bad.returncode == 2 and "whole number" in bad.stderr


def test_a_name_without_a_lease_is_reported_and_never_touched(reaper):
    rows = [("id-2", "p3-weird-name", "active", "t", "r"), ("id-3", "p3-campaign-a100-l1-owner-ejiod-expires-notadate", "active", "t", "r"),
            ("id-4", "p3-campaign-a100-l1-owner-ejiod-expires-20261399T9999Z", "active", "t", "r")]
    done = reaper.reap(rows, 100000, "--terminate")
    assert done.returncode == 0 and done.stdout.count("NO-LEASE") == 3 and reaper.terminated() == []


def test_instances_that_are_already_terminating_are_skipped(reaper):
    done = reaper.reap([("id-1", H100, "terminating", "t", "r"), ("id-5", H100.replace("l1", "l2"), "terminated", "t", "r")], 500, "--terminate")
    assert done.returncode == 0 and done.stdout.count("SKIP") == 2 and reaper.terminated() == []


def test_a_lease_extended_in_the_launch_record_is_honoured_but_only_while_the_launch_is_open(reaper):
    record = write_record(reaper, host="h100", launch="1", lease_name=H100, expires_utc="2026-11-24T20:00:00Z")
    assert record.name == f"{H100}.json"
    held = reaper.reap([ROW], 120, "--terminate")                                  # past the name's lease, before the record's (six hours later)
    assert held.returncode == 0 and " OK " in held.stdout and reaper.terminated() == []
    late = reaper.reap([ROW], 6 * 60 + 31, "--terminate")
    assert "OVERDUE" in late.stdout and reaper.terminated() == ["id-1"]
    data = json.loads(record.read_text())
    data["terminate_confirmed_utc"] = "2026-11-24T10:00:00Z"                          # a confirmed termination extends nothing
    record.write_text(json.dumps(data))
    (reaper.net / "terminated.log").unlink()
    again = reaper.reap([ROW], 120, "--terminate")
    assert "OVERDUE" in again.stdout and reaper.terminated() == ["id-1"]


def test_a_record_that_is_earlier_than_the_name_never_shortens_the_lease(reaper):
    write_record(reaper, host="h100", launch="1", lease_name=H100, expires_utc="2026-11-24T01:00:00Z")
    done = reaper.reap([ROW], -60, "--terminate")
    assert " OK " in done.stdout and reaper.terminated() == []


def test_lower_case_gcp_style_lease_names_parse_too(reaper):
    row = ("id-6", "p3-probe-h100-owner-ejiod-expires-20261124t1400z", "active", "t", "r")
    assert "OVERDUE" in reaper.reap([row], 45, "--terminate").stdout and reaper.terminated() == ["id-6"]


def test_a_failed_listing_is_never_read_as_nothing_overdue(reaper):
    (reaper.net / "list_fails").write_text("1")
    done = reaper.reap([ROW], 9999, "--terminate")
    assert done.returncode == 1 and "listing failed" in done.stderr and reaper.terminated() == []


def test_a_failed_terminate_call_is_reported_and_retried_by_the_next_pass(reaper):
    (reaper.net / "terminate_rc").write_text("1")
    done = reaper.reap([ROW], 90, "--terminate")
    assert done.returncode == 1 and "failed; the next pass tries again" in done.stderr
    assert not (reaper.ledger / "ledger.jsonl").exists()                           # nothing recorded as requested
    (reaper.net / "terminate_rc").write_text("0")
    assert reaper.reap([ROW], 95, "--terminate").returncode == 0 and reaper.terminated() == ["id-1", "id-1"]


def test_the_reaper_asks_the_probes_helper_only_for_list_and_terminate_of_listed_ids(reaper):
    reaper.reap([ROW, ("id-9", "p3-probe-a100-owner-ejiod-expires-20261124T1400Z", "active", "t", "r")], 45, "--terminate")
    calls = (reaper.net / "lambda.calls").read_text().split("\n")
    assert [c.split()[0] for c in calls if c] == ["list", "terminate", "terminate"] and {c.split()[1] for c in calls if c.startswith("terminate")} == {"id-1", "id-9"}


# ---- the Mac-side controller helper --------------------------------------------------------------------------------------------

FAKE_GCLOUD_IAP = r'''#!/bin/bash
# fake gcloud for p3_ctl.sh: logs every call with the project it ran in; `compute ssh --command` answers from files
n="$FAKE_NET"
echo "${CLOUDSDK_CORE_PROJECT:-unset}|$*" >> "$n/ctl.log"
[ "$1 $2" = "compute ssh" ] || { echo "fake gcloud: p3_ctl.sh must only use compute ssh, got: $*" >&2; exit 2; }
cmd=""
while [ $# -gt 0 ]; do case "$1" in --command) cmd="$2"; shift 2 ;; *) shift ;; esac; done
echo "$cmd" >> "$n/ctl.commands"
case "$cmd" in
  *"tar -C /home/p3ops/study/rerun"*) cat "$n/launch.tar" ;;
  *"tar -C /home/p3ops -cf - p3ledger"*) cat "$n/ledger.tar" ;;
  *) echo "ran: $cmd" ;;
esac
'''
CONTROLLER_NAME = "p3-controller-owner-ejiod-expires-20261207t0000z"


@pytest.fixture
def ctl(world):
    (world.bin / "gcloud").write_text(FAKE_GCLOUD_IAP)
    (world.bin / "gcloud").chmod(0o755)
    world.env.update(P3_CONTROLLER=CONTROLLER_NAME, P3_CONTROLLER_ZONE="us-central1-a")
    world.run = lambda *args, **env: sh(world, TOOLS / "p3_ctl.sh", *args, **env)
    return world


def make_launch_tar(world, tamper=False, sums=True):
    base = world.tmp / "tarbase"
    shutil.rmtree(base, ignore_errors=True)
    launch_dir = base / "paper3" / "h100" / "launch1"
    (launch_dir / "rerun" / "paper3-h100-l1").mkdir(parents=True)
    (launch_dir / "rerun" / "paper3-h100-l1" / "status.json").write_text('{"state": "complete"}\n')
    (launch_dir / "launch-record.json").write_text("{}\n")
    if sums:
        subprocess.run("shasum -a 256 rerun/paper3-h100-l1/status.json launch-record.json > LAUNCH-SHA256SUMS", shell=True, cwd=launch_dir, check=True)
    if tamper:
        (launch_dir / "rerun" / "paper3-h100-l1" / "status.json").write_text('{"state": "tampered"}\n')
    subprocess.run(["tar", "-cf", str(world.net / "launch.tar"), "-C", str(base), "paper3"], check=True)
    return world.tmp / "dest"


def test_the_helper_only_ever_names_the_controller_its_zone_and_the_iap_tunnel(ctl):
    done = ctl.run("status")
    assert done.returncode == 0, done.stderr
    lines = (ctl.net / "ctl.log").read_text().splitlines()
    assert lines and all(line.startswith("siriusmedai|compute ssh " + CONTROLLER_NAME + " --zone=us-central1-a --tunnel-through-iap --quiet") for line in lines)
    assert "p3_status.sh" in (ctl.net / "ctl.commands").read_text() and "sudo -u p3ops -H bash -c" in (ctl.net / "ctl.commands").read_text()
    assert ctl.run("logs", "20").returncode == 0 and "journalctl" in (ctl.net / "ctl.commands").read_text().splitlines()[-1] and " -n 20 " in (ctl.net / "ctl.commands").read_text().splitlines()[-1]
    ctl.run("ssh", "uptime")
    assert (ctl.net / "ctl.commands").read_text().splitlines()[-1] == "uptime"
    ctl.run("status", CLOUDSDK_CORE_PROJECT="somebody-elses-production")
    assert (ctl.net / "ctl.log").read_text().splitlines()[-1].startswith("siriusmedai|")        # the study's project, not the operator's default


def test_the_controller_project_is_its_own_setting_and_never_the_campaign_projects(ctl):
    ctl.run("status", P3_GCP_PROJECT="the-campaign-project")
    assert (ctl.net / "ctl.log").read_text().splitlines()[-1].startswith("siriusmedai|")                  # P3_GCP_PROJECT is the campaign tools' variable
    ctl.run("status", P3_CONTROLLER_PROJECT="p3-controller-abc123", P3_GCP_PROJECT="the-campaign-project")
    assert (ctl.net / "ctl.log").read_text().splitlines()[-1].startswith("p3-controller-abc123|compute ssh " + CONTROLLER_NAME)


@pytest.mark.parametrize("name, zone, message", [
    ("siriusmed-prod-1", "us-central1-a", "not a p3-controller-"),
    ("p3-campaign-cgcp-l1-owner-ejiod-expires-20261118t0300z", "us-east4-c", "not a p3-controller-"),
    ("p3-controller-x;rm -rf /", "us-central1-a", "not a p3-controller-"),
    (CONTROLLER_NAME, "somewhere", "does not look like a zone"),
    ("", "us-central1-a", "set P3_CONTROLLER"),
])
def test_the_helper_refuses_any_instance_that_is_not_the_controller(ctl, name, zone, message):
    done = ctl.run("status", P3_CONTROLLER=name, P3_CONTROLLER_ZONE=zone)
    assert done.returncode == 2 and message in done.stderr and not (ctl.net / "ctl.log").exists()


def test_pull_copies_one_launch_and_checks_it_against_its_manifest(ctl):
    dest = make_launch_tar(ctl)
    done = ctl.run("pull", "h100", "1", dest)
    assert done.returncode == 0, done.stderr
    assert "verified: every file of h100 launch 1 matches LAUNCH-SHA256SUMS" in done.stdout
    assert (dest / "rerun" / "paper3" / "h100" / "launch1" / "rerun" / "paper3-h100-l1" / "status.json").exists()
    command = (ctl.net / "ctl.commands").read_text()
    assert "--exclude=.incoming" in command and "paper3/h100/launch1" in command and "rm " not in command and "mv " not in command    # read-only on the controller


def test_pull_fails_on_a_damaged_copy_and_on_a_launch_that_is_not_finished(ctl):
    damaged = ctl.run("pull", "h100", "1", make_launch_tar(ctl, tamper=True))
    assert damaged.returncode == 1 and "differs from LAUNCH-SHA256SUMS" in damaged.stderr and "verified" not in damaged.stdout
    unfinished = ctl.run("pull", "h100", "1", make_launch_tar(ctl, sums=False))            # same destination: the manifest of the earlier copy must not vouch for this one
    assert unfinished.returncode == 2 and "not finished" in unfinished.stderr
    for args in (("pull", "h100"), ("pull", "../etc", "1"), ("pull", "h100", "3"), ("pull", "h100;ls", "1")):
        assert ctl.run(*args).returncode == 2


def test_ledger_copies_the_records_into_a_private_directory(ctl, tmp_path):
    src = tmp_path / "led"
    (src / "p3ledger").mkdir(parents=True)
    (src / "p3ledger" / "ledger.jsonl").write_text('{"event": "launch"}\n')
    subprocess.run(["tar", "-cf", str(ctl.net / "ledger.tar"), "-C", str(src), "p3ledger"], check=True)
    dest = tmp_path / "mac-ledger"
    done = ctl.run("ledger", dest)
    assert done.returncode == 0 and (dest / "ledger.jsonl").read_text() == '{"event": "launch"}\n' and "keep them private" in done.stdout


def test_start_and_stop_manage_only_the_driver_unit_of_a_valid_lease_name(ctl):
    lease = "p3-campaign-h100-l1-owner-ejiod-expires-20261124T1400Z"
    done = ctl.run("start", lease, "--fresh")
    assert done.returncode == 0, done.stderr
    command = (ctl.net / "ctl.commands").read_text().splitlines()[-1]
    assert f"/etc/p3/driver-{lease}.env" in command and f"systemctl enable --now p3-driver@{lease}.service" in command and "P3_DRIVER_ARGS" in command and "--fresh" in command
    assert "terminate" not in command and "delete" not in command
    stopped = ctl.run("stop", lease)
    assert stopped.returncode == 0 and "the instance itself was not touched" in stopped.stdout
    assert f"systemctl disable --now p3-driver@{lease}.service" in (ctl.net / "ctl.commands").read_text().splitlines()[-1]
    calls = len((ctl.net / "ctl.commands").read_text().splitlines())
    for args in (("start", "siriusmed-prod"), ("start", "p3-x;reboot"), ("start", lease, "--fresh; rm -rf /"), ("start", lease, "$(id)"), ("stop",), ("stop", "../x")):
        assert ctl.run(*args).returncode == 2
    assert len((ctl.net / "ctl.commands").read_text().splitlines()) == calls                # none of the refused calls reached the controller


def test_snapshots_copies_the_price_snapshots_read_only(ctl, tmp_path):
    src = tmp_path / "snaps"
    (src / "paper3" / "snapshots" / "20261123T0600Z-h100-l1").mkdir(parents=True)
    (src / "paper3" / "snapshots" / "20261123T0600Z-h100-l1" / "prices.json").write_text("{}")
    subprocess.run(["tar", "-cf", str(ctl.net / "launch.tar"), "-C", str(src), "paper3"], check=True)
    done = ctl.run("snapshots", tmp_path / "out")
    assert done.returncode == 0 and (tmp_path / "out" / "rerun" / "paper3" / "snapshots" / "20261123T0600Z-h100-l1" / "prices.json").exists()
    assert "paper3/snapshots" in (ctl.net / "ctl.commands").read_text() and " rm " not in (ctl.net / "ctl.commands").read_text()


# ---- the units -----------------------------------------------------------------------------------------------------------------

def unit(name):
    """{section: [(key, value), ...]} of a systemd unit file (repeated keys kept)."""
    sections, current = {}, None
    for raw in (CONTROLLER / name).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), [])
        else:
            key, _, value = line.partition("=")
            current.append((key.strip(), value.strip()))
    return sections


def values(sections, section, key):
    return [v for k, v in sections[section] if k == key]


def test_the_driver_unit_restarts_crashes_but_never_a_deliberate_exit_of_the_driver():
    service = unit("p3-driver@.service")
    never = {int(c) for c in values(service, "Service", "RestartPreventExitStatus")[0].split()}
    lines = (TOOLS / "p3_driver.sh").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("# Exit codes:"))
    block = []
    for line in lines[start:]:
        if not line.startswith("#"):
            break
        block.append(line.lstrip("# ").rstrip())
    deliberate = {int(m) for m in re.findall(r"(?:Exit codes: |; |^)(\d+) ", "\n".join(block), flags=re.M)} - {0}
    assert deliberate == {2, 10, 11, 20, 21, 30, 31} and never == deliberate               # a new exit code of the driver must be classified here
    assert values(service, "Service", "Restart") == ["on-failure"] and values(service, "Service", "User") == ["p3ops"]
    assert values(service, "Install", "WantedBy") == ["multi-user.target"]               # enabled: it comes back after a reboot of the controller
    assert int(values(service, "Unit", "StartLimitBurst")[0]) >= 3                        # a crash loop is bounded


def test_the_units_run_the_repositorys_scripts_as_the_campaign_user_never_as_root():
    for name in ("p3-driver@.service", "p3-reaper.service"):
        service = unit(name)["Service"]
        assert ("User", "p3ops") in service and ("NoNewPrivileges", "yes") in service
        exec_start = dict(service)["ExecStart"].split()[0]
        assert exec_start.startswith("/home/p3ops/study/tools/paper3_campaign/")
        assert (REPO / exec_start.removeprefix("/home/p3ops/study/")).is_file() and os.access(REPO / exec_start.removeprefix("/home/p3ops/study/"), os.X_OK)
    assert "--terminate" in values(unit("p3-reaper.service"), "Service", "ExecStart")[0].split()
    assert values(unit("p3-reaper.service"), "Unit", "ConditionPathExists") == ["/home/p3ops/.config/lambda/api_key"]    # idle until the key is in place


def test_the_reaper_timer_survives_a_reboot_and_runs_often_enough():
    timer = unit("p3-reaper.timer")
    assert values(timer, "Timer", "OnBootSec") and values(timer, "Timer", "OnUnitActiveSec") == ["5min"]
    assert values(timer, "Install", "WantedBy") == ["timers.target"]


# ---- the bootstrap script ------------------------------------------------------------------------------------------------------

SETUP = TOOLS / "p3_controller_setup.sh"


def test_the_setup_dry_run_shows_every_step_and_changes_nothing(tmp_path):
    done = subprocess.run([BASH, str(SETUP), "--dry-run"], capture_output=True, text=True, env={**os.environ, "P3_COMMIT": COMMIT, "HOME": str(tmp_path)})
    assert done.returncode == 0, done.stderr
    out = done.stdout
    for fragment in ("apt-get install", "useradd --create-home", "install -d -m 0700", "git checkout --detach " + COMMIT, "p3-reaper.timer", "p3-driver@.service"):
        assert fragment in out
    assert out.count("DRY RUN") >= 12 and "enable --now p3-reaper.timer" in out and "enable --now p3-driver" not in out    # driver units are enabled per launch, by a person


def test_the_setup_refuses_a_bad_commit_and_unknown_options_and_a_non_root_real_run():
    bad = subprocess.run([BASH, str(SETUP), "--dry-run"], capture_output=True, text=True, env={**os.environ, "P3_COMMIT": "main"})
    assert bad.returncode == 2 and "40-character" in bad.stderr
    assert subprocess.run([BASH, str(SETUP), "--nope"], capture_output=True, text=True).returncode == 2
    if os.getuid() != 0:
        real = subprocess.run([BASH, str(SETUP)], capture_output=True, text=True, env={**os.environ, "P3_COMMIT": COMMIT})
        assert real.returncode == 2 and "run as root" in real.stderr


def test_the_setup_script_holds_no_secret_and_never_reads_the_keys():
    text = SETUP.read_text()
    assert not re.search(r"\bcat\b[^\n]*(api_key|lambda_key22)", text) and "BEGIN" not in text and "PRIVATE KEY" not in text
    assert "mode $mode" in text and "600|400" in text                                 # it reports the key files' modes, never their contents


# ---- the document ------------------------------------------------------------------------------------------------------------------

DOC = REPO / "docs" / "PAPER3_CONTROLLER.md"


@pytest.mark.skipif(not DOC.exists(), reason="the document is written last")
def test_the_document_names_files_and_knobs_that_exist():
    text = DOC.read_text()
    for path in sorted(set(re.findall(r"`((?:tools|tests|docs)/[A-Za-z0-9_./@-]+?)`", text))):
        assert (REPO / path).exists() or "*" in path, f"{path} is named in the document but does not exist"
    code = "".join(p.read_text(errors="replace") for p in list(TOOLS.rglob("*.sh")) + list(TOOLS.rglob("*.py")) + list(TOOLS.rglob("*.service")))
    for knob in sorted(set(re.findall(r"\bP3_[A-Z0-9_]+\b", text))):
        assert knob in code, f"{knob} is named in the document but nothing in tools/paper3_campaign reads it"


@pytest.mark.skipif(not DOC.exists(), reason="the document is written last")
def test_the_documents_controller_name_follows_the_lease_convention_and_the_commands_touch_only_p3_resources():
    text = DOC.read_text()
    names = set(re.findall(r"p3-controller-owner-ejiod-expires-[0-9]{8}t[0-9]{4}z", text))
    assert names
    for name in names:
        assert len(name) <= 63 and probe_power.lease_status(name, now=datetime(2026, 11, 23, tzinfo=timezone.utc))["valid"]
    blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.S)
    variables = {}
    for block in blocks:
        for m in re.finditer(r"^([A-Z_]+)=(\S+)$", block, flags=re.M):
            variables[m.group(1)] = m.group(2).strip('"\'')
    mutating = re.compile(r"gcloud (?:compute (?:instances|disks|networks|networks subnets|firewall-rules|snapshots)|projects|billing projects) "
                          r"(?:create|delete|link|unlink|stop|start|update|set-scheduling|resize|add-metadata|remove-metadata|attach-disk|detach-disk|"
                          r"add-labels|add-iam-policy-binding)\s+(\S+)")
    seen = 0
    for block in blocks:
        for m in mutating.finditer(block):
            target = m.group(1).strip('"\'')
            target = variables.get(target[1:], target) if target.startswith("$") else target
            assert target.startswith("p3-"), f"a gcloud command in the document targets {m.group(1)!r}, which is not a p3- resource"
            seen += 1
    assert seen >= 4
    create = next(b for b in blocks if "instances create" in b and "controller" in b.lower())
    for required in ("--no-service-account", "--no-scopes", "enable-oslogin=TRUE", "--network=", "--shielded-secure-boot"):
        assert required in create
    assert "--service-account" not in create.replace("--no-service-account", "") and "--scopes" not in create


def test_the_runbooks_generated_gcp_block_shows_the_hardening_and_the_controller_doc_does_not_contradict_it():
    runbook = (REPO / "docs" / "PAPER3_RUNBOOK.md").read_text()
    block = re.search(r"<!-- p3-gen: launch c-gcp 1 [^>]*-->\n```bash\n(.*?)```", runbook, flags=re.S).group(1)
    assert "--no-service-account" in block and "--no-scopes" in block and "--metadata=block-project-ssh-keys=TRUE" in block
    assert "--service-account" not in block.replace("--no-service-account", "")


def test_the_timeline_includes_b200_at_the_gate_and_it_ends_inside_the_a100s_launches():
    t = P.timeline()
    rows = {r["host"]: r for r in t["rows"]}
    assert rows["b200"]["launch1_start_h"] == t["pathfinder_gate_hours"] and rows["b200"]["launch2_start_h"] > rows["b200"]["launch1_end_h"]
    assert rows["b200"]["launch2_end_h"] < rows["a100"]["launch2_end_h"] == t["all_done_hours"]            # B200 does not lengthen the campaign
    runbook = (REPO / "docs" / "PAPER3_RUNBOOK.md").read_text()
    assert re.search(r"^\| b200 \| 7\.57 \|", runbook, flags=re.M) and f"peak spend rate ${t['peak_usd_per_hour']}/h" in runbook


def test_the_runbook_treats_b200_as_admitted_and_its_command_declares_no_scope_of_its_own():
    runbook = (REPO / "docs" / "PAPER3_RUNBOOK.md").read_text()
    for stale in ("(conditional", "only after admission", "do not exist yet", "refused by the planner", "if admitted"):
        assert stale not in runbook, stale
    block = re.search(r"<!-- p3-gen: command b200 1 -->\n```bash\n(.*?)```", runbook, flags=re.S).group(1)
    assert "--paper3-host b200" in block and "--gpu-energy-scope" not in block                                  # HOSTS["b200"].scope is "board" in paper3_campaign.py


@pytest.mark.parametrize("host, launch", [(h, l) for h in P.PLANS for l in ("1", "2") if not (l == "2" and P.PLANS[h].launches == 1)])
def test_no_campaign_command_declares_a_gpu_energy_scope_the_host_class_takes_it_from_the_host_table(host, launch):
    assert "--gpu-energy-scope" not in P.campaign_args(host, launch)
    assert (P.pc.HOSTS[host].scope is None) is False                      # every host class declares one ("none" for the CPU hosts), B200's "board" since amendment A2


def test_a_host_class_without_a_declared_scope_is_refused_not_given_a_guess(monkeypatch):
    import dataclasses
    monkeypatch.setitem(P.pc.HOSTS, "b200", dataclasses.replace(P.pc.HOSTS["b200"], scope=None))
    with pytest.raises(P.PlanError, match="declares no GPU energy scope"):
        P.campaign_args("b200", "1")


# ---- the second copy in the bucket and the partial pull ----------------------------------------------------------------------------

FAKE_GCLOUD_BUCKET = r'''#!/usr/bin/env python3
"""fake gcloud storage: `rsync` copies a tree into $FAKE_BUCKET/<path under gs://bucket/> honouring --exclude; `ls --long` lists it like the real one."""
import os, re, shutil, sys
from pathlib import Path
net, bucket = Path(os.environ["FAKE_NET"]), Path(os.environ["FAKE_BUCKET"])
with open(net / "bucket.log", "a") as log:
    log.write(os.environ.get("CLOUDSDK_CORE_PROJECT", "unset") + "|" + " ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if args[:2] == ["storage", "rsync"]:
    if os.environ.get("FAKE_RSYNC_RC"):
        sys.exit(int(os.environ["FAKE_RSYNC_RC"]))
    exclude = next((a.split("=", 1)[1] for a in args if a.startswith("--exclude=")), None)
    src, dst = [a for a in args[2:] if not a.startswith("--")][-2:]
    target = bucket / dst.split("/", 3)[3]
    drop = os.environ.get("FAKE_BUCKET_DROP")
    for path in sorted(Path(src).rglob("*")):
        rel = path.relative_to(src).as_posix()
        if path.is_dir() or (exclude and re.search(exclude, rel)) or (drop and rel.endswith(drop)):
            continue
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target / rel)
    sys.exit(0)
if args[:2] == ["storage", "ls"]:
    url = [a for a in args if a.startswith("gs://")][0].removesuffix("/**")
    root = bucket / url.split("/", 3)[3]
    for path in sorted(root.rglob("*")):
        if path.is_file():
            print(f"{path.stat().st_size:>12}  2026-11-22T22:00:00Z  gs://{url.split('/')[2]}/{path.relative_to(bucket).as_posix()}")
    print(f"TOTAL: objects, bytes")
    sys.exit(0)
sys.exit("fake gcloud storage: unsupported " + " ".join(args))
'''
ARCHIVE_PREFIX = "gs://siriusmedai-gpu-benchmark-archive/gpu-spark-polars-study/paper3-campaign"


@pytest.fixture
def archive(world, tmp_path):
    (world.bin / "gcloud").write_text(FAKE_GCLOUD_BUCKET)
    (world.bin / "gcloud").chmod(0o755)
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    world.env.update(FAKE_BUCKET=str(bucket))
    base = tmp_path / "base"
    launch_dir = base / "rerun" / "paper3" / "h100" / "launch1"
    (launch_dir / "rerun" / "paper3-h100-l1" / "round-1").mkdir(parents=True)
    (launch_dir / ".incoming").mkdir()
    (launch_dir / "rerun" / "paper3-h100-l1" / "round-1" / "gates.json").write_text('{"verdict": "pass"}\n')
    (launch_dir / "launch-record.json").write_text("{}\n")
    (launch_dir / ".incoming" / "round-1.tar.gz").write_text("a duplicate archive that must not be uploaded\n")
    (launch_dir / ".driver.pid").write_text("123\n")
    (launch_dir / "driver-20261123T060000Z.log").write_text("driver log\n")
    world.base, world.launch_dir, world.bucket = base, launch_dir, bucket
    world.finish = lambda: subprocess.run("shasum -a 256 rerun/paper3-h100-l1/round-1/gates.json launch-record.json > LAUNCH-SHA256SUMS", shell=True, cwd=launch_dir, check=True)
    world.run = lambda *args, **env: sh(world, TOOLS / "p3_archive.sh", "h100", "1", "--dir", base, *args, **env)
    return world


def bucket_files(world):
    return sorted(p.relative_to(world.bucket).as_posix() for p in world.bucket.rglob("*") if p.is_file())


def test_archive_dry_run_says_what_it_would_do_and_uploads_nothing(archive):
    archive.finish()
    done = archive.run()
    assert done.returncode == 0, done.stderr
    assert "DRY RUN" in done.stdout and f"{ARCHIVE_PREFIX}/h100/launch1" in done.stdout and "verified against LAUNCH-SHA256SUMS (finished launch)" in done.stdout
    assert "4 file(s)" in done.stdout                                                   # gates.json, launch-record.json, the sums, the driver log; not .incoming, not .driver.pid
    assert not (archive.net / "bucket.log").exists() and bucket_files(archive) == []


def test_archive_uploads_one_launch_under_the_paper3_prefix_and_verifies_the_bucket(archive):
    archive.finish()
    done = archive.run("--yes")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "uploaded and verified: 4 object(s)" in done.stdout
    assert bucket_files(archive) == ["gpu-spark-polars-study/paper3-campaign/h100/launch1/" + n for n in
                                     ("LAUNCH-SHA256SUMS", "driver-20261123T060000Z.log", "launch-record.json", "rerun/paper3-h100-l1/round-1/gates.json")]
    calls = (archive.net / "bucket.log").read_text()
    assert calls.splitlines()[0].startswith("siriusmedai|storage rsync --recursive --exclude=")
    assert "--delete" not in calls and "rm" not in calls.replace("--recursive", "")          # nothing is ever removed from the bucket
    assert set(l.split("|")[0] for l in calls.splitlines()) == {"siriusmedai"}                # the study's project, not the operator's default


def test_archive_fails_loudly_when_the_bucket_does_not_hold_what_the_disk_holds(archive):
    archive.finish()
    done = archive.run("--yes", FAKE_BUCKET_DROP="gates.json")
    assert done.returncode == 1 and "NOT verified" in done.stderr and "3 object(s)" in done.stderr
    failed = archive.run("--yes", FAKE_RSYNC_RC="1")
    assert failed.returncode == 1 and "the upload failed" in failed.stderr and "uploaded and verified" not in failed.stdout


def test_archive_refuses_anything_that_does_not_verify_or_is_not_ours_to_write(archive):
    archive.finish()
    for prefix in ("gs://siriusmedai-gpu-benchmark-archive/gpu-spark-polars-study/results", "gs://siriusmedai-gpu-benchmark-archive", "s3://elsewhere/paper3-campaign",
                   "gs://b/paper3-campaign-evil", "gs://b/x;rm -rf /paper3-campaign"):
        refused = archive.run("--yes", P3_ARCHIVE_URL=prefix)
        assert refused.returncode == 2 and "paper3-campaign" in refused.stderr, prefix
    (archive.launch_dir / "rerun" / "paper3-h100-l1" / "round-1" / "gates.json").write_text('{"verdict": "tampered"}\n')
    damaged = archive.run("--yes")
    assert damaged.returncode == 2 and "does not match its LAUNCH-SHA256SUMS" in damaged.stderr
    for args in (("h100",), ("nope", "1"), ("h100", "3")):
        assert sh(archive, TOOLS / "p3_archive.sh", *args, "--dir", archive.base).returncode == 2
    assert not (archive.net / "bucket.log").exists() and bucket_files(archive) == []         # nothing reached the bucket in any refused case


def test_archive_of_a_running_launch_needs_verified_packs_and_says_so(archive):
    none = archive.run("--yes")
    assert none.returncode == 2 and "has not verified anything there yet" in none.stderr
    subprocess.run("shasum -a 256 rerun/paper3-h100-l1/round-1/gates.json > MANIFEST-round-1.sha256", shell=True, cwd=archive.launch_dir, check=True)
    done = archive.run("--yes")
    assert done.returncode == 0, done.stderr
    assert "verified against 1 pack manifest(s) (launch still running or not finished)" in done.stdout
    (archive.launch_dir / "rerun" / "paper3-h100-l1" / "round-1" / "gates.json").write_text("changed after the pack was verified\n")
    assert archive.run("--yes").returncode == 2


def make_running_tar(world, tamper=False, manifest=True, dest="dest"):
    base = world.tmp / "tarbase"
    shutil.rmtree(base, ignore_errors=True)
    launch_dir = base / "paper3" / "h100" / "launch1"
    (launch_dir / "rerun" / "paper3-h100-l1" / "round-1").mkdir(parents=True)
    (launch_dir / "rerun" / "paper3-h100-l1" / "round-1" / "gates.json").write_text('{"verdict": "pass"}\n')
    if manifest:
        subprocess.run("shasum -a 256 rerun/paper3-h100-l1/round-1/gates.json > MANIFEST-round-1.sha256", shell=True, cwd=launch_dir, check=True)
    if tamper:
        (launch_dir / "rerun" / "paper3-h100-l1" / "round-1" / "gates.json").write_text('{"verdict": "tampered"}\n')
    subprocess.run(["tar", "-cf", str(world.net / "launch.tar"), "-C", str(base), "paper3"], check=True)
    return world.tmp / dest


def test_a_partial_pull_takes_the_packs_copied_so_far_and_checks_each_manifest(ctl):
    dest = make_running_tar(ctl)
    refused = ctl.run("pull", "h100", "1", dest)
    assert refused.returncode == 2 and "has not finished" in refused.stderr and "--partial" in refused.stderr
    done = ctl.run("pull", "h100", "1", dest, "--partial")
    assert done.returncode == 0, done.stderr
    assert "verified (partial): every file of the 1 pack(s)" in done.stdout and "the launch is not finished" in done.stdout
    assert (dest / "rerun" / "paper3" / "h100" / "launch1" / "MANIFEST-round-1.sha256").exists()
    command = (ctl.net / "ctl.commands").read_text().splitlines()[-1]
    assert command.endswith("; [ $? -le 1 ]") and "--exclude=.incoming" in command                      # a log that grows while it is read is accepted, a real tar failure is not
    assert "; [ $? -le 1 ]" not in (ctl.net / "ctl.commands").read_text().splitlines()[0]               # the full pull above does not accept it


def test_a_partial_pull_fails_on_a_damaged_pack_and_on_a_launch_without_any(ctl):
    damaged = ctl.run("pull", "h100", "1", make_running_tar(ctl, tamper=True), "--partial")
    assert damaged.returncode == 1 and "differs from MANIFEST-round-1.sha256" in damaged.stderr and "verified" not in damaged.stdout
    empty = ctl.run("pull", "h100", "1", make_running_tar(ctl, manifest=False, dest="dest2"), "--partial")
    assert empty.returncode == 2 and "has not copied and verified a pack" in empty.stderr
