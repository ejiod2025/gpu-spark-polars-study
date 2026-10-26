"""SYNTHETIC-only integrity and static-export tests; never experimental results."""
from __future__ import annotations

import copy
import csv
import json
import math
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research_reporting"))
import render as renderer
import research_analysis


def synthetic_analysis(directory):
    """Construct visibly labeled fake timings only inside pytest temporary paths."""
    sessions = []
    for block_number, block in enumerate(renderer.BLOCKS):
        for suite, scale in renderer.CELLS:
            for position, config in enumerate(renderer.CONFIGS, 1):
                unsupported = renderer.UNSUPPORTED if config == "polars_gpu" and suite == "complex" else set()
                queries = {}
                for query_number, query in enumerate(renderer.QUERIES[suite]):
                    if query in unsupported:
                        queries[query] = {"status": "unsupported", "endpoints": {}, "errors": [{"type": "SYNTHETIC UnsupportedError"}]}
                        continue
                    factor = {"polars_cpu_144": 4., "polars_cpu_200": 2.5, "polars_gpu": .8,
                              "spark_cpu": 6., "spark_gpu": 1.5}[config]
                    # Different block/config/query variation exercises nondegenerate intervals.
                    time = factor * (1 + math.log1p(int(scale))) * (1 + query_number / 8)
                    time *= math.exp(.035 * math.sin((block_number + 1) * position + query_number))
                    endpoints = {}
                    for endpoint, overhead in (("seconds", 0), ("host_result_seconds", .04)):
                        values = [time * multiplier + overhead for multiplier in (.9, 1, 1.1)]
                        endpoints[endpoint] = {"median": values[1], "min": min(values), "max": max(values), "trials": values}
                    queries[query] = {"status": "complete", "endpoints": endpoints}
                sessions.append({"session_id": f"SYNTHETIC-{block}-{suite}-sf{scale}-{config}",
                                 "block_id": block, "suite": suite, "scale": scale, "config_id": config,
                                 "cell_id": f"{suite}-sf{scale}", "position": position,
                                 "queries": queries, "coverage": "4/6" if unsupported else "6/6",
                                 "suite_status": "unsupported" if unsupported else "complete",
                                 "suite_scores": {endpoint: None if unsupported else math.fsum(
                                     q["endpoints"][endpoint]["median"] for q in queries.values())
                                     for endpoint in ("seconds", "host_result_seconds")}})
    report, raw = research_analysis.analyze({"primary_contrasts": renderer.expected_primary()}, sessions)
    report.update({"schema_version": "research-analysis-v1", "campaign_id": "SYNTHETIC-REPORTING-TEST-ONLY",
                   "campaign_sha256": "0" * 64, "synthetic": True, "validation_status": "PASS",
                   "primary_endpoint": "seconds"})
    directory.mkdir()
    (directory / "analysis.json").write_text(json.dumps(report, allow_nan=False))
    research_analysis.write_csv(directory / "block_ratios.csv", raw)
    return report, raw


@pytest.fixture()
def synthetic(tmp_path):
    directory = tmp_path / "SYNTHETIC-analysis"
    report, raw = synthetic_analysis(directory)
    return directory, report, raw


def test_synthetic_requires_explicit_opt_in(synthetic):
    directory, _, _ = synthetic
    with pytest.raises(ValueError, match="Synthetic input needs"):
        renderer.load_analysis(directory)
    data = renderer.load_analysis(directory, allow_synthetic=True)
    assert data["synthetic"] is True
    assert len(data["sessions"]) == 400 and len(data["raw"]) == 260


