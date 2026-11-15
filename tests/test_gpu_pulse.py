"""gpu_pulse.py: the G8 sync pulse child of run_experiment.py. No GPU here: the CUDA pulse object is a fake, and the real script is
run once in a subprocess to show that it imports the probe tool's kernel class and reports a host without CUDA instead of crashing."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import gpu_pulse  # noqa: E402


class FakePulse:
    created = []

    def __init__(self, device):
        FakePulse.created.append(device)

    def pulse(self, seconds):
        return {"nominal_s": seconds, "launch_utc_ns": 1_700_000_000_123_456_789, "launch_mono_ns": 5_000_000_000,
                "done_utc_ns": 1_700_000_003_123_999_000, "done_mono_ns": 8_000_543_210, "host_s": 3.000543210}


def test_the_utc_format_is_the_one_of_every_other_stamp():
    assert gpu_pulse.utc_from_ns(1_700_000_000_123_456_789) == "2023-11-14T22:13:20.123456+00:00"
    assert gpu_pulse.utc_from_ns(1_700_000_000_000_000_000) == "2023-11-14T22:13:20.000000+00:00"      # microseconds always present


def test_a_pulse_reports_the_stamps_around_the_kernel_launch_and_completion():
    FakePulse.created.clear()
    record = gpu_pulse.run_pulse(3.0, device=2, label="start", factory=FakePulse)
    assert FakePulse.created == [2] and record["ok"] is True and "error" not in record
    assert record["label"] == "start" and record["device"] == 2 and record["requested_seconds"] == 3.0 and record["nominal_seconds"] == 3.0
    assert record["launch_utc"] == "2023-11-14T22:13:20.123456+00:00" and record["done_utc"] == "2023-11-14T22:13:23.123999+00:00"
    assert (record["launch_monotonic_ns"], record["done_monotonic_ns"]) == (5_000_000_000, 8_000_543_210)
    assert record["host_seconds"] == pytest.approx(3.00054321) and record["init_seconds"] >= 0
    assert record["process_start_monotonic_ns"] > 0 and record["process_start_utc"].endswith("+00:00")


def test_a_host_that_cannot_pulse_gets_an_error_not_an_exception():
    def broken(device):
        raise OSError("libcuda.so.1: cannot open shared object file: No such file or directory")
    record = gpu_pulse.run_pulse(3.0, factory=broken)
    assert record["ok"] is False and record["error"].startswith("OSError: libcuda.so.1") and "launch_monotonic_ns" not in record

    class Failing(FakePulse):
        def pulse(self, seconds):
            raise RuntimeError("cuLaunchKernel returned CUDA error 700")
    assert "CUDA error 700" in gpu_pulse.run_pulse(1.0, factory=Failing)["error"]


def test_the_command_line_prints_one_json_line_and_sets_the_exit_code(capsys):
    assert gpu_pulse.main(["--seconds", "0.5", "--label", "end"], factory=FakePulse) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True and printed["label"] == "end" and printed["requested_seconds"] == 0.5
    assert gpu_pulse.main([], factory=lambda device: (_ for _ in ()).throw(OSError("no GPU"))) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


@pytest.mark.parametrize("seconds", ["0", "-1", "61", "nan"])
def test_a_pulse_length_outside_the_design_range_is_refused(seconds, capsys):
    with pytest.raises(SystemExit) as raised:
        gpu_pulse.main(["--seconds", seconds], factory=FakePulse)
    assert raised.value.code == 2


def test_the_kernel_class_is_the_probe_tools_not_a_copy():
    cls = gpu_pulse.load_cuda_pulse()
    assert cls.__name__ == "CudaPulse" and cls.__module__ == "p3_gpu_probes"
    assert ".entry spin(" in cls.__init__.__globals__["ptx_source"]()          # the PTX kernel of the validated probe, loaded by file path
    assert Path(cls.__init__.__globals__["__file__"]) == REPO / "tools" / "paper3_probes" / "p3_gpu_probes.py"


def test_the_script_runs_as_a_process_and_reports_what_this_host_can_do():
    done = subprocess.run([sys.executable, str(REPO / "gpu_pulse.py"), "--seconds", "0.1", "--label", "start"],
                          capture_output=True, text=True, timeout=120, cwd="/")
    record = json.loads(done.stdout.strip().splitlines()[-1])
    assert record["label"] == "start" and (done.returncode == 0) == record["ok"]
    if not record["ok"]:                      # no libcuda here: the failure is a recorded error, not a traceback
        assert record["error"] and "Traceback" not in done.stderr
    else:                                     # a GPU host: the pulse really ran
        assert record["launch_monotonic_ns"] < record["done_monotonic_ns"]
