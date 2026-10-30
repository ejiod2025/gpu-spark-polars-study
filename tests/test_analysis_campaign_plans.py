"""Plan variability (stats["plan_variability"]): Spark cells whose trials do not all end in the same final adaptive plan.

A saved Spark plan (plans/<query>-<phase>-<trial>.txt) carries ids that change from one execution to the next, so plans are normalized before they are
compared. The section is descriptive: no median, estimate or interval may depend on it. The event log (stage completions) shows what a plan did.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_campaign as AC  # noqa: E402

QUIET = dict(round_sigma=0.0, query_sigma=0.0, trial_sigma=0.0, position_effect=0.0, gpu_interval=60.0)
CELL = ("complex", "30", "cx_q09")
GIB = 1024 ** 3


def build(tmp_path, name, variants=(CELL,), **kwargs):
    root = tmp_path / name
    AC.make_synthetic_campaign(root, seed=3, a100=False, side_study=False, plan_variants=variants, **{**QUIET, **kwargs})
    return root


def analyze(root):
    return json.loads(json.dumps(AC.analyze(root, energy=False)))


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    root = build(tmp_path_factory.mktemp("plans"), "with")
    return root, analyze(root)


# ------------------------------------------------------------------ normalization and features

def test_ids_that_change_between_executions_do_not_change_the_signature():
    texts = [AC.synthetic_plan_text("cx_q09", 0, serial) for serial in (1, 7, 40)]
    assert len({AC.plan_signature(AC.split_plan(t)[0]) for t in texts}) == 1
    assert len(set(texts)) == 3                     # the files themselves differ (expression, plan, stage and lore ids)
    other = AC.synthetic_plan_text("cx_q09", 1, 1)
    assert AC.plan_signature(AC.split_plan(other)[0]) != AC.plan_signature(AC.split_plan(texts[0])[0])


def test_normalization_removes_only_volatile_ids():
    line = "GpuColumnarExchange gpuhashpartitioning(l_partkey#27L, 120, Murmur3Mode), ENSURE_REQUIREMENTS, [plan_id=15783], [loreId=345]"
    assert AC.normalize_plan(line) == "GpuColumnarExchange gpuhashpartitioning(l_partkey#, 120, Murmur3Mode), ENSURE_REQUIREMENTS, [plan_id=N]"
    assert AC.normalize_plan("ShuffleQueryStage 9\nBroadcastQueryStage 0") == "ShuffleQueryStage N\nBroadcastQueryStage N"
    assert "@H" in AC.normalize_plan("GpuSort [a#1 ASC], true, com.nvidia.spark.rapids.OutOfCoreSort$@5370ea2d, [loreId=351]")
    assert AC.normalize_plan("GpuBroadcastHashJoin [a#1], [b#2], Inner, GpuBuildRight, false, [loreId=4]") != \
        AC.normalize_plan("GpuBroadcastHashJoin [a#1], [b#2], Inner, GpuBuildLeft, false, [loreId=4]")


def test_final_and_initial_plan_are_split():
    final, initial = AC.split_plan(AC.synthetic_plan_text("q", 0, 3))
    assert "SortMergeJoin" in initial and "SortMergeJoin" not in final and final.rstrip().endswith("[loreId=1129]") is False
    assert AC.split_plan("just a plan")[1] is None


def test_features_name_the_join_strategy_exchanges_with_partition_counts_and_the_shuffle_readers():
    short = AC.plan_features(AC.split_plan(AC.synthetic_plan_text("q", 0, 3))[0])
    longer = AC.plan_features(AC.split_plan(AC.synthetic_plan_text("q", 1, 3))[0])
    assert short["joins"] == longer["joins"] == {"GpuBroadcastHashJoin": 1}
    assert short["exchanges"] == {} and longer["exchanges"] == {"gpuhashpartitioning(k, 120)": 1}
    assert short["shuffle_readers"] == {} and longer["shuffle_readers"] == {"local": 1}
    real = """AdaptiveSparkPlan isFinalPlan=true