@pytest.mark.parametrize("mutation", ["missing_session", "duplicate_session", "partial_family", "wrong_ci", "wrong_holm", "gpu_partial_sum", "coverage", "query_imputation"])
def test_inconsistent_artifacts_never_become_publication_figures(synthetic, mutation):
    directory, report, _ = synthetic
    if mutation == "missing_session":
        report["sessions"].pop()
    elif mutation == "duplicate_session":
        report["sessions"].append(copy.deepcopy(report["sessions"][0]))
    elif mutation == "partial_family":
        report["primary"].pop()
    elif mutation == "wrong_ci":
        report["primary"][0]["ci95_pointwise"].reverse()
    elif mutation == "wrong_holm":
        report["primary"][0]["p_holm"] = .999
    elif mutation in ("gpu_partial_sum", "coverage"):
        gpu = next(s for s in report["sessions"] if s["config_id"] == "polars_gpu" and s["suite"] == "complex")
        if mutation == "gpu_partial_sum":
            gpu["suite_scores"]["seconds"] = 1
        else:
            gpu["coverage"] = "6/6"
    else:
        entry = next(e for e in report["exploratory_queries"] if e["denominator"] == "polars_gpu"
                     and e["suite"] == "complex" and e["query"] == "cx_q13")
        entry["geometric_mean_speedup"] = 1
    (directory / "analysis.json").write_text(json.dumps(report))
    with pytest.raises(ValueError):
        renderer.load_analysis(directory, allow_synthetic=True)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "ratio_drift", "session_binding", "log_ratio_drift"])
def test_primary_csv_matches_all_260_session_pairs(synthetic, mutation):
    directory, _, raw = synthetic
    index = next(i for i, row in enumerate(raw) if row["family"] == "primary_suite")
    if mutation == "duplicate":
        raw.append(copy.deepcopy(raw[index]))
    elif mutation == "missing":
        del raw[index]
    elif mutation == "ratio_drift":
        raw[index]["ratio"] *= 2
    elif mutation == "session_binding":
        raw[index]["numerator_session_id"] = "SYNTHETIC-wrong-session"
    else:
        raw[index]["log_ratio"] += 1
    research_analysis.write_csv(directory / "block_ratios.csv", raw)
    with pytest.raises(ValueError):
        renderer.load_analysis(directory, allow_synthetic=True)


def test_exports_all_formats_complete_tables_and_deterministic_svg_pdf(synthetic, tmp_path):
    directory, _, _ = synthetic
    output = tmp_path / "SYNTHETIC-rendered"
    manifest = renderer.render(directory, output, allow_synthetic=True)
    assert manifest["synthetic"] is True
    for stem in ("suite_ratios_baseline", "suite_ratios_complex", "query_ratios_action", "complete_case_scores"):
        for extension in ("png", "svg", "pdf"):
            assert (output / f"{stem}.{extension}").stat().st_size > 1000
        svg = (output / f"{stem}.svg").read_text()
        assert "SYNTHETIC DATA" in svg
        assert "<dc:date>" not in svg
    with (output / "primary_contrasts.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) == 26
    with (output / "complete_case_scores.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 40
    unavailable = [row for row in rows if row["config_id"] == "polars_gpu" and row["suite"] == "complex"]
    assert len(unavailable) == 3
    assert all(row["geometric_mean_score_seconds"] == "" and row["complete_blocks"] == "0" and row["query_coverage"] == "4/6" for row in unavailable)
    second = tmp_path / "SYNTHETIC-rendered-again"
    other = renderer.render(directory, second, formats=("svg", "pdf"), allow_synthetic=True)
    for name, checksum in other["output_files_sha256"].items():
        assert checksum == manifest["output_files_sha256"][name]
    assert "CreationDate" not in (output / "suite_ratios_baseline.pdf").read_bytes().decode("latin-1")
    assert "ModDate" not in (output / "suite_ratios_baseline.pdf").read_bytes().decode("latin-1")


def test_log_ratio_axis_includes_every_point_and_interval(synthetic):
    import matplotlib.pyplot as plt
    directory, _, _ = synthetic
    data = renderer.load_analysis(directory, allow_synthetic=True)
    fig, axis = plt.subplots()
    renderer.ratio_axis(axis, data, "polars_cpu_200", "spark_gpu", "baseline", ("1", "10", "30", "100", "300"))
    assert axis.get_yscale() == "log"
    lo, hi = axis.get_ylim()
    assert lo < 1 < hi
    for scale in ("1", "10", "30", "100", "300"):
        identity = f"polars_cpu_200_over_spark_gpu-baseline-sf{scale}"
        for block in renderer.BLOCKS:
            assert lo < data["raw"][(identity, block)]["ratio"] < hi
    plt.close(fig)
