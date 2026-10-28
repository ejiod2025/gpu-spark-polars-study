"""Synthetic orchestration/metadata tests only; no GPU or study observations."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("contention_runner_test_module", Path(__file__).parents[1] / "contention_runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_prospective_matrix_has_only_one_spark_and_one_gpu_per_pair():
    assert len(runner.waves("pilot")) == 4
    assert len(runner.waves("main")) == 16
    for phase in ("pilot", "main"):
        rows = runner.waves(phase)
        assert rows == runner.waves(phase)
        assert len({r["wave_id"] for r in rows}) == len(rows)
        for row in rows:
            assert sum(name.startswith("spark_") for name in row["participants"]) == 1
            assert sum(name in ("spark_gpu", "polars_gpu") for name in row["participants"]) == 1
            assert row["scale"] in (("30",) if phase == "pilot" else ("30", "100"))


def record(start, end, utc_offset=0):
    origin = datetime(2020, 1, 1, tzinfo=timezone.utc)
    return {"query": "SYNTHETIC_Q", "phase": "measured", "trial": 1,
            "action_start_monotonic_seconds": start, "action_end_monotonic_seconds": end,
            "action_started_at_utc": (origin + timedelta(seconds=start+utc_offset)).isoformat(),
            "action_ended_at_utc": (origin + timedelta(seconds=end+utc_offset)).isoformat()}


def test_actual_action_intersection_not_process_lifetime():
    rows = runner.overlap({"records": [record(10, 20)]}, {"records": [record(12, 15)]})
    assert rows[0]["overlap_seconds"] == 3
    assert rows[0]["actual_start_skew_seconds"] == 2
    assert rows[0]["positive_actual_overlap"] is True
    disjoint = runner.overlap({"records": [record(10, 11)]}, {"records": [record(12, 13)]})
    assert disjoint[0]["overlap_seconds"] == 0
    assert disjoint[0]["positive_actual_overlap"] is False


def test_both_actual_clocks_must_overlap():
    rows = runner.overlap({"records": [record(10, 20)]}, {"records": [record(12, 15, utc_offset=100)]})
    assert rows[0]["overlap_seconds"] == 3
    assert rows[0]["utc_overlap_seconds"] == 0
    assert rows[0]["positive_actual_overlap"] is False


def test_invalid_action_clock_rejected():
    with pytest.raises(ValueError, match="clock"):
        runner.overlap({"records": [record(10, 10)]}, {"records": [record(12, 15)]})


def flags():
    return "\n".join(["uintx GCLockerRetryAllocationCount = 100 {diagnostic}",
                      "size_t MaxHeapSize = 103079215104 {product}",
                      "bool PrintFlagsFinal = true {product}",
                      "bool UnlockDiagnosticVMOptions = true {diagnostic}",
                      "bool UseG1GC = true {product}"])


def test_actual_spark_flags_require_exact_once_table():
    assert runner.driver_flags(flags())["GCLockerRetryAllocationCount"] == 100
    with pytest.raises(ValueError, match="ambiguous"):
        runner.driver_flags(flags() + "\nbool UseG1GC = true {product}")
    with pytest.raises(ValueError, match="differs"):
        runner.driver_flags(flags().replace("103079215104", "8589934592"))


def test_spark_command_retains_requested_budget_and_distinct_scratch(tmp_path):
    wave = next(r for r in runner.waves("pilot") if "spark_gpu" in r["participants"])
    plan = {"configs": [{"id": "spark_gpu", "engine": "spark_gpu", "python": "/synthetic/venv/bin/python"}],
            "source_root": "/synthetic/frozen", "barrier_worker": "/synthetic/tools/contention_worker.py",
            "barrier_timeout_seconds": 1800, "datasets": {"30": {"path": "/synthetic/sf30"}},
            "settings": {"jar": "/synthetic/rapids.jar", "query_cleanup_by_suite": {"baseline": "none", "complex": "verified_gc"}, "cleanup_timeout_seconds": 60}}
    argv = runner.command(plan, wave, "spark_gpu", tmp_path / "spark_gpu", tmp_path / "barriers")
    assert argv[:5] == ["/synthetic/venv/bin/spark-submit", "--master", "local[26]", "--driver-memory", "96g"]
    assert "spark.rapids.sql.concurrentGpuTasks=2" in argv
    assert "spark.local.dir=" + str(tmp_path / "spark_gpu-scratch") in argv
    assert argv.index("--") > argv.index("/synthetic/tools/contention_worker.py")
    assert argv[argv.index("--runs")+1] == "1"


def test_refuses_to_signal_callers_group(monkeypatch):
    monkeypatch.setattr(runner.os, "getpgrp", lambda: 123)
    with pytest.raises(RuntimeError, match="caller"):
        runner.stop_owned(SimpleNamespace(pid=123))


def test_main_gate_rejects_duplicate_or_missing_pilot_wave_ids(tmp_path):
    root = tmp_path / "synthetic"
    (root / "pilot").mkdir(parents=True)
    plan_path = root / "CONTENTION_PLAN.json"
    plan_path.write_text('{"synthetic":true}\n')
    expected = runner.waves("pilot")
    value = {"phase": "pilot", "status": "PASS", "plan_sha256": runner.sha(plan_path),
             "waves": [{"wave_id": expected[0]["wave_id"]} for _ in range(4)],
             "post_input_gate": {"checks": [{"status": "PASS"}]}}
    runner.write(root / "pilot/PHASE.json", value)
    with pytest.raises(ValueError, match="exact genuine"):
        runner.verify_pilot({"root": str(root), "pilot": expected}, plan_path)


def test_main_gate_rejects_changed_wave_bytes_before_validation(tmp_path):
    root = tmp_path / "synthetic"
    (root / "pilot").mkdir(parents=True)
    plan_path = root / "CONTENTION_PLAN.json"
    plan_path.write_text('{"synthetic":true}\n')
    expected = runner.waves("pilot")
    entries = []
    for wave in expected:
        directory = root / "pilot" / wave["wave_id"]
        directory.mkdir()
        (directory / "WAVE.json").write_text('{"synthetic":true}\n')
        (directory / "VALIDATION.json").write_text('{"synthetic":true}\n')
        entries.append({"wave_id": wave["wave_id"], "wave_sha256": "0"*64, "validation_sha256": runner.sha(directory/"VALIDATION.json")})
    runner.write(root / "pilot/PHASE.json", {"phase": "pilot", "status": "PASS", "plan_sha256": runner.sha(plan_path), "waves": entries,
                                           "post_input_gate": {"checks": [{"status": "PASS"}]}})
    with pytest.raises(ValueError, match="bytes changed"):
        runner.verify_pilot({"root": str(root), "pilot": expected}, plan_path)


def test_initial_input_gate_failure_is_explicit_terminal_phase(tmp_path, monkeypatch):
    root = tmp_path / "synthetic"
    root.mkdir()
    jar = root / "synthetic.jar"
    jar.write_bytes(b"synthetic fixture, not a dependency")
    plan = {"root": str(root), "settings": {"jar": str(jar), "jar_sha256": runner.sha(jar)},
            "pilot": runner.waves("pilot")}
    plan_path = root / "CONTENTION_PLAN.json"
    plan_path.write_text('{"synthetic":true}\n')
    monkeypatch.setattr(runner, "load_plan", lambda _: plan)
    monkeypatch.setattr(runner, "require_idle", lambda _: {"eligible": True, "scope": "synthetic mock"})
    monkeypatch.setattr(runner, "runtime_gate", lambda _: {"status": "PASS", "scope": "synthetic mock"})
    def failed(*_):
        raise ValueError("SYNTHETIC input mismatch")
    monkeypatch.setattr(runner, "input_gate", failed)
    with pytest.raises(ValueError, match="SYNTHETIC"):
        runner.run(SimpleNamespace(plan=plan_path, phase="pilot"))
    result = json.loads((root / "pilot/PHASE.json").read_text())
    assert result["status"] == "FAIL"
    assert result["ended_at_utc"]
    assert result["waves"] == []
