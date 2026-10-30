"""Campaign statistics (analyze_campaign.py): pre-registered estimators, exclusions, coverage, schema and watermarks.

Every test runs on synthetic trees with injected ratios, written by analyze_campaign.make_synthetic_campaign.
Noise-free trees (all sigmas 0) must recover the injected ratios exactly; noisy trees must bracket them.
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
CONTRASTS = ("c1", "c2", "c3", "c4", "c4_strict")


def build(tmp_path, name, *, seed=1, a100=True, side_study=True, energy=True, **kwargs):
    root = tmp_path / name
    truth = AC.make_synthetic_campaign(root, seed=seed, a100=a100, side_study=side_study, **kwargs)
    roots = sorted((root / "a100").iterdir()) if a100 else []
    stats = AC.analyze(root, roots, energy=energy)
    return root, truth, json.loads(json.dumps(stats))


@pytest.fixture(scope="module")
def quiet(tmp_path_factory):
    return build(tmp_path_factory.mktemp("quiet"), "tree", **QUIET)


@pytest.fixture(scope="module")
def noisy(tmp_path_factory):
    return build(tmp_path_factory.mktemp("noisy"), "tree", seed=11, gpu_interval=60.0)


# ------------------------------------------------------------------ statistics primitives

@pytest.mark.parametrize("df, expected", [(1, 12.7062047362), (2, 4.30265272991), (3, 3.18244630528), (4, 2.77644510520),
                                          (5, 2.57058183564), (9, 2.26215716274), (30, 2.04227245630)])
def test_t_critical_matches_published_table(df, expected):
    assert AC.t_critical(df) == pytest.approx(expected, abs=1e-9)


def test_ci_construction_uses_t_interval_on_mean_log_ratio_with_df_3():
    ratios = [2.0, 3.0, 4.0, 6.0]
    summary = AC.ratio_summary(ratios, [1, 2, 3, 4])
    logs = [math.log(r) for r in ratios]
    mean = sum(logs) / 4
    sd = math.sqrt(sum((x - mean) ** 2 for x in logs) / 3)
    half = 3.18244630528 * sd / math.sqrt(4)
    assert summary["df"] == 3 and summary["n_rounds"] == 4
    assert summary["estimate"] == pytest.approx(math.exp(mean))
    assert summary["ci_low"] == pytest.approx(math.exp(mean - half))
    assert summary["ci_high"] == pytest.approx(math.exp(mean + half))
    assert (summary["min"], summary["max"]) == (2.0, 6.0)
    assert summary["ci_low"] < summary["estimate"] < summary["ci_high"]
    # the interval is symmetric on the log scale, so the estimate is the geometric mean of its limits
    assert math.sqrt(summary["ci_low"] * summary["ci_high"]) == pytest.approx(summary["estimate"])


def test_single_round_gives_estimate_without_interval_and_empty_gives_nothing():
    one = AC.ratio_summary([2.5], [1])
    assert one["estimate"] == pytest.approx(2.5) and one["ci_low"] is None and one["df"] == 0
    none = AC.ratio_summary([], [])
    assert none["estimate"] is None and none["n_rounds"] == 0


def test_campaign_reports_df_and_t_critical_for_four_rounds(quiet):
    _, _, stats = quiet
    assert stats["campaign"]["rounds_included"] == [1, 2, 3, 4]
    assert stats["campaign"]["df"] == 3
    assert stats["campaign"]["t_critical"] == pytest.approx(3.18244630528)
    for c in CONTRASTS:
        assert stats[c]["baseline"]["sf1"]["df"] == 3


# ------------------------------------------------------------------ estimator recovery

def test_noise_free_tree_recovers_every_injected_ratio_exactly(quiet):
    _, truth, stats = quiet
    checked = 0
    for c in CONTRASTS:
        for suite, by_scale in stats[c].items():
            for scale, record in by_scale.items():
                expected_suite = truth["suite_ratios"][suite][scale][c]
                assert record["estimate"] == pytest.approx(expected_suite, rel=1e-9), (c, suite, scale)
                assert record["ci_low"] == pytest.approx(record["estimate"]) == pytest.approx(record["ci_high"])
                for query, q in record["q"].items():
                    expected = truth["ratios"][suite][scale][query][c]
                    if q["status"] == "ok":
                        assert q["estimate"] == pytest.approx(expected, rel=1e-9), (c, suite, scale, query)
                        checked += 1
    assert checked > 150


def test_noisy_tree_intervals_bracket_the_truth_and_widen_with_noise(noisy, quiet):
    _, truth, stats = noisy
    hits = total = 0
    for c in ("c1", "c2", "c3", "c4"):
        for suite, by_scale in stats[c].items():
            for scale, record in by_scale.items():
                expected = truth["suite_ratios"][suite][scale][c]
                assert record["estimate"] == pytest.approx(expected, rel=0.15), (c, suite, scale)
                assert record["ci_low"] < record["estimate"] < record["ci_high"]
                assert record["min"] <= record["estimate"] <= record["max"]
                hits += record["ci_low"] <= expected <= record["ci_high"]
                total += 1
    assert hits / total >= 0.75, f"only {hits}/{total} 95% intervals contain the injected ratio"


def test_suite_estimate_is_geomean_per_round_then_t_interval_not_pooled(noisy):
    _, _, stats = noisy
    record = stats["c2"]["baseline"]["sf30"]
    queries = record["queries_used"]
    per_round = []
    for position, rnd in enumerate(record["rounds"]):
        logs = [math.log(stats["c2"]["baseline"]["sf30"]["q"][q]["per_round"][position]) for q in queries]
        per_round.append(math.exp(sum(logs) / len(logs)))
    assert record["per_round"] == pytest.approx(per_round)
    again = AC.ratio_summary(per_round, record["rounds"])
    assert record["estimate"] == pytest.approx(again["estimate"]) and record["ci_low"] == pytest.approx(again["ci_low"])


def test_medians_are_the_median_of_three_trials_then_geomean_over_rounds(quiet):
    _, truth, stats = quiet
    cell = stats["medians"]["baseline"]["sf100"]["q03"]["polars"]
    assert cell["status"] == "ok" and cell["n_rounds"] == 4
    assert cell["geomean"] == pytest.approx(truth["times"]["baseline"]["sf100"]["q03"]["polars"])
    assert cell["min"] == pytest.approx(cell["max"])


def test_per_query_median_ignores_warmup_and_takes_middle_of_three(tmp_path):
    session = {"engine": "polars", "runs": 3, "completed_at_utc": "2026-11-03T00:00:00+00:00", "queries": ["q01"],
               "records": [{"query": "q01", "phase": "warmup", "trial": 0, "seconds": 99.0},
                           {"query": "q01", "phase": "measured", "trial": 1, "seconds": 3.0},
                           {"query": "q01", "phase": "measured", "trial": 2, "seconds": 1.0},
                           {"query": "q01", "phase": "measured", "trial": 3, "seconds": 2.0}]}
    cells = AC.session_cells(session, ["q01"], engine="polars")
    assert cells["q01"]["status"] == "ok" and cells["q01"]["median"] == 2.0


# ------------------------------------------------------------------ exclusions

def test_failed_gate_round_is_excluded_listed_and_cannot_move_estimates(tmp_path):
    _, truth, stats = build(tmp_path, "gate", failed_gate_rounds=(3,), **QUIET)
    campaign = stats["campaign"]
    assert campaign["rounds_included"] == [1, 2, 4] and campaign["n_rounds"] == 3 and campaign["df"] == 2
    assert campaign["t_critical"] == pytest.approx(4.30265272991)
    excluded = campaign["rounds_excluded"]
    assert [x["round"] for x in excluded] == [3] and "fail" in excluded[0]["reason"].lower()
    # round 3 holds timings x10; had it leaked in, nothing could match the truth
    assert stats["c1"]["baseline"]["sf30"]["estimate"] == pytest.approx(truth["suite_ratios"]["baseline"]["sf30"]["c1"])
    assert stats["c1"]["baseline"]["sf30"]["rounds"] == [1, 2, 4]
    assert stats["medians"]["baseline"]["sf30"]["q01"]["polars"]["geomean"] == pytest.approx(truth["times"]["baseline"]["sf30"]["q01"]["polars"])


def test_failed_gate_round_is_reported_in_tables_and_report(tmp_path):
    root, _, stats = build(tmp_path, "gate", failed_gate_rounds=(2,), **QUIET)
    AC.write_outputs(stats, tmp_path / "out")
    rounds_csv = list(csv.DictReader((tmp_path / "out" / "tables" / "t19_rounds.csv").open()))
    assert {(r["label"], r["status"]) for r in rounds_csv} == {("round-1", "included"), ("round-2", "excluded"),
                                                               ("round-3", "included"), ("round-4", "included")}
    assert "round-2" in (tmp_path / "out" / "STATS_REPORT.md").read_text()


def test_round_without_gates_json_is_excluded_unless_explicitly_allowed(tmp_path):
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, **QUIET)
    (root / "round-4" / "gates.json").unlink()
    strict = AC.analyze(root, energy=False)
    assert strict["campaign"]["rounds_included"] == [1, 2, 3]
    assert "gates" in strict["campaign"]["rounds_excluded"][0]["reason"]
    lenient = AC.analyze(root, allow_ungated=True, energy=False)
    assert lenient["campaign"]["rounds_included"] == [1, 2, 3, 4]


def test_repeat_round_supersedes_the_failed_one(tmp_path):
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, failed_gate_rounds=(2,), **QUIET)
    (root / "round-2b").mkdir()
    import shutil
    shutil.copytree(root / "round-1", root / "round-2b", dirs_exist_ok=True)
    stats = AC.analyze(root, energy=False)
    assert stats["campaign"]["rounds_included"] == [1, 2, 3, 4]
    assert stats["campaign"]["rounds_included_labels"] == ["round-1", "round-2b", "round-3", "round-4"]
    assert any(x["label"] == "round-2" for x in stats["campaign"]["rounds_excluded"])


def test_all_rounds_failing_gates_exits_nonzero_without_estimates(tmp_path):
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, failed_gate_rounds=(1, 2, 3, 4), **QUIET)
    code = AC.main(["--root", str(root), "--output", str(tmp_path / "out"), "--no-energy"])
    assert code == 1
    stats = json.loads((tmp_path / "out" / "stats.json").read_text())
    assert stats["campaign"]["rounds_included"] == [] and len(stats["campaign"]["rounds_excluded"]) == 4


# ------------------------------------------------------------------ coverage and unsupported queries

def test_unsupported_polars_gpu_queries_are_excluded_from_c1_c3_but_visible_in_coverage(quiet):
    _, _, stats = quiet
    for c in ("c1", "c3", "c4_strict"):
        record = stats[c]["complex"]["sf30"]
        assert "cx_q13" in record["queries_excluded"] and "cx_window" in record["queries_excluded"]
        assert "cx_q13" not in record["queries_used"] and "cx_window" not in record["queries_used"]
        assert record["n_queries"] == 4
        for query in ("cx_q13", "cx_window"):
            q = record["q"][query]
            assert q["status"] == "unsupported" and q["estimate"] is None
            assert any(b["engine"] == "polars_gpu" and b["reason_class"] == "unsupported_operation" for b in q["blocked_by"])
    for query in ("cx_q13", "cx_window"):
        cell = stats["coverage"]["matrix"]["complex"][query]["sf30"]["polars_gpu"]
        assert cell["status"] == "failed" and cell["reason_class"] == "unsupported_operation"
        assert cell["predeclared_unsupported"] is True and cell["rounds_ok"] == 0 and cell["rounds_total"] == 4
        assert stats["coverage"]["matrix"]["complex"][query]["sf30"]["spark_gpu"]["status"] == "supported"
    # the pre-declared gaps are not discrepancies; the undeclared OOM of cx_self_join at SF300 is
    assert not any("cx_q13" in d or "cx_window" in d for d in stats["coverage"]["discrepancies"])
    assert any("cx_self_join" in d for d in stats["coverage"]["discrepancies"])
    # the timed medians exist for the GPU engine that did run and are absent for the one that did not
    assert stats["medians"]["complex"]["sf30"]["cx_q13"]["polars_gpu"]["geomean"] is None
    assert stats["medians"]["complex"]["sf30"]["cx_q13"]["spark_gpu"]["geomean"] is not None


def test_best_gpu_contrast_keeps_queries_only_one_gpu_engine_supports(quiet):
    _, truth, stats = quiet
    c4 = stats["c4"]["complex"]["sf30"]
    assert c4["n_queries"] == 6 and not c4["queries_excluded"]
    q = c4["q"]["cx_q13"]
    assert q["status"] == "ok" and q["best_engine_rounds"] == {"spark_gpu": 4}
    assert q["estimate"] == pytest.approx(truth["ratios"]["complex"]["sf30"]["cx_q13"]["c4"])
    assert stats["c4_strict"]["complex"]["sf30"]["n_queries"] == 4


def test_oom_failure_is_classified_and_not_confused_with_unsupported_operation(quiet):
    _, _, stats = quiet
    cell = stats["coverage"]["matrix"]["complex"]["cx_self_join"]["sf300"]["polars_gpu"]
    assert cell["status"] == "failed" and cell["reason_class"] == "out_of_memory" and cell["predeclared_unsupported"] is False
    assert any("cx_self_join" in d and "polars_gpu" in d for d in stats["coverage"]["discrepancies"])
    assert AC.classify_failure("MemoryError", "std::bad_alloc: out_of_memory") == "out_of_memory"
    assert AC.classify_failure("NotImplementedError", "x is not supported on the GPU") == "unsupported_operation"
    assert AC.classify_failure("RuntimeError", "boom") == "error"


def test_partial_query_is_reported_partial_and_leaves_the_suite_geomean(tmp_path):
    extra = [(2, "baseline", "30", "q10", "spark_gpu", ("TimeoutError", "action exceeded the timeout"))]
    _, _, stats = build(tmp_path, "partial", extra_failures=extra, **QUIET)
    q = stats["c2"]["baseline"]["sf30"]["q"]["q10"]
    assert q["status"] == "partial" and q["n_rounds"] == 3 and q["rounds"] == [1, 3, 4] and q["df"] == 2
    suite = stats["c2"]["baseline"]["sf30"]
    assert "q10" not in suite["queries_used"] and "q10" in suite["queries_excluded"] and suite["n_queries"] == 5
    cell = stats["coverage"]["matrix"]["baseline"]["q10"]["sf30"]["spark_gpu"]
    assert cell["status"] == "partial" and cell["rounds_ok"] == 3 and cell["reason_class"] == "timeout"
    assert stats["legacy"]["totals"]["baseline"]["sf30"]["spark_gpu"]["complete"] is False


def test_missing_or_failed_sessions_never_crash_and_never_count(tmp_path):
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, **QUIET)
    (root / "round-2" / "baseline" / "sf10" / "spark_cpu" / "results.json").unlink()
    broken = root / "round-3" / "baseline" / "sf10" / "polars" / "results.json"
    session = json.loads(broken.read_text())
    session["failed_at_utc"] = "2026-11-03T00:00:00+00:00"
    broken.write_text(json.dumps(session))
    stats = AC.analyze(root, energy=False)
    assert stats["coverage"]["matrix"]["baseline"]["q01"]["sf10"]["spark_cpu"]["status"] == "partial"
    assert stats["c2"]["baseline"]["sf10"]["q"]["q01"]["n_rounds"] == 3
    assert stats["medians"]["baseline"]["sf10"]["q01"]["polars"]["n_rounds"] == 3


def test_polars_gpu_without_strict_mode_is_invalid_not_a_timing(tmp_path):
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, **QUIET)
    path = root / "round-1" / "baseline" / "sf1" / "polars_gpu" / "results.json"
    session = json.loads(path.read_text())
    session["polars_gpu"]["raise_on_fail"] = False
    path.write_text(json.dumps(session))
    stats = AC.analyze(root, energy=False)
    assert stats["coverage"]["matrix"]["baseline"]["q01"]["sf1"]["polars_gpu"]["status"] == "partial"
    assert stats["c1"]["baseline"]["sf1"]["q"]["q01"]["rounds"] == [2, 3, 4]


# ------------------------------------------------------------------ legacy totals

def test_legacy_totals_are_arithmetic_sums_of_medians_separate_from_geomeans(quiet):
    _, truth, stats = quiet
    totals = stats["legacy"]["totals"]["baseline"]["sf30"]
    for engine in ("polars", "spark_cpu", "spark_gpu", "polars_gpu"):
        expected = sum(truth["times"]["baseline"]["sf30"][q][engine] for q in AC.BASELINE_QUERIES)
        assert totals[engine]["complete"] is True
        assert totals[engine]["mean_total"] == pytest.approx(expected)
    ratio = stats["legacy"]["ratios"]["baseline"]["sf30"]["c1"]["ratio_of_mean_totals"]
    assert ratio == pytest.approx(totals["polars"]["mean_total"] / totals["polars_gpu"]["mean_total"])
    # the sum-weighted ratio differs from the equal-weight geometric mean: the two must never be interchanged
    assert ratio != pytest.approx(stats["c1"]["baseline"]["sf30"]["estimate"], rel=1e-3)
    assert not any(k in stats["legacy"]["ratios"]["baseline"]["sf30"]["c1"] for k in ("estimate", "ci_low", "ci_high"))
    assert "arithmetic" in stats["legacy"]["note"]


def test_legacy_total_is_absent_when_any_query_is_missing(quiet):
    _, _, stats = quiet
    gpu = stats["legacy"]["totals"]["complex"]["sf30"]["polars_gpu"]
    assert gpu["complete"] is False and gpu["mean_total"] is None
    assert set(gpu["queries_missing"]) == {"cx_q13", "cx_window"}
    assert stats["legacy"]["ratios"]["complex"]["sf30"]["c1"]["ratio_of_mean_totals"] is None
    assert stats["legacy"]["ratios"]["complex"]["sf30"]["c4"]["ratio_of_mean_totals"] is not None


# ------------------------------------------------------------------ crossover (RQ3)

def test_crossover_scale_matches_the_scale_where_the_truth_crosses_one(quiet):
    _, truth, stats = quiet
    for suite in ("baseline", "complex"):
        scales = [s for s in truth["suite_ratios"][suite]]
        expected = next((s for s in scales if truth["suite_ratios"][suite][s]["c4"] > 1.0), None)
        found = stats["crossover"]["c4"][suite]
        assert found["first_gpu_faster_scale"] == expected and found["stable_from_scale"] == expected
        assert found["crossover_found"] is True
        verdicts = found["verdicts"]
        assert all(v == "cpu_faster" for s, v in verdicts.items() if truth["suite_ratios"][suite][s]["c4"] < 1.0)
        assert all(v == "gpu_faster" for s, v in verdicts.items() if truth["suite_ratios"][suite][s]["c4"] > 1.0)


def test_crossover_requires_the_interval_not_just_the_point_estimate():
    estimates = {"sf1": {"estimate": 1.05, "ci_low": 0.9, "ci_high": 1.2}, "sf10": {"estimate": 1.5, "ci_low": 1.1, "ci_high": 2.0}}
    assert AC.verdict(estimates["sf1"]) == "inconclusive" and AC.verdict(estimates["sf10"]) == "gpu_faster"
    assert AC.verdict({"estimate": 0.5, "ci_low": 0.4, "ci_high": 0.9}) == "cpu_faster"
    assert AC.verdict({"estimate": 1.0, "ci_low": None, "ci_high": None}) == "n/a"


# ------------------------------------------------------------------ variability and order

def test_variability_is_zero_without_noise_and_positive_with_it(quiet, noisy):
    quiet_stats, noisy_stats = quiet[2], noisy[2]
    for engine in AC.ENGINE_ORDER:
        assert quiet_stats["variability"]["by_engine"][engine]["within_session"]["max"] == pytest.approx(0.0, abs=1e-12)
        assert quiet_stats["variability"]["by_engine"][engine]["between_rounds"]["max"] == pytest.approx(0.0, abs=1e-12)
        within = noisy_stats["variability"]["by_engine"][engine]["within_session"]["median"]
        between = noisy_stats["variability"]["by_engine"][engine]["between_rounds"]["median"]
        assert 0.005 < within < 0.15 and 0.01 < between < 0.30


def test_position_effect_is_recovered_and_design_is_balanced(tmp_path):
    _, _, stats = build(tmp_path, "pos", **{**QUIET, "position_effect": 0.05}, a100=False, side_study=False)
    balance = stats["order"]["design_balance"]
    assert balance["positions_balanced"] is True and balance["pairs_balanced"] is True
    means = [stats["order"]["position"][str(p)]["mean_pct"] for p in (1, 2, 3, 4)]
    logs = [math.log(1 + 0.05 * k) for k in range(4)]
    centre = sum(logs) / 4
    for p, observed in enumerate(means):
        assert observed == pytest.approx(math.expm1(logs[p] - centre) * 100, rel=1e-6)
    assert means == sorted(means) and means[0] < 0 < means[-1]
    assert "none" in stats["order"]["predecessor"]
    assert stats["order"]["position"]["1"]["n_rounds"] == 4


def test_order_balance_is_flagged_when_the_observed_orders_are_not_a_latin_square(tmp_path):
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, **QUIET)
    for suite in AC.SUITES:
        schedule_path = root / "round-4" / suite / "schedule.json"
        schedule = json.loads(schedule_path.read_text())
        # make round 4 repeat round 1's order
        for scale in {s["scale"] for s in schedule}:
            block = [s for s in schedule if s["scale"] == scale]
            order = ["spark_cpu", "polars_gpu", "spark_gpu", "polars"]
            starts = sorted(s["start_utc"] for s in block)
            for item, engine, start in zip(block, order, starts):
                item["engine"], item["start_utc"] = engine, start
        schedule_path.write_text(json.dumps(schedule))
    stats = AC.analyze(root, energy=False)
    assert stats["order"]["design_balance"]["positions_balanced"] is False


# ------------------------------------------------------------------ secondary metrics

def test_energy_proxy_is_mean_power_times_wall_time_and_survives_a_timezone_offset(tmp_path):
    results = {}
    for tz in (0, -8):
        _, _, stats = build(tmp_path, f"tz{tz}", tz_hours=tz, **{**QUIET, "gpu_interval": 5.0}, a100=False, side_study=False)
        results[tz] = stats
    assert results[0]["campaign"]["energy"]["tz_offset_seconds"] == [0]
    assert results[-8]["campaign"]["energy"]["tz_offset_seconds"] == [-8 * 3600]
    for tz, stats in results.items():
        cell = stats["secondary"]["energy"]["by_cell"]["baseline"]["sf30"]
        assert cell["polars_gpu"]["mean_power_w"] == pytest.approx(235.0, abs=6.0)
        assert cell["polars"]["mean_power_w"] == pytest.approx(78.0, abs=6.0)
        for engine, e in cell.items():
            assert e["energy_wh"] == pytest.approx(e["mean_power_w"] * e["process_wall_seconds"] / 3600.0, rel=1e-6)
    assert results[0]["secondary"]["energy"]["by_cell"]["baseline"]["sf30"]["polars_gpu"]["mean_power_w"] == pytest.approx(
        results[-8]["secondary"]["energy"]["by_cell"]["baseline"]["sf30"]["polars_gpu"]["mean_power_w"], abs=6.0)


def test_secondary_metrics_present_for_the_right_engines(quiet):
    _, _, stats = quiet
    memory = stats["secondary"]["gpu_memory"]
    assert memory["capacity_bytes"] == AC.SYNTH_H100_BYTES
    assert set(memory["summary"]["baseline"]["sf300"]) == {"polars_gpu", "spark_gpu"}   # CPU engines carry no GPU memory
    assert memory["summary"]["baseline"]["sf300"]["polars_gpu"]["max_bytes"] > memory["summary"]["baseline"]["sf1"]["polars_gpu"]["max_bytes"]
    assert stats["secondary"]["process"]["baseline"]["sf1"]["spark_gpu"]["launch_to_engine_ready_seconds"] == pytest.approx(19.0)
    cleanup = stats["secondary"]["cleanup"]
    assert "baseline" not in cleanup and set(cleanup["complex"]["sf30"]) == set(AC.ENGINE_ORDER)
    assert cleanup["complex"]["sf30"]["spark_gpu"]["mean_seconds_per_cleanup"] > cleanup["complex"]["sf30"]["polars"]["mean_seconds_per_cleanup"]


# ------------------------------------------------------------------ side study and A100

def test_polars_version_bridge_recovers_the_injected_ratio(quiet):
    _, truth, stats = quiet
    meta = stats["side_study"]["meta"]
    assert meta["available"] and meta["numerator"] == "polars_2_0_0" and meta["denominator"] == "polars_1_44_2"
    for suite, by_scale in stats["side_study"]["ratio"].items():
        for scale, record in by_scale.items():
            assert record["estimate"] == pytest.approx(truth["side_study"][suite][scale]) and record["df"] == 3
            assert record["n_queries"] == 6


def test_a100_comparison_covers_gpu_engines_only_and_flags_non_equivalence(quiet):
    _, _, stats = quiet
    meta = stats["a100_vs_h100"]["meta"]
    assert meta["equivalent_hosts"] is False and meta["cpu_engines_compared"] is False
    assert set(stats["a100_vs_h100"]["ratio"]) == {"spark_gpu"}   # the A100 study had no polars_gpu; CPU engines are never compared
    for suite, by_scale in stats["a100_vs_h100"]["ratio"]["spark_gpu"].items():
        for scale, record in by_scale.items():
            assert record["estimate"] == pytest.approx(AC.SYNTH_A100_SLOWDOWN)
    labels = " ".join(h["label"] for h in meta["a100_hosts"])
    assert "A100" in labels and "30 vCPU" in labels
    assert meta["engine_facts"]["h100"]["spark_gpu"]["spark_jars"] == ["rapids-4-spark_2.12-26.08.1.jar"]


def test_a100_interval_reflects_h100_round_variation_only(noisy):
    _, _, stats = noisy
    record = stats["a100_vs_h100"]["ratio"]["spark_gpu"]["baseline"]["sf30"]
    assert record["df"] == 3 and record["ci_low"] < record["estimate"] < record["ci_high"]


# ------------------------------------------------------------------ schema, watermark, outputs

def test_every_stats_key_is_documented_and_every_documented_key_exists(noisy):
    _, _, stats = noisy
    flat = AC.flatten_stats(stats)
    undocumented = [k for k, v in flat.items() if not AC.schema_matches(k)
                    and not (v == {} and any(p.startswith(k + ".") for p, _, _ in AC.SCHEMA))]
    assert undocumented == []
    seen = {p for k in flat for p in AC.schema_matches(k)}
    unused = [p for p, _, optional in AC.SCHEMA if not optional and p not in seen]
    assert unused == []


def test_lead_example_keys_resolve(noisy):
    _, _, stats = noisy
    flat = AC.flatten_stats(stats)
    for key in ("c1.baseline.sf300.estimate", "c1.baseline.sf300.ci_low", "c1.baseline.sf300.ci_high", "c1.baseline.sf300.q.q01.estimate",
                "c4.complex.sf100.min", "crossover.c4.baseline.first_gpu_faster_scale", "side_study.ratio.baseline.sf30.estimate",
                "a100_vs_h100.ratio.spark_gpu.baseline.sf30.estimate", "campaign.df"):
        assert key in flat, key
        assert AC.lookup(stats, key) == flat[key]


def test_outputs_exist_are_strict_json_and_carry_the_watermark(tmp_path):
    out = tmp_path / "out"
    code = AC.main(["--synthetic", "--seed", "4", "--output", str(out)])
    assert code == 0
    for name in ("stats.json", "stats_flat.json", "STATS_SCHEMA.md", "STATS_REPORT.md", "SYNTHETIC_NOT_RESULTS.txt"):
        assert (out / name).is_file(), name

    def reject(constant):
        raise ValueError(f"non-finite constant {constant} in stats.json")

    stats = json.loads((out / "stats.json").read_text(), parse_constant=reject)
    assert stats["synthetic"] is True and stats["watermark"] == AC.WATERMARK
    assert AC.WATERMARK in (out / "STATS_REPORT.md").read_text()
    tables = sorted((out / "tables").glob("t*.csv"))
    assert len(tables) == 24
    for csv_path in tables:
        stem = csv_path.with_suffix("")
        rows = list(csv.DictReader(csv_path.open()))
        assert rows and all(r["data_status"] == AC.WATERMARK for r in rows), csv_path.name
        assert AC.WATERMARK in stem.with_suffix(".md").read_text(), stem.name
        tex = stem.with_suffix(".tex").read_text()
        assert AC.WATERMARK_ASCII in tex and r"\toprule" in tex and r"\bottomrule" in tex, stem.name


def test_real_tree_is_not_watermarked(tmp_path):
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, **QUIET)
    meta = json.loads((root / "campaign.json").read_text())
    del meta["synthetic"]
    (root / "campaign.json").write_text(json.dumps(meta))
    code = AC.main(["--root", str(root), "--output", str(tmp_path / "out"), "--no-energy"])
    assert code == 0
    stats = json.loads((tmp_path / "out" / "stats.json").read_text())
    assert stats["synthetic"] is False and stats["watermark"] is None
    assert not (tmp_path / "out" / "SYNTHETIC_NOT_RESULTS.txt").exists()
    assert "SYNTHETIC" not in (tmp_path / "out" / "STATS_REPORT.md").read_text()
    rows = list(csv.DictReader((tmp_path / "out" / "tables" / "t01_suite_contrasts.csv").open()))
    assert {r["data_status"] for r in rows} == {"measured"}
    assert "SYNTHETIC" not in (tmp_path / "out" / "tables" / "t01_suite_contrasts.tex").read_text()


def test_tables_use_booktabs_escape_latex_and_keep_csv_raw(tmp_path):
    out = tmp_path / "out"
    assert AC.main(["--synthetic", "--seed", "4", "--output", str(out), "--no-energy"]) == 0
    tex = (out / "tables" / "t01_suite_contrasts.tex").read_text()
    body = "\n".join(line for line in tex.splitlines() if not line.startswith("%"))
    assert r"\begin{tabular}" in tex and r"95\% CI" in tex and "_" not in body.replace(r"\_", "")
    long = (out / "tables" / "t02_query_contrasts.tex").read_text()
    assert r"\begin{longtable}" in long and r"\endhead" in long
    rows = list(csv.DictReader((out / "tables" / "t01_suite_contrasts.csv").open()))
    first = next(r for r in rows if r["contrast"] == "c1" and r["suite"] == "baseline" and r["scale"] == "sf300")
    assert float(first["estimate"]) == pytest.approx(json.loads((out / "stats.json").read_text())["c1"]["baseline"]["sf300"]["estimate"])
    assert float(first["ci_low"]) < float(first["estimate"]) < float(first["ci_high"])


def test_synthetic_generator_refuses_to_overwrite_a_real_directory(tmp_path):
    target = tmp_path / "important"
    target.mkdir()
    (target / "keep.txt").write_text("data")
    with pytest.raises(FileExistsError):
        AC.make_synthetic_campaign(target, seed=1)
    assert (target / "keep.txt").read_text() == "data"


def test_synthetic_generator_is_deterministic_per_seed(tmp_path):
    a = AC.analyze(AC.make_synthetic_campaign(tmp_path / "a", seed=5, a100=False, side_study=False) and tmp_path / "a", energy=False)
    b = AC.analyze(AC.make_synthetic_campaign(tmp_path / "b", seed=5, a100=False, side_study=False) and tmp_path / "b", energy=False)
    c = AC.analyze(AC.make_synthetic_campaign(tmp_path / "c", seed=6, a100=False, side_study=False) and tmp_path / "c", energy=False)
    assert a["c1"] == b["c1"] and a["c1"] != c["c1"]
