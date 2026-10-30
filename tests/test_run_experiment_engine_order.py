"""run_experiment.py: --engine-order, appended GPU telemetry columns and the pre-session GPU-idle gate.

No engine or GPU is needed: subprocess launches and nvidia-smi are replaced by fakes, so every test
drives the real main() and reads the schedule.json / environment.json it writes.
"""
from __future__ import annotations

import json
from pathlib import Path
import random
import signal
import sys
import types

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import run_experiment as rx  # noqa: E402

DEFAULT = ["polars", "spark_cpu", "spark_gpu"]


class FakePopen:
    launched: list = []
    telemetry: list = []

    def __init__(self, command, **_kwargs):
        self.command, self.pid = list(command), 2 ** 22 + 1
        (FakePopen.telemetry if command and command[0] == "nvidia-smi" else FakePopen.launched).append(self.command)

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass


class Host:
    """Fake nvidia-smi: `apps` is what --query-compute-apps reports (a list of csv lines)."""

    def __init__(self):
        self.apps: list = []
        self.queries = 0

    def capture(self, command, timeout=None):
        if any(part.startswith("--query-compute-apps") for part in command):
            self.queries += 1
            return {"command": command, "returncode": 0, "output": "\n".join(self.apps) + ("\n" if self.apps else "")}
        return {"command": command, "returncode": 0, "output": ""}


@pytest.fixture
def host(monkeypatch):
    FakePopen.launched, FakePopen.telemetry = [], []
    fake = Host()
    monkeypatch.setattr(rx, "subprocess", types.SimpleNamespace(
        Popen=FakePopen, run=rx.subprocess.run, PIPE=rx.subprocess.PIPE, STDOUT=rx.subprocess.STDOUT,
        TimeoutExpired=rx.subprocess.TimeoutExpired))
    monkeypatch.setattr(rx, "capture", fake.capture)
    previous = signal.getsignal(signal.SIGTERM)
    yield fake
    signal.signal(signal.SIGTERM, previous)


@pytest.fixture
def layout(tmp_path):
    data_root = tmp_path / "data"
    for scale in ("1", "10", "30", "100"):
        directory = data_root / f"sf{scale}"
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(json.dumps({"scale_factor": float(scale)}))
    jar = tmp_path / "rapids.jar"
    jar.write_bytes(b"")
    return types.SimpleNamespace(data_root=data_root, jar=jar, out=tmp_path / "out", tmp=tmp_path)


def run_main(monkeypatch, layout, scales, engines, *extra, out=None):
    argv = ["run_experiment.py", "--data-root", str(layout.data_root), "--output", str(out or layout.out),
            "--scales", *scales, "--engines", *engines, "--runs", "1", "--jar", str(layout.jar),
            "--keep-going", "--queries", "q06", *extra]
    monkeypatch.setattr(sys, "argv", argv)
    rx.main()
    out = out or layout.out
    return (json.loads((out / "schedule.json").read_text()), json.loads((out / "environment.json").read_text()))


def by_scale(schedule):
    result: dict = {}
    for item in schedule:
        result.setdefault(item["scale"], []).append(item["engine"])
    return result


def test_without_the_flag_the_seeded_shuffle_is_unchanged(monkeypatch, layout, host):
    scales = ["1", "10", "30", "100"]
    schedule, environment = run_main(monkeypatch, layout, scales, DEFAULT)
    for index, scale in enumerate(scales):
        expected = list(DEFAULT)
        random.Random(42 + index).shuffle(expected)
        assert by_scale(schedule)[scale] == expected
    assert environment["engine_order"] is None
    assert all("engine_order_position" not in item for item in schedule)


def test_engine_order_replaces_the_shuffle_for_every_scale(monkeypatch, layout, host):
    order = ["spark_gpu", "polars", "spark_cpu"]
    scales = ["1", "10", "30", "100"]
    schedule, environment = run_main(monkeypatch, layout, scales, DEFAULT, "--engine-order", *order)
    assert by_scale(schedule) == {scale: order for scale in scales}
    assert environment["engine_order"] == order
    assert environment["arguments"]["engine_order"] == order
    assert [item["engine_order_position"] for item in schedule[:3]] == [1, 2, 3]
    launched = [command[command.index("--engine") + 1] for command in FakePopen.launched]
    assert launched == order * len(scales)


