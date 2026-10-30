"""compare_hardware.py: input-equivalence gate, host-shape caveats, version flags, failed-cell handling."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import test_analysis_support as S  # noqa: E402

import analyze_results  # noqa: E402
import compare_hardware  # noqa: E402

LEGACY = ["polars", "spark_cpu", "spark_gpu"]


def read_csv(path):
    with Path(path).open(newline="") as handle:
        return list(csv.DictReader(handle))


def sides(tmp_path, *, a_manifests=None, b_manifests=None, a_specs=None, b_specs=None, suite="baseline", scales=("1", "10"),
          a_engines=LEGACY, b_engines=S.ENGINES4, a_env=None, b_env=None, a_schedule=None):
    a = S.build_root(tmp_path / "a100", suite=suite, scales=scales, engines=a_engines, env=a_env or S.A100_ENV,
                     manifests=a_manifests, specs=a_specs, schedule_overrides=a_schedule,
                     cleanup="none")
    b = S.build_root(tmp_path / "h100", suite=suite, scales=scales, engines=b_engines, env=b_env or S.H100_ENV,
                     manifests=b_manifests, specs=b_specs, cleanup="none")
    return a, b


def run(tmp_path, a, b, *extra):
    code, out, err = S.run_main(compare_hardware, ["--a", a, "--a-label", "A100", "--b", b, "--b-label", "H100",
                                                    "--output", tmp_path / "cmp", *extra])
    return code, tmp_path / "cmp", out, err


def report(out):
    return (out / "HARDWARE_COMPARISON.md").read_text()


def test_host_shape_parses_sanitized_and_raw_environment_files():
    a = analyze_results.host_shape(S.A100_ENV)
    assert (a["gpu"], a["vcpus"], a["host_ram_gib"], a["cpus_arg"]) == ("NVIDIA A100-SXM4-40GB", 30, 216, 30)
    b = analyze_results.host_shape(S.H100_ENV)
    assert (b["gpu"], b["gpu_count"], b["vcpus"], b["cpus_arg"]) == ("NVIDIA H100 80GB HBM3", 1, 26, 26)
    assert b["host_ram_gib"] == pytest.approx(225, abs=0.1) and b["gpu_memory_mib"] == 81559
    assert b["driver_version"] == "580.105.08" and b["cuda_version"] == "13.0" and b["cpu_model"].startswith("Intel")
    assert b["packages"] == {"polars": "2.0.0", "cudf-polars-cu12": "25.12.0", "pyspark": "3.5.9"}
    assert analyze_results.host_shape(None)["vcpus"] is None


def test_equivalent_inputs_are_compared_and_cpu_engines_labelled_non_comparable(tmp_path):
    a, b = sides(tmp_path)
    code, out, _, err = run(tmp_path, a, b)
    assert code == 0, err
    text = report(out)
    assert "CPU engines (polars, spark_cpu) are **not comparable**" in text
    assert "vCPU count 30 vs 26" in text and "host RAM 216 vs 225 GiB" in text and "CPU allocation (--cpus) 30 vs 26" in text
    assert "NOT COMPARABLE, reference only" in text
    rows = read_csv(out / "hardware_comparison.csv")
    cpu = [r for r in rows if r["engine"] in ("polars", "spark_cpu")]
    gpu = [r for r in rows if r["engine"] == "spark_gpu"]
    assert cpu and all(r["comparability"].startswith("not comparable") for r in cpu)
    assert gpu and all("GPU engine across hardware" in r["comparability"] for r in gpu)
    assert all(r["input_equivalence"] == "verified" for r in rows)
    spark_total = next(r for r in rows if (r["scale"], r["engine"], r["query"]) == ("sf1", "spark_gpu", "SUM"))
    assert float(spark_total["speedup_H100_over_A100"]) == pytest.approx(1.0)
    assert float(spark_total["A100_median_seconds"]) == pytest.approx(12.48)


def test_polars_gpu_exists_only_on_one_side_and_is_flagged(tmp_path):
    a, b = sides(tmp_path)
    code, out, _, err = run(tmp_path, a, b)
    assert code == 0, err
    assert "polars_gpu was run on only one side (H100)" in report(out)
    gpu = [r for r in read_csv(out / "hardware_comparison.csv") if r["engine"] == "polars_gpu"]
    assert gpu and all(r["speedup_H100_over_A100"] == "" and r["A100_median_seconds"] == "" for r in gpu)
    assert all(r["A100_status"] == "not run" for r in gpu if r["query"] != "SUM")


def test_version_and_configuration_differences_are_flagged(tmp_path):
    a_specs = {(s, "spark_gpu"): {"profile": "a100-parity"} for s in ("1", "10")}
    b_specs = {**{(s, "spark_gpu"): {"profile": "h100-tuned", "version": "3.5.9"} for s in ("1", "10")},
               **{(s, "polars"): {"version": "2.1.0"} for s in ("1", "10")}}
    a, b = sides(tmp_path, a_specs=a_specs, b_specs=b_specs)
    code, out, _, err = run(tmp_path, a, b)
    assert code == 0, err
    text = report(out)
    assert "polars: engine version differs (2.0.0 on A100 vs 2.1.0 on H100)" in text
    assert "spark_gpu: Spark GPU profile differs (a100-parity on A100 vs h100-tuned on H100)" in text
    assert "cudf-polars" in text and "25.12.0" in text


def test_row_count_mismatch_refuses_the_scale_unless_allowed(tmp_path):
    a, b = sides(tmp_path, b_manifests={"10": S.manifest_for("10", rows=999)})
    code, out, _, err = run(tmp_path, a, b)
    assert code == 0, "scale 1 is still comparable"
    assert "Refused baseline sf10" in err
    text = report(out)
    assert "MISMATCH (refused)" in text and "lineitem: row count 6000 vs 999" in text
    assert not [r for r in read_csv(out / "hardware_comparison.csv") if r["scale"] == "sf10"]
    code, out, _, err = run(tmp_path, a, b, "--allow-input-mismatch")
    assert code == 0
    allowed = [r for r in read_csv(out / "hardware_comparison.csv") if r["scale"] == "sf10"]
    assert allowed and {r["input_equivalence"] for r in allowed} == {"mismatch-allowed"}
    assert "MISMATCH (compared: --allow-input-mismatch)" in report(out) and "--allow-input-mismatch was given" in report(out)
    equivalence = json.loads((out / "input_equivalence.json").read_text())
    assert next(s for s in equivalence["scales"] if s["scale"] == "sf10")["allowed_mismatch"] is True


def test_every_scale_mismatching_exits_nonzero(tmp_path):
    a, b = sides(tmp_path, b_manifests={"1": S.manifest_for("1", rows=5), "10": S.manifest_for("10", rows=5)})
    code, out, _, err = run(tmp_path, a, b)
    assert code == 1 and "Refused baseline sf1:" in err and "Refused baseline sf10:" in err
    assert read_csv(out / "hardware_comparison.csv") == []


def test_schema_mismatch_is_refused(tmp_path):
    changed = S.manifest_for("1")
    changed["tables"]["orders"]["schema"] = [{"name": "o_orderkey", "type": "INTEGER"}]
    a, b = sides(tmp_path, b_manifests={"1": changed})
    code, out, _, err = run(tmp_path, a, b)
    assert "orders: schema differs" in report(out) and not [r for r in read_csv(out / "hardware_comparison.csv") if r["scale"] == "sf1"]


def test_file_hash_difference_without_fingerprint_is_refused(tmp_path):
    a, b = sides(tmp_path, b_manifests={"10": S.manifest_for("10", file_hash="c" * 64)})
    code, out, _, err = run(tmp_path, a, b)
    assert "1 file sha256 differ" in report(out) and "MISMATCH (refused)" in report(out)


def test_chunked_scale_uses_fingerprints_and_tolerates_a_different_file_layout(tmp_path):
    same = lambda h: S.manifest_for("10", fingerprint="1234", file_hash=h)
    a, b = sides(tmp_path, a_manifests={"10": same("a" * 64)}, b_manifests={"10": same("c" * 64)})
    code, out, _, err = run(tmp_path, a, b)
    text = report(out)
    assert code == 0 and "order-independent fingerprints" in text and "physical layout differs" in text
    assert [r for r in read_csv(out / "hardware_comparison.csv") if r["scale"] == "sf10"]


def test_fingerprint_difference_is_refused_even_when_rows_and_schema_match(tmp_path):
    a, b = sides(tmp_path, a_manifests={"10": S.manifest_for("10", fingerprint="1234")}, b_manifests={"10": S.manifest_for("10", fingerprint="9999")})
    code, out, _, err = run(tmp_path, a, b)
    assert "fingerprint fields differ" in report(out)
    assert not [r for r in read_csv(out / "hardware_comparison.csv") if r["scale"] == "sf10"]


def test_missing_manifest_cannot_be_verified(tmp_path):
    a, b = sides(tmp_path)
    (b / "sf1" / "manifest.json").unlink()
    code, out, _, err = run(tmp_path, a, b)
    assert "manifest B is missing or has no tables" in report(out)
    assert not [r for r in read_csv(out / "hardware_comparison.csv") if r["scale"] == "sf1"]


def test_failed_and_missing_cells_are_never_zero_and_stay_out_of_totals(tmp_path):
    b_specs = {("10", "polars_gpu"): {"query_errors": {"q03": ("NotImplementedError", "unsupported")}},
               ("10", "spark_gpu"): {"query_errors": {"q06": ("RuntimeError", "oom")}}}
    a, b = sides(tmp_path, b_specs=b_specs)
    code, out, _, err = run(tmp_path, a, b)
    assert code == 0, err
    rows = read_csv(out / "hardware_comparison.csv")
    cell = next(r for r in rows if (r["scale"], r["engine"], r["query"]) == ("sf10", "spark_gpu", "q06"))
    assert cell["H100_status"] == "failed" and cell["H100_median_seconds"] == "" and cell["speedup_H100_over_A100"] == ""
    assert "RuntimeError: oom" in cell["H100_note"]
    total = next(r for r in rows if (r["scale"], r["engine"], r["query"]) == ("sf10", "spark_gpu", "SUM"))
    assert total["A100_status"] == "ok (5/6 queries)"
    assert float(total["A100_median_seconds"]) == pytest.approx(float(total["H100_median_seconds"]))
    assert float(total["A100_median_seconds"]) == pytest.approx(5 * 8.32)
    assert "FAILED" in report(out)


def test_orchestrator_failure_marks_a_whole_session_failed(tmp_path):
    a, b = sides(tmp_path, a_schedule={("10", "spark_cpu"): {"status": "failed", "returncode": 1}})
    code, out, _, err = run(tmp_path, a, b)
    rows = [r for r in read_csv(out / "hardware_comparison.csv") if (r["scale"], r["engine"]) == ("sf10", "spark_cpu") and r["query"] != "SUM"]
    assert rows and {r["A100_status"] for r in rows} == {"failed"} and not any(r["speedup_H100_over_A100"] for r in rows)


def test_matching_host_shape_makes_cpu_engines_comparable(tmp_path):
    a, b = sides(tmp_path, b_env=S.A100_ENV)
    code, out, _, err = run(tmp_path, a, b)
    assert "CPU engines are comparable subject to the version notes" in report(out)
    assert not any(r["comparability"].startswith("not comparable") for r in read_csv(out / "hardware_comparison.csv"))


def test_unknown_host_shape_is_treated_as_non_comparable(tmp_path):
    a, b = sides(tmp_path, a_env={"arguments": {}}, b_env=S.H100_ENV)
    code, out, _, err = run(tmp_path, a, b)
    assert "unknown on one side" in report(out)
    assert all(r["comparability"].startswith("not comparable") for r in read_csv(out / "hardware_comparison.csv") if r["engine"] == "polars")


def test_complex_suite_and_multiple_roots_per_side(tmp_path):
    a1 = S.build_root(tmp_path / "a1", suite="complex", scales=("30",), engines=LEGACY, env=S.A100_ENV)
    a2 = S.build_root(tmp_path / "a2", suite="complex", scales=("100",), engines=LEGACY, env=S.A100_ENV)
    b = S.build_root(tmp_path / "b", suite="complex", scales=("30", "100"), engines=S.ENGINES4, env=S.H100_ENV)
    code, out, _ = S.run_main(compare_hardware, ["--a", a1, a2, "--a-label", "A100", "--b", b, "--b-label", "H100", "--output", tmp_path / "cmp"])
    rows = read_csv(tmp_path / "cmp" / "hardware_comparison.csv")
    assert code == 0 and {r["suite"] for r in rows} == {"complex"} and {r["scale"] for r in rows} == {"sf30", "sf100"}
    assert {r["query"] for r in rows if r["engine"] == "spark_gpu"} >= set(S.COMPLEX_QUERIES) | {"SUM"}


def test_labels_must_differ_and_be_column_safe(tmp_path):
    a, b = sides(tmp_path)
    assert S.run_main(compare_hardware, ["--a", a, "--b", b, "--a-label", "X", "--b-label", "X", "--output", tmp_path / "o"])[0] != 0
    assert S.run_main(compare_hardware, ["--a", a, "--b", b, "--a-label", "has space", "--b-label", "Y", "--output", tmp_path / "o"])[0] != 0


def test_inventory_entries_for_missing_binaries_and_the_rapids_probe_are_tolerated():
    probe = json.dumps({"distributions": {"polars": "2.0.0", "cudf-polars-cu12": "25.12.0", "rmm-cu12": "25.12.0", "numpy": "2"},
                        "imports": {}, "cuda_runtime_version": 12090})
    env = {"arguments": {"cpus": 26}, "inventory": [
        {"command": ["lscpu"], "returncode": None, "output": "", "error": "FileNotFoundError: lscpu"},
        {"command": ["free", "-b"], "returncode": None, "output": "", "error": "FileNotFoundError: free"},
        {"command": ["nvidia-smi", "-q"], "returncode": None, "output": "", "error": "FileNotFoundError: nvidia-smi"},
        {"command": ["/usr/bin/python", "-c", "probe"], "returncode": 0, "output": probe},
        "not-a-dict", {"command": [], "output": None}]}
    shape = analyze_results.host_shape(env)
    assert shape["vcpus"] is None and shape["gpu"] is None and shape["cpus_arg"] == 26
    assert shape["packages"] == {"polars": "2.0.0", "cudf-polars-cu12": "25.12.0", "rmm-cu12": "25.12.0"}
    assert shape["cuda_runtime_version"] == 12090


def test_mixed_spark_gpu_profiles_are_called_out_in_the_reports(tmp_path):
    import analyze_crossover
    specs = {("1", "spark_gpu"): {"profile": "a100-parity"}, ("10", "spark_gpu"): {"profile": "h100-tuned"}}
    root = S.build_root(tmp_path / "root", specs=specs)
    code, _, err = S.run_main(analyze_crossover, ["--results", root, "--output", tmp_path / "out"])
    assert code == 0, err
    text = (tmp_path / "out" / "CROSSOVER_REPORT.md").read_text()
    assert "profile=a100-parity" in text and "profile=h100-tuned" in text and "Mixed Spark GPU profiles across scales" in text
