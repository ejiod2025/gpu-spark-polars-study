"""The A100 -> H100 secondary comparison (F9, a100_vs_h100): the A100 side is the previous study's saved evidence, and says so.

The earlier study ran Polars 2.0.0 with 30 threads, one session per cell and an older run protocol; it is not the A100 campaign. Every difference is read from
the two sides' result files (a field the earlier study did not record is null on its side, never guessed), and CPU engines are never compared.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import matplotlib
import pytest

matplotlib.use("Agg")

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_campaign as AC  # noqa: E402
import plot_campaign as PC  # noqa: E402

QUIET = dict(round_sigma=0.0, query_sigma=0.0, trial_sigma=0.0, position_effect=0.0, gpu_interval=60.0)
EVIDENCE = [REPO / "evidence" / "results" / name for name in
            ("production-20261025", "large-sf100-20261025", "large-sf300-20261025", "complex-formal-v2-20261025")]
H100_TREE = REPO / "rerun" / "hosts" / "h100" / "campaign-h100"


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    root = tmp_path_factory.mktemp("a100study") / "tree"
    AC.make_synthetic_campaign(root, seed=5, side_study=False, a100=True, **QUIET)
    roots = sorted((root / "a100").iterdir())
    return root, roots, json.loads(json.dumps(AC.analyze(root, roots, energy=False)))


def test_without_a100_roots_every_study_key_exists_and_is_empty(tmp_path):
    root = tmp_path / "t"
    AC.make_synthetic_campaign(root, seed=5, side_study=False, a100=False, **QUIET)
    study = json.loads(json.dumps(AC.analyze(root, energy=False)))["a100_vs_h100"]["study"]
    assert study["label"] is None and study["previous_study"] is None and study["differences"] == [] and study["roots"] == []
    assert study["plan_variability"]["cells"] == [] and study["plan_variability"]["sessions_checked"] == 0
    assert study["difference_headline"] is None and study["spark_conf_keys_recorded"] == {"a100_study": None, "h100_campaign": None}


def test_the_study_keys_are_documented(synthetic):
    _, _, stats = synthetic
    flat = AC.flatten_stats(stats)
    keys = [k for k in flat if k.startswith("a100_vs_h100.study")]
    assert len(keys) > 20 and all(AC.schema_matches(k) for k in keys), [k for k in keys if not AC.schema_matches(k)]
    documented = [p for p, _, _ in AC.SCHEMA if p.startswith("a100_vs_h100.study.")]
    assert all(any(AC.schema_regex(p).match(k) for k in flat) or p.endswith("spark_conf_differences") for p in documented), documented


def test_the_a100_side_is_labelled_as_the_previous_study_with_its_date(synthetic):
    _, roots, stats = synthetic
    study = stats["a100_vs_h100"]["study"]
    assert study["previous_study"] is True and study["roots"] == [str(r) for r in roots]
    first = study["first_session_utc"][:10]
    assert study["label"] == f"{first} A100 study" or study["label"].startswith(f"{first} to ")
    assert study["last_session_utc"] >= study["first_session_utc"]
    assert "previous study's saved evidence" in stats["a100_vs_h100"]["meta"]["definition"] and "not the A100 campaign" in stats["a100_vs_h100"]["meta"]["definition"]


def test_only_gpu_engines_are_compared_and_the_cpu_engines_are_named_as_not_compared(synthetic):
    _, _, stats = synthetic
    a100 = stats["a100_vs_h100"]
    assert a100["meta"]["cpu_engines_compared"] is False
    assert set(a100["ratio"]) <= set(AC.GPU_ENGINES) and set(a100["study"]["engines_compared"]) == set(a100["ratio"])
    assert set(a100["study"]["engines_present"]) >= set(a100["study"]["engines_compared"])
    assert "polars_gpu" not in a100["study"]["engines_present"] and a100["study"]["engines_only_in_h100_campaign"] == ["polars_gpu"]


def test_differences_are_read_from_the_result_files(synthetic):
    _, _, stats = synthetic
    study = stats["a100_vs_h100"]["study"]
    by_field = {}
    for d in study["differences"]:
        assert set(d) == {"field", "engines", "suites", "a100_study", "h100_campaign", "in_compared_ratio"}
        by_field.setdefault(d["field"], []).append(d)
    assert (study["cpu_threads"], study["cpu_threads_h100"]) == (30, 26)
    assert by_field["cpu_threads"][0]["a100_study"] == [30] and by_field["cpu_threads"][0]["h100_campaign"] == [26]
    assert by_field["cpu_threads"][0]["in_compared_ratio"] is True
    assert (study["sessions_per_cell"], study["rounds_h100"]) == (1, 4) and by_field["sessions_per_cell"][0]["h100_campaign"] == [4]
    assert "30 against 26 threads" in study["difference_headline"] and "1 session per cell against 4 rounds" in study["difference_headline"]
    assert "against" in study["difference_summary"] and study["difference_summary"].count(";") >= 2
    assert all(x["value"] is not None for x in study["same"])


def test_an_engine_that_is_not_in_the_ratio_is_flagged_as_such():
    h100 = {(1, "baseline", "sf1", "polars"): {"version": "1.44.2", "protocol": {"cpu_threads": 26}}}
    sessions = [{"suite": "baseline", "scale": "sf1", "engine": "polars", "version": "2.0.0", "cpu_threads": 30, "started_at_utc": "2026-10-25T18:00:00+00:00"}]
    data = SimpleNamespace(sessions=h100, rounds=[1], blocks={})
    a100 = {"cells": {("baseline", "sf1", "q01", "spark_gpu"): {}}, "sessions": sessions, "orders": {}, "hosts": [], "arguments": [], "roots": ["x"], "campaign_roots": []}
    study = AC.a100_study_block(data, a100, {"gpu": "H100"}, ["spark_gpu"])
    version = next(d for d in study["differences"] if d["field"] == "version")
    assert version["engines"] == ["polars"] and version["in_compared_ratio"] is False
    assert "Polars 2.0.0 against 1.44.2 (CPU engine; not in the ratio)" in study["difference_headline"]
    thread = next(d for d in study["differences"] if d["field"] == "cpu_threads")
    assert thread["in_compared_ratio"] is False                                # only the Polars CPU engine is in this stub


def test_a_campaign_root_is_not_called_a_previous_study():
    data = SimpleNamespace(sessions={}, rounds=[1], blocks={})
    a100 = {"cells": {("baseline", "sf1", "q01", "spark_gpu"): {}}, "sessions": [], "orders": {}, "hosts": [], "arguments": [], "roots": ["r"], "campaign_roots": ["r"]}
    study = AC.a100_study_block(data, a100, None, ["spark_gpu"])
    assert study["previous_study"] is False and study["label"] == "previous A100 study"      # no session carries a start time: no date is invented


def test_table_report_and_figure_name_the_study(synthetic, tmp_path, monkeypatch):
    root, roots, stats = synthetic
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out), "--no-energy", "--a100", *map(str, roots)]) == 0
    label = json.loads((out / "stats.json").read_text())["a100_vs_h100"]["study"]["label"]
    report = (out / "STATS_REPORT.md").read_text()
    section = report[report.index("## 11. A100"):report.index("## 12. Caveats")]
    assert f"**{label}**" in section and "not the A100 campaign" in section and "What differs from the H100 campaign" in section and "Main differences:" in section
    header = (out / "tables" / "t22_a100_study_differences.csv").read_text().splitlines()[0]
    assert "field,engines,suites,a100_study,h100_campaign,in_compared_ratio" in header
    notes = []
    original = PC.finish
    monkeypatch.setattr(PC, "finish", lambda fig, ctx, fig_id, title, caption, *a, **k: (notes.append((fig_id, title, caption, [t.get_text() for t in fig.texts])),
                                                                                          original(fig, ctx, fig_id, title, caption, *a, **k))[1])
    ctx = PC.build_figures(json.loads((out / "stats.json").read_text()), tmp_path / "figs", only=["f09"], formats=("png",))
    assert {n[0] for n in notes} == {"f09_a100_vs_h100", "f09_a100_vs_h100_1col"}
    for fig_id, title, caption, texts in notes:
        assert label in title and label in caption and "not the A100 campaign" in caption
        assert "30 against 26 threads" in caption
        footnote = " ".join(" ".join(texts).split())
        assert label in footnote and "NOT an equivalent comparison" in footnote and "CPU engines are not compared" in footnote
        assert "Polars GPU was not run in that study" in footnote
    assert all(label in m["caption_draft"] for m in ctx.manifest if m["id"].startswith("f09"))


# ------------------------------------------------------------------ the saved evidence of the 2026-10-25 study

@pytest.mark.skipif(not (H100_TREE / "round-1" / "gates.json").is_file() or not all(r.is_dir() for r in EVIDENCE),
                    reason="the H100 campaign copy or the A100 evidence is not in this checkout")
def test_the_real_evidence_is_the_2026_10_25_study_and_its_differences_are_listed():
    stats = AC.analyze(H100_TREE, EVIDENCE, energy=False, label="H100")
    a100 = stats["a100_vs_h100"]
    study = a100["study"]
    assert study["label"] == "2026-10-25 A100 study" and study["previous_study"] is True
    assert study["engines_present"] == ["polars", "spark_cpu", "spark_gpu"] and study["engines_compared"] == ["spark_gpu"]
    assert study["engines_only_in_h100_campaign"] == ["polars_gpu"] and set(a100["ratio"]) == {"spark_gpu"}
    assert (study["polars_version"], study["polars_version_h100"]) == ("2.0.0", "1.44.2")
    assert (study["cpu_threads"], study["cpu_threads_h100"]) == (30, 26) and (study["sessions_per_cell"], study["rounds_h100"]) == (1, stats["campaign"]["n_rounds"])
    assert (study["gpu"], study["vcpus"]) == ("NVIDIA A100-SXM4-40GB", 30) and study["spark_version"] == study["spark_version_h100"] == "3.5.9"
    version = next(d for d in study["differences"] if d["field"] == "version")
    assert version["engines"] == ["polars"] and version["in_compared_ratio"] is False                   # Polars 2.0.0 vs 1.44.2 is not in the Spark GPU ratio
    notes = {(d["field"], tuple(d["suites"])): d for d in study["differences"]}
    for field in ("query_cleanup_policy", "failure_policy", "suite_version"):                           # the earlier baseline sessions carry no such field
        assert notes[(field, ("baseline",))]["a100_study"] == [None] and notes[(field, ("baseline",))]["h100_campaign"][0] is not None
    assert notes[("spark_gpu_profile", ("baseline", "complex"))]["h100_campaign"] == ["a100-parity"]
    same = {(x["field"], x["engine"], x["suite"]): x["value"] for x in study["same"]}
    assert same[("query_cleanup_policy", "spark_gpu", "complex")] == "verified_gc" and same[("runs", "spark_gpu", "baseline")] == 3
    assert study["spark_conf_differences"]["spark.master"] == {"a100_study": ["local[30]"], "h100_campaign": ["local[26]"], "relative_difference": None}
    assert study["spark_conf_differences"]["spark.rapids.sql.multiThreadedRead.memoryLimit.size"]["relative_difference"] < 0.05
    assert study["spark_conf_keys_recorded"]["a100_study"] < study["spark_conf_keys_recorded"]["h100_campaign"]
    assert "Polars 2.0.0 against 1.44.2 (CPU engine; not in the ratio)" in study["difference_headline"]
    assert "30 against 26 threads" in study["difference_headline"] and "1 session per cell against" in study["difference_headline"]
    earlier = {(c["suite"], c["scale"], c["query"], c["engine"]): c for c in study["plan_variability"]["cells"]}
    cell = earlier[("complex", "sf30", "cx_q09", "spark_gpu")]
    measured = next(p for p in cell["plans"] if p["n_measured"])
    warmup = next(p for p in cell["plans"] if not p["n_measured"])
    assert measured["n_measured"] == 3 and sum(measured["features"]["exchanges"].values()) == 8 and measured["features"]["shuffle_readers"].get("local") == 1
    assert warmup["n_warmup"] == 1 and sum(warmup["features"]["exchanges"].values()) == 7 and not warmup["features"]["shuffle_readers"].get("local")
    assert study["plan_variability"]["cells_checked"] > 40 and study["plan_variability"]["plan_files_missing"] == 0
    assert len(study["arguments"]) == 4 and all(a["cpus"] == 30 for a in study["arguments"])
    assert len(a100["ratio"]["spark_gpu"]["baseline"]) == 5 and len(a100["ratio"]["spark_gpu"]["complex"]) == 3
