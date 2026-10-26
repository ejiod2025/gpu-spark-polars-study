"""Plotters accept four-engine reports, mark engines without a timing, and fail closed on tampered inputs."""
from __future__ import annotations

import csv
import json

import pytest

pytest.importorskip("matplotlib")

import test_analysis_support as S  # noqa: E402

import analyze_complex  # noqa: E402
import analyze_crossover  # noqa: E402
import analyze_results  # noqa: E402
import plot_complex  # noqa: E402
import plot_crossover  # noqa: E402
import plot_results  # noqa: E402

GPU_ERROR = {("10", "polars_gpu"): {"query_errors": {"q03": ("NotImplementedError", "unsupported on the GPU")}}}


@pytest.fixture(autouse=True)
def headless(monkeypatch):
    monkeypatch.setenv("MPLBACKEND", "Agg")


def report_dir(tmp_path, kind="crossover", engines=S.ENGINES4, specs=None, env=None):
    suite = "complex" if kind == "complex" else "baseline"
    scales = ("30", "100") if suite == "complex" else ("1", "10")
    root = S.build_root(tmp_path / "root", suite=suite, scales=scales, engines=engines, specs=specs, env=env,
                        cleanup="verified_gc" if suite == "complex" else "none")
    module = {"crossover": analyze_crossover, "results": analyze_results, "complex": analyze_complex}[kind]
    code, _, err = S.run_main(module, ["--results", root, "--output", tmp_path / "out"])
    assert code == 0, err
    return tmp_path / "out"


def edit_csv(path, change):
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    change(rows)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_crossover_plot_four_engines_with_a_failed_cell(tmp_path):
    out = report_dir(tmp_path, specs=GPU_ERROR)
    code, printed, err = S.run_main(plot_crossover, ["--report", out])
    assert code == 0 or code is None, err
    assert (out / "crossover.png").stat().st_size > 10_000
    svg = (out / "crossover.svg").read_text()
    assert "Polars GPU" in svg and "H100" in svg and "A100" not in svg
    assert "Polars CPU 2.0.0" in svg and "Polars GPU 2.0.0" in svg and "cuDF-Polars 25.12.0" in svg
    assert "CPU/GPU Spark 3.5.9" in svg and "Spark GPU plugin 26.08.1" in svg
    assert "Primary Polars CPU/GPU versions matched (2.0.0)" in svg and "secondary CPU software-stack control is not shown" in svg


def test_crossover_plot_legacy_report_keeps_a100_wording(tmp_path):
    out = report_dir(tmp_path, engines=["polars", "spark_cpu", "spark_gpu"], env=S.A100_ENV)
    assert S.run_main(plot_crossover, ["--report", out])[0] in (0, None)
    svg = (out / "crossover.svg").read_text()
    assert "GPU Spark (A100)" in svg and "Polars GPU" not in svg
    assert "software-stack control" not in svg and "cuDF-Polars" not in svg


def test_crossover_plot_rejects_a_missing_timing_without_a_recorded_failure(tmp_path):
    out = report_dir(tmp_path)
    edit_csv(out / "summary.csv", lambda rows: rows[0].update(polars_gpu_median_seconds=""))
    code, _, err = S.run_main(plot_crossover, ["--report", out])
    assert code == 1 and "without a matching recorded failure" in err


def test_crossover_plot_rejects_a_ratio_for_a_failed_cell(tmp_path):
    out = report_dir(tmp_path, specs=GPU_ERROR)
    edit_csv(out / "summary.csv", lambda rows: [r.update(polars_over_polars_gpu="2.0") for r in rows if r["polars_gpu_status"] == "failed"])
    code, _, err = S.run_main(plot_crossover, ["--report", out])
    assert code == 1 and "Ratio reported for a cell without timing" in err


def test_crossover_plot_rejects_engine_set_drift_between_csv_and_evidence(tmp_path):
    out = report_dir(tmp_path)
    evidence = json.loads((out / "correctness.json").read_text())
    evidence["engines"] = ["polars", "spark_cpu", "spark_gpu"]
    (out / "correctness.json").write_text(json.dumps(evidence))
    code, _, err = S.run_main(plot_crossover, ["--report", out])
    assert code == 1 and "engines do not match" in err


def test_complex_plot_four_engines_with_a_failed_case(tmp_path):
    specs = {("100", "polars_gpu"): {"query_errors": {"cx_window": ("NotImplementedError", "shift().over()")}}}
    out = report_dir(tmp_path, "complex", specs=specs)
    code, _, err = S.run_main(plot_complex, ["--report", out])
    assert code in (0, None), err
    svg = (out / "complex.svg").read_text()
    assert "Polars GPU" in svg and "FAILED" in svg and "1 case(s) without a Polars GPU timing" in svg
    assert "Polars CPU 2.0.0" in svg and "Polars GPU 2.0.0" in svg and "cuDF-Polars 25.12.0" in svg
    assert "CPU/GPU Spark 3.5.9" in svg and "Spark GPU plugin 26.08.1" in svg
    assert "secondary CPU software-stack control is not shown" in svg


def test_complex_plot_rejects_a_missing_timing_without_a_recorded_failure(tmp_path):
    out = report_dir(tmp_path, "complex")
    edit_csv(out / "summary.csv", lambda rows: rows[0].update(polars_gpu_median_seconds=""))
    code, _, err = S.run_main(plot_complex, ["--report", out])
    assert code == 1 and "without a matching recorded failure" in err


def test_complex_plot_rejects_status_drift_from_included_case_evidence(tmp_path):
    specs = {("100", "polars_gpu"): {"query_errors": {"cx_window": ("NotImplementedError", "x")}}}
    out = report_dir(tmp_path, "complex", specs=specs)
    evidence = json.loads((out / "correctness.json").read_text())
    for case in evidence["included_cases"]:
        case["polars_gpu_status"] = "ok"
    (out / "correctness.json").write_text(json.dumps(evidence))
    code, _, err = S.run_main(plot_complex, ["--report", out])
    assert code == 1 and "status differs from included-case evidence" in err


def test_results_plot_marks_failed_queries(tmp_path):
    out = report_dir(tmp_path, "results", specs=GPU_ERROR)
    assert S.run_main(plot_results, ["--report", out])[0] in (0, None)
    svg = (out / "comparison.svg").read_text()
    assert "Polars GPU (cuDF, strict)" in svg and "FAILED" in svg and "GPU Spark (H100)" in svg and "A100" not in svg
