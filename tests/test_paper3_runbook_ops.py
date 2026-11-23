"""The operator scripts of tools/paper3_campaign/: launcher, teardown, GCP watchdog, bring-up script on a fake host, and the driver.

No cloud, no GPU, no study query. `ssh`, `scp`, `gcloud`, `curl`, `sudo`, `systemctl`, `git` and the probes' `lambda.sh` are fakes that
run what they are told on this machine, against a fake host home; the fake ssh returns only when every process holding its pipes is
gone, like the real one, so a start command that does not detach would block. The fake `run_campaign.py` is a script the fake planner
prints as the stage script: it makes the files and exit codes a real round leaves, with the exit code of each invocation set by a file
on the fake host (so a round can be interrupted once, fail its gates twice, or have its lease refused).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools" / "paper3_campaign"
PROBES = REPO / "tools" / "paper3_probes"
BASH = shutil.which("bash")
for path in (str(REPO), str(TOOLS)):
    if path not in sys.path:
        sys.path.insert(0, path)
import p3_plan as P  # noqa: E402

pytestmark = pytest.mark.skipif(any(shutil.which(t) is None for t in ("bash", "jq", "perl", "shasum", "python3")), reason="bash, jq, perl, shasum and python3 are needed")
CANARY = "987654.321"

# What both fake transports run on the fake host. A stage START returns only once the stage is visible (running or finished), like a host whose process
# table answers promptly: without it the driver's "no exit code and nothing running on three consecutive polls" rule can read a stage that is slow to appear as
# died, which made the interrupted-round test depend on scheduling (FAKE_START_WAIT=0 switches the wait off, to reproduce that). Every completed STATUS poll is
# counted in polls.log, so a stage can wait for the driver's polls (await-polls) instead of for the clock.
HOST_RUN = r'''
run_on_host() {
  env HOME="$FAKE_HOST_HOME" bash -c "$1"; local rc=$?
  case "$1" in
    *' STATUS '*) echo "$1" >> "$FAKE_NET/polls.log" ;;
    *'p3_detach.sh" START '*)
      if [ "${FAKE_START_WAIT:-1}" = 1 ]; then
        local stage; stage=$(printf '%s' "$1" | sed -n 's/.*p3_detach.sh" START \([A-Za-z0-9._-]*\).*/\1/p')
        for _ in $(seq 1 400); do
          [ -f "$FAKE_HOST_HOME/p3out/$stage.rc" ] && break
          HOME="$FAKE_HOST_HOME" pgrep -f "p3_detach.sh RUN $stage( |\$)" >/dev/null 2>&1 && break      # the host's own view of its process table
          sleep 0.025
        done
      fi ;;
  esac
  return $rc
}
'''

FAKE_SSH = r'''#!/bin/bash
@HOST_RUN@
n="$FAKE_NET"
echo "$(date +%s) ssh" >> "$n/calls.log"
if [ -f "$n/down_until" ] && [ "$(date +%s)" -lt "$(cat "$n/down_until")" ]; then echo "ssh: connect to host: Connection refused" >&2; exit 255; fi
if [ -f "$n/fail_calls" ] && [ "$(cat "$n/fail_calls")" -gt 0 ]; then echo $(( $(cat "$n/fail_calls") - 1 )) > "$n/fail_calls"; echo "ssh: connection timed out" >&2; exit 255; fi
while [ $# -gt 0 ]; do case "$1" in -i|-o|-p|-l|-F|-J|-L|-R) shift 2 ;; -*) shift ;; *) break ;; esac; done
shift
cmd="$*"
run_on_host "$cmd"; exit $?
'''.replace("@HOST_RUN@", HOST_RUN)

FAKE_SCP = r'''#!/bin/bash
n="$FAKE_NET"
echo "$(date +%s) scp" >> "$n/calls.log"
if [ -f "$n/down_until" ] && [ "$(date +%s)" -lt "$(cat "$n/down_until")" ]; then echo "scp: Connection refused" >&2; exit 255; fi
args=()
while [ $# -gt 0 ]; do case "$1" in -i|-o|-P|-F|-J|-l) shift 2 ;; -*) shift ;; *) args+=("$1"); shift ;; esac; done
dst="${args[$(( ${#args[@]} - 1 ))]}"
unset 'args[$(( ${#args[@]} - 1 ))]'
host_path() { local p=$1; p=${p#*:}; case "$p" in "~/"*) p=${p#"~/"} ;; esac; case "$p" in /*) echo "$p" ;; *) echo "$FAKE_HOST_HOME/$p" ;; esac; }
if [[ "$dst" == *:* && "$dst" != /* ]]; then
  for s in "${args[@]}"; do cp -R "$s" "$(host_path "$dst")" || exit 1; done
else
  for s in "${args[@]}"; do
    cp -R "$(host_path "$s")" "$dst" || { echo "scp: $s: No such file or directory" >&2; exit 1; }
    if [ -f "$n/corrupt_next_tar" ] && [[ "$s" == *.tar.gz ]]; then rm -f "$n/corrupt_next_tar"; printf 'garbage' >> "$dst/$(basename "$s")"; fi
    if [ -f "$n/tamper_next_tar" ] && [[ "$s" == *.tar.gz ]]; then rm -f "$n/tamper_next_tar"; python3 "$n/tamper.py" "$dst/$(basename "$s")" "$n/tampered_sha"; fi
    if [ -f "$n/tampered_sha" ] && [[ "$s" == *.tar.gz.sha256 ]]; then printf '%s  %s\n' "$(cat "$n/tampered_sha")" "$(basename "$s" .sha256)" > "$dst/$(basename "$s")"; rm -f "$n/tampered_sha"; fi
  done
fi
'''

FAKE_GCLOUD = r'''#!/bin/bash
@HOST_RUN@
n="$FAKE_NET"
echo "$*" >> "$n/gcloud.log"
echo "${CLOUDSDK_CORE_PROJECT:-unset}" >> "$n/gcloud.project"
case "$1 $2 $3" in
  "compute instances create") [ "${GCLOUD_CREATE_RC:-0}" = 0 ] && touch "$n/instance"; exit "${GCLOUD_CREATE_RC:-0}" ;;
  "compute disks describe") echo '{"sizeGb": "800", "type": "https://www.googleapis.com/compute/v1/projects/p/zones/z/diskTypes/hyperdisk-balanced", "provisionedIops": "16000", "provisionedThroughput": "1000"}'; exit 0 ;;
  "compute instances describe") case "$*" in *creationTimestamp*) echo "2026-11-17T06:01:00.000-08:00" ;; *) echo 7777 ;; esac; exit 0 ;;
  "compute instances list")
    if [ -f "$n/list_fails" ] && [ "$(cat "$n/list_fails")" -gt 0 ]; then echo $(( $(cat "$n/list_fails") - 1 )) > "$n/list_fails"; echo "ERROR: transient" >&2; exit 1; fi
    if [ -f "$n/instance" ] && [[ "$*" == *"labels.project=paper3-energy"* ]]; then echo "$FAKE_INSTANCE_NAME"; fi; exit 0 ;;
  "compute instances delete") rm -f "$n/instance"; echo "deleted" >> "$n/deletes.log"; exit 0 ;;
  "compute ssh "*|"compute scp "*) ;;
  *) echo "fake gcloud: unsupported $*" >&2; exit 2 ;;
esac
sub=$2; shift 2
if [ "$sub" = ssh ]; then
  echo "$(date +%s) ssh" >> "$n/calls.log"
  cmd=""
  while [ $# -gt 0 ]; do case "$1" in --command) cmd="$2"; shift 2 ;; *) shift ;; esac; done
  run_on_host "$cmd"; exit $?
fi
echo "$(date +%s) scp" >> "$n/calls.log"
args=()
while [ $# -gt 0 ]; do case "$1" in --*) shift ;; *) args+=("$1"); shift ;; esac; done
dst="${args[$(( ${#args[@]} - 1 ))]}"
unset 'args[$(( ${#args[@]} - 1 ))]'
host_path() { local p=${1#*:}; case "$p" in "~/"*) p=${p#"~/"} ;; esac; case "$p" in /*) echo "$p" ;; *) echo "$FAKE_HOST_HOME/$p" ;; esac; }
if [[ "$dst" == *:* && "$dst" != /* ]]; then
  for s in "${args[@]}"; do cp -R "$s" "$(host_path "$dst")" || exit 1; done
else
  for s in "${args[@]}"; do cp -R "$(host_path "$s")" "$dst" || exit 1; done
fi
'''.replace("@HOST_RUN@", HOST_RUN)

FAKE_CURL = r'''#!/bin/bash
# fake curl for the Lambda API: logs argv, checks that the key arrived through -K (never on argv), answers by path
n="$FAKE_NET"
echo "$*" >> "$n/curl.argv"
url="" cfg=""
while [ $# -gt 0 ]; do case "$1" in -K) cfg=$2; shift 2 ;; -X|-H|-d) shift 2 ;; -*) shift ;; *) url=$1; shift ;; esac; done
[ -n "$cfg" ] && grep -q '^user = "' "$cfg" && echo keyfile-ok >> "$n/curl.keyfile"
case "$url" in
  */instance-operations/launch) cat "${FAKE_LAUNCH_RESPONSE:-$n/launch_response}" ;;
  */instance-types) cat "$n/instance_types" ;;
  *) echo "<html>page $url</html>" ;;
esac
'''

FAKE_LAMBDA_SH = r'''#!/bin/bash
# fake lambda.sh: list shows the instance until terminate was called $LISTS_AFTER_TERMINATE times; wait writes the address
n="$FAKE_NET"
echo "$*" >> "$n/lambda.calls"
case "$1" in
  list) if [ -f "$n/instance" ]; then
          if [ -f "$n/terminated" ] && [ "$(cat "$n/terminated")" -le 0 ]; then rm -f "$n/instance"; exit 0; fi
          [ -f "$n/terminated" ] && echo $(( $(cat "$n/terminated") - 1 )) > "$n/terminated"
          printf '%s\tp3-campaign-x\tactive\tgpu\tregion\n' "$FAKE_INSTANCE_ID"
        fi ;;
  terminate) echo "${LISTS_AFTER_TERMINATE:-1}" > "$n/terminated"; echo '[]' ;;
  wait) mkdir -p "${P3_STATE_DIR}"; echo 10.0.0.1 > "${P3_STATE_DIR}/$2.ip"; echo "ssh ready" ;;
esac
'''

FAKE_SUDO = r'''#!/bin/bash
if [ "$1" = tee ] && [ "$2" = /etc/paper3-lease ]; then shift; exec tee "$FAKE_ETC/paper3-lease"; fi
exec "$@"
'''

FAKE_SYSTEMCTL = r'''#!/bin/bash
echo "$*" >> "$FAKE_NET/systemctl.log"
case "$1" in
  list-unit-files) for u in $FAKE_UNITS; do [ "$u" = "$2" ] && { echo "$u enabled enabled"; exit 0; }; done; exit 1 ;;
  mask) exit 0 ;;
  list-timers) for u in $FAKE_UNITS; do case "$u" in *.timer) echo "n/a n/a n/a n/a $u $u.service" ;; esac; done ;;
esac
'''

FAKE_GIT = r'''#!/bin/bash
# fake git on the host for the config sync: records the checkout, answers rev-parse and status
echo "$*" >> "$FAKE_NET/git.log"
case "$1" in
  fetch) exit 0 ;;
  checkout) echo "$3" > "$FAKE_NET/git.head"; exit 0 ;;
  rev-parse) cat "$FAKE_NET/git.head" ;;
  status) exit 0 ;;
esac
'''


TAMPER = r"""
import gzip, hashlib, io, sys, tarfile
path, out = sys.argv[1], sys.argv[2]
buffer = io.BytesIO()
with tarfile.open(path, "r:gz") as source, tarfile.open(fileobj=buffer, mode="w") as target:
    for member in source.getmembers():
        data = source.extractfile(member).read() if member.isfile() else None
        if member.isfile() and not member.name.startswith("MANIFEST"):
            data = data + b" tampered"           # a file whose bytes differ from the manifest the host wrote
            member.size = len(data)
        target.addfile(member, io.BytesIO(data) if data is not None else None)
