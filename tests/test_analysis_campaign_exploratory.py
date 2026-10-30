"""Exploratory (post-hoc) analyses E1 and E4 (stats["exploratory"], t23, F2x).

E1 = time of Polars 2.0.0 CPU (side-study sessions) / time of Polars GPU (main rounds); E4 = the same CPU time / the better GPU engine per query and round. The
two sides are unpaired sessions from different rounds, so the interval is Welch-type on the log scale. Nothing here may reach C1-C4, the crossover or any other
primary output; every block carries the label "exploratory (post-hoc; not in the locked design §9)".
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
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
PRIMARY = ("c1", "c2", "c3", "c4", "c4_strict", "medians", "fastest", "cpu_reference", "legacy", "variability", "order", "crossover", "coverage", "unstable_coverage",
           "output_agreement", "contrasts", "secondary", "side_study")


def build(tmp_path, name, side_study=True, **kwargs):
    root = tmp_path / name
    truth = AC.make_synthetic_campaign(root, seed=3, a100=False, side_study=side_study, **{**QUIET, **kwargs})
    return root, truth


def analyze(root):
    return json.loads(json.dumps(AC.analyze(root, energy=False)))


@pytest.fixture(scope="module")
def quiet(tmp_path_factory):
    root, truth = build(tmp_path_factory.mktemp("expl"), "quiet")
    return root, truth, analyze(root)


@pytest.fixture(scope="module")
def noisy(tmp_path_factory):
    root = tmp_path_factory.mktemp("expl-noisy") / "noisy"
    AC.make_synthetic_campaign(root, seed=11, a100=False, side_study=True, gpu_interval=60.0)
    return root, analyze(root)


def side_time(truth, suite, scale, query):
    return truth["times"][suite][scale][query]["polars"] * AC.SYNTH_VERSION_RATIO[suite]


# ------------------------------------------------------------------ the estimator recovers the injected truth

def test_e1_recovers_the_injected_ratio_per_query_and_per_suite(quiet):
    _, truth, stats = quiet
    block = stats["exploratory"]["e1"]
    assert stats["exploratory"]["e1"]["meta"]["available"] is True and block["meta"]["cpu_version"] == "2.0.0"
    seen = 0
    for suite in ("baseline", "complex"):
        for scale, record in block[suite].items():
            times = truth["times"][suite][scale]
            usable = [q for q, t in times.items() if t["polars_gpu"] is not None]
            assert record["queries_used"] == usable and record["n_queries"] == len(usable)
            for q, t in times.items():
                got = record["q"][q]
                if t["polars_gpu"] is None:
                    assert got["status"] == "unsupported" and got["estimate"] is None and got["blocked_by"][0]["engine"] == "polars_gpu"
                    assert got["blocked_by"][0]["reason_class"] in AC.COVERAGE_CLASSES and q in record["queries_excluded"]
                    continue
                assert got["estimate"] == pytest.approx(side_time(truth, suite, scale, q) / t["polars_gpu"]) and got["status"] == "ok"
                seen += 1
            expected = math.exp(statistics.fmean(math.log(side_time(truth, suite, scale, q) / times[q]["polars_gpu"]) for q in usable))
            assert record["estimate"] == pytest.approx(expected)
            assert (record["n_cpu_rounds"], record["n_gpu_rounds"]) == (4, 4)
    assert seen > 30


def test_e4_uses_the_better_gpu_engine_per_query(quiet):
    _, truth, stats = quiet
    block = stats["exploratory"]["e4"]
    for suite in ("baseline", "complex"):
        for scale, record in block[suite].items():
            times = truth["times"][suite][scale]
            assert record["queries_used"] == list(times)
            for q, t in times.items():
                best = min(v for e, v in t.items() if e in AC.GPU_ENGINES and v is not None)
                assert record["q"][q]["estimate"] == pytest.approx(side_time(truth, suite, scale, q) / best)
                winner = min((e for e in AC.GPU_ENGINES if t[e] is not None), key=lambda e: t[e])
                assert set(record["q"][q]["best_engine_rounds"]) == {winner} and record["q"][q]["best_engine_rounds"][winner] == 4


def test_the_orientation_is_that_of_c1_and_c4(quiet):
    _, _, stats = quiet
    # the injected Polars 2.0.0 factor is 0.92 (baseline) and 1.08 (complex): E equals C times that factor, in the same direction as C
    for suite, factor in (("baseline", AC.SYNTH_VERSION_RATIO["baseline"]), ("complex", AC.SYNTH_VERSION_RATIO["complex"])):
        for scale, record in stats["exploratory"]["e1"][suite].items():
            assert record["estimate"] == pytest.approx(stats["c1"][suite][scale]["estimate"] * factor)
        for scale, record in stats["exploratory"]["e4"][suite].items():
            assert record["estimate"] == pytest.approx(stats["c4"][suite][scale]["estimate"] * factor)


# ------------------------------------------------------------------ the interval is Welch-type on unpaired sessions

def test_the_interval_is_the_welch_interval_of_the_two_unpaired_sets_of_rounds(noisy):
    _, stats = noisy
    record = stats["exploratory"]["e1"]["baseline"]["sf30"]["q"]["q03"]
    a, b = record["cpu_per_round"], record["gpu_per_round"]
    la, lb = [math.log(x) for x in a], [math.log(x) for x in b]
    va, vb = statistics.variance(la) / len(la), statistics.variance(lb) / len(lb)
    se = math.sqrt(va + vb)
    df = (va + vb) ** 2 / (va ** 2 / (len(la) - 1) + vb ** 2 / (len(lb) - 1))
    t = AC.t_quantile(0.975, df)
    mid = statistics.fmean(la) - statistics.fmean(lb)
    assert record["estimate"] == pytest.approx(math.exp(mid)) and record["log_se"] == pytest.approx(se) and record["df"] == pytest.approx(df)
    assert record["ci_low"] == pytest.approx(math.exp(mid - t * se)) and record["ci_high"] == pytest.approx(math.exp(mid + t * se))
    assert record["cpu_rounds"] == [1, 2, 3, 4] and record["gpu_rounds"] == [1, 2, 3, 4]
    suite = stats["exploratory"]["e1"]["baseline"]["sf30"]
    assert suite["df"] is not None and not float(suite["df"]).is_integer()          # Welch-Satterthwaite, not rounds - 1


def test_the_two_sides_are_not_paired_by_round(noisy):
    _, stats = noisy
    record = stats["exploratory"]["e1"]["complex"]["sf100"]
    w = AC.welch_summary(record["cpu_per_round"], record["gpu_per_round"])
    shuffled = AC.welch_summary(list(reversed(record["cpu_per_round"])), record["gpu_per_round"])
    assert (w["estimate"], w["ci_low"], w["ci_high"], w["df"]) == pytest.approx((shuffled["estimate"], shuffled["ci_low"], shuffled["ci_high"], shuffled["df"]))
    assert "unpaired" in stats["exploratory"]["e1"]["meta"]["interval"] and "Welch" in stats["exploratory"]["e1"]["meta"]["interval"]


def test_one_side_study_round_gives_an_estimate_without_an_interval(tmp_path):
    root, truth = build(tmp_path, "one")
    for k in (2, 3, 4):
        shutil.rmtree(root / "side-study" / f"round-{k}")
    stats = analyze(root)
    record = stats["exploratory"]["e1"]["baseline"]["sf30"]
    assert record["n_cpu_rounds"] == 1 and record["n_gpu_rounds"] == 4 and record["estimate"] is not None
    assert record["ci_low"] is None and record["ci_high"] is None and record["df"] is None
    assert stats["exploratory"]["e1"]["meta"]["side_study_rounds"] == [1]


# ------------------------------------------------------------------ labelling, availability, separation from the primary outputs

def test_every_block_carries_the_exploratory_label_and_says_how_it_is_built(quiet):
    _, _, stats = quiet
    for name, contrast, counterpart in (("e1", "Polars 2.0.0 CPU / Polars GPU", "c1"), ("e4", "Polars 2.0.0 CPU / best GPU engine", "c4")):
        meta = stats["exploratory"][name]["meta"]
        assert meta["label"] == LABEL and meta["contrast"] == contrast and meta["counterpart"] == counterpart
        assert meta["side_study_rounds"] == [1, 2, 3, 4] and meta["main_rounds"] == [1, 2, 3, 4] and meta["reason"] is None
        assert "post-hoc" in meta["definition"] and "never mixed into C1-C4" in meta["definition"] and "Welch" in meta["interval"]


def test_without_a_side_study_the_block_exists_but_is_unavailable(tmp_path):
    root, _ = build(tmp_path, "none", side_study=False)
    stats = analyze(root)
    for name in ("e1", "e4"):
        block = stats["exploratory"][name]
        assert set(block) == {"meta"} and block["meta"]["available"] is False and block["meta"]["label"] == LABEL and block["meta"]["reason"]
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out), "--no-energy"]) == 0
    assert "Not computed" in (out / "STATS_REPORT.md").read_text() and (out / "tables" / "t23_exploratory.csv").read_text().count("\n") == 1


def test_primary_outputs_are_the_same_with_and_without_the_exploratory_data(tmp_path, quiet):
    _, _, with_side = quiet
    root, _ = build(tmp_path, "without", side_study=False)
    without = analyze(root)
    for key in PRIMARY:
        if key == "side_study":
            continue
        assert with_side[key] == without[key], key
    assert "exploratory" in with_side and with_side["crossover"] == without["crossover"]
    # no primary key carries exploratory content
    flat = AC.flatten_stats({k: v for k, v in with_side.items() if k != "exploratory"})
    assert not any("exploratory" in k or k.endswith(".e1") for k in flat)


def test_keys_are_documented_and_the_schema_names_the_block_as_exploratory(quiet):
    _, _, stats = quiet
    flat = AC.flatten_stats(stats)
    keys = [k for k in flat if k.startswith("exploratory.")]
    assert len(keys) > 200 and all(AC.schema_matches(k) for k in keys), [k for k in keys if not AC.schema_matches(k)][:5]
    schema = AC.schema_markdown()
    assert "Exploratory block" in schema and LABEL in schema and "never used by C1-C4" in schema


# ------------------------------------------------------------------ table, report, figure

def test_table_and_report_set_the_exploratory_results_apart(quiet, tmp_path):
    root, _, _ = quiet
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out), "--no-energy"]) == 0
    report = (out / "STATS_REPORT.md").read_text()
    section = report[report.index("### EXPLORATORY"):report.index("## 11. A100")]
    assert "post-hoc; not in the locked design §9" in section and "never used by C1-C4 or the crossover verdict" in section and "Welch" in section
    assert "E1 (exploratory)" in section and "E4 (exploratory)" in section and "C1 " in section and "C4 " in section
    # nowhere else in the report: the primary sections stay free of E1 / E4
    assert "E1" not in report.replace(section, "") and "exploratory" not in report.replace(section, "").lower().replace("exploratory.", "")
    header = (out / "tables" / "t23_exploratory.csv").read_text().splitlines()[0]
    assert "contrast,suite,scale,n_queries,n_cpu_rounds,n_gpu_rounds,estimate,ci_low,ci_high,df,counterpart" in header
    for primary in ("t01_suite_contrasts.md", "t11_crossover.md"):
        assert "xploratory" not in (out / "tables" / primary).read_text()


def test_f2x_draws_c_beside_e_with_the_exploratory_panels_marked(quiet, tmp_path, monkeypatch):
    root, _, _ = quiet
    stats = json.loads(json.dumps(AC.analyze(root, energy=False)))
    shown = []
    original = PC.finish
    monkeypatch.setattr(PC, "finish", lambda fig, ctx, fig_id, title, caption, sources, *a, **k: (
        shown.append((fig_id, title, caption, sources, [t.get_text() for t in fig.texts], [a_.get_title() for a_ in fig.axes],
                      [[t.get_text() for t in a_.texts] for a_ in fig.axes])), original(fig, ctx, fig_id, title, caption, sources, *a, **k))[1])
    ctx = PC.build_figures(stats, tmp_path / "figs", only=["f02x", "f02"], formats=("png",))
    by_id = {s[0]: s for s in shown}
    assert {"f02x_c1_e1_exploratory", "f02x_c1_e1_exploratory_1col", "f02_suite_speedup_vs_scale", "f02_suite_speedup_vs_scale_1col"} <= set(by_id)
    for fig_id in ("f02x_c1_e1_exploratory", "f02x_c1_e1_exploratory_1col"):
        _, title, caption, sources, texts, _, annotations = by_id[fig_id]
        assert "exploratory" in title and "post-hoc" in title
        heading = " ".join(" ".join(texts).split())
        assert "EXPLORATORY E1 and E4 (post-hoc; not in the locked design §9)" in heading
        assert "exploratory" in caption and "post-hoc" in caption and "Welch-type" in caption and "never used by the crossover verdict" in caption
        assert any(s.startswith("exploratory.e1") for s in sources) and "c1.*.*" in sources
        assert "crossover" not in " ".join(sources)
        assert sum(1 for a_ in annotations if "EXPLORATORY\npost-hoc" in a_) == 2                    # the E1 and E4 panels are stamped, C1 and C4 are not
        assert any("not used by the crossover verdict" in t.replace("\n", " ") for t in texts)
    for fig_id in ("f02_suite_speedup_vs_scale", "f02_suite_speedup_vs_scale_1col"):
        assert not any("exploratory" in s for s in by_id[fig_id][3]) and "xploratory" not in by_id[fig_id][2]
    assert all(m["id"] != "f02x_c1_e1_exploratory" or "exploratory" in m["caption_draft"] for m in ctx.manifest)


def test_f2x_is_skipped_with_a_reason_when_there_is_no_side_study(tmp_path):
    root, _ = build(tmp_path, "none", side_study=False)
    stats = analyze(root)
    ctx = PC.build_figures(stats, tmp_path / "figs", only=["f02x"], formats=("png",))
    assert not ctx.manifest
    skipped = json.loads((tmp_path / "figs" / "figures.json").read_text())["skipped"]
    assert skipped and all(s["id"] == "f02x" and "side study" in s["reason"] for s in skipped)


# ------------------------------------------------------------------ the copied GH200 campaign

GH200 = REPO / "rerun" / "hosts" / "gh200" / "final" / "campaign-gh200"


@pytest.mark.skipif(not (GH200 / "side-study" / "round-4" / "gates.json").is_file(), reason="the final GH200 campaign is not in this checkout")
def test_gh200_final_e1_matches_an_independent_recomputation_from_the_result_files():
    stats = AC.analyze(GH200, energy=False, label="GH200")
    e1 = stats["exploratory"]["e1"]
    assert e1["meta"]["side_study_rounds"] == [1, 2, 3, 4] and e1["meta"]["main_rounds"] == [1, 2, 3, 4]

    def round_median(path, query):
        rows = [r["seconds"] for r in json.loads(path.read_text())["records"] if r["query"] == query and r["phase"] == "measured"]
        return statistics.median(rows)

    for suite, scale, query in (("baseline", "sf300", "q03"), ("baseline", "sf1", "q06"), ("complex", "sf100", "cx_q09")):
        cpu = [round_median(GH200 / "side-study" / f"round-{k}" / "polars-2.0.0" / suite / scale / "polars" / "results.json", query) for k in (1, 2, 3, 4)]
        gpu = [round_median(GH200 / f"round-{k}" / suite / scale / "polars_gpu" / "results.json", query) for k in (1, 2, 3, 4)]
        expected = AC.welch_summary(cpu, gpu)
        got = e1[suite][scale]["q"][query]
        assert got["estimate"] == pytest.approx(expected["estimate"]) and got["ci_low"] == pytest.approx(expected["ci_low"]) and got["ci_high"] == pytest.approx(expected["ci_high"])
        assert got["status"] == "ok" and (got["n_cpu_rounds"], got["n_gpu_rounds"]) == (4, 4)
    assert e1["complex"]["sf30"]["q"]["cx_window"]["status"] == "unsupported" and e1["complex"]["sf30"]["n_queries"] == 4
    assert stats["exploratory"]["e4"]["complex"]["sf30"]["n_queries"] == 6
    for contrast in ("c1", "c4", "c4_strict"):                                        # the locked-design numbers are what they were
        assert stats[contrast]["baseline"]["sf300"]["estimate"] is not None
