"""The CUDA-readiness gate (tools/paper3_campaign/p3_cuda_ready.sh) and what the driver does with its verdict.

Smoke run 2 on a Lambda H100 (docs/paper3-evidence/smoke/h100-20261122T232520Z) had a host whose every CUDA call returned error 802
(cudaErrorSystemNotReady, "system not yet initialized") while nvidia-smi and the CPU engines were fine. The gate runs a tiny CUDA init and kernel right after
setup, retries for up to ten minutes, tries the fabricmanager unit once if it exists and is not running, and otherwise fails with exit 82, which the campaign driver
turns into an infrastructure failure before round 1 (host torn down, recorded, exit 31).

No GPU, no cloud: `gpu_pulse.py`, `nvidia-smi`, `systemctl` and `sudo` are fakes, and the clock is a file, so a ten-minute retry window takes no time.
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
GATE = TOOLS / "p3_cuda_ready.sh"
for path in (str(TOOLS), str(REPO / "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

import p3_bringup_view as V  # noqa: E402
from test_paper3_runbook_ops import BASH, FAKE_PLAN, BRINGUP_TEMPLATE, ctl, drive, sh, world, write_record  # noqa: E402,F401

# ---- fakes ---------------------------------------------------------------------------------------------------------------------------

FAKE_PULSE = r'''#!/usr/bin/env python3
"""fake gpu_pulse.py: fails the first FAKE_PULSE_FAILS calls (counted in $FAKE_CUDA/pulse-count), then succeeds; FAKE_PULSE_ERROR is the error text"""
import json, os, sys
from pathlib import Path
root = Path(os.environ["FAKE_CUDA"])
count = int((root / "pulse-count").read_text() or 0) if (root / "pulse-count").exists() else 0
(root / "pulse-count").write_text(str(count + 1))
mode = os.environ.get("FAKE_PULSE_MODE", "")
if mode == "junk":
    print("Segmentation fault (core dumped)"); sys.exit(139)
if os.environ.get("FAKE_PULSE_FIXED_BY_FABRICMANAGER") and (root / "fm-started").exists():
    print(json.dumps({"ok": True, "label": "ready"})); sys.exit(0)
if count < int(os.environ.get("FAKE_PULSE_FAILS", "0")):
    print(json.dumps({"ok": False, "label": "ready", "error": os.environ.get("FAKE_PULSE_ERROR", "RuntimeError: cuInit returned CUDA error 802")}))
    sys.exit(1)
print(json.dumps({"ok": True, "label": "ready", "init_seconds": 0.1}))
'''
FAKE_NVIDIA_SMI = r'''#!/bin/bash
case "$1" in
  --help-query-gpu) echo '"timestamp"'; echo '"power.draw"'; [ "${FAKE_FABRIC_LISTED:-1}" = 1 ] && { echo '"fabric.state"'; echo '"fabric.status"'; }; exit 0 ;;
  --query-gpu=fabric.state,fabric.status) echo "${FAKE_FABRIC_VALUE:-Completed, Success}"; exit 0 ;;
  -q) cat <<'EOS'
GPU 00000000:00:05.0
    Product Name                          : NVIDIA H100 80GB HBM3
    Serial Number                         : 1234567890123
    GPU UUID                              : GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
    PCI
        Bus Id                            : 00000000:00:05.0
    Fabric
        State                             : Completed
        Status                            : Success
        Fabric GUID                       : 0x0123456789abcdef
    Driver Version                        : 570.148.08
EOS
  exit 0 ;;
esac
exit 0
'''
FAKE_SYSTEMCTL_FM = r'''#!/bin/bash
echo "$*" >> "$FAKE_CUDA/systemctl.log"
case "$1" in
  list-unit-files) [ "${FAKE_FM_UNIT:-0}" = 1 ] && [ "$2" = nvidia-fabricmanager.service ] && echo "nvidia-fabricmanager.service enabled enabled"; exit 0 ;;
  is-active) if [ -f "$FAKE_CUDA/fm-started" ]; then echo active; else echo "${FAKE_FM_STATE:-inactive}"; fi; exit 0 ;;
  start) touch "$FAKE_CUDA/fm-started"; exit "${FAKE_FM_START_RC:-0}" ;;
esac
'''
FAKE_SUDO = '#!/bin/bash\nexec "$@"\n'


@pytest.fixture
def host(tmp_path):
    """A fake GPU host: a repository root holding the fake gpu_pulse.py, fakes first on PATH, and a clock that only moves when the gate sleeps."""
    repo, bin_dir, cuda, clock = tmp_path / "repo", tmp_path / "bin", tmp_path / "cuda", tmp_path / "clock"
    for d in (repo, bin_dir, cuda, clock):
        d.mkdir()
    (repo / "gpu_pulse.py").write_text(FAKE_PULSE)
    for name, text in (("nvidia-smi", FAKE_NVIDIA_SMI), ("systemctl", FAKE_SYSTEMCTL_FM), ("sudo", FAKE_SUDO)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    (clock / "clock").write_text("1000000\n")
    (clock / "now.sh").write_text('#!/bin/bash\ncat "$FAKE_CLOCK/clock"\n')
    (clock / "sleep.sh").write_text('#!/bin/bash\necho "$1" >> "$FAKE_CLOCK/sleeps.log"\necho $(( $(cat "$FAKE_CLOCK/clock") + $1 )) > "$FAKE_CLOCK/clock"\n')
    for f in ("now.sh", "sleep.sh"):
        (clock / f).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_CUDA": str(cuda), "FAKE_CLOCK": str(clock),
           "P3_CUDA_NOW": str(clock / "now.sh"), "P3_CUDA_SLEEP": str(clock / "sleep.sh"), "LC_ALL": "C"}

    def run(*args, **extra):
        out = tmp_path / "out"
        return subprocess.run([BASH, str(GATE), "--python", sys.executable, "--out", str(out), *args], cwd=repo, capture_output=True, text=True,
                              env={**env, **{k: str(v) for k, v in extra.items()}}, timeout=60)
    run.out = tmp_path / "out"
    run.cuda, run.clock, run.repo = cuda, clock, repo
    run.sleeps = lambda: (clock / "sleeps.log").read_text().split() if (clock / "sleeps.log").exists() else []
    run.attempts = lambda: [json.loads(l) for l in (tmp_path / "out" / "cuda-ready.jsonl").read_text().splitlines() if '"attempt"' in l and '"ok"' in l]
    run.verdict = lambda: json.loads((tmp_path / "out" / "cuda-ready.json").read_text())
    return run


# ---- the gate ------------------------------------------------------------------------------------------------------------------------

def test_a_ready_host_passes_at_once_and_the_records_are_written_with_identifiers_blanked(host):
    done = host()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "[PASS    ] cuda.ready" in done.stdout and host.sleeps() == []
    assert host.verdict() == {"ok": True, "attempts": 1, "seconds": 0, "fabricmanager": "absent", "fabric": "Completed,Success"}
    assert [a["ok"] for a in host.attempts()] == [True]
    smi = (host.out / "cuda-ready-nvidia-smi-q-start.txt").read_text()
    assert "H100" in smi and "Driver Version" in smi and "570.148.08" in smi
    for identifier in ("1234567890123", "aaaaaaaa-bbbb", "0123456789abcdef", "00000000:00:05.0"):
        assert identifier not in smi, identifier
    assert smi.count("<redacted>") == 4 and smi.count("<bus-id>") == 1 and smi.startswith("GPU <bus-id>")
    assert "Completed,Success" in (host.out / "cuda-ready-fabric.csv").read_text()


def test_a_host_that_says_802_for_a_while_is_retried_every_30_seconds_and_then_passes(host):
    done = host(FAKE_PULSE_FAILS=3)
    assert done.returncode == 0, done.stdout + done.stderr
    assert host.sleeps() == ["30", "30", "30"]                                   # a retry every 30 s: no sleeping before the first attempt, none after the last
    verdict = host.verdict()
    assert verdict["ok"] is True and verdict["attempts"] == 4 and verdict["seconds"] == 90
    attempts = host.attempts()
    assert [(a["ok"], a["code"]) for a in attempts] == [(False, 802), (False, 802), (False, 802), (True, None)]
    assert done.stdout.count("CUDA is not usable (RuntimeError: cuInit returned CUDA error 802)") == 3


def test_a_host_that_never_gets_ready_fails_after_ten_minutes_with_a_clear_reason_and_exit_82(host):
    done = host(FAKE_PULSE_FAILS=10 ** 6)
    assert done.returncode == 82, done.stdout + done.stderr
    assert "[FAIL    ] cuda.ready: host GPU not ready (CUDA 802): RuntimeError: cuInit returned CUDA error 802 after 21 attempts" in done.stdout
    assert host.sleeps() == ["30"] * 20                                          # 10 minutes of wall-clock time, 21 attempts
    verdict = host.verdict()
    assert verdict["ok"] is False and verdict["reason"] == "host GPU not ready (CUDA 802)" and verdict["code"] == 802
    assert verdict["attempts"] == 21 and verdict["seconds"] == 600 and verdict["fabricmanager_started"] is False
    assert (host.out / "cuda-ready-nvidia-smi-q-end.txt").exists() and (host.out / "cuda-ready-nvidia-smi-q-start.txt").exists()
    assert (host.out / "cuda-ready-fabric.csv").read_text().count("Completed,Success") == 2           # at the start and after the last attempt


def test_the_window_and_the_period_are_options(host):
    done = host("--retry-seconds", "10", "--max-seconds", "35", FAKE_PULSE_FAILS=10 ** 6)
    assert done.returncode == 82 and host.sleeps() == ["10"] * 4 and host.verdict()["attempts"] == 5
    for bad in (("--retry-seconds", "soon"), ("--max-seconds", "-5"), ("--bogus",)):
        assert host(*bad).returncode == 2


def test_the_fabricmanager_unit_is_started_once_when_it_exists_and_is_not_running(host):
    done = host(FAKE_PULSE_FAILS=10 ** 6, FAKE_FM_UNIT=1)
    assert done.returncode == 82
    starts = [l for l in (host.cuda / "systemctl.log").read_text().splitlines() if l.startswith("start")]
    assert starts == ["start nvidia-fabricmanager"]                              # once, in 21 attempts
    assert host.verdict()["fabricmanager_started"] is True and host.verdict()["fabricmanager"] == "active"
    lines = [json.loads(l) for l in (host.out / "cuda-ready.jsonl").read_text().splitlines()]
    assert [l["action"] for l in lines if "action" in l] == ["systemctl start nvidia-fabricmanager: ok"] and "fabricmanager" in lines[0]
    assert lines[0]["fabricmanager"] == "inactive" and lines[-1]["fabricmanager"] == "active"


def test_a_host_that_the_fabricmanager_start_fixes_passes_on_the_next_attempt(host):
    done = host(FAKE_PULSE_FAILS=10 ** 6, FAKE_PULSE_FIXED_BY_FABRICMANAGER=1, FAKE_FM_UNIT=1)
    assert done.returncode == 0, done.stdout
    assert host.verdict()["attempts"] == 2 and host.sleeps() == ["30"]


def test_a_running_fabricmanager_is_left_alone_and_an_absent_one_is_not_looked_for(host):
    running = host(FAKE_PULSE_FAILS=2, FAKE_FM_UNIT=1, FAKE_FM_STATE="active")
    assert running.returncode == 0 and "start nvidia-fabricmanager" not in (host.cuda / "systemctl.log").read_text()
    (host.cuda / "pulse-count").unlink()
    (host.cuda / "systemctl.log").unlink()
    absent = host(FAKE_PULSE_FAILS=2, FAKE_FM_UNIT=0)
    assert absent.returncode == 0 and not any(l.startswith("start") for l in (host.cuda / "systemctl.log").read_text().splitlines())
    assert host.attempts()[0]["fabricmanager"] == "absent"


def test_a_failed_fabricmanager_start_is_recorded_and_does_not_end_the_retries(host):
    done = host(FAKE_PULSE_FAILS=1, FAKE_FM_UNIT=1, FAKE_FM_START_RC=1)
    assert done.returncode == 0 and "systemctl start nvidia-fabricmanager: failed" in done.stdout
    assert "systemctl start nvidia-fabricmanager: failed" in (host.out / "cuda-ready.jsonl").read_text()


def test_any_other_cuda_init_failure_is_retried_the_same_way_and_named_by_its_text(host):
    done = host(FAKE_PULSE_FAILS=10 ** 6, FAKE_PULSE_ERROR="OSError: libcuda.so.1: cannot open shared object file")
    assert done.returncode == 82
    assert "[FAIL    ] cuda.ready: host GPU not ready (CUDA unknown): OSError: libcuda.so.1: cannot open shared object file after 21 attempts" in done.stdout
    assert host.verdict()["code"] is None and host.verdict()["reason"] == "host GPU not ready (CUDA unknown)"
    named = host(FAKE_PULSE_FAILS=10 ** 6, FAKE_PULSE_ERROR="CUDARuntimeError: cudaErrorSystemNotReady: system not yet initialized")
    assert named.returncode == 82 and "(CUDA 802)" in named.stdout                # the runtime's own name for 802


def test_a_pulse_that_prints_nothing_usable_counts_as_a_failure_and_a_nonzero_exit_overrides_ok(host):
    done = host(FAKE_PULSE_MODE="junk")
    assert done.returncode == 82 and "no JSON record from gpu_pulse.py" in done.stdout


def test_the_gate_refuses_to_run_without_a_python_or_outside_the_repository_root(host, tmp_path):
    assert subprocess.run([BASH, str(GATE), "--python", "/nonexistent/python"], cwd=host.repo, capture_output=True, text=True).returncode == 2
    elsewhere = subprocess.run([BASH, str(GATE), "--python", sys.executable, "--out", str(tmp_path / "x")], cwd=tmp_path, capture_output=True, text=True)
    assert elsewhere.returncode == 2 and "repository root" in elsewhere.stderr


def test_an_nvidia_smi_without_the_fabric_fields_is_recorded_as_unlisted(host):
    done = host(FAKE_FABRIC_LISTED=0)
    assert done.returncode == 0 and "unlisted" in (host.out / "cuda-ready-fabric.csv").read_text() and host.verdict()["fabric"] == "unlisted"


def test_the_operator_view_of_a_bringup_log_shows_the_failed_gate_and_nothing_of_a_passed_one():
    failed = "2026-11-22T23:31:00Z STEP cudaready begin\n[FAIL    ] cuda.ready: host GPU not ready (CUDA 802): RuntimeError: cuInit returned CUDA error 802 after 21 attempts\n2026-11-22T23:41:30Z STEP cudaready FAILED rc=82\n"
    shown = V.view(failed)
    assert shown[0].startswith("[FAIL    ] cuda.ready: host GPU not ready (CUDA 802)") and "STEP cudaready FAILED rc=82" in shown and "2026-11-22" not in "".join(shown)
    passed = "[PASS    ] cuda.ready: CUDA initialized and a kernel ran (attempt 1)\n2026-11-22T23:31:09Z STEP cudaready ok\n"
    assert V.view(passed)[0] == "STEP cudaready ok" and "[PASS" not in "".join(V.view(passed))
    assert "7777" not in "".join(V.view("[FAIL    ] cuda.ready: x in 7777s\n"))


# ---- the driver on exit 82 of the bring-up ---------------------------------------------------------------------------------------------

BRINGUP_82 = r'''#!/usr/bin/env bash
note() { echo "$(date -u +%FT%TZ) $*"; }
mkdir -p "$HOME/study/rerun/setup-logs" "$HOME/study/rerun/bringup-artifacts"
echo setup > "$HOME/study/rerun/setup-logs/setup.log"
note "STEP setup begin"; echo "Installed 41 packages in 1.2s"; note "STEP setup ok"
note "STEP cudaready begin"
A="$HOME/study/rerun/bringup-artifacts"
echo '{"ok": false, "reason": "host GPU not ready (CUDA 802)", "code": 802, "error": "RuntimeError: cuInit returned CUDA error 802", "attempts": 21, "seconds": 600, "fabricmanager": "absent", "fabricmanager_started": false, "fabric": "Completed,Success"}' > "$A/cuda-ready.json"
echo '{"attempt": 1, "ok": false, "code": 802}' > "$A/cuda-ready.jsonl"; echo "Completed,Success" > "$A/cuda-ready-fabric.csv"
echo "GPU 0: Driver Version : 570.148.08" > "$A/cuda-ready-nvidia-smi-q-start.txt"; echo "GPU 0: after the last attempt" > "$A/cuda-ready-nvidia-smi-q-end.txt"
echo "[FAIL    ] cuda.ready: host GPU not ready (CUDA 802): RuntimeError: cuInit returned CUDA error 802 after 21 attempts"
note "STEP cudaready FAILED rc=82"
exit 82
'''


@pytest.fixture
def gate_fails(drive):
    world = drive.world
    (world.tmp / "fake_plan.py").write_text(FAKE_PLAN.replace(repr(BRINGUP_TEMPLATE), repr(BRINGUP_82)))
    return drive


def test_exit_82_of_the_bringup_is_an_infrastructure_failure_the_host_is_torn_down_and_nothing_else_starts(gate_fails):
    world = gate_fails.world
    record = write_record(world)
    done = gate_fails(record)
    assert done.returncode == 31, done.stdout[-2500:] + done.stderr[-1500:]
    log = done.log
    assert "BRING-UP [FAIL    ] cuda.ready: host GPU not ready (CUDA 802)" in log and "BRING-UP STEP cudaready FAILED rc=82" in log                # what the operator sees
    assert "INFRASTRUCTURE FAILURE before round 1: host GPU not ready (CUDA 802)" in log and "no round has run" in log and "not a lost launch" in log
    assert "HELD" not in log and "confirmed terminated" in log and "=== done: INFRASTRUCTURE FAILURE" in log
    data = json.loads(record.read_text())
    assert data["terminate_confirmed_utc"] and "terminate inst-001" in (world.net / "lambda.calls").read_text()                                    # torn down
    failure = data["infrastructure_failure"]
    assert failure["kind"] == "cuda-not-ready" and failure["reason"] == "host GPU not ready (CUDA 802)" and failure["rounds_started"] == 0
    assert failure["stage"] == "bringup" and failure["step"] == "cudaready" and failure["bringup_log"] == "bringup-a1.log"
    assert failure["detail"]["code"] == 802 and failure["detail"]["attempts"] == 21 and failure["at"].endswith("Z")
    events = [json.loads(l) for l in (world.ledger / "ledger.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["infrastructure-failure", "terminate-requested", "terminate-confirmed"]
    assert events[0]["reason"] == "host GPU not ready (CUDA 802)" and events[0]["lease_name"] == data["lease_name"]
    assert not list((world.host / "p3out").glob("campaign-r*")) and not (world.host / "study" / "rerun" / "paper3-h100-l1").exists()           # no round was started
    art = done.dest / "rerun" / "bringup-artifacts"
    assert json.loads((art / "cuda-ready.json").read_text())["attempts"] == 21 and (art / "cuda-ready-nvidia-smi-q-end.txt").exists()
    assert (done.dest / "bringup-a1.log").exists() and (done.dest / ".held-logs").read_text().split() == ["bringup-a1.log"]
    kept = json.loads((done.dest / "launch-record.json").read_text())
    assert kept["infrastructure_failure"]["kind"] == "cuda-not-ready" and "ip" not in kept and kept["terminate_confirmed_utc"]
    assert any("cuda-ready.json" in l for l in (done.dest / "LAUNCH-SHA256SUMS").read_text().splitlines())
    assert subprocess.run(["shasum", "-a", "256", "-c", "LAUNCH-SHA256SUMS"], cwd=done.dest, capture_output=True).returncode == 0
    assert gate_fails(record).stdout.strip().endswith("nothing to drive")                                                                          # a torn-down launch stays done


def test_the_failed_gates_log_can_be_opened_for_diagnosis_through_the_logged_path_only(gate_fails):
    world = gate_fails.world
    record = write_record(world)
    done = gate_fails(record)
    assert done.returncode == 31
    opened = sh(world, TOOLS / "p3_open_log.sh", record, "bringup-a1.log", "--dest", done.dest)
    assert opened.returncode == 0 and "Installed 41 packages in 1.2s" in opened.stdout and "OPENED bringup-a1.log" in (done.dest / "log-openings.log").read_text()
    assert sh(world, TOOLS / "p3_open_log.sh", record, "campaign-r1-a1.log", "--dest", done.dest).returncode == 2


def test_with_no_teardown_the_failure_is_recorded_and_the_host_is_left_for_a_person(gate_fails):
    world = gate_fails.world
    record = write_record(world)
    done = gate_fails(record, "--no-teardown")
    assert done.returncode == 31 and "teardown skipped (--no-teardown)" in done.log
    data = json.loads(record.read_text())
    assert data["terminate_confirmed_utc"] is None and data["infrastructure_failure"]["kind"] == "cuda-not-ready" and not (world.net / "lambda.calls").exists()


def test_a_bringup_that_fails_for_another_reason_still_holds_the_host_as_before(drive):
    world = drive.world
    ctl(world, "rc-bringup", "5")
    done = drive(write_record(world))
    assert done.returncode == 20 and "INFRASTRUCTURE" not in done.log and not (world.net / "lambda.calls").exists()


# ---- the planner puts the gate in the right place ------------------------------------------------------------------------------------------

def test_every_gpu_bringup_runs_the_gate_right_after_setup_and_no_cpu_bringup_does():
    import p3_plan as P
    for host in ("a100", "h100", "gh200", "b200"):
        script = P.bringup_script(host, "1", {"campaign_commit": "a" * 40, "lease_name": P.lease_spec(host, "1", P.utc("2026-11-23T06:00:00Z"))["lease_name"]})
        order = [l.split()[1] for l in script.splitlines() if l.startswith("run_step ")]
        assert order[order.index("setup") + 1] == "cudaready" and order.index("cudaready") < order.index("generate") < order.index("preflight"), host
        assert 'tools/paper3_campaign/p3_cuda_ready.sh --python "$PYTHON_GPU_ENV" --out rerun/bringup-artifacts' in script
    for host in ("c-gcp", "c-vm"):
        script = P.bringup_script(host, "1", {"campaign_commit": "a" * 40, "lease_name": P.lease_spec(host, "1", P.utc("2026-11-23T06:00:00Z"))["lease_name"]})
        assert "cudaready" not in script and "p3_cuda_ready" not in script
