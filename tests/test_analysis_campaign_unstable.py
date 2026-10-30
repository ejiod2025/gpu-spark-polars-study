"""Amendment A1, unstable coverage: a coverage outcome (unsupported operation, out of memory) seen in some rounds of a cell but not all
of them excludes that cell from every contrast involving the engine, per-query and suite-level, in ALL rounds.

The raw per-round values may appear only in the supplementary section / table labelled "excluded by A1 (unstable coverage); not an estimate".
Noise-free synthetic trees make every expectation exact.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_campaign as AC  # noqa: E402

QUIET = dict(round_sigma=0.0, query_sigma=0.0, trial_sigma=0.0, position_effect=0.0, gpu_interval=60.0)
OOM = ("MemoryError", "std::bad_alloc: out_of_memory: RMM failure at allocation of 41 GiB")
UNSUPPORTED = ("NotImplementedError", "operation is not supported on the GPU")
CELL = ("baseline", "100", "q06", "polars_gpu")


def build(tmp_path, name, extra, host="H100", seed=3):
    root = tmp_path / name
    truth = AC.make_synthetic_campaign(root, seed=seed, host=host, a100=False, side_study=False, extra_failures=extra, **QUIET)
    return root, truth, json.loads(json.dumps(AC.analyze(root, energy=False)))


def failing_in(rounds, failure=OOM, cell=CELL):
    return [(r, *cell[:3], cell[3], failure) for r in rounds]


@pytest.fixture(scope="module")
def unstable(tmp_path_factory):
    """polars_gpu runs out of memory on baseline q06 at SF100 in round 3 only."""
    return build(tmp_path_factory.mktemp("unstable"), "tree", failing_in([3]))


# ------------------------------------------------------------------ the cell is recognised

def test_a_coverage_outcome_in_some_rounds_only_is_flagged_unstable_in_the_coverage_matrix(unstable):
    _, _, stats = unstable
    cell = stats["coverage"]["matrix"]["baseline"]["q06"]["sf100"]["polars_gpu"]
    assert (cell["status"], cell["rounds_ok"], cell["rounds_total"], cell["reason_class"], cell["unstable"]) == ("partial", 3, 4, "out_of_memory", True)
    others = [c for by_query in stats["coverage"]["matrix"].values() for by_scale in by_query.values() for by_engine in by_scale.values()
              for c in by_engine.values() if c["unstable"]]
    assert len(others) == 1                                           # nothing else is unstable
    assert stats["coverage"]["matrix"]["baseline"]["q06"]["sf100"]["spark_gpu"]["unstable"] is False


def test_the_same_failure_in_every_round_is_stable_coverage_not_unstable(tmp_path):
    _, _, stats = build(tmp_path, "stable", failing_in([1, 2, 3, 4]))
    cell = stats["coverage"]["matrix"]["baseline"]["q06"]["sf100"]["polars_gpu"]
    assert (cell["status"], cell["unstable"]) == ("failed", False)
    q = stats["c1"]["baseline"]["sf100"]["q"]["q06"]
    assert q["status"] == "unsupported" and q["blocked_by"][0]["reason_class"] == "out_of_memory" and q["unstable_engines"] == []
    assert stats["unstable_coverage"]["cells"] == []


def test_different_coverage_classes_in_different_rounds_are_still_stable(tmp_path):
    extra = failing_in([1, 2], OOM) + failing_in([3, 4], UNSUPPORTED)
    _, _, stats = build(tmp_path, "mixed", extra)
    assert stats["coverage"]["matrix"]["baseline"]["q06"]["sf100"]["polars_gpu"]["unstable"] is False
    assert stats["unstable_coverage"]["cells"] == []


def test_an_integrity_failure_in_one_round_is_partial_but_not_unstable_coverage(tmp_path):
    extra = [(2, "baseline", "100", "q06", "spark_cpu", ("TimeoutError", "action exceeded the timeout"))]
    _, _, stats = build(tmp_path, "timeout", extra)
    cell = stats["coverage"]["matrix"]["baseline"]["q06"]["sf100"]["spark_cpu"]
    assert (cell["status"], cell["reason_class"], cell["unstable"]) == ("partial", "timeout", False)
    q = stats["c2"]["baseline"]["sf100"]["q"]["q06"]
    assert q["status"] == "partial" and q["n_rounds"] == 3 and q["unstable_engines"] == []        # A1's exclusion is for coverage outcomes only


# ------------------------------------------------------------------ excluded everywhere an estimate is made

def test_per_query_contrasts_involving_the_engine_have_no_estimate_in_any_round(unstable):
    _, truth, stats = unstable
    for c in ("c1", "c3", "c4_strict"):
        q = stats[c]["baseline"]["sf100"]["q"]["q06"]
        assert q["status"] == "unstable" and q["estimate"] is None and q["ci_low"] is None and q["rounds"] == [] and q["per_round"] == [], c
        assert q["blocked_by"][0]["engine"] == "polars_gpu" and q["blocked_by"][0]["reason_class"] == "unstable_coverage"
        assert q["unstable_engines"] == ["polars_gpu"]
    c4 = stats["c4"]["baseline"]["sf100"]["q"]["q06"]
    times = truth["times"]["baseline"]["sf100"]["q06"]
    assert c4["status"] == "ok" and c4["n_rounds"] == 4 and c4["unstable_engines"] == ["polars_gpu"]
    assert c4["best_engine_rounds"] == {"spark_gpu": 4}                                   # polars_gpu is not a candidate in ANY round
    assert c4["estimate"] == pytest.approx(times["polars"] / times["spark_gpu"])
    c2 = stats["c2"]["baseline"]["sf100"]["q"]["q06"]
    assert c2["status"] == "ok" and c2["n_rounds"] == 4 and c2["unstable_engines"] == []   # Spark's own contrast is untouched
    assert c2["estimate"] == pytest.approx(truth["ratios"]["baseline"]["sf100"]["q06"]["c2"])


def test_suite_level_estimates_leave_the_cell_out_in_all_rounds(unstable):
    _, truth, stats = unstable
    for c in ("c1", "c3"):
        record = stats[c]["baseline"]["sf100"]
        assert "q06" not in record["queries_used"] and record["n_queries"] == 5
        assert record["queries_excluded"]["q06"].startswith("unstable: polars_gpu (unstable_coverage)")
        assert record["rounds"] == [1, 2, 3, 4]                                            # round 3 still counts for the other five queries
        expected = AC.geomean([truth["ratios"]["baseline"]["sf100"][q][c] for q in AC.BASELINE_QUERIES if q != "q06"])
        assert record["estimate"] == pytest.approx(expected, rel=1e-9) and record["ci_low"] == pytest.approx(record["ci_high"])
    # other scales are untouched
    assert stats["c1"]["baseline"]["sf30"]["n_queries"] == 6


def test_the_cells_times_are_withheld_from_medians_totals_rankings_and_variability(unstable):
    _, _, stats = unstable
    m = stats["medians"]["baseline"]["sf100"]["q06"]["polars_gpu"]
    assert m["status"] == "unstable" and m["reason_class"] == "unstable_coverage"
    assert (m["geomean"], m["min"], m["max"], m["cv_between_rounds"]) == (None, None, None, None)
    assert m["per_round"] == [None] * 4 and m["n_rounds"] == 0
    fastest = stats["fastest"]["baseline"]["sf100"]["q06"]
    assert "polars_gpu" not in fastest["ranking"]
    total = stats["legacy"]["totals"]["baseline"]["sf100"]["polars_gpu"]
    assert total["complete"] is False and "q06" in total["queries_missing"]
    assert not any((c["engine"], c["suite"], c["scale"], c["query"]) == ("polars_gpu", "baseline", "sf100", "q06") for c in stats["variability"]["cells"])
    assert stats["secondary"]["gpu_memory"]["cells"]["baseline"]["sf100"]["q06"].get("polars_gpu") is None


def test_crossover_and_the_best_gpu_contrast_never_use_the_engine_in_an_unstable_cell(unstable):
    _, _, stats = unstable
    q = stats["crossover"]["c4"]["baseline"]["queries"]["q06"]
    assert q["estimates"]["sf100"] == stats["c4"]["baseline"]["sf100"]["q"]["q06"]["estimate"]
    assert stats["crossover"]["c4_strict"]["baseline"]["queries"]["q06"]["estimates"]["sf100"] is None


# ------------------------------------------------------------------ the supplementary table

def test_raw_per_round_values_exist_only_in_the_labelled_supplementary_section(unstable, tmp_path):
    root, truth, stats = unstable
    section = stats["unstable_coverage"]
    assert section["note"].startswith(AC.UNSTABLE_NOTE) and "excluded from every contrast" in section["note"]
    (cell,) = section["cells"]
    assert (cell["suite"], cell["scale"], cell["query"], cell["engine"]) == ("baseline", "sf100", "q06", "polars_gpu")
    assert cell["rounds"] == [1, 2, 3, 4] and cell["outcome_per_round"] == ["ok", "ok", "out_of_memory", "ok"] and cell["coverage_rounds"] == [3]
    expected = truth["times"]["baseline"]["sf100"]["q06"]["polars_gpu"]
    assert cell["seconds_per_round"][0] == pytest.approx(expected) and cell["seconds_per_round"][2] is None
    assert cell["label"] == "excluded by A1 (unstable coverage); not an estimate"
    out = tmp_path / "out"
    AC.write_outputs(stats, out)
    rows = list(csv.DictReader((out / "tables" / "t20_unstable_coverage.csv").open()))
    assert len(rows) == 1 and rows[0]["label"] == AC.UNSTABLE_NOTE and rows[0]["engine"] == "polars_gpu"
    md = (out / "tables" / "t20_unstable_coverage.md").read_text()
    assert "excluded by A1 (unstable coverage); not an estimate" in md and "r3: out of memory" in md
    report = (out / "STATS_REPORT.md").read_text()
    assert "Unstable coverage (amendment A1)" in report and "excluded by A1" in report
    coverage_md = (out / "tables" / "t12_coverage.md").read_text()
    assert "unstable (A1: excluded)" in coverage_md


def test_no_unstable_cells_means_an_empty_supplementary_table(tmp_path):
    _, _, stats = build(tmp_path, "clean", [])
    assert stats["unstable_coverage"]["cells"] == []
    out = tmp_path / "out"
    AC.write_outputs(stats, out)
    assert list(csv.DictReader((out / "tables" / "t20_unstable_coverage.csv").open())) == []
    assert "No cell has a coverage outcome in some rounds and not in others." in (out / "STATS_REPORT.md").read_text()


# ------------------------------------------------------------------ other estimators that involve the cell

def test_cross_host_ratios_skip_a_cell_that_is_unstable_on_either_host(tmp_path):
    _, _, h100 = build(tmp_path, "h100", [])
    _, _, b200 = build(tmp_path, "b200", failing_in([2]), host="B200")
    cross = json.loads(json.dumps(AC.compare_hosts({"H100": h100, "B200": b200})))
    q = cross["ratio_query"]["B200"]["polars_gpu"]["baseline"]["sf100"]["q06"]
    assert q["status"] == "unavailable" and q["estimate"] is None
    assert q["blocked_by"] == [{"host": "B200", "reason_class": "unstable_coverage"}]
    record = cross["ratio"]["B200"]["polars_gpu"]["baseline"]["sf100"]
    assert "q06" not in record["queries_used"] and "unstable_coverage" in record["queries_excluded"]["q06"]
    # the reference host unstable instead: the same rule
    cross = json.loads(json.dumps(AC.compare_hosts({"H100": b200, "B200": h100}, "H100")))
    q = cross["ratio_query"]["B200"]["polars_gpu"]["baseline"]["sf100"]["q06"]
    assert q["status"] == "unavailable" and q["blocked_by"] == [{"host": "H100", "reason_class": "unstable_coverage"}]


def test_schema_documents_the_new_keys(unstable):
    _, _, stats = unstable
    flat = AC.flatten_stats(stats)
    undocumented = [k for k, v in flat.items() if not AC.schema_matches(k) and not k.startswith("secondary.energy.")      # energy is off in this fixture
                    and not (v == {} and any(p.startswith(k + ".") for p, _, _ in AC.SCHEMA))]
    assert undocumented == []
    assert "coverage.matrix.baseline.q06.sf100.polars_gpu.unstable" in flat and "c1.baseline.sf100.q.q06.unstable_engines" in flat
    assert "unstable_coverage.note" in flat and "unstable_coverage.cells" in flat
