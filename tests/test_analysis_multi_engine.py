"""Four-engine analysis: engine discovery, failure visibility, GPU validation, ratios, and the legacy three-engine shape."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import test_analysis_support as S  # noqa: E402  (also puts the repo root on sys.path)

import analyze_complex  # noqa: E402
import analyze_crossover  # noqa: E402
import analyze_results  # noqa: E402

LEGACY = ["polars", "spark_cpu", "spark_gpu"]
CANONICAL = ["polars", "polars_gpu", "spark_cpu", "spark_gpu"]


def read_csv(path):
    with Path(path).open(newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def load(path):
    return json.loads(Path(path).read_text())


def engines_in_header(header):
    return [e for e in CANONICAL if e + "_median_seconds" in header]


def results(tmp_path, **kwargs):
    root = S.build_root(tmp_path / "root", **kwargs)
    code, out, err = S.run_main(analyze_results, ["--results", root, "--output", tmp_path / "out"])
    return code, tmp_path / "out", err


def crossover(tmp_path, **kwargs):
    root = S.build_root(tmp_path / "root", **kwargs)
    code, out, err = S.run_main(analyze_crossover, ["--results", root, "--output", tmp_path / "out"])
    return code, tmp_path / "out", err


def complex_run(tmp_path, scales=("30", "100"), **kwargs):
    root = S.build_root(tmp_path / "root", suite="complex", scales=scales, cleanup="verified_gc", **kwargs)
    code, out, err = S.run_main(analyze_complex, ["--results", root, "--output", tmp_path / "out"])
    return code, tmp_path / "out", err


GPU_ERROR = {("10", "polars_gpu"): {"query_errors": {"q03": ("NotImplementedError", "join on this key is not supported on the GPU")}}}


# ---------------------------------------------------------------- baseline: results / crossover

def test_four_engines_are_discovered_in_canonical_order(tmp_path):
    code, out, _ = results(tmp_path)
    assert code == 0
    header, rows = read_csv(out / "summary.csv")
    assert engines_in_header(header) == CANONICAL
    evidence = load(out / "correctness.json")
    assert evidence["engines"] == CANONICAL and evidence["passed"] is True
    assert {r["polars_gpu_status"] for r in rows} == {"ok"}
    for column in ("polars_over_polars_gpu", "polars_gpu_over_spark_gpu", "polars_over_spark_gpu", "spark_cpu_over_spark_gpu"):
        assert column in header
    row = rows[0]
    assert float(row["polars_over_polars_gpu"]) == pytest.approx(float(row["polars_median_seconds"]) / float(row["polars_gpu_median_seconds"]))
    assert float(row["polars_gpu_over_spark_gpu"]) == pytest.approx(float(row["polars_gpu_median_seconds"]) / float(row["spark_gpu_median_seconds"]))
    report = (out / "REPORT.md").read_text()
    assert "Pairwise speedup matrices" in report and "Polars / Polars GPU" in report and "Polars GPU / GPU Spark" in report
    pair_header, pairs = read_csv(out / "pairwise_speedups.csv")
    # 4 engines -> 6 unordered pairs; each has one ALL row and six per-query rows per scale (2 scales)
    assert len(pairs) == 6 * (1 + 6) * 2
    all_row = next(p for p in pairs if (p["scale"], p["numerator"], p["denominator"], p["query"]) == ("sf1", "polars", "polars_gpu", "ALL"))
    assert float(all_row["ratio"]) == pytest.approx(2.0) and all_row["queries_used"] == "6"


def test_legacy_three_engine_tree_gains_nothing(tmp_path):
    code, out, _ = results(tmp_path, engines=LEGACY)
    assert code == 0
    header, _ = read_csv(out / "summary.csv")
    assert engines_in_header(header) == LEGACY
    assert not any("polars_gpu" in column for column in header)
    assert "engines" not in load(out / "correctness.json")
    assert not (out / "pairwise_speedups.csv").exists()
    assert header[-2:] == ["spark_cpu_over_spark_gpu", "polars_over_spark_gpu"]
    assert (out / "REPORT.md").read_text().startswith("# Polars, CPU Spark, and GPU Spark experiment")


def test_missing_legacy_engine_is_still_an_error(tmp_path):
    code, out, err = results(tmp_path, engines=LEGACY, skip={("10", "spark_gpu")})
    assert code == 1 and "sf10/spark_gpu" in err
    assert not (out / "REPORT.md").exists()


def test_polars_gpu_query_error_is_an_explicit_failed_cell(tmp_path):
    code, out, err = results(tmp_path, specs=GPU_ERROR)
    assert code == 0, err
    header, rows = read_csv(out / "summary.csv")
    failed = next(r for r in rows if (r["scale"], r["query"]) == ("sf10", "q03"))
    assert failed["polars_gpu_status"] == "failed" and "NotImplementedError" in failed["polars_gpu_failure"]
    assert failed["polars_gpu_median_seconds"] == "" and failed["polars_over_polars_gpu"] == "" and failed["polars_gpu_over_spark_gpu"] == ""
    # nothing is lost for the other engines, and the other queries still carry GPU timings
    assert all(failed[e + "_median_seconds"] for e in LEGACY)
    assert sum(r["polars_gpu_status"] == "ok" for r in rows) == 11
    report = (out / "REPORT.md").read_text()
    assert "FAILED: NotImplementedError" in report
    assert "n/a (5/6 queries; q03 failed)" in report
    assert "5.0" not in report.split("| sf10 | 3 |")[1].split("\n")[0], "no plain sum over the 5 surviving queries"
    evidence = load(out / "correctness.json")
    assert [(f["scale"], f["engine"], f["query"], f["kind"]) for f in evidence["engine_failures"]] == [("sf10", "polars_gpu", "q03", "failed")]
    assert evidence["integrity_violations"] == []
    # a suite-level pair involving the incomplete engine is n/a; it is never a ratio over the surviving queries
    matrix = report.split("### sf10")[1].split("### ")[0]
    assert "Polars GPU: 5/6 queries; q03 failed" in matrix
    assert "| Polars | — | n/a | 0.33x | 0.50x |" in matrix
    pairs = read_csv(out / "pairwise_speedups.csv")[1]
    partial = next(p for p in pairs if (p["scale"], p["numerator"], p["denominator"], p["query"]) == ("sf10", "polars", "polars_gpu", "ALL"))
    assert partial["ratio"] == "" and partial["numerator_seconds"] == "" and partial["queries_used"] == "5" and partial["queries_total"] == "6"
    healthy = next(p for p in pairs if (p["scale"], p["numerator"], p["denominator"], p["query"]) == ("sf10", "polars", "polars_gpu", "q06"))
    assert float(healthy["ratio"]) == pytest.approx(2.0), "per-query ratios of successful queries stay"
    q03 = next(p for p in pairs if (p["scale"], p["numerator"], p["denominator"], p["query"]) == ("sf10", "polars", "polars_gpu", "q03"))
    assert q03["ratio"] == "" and q03["denominator_seconds"] == ""


def test_polars_gpu_session_failure_marks_every_query_failed(tmp_path):
    specs = {("10", "polars_gpu"): {"incomplete": True, "global_failure": True}}
    code, out, err = results(tmp_path, specs=specs)
    assert code == 0, err
    _, rows = read_csv(out / "summary.csv")
    sf10 = [r for r in rows if r["scale"] == "sf10"]
    assert {r["polars_gpu_status"] for r in sf10} == {"failed"} and not any(r["polars_gpu_median_seconds"] for r in sf10)
    assert all(r["polars_median_seconds"] and r["spark_gpu_median_seconds"] for r in sf10)
    assert {r["polars_gpu_status"] for r in rows if r["scale"] == "sf1"} == {"ok"}
    report = (out / "REPORT.md").read_text()
    assert "FAILED: session recorded a global failure" in report
    failure = next(f for f in load(out / "correctness.json")["engine_failures"] if f["query"] is None)
    assert failure["scale"] == "sf10" and failure["kind"] == "failed"


def test_missing_polars_gpu_results_in_one_scale_stays_visible(tmp_path):
    code, out, err = results(tmp_path, skip={("10", "polars_gpu")})
    assert code == 0, err
    _, rows = read_csv(out / "summary.csv")
    assert {r["polars_gpu_status"] for r in rows if r["scale"] == "sf10"} == {"failed"}
    assert "results.json unavailable (FileNotFoundError)" in (out / "REPORT.md").read_text()


@pytest.mark.parametrize("spec", [
    {"fallback_class": "cpu_engine"},
    {"fallback_class": None},
    {"session_overrides": {"polars_execution_engine": "streaming"}},
    {"session_overrides": {"polars_gpu": {"executor": "streaming", "raise_on_fail": False}}},
    {"session_overrides": {"polars_gpu": None}},
], ids=["cpu-class", "no-class", "streaming-engine", "raise-on-fail-false", "no-metadata"])
def test_polars_gpu_without_strict_evidence_is_invalid_and_never_timed(tmp_path, spec):
    code, out, err = results(tmp_path, specs={("1", "polars_gpu"): spec})
    assert code == 1, "an integrity violation must fail the run"
    assert "invalid" in err
    _, rows = read_csv(out / "summary.csv")
    sf1 = [r for r in rows if r["scale"] == "sf1"]
    assert {r["polars_gpu_status"] for r in sf1} == {"invalid"} and not any(r["polars_gpu_median_seconds"] for r in sf1)
    assert "INVALID" in (out / "REPORT.md").read_text()
    evidence = load(out / "correctness.json")
    assert evidence["integrity_violations"] and all(f["kind"] == "invalid" for f in evidence["integrity_violations"])
    # a CPU run labelled GPU never contributes comparisons
    assert not [c for c in evidence["comparisons"] if c["engine"] == "polars_gpu" and c["scale"] == "sf1"]


def test_polars_gpu_mismatch_beyond_tolerance_is_reported_not_loosened(tmp_path):
    code, out, err = results(tmp_path, specs={("1", "polars_gpu"): {"perturb": {"q06": 1e-5}}})
    assert code == 1 and "mismatch" in err
    _, rows = read_csv(out / "summary.csv")
    row = next(r for r in rows if (r["scale"], r["query"]) == ("sf1", "q06"))
    assert row["polars_gpu_status"] == "mismatch" and row["polars_gpu_median_seconds"] == ""
    agreement = {(a["scale"], a["query"]): a for a in load(out / "correctness.json")["polars_gpu_numeric_agreement"]}
    finding = agreement[("sf1", "q06")]
    assert finding["failed_comparisons"] == finding["comparisons"] == 4
    assert finding["max_relative_numeric_difference"] == pytest.approx(1e-5 / (1 + 1e-5), rel=1e-3)
    assert agreement[("sf1", "q01")]["failed_comparisons"] == 0
    assert "MISMATCH" in (out / "REPORT.md").read_text()
    evidence = load(out / "correctness.json")
    assert evidence["passed"] is False and evidence["correctness_verified"] is False
    assert evidence["all_requested_engine_cases_validated"] is False


@pytest.mark.parametrize("analyzer", [analyze_results, analyze_crossover, analyze_complex])
def test_strict_gpu_guard_cannot_be_a_declaration_only(tmp_path, analyzer):
    suite = "complex" if analyzer is analyze_complex else "baseline"
    root = S.build_root(tmp_path / "root", suite=suite, scales=("1",),
                        cleanup="verified_gc" if suite == "complex" else "none")
    path = root / "sf1" / "polars_gpu" / "results.json"
    session = load(path)
    del session["polars_gpu"]["session_guard"]
    S.write(path, session)
    code, _, _ = S.run_main(analyzer, ["--results", root, "--output", tmp_path / "out"])
    assert code == 1
    evidence = load(tmp_path / "out" / "correctness.json")
    assert evidence["passed"] is False and evidence["correctness_verified"] is False
    assert evidence["integrity_violations"]
    _, rows = read_csv(tmp_path / "out" / "summary.csv")
    assert all(row["polars_gpu_status"] == "invalid" for row in rows)
    assert all(row["polars_gpu_median_seconds"] == "" for row in rows)
    assert all(row["polars_median_seconds"] and row["spark_gpu_median_seconds"] for row in rows)


def test_streaming_gpu_fallback_warning_does_not_mean_cpu_fallback(tmp_path):
    root = S.build_root(tmp_path / "root", scales=("1",))
    path = root / "sf1" / "polars_gpu" / "results.json"
    session = load(path)
    for record in session["records"]:
        record["gpu_engine_warnings"] = ["UserWarning: Falling back to single-partition GPU execution"]
    S.write(path, session)
    code, _, err = S.run_main(analyze_results, ["--results", root, "--output", tmp_path / "out"])
    assert code == 0, err
    assert load(tmp_path / "out" / "correctness.json")["passed"] is True
    assert "Falling back to single-partition GPU execution" in (tmp_path / "out" / "REPORT.md").read_text()
    _, rows = read_csv(tmp_path / "out" / "summary.csv")
    assert all(row["polars_gpu_status"] == "ok" for row in rows)


def test_unsupported_polars_gpu_case_prevents_full_four_engine_coverage(tmp_path):
    code, out, err = complex_run(tmp_path, specs={("30", "polars_gpu"): {
        "query_errors": {"cx_q13": ("NotImplementedError", "unsupported operation")}}})
    assert code == 0, err
    evidence = load(out / "correctness.json")
    assert evidence["passed"] is True, "known unsupported cases are visible partial coverage, not an integrity mismatch"
    assert evidence["all_selected_cases_validated"] is False
    assert evidence["all_full_suites_validated"] is False
    sf30 = next(total for total in evidence["full_suite_totals"] if total["scale"] == "sf30")
    assert sf30["polars_gpu_sum_medians_seconds"] is None
    assert sf30["polars_sum_medians_seconds"] > 0


def test_polars_gpu_difference_within_tolerance_is_accepted_and_still_reported(tmp_path):
    code, out, err = results(tmp_path, specs={("1", "polars_gpu"): {"perturb": {"q06": 1e-12}}})
    assert code == 0, err
    finding = next(a for a in load(out / "correctness.json")["polars_gpu_numeric_agreement"] if (a["scale"], a["query"]) == ("sf1", "q06"))
    assert finding["failed_comparisons"] == 0 and 0 < finding["max_relative_numeric_difference"] < 1e-11
    assert f"{finding['max_relative_numeric_difference']:.3e}" in (out / "REPORT.md").read_text()


def test_engines_flag_selects_a_subset_and_names_what_it_left_out(tmp_path):
    root = S.build_root(tmp_path / "root")
    code, _, _ = S.run_main(analyze_results, ["--results", root, "--output", tmp_path / "legacy", "--engines", *LEGACY])
    assert code == 0
    header, _ = read_csv(tmp_path / "legacy" / "summary.csv")
    assert engines_in_header(header) == LEGACY
    assert load(tmp_path / "legacy" / "correctness.json")["engines_excluded_by_flag"] == ["polars_gpu"]
    code, _, _ = S.run_main(analyze_results, ["--results", root, "--output", tmp_path / "pg", "--engines", "polars", "polars_gpu"])
    assert code == 0, "polars + polars_gpu alone is analyzable; GPU evidence is the strict Polars GPU sessions"
    assert load(tmp_path / "pg" / "correctness.json")["gpu_execution_verified"] is True
    code, _, err = S.run_main(analyze_results, ["--results", root, "--output", tmp_path / "bad", "--engines", "spark_cpu"])
    assert code != 0


def test_peak_gpu_memory_is_reported_when_recorded_and_absent_otherwise(tmp_path):
    code, out, _ = results(tmp_path / "with", gpu_memory=True)
    assert code == 0
    header, rows = read_csv(out / "summary.csv")
    assert "polars_gpu_max_peak_gpu_memory_bytes" in header and "spark_gpu_max_peak_gpu_memory_bytes" in header
    assert "polars_max_peak_gpu_memory_bytes" not in header
    assert int(rows[0]["polars_gpu_max_peak_gpu_memory_bytes"]) == 8 * 1024**3
    assert "## Peak GPU memory" in (out / "REPORT.md").read_text()
    code, out, _ = results(tmp_path / "without")
    assert not any("gpu_memory" in c for c in read_csv(out / "summary.csv")[0])
    assert "## Peak GPU memory" not in (out / "REPORT.md").read_text()


def test_crossover_polars_gpu_failure_does_not_exclude_the_scale(tmp_path):
    code, out, err = crossover(tmp_path, specs=GPU_ERROR)
    assert code == 0, err
    evidence = load(out / "correctness.json")
    assert [s["scale"] for s in evidence["included_scales"]] == ["sf1", "sf10"] and evidence["excluded_scales"] == []
    assert evidence["engines"] == CANONICAL
    header, rows = read_csv(out / "summary.csv")
    assert engines_in_header(header) == CANONICAL
    report = (out / "CROSSOVER_REPORT.md").read_text()
    assert "FAILED: NotImplementedError" in report and "Polars GPU has no six-query aggregate at sf10" in report
    assert "## Engine failures and integrity findings" in report and "Polars GPU against Polars CPU brackets" in report


def test_crossover_orchestrator_failure_beats_a_complete_looking_polars_gpu_json(tmp_path):
    schedule = {("10", "polars_gpu"): {"status": "failed", "returncode": 137}}
    code, out, err = crossover(tmp_path, schedule_overrides=schedule)
    assert code == 0, err
    failures = load(out / "correctness.json")["engine_failures"]
    assert [(f["scale"], f["engine"], f["kind"]) for f in failures] == [("sf10", "polars_gpu", "failed")]
    assert "orchestrator status=failed, returncode=137" in failures[0]["reason"]
    _, rows = read_csv(out / "summary.csv")
    assert {r["polars_gpu_status"] for r in rows if r["scale"] == "sf10"} == {"failed"}
    assert not [c for c in load(out / "correctness.json")["comparisons"] if c["engine"] == "polars_gpu" and c["scale"] == "sf10"]


def test_crossover_legacy_engine_failure_still_excludes_the_scale(tmp_path):
    schedule = {("10", "spark_cpu"): {"status": "failed", "returncode": 1}}
    code, out, err = crossover(tmp_path, schedule_overrides=schedule)
    evidence = load(out / "correctness.json")
    assert [s["scale"] for s in evidence["excluded_scales"]] == ["sf10"] and [s["scale"] for s in evidence["included_scales"]] == ["sf1"]
    assert "spark_cpu: orchestrator status=failed" in err
    states = {s["engine"]: s["status"] for s in evidence["excluded_scales"][0]["engines"]}
    assert states["spark_cpu"] == "failed" and states["polars_gpu"] == "complete"


def test_crossover_polars_gpu_in_the_schedule_only_is_required(tmp_path):
    """A polars_gpu run that never produced a directory is discovered from schedule.json and shown as failed."""
    code, out, err = crossover(tmp_path, engines=S.ENGINES4, skip={("1", "polars_gpu"), ("10", "polars_gpu")},
                               schedule_overrides={("1", "polars_gpu"): {"status": "failed", "returncode": 1},
                                                   ("10", "polars_gpu"): {"status": "failed", "returncode": 1}})
    assert code == 0, err
    assert {r["polars_gpu_status"] for r in read_csv(out / "summary.csv")[1]} == {"failed"}
    assert "Polars GPU has no six-query aggregate at sf1, sf10" in (out / "CROSSOVER_REPORT.md").read_text()


def test_crossover_host_text_follows_the_recorded_host(tmp_path):
    code, out, _ = crossover(tmp_path / "h100")
    assert "NVIDIA H100 80GB HBM3" in (out / "CROSSOVER_REPORT.md").read_text() and "26 vCPU" in (out / "CROSSOVER_REPORT.md").read_text()
    assert "A100 40 GB" not in (out / "CROSSOVER_REPORT.md").read_text()
    code, out, _ = crossover(tmp_path / "a100", engines=LEGACY, env=S.A100_ENV)
    assert "The experiment targets one A100 40 GB VM with 30 CPU threads and 216 GiB host RAM" in (out / "CROSSOVER_REPORT.md").read_text()
    code, out, _ = crossover(tmp_path / "h100-legacy-engines", engines=LEGACY)
    assert "A100 40 GB" not in (out / "CROSSOVER_REPORT.md").read_text()


# ---------------------------------------------------------------- complex suite

def test_complex_four_engines_and_pairwise_ratios(tmp_path):
    code, out, err = complex_run(tmp_path)
    assert code == 0, err
    header, rows = read_csv(out / "summary.csv")
    assert engines_in_header(header) == CANONICAL and len(rows) == 12
    evidence = load(out / "correctness.json")
    assert evidence["engines"] == CANONICAL and evidence["passed"] is True
    assert {r["polars_gpu_status"] for r in rows} == {"ok"}
    assert float(rows[0]["polars_over_polars_gpu"]) == pytest.approx(2.0)
    assert all(t["polars_gpu_queries_timed"] == 6 for t in evidence["full_suite_totals"])
    assert (out / "pairwise_speedups.csv").exists()
    report = (out / "COMPLEX_REPORT.md").read_text()
    assert "Polars GPU" in report and "Pairwise speedup matrices" in report


def test_complex_polars_gpu_case_failure_keeps_the_case_and_hides_the_sum(tmp_path):
    specs = {("100", "polars_gpu"): {"query_errors": {"cx_window": ("NotImplementedError", "shift().over() is not supported")}}}
    code, out, err = complex_run(tmp_path, specs=specs)
    assert code == 0, err
    evidence = load(out / "correctness.json")
    assert evidence["excluded_cases"] == [] and len(evidence["included_cases"]) == 12
    _, rows = read_csv(out / "summary.csv")
    row = next(r for r in rows if (r["scale"], r["query"]) == ("sf100", "cx_window"))
    assert row["polars_gpu_status"] == "failed" and "NotImplementedError: shift().over()" in row["polars_gpu_failure"]
    assert row["polars_gpu_median_seconds"] == "" and row["polars_gpu_cleanup_seconds"] == "" and row["polars_median_seconds"]
    totals = {t["scale"]: t for t in evidence["full_suite_totals"]}
    assert totals["sf100"]["polars_gpu_sum_medians_seconds"] is None and totals["sf100"]["polars_gpu_queries_timed"] == 5
    assert totals["sf100"]["polars_over_polars_gpu"] is None and totals["sf30"]["polars_gpu_sum_medians_seconds"] is not None
    report = (out / "COMPLEX_REPORT.md").read_text()
    assert "FAILED: NotImplementedError: shift().over()" in report and "n/a (5/6 cases; cx_window failed)" in report


def test_complex_polars_gpu_session_failure_is_visible(tmp_path):
    specs = {("100", "polars_gpu"): {"incomplete": True, "global_failure": True}}
    code, out, err = complex_run(tmp_path, specs=specs)
    assert code == 0, err
    evidence = load(out / "correctness.json")
    assert evidence["excluded_cases"] == []
    _, rows = read_csv(out / "summary.csv")
    assert {r["polars_gpu_status"] for r in rows if r["scale"] == "sf100"} == {"failed"}
    assert "FAILED: engine session is incomplete" in (out / "COMPLEX_REPORT.md").read_text()


def test_complex_polars_gpu_fallback_is_invalid_and_fails_the_run(tmp_path):
    code, out, err = complex_run(tmp_path, specs={("30", "polars_gpu"): {"fallback_class": "cpu_engine"}})
    assert code == 1 and "invalid" in err
    _, rows = read_csv(out / "summary.csv")
    assert {r["polars_gpu_status"] for r in rows if r["scale"] == "sf30"} == {"invalid"}
    assert not any(r["polars_gpu_median_seconds"] for r in rows if r["scale"] == "sf30")


def test_complex_polars_gpu_mismatch_and_oracle_free_agreement(tmp_path):
    code, out, err = complex_run(tmp_path, specs={("30", "polars_gpu"): {"perturb": {"cx_q13": 1e-5}}})
    assert code == 1 and "mismatch" in err
    evidence = load(out / "correctness.json")
    finding = next(a for a in evidence["polars_gpu_numeric_agreement"] if (a["scale"], a["query"]) == ("sf30", "cx_q13"))
    assert finding["failed_comparisons"] == 4 and finding["max_relative_numeric_difference"] > 1e-8
    _, rows = read_csv(out / "summary.csv")
    assert next(r for r in rows if (r["scale"], r["query"]) == ("sf30", "cx_q13"))["polars_gpu_status"] == "mismatch"


def test_complex_legacy_engine_failure_still_excludes_the_case(tmp_path):
    specs = {("30", "spark_cpu"): {"query_errors": {"cx_q13": ("RuntimeError", "boom")}}}
    code, out, err = complex_run(tmp_path, specs=specs)
    evidence = load(out / "correctness.json")
    assert [(c["scale"], c["query"]) for c in evidence["excluded_cases"]] == [("sf30", "cx_q13")]
    assert "spark_cpu" in evidence["excluded_cases"][0]["reasons"][0]
    _, rows = read_csv(out / "summary.csv")
    assert ("sf30", "cx_q13") not in {(r["scale"], r["query"]) for r in rows}


def test_complex_legacy_three_engine_tree_has_legacy_shape(tmp_path):
    code, out, err = complex_run(tmp_path, engines=LEGACY, env=S.A100_ENV)
    assert code == 0, err
    evidence = load(out / "correctness.json")
    assert "engines" not in evidence and "engine_failures" not in evidence
    header, _ = read_csv(out / "summary.csv")
    assert not any("polars_gpu" in c for c in header) and not (out / "pairwise_speedups.csv").exists()
    assert all("polars_gpu" not in key for total in evidence["full_suite_totals"] for key in total)


def test_inspect_spark_metrics_lists_non_spark_engines_it_skips(tmp_path):
    import inspect_spark_metrics
    root = S.build_root(tmp_path / "root", scales=("1",))
    code, _, _ = S.run_main(inspect_spark_metrics, ["--results", root, "--output", tmp_path / "metrics.json"])
    result = load(tmp_path / "metrics.json")
    assert result["non_spark_engines_skipped"] == ["polars_gpu"]
    assert {s["engine"] for s in result["sessions"]} == {"spark_cpu", "spark_gpu"}