blob = gzip.compress(buffer.getvalue(), mtime=0)
open(path, "wb").write(blob)
open(out, "w").write(hashlib.sha256(blob).hexdigest())
"""


@pytest.fixture
def world(tmp_path):
    """A fake host home, fake cloud tools first on PATH, the operator's ledger and a launch record."""
    host, net, bin_dir, mac = tmp_path / "host", tmp_path / "net", tmp_path / "bin", tmp_path / "mac"
    for directory in (host, net, bin_dir, mac / ".ssh", mac / ".config" / "lambda", tmp_path / "etc", tmp_path / "state"):
        directory.mkdir(parents=True)
    for name, text in (("ssh", FAKE_SSH), ("scp", FAKE_SCP), ("gcloud", FAKE_GCLOUD), ("curl", FAKE_CURL), ("sudo", FAKE_SUDO),
                       ("systemctl", FAKE_SYSTEMCTL), ("git", FAKE_GIT)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    (tmp_path / "lambda.sh").write_text(FAKE_LAMBDA_SH)
    (tmp_path / "lambda.sh").chmod(0o755)
    (mac / ".ssh" / "lambda_key22").write_text("fake")
    (mac / ".config" / "lambda" / "api_key").write_text("SECRET-API-KEY-DO-NOT-LOG\n")
    (net / "launch_response").write_text(json.dumps({"data": {"instance_ids": ["inst-001"]}}))
    (net / "tamper.py").write_text(TAMPER)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(mac), "FAKE_HOST_HOME": str(host), "FAKE_NET": str(net),
           "FAKE_ETC": str(tmp_path / "etc"), "FAKE_INSTANCE_ID": "inst-001", "P3_LEDGER_DIR": str(tmp_path / "ledger"),
           "P3_LAMBDA_SH": str(tmp_path / "lambda.sh"), "P3_STATE_DIR": str(tmp_path / "state"), "P3_NO_CAFFEINATE": "1",
           "P3_SKIP_PUSH_CHECK": "1", "P3_POLL_SECONDS": "0.3", "P3_RETRY_PAUSE": "0.1", "P3_SSH_BOUND": "5", "P3_SCP_BOUND": "10",
           "P3_PACK_FETCH_BOUND": "20", "P3_PACK_TIMEOUT": "30", "P3_SSH_WAIT_PAUSE": "0.2", "P3_TERMINATE_POLL": "0.2", "P3_TERMINATE_WAIT": "10",
           "P3_STAGE_TIMEOUT": "60", "LC_ALL": "C"}
    (host / "ctl").mkdir()
    yield type("World", (), {"host": host, "net": net, "env": env, "tmp": tmp_path, "mac": mac, "bin": bin_dir, "ledger": tmp_path / "ledger"})
    subprocess.run(["pkill", "-f", str(tmp_path)], capture_output=True)


def wait_for(path, seconds=10):
    deadline = time.time() + seconds
    while not path.exists() and time.time() < deadline:
        time.sleep(0.05)
    return path


def sh(world, script, *args, timeout=120, **env):
    return subprocess.run([BASH, str(script), *map(str, args)], capture_output=True, text=True, timeout=timeout,
                          env={**world.env, **{k: str(v) for k, v in env.items()}})


def snapshot(world, age_s=60, lambda_types=("gpu_1x_h100_sxm5",)):
    directory = world.tmp / "snap"
    directory.mkdir(exist_ok=True)
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (directory / "prices.json").write_text(json.dumps({"snapshot_utc": stamp, "lambda": {t: {"price_usd_per_hour": 4.89, "regions_with_capacity": ["us-south-2"]} for t in lambda_types}}))
    return directory


# ---- the launcher --------------------------------------------------------------------------------------------------------------

def launch(world, *args, **env):
    return sh(world, TOOLS / "p3_launch.sh", *args, **env)


COMMIT = "b" * 40
NOW_ARG = "2026-11-17T06:00:00Z"


def test_without_yes_the_launcher_prints_the_request_and_calls_nothing(world):
    done = launch(world, "h100", "1", "--place", "us-south-2", "--commit", COMMIT, "--now", "2026-11-17T06:00:00Z")
    assert done.returncode == 0 and "DRY RUN: nothing was launched" in done.stdout
    assert "p3-campaign-h100-l1-owner-ejiod-expires-20261118T1400Z" in done.stdout and '"instance_type_name": "gpu_1x_h100_sxm5"' in done.stdout
    assert not (world.net / "curl.argv").exists() and not (world.net / "gcloud.log").exists() and not world.ledger.exists()


