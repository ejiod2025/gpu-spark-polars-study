"""Synthetic synchronization tests only; no measured study results or GPU jobs."""
from pathlib import Path
import importlib.util
import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


MODULE = Path(__file__).resolve().parents[1] / "contention_worker.py"
spec = importlib.util.spec_from_file_location("tested_contention_worker", MODULE)
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)
WORKER_SHA = "a" * 64


def child(root, participant):
    code = """import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('barrier_under_test',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
b=m.FileBarrier(Path(sys.argv[2]),sys.argv[3],['cpu','gpu'],'a'*64,timeout_seconds=5,release_delay_seconds=.05)
b.wait({'query':'q01','phase':'measured','trial':1})
"""
    return subprocess.Popen([sys.executable, "-c", code, str(MODULE), str(root), participant],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def read_events(root, participant):
    return [json.loads(line) for line in (root / f"events-{participant}.jsonl").read_text().splitlines()]


def test_two_processes_share_actual_readiness_and_release(tmp_path):
    root = tmp_path / "synthetic-barrier"
    first = child(root, "cpu")
    try:
        deadline = time.monotonic() + 5
        while not list(root.glob("*/ready-cpu.json")):
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert not list(root.glob("*/RELEASE.json"))
        second = child(root, "gpu")
        try:
            for process in (first, second):
                stdout, stderr = process.communicate(timeout=8)
                assert process.returncode == 0, (stdout, stderr)
        finally:
            if second.poll() is None:
                second.kill()
                second.wait()
        release = json.loads(next(root.glob("*/RELEASE.json")).read_text())
        assert set(release["ready_monotonic_ns"]) == {"cpu", "gpu"}
        for participant in ("cpu", "gpu"):
            events = read_events(root, participant)
            assert [e["event"] for e in events] == ["participant_claimed", "ready", "released"]
            assert events[-1]["release_monotonic_ns"] == release["release_monotonic_ns"]
            assert events[-1]["monotonic_ns"] >= release["release_monotonic_ns"]
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()


def test_missing_peer_times_out_without_release_or_retry(tmp_path):
    barrier = cw.FileBarrier(tmp_path, "cpu", ["cpu", "gpu"], WORKER_SHA,
                             timeout_seconds=.03, release_delay_seconds=.01)
    key = {"query": "q01", "phase": "warmup", "trial": 0}
    with pytest.raises(TimeoutError):
        barrier.wait(key)
    assert not list(tmp_path.glob("*/RELEASE.json"))
    assert read_events(tmp_path, "cpu")[-1]["event"] == "timeout"
    with pytest.raises(ValueError, match="Duplicate"):
        barrier.wait(key)


def test_root_and_participant_cannot_be_silently_reused(tmp_path):
    cw.FileBarrier(tmp_path, "cpu", ["cpu", "gpu"], WORKER_SHA)
    with pytest.raises(ValueError, match="already claimed"):
        cw.FileBarrier(tmp_path, "cpu", ["cpu", "gpu"], WORKER_SHA)
    with pytest.raises(ValueError, match="different"):
        cw.FileBarrier(tmp_path, "gpu", ["cpu", "gpu"], "b" * 64)


@pytest.mark.parametrize("participants", [["cpu"], ["cpu", "cpu"], ["cpu", "../gpu"], ["cpu", "gpu", "extra"]])
def test_unsafe_or_nonpair_participants_rejected(tmp_path, participants):
    with pytest.raises(ValueError):
        cw.FileBarrier(tmp_path, "cpu", participants, WORKER_SHA)


def test_hooks_gate_setup_and_only_before_each_action():
    saves, counter_calls, waits, skips = [], [], [], []
    worker = SimpleNamespace(save=lambda path, value: saves.append((path, value)),
                             process_counters=lambda processes: counter_calls.append(processes) or {"counter": 3})
    barrier = SimpleNamespace(wait=lambda key: waits.append(key),
                              skip=lambda key, reason: skips.append((key, reason)))
    cw.install_hooks(worker, barrier, pair_has_polars_gpu=True)
    metadata = {"suite": "complex", "records": [], "session_and_table_setup_seconds": 1,
                "active_query": None}
    worker.save(Path("synthetic.json"), metadata)
    worker.save(Path("synthetic.json"), metadata)
    assert waits == [{"query": "setup", "phase": "setup", "trial": 0}]
    metadata["active_query"] = {"query": "cx_q09", "phase": "warmup", "trial": 0}
    worker.save(Path("synthetic.json"), metadata)
    assert worker.process_counters(["fake-process"]) == {"counter": 3}
    worker.process_counters(["fake-process"])
    assert len(waits) == 2
    metadata["active_query"] = {"query": "cx_q13", "phase": "warmup", "trial": 0}
    worker.process_counters([])
    assert len(waits) == 3  # Unsupported warmup still has a real paired attempt.
    metadata["active_query"] = {"query": "cx_q13", "phase": "measured", "trial": 1}
    worker.process_counters([])
    worker.process_counters([])
    assert len(skips) == 1 and skips[0][0]["query"] == "cx_q13"
    assert len(waits) == 3
    assert len(counter_calls) == 5
    assert len(saves) == 3


def test_no_gpu_pair_does_not_skip_complex_cpu_case():
    calls = []
    worker = SimpleNamespace(save=lambda *_: None, process_counters=lambda _: {})
    barrier = SimpleNamespace(wait=lambda key: calls.append(key), skip=lambda *_: pytest.fail("unexpected bypass"))
    cw.install_hooks(worker, barrier)
    worker.save(Path("synthetic.json"), {"suite": "complex", "session_and_table_setup_seconds": 1,
                "active_query": {"query": "cx_q13", "phase": "measured", "trial": 1}})
    worker.process_counters([])
    assert calls[-1]["query"] == "cx_q13"


def test_wrapper_requires_explicit_argument_separator():
    with pytest.raises(ValueError, match="Separate"):
        cw.main(["--source", "unused"])