@pytest.mark.parametrize("order", [
    ["polars", "spark_cpu"],                                  # missing one
    ["polars", "spark_cpu", "spark_gpu", "polars_gpu"],       # extra engine
    ["polars", "polars", "spark_cpu"],                        # duplicate
])
def test_engine_order_must_be_a_permutation_of_engines(monkeypatch, layout, host, order):
    with pytest.raises(SystemExit) as raised:
        run_main(monkeypatch, layout, ["1"], DEFAULT, "--engine-order", *order)
    assert raised.value.code == 2
    assert not layout.out.exists()  # rejected before the output directory is created


def test_gpu_telemetry_only_appends_columns(monkeypatch, layout, host):
    run_main(monkeypatch, layout, ["1"], ["polars"])
    query = next(a for command in FakePopen.telemetry for a in command if a.startswith("--query-gpu="))
    columns = query.removeprefix("--query-gpu=").split(",")
    assert columns[:6] == ["timestamp", "index", "utilization.gpu", "utilization.memory", "memory.used", "power.draw"]
    assert columns[6:] == ["temperature.gpu", "clocks.sm", "clocks.mem"]


def test_idle_gpu_is_recorded_and_sessions_run(monkeypatch, layout, host):
    schedule, _ = run_main(monkeypatch, layout, ["1"], DEFAULT)
    assert all(item["status"] == "complete" for item in schedule)
    for item in schedule:  # recorded for every session once a GPU engine is selected
        gate = item["gpu_idle_gate"]
        assert gate["idle"] is True and gate["compute_apps"] == [] and gate["polls"] == 1 and gate["error"] is None
    assert host.queries == len(schedule)


def test_busy_gpu_fails_the_session_without_launching_it(monkeypatch, layout, host):
    host.apps = ["4242, python, 1024"]
    schedule, _ = run_main(monkeypatch, layout, ["1"], DEFAULT, "--gpu-idle-timeout-seconds", "0")
    assert len(schedule) == 3 and FakePopen.launched == []  # --keep-going: all recorded, none launched
    for item in schedule:
        assert item["status"] == "failed" and item["failure_reason"] == "gpu_not_idle"
        assert item["returncode"] is None and item["gpu_idle_gate"]["idle"] is False
        assert item["gpu_idle_gate"]["compute_apps"] == ["4242, python, 1024"]


def test_busy_gpu_stops_the_run_without_keep_going(monkeypatch, layout, host):
    host.apps = ["4242, python, 1024"]
    argv = ["run_experiment.py", "--data-root", str(layout.data_root), "--output", str(layout.out), "--scales", "1",
            "--runs", "1", "--jar", str(layout.jar), "--gpu-idle-timeout-seconds", "0"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(RuntimeError, match="GPU not idle"):
        rx.main()
    assert json.loads((layout.out / "schedule.json").read_text())[0]["failure_reason"] == "gpu_not_idle"


def test_no_gpu_engine_means_no_gate(monkeypatch, layout, host):
    schedule, _ = run_main(monkeypatch, layout, ["1", "10"], ["polars", "spark_cpu"])
    assert host.queries == 0
    assert all("gpu_idle_gate" not in item for item in schedule)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_wait_for_idle_gpu_polls_until_the_gpu_frees_up():
    clock, answers = Clock(), iter([(["1, x, 1"], None), (["1, x, 1"], None), ([], None)])
    result = rx.wait_for_idle_gpu(60, poll=1.0, list_apps=lambda: next(answers), sleep=clock.sleep, clock=clock)
    assert result["idle"] is True and result["polls"] == 3 and result["waited_seconds"] == 2.0


def test_wait_for_idle_gpu_gives_up_after_the_timeout():
    clock = Clock()
    result = rx.wait_for_idle_gpu(60, poll=1.0, list_apps=lambda: (["7, y, 2"], None), sleep=clock.sleep, clock=clock)
    assert result["idle"] is False and result["waited_seconds"] >= 60 and result["polls"] == 61
    assert result["compute_apps"] == ["7, y, 2"]


def test_failing_nvidia_smi_is_never_read_as_idle():
    clock = Clock()
    result = rx.wait_for_idle_gpu(3, poll=1.0, list_apps=lambda: (None, "NVML error"), sleep=clock.sleep, clock=clock)
    assert result["idle"] is False and result["error"] == "NVML error" and result["compute_apps"] == []
