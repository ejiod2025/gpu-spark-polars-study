"""CPU stack controls must preserve inputs, sources and protocol before timing comparisons."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path

import pytest

import test_analysis_support as S
import analyze_polars_control as control
import complex_workloads
import workloads


def load(path):
    return json.loads(Path(path).read_text())


def rewrite(path, change):
    data = load(path)
    change(data)
    S.write(path, data)


def roots(tmp_path, suite="baseline"):
    registry = complex_workloads if suite == "complex" else workloads
    scales = ("30", "100") if suite == "complex" else ("1", "10")
    sources = {name: hashlib.sha256((S.REPO / name).read_bytes()).hexdigest()
               for name in control.REQUIRED_SOURCES + control.COMPLEX_SOURCES}
    env = copy.deepcopy(S.H100_ENV)
    env.update(python="3.11.13 (primary stack)", source_sha256=sources)
    specs = {(scale, engine): {"version": "1.44.2"} for scale in scales for engine in ("polars", "polars_gpu")}
    if suite == "complex":
        for scale in scales:
            specs[(scale, "polars_gpu")]["query_errors"] = {
                "cx_q13": ("NotImplementedError", "unsupported regex"), "cx_window": ("NotImplementedError", "unsupported rolling")}
    primary = S.build_root(tmp_path / "primary", specs=specs, env=env, suite=suite, scales=scales,
                           cleanup="verified_gc" if suite == "complex" else "none")
    other_env = copy.deepcopy(env)
    other_env["python"] = "3.10.12 (control stack)"
    other = S.build_root(tmp_path / "control", engines=["polars"], env=other_env, suite=suite, scales=scales,
                         cleanup="verified_gc" if suite == "complex" else "none",
                         specs={(scale, "polars"): {"version": "2.0.0", "time_factor": 0.8} for scale in scales})
    for root in (primary, other):
        for path in root.glob("sf*/*/results.json"):
            def contract(data):
                data.update(suite_version=getattr(registry, "SUITE_VERSION", "baseline-v1"),
                            query_descriptions={q: registry.DESCRIPTIONS[q] for q in registry.QUERY_IDS},
                            failure_policy="record_and_continue" if suite == "complex" else "raise")
                data.setdefault("query_execution_status", "complete")
                if suite == "baseline":
                    data.update(query_cleanup_policy="none", cleanup_records=[])
            rewrite(path, contract)
    return primary, other


def run(tmp_path, primary, other, *flags):
    out = tmp_path / "out"
    code, stdout, stderr = S.run_main(control, ["--primary-root", primary, "--control-root", other, "--output", out, *flags])
    return code, out, stdout, stderr


def csv_rows(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def test_complete_control_matches_every_output_and_reports_stack_caveat(tmp_path):
    primary, other = roots(tmp_path)
    code, out, _, err = run(tmp_path, primary, other, "--include-polars-gpu")
    assert code == 0, err
    evidence = load(out / "correctness.json")
    assert evidence["passed"] is True
    assert evidence["counts"] == {"scales_expected": 2, "scales_validated": 2, "control_outputs_expected": 48,
                                   "control_outputs_compared": 48, "control_output_mismatches": 0}
    assert evidence["primary_validation"]["comparisons"] == 192
    assert evidence["polars_gpu_reference_included"] is True
    scores = csv_rows(out / "suite_scores.csv")
    assert len(scores) == 2 and float(scores[0]["primary_cpu_over_control_cpu"]) == pytest.approx(1.25)
    assert float(scores[0]["control_cpu_over_polars_gpu"]) == pytest.approx(1.6)
    report = (out / "CPU_STACK_CONTROL.md").read_text()
    assert "CPU software-stack control" in report and "not an isolated Polars version effect" in report
    assert "Python 3.11.13" in report and "Python 3.10.12" in report
    assert "were not rerun under the control" in report
    assert "control_cpu_median_seconds" in csv_rows(out / "summary.csv")[0]


def test_gpu_reference_is_opt_in(tmp_path):
    primary, other = roots(tmp_path)
    code, out, _, _ = run(tmp_path, primary, other)
    assert code == 0
    assert load(out / "correctness.json")["polars_gpu_reference_included"] is False
    assert not any("polars_gpu" in key for key in csv_rows(out / "summary.csv")[0])


@pytest.mark.parametrize("change,reason", [
    (lambda d: d["records"].pop(), "missing query/phase/trial"),
    (lambda d: d["records"][0]["rows"][0].__setitem__("n", d["records"][0]["rows"][0]["n"] + 1), "differ from the matching"),
    (lambda d: d.__setitem__("cpu_threads", 25), "cpu_threads"),
    (lambda d: d.__setitem__("timing_scope", "execution only"), "timing_scope"),
    (lambda d: d.__setitem__("cache_policy", "application cached"), "cache_policy"),
    (lambda d: d.__setitem__("query_cleanup_policy", "verified_gc"), "cleanup lifecycle"),
    (lambda d: d.__setitem__("query_errors", [{"query": "q06", "type": "RuntimeError", "message": "failure"}]), "execution or cleanup failure"),
    (lambda d: d.__setitem__("runs", 1), "query/phase/trial"),
])
def test_bad_control_scale_never_has_a_partial_score(tmp_path, change, reason):
    primary, other = roots(tmp_path)
    rewrite(other / "sf1/polars/results.json", change)
    code, out, _, _ = run(tmp_path, primary, other)
    assert code == 1
    evidence = load(out / "correctness.json")
    assert not evidence["passed"] and reason in evidence["failures"][0]["reason"]
    score = next(row for row in csv_rows(out / "suite_scores.csv") if row["scale"] == "sf1")
    assert score["control_cpu_sum_medians_seconds"] == score["primary_cpu_over_control_cpu"] == ""
    assert all(row["control_cpu_median_seconds"] == "" for row in csv_rows(out / "summary.csv") if row["scale"] == "sf1")


def test_missing_saved_control_plan_is_a_failure(tmp_path):
    primary, other = roots(tmp_path)
    next((other / "sf1/polars/plans").iterdir()).unlink()
    code, out, _, _ = run(tmp_path, primary, other)
    assert code == 1 and "saved plan" in load(out / "correctness.json")["failures"][0]["reason"]


def test_source_difference_refuses_all_control_timings_and_gpu_reference(tmp_path):
    primary, other = roots(tmp_path)
    rewrite(other / "environment.json", lambda d: d["source_sha256"].__setitem__("worker.py", "a" * 64))
    code, out, _, _ = run(tmp_path, primary, other, "--include-polars-gpu")
    evidence = load(out / "correctness.json")
    assert code == 1 and "source SHA-256 maps differ" in evidence["failures"][0]["reason"]
    assert csv_rows(out / "summary.csv") == [] and not evidence["polars_gpu_reference_included"]


def test_control_manifest_must_be_byte_identical(tmp_path):
    primary, other = roots(tmp_path)
    path = other / "sf1/manifest.json"
    path.write_text(path.read_text() + "\n")
    code, out, _, _ = run(tmp_path, primary, other)
    assert code == 1 and "input manifests differ" in load(out / "correctness.json")["failures"][0]["reason"]


def test_primary_gpu_mismatch_refuses_control_and_gpu_references(tmp_path):
    primary, other = roots(tmp_path)
    rewrite(primary / "sf1/polars_gpu/results.json", lambda d: d["records"][0]["rows"][0].__setitem__("n", 999999))
    code, out, _, _ = run(tmp_path, primary, other, "--include-polars-gpu")
    evidence = load(out / "correctness.json")
    assert code == 1 and "primary four-engine evidence is not fully validated" in evidence["failures"][0]["reason"]
    assert not evidence["polars_gpu_reference_included"] and csv_rows(out / "summary.csv") == []


def test_failed_schedule_cannot_be_overridden_by_complete_json(tmp_path):
    primary, other = roots(tmp_path)
    path = other / "schedule.json"
    data = load(path)
    data[0].update(status="failed", returncode=137)
    S.write(path, data)
    code, out, _, _ = run(tmp_path, primary, other)
    assert code == 1 and "schedule did not finish successfully" in load(out / "correctness.json")["failures"][0]["reason"]


def test_missing_control_scale_refuses_incomplete_series(tmp_path):
    import shutil
    primary, other = roots(tmp_path)
    shutil.rmtree(other / "sf10")
    code, out, _, _ = run(tmp_path, primary, other)
    assert code == 1 and "scale sets differ" in load(out / "correctness.json")["failures"][0]["reason"]
    assert csv_rows(out / "suite_scores.csv") == []


def test_existing_output_is_never_overwritten(tmp_path):
    primary, other = roots(tmp_path)
    code, out, _, _ = run(tmp_path, primary, other)
    before = (out / "correctness.json").read_bytes()
    assert code == 0
    assert run(tmp_path, primary, other)[0] == 2
    assert (out / "correctness.json").read_bytes() == before


def test_complex_control_compares_all_six_cpu_cases_with_partial_gpu_references(tmp_path):
    primary, other = roots(tmp_path, "complex")
    code, out, _, err = run(tmp_path, primary, other, "--suite", "complex", "--include-polars-gpu")
    assert code == 0, err
    evidence = load(out / "correctness.json")
    assert evidence["passed"] is True and evidence["counts"]["control_outputs_compared"] == 48
    assert len(evidence["primary_validation"]["engine_failures"]) == 4
    rows = csv_rows(out / "summary.csv")
    unsupported = [row for row in rows if row["query"] in ("cx_q13", "cx_window")]
    assert len(unsupported) == 4 and all(row["control_cpu_median_seconds"] for row in unsupported)
    assert all(row["polars_gpu_median_seconds"] == row["control_cpu_over_polars_gpu"] == "" for row in unsupported)
    scores = csv_rows(out / "suite_scores.csv")
    assert all(row["control_cpu_sum_medians_seconds"] and row["primary_cpu_over_control_cpu"] for row in scores)
    assert all(row["polars_gpu_sum_medians_seconds"] == row["control_cpu_over_polars_gpu"] == "" for row in scores)
    report = (out / "CPU_STACK_CONTROL.md").read_text()
    assert "Suite: **complex**" in report and "unsupported queries have no GPU timing or complete GPU suite score" in report


def test_complex_control_missing_cleanup_barrier_has_no_score(tmp_path):
    primary, other = roots(tmp_path, "complex")
    rewrite(other / "sf30/polars/results.json", lambda d: d["cleanup_records"].pop())
    code, out, _, _ = run(tmp_path, primary, other, "--suite", "complex")
    assert code == 1
    evidence = load(out / "correctness.json")
    assert "exactly one passed cleanup" in evidence["failures"][0]["reason"]
    row = next(row for row in csv_rows(out / "suite_scores.csv") if row["scale"] == "sf30")
    assert row["control_cpu_sum_medians_seconds"] == ""


def test_complex_query_source_difference_refuses_cpu_timings(tmp_path):
    primary, other = roots(tmp_path, "complex")
    rewrite(other / "environment.json", lambda d: d["source_sha256"].__setitem__("complex_analytics.py", "a" * 64))
    code, out, _, _ = run(tmp_path, primary, other, "--suite", "complex")
    assert code == 1 and "complex_analytics.py" in load(out / "correctness.json")["failures"][0]["reason"]
    assert csv_rows(out / "summary.csv") == []
