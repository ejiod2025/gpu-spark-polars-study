"""Offline proof checks using archived metadata and explicitly synthetic timestamps.

No benchmark, GPU, JVM, source Parquet file, or remote host is accessed.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_reporting"))
import audit_complete as audit

ARCHIVE = ROOT / "evidence-research/20261026/h100-confirmatory-v1"


def json_file(path):
    return json.loads(Path(path).read_text())


@pytest.fixture()
def readiness():
    return json_file(ARCHIVE / "readiness/complex-polars_gpu/sf1/polars_gpu/results.json")


def test_actual_readiness_has_the_two_specific_translation_shapes(readiness):
    errors = {entry["query"]: entry for entry in readiness["query_errors"]}
    assert set(errors) == {"cx_q13", "cx_window"}
    assert all(audit.translation_rejection(error, error) for error in errors.values())


@pytest.mark.parametrize("kind", ["io", "oom", "different_operator", "measured", "boolean_trial"])
def test_unrelated_failure_is_never_a_known_coverage_rejection(readiness, kind):
    reference = next(e for e in readiness["query_errors"] if e["query"] == "cx_window")
    error = copy.deepcopy(reference)
    if kind == "io":
        error.update(type="OSError", message="Operation not supported")
    elif kind == "oom":
        error.update(message="CUDA out of memory; unsupported allocation size")
    elif kind == "different_operator":
        error["message"] = error["message"].replace("UnaryFunction", "DifferentOperation")
    elif kind == "measured":
        error.update(phase="measured", trial=1)
    else:
        error["trial"] = False
    assert not audit.translation_rejection(error, reference)


def chronology():
    start = datetime(2026, 10, 26, 20, tzinfo=timezone.utc)
    attempts = [{"started_at_utc": start.isoformat(), "ended_at_utc": (start + timedelta(seconds=1)).isoformat()},
                {"started_at_utc": (start + timedelta(seconds=2)).isoformat(),
                 "ended_at_utc": (start + timedelta(seconds=3)).isoformat()}]
    inventory = {"1": {"region/data.parquet": {}}, "10": {"region/data.parquet": {}}}
    def gate(seconds):
        return {"completed_at_utc": (start + timedelta(seconds=seconds)).isoformat(),
                "checks": [{"scale": scale, "status": "pass", "files": 1} for scale in inventory]}
    journal = {"completed_at_utc": (start + timedelta(seconds=5)).isoformat(),
               "input_integrity_gates": [gate(-1), gate(4)]}
    return journal, attempts, inventory


def test_byte_proof_requires_real_temporal_bracketing():
    journal, attempts, inventory = chronology()
    checks = audit.Checks()
    audit.check_chronology(journal, attempts, inventory, checks)
    assert not checks.findings
    journal["input_integrity_gates"][1]["completed_at_utc"] = journal["input_integrity_gates"][0]["completed_at_utc"]
    checks = audit.Checks()
    audit.check_chronology(journal, attempts, inventory, checks)
    assert {f["check_id"] for f in checks.findings} == {"post_byte_gate"}


@pytest.mark.parametrize("kind", ["overlap", "late_gate", "duplicate_scale", "wrong_file_count"])
def test_incomplete_or_misordered_byte_proofs_fail(kind):
    journal, attempts, inventory = chronology()
    if kind == "overlap":
        attempts[1]["started_at_utc"] = attempts[0]["started_at_utc"]
    elif kind == "late_gate":
        journal["completed_at_utc"] = attempts[-1]["ended_at_utc"]
    elif kind == "duplicate_scale":
        journal["input_integrity_gates"][1]["checks"][1]["scale"] = "1"
    else:
        journal["input_integrity_gates"][1]["checks"][0]["files"] = 2
    checks = audit.Checks()
    audit.check_chronology(journal, attempts, inventory, checks)
    assert checks.findings


def cache_schedule():
    files = {"region/data.parquet": {"bytes": 8000}, "nation/data.parquet": {"bytes": 4000}}
    # Deliberately low residency must be retained, not turned into an exclusion.
    observation = {"cache": {"files": {name: {"bytes": value["bytes"], "pages": 2, "resident_pages": 0}
                                          for name, value in files.items()},
                             "errors": [], "bytes": 12000, "pages": 4, "resident_pages": 0, "resident_fraction": 0.0},
                   "host": {"synthetic_notice": "SYNTHETIC cache structure test"}}
    return {"cache_observations": {phase: copy.deepcopy(observation) for phase in
             ("before_preheat", "after_preheat", "after_worker")}}, files


def test_zero_residency_is_measured_without_a_new_rejection_threshold():
    schedule, files = cache_schedule()
    checks = audit.Checks()
    audit.check_cache(schedule, files, checks, "SYNTHETIC-session")
    assert not checks.findings


@pytest.mark.parametrize("kind", ["missing_phase", "missing_file", "probe_error", "invented_fraction"])
def test_cache_proof_retains_every_file_and_observation(kind):
    schedule, files = cache_schedule()
    if kind == "missing_phase":
        del schedule["cache_observations"]["after_worker"]
    else:
        cache = schedule["cache_observations"]["after_worker"]["cache"]
        if kind == "missing_file":
            del cache["files"]["region/data.parquet"]
        elif kind == "probe_error":
            cache["errors"] = [{"path": "region/data.parquet", "error": "mincore failed"}]
        else:
            cache["resident_fraction"] = 1.0
    checks = audit.Checks()
    audit.check_cache(schedule, files, checks, "SYNTHETIC-session")
    assert checks.findings


@pytest.mark.parametrize("config", audit.CONFIGS)
def test_archived_readiness_effective_configuration(config, readiness):
    engine = audit.CONFIGS[config]
    value = json_file(ARCHIVE / f"readiness/baseline-{config}/sf1/{engine}/results.json")
    checks = audit.Checks()
    audit.check_config(value, {"config_id": config, "engine": engine, "session_id": "readiness-only"}, readiness,
                       audit.load_frozen_checks(ARCHIVE / "campaign.json.source"), checks)
    assert not checks.findings


@pytest.mark.parametrize("kind", ["threads", "heap", "gpu_plugin", "gpu_tasks", "gpu_executor"])
def test_effective_configuration_drift_is_explicit_failure(kind, readiness):
    config = "polars_gpu" if kind == "gpu_executor" else "spark_gpu"
    engine = audit.CONFIGS[config]
    value = json_file(ARCHIVE / f"readiness/baseline-{config}/sf1/{engine}/results.json")
    if kind == "threads":
        value["cpu_threads"] = 25
    elif kind == "heap":
        value["spark_conf"]["spark.driver.memory"] = "8g"
    elif kind == "gpu_plugin":
        value["spark_conf"]["spark.plugins"] = ""
    elif kind == "gpu_tasks":
        value["spark_conf"]["spark.rapids.sql.concurrentGpuTasks"] = "8"
    else:
        value["polars_gpu"]["executor"] = "in-memory"
    checks = audit.Checks()
    audit.check_config(value, {"config_id": config, "engine": engine, "session_id": "SYNTHETIC-mutated-metadata"}, readiness,
                       audit.load_frozen_checks(ARCHIVE / "campaign.json.source"), checks)
    assert checks.findings


def test_hash_tracking_detects_mutation_and_duplicate_json(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text('{"synthetic":true}')
    inputs = audit.Inputs()
    first = inputs.sha(path)
    assert inputs.json(path)["synthetic"] is True
    assert inputs.hashes[str(path.resolve())] == first
    path.write_text('{"synthetic":true,"changed":true}')
    with pytest.raises(ValueError, match="changed during audit"):
        inputs.sha(path)
    other = tmp_path / "duplicate.json"
    other.write_text('{"status":"FAIL","status":"PASS"}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        inputs.json(other)


def test_direct_no_B_loader_preserves_complete_frozen_snapshot(tmp_path):
    snapshot = tmp_path / "immutable-source"
    shutil.copytree(ARCHIVE / "campaign.json.source", snapshot)
    def tree():
        return {str(path.relative_to(snapshot)): audit.digest(path.read_bytes()) if path.is_file() else None
                for path in snapshot.rglob("*")}
    before = tree()
    code = ("import sys; from pathlib import Path; "
            f"sys.path.insert(0,{str(ROOT / 'research_reporting')!r}); import audit_complete as a; "
            "assert sys.dont_write_bytecode is False; "
            f"m=a.load_frozen_checks(Path({str(snapshot)!r})); "
            "assert callable(m.check_polars_gpu_metadata); assert sys.dont_write_bytecode is False")
    environment = dict(os.environ)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment.pop("PYTHONPYCACHEPREFIX", None)
    # Deliberately no -B: this is the standalone/direct-loader interpreter mode.
    result = subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert tree() == before
    assert not list(snapshot.rglob("__pycache__"))


@pytest.mark.parametrize("caller_flag", [False, True])
def test_frozen_loader_restores_bytecode_flag_after_error(tmp_path, monkeypatch, caller_flag):
    path = tmp_path / "analyze_results.py"
    path.write_text("import sys\nassert sys.dont_write_bytecode is True\nraise RuntimeError('deliberate loader failure')\n")
    original = path.read_bytes()
    monkeypatch.setattr(sys, "dont_write_bytecode", caller_flag)
    with pytest.raises(RuntimeError, match="deliberate loader failure"):
        audit.load_frozen_checks(tmp_path)
    assert sys.dont_write_bytecode is caller_flag
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["analyze_results.py"]


def test_artifact_mapping_never_follows_escape_or_symlink(tmp_path):
    with pytest.raises(ValueError, match="Unsafe artifact"):
        audit.contained(tmp_path, "../escape")
    target = tmp_path / "outside"
    target.write_text("SYNTHETIC")
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "link").symlink_to(target)
    with pytest.raises(ValueError, match="Escaping/symlink"):
        audit.contained(root, "link")


def synthetic_sensitivity():
    primary, secondary = [], []
    pairs = (("polars_cpu_200", "spark_gpu"), ("spark_cpu", "spark_gpu"),
             ("polars_cpu_144", "polars_gpu"), ("polars_cpu_200", "polars_gpu"))
    for numerator, denominator in pairs:
        for suite, scale in audit.CELLS:
            common = {"contrast_id": f"{numerator}_over_{denominator}-{suite}-sf{scale}",
                      "numerator": numerator, "denominator": denominator, "suite": suite, "scale": scale}
            unsupported = denominator == "polars_gpu" and suite == "complex"
            if not unsupported:
                primary.append({**common, "endpoint": "seconds", "geometric_mean_speedup": 2.0, "ci95_pointwise": [1.5, 2.5]})
            secondary.append({**common, "endpoint": "host_result_seconds", "status": "incomplete" if unsupported else "complete",
                "n_blocks": 0 if unsupported else 10, "missing_blocks": ["SYNTHETIC-missing"] if unsupported else [],
                "geometric_mean_speedup": None if unsupported else .5, "ci95_pointwise": None if unsupported else [.4, .6],
                "direction_changed_vs_action_point_estimate": None if unsupported else True})
    return {"synthetic": True, "primary": primary, "secondary_host_result": secondary}


def test_descriptive_sensitivity_keeps_all_pairs_and_no_secondary_tests(tmp_path):
    rows = audit.sensitivity_rows(synthetic_sensitivity())
    assert len(rows) == 32 and sum(row["host_status"] == "incomplete" for row in rows) == 6
    assert all("p_raw" not in row and "p_holm" not in row for row in rows)
    audit.write_sensitivity(tmp_path, rows, synthetic=True)
    text = (tmp_path / "HOST_RESULT_SENSITIVITY.md").read_text()
    assert "SYNTHETIC TEST ONLY" in text and "pointwise" in text and "No new hypothesis tests" in text


def test_sensitivity_refuses_missing_pair_or_invented_direction():
    value = synthetic_sensitivity()
    value["secondary_host_result"][0]["direction_changed_vs_action_point_estimate"] = False
    with pytest.raises(ValueError, match="direction-change"):
        audit.sensitivity_rows(value)
    value["secondary_host_result"].pop()
    with pytest.raises(ValueError, match="all 32"):
        audit.sensitivity_rows(value)
