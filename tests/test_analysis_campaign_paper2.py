"""Paper-2 analyses (analyze_campaign.build_paper2, plot_campaign f20 / f21): protocol check, cross-host speedup decomposition, C1 beside E1.

Everything is exploratory (post-hoc, not in the locked design section 9). The decomposition identity log(speedup_ratio) = log(cpu_side) - log(gpu_side) must hold
exactly for the point estimates because all three use one matched query set.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import statistics
import sys

import matplotlib
import pytest

matplotlib.use("Agg")

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_campaign as AC  # noqa: E402
import plot_campaign as PC  # noqa: E402

LABEL = "exploratory (post-hoc; not in the locked design §9)"
QUIET = dict(round_sigma=0.0, query_sigma=0.0, trial_sigma=0.0, position_effect=0.0, gpu_interval=60.0)
HOSTS = tuple(AC.SYNTH_HOSTS)


def build_host(base, host, side_study=True, seed=3, **kwargs):
    root = Path(base) / (host + ("" if side_study else "-noside"))
    truth = AC.make_synthetic_campaign(root, seed=seed, a100=False, side_study=side_study, host=host, **{**QUIET, **kwargs})
    return truth, json.loads(json.dumps(AC.analyze(root, energy=False)))


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    base = tmp_path_factory.mktemp("p2")
    built = {h: build_host(base, h) for h in HOSTS}
    truth = {h: t for h, (t, _) in built.items()}
    stats = {h: s for h, (_, s) in built.items()}
    return truth, stats, AC.build_paper2(stats)


def geo(values):
    return math.exp(statistics.fmean(math.log(v) for v in values))


# ------------------------------------------------------------------ shape and labelling

def test_the_file_is_labelled_and_keyed_by_lower_case_host_in_ladder_order(synth):
    _, stats, p2 = synth
    assert p2["meta"]["label"] == LABEL and p2["meta"]["reference"] == "h100" and p2["meta"]["hosts"] == ["a100", "h100", "gh200", "b200"]
    assert set(p2) == {"meta", "hosts", "protocol_check", "decomp", "sensitivity", "exploratory_adj"} and set(p2["hosts"]) == set(p2["protocol_check"]) == set(p2["sensitivity"]) == {"a100", "h100", "gh200", "b200"}
    assert set(p2["decomp"]) == {"a100", "gh200", "b200"}                                  # the reference has no decomposition against itself
    for key, label in zip(p2["meta"]["hosts"], ("A100", "H100", "GH200", "B200")):
        info = p2["hosts"][key]
        assert info["label"] == label and info["bridge_available"] is True and info["cores_text"] == AC.paper2_cores_text(stats[label]["host"])
    assert p2["hosts"]["h100"]["cores_text"] == "26 thr on 13 cores (SMT2)" and p2["hosts"]["gh200"]["cores_text"] == "26 thr on 26 cores (SMT1)"
    assert p2["hosts"]["h100"]["role"] == "reference" and p2["hosts"]["a100"]["side_study_rounds"] == [1, 2, 3, 4]


def test_cores_text_is_the_threads_on_the_cores_they_can_occupy():
    assert AC.paper2_cores_text({"cpus_arg": 26, "cpus_arg_cores_min": 13, "cpus_arg_cores_max": 13, "smt_threads_per_core": 2}) == "26 thr on 13 cores (SMT2)"
    assert AC.paper2_cores_text({"cpus_arg": 8, "cpus_arg_cores_min": 4, "cpus_arg_cores_max": 8, "smt_threads_per_core": 2}) == "8 thr on 4 to 8 cores (SMT2)"
    assert AC.paper2_cores_text({"cpus_arg": 26, "cpus_arg_cores_min": None, "cpus_arg_cores_max": None}) is None


# ------------------------------------------------------------------ K1 protocol check

def test_protocol_check_is_one_when_the_two_polars_1_44_2_sessions_are_the_same_machine(synth):
    _, _, p2 = synth
    for host in p2["meta"]["hosts"]:
        for suite, by_scale in p2["protocol_check"][host].items():
            for scale, r in by_scale.items():
                assert r["estimate"] == pytest.approx(1.0) and r["ci_low"] == pytest.approx(1.0) and r["ci_high"] == pytest.approx(1.0)
                assert r["n_queries"] == 6 and r["n_side_rounds"] == r["n_main_rounds"] == 4 and r["df"] == pytest.approx(6.0)
                assert set(r) >= {"estimate", "ci_low", "ci_high", "df", "n_queries"}
    assert set(p2["protocol_check"]["h100"]) == {"baseline", "complex"} and set(p2["protocol_check"]["h100"]["baseline"]) == {"sf1", "sf10", "sf30", "sf100", "sf300"}


def test_protocol_check_with_noise_matches_a_recomputation_from_the_per_round_medians(tmp_path):
    _, stats = build_host(tmp_path, "H100", seed=11, round_sigma=0.04, query_sigma=0.03, trial_sigma=0.02, position_effect=0.01, gpu_interval=60.0)
    record = AC.build_paper2({"H100": stats})["protocol_check"]["h100"]["baseline"]["sf30"]
    side = stats["side_study"]["medians"]["baseline"]["sf30"]
    queries = record["queries_used"]
    old = "polars_1_44_2"
    side_rounds = [geo([side[q][old]["per_round"][i] for q in queries]) for i in range(4)]
    main_rounds = [geo([stats["medians"]["baseline"]["sf30"][q]["polars"]["per_round"][i] for q in queries]) for i in range(4)]
    la, lb = [math.log(x) for x in side_rounds], [math.log(x) for x in main_rounds]
    va, vb = statistics.variance(la) / 4, statistics.variance(lb) / 4
    df = (va + vb) ** 2 / (va ** 2 / 3 + vb ** 2 / 3)
    half = AC.t_quantile(0.975, df) * math.sqrt(va + vb)
    mid = statistics.fmean(la) - statistics.fmean(lb)
    assert record["estimate"] == pytest.approx(math.exp(mid)) and record["df"] == pytest.approx(df)
    assert record["ci_low"] == pytest.approx(math.exp(mid - half)) and record["ci_high"] == pytest.approx(math.exp(mid + half))
    assert abs(record["estimate"] - 1.0) < 0.1                             # the two sessions differ only by noise


# ------------------------------------------------------------------ K2 decomposition

def truth_ratio(truth, host, ref, suite, scale, queries, engine):
    return geo([truth[host]["times"][suite][scale][q][engine] / truth[ref]["times"][suite][scale][q][engine] for q in queries])


def test_the_decomposition_identity_holds_for_every_cell(synth):
    _, _, p2 = synth
    cells = 0
    for host, by_c in p2["decomp"].items():
        for c, by_suite in by_c.items():
            for suite, by_scale in by_suite.items():
                for scale, r in by_scale.items():
                    assert math.log(r["speedup_ratio"]["estimate"]) == pytest.approx(math.log(r["cpu_side"]["estimate"]) - math.log(r["gpu_side"]["estimate"]), abs=1e-9)
                    assert r["identity_gap"] <= 1e-9 and set(r["speedup_ratio"]) == {"estimate", "ci_low", "ci_high", "df"}
                    cells += 1
    assert cells == 3 * 2 * 8                                                # three hosts, c1 and c2, eight suite-scale blocks


def test_the_three_ratios_use_one_matched_query_set_and_recover_the_injected_truth(synth):
    truth, _, p2 = synth
    for host in ("A100", "GH200", "B200"):
        for c, cpu_engine, gpu_engine in (("c1", "polars", "polars_gpu"), ("c2", "spark_cpu", "spark_gpu")):
            for suite in ("baseline", "complex"):
                for scale, r in p2["decomp"][host.lower()][c][suite].items():
                    times = lambda h: truth[h]["times"][suite][scale]
                    matched = [q for q in times(host) if times(host)[q][gpu_engine] is not None and times("H100")[q][gpu_engine] is not None
                               and times(host)[q][cpu_engine] is not None and times("H100")[q][cpu_engine] is not None]
                    assert r["queries_used"] == matched and r["n_queries"] == len(matched)
                    cpu, gpu = truth_ratio(truth, host, "H100", suite, scale, matched, cpu_engine), truth_ratio(truth, host, "H100", suite, scale, matched, gpu_engine)
                    assert r["cpu_side"]["estimate"] == pytest.approx(cpu) and r["gpu_side"]["estimate"] == pytest.approx(gpu)
                    assert r["speedup_ratio"]["estimate"] == pytest.approx(cpu / gpu)


def test_c1_complex_counts_only_the_queries_polars_gpu_supports_on_the_cpu_side_too(synth):
    _, stats, p2 = synth
    for host in ("gh200", "b200"):
        r = p2["decomp"][host]["c1"]["complex"]["sf30"]
        assert r["n_queries"] == 4 and "cx_q13" not in r["queries_used"] and "cx_window" not in r["queries_used"]
        xhost = AC.compare_hosts({h: stats[h] for h in HOSTS})["ratio"][host.upper()]["polars"]["complex"]["sf30"]
        assert xhost["n_queries"] == 6 and xhost["estimate"] != pytest.approx(r["cpu_side"]["estimate"])        # the cross-host polars ratio spans six queries
    r2 = p2["decomp"]["gh200"]["c2"]["complex"]["sf30"]
    assert r2["n_queries"] == 6                                                                               # Spark has no unsupported queries


def test_intervals_are_welch_type_across_the_two_hosts_rounds(synth, tmp_path):
    _, stats, _ = synth
    noisy = {h: build_host(tmp_path, h, seed=11, round_sigma=0.04, query_sigma=0.03, trial_sigma=0.02, position_effect=0.01)[1] for h in ("H100", "GH200")}
    r = AC.build_paper2(noisy)["decomp"]["gh200"]["c2"]["baseline"]["sf100"]
    host_c, ref_c = noisy["GH200"]["c2"]["baseline"]["sf100"]["per_round"], noisy["H100"]["c2"]["baseline"]["sf100"]["per_round"]
    expected = AC.welch_summary(host_c, ref_c)
    assert r["speedup_ratio"]["estimate"] == pytest.approx(expected["estimate"]) and r["speedup_ratio"]["df"] == pytest.approx(expected["df"])
    assert r["speedup_ratio"]["ci_low"] == pytest.approx(expected["ci_low"]) and not float(r["speedup_ratio"]["df"]).is_integer()


# ------------------------------------------------------------------ missing bridge, sensitivity, CLI, figures

def test_a_host_without_a_side_study_has_no_bridge_and_no_protocol_check(tmp_path):
    stats = {"H100": build_host(tmp_path, "H100")[1], "A100": build_host(tmp_path, "A100", side_study=False)[1]}
    p2 = AC.build_paper2(stats)
    assert p2["hosts"]["a100"]["bridge_available"] is False and p2["protocol_check"]["a100"] == {}
    assert all(rec["e1"] is None and rec["c1"]["estimate"] is not None for by_scale in p2["sensitivity"]["a100"].values() for rec in by_scale.values())
    assert all(rec["e1"]["estimate"] is not None for by_scale in p2["sensitivity"]["h100"].values() for rec in by_scale.values())
    assert p2["decomp"]["a100"]["c1"]["baseline"]["sf30"]["n_queries"] == 6                           # the decomposition does not need a side study


def test_sensitivity_copies_c1_and_e1_unchanged(synth):
    _, stats, p2 = synth
    rec = p2["sensitivity"]["gh200"]["complex"]["sf100"]
    assert rec["c1"]["estimate"] == stats["GH200"]["c1"]["complex"]["sf100"]["estimate"] and rec["c1"]["ci_low"] == stats["GH200"]["c1"]["complex"]["sf100"]["ci_low"]
    assert rec["e1"]["estimate"] == stats["GH200"]["exploratory"]["e1"]["complex"]["sf100"]["estimate"] and rec["e1"]["n_queries"] == 4


def test_cli_writes_the_file_and_schema_and_the_figures(synth, tmp_path):
    _, stats, _ = synth
    paths = {}
    for h in HOSTS:
        paths[h] = tmp_path / f"{h}.json"
        paths[h].write_text(json.dumps(stats[h]))
    out = tmp_path / "paper2-final"
    code = AC.main(["--paper2", *sum((["--host", f"{h}={paths[h]}"] for h in HOSTS), []), "--reference", "H100", "--output", str(out)])
    assert code == 0 and (out / "paper2.json").is_file() and (out / "PAPER2_SCHEMA.md").is_file()
    loaded = json.loads((out / "paper2.json").read_text(), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    assert loaded["meta"]["label"] == LABEL
    schema = (out / "PAPER2_SCHEMA.md").read_text()
    assert LABEL in schema and all(f"`{k}" in schema for k in ("protocol_check.", "decomp.", "sensitivity.", "hosts.", "meta.label", "exploratory_adj.")) and "gap_exceeds_k1_limits" in schema
    assert PC.main(["--paper2", str(out), "--formats", "png", "--output", str(out / "figs")]) == 0
    manifest = json.loads((out / "figs" / "figures.json").read_text())
    assert sorted(m["id"] for m in manifest["figures"]) == ["f20_baseline_sensitivity", "f20_baseline_sensitivity_1col", "f21_speedup_decomposition", "f21_speedup_decomposition_1col"]
    for m in manifest["figures"]:
        assert "exploratory (post-hoc)" in m["title"] and LABEL in m["caption_draft"].lower()


def test_figures_draw_c1_beside_e1_and_mark_a_missing_bridge(tmp_path, monkeypatch):
    stats = {"H100": build_host(tmp_path, "H100")[1], "A100": build_host(tmp_path, "A100", side_study=False)[1], "GH200": build_host(tmp_path, "GH200")[1]}
    p2 = AC.build_paper2(stats)
    seen = {}
    original = PC.finish
    monkeypatch.setattr(PC, "finish", lambda fig, ctx, fig_id, title, caption, sources, *a, **k: (
        seen.__setitem__(fig_id, (fig, title, caption, [t.get_text() for ax in fig.axes for t in ax.texts], [t.get_text() for t in fig.texts],
                                  [[(ln.get_linestyle(), ln.get_marker()) for ln in ax.get_lines()] for ax in fig.axes])), original(fig, ctx, fig_id, title, caption, sources, *a, **k))[1])
    PC.build_paper2_figures(p2, tmp_path / "figs", formats=("png",))
    fig, title, caption, ax_texts, fig_texts, lines = seen["f20_baseline_sensitivity"]
    assert "exploratory (post-hoc)" in title and ax_texts.count("E1: bridge not available") == 2               # the A100 column, one per suite: E1 only; C1 is still drawn
    drawn = [panel for panel in lines if any(ls == "--" for ls, _ in panel)]
    assert len(drawn) == 4 and all(any(ls == "-" for ls, _ in panel) for panel in drawn)                      # C1 solid beside E1 dashed in the H100 and GH200 panels (two suites)
    assert all(any(m == "D" for _, m in panel) for panel in drawn)                                              # the engine's diamond marks the lines
    no_bridge = [panel for panel in lines if any(ls == "-" for ls, _ in panel) and not any(ls == "--" for ls, _ in panel)]
    assert len(no_bridge) == 2                                                                                  # the A100 panels show the pre-registered C1 alone
    assert "26 thr on 13 cores (SMT2)" in " ".join(fig_texts + ax_texts) or any("26 thr on" in t for t in ax_texts)
    assert "Welch" in caption and "locked-design contrast" in caption
    fig, title, caption, ax_texts, fig_texts, lines = seen["f21_speedup_decomposition"]
    assert "exploratory (post-hoc)" in title and "H100: 26 thr on 13 cores (SMT2)" in " ".join(" ".join(fig_texts).split())
    assert any("26 cores (SMT1)" in t for t in ax_texts) and "which lowers that host's speedup" in caption


def test_the_standard_figure_set_does_not_include_the_paper2_figures(synth):
    assert set(PC.PAPER2_FIGURES) == {"f20", "f21"} and not set(PC.PAPER2_FIGURES) & set(PC.FIGURES)


# ------------------------------------------------------------------ the final campaigns

FINAL = {h: REPO / "rerun" / "analysis" / d / "stats.json" for h, d in (("H100", "h100-final"), ("GH200", "gh200-final"), ("A100", "a100-main"), ("B200", "b200-main"))}
XHOST = REPO / "rerun" / "analysis" / "xhost-final" / "cross_host.json"


@pytest.mark.skipif(not all(p.is_file() for p in FINAL.values()) or not XHOST.is_file(), reason="the final analyses are not in this checkout")
def test_final_decomposition_agrees_with_the_cross_host_file_where_the_query_sets_match():
    stats = {h: json.loads(p.read_text()) for h, p in FINAL.items()}
    p2 = AC.build_paper2(stats)
    cross = json.loads(XHOST.read_text())
    n = 0
    for host in ("A100", "GH200", "B200"):
        for c, cpu_engine, gpu_engine in (("c1", "polars", "polars_gpu"), ("c2", "spark_cpu", "spark_gpu")):
            for suite, by_scale in p2["decomp"][host.lower()][c].items():
                for scale, r in by_scale.items():
                    assert abs(math.log(r["speedup_ratio"]["estimate"]) - (math.log(r["cpu_side"]["estimate"]) - math.log(r["gpu_side"]["estimate"]))) <= 1e-9
                    gpu = cross["ratio"][host][gpu_engine][suite][scale]
                    assert r["gpu_side"]["estimate"] == pytest.approx(gpu["estimate"], rel=1e-12) and r["gpu_side"]["ci_low"] == pytest.approx(gpu["ci_low"], rel=1e-9)
                    cpu = cross["ratio"][host][cpu_engine][suite][scale]
                    if cpu["n_queries"] == r["n_queries"]:                                                    # all but c1 complex
                        assert r["cpu_side"]["estimate"] == pytest.approx(cpu["estimate"], rel=1e-12) and r["cpu_side"]["df"] == pytest.approx(cpu["df"])
                        n += 1
                    else:
                        assert (c, suite) == ("c1", "complex") and r["n_queries"] == 4 and cpu["n_queries"] == 6
    assert n == 3 * (5 + 5 + 3)                                                                               # c1 baseline, c2 baseline, c2 complex per host (c1 complex has 4 queries)
    for host in ("H100", "GH200"):
        for suite, by_scale in p2["protocol_check"][host.lower()].items():
            assert all(0.9 < r["estimate"] < 1.1 and r["n_queries"] == 6 for r in by_scale.values())


# ------------------------------------------------------------------ K4 session-adjusted E1 / E4 and the K1 gap keys

def adjusted(p2, which, host, suite, scale):
    return p2["exploratory_adj"][which][host][suite][scale]


def test_adjusted_e1_is_b_matched_times_c1_and_equals_e1_over_k1(synth):
    _, stats, p2 = synth
    cells = 0
    for which, c in (("e1", "c1"), ("e4", "c4")):
        for host in ("h100", "gh200", "b200", "a100"):
            for suite, by_scale in p2["exploratory_adj"][which][host].items():
                for scale, r in by_scale.items():
                    cells += 1
                    assert r["identity_gap"] <= 1e-9 and r["estimate"] == pytest.approx(r["unadjusted"]["estimate"] / r["session_check"]["estimate"], rel=1e-9)
                    assert r["estimate"] == pytest.approx(r["b_matched"]["estimate"] * r["c_baseline"]["estimate"], rel=1e-12)
                    assert set(r) >= {"estimate", "ci_low", "ci_high", "df", "n_queries"} and r["n_queries"] == len(r["queries_used"])
                    contrast = stats[host.upper()][c][suite][scale]
                    if sorted(contrast["queries_used"]) == sorted(r["queries_used"]):          # C1 / C4 of the host's own campaign is the same quantity
                        assert r["c_baseline"]["estimate"] == pytest.approx(contrast["estimate"], rel=1e-9)
    assert cells == 2 * 4 * 8


def test_adjusted_equals_unadjusted_when_the_two_polars_1_44_2_sessions_agree(synth):
    _, _, p2 = synth
    for host in p2["meta"]["hosts"]:
        for suite, by_scale in p2["exploratory_adj"]["e1"][host].items():
            for scale, r in by_scale.items():
                assert r["session_check"]["estimate"] == pytest.approx(1.0) and r["estimate"] == pytest.approx(r["unadjusted"]["estimate"])
                assert r["verdict_changes"] is False and r["verdict_unadjusted"] == r["verdict_adjusted"]


def test_the_adjusted_interval_is_the_welch_combination_of_the_two_paired_factors(tmp_path):
    _, stats = build_host(tmp_path, "H100", seed=11, round_sigma=0.04, query_sigma=0.03, trial_sigma=0.02, position_effect=0.01, gpu_interval=60.0)
    r = AC.build_paper2({"H100": stats})["exploratory_adj"]["e1"]["h100"]["baseline"]["sf30"]
    queries = r["queries_used"]
    side = stats["side_study"]["medians"]["baseline"]["sf30"]
    main = stats["medians"]["baseline"]["sf30"]
    b = [geo([side[q]["polars_2_0_0"]["per_round"][i] for q in queries]) / geo([side[q]["polars_1_44_2"]["per_round"][i] for q in queries]) for i in range(4)]
    c = [geo([main[q]["polars"]["per_round"][i] for q in queries]) / geo([main[q]["polars_gpu"]["per_round"][i] for q in queries]) for i in range(4)]
    lb, lc = [math.log(x) for x in b], [math.log(x) for x in c]
    vb, vc = statistics.variance(lb) / 4, statistics.variance(lc) / 4
    df = (vb + vc) ** 2 / (vb ** 2 / 3 + vc ** 2 / 3)
    half = AC.t_quantile(0.975, df) * math.sqrt(vb + vc)
    mid = statistics.fmean(lb) + statistics.fmean(lc)
    assert r["estimate"] == pytest.approx(math.exp(mid)) and r["df"] == pytest.approx(df)
    assert r["ci_low"] == pytest.approx(math.exp(mid - half)) and r["ci_high"] == pytest.approx(math.exp(mid + half))
    assert r["b_matched"]["df"] == 3.0 and r["c_baseline"]["df"] == 3.0


def test_per_query_adjusted_records_cover_every_query_with_a_session_check(synth):
    _, _, p2 = synth
    r = adjusted(p2, "e1", "gh200", "complex", "sf30")
    assert sorted(r["q"]) == sorted(r["queries_used"]) and "cx_q13" not in r["q"]
    for q, rec in r["q"].items():
        assert rec["identity_gap"] <= 1e-9 and rec["n_queries"] == 1 and set(rec) >= {"estimate", "ci_low", "ci_high", "df", "verdict_unadjusted", "verdict_adjusted", "verdict_changes"}
    e4 = adjusted(p2, "e4", "gh200", "complex", "sf30")
    assert len(e4["queries_used"]) == 6 and len(r["queries_used"]) == 4                  # the best-engine side also has the two queries Polars GPU cannot run


def test_a_host_without_a_bridge_has_no_adjusted_block_and_a_verdict_change_is_flagged(tmp_path):
    stats = {"H100": build_host(tmp_path, "H100")[1], "A100": build_host(tmp_path, "A100", side_study=False)[1]}
    p2 = AC.build_paper2(stats)
    assert p2["exploratory_adj"]["e1"]["a100"] == {} and p2["exploratory_adj"]["e4"]["a100"] == {} and p2["exploratory_adj"]["e1"]["h100"]
    assert AC.paper2_verdict(1.1, 1.3) == "gpu_faster" and AC.paper2_verdict(0.7, 0.9) == "cpu_faster" and AC.paper2_verdict(0.9, 1.2) == "inconclusive" and AC.paper2_verdict(None, None) is None
    base = {"1": 2.0, "2": 2.0, "3": 2.0, "4": 2.0}
    shifted = dict(zip(base, [1.9, 2.0, 2.1, 2.0]))
    record = AC.paper2_adjusted_record({"q": base}, {"q": {k: v * 1.5 for k, v in base.items()}}, {"q": shifted}, {"q": {k: 1.0 for k in base}}, ["q"])
    assert record["verdict_unadjusted"] == "gpu_faster" and record["identity_gap"] <= 1e-9 and record["estimate"] == pytest.approx(1.5 * geo(shifted.values()))


def test_the_gap_keys_follow_the_definition_of_the_k1_limit(synth):
    _, _, p2 = synth
    for host in ("h100", "gh200", "a100", "b200"):
        for suite, by_scale in p2["protocol_check"][host].items():
            for scale, record in by_scale.items():
                sens = p2["sensitivity"][host][suite][scale]
                gap = 1.0 - sens["e1"]["estimate"] / sens["c1"]["estimate"]
                limit = max(abs(record["ci_low"] - 1.0), abs(record["ci_high"] - 1.0))
                assert record["e1_c1_gap"] == pytest.approx(gap, abs=1e-12) and record["k1_limit"] == pytest.approx(limit, abs=1e-12)
                assert record["gap_exceeds_k1_limits"] is bool(gap > limit)
    assert "e1_c1_gap" not in AC.paper2_gap_keys({"baseline": {"sf1": {"ci_low": 0.9, "ci_high": 1.1}}}, {"baseline": {"sf1": {"c1": {"estimate": 1.0}, "e1": None}}})["baseline"]["sf1"]


COMMITTED_STATS = {h: REPO / "results" / "campaign-20261030" / "analysis" / d / "stats.json" for h, d in (("H100", "h100-final"), ("GH200", "gh200-final"), ("A100", "a100-main"), ("B200", "b200-main"))}


@pytest.mark.skipif(not all(p.is_file() for p in COMMITTED_STATS.values()), reason="the committed analyses are not in this checkout")
def test_committed_h100_and_gh200_adjustment_changes_no_suite_level_verdict():
    p2 = AC.build_paper2({h: json.loads(p.read_text()) for h, p in COMMITTED_STATS.items()})
    gaps = {(h, su, sc): r for h in ("h100", "gh200") for su, bs in p2["protocol_check"][h].items() for sc, r in bs.items()}
    assert round(gaps[("h100", "baseline", "sf1")]["e1_c1_gap"], 3) == 0.043 and round(gaps[("h100", "baseline", "sf1")]["k1_limit"], 3) == 0.094
    assert round(gaps[("h100", "baseline", "sf10")]["e1_c1_gap"], 3) == 0.099 and round(gaps[("h100", "baseline", "sf10")]["k1_limit"], 3) == 0.108
    inside = sorted(k for k, r in gaps.items() if not r["gap_exceeds_k1_limits"])
    assert inside == [("h100", "baseline", "sf1"), ("h100", "baseline", "sf10")]
    suite_changes = [(w, h, su, sc) for w in ("e1", "e4") for h in ("h100", "gh200") for su, bs in p2["exploratory_adj"][w][h].items() for sc, r in bs.items() if r["verdict_changes"]]
    assert suite_changes == [] and sum(len(bs) for w in ("e1", "e4") for h in ("h100", "gh200") for bs in p2["exploratory_adj"][w][h].values()) == 32
    query_changes = sorted({(h, su, sc, q) for h in ("h100", "gh200") for su, bs in p2["exploratory_adj"]["e1"][h].items() for sc, r in bs.items() for q, rec in r["q"].items() if rec["verdict_changes"]})
    assert query_changes == [("h100", "baseline", "sf30", "q06"), ("h100", "baseline", "sf30", "q14"), ("h100", "baseline", "sf300", "q06")]
    assert max(r["identity_gap"] for w in ("e1", "e4") for h in ("h100", "gh200") for bs in p2["exploratory_adj"][w][h].values() for r in bs.values()) <= 1e-9


FINAL_FOUR = {"H100": COMMITTED_STATS["H100"], "GH200": COMMITTED_STATS["GH200"],
              "A100": REPO / "rerun" / "analysis" / "a100-final" / "stats.json", "B200": REPO / "rerun" / "analysis" / "b200-final" / "stats.json"}
XHOST_V2 = REPO / "rerun" / "analysis" / "xhost-final-v2" / "cross_host.json"


@pytest.mark.skipif(not all(p.is_file() for p in FINAL_FOUR.values()) or not XHOST_V2.is_file(), reason="a100-final, b200-final and xhost-final-v2 are not in this checkout")
def test_all_four_final_hosts_have_a_bridge_and_every_identity_holds():
    stats = {h: json.loads(p.read_text()) for h, p in FINAL_FOUR.items()}
    p2 = AC.build_paper2(stats)
    cross = json.loads(XHOST_V2.read_text())
    assert all(info["bridge_available"] and info["side_study_rounds"] == [1, 2, 3, 4] for info in p2["hosts"].values())
    for family in ("e1", "e4"):
        assert sum(len(bs) for h in p2["meta"]["hosts"] for bs in p2["exploratory_adj"][family][h].values()) == 32
        assert all(r["identity_gap"] <= 1e-9 and all(q["identity_gap"] <= 1e-9 for q in r["q"].values())
                   for h in p2["meta"]["hosts"] for bs in p2["exploratory_adj"][family][h].values() for r in bs.values())
        assert not [1 for h in p2["meta"]["hosts"] for bs in p2["exploratory_adj"][family][h].values() for r in bs.values() if r["verdict_changes"]]       # no suite-level verdict changes
    cells = [r for h in p2["decomp"] for by_c in p2["decomp"][h].values() for bs in by_c.values() for r in bs.values()]
    assert len(cells) == 3 * 2 * 8 and max(r["identity_gap"] for r in cells) <= 1e-9
    for host in ("A100", "GH200", "B200"):
        for suite, by_scale in p2["decomp"][host.lower()]["c2"].items():
            for scale, r in by_scale.items():
                assert r["cpu_side"]["estimate"] == pytest.approx(cross["ratio"][host]["spark_cpu"][suite][scale]["estimate"], rel=1e-12)
    assert sorted(h for h, by_suite in p2["protocol_check"].items() for bs in by_suite.values() for r in bs.values() if r["ci_low"] > 1 or r["ci_high"] < 1) == ["a100", "a100", "h100"]
    assert {h: sum(not r["gap_exceeds_k1_limits"] for bs in p2["protocol_check"][h].values() for r in bs.values()) for h in p2["meta"]["hosts"]} == {"a100": 1, "h100": 2, "gh200": 0, "b200": 1}
