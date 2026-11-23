"""Resuming a failed bring-up (p3_driver.sh --resume-bringup, p3_plan.py bringup --skip) and the 2026-11-23 verify incident that called for it.

All three campaign hosts failed their bring-up at STEP verify because verify_generated_inputs.py read `.sf300.generation.lock` as a leftover of sf30 (a prefix match;
fixed and tested in tests/test_inputs_verify.py). Rerunning the whole bring-up would repeat setup and re-hash 150 GB, and on an A100 the setup step itself would fail the
free-space check (the data now fills the disk it wants free). A resumed attempt skips the steps that finished ok, always re-runs checkout and lease (so a corrected commit takes effect),
and runs the failed step and the ones after it.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools" / "paper3_campaign"
for path in (str(TOOLS), str(REPO / "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

import p3_bringup_view as V  # noqa: E402
import p3_plan as P  # noqa: E402
from test_paper3_runbook_ops import BASH, BRINGUP_TEMPLATE, FAKE_PLAN, ctl, drive, world, write_record  # noqa: E402,F401

NOW = P.utc("2026-11-23T06:00:00Z")


def record_for(host):
    return {"campaign_commit": "a" * 40, "lease_name": P.lease_spec(host, "1", NOW)["lease_name"]}


def run_steps(script):
    return re.findall(r"^run_step (\w+) ", script, flags=re.M)


def skipped(script):
    return re.findall(r'^note "STEP (\w+) skipped', script, flags=re.M)


# ---- the planner ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["a100", "h100", "c-gcp", "c-vm"])
def test_skipped_steps_are_noted_not_run_and_the_rest_keep_their_order(host):
    full = P.bringup_script(host, "1", record_for(host))
    order = run_steps(full)
    done = [s for s in order if s not in ("checkout", "lease", "verify") and order.index(s) < order.index("verify")]
    script = P.bringup_script(host, "1", record_for(host), skip=tuple(done))
    assert skipped(script) == done and run_steps(script) == [s for s in order if s not in done]
    assert run_steps(script)[:2] == ["checkout", "lease"] and "verify" in run_steps(script)                  # checkout and lease always run; the failed step and the rest follow
    assert subprocess.run([BASH, "-n"], input=script, capture_output=True, text=True).returncode == 0
    for name in done:                                                                           # the function is still defined: a later attempt may need it
        assert f"s_{name}()" in script
    assert P.bringup_script(host, "1", record_for(host)) == full                                 # no skip: the script is exactly what it was


def test_checkout_and_lease_cannot_be_skipped_and_unknown_steps_are_refused():
    for bad in (("checkout",), ("lease",), ("nonsense",), ("setup", "cudaready_typo")):
        with pytest.raises(P.PlanError, match="--skip names"):
            P.bringup_script("a100", "1", record_for("a100"), skip=bad)
    with pytest.raises(P.PlanError, match="--skip names"):
        P.bringup_script("c-vm", "1", record_for("c-vm"), skip=("cudaready",))                 # a CPU host has no such step


def test_the_command_line_takes_a_comma_separated_skip_list(tmp_path):
    record = tmp_path / "record.json"
    record.write_text(json.dumps(record_for("a100")))
    done = subprocess.run([sys.executable, str(TOOLS / "p3_plan.py"), "bringup", "a100", "1", "--record", str(record), "--skip", "setup,generate"], capture_output=True, text=True)
    assert done.returncode == 0 and skipped(done.stdout) == ["setup", "generate"] and "verify" in run_steps(done.stdout) and "setup" not in run_steps(done.stdout)
    refused = subprocess.run([sys.executable, str(TOOLS / "p3_plan.py"), "bringup", "a100", "1", "--record", str(record), "--skip", "checkout"], capture_output=True, text=True)
    assert refused.returncode == 2 and "--skip names" in refused.stderr


def test_the_operator_view_shows_a_skipped_step_as_a_step_result_without_its_note():
    log = "2026-11-23T16:00:00Z STEP setup skipped: it finished ok in an earlier attempt of this launch (resume)\n2026-11-23T16:00:01Z STEP verify ok\nVERDICT: pass\n"
    assert V.view(log) == ["STEP setup skipped", "STEP verify ok", "VERDICT: pass"]


# ---- the driver --------------------------------------------------------------------------------------------------------------------------

BRINGUP_VERIFY = r'''#!/usr/bin/env bash
rc=0; [ -s "$HOME/ctl/rc-bringup" ] && rc=$(cat "$HOME/ctl/rc-bringup")
note() { echo "$(date -u +%FT%TZ) $*"; }
mkdir -p "$HOME/study/rerun/setup-logs" "$HOME/study/rerun/preflight-bringup" "$HOME/study/rerun/bringup-artifacts"
echo setup > "$HOME/study/rerun/setup-logs/setup.log"; echo '{"result": "OK"}' > "$HOME/study/rerun/verify-sf1-30-ref.json"
echo '{"allow_list_items": []}' > "$HOME/study/rerun/bringup-artifacts/exclusivity-after.json"
for s in boot_wait checkout lease setup cudaready hostinfo generate; do note "STEP $s begin"; note "STEP $s ok"; done
note "STEP verify begin"
if [ "$rc" != 0 ]; then echo "[FAIL    ] inputs.verify: sf30 disk_vs_new_manifest: partial-write leftovers: .sf300.generation.lock"; note "STEP verify FAILED rc=$rc"; exit "$rc"; fi
note "STEP verify ok"; echo "VERDICT: pass"; exit 0
'''


@pytest.fixture
def resumable(drive):
    world = drive.world
    plan = FAKE_PLAN.replace(repr(BRINGUP_TEMPLATE), repr(BRINGUP_VERIFY)).replace(
        'print(BRINGUP, end="")', 'print(BRINGUP, end=""); print("# PLAN-ARGS: " + " ".join(sys.argv[1:]))')
    assert "PLAN-ARGS" in plan and repr(BRINGUP_VERIFY) in plan
    (world.tmp / "fake_plan.py").write_text(plan)
    world.env.update(FAKE_ROUNDS="1")
    return drive


def plan_args(dest):
    return re.search(r"# PLAN-ARGS: (.*)", (dest / ".stage-scripts" / "bringup.sh").read_text()).group(1)


def test_a_resumed_bringup_skips_what_finished_ok_and_runs_on_to_the_end_of_the_launch(resumable):
    world = resumable.world
    ctl(world, "rc-bringup", "1")
    record = write_record(world)
    first = resumable(record)
    assert first.returncode == 20 and "STEP verify FAILED rc=1" in first.log and "BRING-UP STEP generate ok" in first.log
    assert "--skip" not in plan_args(first.dest)                                            # the first attempt runs everything
    ctl(world, "rc-bringup", "0")                                                           # the cause is fixed (the verifier, here the fake host's)
    again = resumable(record, "--resume-bringup", "--no-teardown")
    assert again.returncode == 0, again.stdout[-2500:] + again.stderr[-1500:]
    assert "resuming the bring-up as attempt 2: not repeating the steps that finished ok in attempt 1: boot_wait,setup,cudaready,hostinfo,generate" in again.log
    assert "--skip boot_wait,setup,cudaready,hostinfo,generate" in plan_args(again.dest) and "checkout" not in plan_args(again.dest).split("--skip")[1]
    assert (world.host / "p3out" / "bringup-a2.rc").read_text().strip() == "0" and (world.host / "p3out" / "bringup-a1.rc").read_text().strip() == "1"        # a new attempt, the old one kept
    assert "BRING-UP VERDICT: pass" in again.log and (again.dest / ".verified" / "bringup").exists() and (again.dest / ".verified" / "round-1").exists()


def test_resume_needs_the_log_of_a_previous_attempt_and_plain_fresh_still_repeats_everything(resumable):
    world = resumable.world
    record = write_record(world)
    none = resumable(record, "--resume-bringup")
    assert none.returncode == 2 and "needs the fetched log of the previous attempt" in none.stderr and not (world.host / "p3out" / "bringup-a1.started").exists()
    ctl(world, "rc-bringup", "1")
    assert resumable(record).returncode == 20
    ctl(world, "rc-bringup", "0")
    plain = resumable(record, "--fresh", "--no-teardown")
    assert plain.returncode == 0 and "--skip" not in plan_args(plain.dest) and "resuming the bring-up" not in plain.log


def test_resuming_twice_keeps_what_the_first_resume_skipped(resumable):
    """Attempt 2 skips the early steps and fails again later; attempt 3 must still skip those (they are `skipped` in attempt 2's log) and not the failed one."""
    world = resumable.world
    ctl(world, "rc-bringup", "1")
    record = write_record(world)
    assert resumable(record).returncode == 20
    second = resumable(record, "--resume-bringup")
    assert second.returncode == 20 and "--skip boot_wait,setup,cudaready,hostinfo,generate" in plan_args(second.dest)
    # a real resumed attempt would print `skipped` lines for those steps; model that in the fetched log of attempt 2
    log2 = second.dest / "bringup-a2.log"
    log2.write_text("".join(f"2026-11-23T16:00:00Z STEP {s} skipped: it finished ok in an earlier attempt of this launch (resume)\n" for s in "boot_wait setup cudaready hostinfo generate".split())
                    + "2026-11-23T16:01:00Z STEP checkout ok\n2026-11-23T16:01:01Z STEP lease ok\n2026-11-23T16:02:00Z STEP verify FAILED rc=1\n")
    ctl(world, "rc-bringup", "0")
    third = resumable(record, "--resume-bringup", "--no-teardown")
    assert third.returncode == 0 and "--skip boot_wait,setup,cudaready,hostinfo,generate" in plan_args(third.dest) and "attempt 3" in third.log
