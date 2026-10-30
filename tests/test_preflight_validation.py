"""preflight.py validation logic against synthetic run directories (no GPU, no engines needed).

A fully valid four-engine run is built from the interface contract in docs/H100_PLAN.md; each
test then breaks exactly one thing and asserts the validator reports it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import preflight  # noqa: E402
import workloads  # noqa: E402
import complex_workloads  # noqa: E402

SUITES = {"baseline": workloads, "complex": complex_workloads}
GPU_PLAN = ("== Final Plan ==\nAdaptiveSparkPlan isFinalPlan=true\n+- GpuColumnarToRow\n"
            "   +- GpuHashAggregate\n      +- GpuFileGpuScan parquet\n== Initial Plan ==\n")
CPU_PLAN = "== Final Plan ==\nAdaptiveSparkPlan isFinalPlan=true\n+- HashAggregate\n   +- FileScan parquet\n"
# Real spark_gpu final plan of cx_window on the H100: fully on GPU, no aggregate or join by design.
WINDOW_PLAN = ("== Final Plan ==\nAdaptiveSparkPlan isFinalPlan=true\nGpuColumnarToRow\n+- GpuTopN\n"
               "   +- GpuColumnarExchange\n      +- GpuTopN\n         +- GpuProject\n            +- GpuFilter\n"
               "               +- GpuBatchedBoundedWindow\n                  +- GpuRunningWindow\n"
               "                     +- GpuSort\n                        +- GpuSort\n"
               "                           +- GpuFileGpuScan parquet\n== Initial Plan ==\n")
DEVICE = {"name": "NVIDIA H100 80GB HBM3", "driver_version": "580.105.08", "memory_total_bytes": 85_000_000_000}


def rows_for(query: str) -> list[dict]:
    seed = sum(map(ord, query)) % 7 + 1
    return [{"k": i, "total": 1.5 * seed * i, "count_order": 100 * seed + i, "d": "1998-01-01"} for i in range(1, 4)]


def make_session(suite: str, engine: str, runs: int = 1) -> dict:
    queries = list(SUITES[suite].QUERY_IDS)
    session = {
        "engine": engine, "suite": suite, "runs": runs, "queries": queries, "manifest_sha256": "m" * 64,
        "completed_at_utc": "2026-10-26T00:00:00+00:00", "query_execution_status": "complete",
        "version": {"polars": "1.44.2", "polars_gpu": "1.44.2"}.get(engine, "3.5.9"),
        "polars_execution_engine": {"polars": "streaming", "polars_gpu": "gpu"}.get(engine),
        "query_errors": [], "records": [], "failure_policy": "record_and_continue",
        "query_cleanup_policy": "verified_gc" if suite == "complex" else "none", "cleanup_records": [],
    }
    if engine == "polars_gpu":
        session["polars_gpu"] = {"executor": "streaming", "executor_options": {}, "raise_on_fail": True, "device": 0,
                                 "cudf_polars_version": "26.10.2", "memory_resource": None,
                                 "session_guard": {"unsupported_canary_error": "ComputeError: map_elements"}}
    if engine == "spark_gpu":
        session["spark_gpu_profile"] = "a100-parity"
        session["spark_conf"] = {"spark.plugins": "com.nvidia.spark.SQLPlugin"}
    if engine in preflight.GPU_ENGINES:
        session["gpu_inventory"] = dict(DEVICE)
    for query in queries:
        for phase, trial in [("warmup", 0), *[("measured", t) for t in range(1, runs + 1)]]:
            record = {"query": query, "phase": phase, "trial": trial, "seconds": 0.25, "rows": rows_for(query),
                      "plan": f"plans/{query}-{phase}-{trial}.txt",
                      "gpu_execution_class": {"polars_gpu": "gpu_engine_strict", "spark_gpu": "hybrid"}.get(engine, "cpu_engine")}
            if engine in preflight.GPU_ENGINES:
                record["peak_gpu_memory_bytes"] = 1 << 30
            if engine == "polars_gpu":
                record["gpu_engine_warnings"] = []
            if engine == "spark_gpu":
                record["gpu_plan_observed"] = True
            session["records"].append(record)
            if suite == "complex":
                session["cleanup_records"].append({"query": query, "status": "passed"})
    return session


def write_engine(run_dir: Path, scale: str, session: dict, plan_text: str | None = None) -> None:
    folder = run_dir / f"sf{scale}" / session["engine"]
    (folder / "plans").mkdir(parents=True, exist_ok=True)
    text = plan_text or {"spark_gpu": GPU_PLAN, "spark_cpu": CPU_PLAN}.get(session["engine"], "plan\n")
    for record in session["records"]:
        (folder / record["plan"]).write_text(text)
    (folder / "results.json").write_text(json.dumps(session))


def build_run(tmp: Path, suite: str, engines=preflight.ALL_ENGINES, runs: int = 1) -> Path:
    run_dir = tmp / suite
    run_dir.mkdir(parents=True)
    (run_dir / "schedule.json").write_text(json.dumps(
        [{"scale": "1", "engine": e, "status": "complete", "returncode": 0} for e in engines]))
    for engine in engines:
        write_engine(run_dir, "1", make_session(suite, engine, runs))
    return run_dir


def args_for(*extra: str):
    return preflight.build_parser().parse_args(list(extra))


def validate(run_dir: Path, suite: str, engines=preflight.ALL_ENGINES, runs: int = 1, *extra: str):
    checks = preflight.Checks(quiet=True)
    preflight.validate_suite(checks, suite, run_dir, "1", list(engines), runs, args_for(*extra))
    return checks


def edit_session(run_dir: Path, engine: str, fn, scale: str = "1") -> None:
    path = run_dir / f"sf{scale}" / engine / "results.json"
    session = json.loads(path.read_text())
    fn(session)
    path.write_text(json.dumps(session))


def failing(checks: preflight.Checks) -> list[str]:
    return [c["id"] for c in checks.items if c["status"] == "FAIL"]


def status_of(checks: preflight.Checks, suffix: str) -> list[str]:
    return [c["status"] for c in checks.items if c["id"].endswith(suffix)]


# ---- happy path -----------------------------------------------------------------------------------

@pytest.mark.parametrize("suite", ["baseline", "complex"])
def test_valid_four_engine_run_passes(tmp_path, suite):
    checks = validate(build_run(tmp_path, suite), suite)
    assert failing(checks) == []
    assert preflight.Checks.failed(checks) is False
    assert any(c["id"].endswith("polars_gpu:contract") and c["status"] == "PASS" for c in checks.items)
    assert any(c["id"].endswith("spark_gpu:agreement") and c["status"] == "PASS" for c in checks.items)


def test_two_measured_runs_are_counted(tmp_path):
    run_dir = build_run(tmp_path, "baseline", runs=2)
    assert failing(validate(run_dir, "baseline", runs=2)) == []
    assert any("records" in i for i in failing(validate(run_dir, "baseline", runs=3)))


# ---- polars_gpu contract ----------------------------------------------------------------------------

def test_raise_on_fail_must_be_true(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "polars_gpu", lambda s: s["polars_gpu"].update(raise_on_fail=False))
    assert "baseline/sf1:polars_gpu:contract" in failing(validate(run_dir, "baseline"))


@pytest.mark.parametrize("key", list(preflight.POLARS_GPU_REQUIRED))
def test_polars_gpu_metadata_keys_required(tmp_path, key):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "polars_gpu", lambda s: s["polars_gpu"].pop(key))
    assert "baseline/sf1:polars_gpu:contract" in failing(validate(run_dir, "baseline"))


def test_executor_must_match_the_request(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    assert "baseline/sf1:polars_gpu:contract" in failing(validate(run_dir, "baseline", preflight.ALL_ENGINES, 1,
                                                                  "--polars-gpu-executor", "in-memory"))


def test_polars_gpu_record_class_and_presence_of_peak_memory(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "polars_gpu", lambda s: s["records"][0].update(gpu_execution_class="cpu_engine"))
    assert "baseline/sf1:polars_gpu:contract" in failing(validate(run_dir, "baseline"))
    run_dir = build_run(tmp_path / "b", "baseline")
    edit_session(run_dir, "polars_gpu", lambda s: s["records"][0].pop("peak_gpu_memory_bytes"))
    assert "baseline/sf1:polars_gpu:contract" in failing(validate(run_dir, "baseline"))


def test_cpu_engines_must_not_carry_gpu_fields(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "polars", lambda s: s["records"][0].update(peak_gpu_memory_bytes=1))
    assert "baseline/sf1:polars:contract" in failing(validate(run_dir, "baseline"))
    run_dir = build_run(tmp_path / "b", "baseline")
    edit_session(run_dir, "spark_cpu", lambda s: s.update(polars_gpu={}))
    assert "baseline/sf1:spark_cpu:contract" in failing(validate(run_dir, "baseline"))


def test_null_peak_memory_warns_and_impossible_peak_fails(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "polars_gpu", lambda s: [r.update(peak_gpu_memory_bytes=None) for r in s["records"]])
    checks = validate(run_dir, "baseline")
    assert failing(checks) == [] and status_of(checks, "polars_gpu:gpu_memory") == ["WARN"]
    run_dir = build_run(tmp_path / "b", "baseline")
    edit_session(run_dir, "spark_gpu", lambda s: s["records"][0].update(peak_gpu_memory_bytes=10**12))
    assert "baseline/sf1:spark_gpu:gpu_memory" in failing(validate(run_dir, "baseline"))


def test_non_h100_is_rejected_unless_allowed(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    for engine in preflight.GPU_ENGINES:
        edit_session(run_dir, engine, lambda s: s["gpu_inventory"].update(name="NVIDIA A100-SXM4-40GB"))
    checks = validate(run_dir, "baseline")
    assert {"baseline/sf1:polars_gpu:contract", "baseline/sf1:spark_gpu:contract"} <= set(failing(checks))
    assert failing(validate(run_dir, "baseline", preflight.ALL_ENGINES, 1, "--allow-any-gpu")) == []


def test_cpu_fallback_warning_fails_but_single_partition_warning_only_warns(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "polars_gpu", lambda s: s["records"][0].update(
        gpu_engine_warnings=["UserWarning: Query execution with GPU not possible, falling back to CPU"]))
    assert "baseline/sf1:polars_gpu:fallback_warnings" in failing(validate(run_dir, "baseline"))
    run_dir = build_run(tmp_path / "b", "baseline")
    edit_session(run_dir, "polars_gpu", lambda s: s["records"][0].update(
        gpu_engine_warnings=["UserWarning: Failed to concat; falling back to a single partition"]))
    checks = validate(run_dir, "baseline")
    assert failing(checks) == [] and status_of(checks, "polars_gpu:fallback_warnings") == ["WARN"]


# ---- spark_gpu plans -------------------------------------------------------------------------------

def test_spark_gpu_plan_without_gpu_operators_fails(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    write_engine(run_dir, "1", json.loads((run_dir / "sf1" / "spark_gpu" / "results.json").read_text()), CPU_PLAN)
    assert "baseline/sf1:spark_gpu:plans" in failing(validate(run_dir, "baseline"))


def test_spark_gpu_plan_needs_scan_and_aggregate_or_join():
    assert preflight.gpu_plan_problems(GPU_PLAN) == []
    assert preflight.gpu_plan_problems(GPU_PLAN.replace("GpuHashAggregate", "GpuProject"))
    assert preflight.gpu_plan_problems(GPU_PLAN.replace("GpuFileGpuScan", "GpuProject"))
    assert preflight.gpu_plan_problems(GPU_PLAN.replace("isFinalPlan=true", "isFinalPlan=false"))


def test_complex_window_plan_without_aggregate_or_join_is_accepted():
    assert preflight.gpu_plan_problems(WINDOW_PLAN, "complex") == []
    # the baseline rule is unchanged: scan plus aggregate or join
    assert preflight.gpu_plan_problems(WINDOW_PLAN, "baseline") == ["no GPU aggregate or join operator in the final plan"]
    assert preflight.gpu_plan_problems(WINDOW_PLAN) == ["no GPU aggregate or join operator in the final plan"]
    assert preflight.gpu_plan_problems(GPU_PLAN, "complex") == []


@pytest.mark.parametrize("variant, expect", [
    (lambda p: p.replace("GpuFileGpuScan", "GpuProject"), "no GPU scan operator"),
    (lambda p: p.replace("isFinalPlan=true", "isFinalPlan=false"), "isFinalPlan"),
    (lambda p: p.replace("GpuTopN", "GpuProject").replace("GpuRunningWindow", "GpuProject")
     .replace("GpuBatchedBoundedWindow", "GpuProject").replace("GpuSort", "GpuProject"), "no GPU aggregate, join, window"),
    (lambda p: p.replace("GpuColumnarToRow", "TakeOrderedAndProject"), "CPU core operators"),
    (lambda p: p.replace("   +- GpuColumnarExchange", "   +- Sort\n   +- GpuColumnarExchange"), "CPU core operators"),
    (lambda p: p.replace("GpuProject", "Window"), "CPU core operators"),
])
def test_complex_plan_still_rejects_cpu_operators_and_missing_scan(variant, expect):
    problems = preflight.gpu_plan_problems(variant(WINDOW_PLAN), "complex")
    assert problems and any(expect in problem for problem in problems), problems


def test_gpu_prefixed_operators_do_not_count_as_cpu_core_operators():
    assert preflight.CPU_CORE_OPERATOR_RE.findall("GpuSort GpuRunningWindow GpuTopN GpuHashAggregate GpuFileGpuScan") == []
    assert preflight.CPU_CORE_OPERATOR_RE.findall("Sort Window HashAggregate FileScan") == ["Sort", "Window", "HashAggregate", "FileScan"]


def test_complex_suite_spark_gpu_window_plan_passes_end_to_end(tmp_path):
    run_dir = build_run(tmp_path, "complex")
    session = json.loads((run_dir / "sf1" / "spark_gpu" / "results.json").read_text())
    write_engine(run_dir, "1", session, WINDOW_PLAN)  # every query's plan is the window plan
    assert failing(validate(run_dir, "complex")) == []
    # the same plan in the baseline suite is still rejected
    base = build_run(tmp_path / "b", "baseline")
    session = json.loads((base / "sf1" / "spark_gpu" / "results.json").read_text())
    write_engine(base, "1", session, WINDOW_PLAN)
    assert "baseline/sf1:spark_gpu:plans" in failing(validate(base, "baseline"))


def test_spark_gpu_cpu_fallback_class_fails(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "spark_gpu", lambda s: s["records"][0].update(gpu_execution_class="cpu_fallback"))
    assert "baseline/sf1:spark_gpu:contract" in failing(validate(run_dir, "baseline"))


def test_gpu_operator_in_cpu_spark_plan_fails(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    write_engine(run_dir, "1", json.loads((run_dir / "sf1" / "spark_cpu" / "results.json").read_text()), GPU_PLAN)
    assert "baseline/sf1:spark_cpu:plans" in failing(validate(run_dir, "baseline"))


# ---- output agreement -------------------------------------------------------------------------------

def test_agreement_tolerances(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    # relative difference 1e-9 on a float: inside rel 1e-8
    edit_session(run_dir, "spark_gpu", lambda s: s["records"][0]["rows"][0].update(total=s["records"][0]["rows"][0]["total"] * (1 + 1e-9)))
    assert failing(validate(run_dir, "baseline")) == []
    # relative difference 1e-6: outside
    edit_session(run_dir, "spark_gpu", lambda s: s["records"][0]["rows"][0].update(total=s["records"][0]["rows"][0]["total"] * (1 + 1e-6)))
    assert "baseline/sf1:spark_gpu:agreement" in failing(validate(run_dir, "baseline"))


def test_integer_off_by_one_fails_exactly(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "polars_gpu", lambda s: s["records"][2]["rows"][1].update(count_order=s["records"][2]["rows"][1]["count_order"] + 1))
    assert "baseline/sf1:polars_gpu:agreement" in failing(validate(run_dir, "baseline"))


def test_extra_row_and_changed_string_fail(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "spark_cpu", lambda s: s["records"][0]["rows"].append(dict(s["records"][0]["rows"][0])))
    assert "baseline/sf1:spark_cpu:agreement" in failing(validate(run_dir, "baseline"))
    run_dir = build_run(tmp_path / "b", "baseline")
    edit_session(run_dir, "spark_cpu", lambda s: s["records"][0]["rows"][0].update(d="1998-01-02"))
    assert "baseline/sf1:spark_cpu:agreement" in failing(validate(run_dir, "baseline"))


def test_compare_values_unit():
    ok = preflight.compare_values([{"a": 1, "b": 1.0}], [{"a": 1, "b": 1.0 + 1e-10}])
    assert ok["passed"]
    assert not preflight.compare_values([{"a": 1}], [{"a": 2}])["passed"]
    assert not preflight.compare_values([{"a": 1}], [{"b": 1}])["passed"]
    assert not preflight.compare_values([{"a": float("nan")}], [{"a": float("nan")}])["passed"]
    assert not preflight.compare_values([{"a": True}], [{"a": 1}])["passed"]
    assert not preflight.compare_values([1, 2], [1])["passed"]
    assert preflight.compare_values([{"a": 5}], [{"a": 5.0}])["passed"]  # int vs float uses tolerance, as the analyzers do


# ---- records, errors, sessions ----------------------------------------------------------------------

def test_empty_result_missing_record_and_bad_time_fail(tmp_path):
    for name, mutate in {
        "empty": lambda s: s["records"][0].update(rows=[]),
        "missing": lambda s: s["records"].pop(),
        "time": lambda s: s["records"][0].update(seconds=0.0),
        "nan": lambda s: s["records"][0].update(seconds=float("nan")),
    }.items():
        run_dir = build_run(tmp_path / name, "baseline")
        edit_session(run_dir, "polars", mutate)
        assert "baseline/sf1:polars:records" in failing(validate(run_dir, "baseline")), name


def test_unexpected_query_error_fails_and_expected_gpu_gap_is_xfail(tmp_path):
    run_dir = build_run(tmp_path, "complex")

    def break_query(session):
        session["records"] = [r for r in session["records"] if r["query"] != "cx_window"]
        session["cleanup_records"] = session["cleanup_records"][:len(session["records"])]
        session["query_errors"] = [{"query": "cx_window", "phase": "warmup", "trial": 0, "type": "NotImplementedError",
                                    "message": "Unsupported over(...) only expression: UnaryFunction="}]
        session["query_execution_status"] = "partial"

    edit_session(run_dir, "polars_gpu", break_query)
    strict = validate(run_dir, "complex")
    assert "complex/sf1:polars_gpu:query_errors" in failing(strict)
    tolerant = validate(run_dir, "complex", preflight.ALL_ENGINES, 1, "--expect-gpu-unsupported", "cx_window")
    assert failing(tolerant) == []
    assert status_of(tolerant, "polars_gpu:cx_window") == ["XFAIL"]
    # the same query failing on another engine is never excused
    run_dir = build_run(tmp_path / "b", "complex")
    edit_session(run_dir, "spark_gpu", break_query)
    assert "complex/sf1:spark_gpu:query_errors" in failing(
        validate(run_dir, "complex", preflight.ALL_ENGINES, 1, "--expect-gpu-unsupported", "cx_window"))


def test_xpass_is_flagged(tmp_path):
    run_dir = build_run(tmp_path, "complex")
    checks = validate(run_dir, "complex", preflight.ALL_ENGINES, 1, "--expect-gpu-unsupported", "cx_window")
    assert status_of(checks, "polars_gpu:cx_window") == ["WARN"]


def test_unfinished_or_failed_sessions_fail(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "spark_cpu", lambda s: s.pop("completed_at_utc"))
    assert "baseline/sf1:spark_cpu:session" in failing(validate(run_dir, "baseline"))
    run_dir = build_run(tmp_path / "b", "baseline")
    edit_session(run_dir, "polars", lambda s: s.update(failure={"type": "RuntimeError", "message": "boom"}))
    assert "baseline/sf1:polars:session" in failing(validate(run_dir, "baseline"))
    run_dir = build_run(tmp_path / "c", "baseline")
    schedule = json.loads((run_dir / "schedule.json").read_text())
    schedule[0].update(status="failed", returncode=1)
    (run_dir / "schedule.json").write_text(json.dumps(schedule))
    assert any(i.endswith(":session") for i in failing(validate(run_dir, "baseline")))


def test_missing_engine_results_fail(tmp_path):
    run_dir = build_run(tmp_path, "baseline", engines=("polars", "spark_cpu"))
    checks = validate(run_dir, "baseline")
    assert "baseline/sf1:results_present" in failing(checks)


def test_different_polars_versions_between_polars_engines_fail(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "polars", lambda s: s.update(version="2.0.0"))
    assert "baseline/sf1:polars_versions" in failing(validate(run_dir, "baseline"))


def test_engines_must_read_the_same_manifest(tmp_path):
    run_dir = build_run(tmp_path, "baseline")
    edit_session(run_dir, "spark_cpu", lambda s: s.update(manifest_sha256="x" * 64))
    assert "baseline/sf1:same_input" in failing(validate(run_dir, "baseline"))


def test_complex_cleanup_barriers_are_required(tmp_path):
    run_dir = build_run(tmp_path, "complex")
    edit_session(run_dir, "spark_cpu", lambda s: s["cleanup_records"].pop())
    assert "complex/sf1:spark_cpu:contract" in failing(validate(run_dir, "complex"))
    run_dir = build_run(tmp_path / "b", "complex")
    edit_session(run_dir, "polars", lambda s: s["cleanup_records"][0].update(status="timeout"))
    assert "complex/sf1:polars:contract" in failing(validate(run_dir, "complex"))
    run_dir = build_run(tmp_path / "c", "complex")
    edit_session(run_dir, "polars", lambda s: s.update(query_cleanup_policy="none"))
    assert "complex/sf1:polars:contract" in failing(validate(run_dir, "complex"))


# ---- CLI behaviour ----------------------------------------------------------------------------------

def test_cli_skip_run_end_to_end_and_report(tmp_path):
    base, cx = build_run(tmp_path / "x", "baseline"), build_run(tmp_path / "y", "complex")
    report = tmp_path / "out" / "report.json"
    argv = ["--skip-run", "--skip-env", "--skip-inputs", "--baseline-dir", str(base), "--complex-dir", str(cx),
            "--output", str(tmp_path / "out"), "--report", str(report)]
    assert preflight.main(argv) == 0
    data = json.loads(report.read_text())
    assert data["ok"] and data["engines"] == list(preflight.ALL_ENGINES) and data["counts"]["FAIL"] == 0
    with pytest.raises(SystemExit):  # never overwrites a report
        preflight.main(argv)


def test_cli_exit_code_is_nonzero_on_failure(tmp_path):
    base, cx = build_run(tmp_path / "x", "baseline"), build_run(tmp_path / "y", "complex")
    edit_session(base, "polars_gpu", lambda s: s["polars_gpu"].update(raise_on_fail=False))
    assert preflight.main(["--skip-run", "--skip-env", "--skip-inputs", "--baseline-dir", str(base), "--complex-dir", str(cx),
                           "--output", str(tmp_path / "out")]) == 1


def test_cli_refuses_existing_run_directory(tmp_path):
    existing = tmp_path / "exists"
    existing.mkdir()
    with pytest.raises(SystemExit):
        preflight.main(["--baseline-dir", str(existing), "--output", str(tmp_path / "o"), "--skip-env"])


def test_parse_pins_normalizes_names(tmp_path):
    path = tmp_path / "pins.txt"
    path.write_text("# comment\nPolars_Runtime_32==1.0\nnvidia-ml-py==13.0 # trailing\nfoo>=1\n")
    assert preflight.parse_pins(path) == {"polars-runtime-32": "1.0", "nvidia-ml-py": "13.0"}


def test_requirements_gpu_overrides_only_polars_in_the_lock():
    lock = preflight.parse_pins(REPO / "evidence" / "requirements.lock.txt")
    gpu = preflight.parse_pins(REPO / "requirements-gpu.txt")
    overridden = {name for name in gpu if name in lock and gpu[name] != lock[name]}
    assert overridden == {"polars", "polars-runtime-32"}
    assert gpu["polars"] == gpu["polars-runtime-32"]
    assert "cudf-polars-cu12" in gpu and not any(name.endswith("cu13") for name in gpu)


# ---- the real CPU smoke runs, when present locally ----------------------------------------------------

@pytest.mark.skipif(not (REPO / "rerun/dq/smoke-baseline/schedule.json").exists(), reason="local CPU smoke run not present")
def test_real_cpu_smoke_directories_validate(tmp_path):
    code = preflight.main(["--skip-run", "--skip-env", "--skip-inputs", "--engines", "polars", "spark_cpu",
                           "--baseline-dir", str(REPO / "rerun/dq/smoke-baseline"),
                           "--complex-dir", str(REPO / "rerun/dq/smoke-complex"), "--output", str(tmp_path)])
    assert code == 0