+- == Final Plan ==
   GpuColumnarToRow false, [loreId=352]
   +- GpuSort [nation#1513 ASC NULLS FIRST], true, com.nvidia.spark.rapids.OutOfCoreSort$@5370ea2d, [loreId=351]
      +- GpuCustomShuffleReader coalesced
         +- ShuffleQueryStage 9
            +- GpuColumnarExchange gpurangepartitioning(nation#1513 ASC NULLS FIRST, o_year#1514 DESC NULLS LAST, 120), ENSURE_REQUIREMENTS, [plan_id=15783], [loreId=345]
               +- GpuShuffledSymmetricHashJoin Inner, [l_orderkey#26L], [o_orderkey#135L], false, 1073741824, 1.0, CoalesceReadOption(true,GPU,Never,None,false), false, [loreId=330]
                  :- GpuCustomShuffleReader
                  +- GpuCustomShuffleReader skewed
+- == Initial Plan ==
Sort [nation#1513 ASC NULLS FIRST]
"""
    features = AC.plan_features(AC.split_plan(real)[0])
    assert features["exchanges"] == {"gpurangepartitioning(nation ASC NULLS FIRST, o_year DESC NULLS LAST, 120)": 1}
    assert features["joins"] == {"GpuShuffledSymmetricHashJoin": 1}
    assert features["shuffle_readers"] == {"coalesced": 1, "plain": 1, "skewed": 1}


def test_changes_list_what_the_other_plan_adds():
    a, b = (AC.split_plan(AC.synthetic_plan_text("q", v, 3))[0] for v in (0, 1))
    changes = AC.plan_changes(AC.plan_features(a), AC.plan_features(b), a, b)
    assert changes["exchanges"] == {"gpuhashpartitioning(k, 120)": [0, 1]} and changes["shuffle_readers"] == {"local": [0, 1]}
    assert "joins" not in changes                                          # the join operators are the same in both plans
    assert any("GpuColumnarExchange gpuhashpartitioning" in line for line in changes["lines_only_in_this_plan"])
    assert changes["lines_only_in_reference_plan"] == []


# ------------------------------------------------------------------ event-log stages

def stage_line(submitted, completed, tasks, written, failure=None):
    info = {"Stage ID": 1, "Number of Tasks": tasks, "Submission Time": submitted, "Completion Time": completed,
            "Accumulables": [{"Name": "internal.metrics.shuffle.write.bytesWritten", "Value": written}, {"Name": "other", "Value": 99}]}
    if failure:
        info["Failure Reason"] = failure
    return json.dumps({"Event": "SparkListenerStageCompleted", "Stage Info": info})


def test_stage_completions_are_read_from_plain_and_gzip_logs_and_failed_stages_are_skipped(tmp_path):
    d = tmp_path / "events"
    d.mkdir()
    (d / "local-1").write_text("\n".join([stage_line(1000, 2000, 30, 7), stage_line(1100, 1200, 5, 1, failure="boom"), '{"Event": "SparkListenerTaskEnd"}']))
    with gzip.open(d / "local-2.gz", "wt") as handle:
        handle.write(stage_line(3000, 4000, 118, 5_000_000_000) + "\n")
    (d / "local-3.zstd").write_bytes(b"\x28\xb5\x2f\xfd")
    stages, notes = AC.read_spark_stages(d)
    assert sorted(stages) == [(1000.0, 2000.0, 30, 7), (3000.0, 4000.0, 118, 5_000_000_000)]
    assert any("zstd" in n for n in notes)
    assert AC.read_spark_stages(tmp_path / "missing") == ([], [])


def test_stages_are_assigned_to_the_action_whose_window_holds_their_submission():
    t0 = 1_700_000_000
    trials = [{"query": "q", "phase": "measured", "trial": 1, "started": "2023-11-14T22:13:20+00:00", "ended": "2023-11-14T22:13:25+00:00"},
              {"query": "q", "phase": "measured", "trial": 2, "started": "2023-11-14T22:13:30+00:00", "ended": "2023-11-14T22:13:32+00:00"}]
    stages = [(t0 * 1000 + 1000, t0 * 1000 + 2000, 30, 10), (t0 * 1000 + 2000, t0 * 1000 + 4000, 118, 5), (t0 * 1000 + 10500, t0 * 1000 + 11000, 8, 1),
              (t0 * 1000 + 60_000, t0 * 1000 + 61_000, 1, 1)]
    got = AC.action_stages(trials, stages)
    assert got[("q", "measured", 1)] == {"stages": 2, "tasks": 148, "shuffle_write_bytes": 15}
    assert got[("q", "measured", 2)] == {"stages": 1, "tasks": 8, "shuffle_write_bytes": 1}


# ------------------------------------------------------------------ the section

def test_the_variable_cell_is_found_with_both_plans_their_trials_and_the_event_log_facts(tree):
    _, stats = tree
    pv = stats["plan_variability"]
    assert pv["engines"] == ["spark_cpu", "spark_gpu"] and pv["plan_files_missing"] == 0
    assert pv["cells_variable"] == len(pv["cells"]) == 1 and pv["cells_checked"] > 20 and pv["sessions_checked"] > 0
    cell = pv["cells"][0]
    assert (cell["suite"], cell["scale"], cell["query"], cell["engine"]) == ("complex", "sf30", "cx_q09", "spark_gpu")
    assert cell["rounds"] == [1, 2, 3, 4] and cell["n_plans"] == 2 and cell["initial_plan_identical"] is True
    assert cell["n_trials"] == cell["n_measured"] + cell["n_warmup"] == 16 and cell["n_warmup"] == 4
    short, longer = cell["plans"]
    assert short["signature"] == cell["reference_signature"] and short["changes_vs_reference"] is None
    assert short["n_measured"] + longer["n_measured"] == 12 and short["n_measured"] > 0 and longer["n_measured"] > 0
    assert short["n_warmup"] == 4 and longer["n_warmup"] == 0                 # warm-ups never take the variant
    assert sum(short["measured_per_round"].values()) == short["n_measured"]
    assert longer["changes_vs_reference"]["exchanges"] == {"gpuhashpartitioning(k, 120)": [0, 1]}
    assert longer["changes_vs_reference"]["shuffle_readers"] == {"local": [0, 1]}
    assert short["features"]["shuffle_readers"] == {} and longer["features"]["shuffle_readers"] == {"local": 1}
    # the generator's event log gives the longer plan one more stage (1e6 bytes written) and a 5e9-byte shuffle
    assert short["execution"]["trials_with_event_log"] == short["n_measured"]
    assert longer["execution"]["stages"]["median"] - short["execution"]["stages"]["median"] == 1
    assert longer["execution"]["shuffle_write_bytes"]["median"] - short["execution"]["shuffle_write_bytes"]["median"] == 5_001_000_000
    assert len(longer["trials"]) == longer["n_measured"] and {t["phase"] for t in longer["trials"]} == {"measured"}


def test_plans_that_do_not_differ_give_an_empty_section(tmp_path):
    stats = analyze(build(tmp_path, "none", variants=()))
    pv = stats["plan_variability"]
    assert pv["cells"] == [] and pv["cells_variable"] == 0 and pv["cells_checked"] > 20 and pv["trials_with_plan"] > 100


def test_no_estimate_depends_on_the_plan_section(tmp_path, tree):
    _, with_variants = tree
    without = analyze(build(tmp_path, "without", variants=()))
    for key in ("medians", "c1", "c2", "c3", "c4", "c4_strict", "variability", "coverage", "order", "crossover", "unstable_coverage"):
        assert with_variants[key] == without[key], key


def test_a_missing_plan_file_is_counted_not_fatal(tmp_path):
    root = build(tmp_path, "gap")
    victim = next(root.glob("round-1/complex/sf30/spark_gpu/plans/cx_q09-measured*.txt"))
    victim.unlink()
    pv = analyze(root)["plan_variability"]
    assert pv["plan_files_missing"] == 1 and pv["cells_variable"] == 1


def test_a_cell_is_listed_when_only_the_warm_ups_differ(tmp_path):
    root = build(tmp_path, "warm", variants=())
    for path in root.glob("round-2/complex/sf30/spark_gpu/plans/cx_q09-warmup*.txt"):
        path.write_text(AC.synthetic_plan_text("cx_q09", 1, 5))
    cell = analyze(root)["plan_variability"]["cells"][0]
    assert cell["n_plans"] == 2 and cell["n_measured"] == 12 and cell["measured_time_ranges_overlap"] is None
    assert cell["plans"][1]["n_measured"] == 0 and cell["plans"][1]["n_warmup"] == 1


def test_the_report_and_table_show_the_cell(tree, tmp_path):
    root, _ = tree
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out), "--no-energy"]) == 0
    report = (out / "STATS_REPORT.md").read_text()
    section = report[report.index("### Plan variability (descriptive)"):report.index("## 7. Variability and order effects")]
    assert "complex SF30 cx_q09 (Spark GPU)" in section and "2 final plans in 16 trials" in section and "initial plan identical in every trial" in section
    assert "gpuhashpartitioning(k, 120)" in section and "Per action (Spark event log, median)" in section
    assert (out / "tables" / "t21_plan_variability.csv").is_file()
    header = (out / "tables" / "t21_plan_variability.csv").read_text().splitlines()[0]
    assert "suite,scale,query,engine,plan,role,n_measured,n_warmup" in header
    assert "Medians, estimates and intervals are unchanged" in section


def test_keys_are_documented(tree):
    _, stats = tree
    flat = AC.flatten_stats(stats)
    keys = [k for k in flat if k.startswith("plan_variability")]
    assert keys and all(AC.schema_matches(k) for k in keys), [k for k in keys if not AC.schema_matches(k)]


# ------------------------------------------------------------------ cross-host

def test_the_cross_host_comparison_lists_the_plans_per_host(tmp_path):
    stats = {}
    for host, variants in (("H100", (CELL,)), ("GH200", (CELL,)), ("B200", ())):
        root = tmp_path / host
        AC.make_synthetic_campaign(root, seed=3, host=host, a100=False, side_study=False, plan_variants=variants, **QUIET)
        stats[host] = analyze(root)
    cross = json.loads(json.dumps(AC.compare_hosts(stats)))
    pv = cross["plan_variability"]
    assert set(pv) == {"H100", "GH200", "B200"} and pv["B200"]["cells"] == [] and pv["B200"]["cells_variable"] == 0
    for host in ("H100", "GH200"):
        (cell,) = pv[host]["cells"]
        assert (cell["query"], cell["scale"]) == ("cx_q09", "sf30") and cell["n_plans"] == 2
        assert all(set(p) >= {"signature", "n_measured", "n_warmup", "measured_median_seconds", "exchanges", "shuffle_write_bytes_median"} for p in cell["plans"])
    assert {p["signature"] for p in pv["H100"]["cells"][0]["plans"]} == {p["signature"] for p in pv["GH200"]["cells"][0]["plans"]}
    tables = {t.name: t for t in AC.build_cross_tables(cross)}
    assert len(tables["x07_plan_variability"].rows) == 4
    report = AC.cross_report_text(cross, list(tables.values()))
    assert "## 7. Plan variability of the Spark cells (descriptive)" in report and "## 9. Caveats" in report
    flat = AC.flatten_stats(cross, (), AC.CROSS_OPAQUE_PATTERNS)
    keys = [k for k in flat if k.startswith("plan_variability")]
    assert keys and all(AC.schema_matches(k, AC.CROSS_SCHEMA) for k in keys)


def test_a_host_stats_file_without_the_section_is_reported_as_not_computed(tmp_path):
    stats = {}
    for host in ("H100", "GH200"):
        root = tmp_path / host
        AC.make_synthetic_campaign(root, seed=3, host=host, a100=False, side_study=False, **QUIET)
        stats[host] = analyze(root)
    del stats["GH200"]["plan_variability"]
    cross = json.loads(json.dumps(AC.compare_hosts(stats)))
    assert cross["plan_variability"]["GH200"] == {"cells_checked": None, "cells_variable": None, "cells": []}
    report = AC.cross_report_text(cross, AC.build_cross_tables(cross))
    assert "Plan variability was not computed for: GH200" in report


# ------------------------------------------------------------------ the copied campaigns

HOSTS = {name: REPO / "rerun" / "hosts" / name / f"campaign-{name}" for name in ("h100", "b200", "a100", "gh200")}


@pytest.mark.parametrize("name", sorted(HOSTS))
def test_real_trees_cx_q09_at_sf30_on_spark_gpu_ends_in_the_two_known_plans(name, tmp_path):
    root = HOSTS[name]
    if not (root / "round-1" / "gates.json").is_file():
        pytest.skip(f"the copied {name} campaign is not in this checkout")
    pv = AC.analyze(root, energy=False)["plan_variability"]
    assert pv["plan_files_missing"] == 0 and pv["cells_checked"] == 96
    cell = next(c for c in pv["cells"] if (c["suite"], c["scale"], c["query"], c["engine"]) == ("complex", "sf30", "cx_q09", "spark_gpu"))
    assert cell["n_plans"] == 2 and cell["initial_plan_identical"] is True
    plain = next(p for p in cell["plans"] if not p["features"]["shuffle_readers"].get("local"))
    extra = next(p for p in cell["plans"] if p["features"]["shuffle_readers"].get("local") == 1)
    assert extra["changes_vs_reference"] is None or extra["changes_vs_reference"]["exchanges"] == {"gpuhashpartitioning(l_partkey, 120)": [0, 1]}
    assert plain["features"]["joins"] == extra["features"]["joins"]              # the same join operators, one more shuffle
    assert sum(extra["features"]["exchanges"].values()) == sum(plain["features"]["exchanges"].values()) + 1
    if plain["n_measured"] and extra["n_measured"]:
        assert cell["measured_time_ranges_overlap"] is False                      # the plan separates the two timing clusters
        assert plain["measured_seconds"]["max"] < extra["measured_seconds"]["min"]
        assert extra["execution"]["shuffle_write_bytes"]["median"] > plain["execution"]["shuffle_write_bytes"]["median"] + 4 * GIB
        assert extra["execution"]["stages"]["median"] == plain["execution"]["stages"]["median"] + 1
    if name == "gh200":
        assert plain["n_measured"] == 0 and extra["n_measured"] == cell["n_measured"]