def test_a_lambda_launch_records_the_instance_arms_the_watchdog_and_keeps_the_key_off_argv(world):
    watchdog = world.tmp / "wd.sh"
    watchdog.write_text('#!/bin/bash\necho "$@" > "$FAKE_NET/watchdog.args"\nexec sleep 30\n')
    watchdog.chmod(0o755)
    done = launch(world, "h100", "1", "--place", "us-south-2", "--commit", COMMIT, "--price-snapshot", snapshot(world), "--yes", "--now", NOW_ARG,
                  P3_LAMBDA_WATCHDOG=watchdog)
    assert done.returncode == 0, done.stdout + done.stderr
    record = json.loads(next(world.ledger.glob("p3-campaign-h100-l1-*.json")).read_text())
    assert record["instance_id"] == "inst-001" and record["provider"] == "lambda" and record["region"] == "us-south-2" and record["zone"] is None
    assert record["price_usd_h"] == 4.89 and record["credit_funded"] == "yes" and record["campaign_commit"] == COMMIT
    assert record["lease_name"] == "p3-campaign-h100-l1-owner-ejiod-expires-20261118T1400Z" and record["terminate_confirmed_utc"] is None
    body = [line for line in (world.net / "curl.argv").read_text().splitlines() if "instance-operations/launch" in line][0]
    assert record["lease_name"] in body and "gpu_1x_h100_sxm5" in body and "Key22" in body
    assert "SECRET-API-KEY" not in (world.net / "curl.argv").read_text() + done.stdout + done.stderr           # the key travels on stdin (-K), not argv
    assert (world.net / "curl.keyfile").read_text().strip() == "keyfile-ok"
    seconds = int(record["lease_hours"] * 3600)
    assert wait_for(world.net / "watchdog.args").read_text().split() == ["inst-001", str(seconds)]
    assert record["watchdog_pid"] and subprocess.run(["kill", "-0", str(record["watchdog_pid"])], capture_output=True).returncode == 0
    events = [json.loads(line) for line in (world.ledger / "ledger.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["launch"] and events[0]["lease_name"] == record["lease_name"]
    again = launch(world, "h100", "1", "--place", "us-south-2", "--commit", COMMIT, "--price-snapshot", snapshot(world), "--yes", "--now", NOW_ARG,
                   P3_LAMBDA_WATCHDOG=watchdog)
    assert again.returncode == 2 and "launched already" in again.stderr                    # the same lease is never launched twice
    assert (world.net / "curl.argv").read_text().count("instance-operations/launch") == 1
    subprocess.run(["kill", str(record["watchdog_pid"])], capture_output=True)


@pytest.mark.parametrize("extra, message", [
    (["--commit", "abc"], "40-character"),
    (["--commit", COMMIT, "--price-snapshot", "/nonexistent"], "prices.json"),
])
def test_the_launcher_refuses_a_bad_commit_and_a_missing_snapshot(world, extra, message):
    done = launch(world, "h100", "1", "--place", "us-south-2", *extra, "--yes")
    assert done.returncode == 2 and message in done.stderr and not (world.net / "curl.argv").exists()


def test_the_launcher_refuses_a_stale_snapshot_and_a_type_without_a_price(world):
    stale = launch(world, "h100", "1", "--place", "us-south-2", "--commit", COMMIT, "--price-snapshot", snapshot(world, age_s=4 * 3600), "--yes")
    assert stale.returncode == 2 and "old" in stale.stderr
    other = launch(world, "h100", "1", "--place", "us-south-2", "--commit", COMMIT, "--price-snapshot", snapshot(world, lambda_types=("gpu_1x_gh200",)), "--yes")
    assert other.returncode == 2 and "no Lambda price for gpu_1x_h100_sxm5" in other.stderr
    assert not (world.net / "curl.argv").exists()


def test_a_failed_provider_call_leaves_no_record_and_no_watchdog(world):
    (world.net / "launch_response").write_text(json.dumps({"error": {"code": "instance-operations/launch/insufficient-capacity", "message": "no capacity"}}))
    done = launch(world, "h100", "1", "--place", "us-south-2", "--commit", COMMIT, "--price-snapshot", snapshot(world), "--yes")
    assert done.returncode == 1 and "launch failed" in done.stderr and "insufficient-capacity" in done.stderr
    assert not list(world.ledger.glob("*.json")) and not (world.ledger / "ledger.jsonl").exists()


def test_a_gcp_launch_needs_the_authors_credit_answer_and_uses_the_labelled_command(world):
    watchdog = world.tmp / "gwd.sh"
    watchdog.write_text('#!/bin/bash\necho "$@" > "$FAKE_NET/watchdog.args"\nexec sleep 30\n')
    watchdog.chmod(0o755)
    args = ["c-gcp", "1", "--place", "us-east4-c", "--commit", COMMIT, "--price-snapshot", snapshot(world), "--yes"]
    missing = launch(world, *args, P3_GCP_WATCHDOG=watchdog)
    assert missing.returncode == 2 and "credit-funded" in missing.stderr and not (world.net / "gcloud.log").exists()
    done = launch(world, *args, "--credit-funded", "no", P3_GCP_WATCHDOG=watchdog)
    assert done.returncode == 0, done.stdout + done.stderr
    record = json.loads(next(world.ledger.glob("p3-campaign-cgcp-l1-*.json")).read_text())
    assert record["provider"] == "gcp" and record["zone"] == "us-east4-c" and record["instance_id"] == "7777" and record["credit_funded"] == "no"
    assert record["price_usd_h"] == 12.701568 and record["provider_created"].startswith("2026-11-17T06:01")
    assert record["boot_disk_requested"] == {"size": "800GB", "type": "hyperdisk-balanced", "iops": 16000, "throughput": 1000}
    assert record["boot_disk_actual"] == {"size_gb": 800, "type": "hyperdisk-balanced", "provisioned_iops": "16000", "provisioned_throughput": "1000"}   # what the provider reports
    create = (world.net / "gcloud.log").read_text().splitlines()[0]
    assert create.startswith(f"compute instances create {record['lease_name']} --zone=us-east4-c --machine-type=c3-highmem-192-metal")
    assert "--max-run-duration=21h" in create and "--instance-termination-action=STOP" in create and "labels=project=paper3-energy" in create
    assert wait_for(world.net / "watchdog.args").read_text().split() == [record["lease_name"], "us-east4-c", str(int(record["lease_hours"] * 3600))]
    subprocess.run(["kill", str(record["watchdog_pid"])], capture_output=True)


def test_every_gcloud_call_of_the_tools_runs_in_the_study_project_not_the_operators_default(world, clock):
    record = write_record(world, host="c-gcp", provider="gcp", instance_id="7777", zone="us-east4-c", region=None)
    name = json.loads(record.read_text())["lease_name"]
    launched = launch(world, "c-vm", "1", "--place", "us-east4-a", "--commit", COMMIT, "--price-snapshot", snapshot(world), "--credit-funded", "no", "--yes",
                      CLOUDSDK_CORE_PROJECT="somebody-elses-production", P3_GCP_WATCHDOG="/usr/bin/true")
    assert launched.returncode == 0, launched.stderr
    done = sh(world, TOOLS / "p3_teardown.sh", record, "--yes", FAKE_INSTANCE_NAME=name, CLOUDSDK_CORE_PROJECT="somebody-elses-production")
    assert done.returncode == 0, done.stderr
    watchdog = sh(world, TOOLS / "p3_gcp_watchdog.sh", NAME, "us-east4-c", 10, FAKE_INSTANCE_NAME=NAME, CLOUDSDK_CORE_PROJECT="somebody-elses-production")
    assert watchdog.returncode == 0
    projects = set((world.net / "gcloud.project").read_text().split())
    assert projects == {"siriusmedai"} and len((world.net / "gcloud.project").read_text().split()) >= 6
    override = sh(world, TOOLS / "p3_teardown.sh", record, "--yes", FAKE_INSTANCE_NAME=name, P3_GCP_PROJECT="another-study-project")
    assert "another-study-project" in (world.net / "gcloud.project").read_text()


def test_a_gcp_create_error_launches_nothing_and_records_nothing(world):
    done = launch(world, "c-vm", "1", "--place", "us-east4-a", "--commit", COMMIT, "--price-snapshot", snapshot(world), "--credit-funded", "no", "--yes", GCLOUD_CREATE_RC=1)
    assert done.returncode == 1 and "launch failed" in done.stderr and not list(world.ledger.glob("*.json"))


# ---- the teardown --------------------------------------------------------------------------------------------------------------

def write_record(world, host="h100", launch="1", **extra):
    spec = P.lease_spec(host, launch, datetime.now(timezone.utc))
    record = {"format": 1, "host": host, "launch": int(launch), "provider": "lambda", "instance_type": "gpu_1x_h100_sxm5", "region": "us-south-2", "zone": None,
              "instance_id": "inst-001", "lease_name": spec["lease_name"], "launch_utc": "2026-11-17T06:01:00Z", "expires_utc": spec["expires_utc"],
              "lease_hours": spec["lease_hours"], "price_usd_h": 4.89, "price_snapshot_utc": "2026-11-17T05:40:00Z", "credit_funded": "yes",
              "campaign_commit": COMMIT, "ip": "10.0.0.1", "watchdog_pid": None, "terminate_requested_utc": None, "terminate_confirmed_utc": None, **extra}
    world.ledger.mkdir(exist_ok=True)
    path = world.ledger / f"{record['lease_name']}.json"
    path.write_text(json.dumps(record))
    (world.net / "instance").write_text("up")
    return path


def test_teardown_terminates_only_the_recorded_instance_and_waits_for_the_listing_to_clear(world):
    record = write_record(world)
    sleeper = subprocess.Popen(["sleep", "60"])
    data = json.loads(record.read_text())
    data["watchdog_pid"] = sleeper.pid
    record.write_text(json.dumps(data))
    dry = sh(world, TOOLS / "p3_teardown.sh", record)
    assert dry.returncode == 0 and "DRY RUN" in dry.stdout and not (world.net / "lambda.calls").exists()
    done = sh(world, TOOLS / "p3_teardown.sh", record, "--yes", LISTS_AFTER_TERMINATE=2)
    assert done.returncode == 0 and "confirmed terminated at" in done.stdout, done.stdout + done.stderr
    calls = (world.net / "lambda.calls").read_text().splitlines()
    assert calls[0] == "terminate inst-001" and calls.count("list") >= 3                       # polled until the listing no longer showed it
    final = json.loads(record.read_text())
    assert final["terminate_requested_utc"] and final["terminate_confirmed_utc"] and final["terminate_requested_utc"] <= final["terminate_confirmed_utc"]
    assert [json.loads(l)["event"] for l in (world.ledger / "ledger.jsonl").read_text().splitlines()] == ["terminate-requested", "terminate-confirmed"]
    time.sleep(0.2)
    assert sleeper.poll() is not None                                                      # the watchdog was stopped
    sleeper.kill()


def test_teardown_that_cannot_confirm_says_so_and_keeps_the_watchdog(world):
    record = write_record(world)
    sleeper = subprocess.Popen(["sleep", "60"])
    data = json.loads(record.read_text())
    data["watchdog_pid"] = sleeper.pid
    record.write_text(json.dumps(data))
    done = sh(world, TOOLS / "p3_teardown.sh", record, "--yes", LISTS_AFTER_TERMINATE=1000, P3_TERMINATE_WAIT=1)
    assert done.returncode == 1 and "NOT CONFIRMED" in done.stderr and json.loads(record.read_text())["terminate_confirmed_utc"] is None
    assert sleeper.poll() is None                                                          # still armed
    sleeper.kill()


def test_gcp_teardown_deletes_by_exact_name_only_while_the_instance_carries_our_label(world):
    record = write_record(world, host="c-gcp", provider="gcp", instance_id="7777", zone="us-east4-c", region=None)
    name = json.loads(record.read_text())["lease_name"]
    done = sh(world, TOOLS / "p3_teardown.sh", record, "--yes", FAKE_INSTANCE_NAME=name)
    assert done.returncode == 0, done.stdout + done.stderr
    log = (world.net / "gcloud.log").read_text().splitlines()
    assert any(l.startswith(f"compute instances delete {name} --zone=us-east4-c") for l in log)
    assert all("labels.project=paper3-energy" in l for l in log if " list " in l)
    gone = write_record(world, host="c-gcp", launch="2", provider="gcp", instance_id="7778", zone="us-east4-c", region=None)
    (world.net / "instance").unlink()
    (world.net / "gcloud.log").write_text("")
    again = sh(world, TOOLS / "p3_teardown.sh", gone, "--yes", FAKE_INSTANCE_NAME="whatever")
    assert again.returncode == 0 and "already gone" in again.stdout and "delete" not in (world.net / "gcloud.log").read_text()


def test_a_listing_that_keeps_failing_never_confirms_a_termination(world):
    record = write_record(world, host="c-gcp", provider="gcp", instance_id="7777", zone="us-east4-c", region=None)
    (world.net / "list_fails").write_text("100000")
    done = sh(world, TOOLS / "p3_teardown.sh", record, "--yes", FAKE_INSTANCE_NAME="x", P3_TERMINATE_WAIT=1)
    assert done.returncode == 1 and "NOT CONFIRMED" in done.stderr and json.loads(record.read_text())["terminate_confirmed_utc"] is None


def test_a_transient_gcp_listing_failure_is_never_read_as_gone(world):
    record = write_record(world, host="c-gcp", provider="gcp", instance_id="7777", zone="us-east4-c", region=None)
    name = json.loads(record.read_text())["lease_name"]
    (world.net / "list_fails").write_text("2")
    done = sh(world, TOOLS / "p3_teardown.sh", record, "--yes", FAKE_INSTANCE_NAME=name)
    assert done.returncode == 0 and "listing failed; deleting anyway by exact name" in done.stderr
    assert json.loads(record.read_text())["terminate_confirmed_utc"] is not None


# ---- the GCP watchdog ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def clock(world):
    fake = world.tmp / "clock"
    fake.mkdir()
    (fake / "now.sh").write_text('#!/bin/bash\ncat "$FAKE_CLOCK/clock"\n')
    (fake / "sleep.sh").write_text('#!/bin/bash\necho "$1" >> "$FAKE_CLOCK/sleeps.log"\necho $(( $(cat "$FAKE_CLOCK/clock") + ${1%.*} + ${JUMP:-0} )) > "$FAKE_CLOCK/clock"\n')
    for f in ("now.sh", "sleep.sh"):
        (fake / f).chmod(0o755)
    (fake / "clock").write_text("1000000\n")
    world.env.update(FAKE_CLOCK=str(fake), P3_WATCHDOG_NOW=str(fake / "now.sh"), P3_WATCHDOG_SLEEP=str(fake / "sleep.sh"), P3_STATE_DIR=str(world.tmp / "wstate"))
    return fake


NAME = "p3-campaign-cgcp-l1-owner-ejiod-expires-20261118t0300z"


def test_the_campaign_watchdog_deletes_at_the_wall_clock_deadline_through_the_campaign_label(world, clock):
    (world.net / "instance").write_text("up")
    done = sh(world, TOOLS / "p3_gcp_watchdog.sh", NAME, "us-east4-c", 100, FAKE_INSTANCE_NAME=NAME, JUMP=20000)
    assert done.returncode == 0 and "deadline_epoch=1000100" in done.stdout and "deleted" in done.stdout
    assert "asleep or suspended" in done.stdout                                           # the clock jumped inside one sleep
    log = (world.net / "gcloud.log").read_text()
    assert "labels.project=paper3-energy" in log and "power-probe" not in log
    assert (world.net / "deletes.log").exists() and not (world.net / "instance").exists()


def test_the_campaign_watchdog_refuses_names_that_are_not_ours_and_retries_failed_listings(world, clock):
    refused = sh(world, TOOLS / "p3_gcp_watchdog.sh", "production-db", "us-east4-c", 100)
    assert refused.returncode == 1 and "not named p3-" in refused.stderr and not (world.net / "gcloud.log").exists()
    (world.net / "instance").write_text("up")
    (world.net / "list_fails").write_text("3")
    done = sh(world, TOOLS / "p3_gcp_watchdog.sh", NAME, "us-east4-c", 10, FAKE_INSTANCE_NAME=NAME)
    assert done.returncode == 0 and done.stderr.count("list query failed") == 3 and (world.net / "deletes.log").exists()   # never read as "already deleted"
    (world.net / "gcloud.log").write_text("")
    (world.net / "deletes.log").unlink()
    absent = sh(world, TOOLS / "p3_gcp_watchdog.sh", NAME, "us-east4-c", 10, FAKE_INSTANCE_NAME=NAME)
    assert absent.returncode == 0 and "already deleted" in absent.stdout and not (world.net / "deletes.log").exists()


def test_the_probe_watchdog_would_have_silently_skipped_a_campaign_host(world, clock):
    """Why the twin exists: the probe watchdog filters on purpose=paper3-power-probe, which a campaign instance does not carry."""
    (world.net / "instance").write_text("up")
    done = sh(world, PROBES / "gcp_watchdog.sh", NAME, "us-east4-c", 10, FAKE_INSTANCE_NAME=NAME)
    assert done.returncode == 0 and "already deleted" in done.stdout and (world.net / "instance").exists()


# ---- the bring-up script, run on a fake host against a local origin -------------------------------------------------------------

STUBS = {
    "setup_h100.sh": '#!/usr/bin/env bash\nmkdir -p rerun/setup-logs\necho "$@" > rerun/setup-logs/args.txt\n[ "${FAKE_SETUP_RC:-0}" = 0 ] || exit "$FAKE_SETUP_RC"\n'
                     'printf \'export DATA_ROOT=%s/data\\npython() { python3 "$@"; }\\n\' "$PWD" > rerun/cpu-env.sh\n'
                     'cp rerun/cpu-env.sh rerun/h100-env.sh; echo "export RAPIDS_JAR=$PWD/jars/rapids.jar" >> rerun/h100-env.sh\n'
                     'echo "export PYTHON_GPU_ENV=$(command -v python3)" >> rerun/h100-env.sh\n',
    "gpu_pulse.py": "import json, os, sys\nfails = int(os.environ.get('FAKE_PULSE_FAILS', '0'))\nif fails:\n    print(json.dumps({'ok': False, 'error': 'RuntimeError: cuInit returned CUDA error 802'})); sys.exit(1)\nprint(json.dumps({'ok': True}))\n",
    "tools/paper3_campaign/p3_cuda_ready.sh": (REPO / "tools" / "paper3_campaign" / "p3_cuda_ready.sh").read_text(),
    "generate_data.py": "import sys, pathlib\nroot = pathlib.Path(sys.argv[sys.argv.index('--root') + 1])\n(root / 'sf1').mkdir(parents=True, exist_ok=True)\n(root / 'sf1' / 'manifest.json').write_text('{}')\n",
    "generate_large_data.py": "import sys, pathlib\nroot = pathlib.Path(sys.argv[sys.argv.index('--root') + 1])\n(root / ('sf' + sys.argv[sys.argv.index('--scales') + 1])).mkdir(parents=True, exist_ok=True)\n",
    "verify_generated_inputs.py": "import sys, pathlib\nreport = pathlib.Path(sys.argv[sys.argv.index('--report') + 1])\nreport.write_text('{\"result\": \"OK\"}')\n",
    "preflight.py": "import sys, pathlib\nout = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])\nout.mkdir(parents=True)\n(out / 'args.txt').write_text(' '.join(sys.argv[1:]))\n",
    "probe_power.py": "import json, sys\nassert '--no-load' in sys.argv and '--lease-name' in sys.argv\nopen(sys.argv[sys.argv.index('--output') + 1], 'w').write(json.dumps({'probe': 'stub'}))\n",
    "paper3_gates.py": "import json, sys\nassert sys.argv[1] == '--capture-exclusivity'\nprint(json.dumps({'capture': {'label': 'start'}, 'allow_list_items': ['timer:a.timer']}))\n",
}


@pytest.fixture
def origin(tmp_path):
    repo = tmp_path / "origin"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    for name, text in STUBS.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
        (repo / name).chmod(0o755)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "lock"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "tag", "-a", "paper3-design-lock", "-m", "lock"], cwd=repo, check=True, env=env)
    return repo, subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def run_bringup(world, origin_repo, commit, host, monkeypatch, units="apt-daily.timer fstrim.timer cron.service", sysfs=None, **env):
    monkeypatch.setattr(P, "REPO_URL", str(origin_repo))
    monkeypatch.setattr(P, "check_admitted", lambda *a, **k: None)
    spec = P.lease_spec(host, "1", datetime.now(timezone.utc))
    script = world.tmp / "bringup.sh"
    script.write_text(P.bringup_script(host, "1", {"campaign_commit": commit, "lease_name": spec["lease_name"]}))
    bin_dir = world.tmp / "bin2"
    bin_dir.mkdir(exist_ok=True)
    for name, text in (("sudo", FAKE_SUDO), ("systemctl", FAKE_SYSTEMCTL)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    environment = {**world.env, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(world.host), "FAKE_UNITS": units, **{k: str(v) for k, v in env.items()}}
    if sysfs is not None:
        environment["P3_SYS_CPU"] = str(sysfs)
    done = subprocess.run([BASH, str(script)], capture_output=True, text=True, env=environment, timeout=120)
    return done, spec["lease_name"]


def fake_sysfs(root, good=True):
    for cpu in range(0, 13):
        for number, core in ((cpu, cpu), (cpu + 96, cpu if good else cpu + 500)):
            directory = root / f"cpu{number}" / "topology"
            directory.mkdir(parents=True)
            (directory / "physical_package_id").write_text("0\n")
            (directory / "core_id").write_text(f"{core}\n")
    return root


def steps(text):
    return re.findall(r"STEP (\w+) (begin|ok|FAILED rc=\d+)", text)


def test_the_gpu_bringup_runs_every_step_in_order_and_leaves_what_the_driver_collects(world, origin, monkeypatch):
    repo, commit = origin
    done, lease = run_bringup(world, repo, commit, "a100", monkeypatch)
    assert done.returncode == 0, done.stdout + done.stderr
    assert [s for s, what in steps(done.stdout) if what == "ok"] == ["boot_wait", "checkout", "lease", "setup", "cudaready", "hostinfo", "generate", "verify", "preflight", "artifacts"]
    study = world.host / "study"
    assert json.loads((study / "rerun" / "bringup-artifacts" / "cuda-ready.json").read_text())["ok"] is True              # the CUDA gate's record is collected with the artifacts
    assert (world.tmp / "etc" / "paper3-lease").read_text().strip() == lease
    assert (study / "rerun" / "setup-logs" / "args.txt").read_text().strip() == "--envs gpu --expect-gpu A100 --min-free-gib 450"
    assert json.loads((study / "rerun" / "verify-sf1-30-ref.json").read_text())["result"] == "OK" and (study / "rerun" / "verify-sf100-300.json").exists()
    args = (study / "rerun" / "preflight-bringup" / "args.txt").read_text()
    assert "--allow-any-gpu" in args and "--min-free-gib 250" in args and "--skip-inputs" in args
    assert (study / "data" / "sf300").is_dir() and (study / "data" / "sf1" / "manifest.json").exists()
    assert json.loads((study / "rerun" / "bringup-artifacts" / "probe-power.json").read_text()) == {"probe": "stub"}       # hypervisor, sources, clock: evidence per launch
    assert subprocess.run(["git", "-C", str(study), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() == commit
    calls = (world.net / "systemctl.log").read_text().splitlines() if (world.net / "systemctl.log").exists() else []
    assert not [c for c in calls if c.split()[0] in ("mask", "stop", "start", "disable")]                                  # GPU hosts are not quiesced (the CUDA gate only looks: list-unit-files)


def test_a_failing_step_ends_the_stage_with_its_code_and_runs_nothing_after_it(world, origin, monkeypatch):
    repo, commit = origin
    done, _ = run_bringup(world, repo, commit, "h100", monkeypatch, FAKE_SETUP_RC=5)
    assert done.returncode == 5 and "STEP setup FAILED rc=5" in done.stdout
    assert [s for s, what in steps(done.stdout) if what == "begin"] == ["boot_wait", "checkout", "lease", "setup"]
    assert not (world.host / "study" / "rerun" / "verify-sf1-30-ref.json").exists()


def test_a_host_whose_cuda_never_works_ends_the_bringup_with_82_before_any_data_or_engine_work(world, origin, monkeypatch):
    repo, commit = origin
    clock = world.tmp / "clock"
    clock.mkdir()
    (clock / "clock").write_text("1000000\n")
    (clock / "now.sh").write_text('#!/bin/bash\ncat "$FAKE_CLOCK/clock"\n')
    (clock / "sleep.sh").write_text('#!/bin/bash\necho $(( $(cat "$FAKE_CLOCK/clock") + $1 )) > "$FAKE_CLOCK/clock"\n')
    for f in ("now.sh", "sleep.sh"):
        (clock / f).chmod(0o755)
    done, _ = run_bringup(world, repo, commit, "h100", monkeypatch, FAKE_PULSE_FAILS=1, FAKE_CLOCK=clock, P3_CUDA_NOW=clock / "now.sh", P3_CUDA_SLEEP=clock / "sleep.sh")
    assert done.returncode == 82, done.stdout + done.stderr
    assert "STEP cudaready FAILED rc=82" in done.stdout and "[FAIL    ] cuda.ready: host GPU not ready (CUDA 802)" in done.stdout
    assert [s for s, what in steps(done.stdout) if what == "begin"] == ["boot_wait", "checkout", "lease", "setup", "cudaready"]       # nothing after the gate ran
    study = world.host / "study"
    assert not (study / "rerun" / "verify-sf1-30-ref.json").exists() and not (study / "data").exists() and not (study / "rerun" / "preflight-bringup").exists()
    assert json.loads((study / "rerun" / "bringup-artifacts" / "cuda-ready.json").read_text())["reason"] == "host GPU not ready (CUDA 802)"


def test_bringup_refuses_once_the_campaign_has_started_and_a_dirty_or_wrong_checkout(world, origin, monkeypatch):
    repo, commit = origin
    first, _ = run_bringup(world, repo, commit, "h100", monkeypatch)
    assert first.returncode == 0
    root = world.host / "study" / "rerun" / "paper3-h100-l1"
    root.mkdir(parents=True)
    (root / "campaign.json").write_text("{}")
    again, _ = run_bringup(world, repo, commit, "h100", monkeypatch)
    assert again.returncode == 90 and "bring-up must not run again" in again.stdout and "STEP" not in again.stdout
    (root / "campaign.json").unlink()
    (world.host / "study" / "setup_h100.sh").write_text("# edited on the host\n")
    dirty, _ = run_bringup(world, repo, commit, "h100", monkeypatch)
    assert dirty.returncode != 0 and "STEP checkout FAILED" in dirty.stdout                                        # a clean tree is part of the checkout step
    (world.host / "study" / "setup_h100.sh").unlink()
    subprocess.run(["git", "-C", str(world.host / "study"), "checkout", "--", "setup_h100.sh"], check=True, capture_output=True)
    wrong, _ = run_bringup(world, repo, "c" * 40, "h100", monkeypatch)
    assert wrong.returncode != 0 and "STEP checkout FAILED" in wrong.stdout


def test_the_c_gcp_bringup_checks_the_pin_masks_only_existing_timers_and_captures_twice(world, origin, monkeypatch):
    repo, commit = origin
    done, _ = run_bringup(world, repo, commit, "c-gcp", monkeypatch, sysfs=fake_sysfs(world.tmp / "sys"))
    assert done.returncode == 0, done.stdout + done.stderr
    assert [s for s, what in steps(done.stdout) if what == "ok"] == ["boot_wait", "checkout", "lease", "setup", "hostinfo", "generate", "verify",
                                                                        "pincheck", "preflight", "capture_before", "quiesce", "capture_after", "artifacts"]
    assert "pin 0-12,96-108: 13 physical cores plus their siblings, all on socket 0" in done.stdout
    masked = [l.split()[-1] for l in (world.net / "systemctl.log").read_text().splitlines() if l.startswith("mask --now")]
    assert masked == ["apt-daily.timer", "fstrim.timer", "cron.service"]                                          # the units this fake host has, and no others
    assert "absent logrotate.timer" in done.stdout
    out = world.host / "p3out"
    assert json.loads((out / "exclusivity-before.json").read_text())["allow_list_items"] and (out / "exclusivity-after.json").exists()
    study = world.host / "study" / "rerun"
    assert (study / "bringup-artifacts" / "exclusivity-after.json").exists() and (study / "bringup-artifacts" / "exclusivity-before.json").exists()
    assert "--cpu-only --enable-rapl-read --install-power-tools" in (study / "setup-logs" / "args.txt").read_text()
    assert "REVIEW-REQUIRED exclusivity-after.json" in done.stdout


def test_a_host_whose_topology_does_not_match_the_pin_stops_before_any_timer_is_touched(world, origin, monkeypatch):
    repo, commit = origin
    done, _ = run_bringup(world, repo, commit, "c-gcp", monkeypatch, sysfs=fake_sysfs(world.tmp / "sys", good=False))
    assert done.returncode != 0 and "STEP pincheck FAILED" in done.stdout and "are not SMT siblings" in done.stderr
    assert not (world.net / "systemctl.log").exists() and not (world.host / "p3out" / "exclusivity-before.json").exists()


def test_c_vm_generates_only_the_scales_it_runs(world, origin, monkeypatch):
    repo, commit = origin
    done, _ = run_bringup(world, repo, commit, "c-vm", monkeypatch)
    assert done.returncode == 0, done.stdout + done.stderr
    data = world.host / "study" / "data"
    assert (data / "sf300").is_dir() and not (data / "sf100").exists()
    assert (world.host / "study" / "rerun" / "verify-sf300.json").exists() and not (world.host / "study" / "rerun" / "verify-sf100-300.json").exists()


# ---- the driver ----------------------------------------------------------------------------------------------------------------

BRINGUP_TEMPLATE = r'''#!/usr/bin/env bash
rc=0; [ -s "$HOME/ctl/rc-bringup" ] && rc=$(cat "$HOME/ctl/rc-bringup")
mkdir -p "$HOME/study/rerun/setup-logs" "$HOME/study/rerun/preflight-bringup" "$HOME/study/rerun/bringup-artifacts"
echo setup > "$HOME/study/rerun/setup-logs/setup.log"; echo '{"result": "OK"}' > "$HOME/study/rerun/verify-sf1-30-ref.json"
echo '{"allow_list_items": ["timer:a.timer"]}' > "$HOME/study/rerun/bringup-artifacts/exclusivity-after.json"
echo "BRINGUP-OK"; exit $rc
'''

STAGE_TEMPLATE = r'''#!/usr/bin/env bash
set -u
CTL="$HOME/ctl"; ROOT="$HOME/study/@ROOT@"; K=@K@; STOP="@STOP@"
polls() { wc -l < "$FAKE_NET/polls.log" 2>/dev/null || echo 0; }
# The round's timing is driven by what the driver does, never by the clock:
#   outage-calls N   the next N ssh calls fail (an outage that starts with this stage), consumed once
#   await-polls N    stay running until the driver has completed N status polls since this stage began
#   gate             stay running until $CTL/release exists
if [ -f "$CTL/outage-calls" ]; then cp "$CTL/outage-calls" "$FAKE_NET/fail_calls"; rm -f "$CTL/outage-calls"; fi
if [ -f "$CTL/await-polls" ]; then
  want=$(cat "$CTL/await-polls"); base=$(polls)
  for _ in $(seq 1 1200); do [ $(( $(polls) - base )) -ge "$want" ] && break; sleep 0.05; done
fi
if [ -f "$CTL/gate" ]; then for _ in $(seq 1 1200); do [ -f "$CTL/release" ] && break; sleep 0.05; done; fi
[ -f "$CTL/sleep" ] && sleep "$(cat "$CTL/sleep")"
f="$CTL/rc-round-$K"; rc=0
if [ -s "$f" ]; then rc=$(head -n 1 "$f"); tail -n +2 "$f" > "$f.new"; mv "$f.new" "$f"; fi
mkdir -p "$ROOT"
echo '{"fake": "campaign"}' > "$ROOT/campaign.json"; echo "{\"k\": $K, \"stop\": \"$STOP\"}" >> "$ROOT/invocations.jsonl"
mkround() {
  mkdir -p "$1/baseline"
  echo "{\"energy_j\": \"@CANARY@\"}" > "$1/baseline/results.json"; printf 't,p\n0,@CANARY@\n' > "$1/baseline/gpu.csv"
  echo "{\"verdict\": \"$2\", \"reasons\": [], \"design_conformance\": {\"passed\": true, \"problems\": []}, \"preflight\": {\"verdict\": \"$2\"}, \"paper3\": {\"evaluated\": true, \"round_failed\": false, \"round_failures\": [], \"gates\": {\"G6\": {\"pass\": 2, \"fail\": 0, \"not_evaluable\": 0, \"not_applicable\": 0}}, \"ineligible_keys\": 0}}" > "$1/gates.json"
}
state=stopped
case "$rc" in
  0) mkround "$ROOT/round-$K" pass; if [ -n "$STOP" ]; then state=paused; else state=complete; fi ;;
  3) mkround "$ROOT/round-$K" fail; mkround "$ROOT/round-$K-repeat-1" fail ;;
  130) state=interrupted ;;
esac
[ -f "$CTL/stray" ] && echo stray > "$ROOT/stray.txt"
echo "{\"state\": \"$state\", \"message\": \"fake\", \"updated_utc\": \"2026-11-17T12:00:00Z\", \"sessions\": {\"completed\": 1, \"total\": 2}, \"rounds\": {}, \"eta\": {}}" > "$ROOT/status.json"
exit "$rc"
'''

FAKE_PLAN = '''#!/usr/bin/env python3
import json, os, sys
BRINGUP = %r
STAGE = %r
cmd, rest = sys.argv[1], sys.argv[2:]
host, launch = (rest + ["", ""])[:2]
root = "rerun/paper3-%%s-l%%s" %% (host, launch)
rounds = os.environ.get("FAKE_ROUNDS", "1 2").split()
if cmd == "root":
    print(root)
elif cmd == "budget":
    print(json.dumps({"bringup_s": 120, "round_s": 120}))
elif cmd == "rounds":
    print("\\n".join(rounds))
elif cmd == "check-allowlist":
    print(json.dumps({"filled_now": True}))
elif cmd == "bringup":
    print(BRINGUP, end="")
elif cmd == "stage-script":
    stop = rest[rest.index("--stop-after") + 1] if "--stop-after" in rest else ""
    k = stop or rounds[-1]
    print(STAGE.replace("@ROOT@", root).replace("@K@", k).replace("@STOP@", stop).replace("@CANARY@", %r), end="")
else:
    sys.exit("fake plan: " + cmd)
''' % (BRINGUP_TEMPLATE, STAGE_TEMPLATE, CANARY)


@pytest.fixture
def drive(world, monkeypatch):
    plan = world.tmp / "fake_plan.py"
    plan.write_text(FAKE_PLAN)
    world.env.update(P3_PLAN_CMD=f"{sys.executable} {plan}")
    state = {"n": 0}

    def run(record, *args, timeout=240, **env):
        state["n"] += 1
        dest = world.tmp / "dest"
        done = sh(world, TOOLS / "p3_driver.sh", record, "--dest", dest, *args, timeout=timeout, **env)
        done.dest = dest
        logs = sorted(dest.glob("driver-*.log"))
        done.log = "\n".join(p.read_text() for p in logs)
        return done
    run.world = world
    return run


def ctl(world, name, text):
    (world.host / "ctl" / name).write_text(text)


def test_a_launch_runs_from_bringup_to_a_confirmed_teardown_and_prints_no_study_value(drive):
    world = drive.world
    record = write_record(world)
    done = drive(record)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    dest = done.dest
    assert all((dest / ".verified" / m).exists() for m in ("bringup", "round-1", "round-2"))
    root = dest / "rerun" / "paper3-h100-l1"
    assert (root / "round-1" / "gates.json").exists() and (root / "round-2" / "baseline" / "results.json").exists()      # the raw data is copied ...
    assert (dest / "rerun" / "bringup-artifacts" / "exclusivity-after.json").exists() and (dest / "bringup-a1.log").exists()
    assert CANARY not in done.stdout + done.log + done.stderr                                              # ... and never printed
    assert "QA " in done.log and "G6 p2/f0/n0" in done.log and "pack round-1 verified" in done.log and "inventory: the campaign root on the host" in done.log
    scripts = {p.name: p.read_text() for p in (dest / ".stage-scripts").glob("campaign-*.sh")}
    assert set(scripts) == {"campaign-r1-a1.sh", "campaign-r2-a1.sh"}
    assert 'K=1; STOP="1"' in scripts["campaign-r1-a1.sh"] and 'K=2; STOP=""' in scripts["campaign-r2-a1.sh"]       # the last round runs to the end
    for stage in ("bringup-a1", "campaign-r1-a1", "campaign-r2-a1"):
        assert (world.host / "p3out" / f"{stage}.rc").read_text().strip() == "0"
    data = json.loads(record.read_text())
    assert data["ip"] == "10.0.0.1" and data["terminate_requested_utc"] and data["terminate_confirmed_utc"]
    assert "terminate inst-001" in (world.net / "lambda.calls").read_text()
    assert [json.loads(l)["event"] for l in (world.ledger / "ledger.jsonl").read_text().splitlines()] == ["terminate-requested", "terminate-confirmed"]
    manifest = (dest / "LAUNCH-SHA256SUMS").read_text().splitlines()
    assert any("rerun/paper3-h100-l1/round-2/gates.json" in l for l in manifest) and not any(".incoming" in l or "driver-" in l for l in manifest)
    kept = json.loads((dest / "launch-record.json").read_text())                                  # the operator's record travels with the data
    assert kept["terminate_confirmed_utc"] and kept["lease_name"] == data["lease_name"] and "ip" not in kept and "watchdog_pid" not in kept
    assert any(l.endswith("  launch-record.json") for l in manifest)
    assert subprocess.run(["shasum", "-a", "256", "-c", "LAUNCH-SHA256SUMS"], cwd=dest, capture_output=True).returncode == 0
    assert drive(record).stdout.strip().endswith("nothing to drive")                                     # a torn-down launch is not driven again


def test_the_round_copy_happens_while_nothing_runs_on_the_host(drive):
    """Each pack is started only after the round's stage has written its exit code (design 3.2 controls: no other workload)."""
    world = drive.world
    done = drive(write_record(world))
    assert done.returncode == 0
    for k in (1, 2):
        rc_time = (world.host / "p3out" / f"campaign-r{k}-a1.rc").stat().st_mtime
        pack_started = (world.host / "p3out" / f"pack-round-{k}-t1.started").stat().st_mtime
        assert pack_started >= rc_time - 0.01, k
    log = done.log
    assert log.index("stage campaign-r1-a1 finished: 0") < log.index("pack round-1 verified") < log.index("stage campaign-r2-a1 finished: 0")


def test_the_status_view_shows_stages_and_the_qa_view_and_no_study_value(drive):
    world = drive.world
    record = write_record(world)
    done = drive(record, "--no-teardown")
    assert done.returncode == 0
    qa_dir = world.host / "study" / "tools" / "paper3_campaign"
    qa_dir.mkdir(parents=True)
    shutil.copy(TOOLS / "p3_qa_view.py", qa_dir / "p3_qa_view.py")
    status = sh(world, TOOLS / "p3_status.sh", record)
    assert status.returncode == 0, status.stderr
    assert "stage bringup-a1 rc=0" in status.stdout and "stage campaign-r2-a1 rc=0" in status.stdout and "stage pack-round-1-t1 rc=0" in status.stdout
    assert "state complete" in status.stdout and "terminated at:" in status.stdout and CANARY not in status.stdout
    (world.net / "down_until").write_text(str(int(time.time()) + 600))
    down = sh(world, TOOLS / "p3_status.sh", record, P3_RETRY_PAUSE=0.1)
    assert down.returncode == 1 and "did not answer" in down.stderr


def test_a_network_outage_mid_round_costs_polls_not_the_round(drive):
    """The outage begins with the round (the next five ssh calls fail) and the round does not end before the driver has polled twice again."""
    world = drive.world
    ctl(world, "outage-calls", "5")
    ctl(world, "await-polls", "2")
    done = drive(write_record(world))
    assert done.returncode == 0, done.stdout[-2000:]
    assert done.log.count("poll failed") >= 1 and "polling works again after" in done.log


def test_a_corrupted_copy_is_detected_by_the_hash_and_fetched_again(drive):
    world = drive.world
    (world.net / "corrupt_next_tar").write_text("1")
    done = drive(write_record(world))
    assert done.returncode == 0
    assert re.search(r"pack bringup, try 1: archive sha256 differs", done.log) and "pack bringup verified" in done.log
    assert (done.dest / ".verified" / "bringup").exists()


def test_a_file_that_differs_from_the_hosts_manifest_is_caught_even_when_the_archive_hash_matches(drive):
    """The archive hash only proves the copy equals what the host packed; the per-file manifest proves each file equals what the host hashed."""
    world = drive.world
    (world.net / "tamper_next_tar").write_text("1")
    done = drive(write_record(world))
    assert done.returncode == 0
    assert "try 1: a file differs from the manifest" in done.log and "archive sha256 differs" not in done.log and "pack bringup verified" in done.log


def test_an_interrupted_round_is_resumed_and_too_many_interruptions_hold_the_host(drive):
    world = drive.world
    ctl(world, "rc-round-1", "130\n0\n")
    done = drive(write_record(world))
    assert done.returncode == 0 and "round 1: 130; resuming (attempt 2)" in done.log
    assert (world.host / "p3out" / "campaign-r1-a2.rc").read_text().strip() == "0"
    world2 = drive.world
    (world2.net / "instance").write_text("up")
    for path in (world2.host / "study", world2.host / "p3out", world2.host / "p3stage"):
        shutil.rmtree(path)
    shutil.rmtree(done.dest)
    ctl(world2, "rc-round-1", "130\n130\n130\n130\n")
    record = write_record(world2, instance_id="inst-002", ip="10.0.0.1", terminate_confirmed_utc=None)
    held = drive(record, "--max-resume", "1")
    assert held.returncode == 20 and "HELD: round 1 was interrupted 2 times" in held.log
    assert "terminate" not in ((world2.net / "lambda.calls").read_text() if (world2.net / "lambda.calls").exists() else "").replace("terminate inst-001", "")


PGREP_WITH_LAG = r'''#!/bin/bash
# a fake host whose process table lags: a stage's wrapper is not shown until FAKE_PGREP_LAG seconds after its .started directory was made
lag=$(cat "$FAKE_NET/pgrep_lag" 2>/dev/null || echo 0)
stage=$(printf '%s' "$*" | sed -n 's/.*RUN \([A-Za-z0-9._-]*\).*/\1/p')
if [ -n "$stage" ] && [ -d "$HOME/p3out/$stage.started" ]; then
  age=$(perl -e 'print int((time - (stat shift)[9]) * 1000)' "$HOME/p3out/$stage.started")
  [ "$age" -lt "$(( ${lag%.*} * 1000 + $(printf '%s' "${lag#*.}000" | cut -c1-3) ))" ] && exit 1
fi
exec /usr/bin/pgrep "$@"
'''


def test_a_stage_that_is_slow_to_appear_on_the_host_is_never_read_as_died(drive):
    """A loaded host can take a while to show a detached wrapper in its process table. Here the fake host's `pgrep` shows a stage only 1.5 s after it was
    started, and the round is slow enough to be caught by that. The fake host's START returns once the stage is visible, so the driver's three-idle-polls rule
    cannot misfire (the clock only models the lag, it does not decide the outcome). With FAKE_START_WAIT=0 the same run reads the round as died and starts a second one,
    which is what made the interrupted-round test depend on scheduling."""
    world = drive.world
    (world.bin / "pgrep").write_text(PGREP_WITH_LAG)
    (world.bin / "pgrep").chmod(0o755)
    (world.net / "pgrep_lag").write_text("1.5")
    ctl(world, "await-polls", "6")
    ctl(world, "rc-round-1", "130\n0\n")
    done = drive(write_record(world), P3_POLL_SECONDS=0.3)
    assert done.returncode == 0, done.stdout[-2500:]
    assert "round 1: 130; resuming (attempt 2)" in done.log and "died" not in done.log
    assert not (world.host / "p3out" / "campaign-r1-a3.rc").exists()


@pytest.mark.parametrize("code, fragment", [(4, "gates could not be evaluated"), (5, "design reason"), (6, "lease is not valid"), (9, "ended with exit 9")])
def test_exits_that_need_a_person_hold_the_host_and_terminate_nothing(drive, code, fragment):
    world = drive.world
    ctl(world, "rc-round-1", f"{code}\n")
    done = drive(write_record(world))
    assert done.returncode == 20 and "HELD:" in done.log and fragment in done.log
    assert "The host keeps running" in done.log and not (world.net / "lambda.calls").exists()
    assert not (done.dest / ".verified" / "round-1").exists()


def test_a_round_that_fails_its_gates_twice_stops_the_launch_after_copying_everything(drive):
    world = drive.world
    ctl(world, "rc-round-1", "3\n")
    done = drive(write_record(world))
    assert done.returncode == 30, done.stdout[-2000:]
    assert "the campaign stopped by its own rule at round 1" in done.log and "STOPPED by the campaign's own rule" in done.log
    root = done.dest / "rerun" / "paper3-h100-l1"
    assert (root / "round-1" / "gates.json").exists() and (root / "round-1-repeat-1" / "gates.json").exists()
    assert (done.dest / ".verified" / "stopped").exists() and not (done.dest / ".verified" / "round-2").exists()
    assert "terminate inst-001" in (world.net / "lambda.calls").read_text()


def test_on_stop_hold_keeps_the_host_for_diagnosis(drive):
    world = drive.world
    ctl(world, "rc-round-1", "3\n")
    done = drive(write_record(world), "--on-stop", "hold")
    assert done.returncode == 20 and "stopped by rule, --on-stop hold" in done.log and not (world.net / "lambda.calls").exists()


def test_a_hold_is_repeated_on_a_rerun_until_a_person_says_fresh(drive):
    world = drive.world
    ctl(world, "rc-round-1", "6\n")
    record = write_record(world)
    first = drive(record)
    assert first.returncode == 20 and "lease is not valid" in first.log
    again = drive(record)                                                                  # nothing was fixed: attach to the finished failure, start nothing
    assert again.returncode == 20 and "stage campaign-r1-a1 is already on the host (done:6)" in again.log
    assert not (world.host / "p3out" / "campaign-r1-a2.started").exists()
    fresh = drive(record, "--fresh")                                                       # the cause is fixed (the fake's next exit code is 0)
    assert fresh.returncode == 0, fresh.stdout[-2000:]
    assert (world.host / "p3out" / "campaign-r1-a2.rc").read_text().strip() == "0" and (world.host / "p3out" / "campaign-r1-a1.rc").read_text().strip() == "6"


def test_stop_after_bringup_leaves_the_host_up_for_a_manual_step_and_the_next_run_continues(drive):
    world = drive.world
    record = write_record(world)
    first = drive(record, "--stop-after-bringup")
    assert first.returncode == 11 and "bring-up is verified (--stop-after-bringup)" in first.log and (first.dest / ".verified" / "bringup").exists()
    assert not list((world.host / "p3out").glob("campaign-*")) and not (world.net / "lambda.calls").exists()          # nothing started, nothing terminated
    second = drive(record)
    assert second.returncode == 0 and "bring-up" not in second.log.split("=== p3_driver")[-1].split("stage campaign-r1-a1")[0].replace("bring-up is verified", "")
    assert (world.host / "p3out" / "campaign-r1-a1.rc").read_text().strip() == "0"


def test_a_second_driver_for_the_same_launch_is_refused_and_a_stale_pid_file_is_not(drive):
    world = drive.world
    record = write_record(world)
    dest = world.tmp / "dest"
    dest.mkdir()
    sleeper = subprocess.Popen(["sleep", "60"])
    (dest / ".driver.pid").write_text(str(sleeper.pid))
    refused = drive(record)
    assert refused.returncode == 2 and f"another driver (pid {sleeper.pid})" in refused.stderr and not list((world.host / "p3out").glob("*")) 
    sleeper.kill()
    sleeper.wait()
    (dest / ".driver.pid").write_text(str(sleeper.pid))                                      # a dead pid: left behind by a crash
    ok = drive(record, "--stop-after-bringup")
    assert ok.returncode == 11 and not (dest / ".driver.pid").exists()                         # and the lock is released on exit


def test_the_usage_text_is_the_header_of_the_script(world):
    done = sh(world, TOOLS / "p3_driver.sh", "/nonexistent.json")
    assert done.returncode == 2 and "p3_driver.sh RECORD.json" in done.stdout and "--stop-after-bringup" in done.stdout and "set -uo" not in done.stdout


def test_a_failed_bringup_is_retried_under_its_next_attempt_name_with_fresh(drive):
    world = drive.world
    ctl(world, "rc-bringup", "7")
    record = write_record(world)
    assert drive(record).returncode == 20
    ctl(world, "rc-bringup", "0")
    fresh = drive(record, "--fresh")
    assert fresh.returncode == 0 and (world.host / "p3out" / "bringup-a1.rc").read_text().strip() == "7"
    assert (world.host / "p3out" / "bringup-a2.rc").read_text().strip() == "0" and (fresh.dest / ".verified" / "bringup").exists()


def test_a_bringup_that_fails_holds_the_host_and_fetches_its_log(drive):
    world = drive.world
    ctl(world, "rc-bringup", "7")
    done = drive(write_record(world))
    assert done.returncode == 20 and "bring-up ended with 7" in done.log and (done.dest / "bringup-a1.log").exists()
    assert not (world.host / "p3out" / "campaign-r1-a1.started").exists()


def test_a_stage_still_running_at_the_end_of_its_budget_is_held_not_killed_and_the_next_run_attaches(drive):
    world = drive.world
    ctl(world, "gate", "1")                                   # the round cannot finish until the test releases it: no race with the poll budget
    record = write_record(world)
    first = drive(record, P3_ROUND_TIMEOUT=1)
    assert first.returncode == 20 and "still running after its poll budget" in first.log
    assert not (world.host / "p3out" / "campaign-r1-a1.rc").exists()
    ctl(world, "release", "1")
    second = drive(record, P3_ROUND_TIMEOUT=60)
    assert second.returncode == 0, second.stdout[-2000:]
    assert "stage campaign-r1-a1 is already on the host" in second.log
    assert (world.host / "study" / "rerun" / "paper3-h100-l1" / "invocations.jsonl").read_text().count('"k": 1') == 1          # started once, attached to, never restarted


def test_a_file_on_the_host_that_was_never_copied_fails_the_inventory_and_holds(drive):
    world = drive.world
    ctl(world, "stray", "1")
    done = drive(write_record(world))
    assert done.returncode == 20 and "inventory differs" in done.log and not (world.net / "lambda.calls").exists()


def test_the_c_gcp_launch_waits_for_the_allow_list_review_then_syncs_exactly_that_commit(drive):
    world = drive.world
    record = write_record(world, host="c-gcp", provider="gcp", instance_id="7777", zone="us-east4-c", region=None)
    name = json.loads(record.read_text())["lease_name"]
    env = {"FAKE_INSTANCE_NAME": name}
    first = drive(record, **env)
    assert first.returncode == 10 and "REVIEW REQUIRED" in first.log and (first.dest / "rerun" / "bringup-artifacts" / "exclusivity-after.json").exists()
    assert not list((world.host / "p3out").glob("campaign-*")) and not (world.net / "deletes.log").exists()
    bad = drive(record, "--config-commit", "nothex", **env)
    assert bad.returncode == 2
    commit = "d" * 40
    second = drive(record, "--config-commit", commit, **env)
    assert second.returncode == 0, second.stdout[-2000:] + second.stderr[-1500:]
    assert (world.net / "git.head").read_text().strip() == commit and "checkout --detach " + commit in (world.net / "git.log").read_text()
    assert json.loads(record.read_text())["config_commit"] == commit
    assert (second.dest / ".verified" / "config").exists() and (world.net / "deletes.log").exists()                    # GCP teardown by exact name
    first_round = (world.host / "p3out" / "campaign-r1-a1.started")
    assert first_round.exists()
    assert "--config-commit belongs to c-gcp only" in drive(write_record(world, launch="2"), "--config-commit", commit).stderr


def test_a_gcp_launch_uses_gcloud_for_every_host_call(drive):
    world = drive.world
    record = write_record(world, host="c-vm", provider="gcp", instance_id="7778", zone="us-east4-a", region=None)
    name = json.loads(record.read_text())["lease_name"]
    done = drive(record, FAKE_INSTANCE_NAME=name)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-1500:]
    log = (world.net / "gcloud.log").read_text()
    assert f"compute ssh {name} --zone=us-east4-a" in log and f"compute scp --zone=us-east4-a" in log and f"compute instances delete {name} --zone=us-east4-a" in log
    assert "10.0.0.1" not in log                                                                       # no address needed on GCP


# ---- the generated stage script, executed ---------------------------------------------------------------------------------------

def run_stage_script(world, host, launch, stop_after, tmp_path):
    study = world.host / "study"
    (study / "rerun").mkdir(parents=True, exist_ok=True)
    stub_dir = world.tmp / "pybin"
    stub_dir.mkdir(exist_ok=True)
    (stub_dir / "python").write_text('#!/bin/bash\nprintf \'%s\\n\' "$@" > "$HOME/argv.txt"\nprintf \'%s\\n\' "$PWD" > "$HOME/cwd.txt"\n')
    (stub_dir / "python").chmod(0o755)
    env_name = "h100-env.sh" if host in ("a100", "h100", "gh200", "b200") else "cpu-env.sh"
    lines = [f'export PATH="{stub_dir}:$PATH"', 'export DATA_ROOT="/mnt/data with space/data"']
    if env_name == "h100-env.sh":
        lines.append('export RAPIDS_JAR="/jars/rapids.jar"')
    (study / "rerun" / env_name).write_text("\n".join(lines) + "\n")
    record = {"host": host, "launch": int(launch), "provider": P.PLANS[host].provider, "instance_type": P.PLANS[host].instance_type, "region": "us-south-2",
              "zone": "us-east4-c", "instance_id": "i-1", "lease_name": P.lease_spec(host, launch, datetime.now(timezone.utc))["lease_name"],
              "launch_utc": "2026-11-17T06:01:00Z", "price_usd_h": P.PLANS[host].price, "price_snapshot_utc": "2026-11-17T05:40:00Z", "credit_funded": "no"}
    script = tmp_path / "stage.sh"
    script.write_text(P.stage_script(host, launch, stop_after, record))
    done = subprocess.run([BASH, str(script)], capture_output=True, text=True, env={**world.env, "HOME": str(world.host)})
    return done, record, (world.host / "argv.txt").read_text().splitlines() if (world.host / "argv.txt").exists() else None, (world.host / "cwd.txt").read_text().strip()


@pytest.mark.parametrize("host", ["h100", "c-gcp"])
def test_the_stage_script_hands_the_exact_arguments_to_python_after_sourcing_the_hosts_environment(world, tmp_path, host):
    done, record, argv, cwd = run_stage_script(world, host, "1", 2, tmp_path)
    assert done.returncode == 0, done.stderr
    expected = [str(a) for a in P.campaign_args(host, "1", record, 2)][2:]                     # after "python run_campaign.py"
    assert argv[0] == "run_campaign.py" and len(argv) == len(expected) + 1
    mapping = {"$DATA_ROOT": "/mnt/data with space/data", "$RAPIDS_JAR": "/jars/rapids.jar"}
    assert argv[1:] == [mapping.get(a.strip('"'), a) for a in expected]                          # $DATA_ROOT and $RAPIDS_JAR expanded by the host's shell, spaces intact
    assert cwd.endswith("/host/study") and "--stop-after-round" in argv and argv[argv.index("--stop-after-round") + 1] == "2"
    assert ("--cpu-list" in argv) == (host == "c-gcp")
    assert "--lease-name" in argv and argv[argv.index("--lease-name") + 1] == record["lease_name"]


# ---- the scripts and the real planner agree on the command line -----------------------------------------------------------------

def test_every_planner_call_in_the_scripts_is_a_subcommand_the_real_planner_accepts(tmp_path):
    used = set()
    for script in TOOLS.glob("*.sh"):
        used |= set(re.findall(r'\bplan\s+([a-z][a-z-]+)\b', script.read_text()))
    used -= {"on", "and", "the", "is"}
    assert {"root", "budget", "rounds", "bringup", "stage-script", "check-allowlist"} <= used, used           # p3_launch.sh builds launch-spec in an array
    record = tmp_path / "record.json"
    spec = P.lease_spec("h100", "1", datetime.now(timezone.utc))
    record.write_text(json.dumps({"host": "h100", "launch": 1, "provider": "lambda", "instance_type": "gpu_1x_h100_sxm5", "region": "us-south-2", "instance_id": "i-1",
                                  "lease_name": spec["lease_name"], "launch_utc": "2026-11-17T06:01:00Z", "price_usd_h": 4.89,
                                  "price_snapshot_utc": "2026-11-17T05:40:00Z", "credit_funded": "yes", "campaign_commit": "a" * 40}))
    forms = {"root": ["root", "h100", "1"], "budget": ["budget", "h100", "1"], "rounds": ["rounds", "h100", "2"],
             "bringup": ["bringup", "h100", "1", "--record", str(record)], "stage-script": ["stage-script", "h100", "1", "--record", str(record), "--stop-after", "1"],
             "launch-spec": ["launch-spec", "h100", "1", "--place", "us-south-2"]}
    for name, argv in forms.items():
        done = subprocess.run([sys.executable, str(TOOLS / "p3_plan.py"), *argv], capture_output=True, text=True)
        assert done.returncode == 0, (name, done.stderr)
    assert json.loads(subprocess.run([sys.executable, str(TOOLS / "p3_plan.py"), "budget", "h100", "1"], capture_output=True, text=True).stdout).keys() >= {"bringup_s", "round_s"}
    assert subprocess.run([sys.executable, str(TOOLS / "p3_plan.py"), "rounds", "h100", "2"], capture_output=True, text=True).stdout.split() == ["5", "6", "7", "8"]
    parser_choices = subprocess.run([sys.executable, str(TOOLS / "p3_plan.py"), "nosuch"], capture_output=True, text=True).stderr
    for name in used | {"launch-spec"}:
        assert f"'{name}'" in parser_choices, name
