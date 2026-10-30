"""Section 7 integrity gates of the locked H100 design, at formal scale (preflight.py --formal --skip-run).

A pristine synthetic round is built exactly as run_experiment.py lays one out (baseline/ with SF1..SF300 and
complex/ with SF30..SF300, four engines, 3 measured runs, the section 5 engine order, polars_gpu rejecting
cx_q13 and cx_window). Each test then breaks exactly one thing and asserts which gate fails, and that the
other gates still pass: the gates are independent and none passes vacuously.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import complex_workloads  # noqa: E402
import preflight  # noqa: E402
import workloads  # noqa: E402

BASELINE_SCALES = ["1", "10", "30", "100", "300"]
COMPLEX_SCALES = ["30", "100", "300"]
ENGINES = ["polars", "polars_gpu", "spark_cpu", "spark_gpu"]
DECLARED = ("cx_q13", "cx_window")
RUNS = 3
SUITE_SCALES = {"baseline": BASELINE_SCALES, "complex": COMPLEX_SCALES}
QUERIES = {"baseline": list(workloads.QUERY_IDS), "complex": list(complex_workloads.QUERY_IDS)}
# Section 5, written out by hand so that a bug in preflight.round_engine_order cannot hide itself.
ROUND_ORDER = {
    1: ["spark_cpu", "polars_gpu", "spark_gpu", "polars"],
    2: ["polars_gpu", "polars", "spark_cpu", "spark_gpu"],
    3: ["polars", "spark_gpu", "polars_gpu", "spark_cpu"],
    4: ["spark_gpu", "spark_cpu", "polars", "polars_gpu"],
}
G1, G2, G3, G4, G5, GO = preflight.G1, preflight.G2, preflight.G3, preflight.G4, preflight.G5, preflight.GO
DEVICE = {"name": "NVIDIA H100 80GB HBM3", "driver_version": "580.105.08", "memory_total_bytes": 85_000_000_000}

BASELINE_PLAN = ("== Final Plan ==\nAdaptiveSparkPlan isFinalPlan=true\n+- GpuColumnarToRow\n   +- GpuHashAggregate\n"
                 "      +- GpuFileGpuScan parquet\n== Initial Plan ==\nHashAggregate\n+- FileScan parquet\n")
COMPLEX_PLAN = ("== Final Plan ==\nAdaptiveSparkPlan isFinalPlan=true\nGpuColumnarToRow\n+- GpuTopN\n   +- GpuBroadcastHashJoin\n"
                "      +- GpuFileGpuScan parquet\n== Initial Plan ==\nSort\n+- FileScan parquet\n")
WINDOW_PLAN = ("== Final Plan ==\nAdaptiveSparkPlan isFinalPlan=true\nGpuColumnarToRow\n+- GpuTopN\n   +- GpuRunningWindow\n"
               "      +- GpuSort\n         +- GpuFileGpuScan parquet\n== Initial Plan ==\nWindow\n+- FileScan parquet\n")
CPU_PLAN = "== Final Plan ==\nAdaptiveSparkPlan isFinalPlan=true\n+- HashAggregate\n   +- FileScan parquet\n"


def manifest_digest(scale: str) -> str:
    return hashlib.sha256(f"manifest-sf{scale}".encode()).hexdigest()


def rows_for(scale: str, query: str) -> list[dict]:
    seed = (sum(map(ord, query)) + int(scale)) % 11 + 1
    return [{"k": i, "total": round(1.5 * seed * i * 1000.123, 4), "count_order": 600_000_000 * seed + i, "d": "1998-01-01"}
            for i in range(1, 4)]


def plan_for(suite: str, query: str) -> str:
    if suite == "baseline":
        return BASELINE_PLAN
    return WINDOW_PLAN if query == "cx_window" else COMPLEX_PLAN


def make_session(suite: str, scale: str, engine: str) -> tuple[dict, dict[str, str]]:
    """(results.json content, {plan file name: text})."""
    queries = QUERIES[suite]
    gpu_rejects = engine == "polars_gpu" and suite == "complex"
    session = {
        "engine": engine, "suite": suite, "suite_version": "complex-v1" if suite == "complex" else "baseline-v1",
        "scale_factor": float(scale), "runs": RUNS, "queries": list(queries), "manifest_sha256": manifest_digest(scale),
        "started_at_utc": "2026-11-01T00:00:00+00:00", "completed_at_utc": "2026-11-01T01:00:00+00:00",
        "query_execution_status": "partial" if gpu_rejects else "complete", "cpu_threads": 26, "active_query": None,
        "version": {"polars": "1.44.2", "polars_gpu": "1.44.2"}.get(engine, "3.5.9"),
        "polars_execution_engine": {"polars": "streaming", "polars_gpu": "gpu"}.get(engine),
        "query_errors": [], "records": [], "failure_policy": "record_and_continue",
        "query_cleanup_policy": "verified_gc" if suite == "complex" else "none", "cleanup_records": [],
    }
    if engine == "polars_gpu":
        session["polars_gpu"] = {
            "executor": "streaming", "executor_options": {}, "raise_on_fail": True, "device": 0,
            "cudf_polars_version": "26.10.2", "memory_resource": None,
            "session_guard": {"unsupported_canary_error": "ComputeError: map_elements",
                              "effective_config": {"raise_on_fail": True, "executor": {"name": "streaming"}}}}
    if engine == "spark_gpu":
        session["spark_gpu_profile"] = "a100-parity"
        session["spark_conf"] = {"spark.plugins": "com.nvidia.spark.SQLPlugin", "spark.master": "local[26]"}
    if engine in preflight.GPU_ENGINES:
        session["gpu_inventory"] = dict(DEVICE)
    if suite == "complex" and engine != "polars_gpu":
        pass
    plans: dict[str, str] = {}
    for query in queries:
        if gpu_rejects and query in DECLARED:
            session["query_errors"].append({
                "query": query, "phase": "warmup", "trial": 0, "type": "NotImplementedError",
                "message": "Unsupported over(...) only expression: UnaryFunction=" if query == "cx_window"
                else "inline regex flag (?s) is not supported", "failed_at_utc": "2026-11-01T00:10:00+00:00"})
            session.setdefault("failed_query_cleanup_records", []).append(
                {"query": query, "phase": "warmup", "trial": 0, "status": "passed"})
            continue
        for phase, trial in [("warmup", 0), *[("measured", t) for t in range(1, RUNS + 1)]]:
            name = f"{query}-{phase}-{trial}.txt"
            record = {"query": query, "phase": phase, "trial": trial, "seconds": 0.25 + trial / 100, "rows": rows_for(scale, query),
                      "plan": f"plans/{name}",
                      "gpu_execution_class": {"polars_gpu": "gpu_engine_strict", "spark_gpu": "hybrid"}.get(engine, "cpu_engine")}
            if engine in preflight.GPU_ENGINES:
                record["peak_gpu_memory_bytes"] = 1 << 30
            if engine == "polars_gpu":
                record["gpu_engine_warnings"] = []
            if engine == "spark_gpu":
                record["gpu_plan_observed"] = True
            session["records"].append(record)
            plans[name] = {"spark_gpu": plan_for(suite, query), "spark_cpu": CPU_PLAN}.get(engine, "plan\n")
            if suite == "complex":
                session["cleanup_records"].append({"query": query, "phase": phase, "trial": trial, "status": "passed"})
    return session, plans


def write_session(root: Path, suite: str, scale: str, engine: str, session: dict | None = None, plans: dict | None = None) -> None:
    built, built_plans = make_session(suite, scale, engine)
    session, plans = session if session is not None else built, plans if plans is not None else built_plans
    folder = root / suite / f"sf{scale}" / engine
    (folder / "plans").mkdir(parents=True, exist_ok=True)
    for name, text in plans.items():
        (folder / "plans" / name).write_text(text)
    (folder / "results.json").write_text(json.dumps(session))


def build_round(root: Path, round_number: int = 1, scales: dict | None = None, engines=None) -> Path:
    """One finished round under root/{baseline,complex}; returns root."""
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    suite_order = ["baseline", "complex"] if round_number % 2 else ["complex", "baseline"]
    for suite_index, suite in enumerate(suite_order):
        run_dir = root / suite
        run_dir.mkdir(parents=True)
        schedule, clock = [], base + timedelta(days=suite_index)
        for scale in (scales or SUITE_SCALES)[suite]:
            (run_dir / f"sf{scale}").mkdir(parents=True, exist_ok=True)
            (run_dir / f"sf{scale}" / "manifest.json").write_text(f"manifest-sf{scale}")
            for engine in [e for e in ROUND_ORDER[round_number] if engines is None or e in engines]:
                schedule.append({"scale": scale, "engine": engine, "status": "complete", "returncode": 0,
                                 "start_utc": clock.isoformat(), "process_wall_seconds": 60.0,
                                 "engine_order_position": [e for e in ROUND_ORDER[round_number] if engines is None or e in engines].index(engine) + 1,
                                 "gpu_idle_gate": {"idle": True, "waited_seconds": 0.0, "compute_apps": [], "error": None}})
                clock += timedelta(minutes=30)
                write_session(root, suite, scale, engine)
        (run_dir / "schedule.json").write_text(json.dumps(schedule))
    return root


def edit(root: Path, suite: str, scale: str, engine: str, fn) -> None:
    path = root / suite / f"sf{scale}" / engine / "results.json"
    session = json.loads(path.read_text())
    fn(session)
    path.write_text(json.dumps(session))


def edit_schedule(root: Path, suite: str, fn) -> None:
    path = root / suite / "schedule.json"
    schedule = json.loads(path.read_text())
    fn(schedule)
    path.write_text(json.dumps(schedule))


def write_plan(root: Path, suite: str, scale: str, engine: str, name: str, text: str) -> None:
    (root / suite / f"sf{scale}" / engine / "plans" / name).write_text(text)


def run_preflight(root: Path, tmp_path: Path, *extra: str, round_number: int = 1, name: str = "pf") -> tuple[int, dict]:
    report = tmp_path / f"{name}.json"
    code = preflight.main(["--formal", "--skip-run", "--skip-env", "--skip-inputs", "--round", str(round_number),
                           "--baseline-dir", str(root / "baseline"), "--complex-dir", str(root / "complex"),
                           "--output", str(tmp_path / f"{name}-out"), "--report", str(report), *extra])
    return code, json.loads(report.read_text())


def gate_verdicts(report: dict) -> dict[str, str]:
    return {gate: info["verdict"] for gate, info in report["gates"].items()}


@pytest.fixture(scope="module")
def pristine(tmp_path_factory) -> Path:
    return build_round(tmp_path_factory.mktemp("round1") / "round", 1)


@pytest.fixture()
def rnd(pristine, tmp_path) -> Path:
    target = tmp_path / "round"
    shutil.copytree(pristine, target)
    return target


def assert_only_failing(report: dict, *gates: str) -> None:
    verdicts = gate_verdicts(report)
    assert report["verdict"] == "fail" and report["ok"] is False
    failing = {g for g, v in verdicts.items() if v == "fail"}
    assert failing == set(gates), (verdicts, report["gates"][next(iter(failing), G1)]["failing_checks"][:4])


# ---- the passing formal-scale round ------------------------------------------------------------------------

def test_pristine_formal_round_passes_with_machine_readable_verdict(rnd, tmp_path):
    code, report = run_preflight(rnd, tmp_path)
    assert code == 0, report["failing_checks"][:5]
    assert report["verdict"] == "pass" and report["ok"] is True and report["failing_checks"] == []
    assert gate_verdicts(report) == {G1: "pass", G2: "pass", G3: "pass", G4: "pass", G5: "pass", GO: "pass"}
    assert report["counts"]["FAIL"] == 0
    # 5 baseline + 3 complex scales x 4 engines = 32 sessions, each carrying a verdict for every applicable gate
    assert len(report["sessions"]) == 32
    for session in report["sessions"]:
        assert set(session["gates"].values()) == {"pass"}
        assert G1 in session["gates"] and G2 in session["gates"]
        assert (G3 in session["gates"]) == (session["engine"] == "polars_gpu")
        assert (G4 in session["gates"]) == (session["engine"] == "spark_gpu")
        assert (G5 in session["gates"]) == (session["suite"] == "complex")
    assert report["expected"]["runs"] == 3 and report["expected"]["round"] == 1
    assert report["expected"]["engines"] == ENGINES
    assert report["expected"]["suites"]["baseline"]["scales"] == BASELINE_SCALES
    assert report["expected"]["suites"]["complex"]["scales"] == COMPLEX_SCALES


def test_pristine_round_reports_the_two_predeclared_gaps_as_coverage(rnd, tmp_path):
    _, report = run_preflight(rnd, tmp_path)
    outcomes = report["coverage"]["outcomes"]
    assert sorted((o["scale"], o["query"]) for o in outcomes) == sorted((s, q) for s in COMPLEX_SCALES for q in DECLARED)
    assert all(o["engine"] == "polars_gpu" and o["predeclared"] and o["class"] == "unsupported_operation" for o in outcomes)
    assert report["counts"]["XFAIL"] == 6 and report["counts"]["COVERAGE"] == 0
    assert {r["id"] for r in report["coverage"]["rules"]} >= {"I1-silent-cpu-fallback", "C1-resource-exhaustion"}


def test_all_four_rounds_validate_with_their_own_order(tmp_path):
    for number in (1, 2, 3, 4):
        root = build_round(tmp_path / f"r{number}", number)
        code, report = run_preflight(root, tmp_path, round_number=number, name=f"pf{number}")
        assert code == 0 and report["verdict"] == "pass", (number, report["failing_checks"][:4])
        assert report["expected"]["round_engine_order"] == ROUND_ORDER[number]


def test_validation_is_idempotent_and_read_only(rnd, tmp_path):
    before = {p: p.read_bytes() for p in rnd.rglob("*") if p.is_file()}
    code1, first = run_preflight(rnd, tmp_path, name="a")
    code2, second = run_preflight(rnd, tmp_path, name="b")
    assert code1 == code2 == 0
    strip = lambda r: {k: v for k, v in r.items() if k not in ("started_at_utc", "completed_at_utc", "args")}
    assert strip(first) == strip(second)
    assert {p: p.read_bytes() for p in rnd.rglob("*") if p.is_file()} == before  # the run root is never touched


def test_report_is_never_overwritten(rnd, tmp_path):
    run_preflight(rnd, tmp_path, name="once")
    with pytest.raises(SystemExit) as stop:
        run_preflight(rnd, tmp_path, name="once")
    assert stop.value.code == 2


# ---- usage errors (exit 2): --formal pins the design ---------------------------------------------------------

@pytest.mark.parametrize("extra", [
    ["--runs", "1"], ["--engines", "polars", "spark_cpu"], ["--baseline-scales", "1", "10"], ["--complex-scales", "30"],
    ["--expect-gpu-unsupported", "cx_window"], ["--expect-cpus", "30"], ["--scales", "1"],
    ["--spark-gpu-profile", "h100-tuned"], ["--polars-gpu-executor", "in-memory"], ["--error-policy", "strict"],
])
def test_formal_refuses_to_deviate_from_the_locked_design(rnd, tmp_path, extra):
    with pytest.raises(SystemExit) as stop:
        run_preflight(rnd, tmp_path, *extra)
    assert stop.value.code == 2


def test_formal_requires_skip_run_and_round(rnd, tmp_path):
    for argv in (["--formal", "--skip-env", "--skip-inputs", "--round", "1"],
                 ["--formal", "--skip-run", "--skip-env", "--skip-inputs"]):
        with pytest.raises(SystemExit) as stop:
            preflight.main([*argv, "--baseline-dir", str(rnd / "baseline"), "--complex-dir", str(rnd / "complex"),
                            "--output", str(tmp_path / "o"), "--report", str(tmp_path / "r.json")])
        assert stop.value.code == 2


def test_formal_accepts_the_values_it_pins(rnd, tmp_path):
    code, _ = run_preflight(rnd, tmp_path, "--runs", "3", "--engines", *ENGINES, "--expect-gpu-unsupported", *DECLARED,
                            "--expect-cpus", "26", "--baseline-scales", *BASELINE_SCALES, "--complex-scales", *COMPLEX_SCALES)
    assert code == 0


def test_cli_exit_codes_pass_and_fail(rnd, tmp_path):
    assert run_preflight(rnd, tmp_path, name="ok")[0] == 0
    edit(rnd, "baseline", "10", "polars", lambda s: s["records"].pop())
    code, report = run_preflight(rnd, tmp_path, name="bad")
    assert code == 1 and report["verdict"] == "fail"


# ---- G1: every session exits, no engine timeout ---------------------------------------------------------------

def test_g1_engine_timeout_fails_even_when_the_message_looks_like_oom(rnd, tmp_path):
    def timeout(schedule):
        item = next(i for i in schedule if i["scale"] == "100" and i["engine"] == "spark_gpu")
        item.update(status="failed", returncode=124, failure_reason="engine_timeout")

    edit_schedule(rnd, "baseline", timeout)
    edit(rnd, "baseline", "100", "spark_gpu", lambda s: s.update(
        failure={"type": "OutOfMemoryError", "message": "java.lang.OutOfMemoryError: Java heap space"}, completed_at_utc=None))
    _, report = run_preflight(rnd, tmp_path)
    assert_only_failing(report, G1)
    assert any("engine timeout" in c["detail"] for c in report["checks"] if c["status"] == "FAIL")


def test_g1_nonzero_exit_without_a_classifiable_failure(rnd, tmp_path):
    edit_schedule(rnd, "complex", lambda sch: next(i for i in sch if i["scale"] == "30" and i["engine"] == "polars").update(
        status="failed", returncode=-9))
    _, report = run_preflight(rnd, tmp_path)
    assert_only_failing(report, G1)
    assert any("SIGKILL" in c["detail"] for c in report["checks"] if c["status"] == "FAIL")


@pytest.mark.parametrize("engine", ENGINES)
def test_g1_missing_results_json(rnd, tmp_path, engine):
    (rnd / "baseline" / "sf30" / engine / "results.json").unlink()
    _, report = run_preflight(rnd, tmp_path)
    assert G1 in {g for g, v in gate_verdicts(report).items() if v == "fail"}
    assert report["verdict"] == "fail"
    assert [s for s in report["sessions"] if (s["suite"], s["scale"], s["engine"]) == ("baseline", "30", engine)][0]["gates"][G1] != "pass"


def test_g1_missing_extra_and_duplicate_schedule_entries(rnd, tmp_path):
    """A schedule that does not hold exactly the expected sessions fails G1 (and the executed-order control)."""
    cases = {
        "missing": ("baseline", lambda sch: sch.pop(3)),
        "extra": ("baseline", lambda sch: sch.append({**sch[0], "scale": "1000"})),
        "duplicate": ("complex", lambda sch: sch.append(copy.deepcopy(sch[0]))),
    }
    for name, (suite, mutate) in cases.items():
        root = tmp_path / name
        shutil.copytree(rnd, root)
        edit_schedule(root, suite, mutate)
        _, report = run_preflight(root, tmp_path, name=name)
        failing = {g for g, v in gate_verdicts(report).items() if v == "fail"}
        assert G1 in failing and failing <= {G1, GO}, (name, gate_verdicts(report))
        assert report["verdict"] == "fail"


@pytest.mark.parametrize("name, mutate", {
    "unfinished": lambda s: s.pop("completed_at_utc"),
    "failure_recorded": lambda s: s.update(failure={"type": "RuntimeError", "message": "boom"}),
    "missing_record": lambda s: s["records"].pop(),
    "one_run_short": lambda s: s["records"].remove(next(r for r in s["records"] if r["phase"] == "measured" and r["trial"] == 3)),
    "wrong_runs": lambda s: s.update(runs=1),
    "bad_time": lambda s: s["records"][0].update(seconds=float("nan")),
    "zero_time": lambda s: s["records"][2].update(seconds=0.0),
    "empty_rows": lambda s: s["records"][5].update(rows=[]),
    "duplicate_trial": lambda s: s["records"][3].update(trial=2),
    "wrong_scale": lambda s: s.update(scale_factor=30.0),
    "wrong_suite_version": lambda s: s.update(suite_version="complex-v0"),
    "query_subset": lambda s: s.update(queries=s["queries"][:-1]),
    "partial_without_errors": lambda s: s.update(query_execution_status="partial"),
}.items())
def test_g1_session_defects_fail_only_g1(rnd, tmp_path, name, mutate):
    edit(rnd, "baseline", "10", "spark_cpu", mutate)
    _, report = run_preflight(rnd, tmp_path)
    failing = {g for g, v in gate_verdicts(report).items() if v == "fail"}
    assert G1 in failing, (name, gate_verdicts(report))
    assert failing <= {G1, G2}, (name, failing)  # a missing/duplicated record may also surface in agreement
    assert report["verdict"] == "fail"


def test_g1_cpu_threads_control(rnd, tmp_path):
    edit(rnd, "complex", "100", "spark_cpu", lambda s: s.update(cpu_threads=30))
    _, report = run_preflight(rnd, tmp_path)
    assert report["verdict"] == "fail" and report["gates"][GO]["verdict"] == "fail"


# ---- G2: output agreement with polars ---------------------------------------------------------------------------

def bump(record_index, key, delta=None, factor=None):
    def mutate(session):
        row = session["records"][record_index]["rows"][1]
        row[key] = row[key] + delta if delta is not None else row[key] * factor
    return mutate


@pytest.mark.parametrize("engine", ["polars_gpu", "spark_cpu", "spark_gpu"])
def test_g2_float_outside_tolerance_fails_each_engine(rnd, tmp_path, engine):
    edit(rnd, "baseline", "300", engine, bump(7, "total", factor=1 + 1e-6))
    _, report = run_preflight(rnd, tmp_path)
    assert_only_failing(report, G2)


def test_g2_float_inside_tolerance_passes(rnd, tmp_path):
    edit(rnd, "baseline", "300", "spark_gpu", bump(7, "total", factor=1 + 1e-9))
    assert run_preflight(rnd, tmp_path)[0] == 0


def test_g2_integer_off_by_one_fails(rnd, tmp_path):
    edit(rnd, "complex", "100", "polars_gpu", bump(5, "count_order", delta=1))
    assert_only_failing(run_preflight(rnd, tmp_path)[1], G2)


def test_g2_integer_as_float_within_relative_tolerance_still_fails(rnd, tmp_path):
    """600_000_000 vs 600_000_000.5 is within rel 1e-8 for floats, but a count must be exact (silent coercion)."""
    edit(rnd, "baseline", "100", "spark_cpu", bump(0, "count_order", delta=0.5))
    assert_only_failing(run_preflight(rnd, tmp_path)[1], G2)


def test_g2_integral_float_equal_to_the_integer_passes(rnd, tmp_path):
    edit(rnd, "baseline", "100", "spark_cpu", lambda s: s["records"][0]["rows"][0].update(count_order=float(s["records"][0]["rows"][0]["count_order"])))
    assert run_preflight(rnd, tmp_path)[0] == 0


def test_g2_one_bad_measured_trial_among_good_ones_fails(rnd, tmp_path):
    edit(rnd, "complex", "300", "spark_gpu", bump(11, "total", factor=1.01))
    assert_only_failing(run_preflight(rnd, tmp_path)[1], G2)


@pytest.mark.parametrize("name, mutate", {
    "extra_row": lambda s: s["records"][0]["rows"].append(dict(s["records"][0]["rows"][0])),
    "dropped_row": lambda s: s["records"][0]["rows"].pop(),
    "renamed_column": lambda s: s["records"][0]["rows"][0].update(extra=1),
    "string_changed": lambda s: s["records"][0]["rows"][0].update(d="1998-01-02"),
    "nan": lambda s: s["records"][0]["rows"][0].update(total=float("nan")),
    "bool_for_int": lambda s: s["records"][0]["rows"][0].update(k=True),
    "row_order": lambda s: s["records"][0]["rows"].reverse(),
}.items())
def test_g2_structural_differences_fail(rnd, tmp_path, name, mutate):
    edit(rnd, "baseline", "30", "polars_gpu", mutate)
    _, report = run_preflight(rnd, tmp_path)
    assert_only_failing(report, G2)


def test_g2_reference_must_be_polars(rnd, tmp_path):
    (rnd / "baseline" / "sf30" / "polars" / "results.json").unlink()
    _, report = run_preflight(rnd, tmp_path)
    assert report["verdict"] == "fail"
    assert report["gates"][G2]["verdict"] == "fail"
    # no silent fallback to another engine as the reference
    assert any(c["id"] == "baseline/sf30:reference" and c["status"] == "FAIL" for c in report["checks"])


def test_g2_engines_must_have_read_the_same_manifest(rnd, tmp_path):
    edit(rnd, "baseline", "30", "spark_gpu", lambda s: s.update(manifest_sha256="0" * 64))
    assert_only_failing(run_preflight(rnd, tmp_path)[1], G2)


def test_g2_scale_directory_manifest_must_match_what_the_engines_read(rnd, tmp_path):
    (rnd / "baseline" / "sf30" / "manifest.json").write_text("tampered")
    assert_only_failing(run_preflight(rnd, tmp_path)[1], G2)


# ---- G3: polars_gpu strict guard; classified failures --------------------------------------------------------------

@pytest.mark.parametrize("name, mutate", {
    "raise_on_fail_false": lambda s: s["polars_gpu"].update(raise_on_fail=False),
    "wrong_executor": lambda s: s["polars_gpu"].update(executor="in-memory"),
    "no_canary": lambda s: s["polars_gpu"]["session_guard"].pop("unsupported_canary_error"),
    "empty_canary": lambda s: s["polars_gpu"]["session_guard"].update(unsupported_canary_error=""),
    "resolved_raise_on_fail_false": lambda s: s["polars_gpu"]["session_guard"]["effective_config"].update(raise_on_fail=False),
    "resolved_executor_differs": lambda s: s["polars_gpu"]["session_guard"]["effective_config"].update(executor={"name": "in-memory"}),
    "resolved_executor_differs_as_string": lambda s: s["polars_gpu"]["session_guard"]["effective_config"].update(executor="in_memory"),
    "missing_metadata": lambda s: s.pop("polars_gpu"),
    "device_not_int": lambda s: s["polars_gpu"].update(device="0"),
    "cpu_class_record": lambda s: s["records"][4].update(gpu_execution_class="cpu_engine"),
    "cpu_fallback_warning": lambda s: s["records"][4].update(gpu_engine_warnings=["UserWarning: GPU not possible, falling back to CPU"]),
    "guard_stage_failure": lambda s: s.update(failure={"type": "RuntimeError", "stage": "polars_gpu_session_guard", "message": "x"}),
}.items())
def test_g3_strict_guard_violations_fail_g3(rnd, tmp_path, name, mutate):
    edit(rnd, "baseline", "100", "polars_gpu", mutate)
    _, report = run_preflight(rnd, tmp_path)
    failing = {g for g, v in gate_verdicts(report).items() if v == "fail"}
    assert G3 in failing, (name, gate_verdicts(report))
    assert report["verdict"] == "fail"


def test_g3_flattened_effective_config_shapes_are_not_false_failures(rnd, tmp_path):
    """worker.json_safe stringifies what it cannot flatten: a repr string or a dict without 'name' must pass."""
    for scale in BASELINE_SCALES:
        edit(rnd, "baseline", scale, "polars_gpu", lambda s: s["polars_gpu"]["session_guard"].update(
            effective_config={"raise_on_fail": True, "executor": {"runtime": "tasks", "max_rows_per_partition": 1000000}}))
    edit(rnd, "complex", "30", "polars_gpu", lambda s: s["polars_gpu"]["session_guard"].update(
        effective_config={"raise_on_fail": True, "executor": "StreamingExecutor(runtime=tasks)"}))
    edit(rnd, "complex", "100", "polars_gpu", lambda s: s["polars_gpu"]["session_guard"].update(effective_config="ConfigOptions(...)"))
    edit(rnd, "complex", "300", "polars_gpu", lambda s: s["polars_gpu"]["session_guard"].pop("effective_config"))
    edit(rnd, "complex", "300", "polars_gpu", lambda s: s["polars_gpu"]["session_guard"].update(effective_config_error="ConfigOptions unavailable"))
    code, report = run_preflight(rnd, tmp_path)
    assert code == 0, report["failing_checks"][:3]
    assert any(c["id"].endswith("strict_guard_config") and c["status"] == "WARN" for c in report["checks"])


def test_g3_unclassified_error_on_polars_gpu_fails(rnd, tmp_path):
    def crash(session):
        session["records"] = [r for r in session["records"] if r["query"] != "q06"]
        session["query_errors"].append({"query": "q06", "phase": "warmup", "trial": 0, "type": "RuntimeError",
                                        "message": "CUDA error: an illegal memory access was encountered"})
        session["query_execution_status"] = "partial"
    edit(rnd, "baseline", "30", "polars_gpu", crash)
    _, report = run_preflight(rnd, tmp_path)
    assert G3 in {g for g, v in gate_verdicts(report).items() if v == "fail"} and report["verdict"] == "fail"


def test_g3_silent_cpu_fallback_message_is_integrity_even_if_it_says_unsupported(rnd, tmp_path):
    def fallback(session):
        session["records"] = [r for r in session["records"] if r["query"] != "q06"]
        session["query_errors"].append({"query": "q06", "phase": "warmup", "trial": 0, "type": "NotImplementedError",
                                        "message": "unsupported expression, falling back to CPU"})
        session["query_execution_status"] = "partial"
    edit(rnd, "baseline", "30", "polars_gpu", fallback)
    _, report = run_preflight(rnd, tmp_path)
    assert G3 in {g for g, v in gate_verdicts(report).items() if v == "fail"}
    assert report["coverage"]["outcomes"] and all(o["query"] in DECLARED for o in report["coverage"]["outcomes"])


# ---- A1: coverage outcomes do not fail a round ------------------------------------------------------------------------

def reject(query, error_type, message, phase="warmup"):
    def mutate(session):
        session["records"] = [r for r in session["records"] if r["query"] != query]
        session["cleanup_records"] = [c for c in session["cleanup_records"] if c["query"] != query]
        session["query_errors"].append({"query": query, "phase": phase, "trial": 0, "type": error_type, "message": message})
        session.setdefault("failed_query_cleanup_records", []).append({"query": query, "phase": phase, "trial": 0, "status": "passed"})
        session["query_execution_status"] = "partial"
    return mutate


@pytest.mark.parametrize("engine, error_type, message, klass", [
    ("polars_gpu", "MemoryError", "std::bad_alloc: out_of_memory: RMM failure at: /cudf/rmm/mr/device/cuda_async", "resource_exhaustion"),
    ("polars_gpu", "RuntimeError", "CUDA error: cudaErrorMemoryAllocation out of memory", "resource_exhaustion"),
    ("polars_gpu", "NotImplementedError", "Unsupported over(...) only expression", "unsupported_operation"),
    ("spark_gpu", "Py4JJavaError", "com.nvidia.spark.rapids.jni.GpuSplitAndRetryOOM: GPU OutOfMemory", "resource_exhaustion"),
    ("spark_cpu", "Py4JJavaError", "java.lang.OutOfMemoryError: Java heap space", "resource_exhaustion"),
    ("polars", "MemoryError", "Cannot allocate memory", "resource_exhaustion"),
])
def test_a1_coverage_outcomes_are_reported_not_failed(rnd, tmp_path, engine, error_type, message, klass):
    edit(rnd, "complex", "300", engine, reject("cx_distinct", error_type, message))
    code, report = run_preflight(rnd, tmp_path)
    # cx_distinct is not excluded from the other engines, so the reference/agreement checks still run
    assert code == 0 and report["verdict"] == "pass", report["failing_checks"][:4]
    mine = [o for o in report["coverage"]["outcomes"] if o["engine"] == engine and o["query"] == "cx_distinct"]
    assert len(mine) == 1 and mine[0]["class"] == klass and mine[0]["scale"] == "300"
    assert mine[0]["predeclared"] is False
    assert any(c["status"] == "WARN" and c["id"].endswith("cx_distinct:coverage_note") for c in report["checks"])


def test_a1_unclassified_error_on_a_cpu_engine_fails(rnd, tmp_path):
    edit(rnd, "complex", "300", "spark_cpu", reject("cx_distinct", "AnalysisException", "Column 'x' does not exist"))
    _, report = run_preflight(rnd, tmp_path)
    assert G1 in {g for g, v in gate_verdicts(report).items() if v == "fail"} and report["verdict"] == "fail"


def test_a1_unsupported_operation_on_a_cpu_engine_is_not_coverage(rnd, tmp_path):
    edit(rnd, "complex", "300", "polars", reject("cx_distinct", "NotImplementedError", "Unsupported over(...)"))
    _, report = run_preflight(rnd, tmp_path)
    assert report["verdict"] == "fail"


def test_a1_session_level_gpu_oom_is_coverage(rnd, tmp_path):
    """spark_gpu at SF300 baseline: the worker aborts the session on the first failing query (policy 'raise')."""
    def abort(session):
        keep = {"q01", "q03"}
        session["records"] = [r for r in session["records"] if r["query"] in keep and r["phase"] == "warmup"]
        session.update(completed_at_utc=None, query_execution_status=None,
                       failure={"type": "Py4JJavaError", "message": "GpuSplitAndRetryOOM: GPU out of memory"})
    edit(rnd, "baseline", "300", "spark_gpu", abort)
    edit_schedule(rnd, "baseline", lambda sch: next(i for i in sch if i["scale"] == "300" and i["engine"] == "spark_gpu").update(
        status="failed", returncode=1))
    code, report = run_preflight(rnd, tmp_path)
    assert code == 0 and report["verdict"] == "pass", report["failing_checks"][:4]
    session = [o for o in report["coverage"]["outcomes"] if o["scope"] == "session"]
    assert len(session) == 1 and session[0]["engine"] == "spark_gpu" and session[0]["class"] == "resource_exhaustion"
    assert sorted(session[0]["queries_not_completed"]) == ["q06", "q10", "q12", "q14"] or "q03" in session[0]["queries_not_completed"]
    # the records that did complete were still compared with polars
    assert any(c["id"] == "baseline/sf300:spark_gpu:agreement" and c["status"] == "PASS" for c in report["checks"])


def test_a1_session_crash_found_only_in_the_engine_log(rnd, tmp_path):
    edit(rnd, "baseline", "300", "polars_gpu", lambda s: s.update(completed_at_utc=None))
    edit_schedule(rnd, "baseline", lambda sch: next(i for i in sch if i["scale"] == "300" and i["engine"] == "polars_gpu").update(
        status="failed", returncode=1))
    log = rnd / "baseline" / "sf300" / "polars_gpu.log"
    log.write_text("starting\nTraceback\nMemoryError: std::bad_alloc\n")
    edit(rnd, "baseline", "300", "polars_gpu", lambda s: (s.update(records=[]), s.update(query_execution_status=None)))
    code, report = run_preflight(rnd, tmp_path)
    assert code == 0, report["failing_checks"][:4]
    log.write_text("starting\nTraceback\nSegmentation fault\n")
    code, report = run_preflight(rnd, tmp_path, name="seg")
    assert code == 1 and G1 in {g for g, v in gate_verdicts(report).items() if v == "fail"}


def test_a1_predeclared_gap_that_fails_for_another_reason_is_flagged(rnd, tmp_path):
    def oom(session):
        for error in session["query_errors"]:
            if error["query"] == "cx_window":
                error.update(type="MemoryError", message="out of memory")
    edit(rnd, "complex", "300", "polars_gpu", oom)
    code, report = run_preflight(rnd, tmp_path)
    assert code == 0
    note = [c for c in report["checks"] if c["id"].endswith("polars_gpu:cx_window:coverage_note") and "sf300" in c["id"]]
    assert note and "another reason" in note[0]["detail"]


def test_a1_a_predeclared_query_that_ran_is_an_xpass_warning_and_must_agree(rnd, tmp_path):
    write_session(rnd, "complex", "30", "polars_gpu", *make_session("complex", "30", "polars"))  # a full CPU-like session
    edit(rnd, "complex", "30", "polars_gpu", lambda s: s.update(
        engine="polars_gpu", polars_execution_engine="gpu", polars_gpu={**make_session("complex", "30", "polars_gpu")[0]["polars_gpu"]},
        gpu_inventory=dict(DEVICE), failure_policy="record_and_continue"))
    edit(rnd, "complex", "30", "polars_gpu", lambda s: [r.update(gpu_execution_class="gpu_engine_strict", peak_gpu_memory_bytes=1,
                                                                 gpu_engine_warnings=[]) for r in s["records"]])
    code, report = run_preflight(rnd, tmp_path)
    assert code == 0, report["failing_checks"][:4]
    assert any(c["status"] == "WARN" and "XPASS" in c["detail"] for c in report["checks"])
    edit(rnd, "complex", "30", "polars_gpu", bump(next(i for i, r in enumerate(json.loads((rnd / "complex/sf30/polars_gpu/results.json").read_text())["records"]) if r["query"] == "cx_window"), "total", factor=2))
    assert_only_failing(run_preflight(rnd, tmp_path, name="xp")[1], G2)


def test_a1_unstable_coverage_across_rounds_is_reported(rnd, tmp_path):
    other = build_round(tmp_path / "other", 2)
    edit(other, "complex", "300", "polars_gpu", reject("cx_distinct", "MemoryError", "out of memory"))
    _, second = run_preflight(other, tmp_path, round_number=2, name="r2")
    code, first = run_preflight(rnd, tmp_path, "--prior-reports", str(tmp_path / "r2.json"), name="r1")
    assert code == 0
    assert first["coverage"]["unstable"] == [["polars_gpu", "complex", "300", "cx_distinct"]]
    assert any(c["id"].startswith("unstable_coverage:") and c["status"] == "WARN" for c in first["checks"])


# ---- G4: spark_gpu plan rules ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("suite, scale, query, plan, expect", [
    ("baseline", "100", "q03", BASELINE_PLAN.replace("GpuFileGpuScan", "GpuProject"), "no GPU scan"),
    ("baseline", "100", "q03", BASELINE_PLAN.replace("GpuHashAggregate", "GpuProject"), "no GPU aggregate or join"),
    ("baseline", "100", "q03", CPU_PLAN, "no GPU scan"),
    ("baseline", "100", "q03", BASELINE_PLAN.replace("isFinalPlan=true", "isFinalPlan=false"), "isFinalPlan"),
    ("complex", "300", "cx_q09", COMPLEX_PLAN.replace("GpuTopN", "GpuProject").replace("GpuBroadcastHashJoin", "GpuProject"), "no GPU aggregate, join, window"),
    ("complex", "300", "cx_q09", COMPLEX_PLAN.replace("GpuColumnarToRow", "Sort\n+- GpuColumnarToRow"), "CPU core operators"),
    ("complex", "300", "cx_window", WINDOW_PLAN.replace("GpuSort", "Window"), "CPU core operators"),
    ("complex", "300", "cx_window", WINDOW_PLAN.replace("GpuFileGpuScan", "GpuProject"), "no GPU scan"),
])
def test_g4_plan_rule_violations_fail_only_g4(rnd, tmp_path, suite, scale, query, plan, expect):
    write_plan(rnd, suite, scale, "spark_gpu", f"{query}-measured-2.txt", plan)
    _, report = run_preflight(rnd, tmp_path)
    assert_only_failing(report, G4)
    assert any(expect in c["detail"] for c in report["checks"] if c["status"] == "FAIL")


def test_g4_cx_window_without_aggregate_or_join_is_accepted_in_complex_but_not_baseline(rnd, tmp_path):
    assert run_preflight(rnd, tmp_path)[0] == 0  # pristine complex cx_window plan is GpuTopN/GpuRunningWindow/GpuSort only
    write_plan(rnd, "baseline", "100", "spark_gpu", "q03-measured-2.txt", WINDOW_PLAN)
    assert_only_failing(run_preflight(rnd, tmp_path, name="b")[1], G4)


def test_g4_missing_plan_file_fails(rnd, tmp_path):
    (rnd / "complex" / "sf100" / "spark_gpu" / "plans" / "cx_q09-warmup-0.txt").unlink()
    assert_only_failing(run_preflight(rnd, tmp_path)[1], G4)


def test_g4_cpu_fallback_class_and_plugin_and_profile(rnd, tmp_path):
    edit(rnd, "baseline", "30", "spark_gpu", lambda s: s["records"][0].update(gpu_execution_class="cpu_fallback"))
    assert_only_failing(run_preflight(rnd, tmp_path)[1], G4)
    edit(rnd, "baseline", "30", "spark_gpu", lambda s: s["records"][0].update(gpu_execution_class="hybrid"))
    edit(rnd, "baseline", "10", "spark_gpu", lambda s: s["spark_conf"].update({"spark.plugins": ""}))
    assert_only_failing(run_preflight(rnd, tmp_path, name="p")[1], G4)
    edit(rnd, "baseline", "10", "spark_gpu", lambda s: s["spark_conf"].update({"spark.plugins": "com.nvidia.spark.SQLPlugin"}))
    edit(rnd, "baseline", "1", "spark_gpu", lambda s: s.update(spark_gpu_profile="h100-tuned"))
    assert_only_failing(run_preflight(rnd, tmp_path, name="h")[1], G4)


def test_g4_a_gpu_operator_in_a_cpu_spark_plan_is_flagged(rnd, tmp_path):
    write_plan(rnd, "baseline", "30", "spark_cpu", "q01-warmup-0.txt", BASELINE_PLAN)
    _, report = run_preflight(rnd, tmp_path)
    assert report["verdict"] == "fail" and report["gates"][GO]["verdict"] == "fail"


# ---- G5: verified_gc barriers (complex) ---------------------------------------------------------------------------------

@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("name, mutate", {
    "timeout": lambda s: s["cleanup_records"][0].update(status="timeout"),
    "failed": lambda s: s["cleanup_records"][-1].update(status="failed"),
    "missing": lambda s: s["cleanup_records"].pop(),
    "policy_none": lambda s: s.update(query_cleanup_policy="none"),
    "duplicate_masks_missing": lambda s: s["cleanup_records"].__setitem__(0, dict(s["cleanup_records"][1])),
    "cleanup_failure_recorded": lambda s: s.update(cleanup_failure={"query": "cx_q09", "type": "CleanupTimeout", "message": "x"}),
}.items())
def test_g5_barrier_violations_fail_g5(rnd, tmp_path, engine, name, mutate):
    edit(rnd, "complex", "100", engine, mutate)
    _, report = run_preflight(rnd, tmp_path)
    failing = {g for g, v in gate_verdicts(report).items() if v == "fail"}
    assert G5 in failing, (engine, name, gate_verdicts(report))
    assert report["verdict"] == "fail"


def test_g5_barrier_after_a_failed_action_must_exist_and_pass(rnd, tmp_path):
    edit(rnd, "complex", "30", "polars_gpu", lambda s: s["failed_query_cleanup_records"].pop())
    assert_only_failing(run_preflight(rnd, tmp_path)[1], G5)
    rnd2 = tmp_path / "again"
    shutil.copytree(rnd, rnd2)
    edit(rnd2, "complex", "30", "polars_gpu", lambda s: s.update(failed_query_cleanup_records=[
        {"query": "cx_q13", "phase": "warmup", "trial": 0, "status": "passed"},
        {"query": "cx_window", "phase": "warmup", "trial": 0, "status": "timeout"}]))
    assert_only_failing(run_preflight(rnd2, tmp_path, name="t")[1], G5)


def test_g5_baseline_must_not_use_verified_gc(rnd, tmp_path):
    edit(rnd, "baseline", "30", "polars", lambda s: s.update(query_cleanup_policy="verified_gc"))
    _, report = run_preflight(rnd, tmp_path)
    assert report["verdict"] == "fail" and report["gates"][GO]["verdict"] == "fail"


# ---- executed order (section 5) ---------------------------------------------------------------------------------------------

def test_order_engine_swap_within_a_scale_fails(rnd, tmp_path):
    def swap(schedule):
        a, b = [i for i in schedule if i["scale"] == "30"][:2]
        a["engine"], b["engine"] = b["engine"], a["engine"]
    edit_schedule(rnd, "baseline", swap)
    _, report = run_preflight(rnd, tmp_path)
    assert report["verdict"] == "fail" and report["gates"][GO]["verdict"] == "fail"
    assert any(c["id"] == "baseline:executed_order" and c["status"] == "FAIL" for c in report["checks"])


def test_order_scales_must_ascend(rnd, tmp_path):
    edit_schedule(rnd, "complex", lambda sch: sch.reverse())
    _, report = run_preflight(rnd, tmp_path)
    assert any(c["id"] == "complex:executed_order" and c["status"] == "FAIL" for c in report["checks"])


def test_order_a_round_validated_against_the_wrong_row_fails(tmp_path):
    root = build_round(tmp_path / "r2", 2)
    code, report = run_preflight(root, tmp_path, round_number=1)
    assert code == 1 and report["gates"][GO]["verdict"] == "fail"
    assert any(c["id"] == "suite_order" and c["status"] == "FAIL" for c in report["checks"])


def test_order_suites_alternate(tmp_path):
    root = build_round(tmp_path / "r3", 3)  # odd round: baseline first
    clock = datetime(2026, 12, 1, tzinfo=timezone.utc)
    edit_schedule(root, "complex", lambda sch: [i.update(start_utc=(clock + timedelta(minutes=n)).isoformat()) for n, i in enumerate(sch)])
    code, report = run_preflight(root, tmp_path, round_number=3)
    assert code == 0  # complex still after baseline
    edit_schedule(root, "complex", lambda sch: [i.update(start_utc=(datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=n)).isoformat())
                                                for n, i in enumerate(sch)])
    code, report = run_preflight(root, tmp_path, round_number=3, name="swapped")
    assert code == 1 and any(c["id"] == "suite_order" and c["status"] == "FAIL" for c in report["checks"])


# ---- link to the verified inputs ---------------------------------------------------------------------------------------------

def inputs_report(tmp_path: Path, scales=("1", "10", "30", "100", "300"), ok=True, digest=manifest_digest) -> Path:
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps({
        "tool": "verify_generated_inputs", "ok": ok, "complete": True,
        "datasets": [{"scale_factor": float(s), "ok": True, "checks": [{"id": "new_manifest", "status": "INFO", "sha256": digest(s)}]}
                     for s in scales]}))
    return path


def test_inputs_report_must_match_the_manifests_the_engines_read(rnd, tmp_path):
    code, report = run_preflight(rnd, tmp_path, "--inputs-report", str(inputs_report(tmp_path)))
    assert code == 0, report["failing_checks"][:3]
    other = tmp_path / "other"
    other.mkdir()
    code, report = run_preflight(rnd, tmp_path, "--inputs-report",
                                 str(inputs_report(other, digest=lambda s: "f" * 64)), name="mismatch")
    assert code == 1 and report["gates"][GO]["verdict"] == "fail"


def test_inputs_report_that_failed_or_misses_a_scale_fails_the_round(rnd, tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    assert run_preflight(rnd, tmp_path, "--inputs-report", str(inputs_report(bad, ok=False)), name="notok")[0] == 1
    short = tmp_path / "short"
    short.mkdir()
    assert run_preflight(rnd, tmp_path, "--inputs-report", str(inputs_report(short, scales=("1", "10", "30", "100"))), name="short")[0] == 1


# ---- the gate arithmetic itself ----------------------------------------------------------------------------------------------

def test_a_gate_with_no_evidence_does_not_pass():
    checks = preflight.Checks(quiet=True)
    verdicts = preflight.evaluate_gates(checks, [("baseline", "1", "polars")])
    assert verdicts["gates"][G1]["verdict"] == "fail" and verdicts["gates"][G2]["verdict"] == "fail"
    assert verdicts["gates"][G3]["verdict"] == "not_applicable"
    assert verdicts["sessions"][0]["gates"] == {G1: "not_evaluated", G2: "not_evaluated"}


def test_warnings_do_not_fail_unless_strict(rnd, tmp_path):
    edit(rnd, "baseline", "30", "polars_gpu", lambda s: s["records"][0].update(
        gpu_engine_warnings=["UserWarning: Failed to concat; falling back to a single partition"]))
    code, report = run_preflight(rnd, tmp_path)
    assert code == 0 and report["counts"]["WARN"] >= 1
    code, report = run_preflight(rnd, tmp_path, "--strict", name="strict")
    assert code == 1 and report["verdict"] == "fail"


def test_zero_sessions_is_a_failure_not_a_vacuous_pass(tmp_path):
    (tmp_path / "baseline").mkdir()
    (tmp_path / "complex").mkdir()
    code, report = run_preflight(tmp_path, tmp_path)
    assert code == 1 and report["verdict"] == "fail"


# ---- runner facts (schedule.json stamps) and amendment A2 hosts ----------------------------------------------------------------

def test_gpu_not_idle_session_fails_and_is_never_coverage(rnd, tmp_path):
    edit_schedule(rnd, "baseline", lambda sch: sch[0].update(status="failed", returncode=None, failure_reason="gpu_not_idle",
                                                           gpu_idle_gate={"idle": False, "compute_apps": ["1234 python"]}))
    _, report = run_preflight(rnd, tmp_path)
    assert report["verdict"] == "fail" and G1 in {g for g, v in gate_verdicts(report).items() if v == "fail"}
    assert any("GPU was not idle" in c["detail"] for c in report["checks"] if c["status"] == "FAIL")


def test_missing_gpu_idle_gate_stamp_fails_the_control(rnd, tmp_path):
    edit_schedule(rnd, "complex", lambda sch: sch[2].pop("gpu_idle_gate"))
    _, report = run_preflight(rnd, tmp_path)
    assert report["verdict"] == "fail" and report["gates"][GO]["verdict"] == "fail"


def test_engine_order_position_stamp_must_match_the_executed_position(rnd, tmp_path):
    edit_schedule(rnd, "baseline", lambda sch: sch[1].update(engine_order_position=4))
    _, report = run_preflight(rnd, tmp_path)
    assert report["verdict"] == "fail" and any("engine_order_position" in c["detail"] for c in report["checks"] if c["status"] == "FAIL")


def test_a2_other_gpu_hosts_need_allow_any_gpu_even_in_formal_mode(rnd, tmp_path):
    for engine in ("polars_gpu", "spark_gpu"):
        for scale in BASELINE_SCALES:
            edit(rnd, "baseline", scale, engine, lambda s: s["gpu_inventory"].update(name="NVIDIA B200"))
        for scale in COMPLEX_SCALES:
            edit(rnd, "complex", scale, engine, lambda s: s["gpu_inventory"].update(name="NVIDIA B200"))
    code, report = run_preflight(rnd, tmp_path, name="h100only")
    assert code == 1 and report["verdict"] == "fail"
    code, report = run_preflight(rnd, tmp_path, "--allow-any-gpu", name="a2")
    assert code == 0, report["failing_checks"][:3]


def test_check_kinds_distinguish_integrity_from_coverage(rnd, tmp_path):
    edit(rnd, "complex", "300", "spark_cpu", reject("cx_distinct", "MemoryError", "out of memory"))
    edit(rnd, "baseline", "30", "spark_gpu", lambda s: s["records"][0]["rows"][0].update(total=-1.0))
    _, report = run_preflight(rnd, tmp_path)
    kinds = {c["status"]: c.get("kind") for c in report["checks"] if c["status"] in ("FAIL", "COVERAGE", "XFAIL")}
    assert kinds == {"FAIL": "integrity", "COVERAGE": "coverage", "XFAIL": "coverage"}


# ---- the error classification table (amendment A1) ---------------------------------------------------------------------------

@pytest.mark.parametrize("engine, type_name, message, outcome, klass", [
    ("polars_gpu", "NotImplementedError", "Unsupported over(...) only expression: UnaryFunction=", "coverage", "unsupported_operation"),
    ("polars_gpu", "ComputeError", "regex with inline flag (?s) is not supported by libcudf", "coverage", "unsupported_operation"),
    ("polars_gpu", "MemoryError", "std::bad_alloc: out_of_memory: CUDA error at pool_memory_resource.hpp:131", "coverage", "resource_exhaustion"),
    ("polars_gpu", "RuntimeError", "CUDA error: cudaErrorMemoryAllocation", "coverage", "resource_exhaustion"),
    ("polars_gpu", "RuntimeError", "RMM failure: allocation failed", "coverage", "resource_exhaustion"),
    ("polars_gpu", "RuntimeError", "CUDA error: an illegal memory access was encountered", "integrity", "unclassified"),
    ("polars_gpu", "AssertionError", "plan mismatch", "integrity", "unclassified"),
    ("polars_gpu", "NotImplementedError", "unsupported; falling back to CPU", "integrity", "silent_cpu_fallback"),
    ("polars_gpu", "RuntimeError", "polars_gpu session guard: an unsupported query did not raise", "integrity", "silent_cpu_fallback"),
    ("spark_gpu", "RuntimeError", "Missing GPU scan/aggregate in executed plan for q03: []", "integrity", "silent_cpu_fallback"),
    ("spark_gpu", "Py4JJavaError", "com.nvidia.spark.rapids.jni.GpuSplitAndRetryOOM: GPU OutOfMemory", "coverage", "resource_exhaustion"),
    ("spark_gpu", "Py4JJavaError", "org.apache.spark.memory.SparkOutOfMemoryError: Unable to acquire 64 bytes of memory", "coverage", "resource_exhaustion"),
    ("spark_gpu", "UnsupportedOperationException", "Operation not supported on the GPU: ExpandExec", "coverage", "unsupported_operation"),
    ("spark_gpu", "AnalysisException", "Column 'x' does not exist", "integrity", "unclassified"),
    ("spark_cpu", "Py4JJavaError", "java.lang.OutOfMemoryError: Java heap space", "coverage", "resource_exhaustion"),
    ("spark_cpu", "OSError", "[Errno 12] Cannot allocate memory", "coverage", "resource_exhaustion"),
    ("spark_cpu", "NotImplementedError", "not supported", "integrity", "unclassified"),
    ("spark_cpu", "OSError", "[Errno 28] No space left on device", "integrity", "unclassified"),
    ("polars", "MemoryError", "", "coverage", "resource_exhaustion"),
    ("polars", "NotImplementedError", "Unsupported", "integrity", "unclassified"),
])
def test_error_classification_rules(engine, type_name, message, outcome, klass):
    verdict = preflight.classify_error(engine, type_name, message)
    assert (verdict["outcome"], verdict["class"]) == (outcome, klass), verdict


def test_unmatched_errors_are_integrity_failures_by_default():
    verdict = preflight.classify_error("polars_gpu", "KeyError", "q99")
    assert verdict == {"rule": "none", "outcome": "integrity", "class": "unclassified"}
    assert all(r.outcome in ("coverage", "integrity") for r in preflight.ERROR_RULES)
    assert preflight.ERROR_RULES[0].outcome == "integrity"  # the fallback rule is consulted before any coverage rule


def test_the_report_lists_the_rules_it_applied(rnd, tmp_path):
    _, report = run_preflight(rnd, tmp_path)
    rules = {r["id"]: r for r in report["coverage"]["rules"]}
    assert rules["I1-silent-cpu-fallback"]["outcome"] == "integrity" and rules["C1-resource-exhaustion"]["outcome"] == "coverage"
    assert all(r["message_pattern"] or r["type_pattern"] for r in rules.values())
    assert report["coverage"]["policy"] == "classified" and report["coverage"]["predeclared"] == list(DECLARED)


# ---- inputs and evidence are tied down ------------------------------------------------------------------------------------------

def test_report_records_the_manifests_and_the_exact_bytes_the_verdict_covers(rnd, tmp_path):
    _, report = run_preflight(rnd, tmp_path)
    assert set(report["inputs"]["manifest_sha256"]) == {f"{s}/sf{sc}" for s, scales in SUITE_SCALES.items() for sc in scales}
    assert report["inputs"]["manifest_sha256"]["complex/sf300"] == manifest_digest("300")
    evidence = report["evidence_sha256"]
    assert len(evidence) == 2 + 32  # two schedules + one results.json per session
    import hashlib as _h
    assert evidence["baseline/sf10/spark_gpu/results.json"] == _h.sha256((rnd / "baseline/sf10/spark_gpu/results.json").read_bytes()).hexdigest()


def test_baseline_and_complex_must_read_the_same_dataset_per_scale(rnd, tmp_path):
    for engine in ENGINES:
        edit(rnd, "complex", "100", engine, lambda s: s.update(manifest_sha256="a" * 64))
    (rnd / "complex" / "sf100" / "manifest.json").write_text("other")
    _, report = run_preflight(rnd, tmp_path)
    assert report["verdict"] == "fail" and any(c["id"] == "input_consistency" and c["status"] == "FAIL" for c in report["checks"])


def test_a_dataset_that_changed_since_an_earlier_round_fails_the_round(rnd, tmp_path):
    other = build_round(tmp_path / "other", 2)
    _, first = run_preflight(other, tmp_path, round_number=2, name="r2")
    code, _ = run_preflight(rnd, tmp_path, "--prior-reports", str(tmp_path / "r2.json"), name="same")
    assert code == 0
    for engine in ENGINES:
        edit(rnd, "baseline", "30", engine, lambda s: s.update(manifest_sha256="b" * 64))
    (rnd / "baseline" / "sf30" / "manifest.json").write_text("changed")
    code, report = run_preflight(rnd, tmp_path, "--prior-reports", str(tmp_path / "r2.json"), name="changed")
    assert code == 1 and any(c["id"] == "input_consistency" and c["status"] == "FAIL" for c in report["checks"])
