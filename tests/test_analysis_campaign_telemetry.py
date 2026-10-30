"""Descriptive GPU telemetry (secondary.gpu_telemetry, t24, x08, F12): nvidia-smi samples that fall inside measured actions.

Label: "descriptive telemetry; not a pre-registered contrast; no causal claim". Actions shorter than 1 s are left out of every statistic (nvidia-smi is sampled every
~200 ms) and counted; the idle fraction is the share of samples with utilization.gpu below 10%.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_campaign as AC  # noqa: E402
import plot_campaign as PC  # noqa: E402

LABEL = "descriptive telemetry; not a pre-registered contrast; no causal claim"
QUIET = dict(round_sigma=0.0, query_sigma=0.0, trial_sigma=0.0, position_effect=0.0)
HOSTS = tuple(AC.SYNTH_HOSTS)


def build(tmp_path, name="t", host=AC.DEFAULT_REFERENCE, interval=0.5, seed=3, **kwargs):
    root = tmp_path / name
    AC.make_synthetic_campaign(root, seed=seed, a100=False, side_study=False, host=host, gpu_interval=interval, rounds=2, **{**QUIET, **kwargs})
    return root


def analyze(root, **kwargs):
    return json.loads(json.dumps(AC.analyze(root, **{"energy": True, **kwargs})))


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    root = build(tmp_path_factory.mktemp("tele"))
    return root, analyze(root)


# ------------------------------------------------------------------ parsing

def test_smi_values_with_units_and_not_available_markers():
    assert AC.smi_number("85 %") == 85.0 and AC.smi_number("235.12 W") == pytest.approx(235.12) and AC.smi_number("1410 MHz") == 1410.0
    assert AC.smi_number("512 MiB") == 512.0 and AC.smi_number("[N/A]") is None and AC.smi_number("") is None and AC.smi_number("[Not Supported]") is None


def test_gpu_csv_samples_are_read_for_the_requested_gpu_only(tmp_path):
    path = tmp_path / "gpu.csv"
    path.write_text("timestamp, index, utilization.gpu [%], utilization.memory [%], memory.used [MiB], power.draw [W], temperature.gpu, clocks.current.sm [MHz]\n"
                    "2026/10/30 03:57:03.323, 0, 85 %, 12 %, 100 MiB, 235.50 W, 40, 1700 MHz\n"
                    "2026/10/30 03:57:03.530, 1, 5 %, 1 %, 100 MiB, 90.00 W, 40, 345 MHz\n"
                    "2026/10/30 03:57:03.530, 0, [N/A], 0 %, 100 MiB, [N/A], 40, 1700 MHz\n"
                    "garbage line\n")
    samples = AC.read_gpu_samples(path, 0)
    assert len(samples["times"]) == 2 and samples["util"] == [85.0, None] and samples["power"] == [235.5, None] and samples["sm_clock"] == [1700.0, 1700.0]
    assert samples["times"][1] - samples["times"][0] == pytest.approx(0.207)
    assert AC.read_gpu_samples(path, 1)["util"] == [5.0]
    path.write_text("timestamp, index, power.draw [W]\n2026/10/30 03:57:03.323, 0, 70.0 W\n")
    assert AC.read_gpu_samples(path, 0)["util"] == [None]                 # a column the file does not have stays None


def test_power_limit_and_maximum_clock_come_from_nvidia_smi_q():
    env = {"inventory": [{"command": ["nvidia-smi", "-q"], "output": "Product Name : X\n    GPU Power Readings\n        Current Power Limit : 700.00 W\n"
                                                                    "        Max Power Limit : 900.00 W\n    Max Clocks\n        Graphics : 1980 MHz\n        SM : 1960 MHz\n"
                                                                    "    Module Power Readings\n        Current Power Limit : 1000.00 W\n"}]}
    assert AC.gpu_limits(env) == (700.0, 1960.0)                          # the first GPU's current limit, not the module's
    assert AC.gpu_limits({"inventory": []}) == (None, None) and AC.gpu_limits(None) == (None, None)


# ------------------------------------------------------------------ the statistics, against an independent recomputation

def independent_cell(root, suite, scale, engine, rounds=(1, 2)):
    """Util / idle / counts of one cell recomputed from gpu.csv and results.json with plain code."""
    util, short, used = [], 0, 0
    for rnd in rounds:
        run = root / f"round-{rnd}" / suite
        rows = list(csv.reader((run / "gpu.csv").open()))[1:]
        stamps = [AC.datetime.strptime(r[0].strip(), "%Y/%m/%d %H:%M:%S.%f").replace(tzinfo=AC.timezone.utc).timestamp() for r in rows]
        values = [float(r[2].split()[0]) for r in rows]
        for rec in json.loads((run / f"sf{scale}" / engine / "results.json").read_text())["records"]:
            if rec["phase"] != "measured":
                continue
            start, end = AC.parse_utc(rec["started_at_utc"]).timestamp(), AC.parse_utc(rec["ended_at_utc"]).timestamp()
            if end - start < 1.0:
                short += 1
                continue
            inside = [v for t, v in zip(stamps, values) if start <= t <= end]
            if inside:
                used += 1
                util += inside
    return util, used, short


@pytest.mark.parametrize("suite, scale, engine", [("complex", "100", "spark_gpu"), ("baseline", "30", "polars_gpu"), ("baseline", "1", "spark_gpu"), ("complex", "300", "polars_gpu")])
def test_cell_statistics_equal_a_recomputation_from_the_raw_files(tree, suite, scale, engine):
    root, stats = tree
    util, used, short = independent_cell(root, suite, scale, engine)
    cell = stats["secondary"]["gpu_telemetry"]["cells"][suite][f"sf{scale}"][engine]
    assert (cell["n_actions"], cell["n_actions_excluded_short"], cell["n_samples"]) == (used, short, len(util))
    assert cell["util_median_pct"] == pytest.approx(float(np.median(util))) and cell["util_p90_pct"] == pytest.approx(float(np.percentile(util, 90)))
    assert cell["idle_fraction"] == pytest.approx(sum(1 for u in util if u < 10) / len(util))
    assert 0.2 < cell["idle_fraction"] < 0.4                                # the synthetic GPU is busy for 70% of each action


def test_actions_under_one_second_are_left_out_of_every_statistic_and_counted(tree):
    root, stats = tree
    cells = stats["secondary"]["gpu_telemetry"]["cells"]
    sf1 = cells["baseline"]["sf1"]["polars_gpu"]
    assert sf1["n_actions"] == 0 and sf1["n_samples"] == 0 and sf1["n_actions_excluded_short"] == 36           # 6 queries x 3 trials x 2 rounds
    assert all(sf1[k] is None for k in ("util_median_pct", "util_p90_pct", "idle_fraction", "power_median_w", "sm_clock_median_mhz", "mem_util_median_pct"))
    assert sf1["seconds_excluded_short"] > 0 and sf1["idle_fraction_by_round"] == [None, None]
    for suite, by_scale in cells.items():
        for scale, by_engine in by_scale.items():
            for engine, cell in by_engine.items():
                total = sum(1 for rnd in (1, 2) for rec in json.loads((root / f"round-{rnd}" / suite / f"sf{scale[2:]}" / engine / "results.json").read_text())["records"] if rec["phase"] == "measured")
                assert cell["n_actions"] + cell["n_actions_excluded_short"] + cell["n_actions_without_samples"] == total


def test_per_round_and_per_query_detail_are_consistent_with_the_pooled_cell(tree):
    _, stats = tree
    cell = stats["secondary"]["gpu_telemetry"]["cells"]["complex"]["sf100"]["spark_gpu"]
    assert cell["rounds"] == [1, 2] and len(cell["idle_fraction_by_round"]) == 2 and all(0 <= f <= 1 for f in cell["idle_fraction_by_round"])
    assert sorted(cell["q"]) == sorted(stats["campaign"]["queries"]["complex"])
    assert sum(q["n_actions"] for q in cell["q"].values()) == cell["n_actions"] and set(cell["q"]["cx_q09"]) == {"n_actions", "util_median_pct", "util_p90_pct", "idle_fraction"}


def test_power_and_clock_are_reported_with_their_share_of_the_limit_and_the_maximum(tree):
    _, stats = tree
    gt = stats["secondary"]["gpu_telemetry"]
    assert gt["meta"]["power_limit_w"] == 700.0 and gt["meta"]["max_sm_clock_mhz"] == 1980.0 and gt["meta"]["sample_interval_ms"] == pytest.approx(500.0)
    cell = gt["cells"]["complex"]["sf100"]["polars_gpu"]
    assert cell["power_fraction_of_limit"] == pytest.approx(cell["power_median_w"] / 700.0) and 200 < cell["power_median_w"] < 260
    assert cell["sm_clock_median_mhz"] == 1700.0 and cell["sm_clock_fraction_of_max"] == pytest.approx(1700.0 / 1980.0)
    assert cell["mem_util_median_pct"] is not None


def test_the_label_the_definition_and_the_resolution_limit_are_stated(tree):
    _, stats = tree
    meta = stats["secondary"]["gpu_telemetry"]["meta"]
    assert meta["label"] == LABEL and meta["available"] is True and meta["idle_threshold_pct"] == 10.0 and meta["min_action_seconds"] == 1.0
    assert "below 10%" in meta["definition"] and "not SM occupancy" in meta["definition"] and "no causal claim" in meta["definition"]
    assert "shorter than 1 s" in meta["resolution_note"] and "n_actions_excluded_short" in meta["resolution_note"]


# ------------------------------------------------------------------ availability, separation, documentation

def test_without_gpu_csv_or_with_no_energy_the_section_is_unavailable(tmp_path, tree):
    root, _ = tree
    off = analyze(root, energy=False)["secondary"]["gpu_telemetry"]
    assert off["cells"] == {} and off["meta"]["available"] is False and off["meta"]["label"] == LABEL and off["meta"]["reason"] and off["meta"]["power_limit_w"] == 700.0
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out), "--no-energy"]) == 0
    assert "Not computed" in (out / "STATS_REPORT.md").read_text() and (out / "tables" / "t24_gpu_telemetry.csv").read_text().count("\n") == 1


def test_primary_outputs_do_not_depend_on_the_telemetry(tree):
    root, with_gpu = tree
    without = analyze(root, energy=False)
    for key in ("c1", "c2", "c3", "c4", "c4_strict", "medians", "fastest", "legacy", "variability", "order", "crossover", "coverage", "unstable_coverage", "output_agreement"):
        assert with_gpu[key] == without[key], key


def test_keys_are_documented_and_the_report_and_table_carry_the_label(tree, tmp_path):
    root, stats = tree
    keys = [k for k in AC.flatten_stats(stats) if k.startswith("secondary.gpu_telemetry")]
    assert len(keys) > 150 and all(AC.schema_matches(k) for k in keys), [k for k in keys if not AC.schema_matches(k)][:5]
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out)]) == 0
    report = (out / "STATS_REPORT.md").read_text()
    section = report[report.index("### GPU telemetry inside measured actions"):report.index("## 10. RQ5")]
    assert LABEL in section and "Actions used / left out (< 1 s)" in section and "GPU-idle fraction (util < 10%)" in section and "Power median W (fraction of limit)" in section
    header = (out / "tables" / "t24_gpu_telemetry.csv").read_text().splitlines()[0]
    assert "suite,scale,engine,n_actions,n_actions_excluded_short,n_samples,util_median_pct,util_p90_pct,idle_fraction" in header


# ------------------------------------------------------------------ cross-host and F12

@pytest.fixture(scope="module")
def cross(tmp_path_factory):
    base = tmp_path_factory.mktemp("tele-hosts")
    stats = {h: analyze(build(base, h, host=h, interval=1.0, seed=5)) for h in HOSTS}
    return json.loads(json.dumps(AC.compare_hosts(stats))), stats


def test_the_cross_host_file_carries_a_compact_telemetry_view_per_host(cross):
    c, stats = cross
    gt = c["gpu_telemetry"]
    assert set(gt) == set(HOSTS)
    for host in HOSTS:
        assert gt[host]["label"] == LABEL and gt[host]["available"] is True and gt[host]["power_limit_w"] == AC.SYNTH_HOSTS[host]["power_limit_w"]
        assert gt[host]["max_sm_clock_mhz"] == AC.SYNTH_HOSTS[host]["max_sm_mhz"]
        cell = gt[host]["cells"]["complex"]["sf100"]["spark_gpu"]
        full = stats[host]["secondary"]["gpu_telemetry"]["cells"]["complex"]["sf100"]["spark_gpu"]
        assert set(cell) == set(AC.TELEMETRY_COMPACT) and all(cell[k] == full[k] for k in cell) and "q" not in cell
    fractions = {h: gt[h]["cells"]["complex"]["sf100"]["polars_gpu"]["power_fraction_of_limit"] for h in HOSTS}
    assert fractions["A100"] > fractions["B200"]                          # the same watts against a 400 W and a 1000 W limit


def test_the_cross_host_table_report_and_keys(cross):
    c, _ = cross
    tables = {t.name: t for t in AC.build_cross_tables(c)}
    table = tables["x08_gpu_telemetry"]
    assert len(table.rows) == 16 and [h for h, _ in table.text_columns][:3] == ["Suite", "Scale", "Engine"]
    assert any("A100: GPU-idle / util median / power (of limit)" == h for h, _ in table.text_columns)
    text = dict(zip([h for h, _ in table.text_columns], table.text_rows()[table.rows.index(next(r for r in table.rows if (r["suite"], r["scale"], r["engine"]) == ("complex", "sf100", "spark_gpu")))]))
    assert "%" in text["H100: GPU-idle / util median / power (of limit)"] and " W (" in text["H100: GPU-idle / util median / power (of limit)"]
    report = AC.cross_report_text(c, list(tables.values()))
    assert "## 8. GPU telemetry inside measured actions (descriptive)" in report and LABEL in report and "## 9. Caveats" in report
    keys = [k for k in AC.flatten_stats(c, (), AC.CROSS_OPAQUE_PATTERNS) if k.startswith("gpu_telemetry")]
    assert len(keys) > 100 and all(AC.schema_matches(k, AC.CROSS_SCHEMA) for k in keys), [k for k in keys if not AC.schema_matches(k, AC.CROSS_SCHEMA)][:5]


def test_a_host_without_telemetry_is_marked_unavailable_in_the_cross_host_file(cross):
    c, stats = cross
    stats = {h: dict(v) for h, v in stats.items()}
    stats["A100"]["secondary"] = {k: v for k, v in stats["A100"]["secondary"].items() if k != "gpu_telemetry"}
    compact = json.loads(json.dumps(AC.compare_hosts(stats)))["gpu_telemetry"]["A100"]
    assert compact["available"] is False and compact["cells"] == {} and compact["label"] == LABEL


def test_f12_is_drawn_from_the_cross_host_file_alone_and_says_what_it_is(cross, tmp_path, monkeypatch):
    c, _ = cross
    shown = []
    original = PC.finish
    monkeypatch.setattr(PC, "finish", lambda fig, ctx, fig_id, title, caption, sources, *a, **k: (
        shown.append((fig_id, title, caption, sources, [t.get_text() for t in fig.texts], [ax.get_ylim() for ax in fig.axes], [len(ax.lines) for ax in fig.axes])),
        original(fig, ctx, fig_id, title, caption, sources, *a, **k))[1])
    ctx = PC.build_figures(None, tmp_path / "figs", only=["f12"], formats=("png",), cross=c)
    assert {s[0] for s in shown} == {"f12_gpu_idle_fraction", "f12_gpu_idle_fraction_1col"} and {m["id"] for m in ctx.manifest} == {s[0] for s in shown}
    for _, title, caption, sources, texts, ylims, nlines in shown:
        assert "GPU-idle fraction" in title and LABEL in caption and "not SM occupancy" in caption and "actions shorter than 1 s are left out" in caption
        assert any("gpu_telemetry" in s for s in sources) and any("shorter actions" in " ".join(t.split()) for t in texts)
        assert len(set(ylims)) == 1 and ylims[0][1] <= 60                  # one shared range sized to the data (idle fractions here are about 30%), not 100%
        assert sum(nlines) >= 8 * 4                                       # a line and its markers for every host in the cells that have a qualifying action
    assert not (PC.CROSS_ONLY is None) and "f12" in PC.CROSS_ONLY


def test_f12_is_skipped_with_a_reason_without_telemetry(cross, tmp_path):
    c, _ = cross
    bare = dict(c, gpu_telemetry={h: {"label": LABEL, "available": False, "cells": {}} for h in c["hosts"]})
    ctx = PC.build_figures(None, tmp_path / "figs", only=["f12"], formats=("png",), cross=bare)
    assert not ctx.manifest
    skipped = json.loads((tmp_path / "figs" / "figures.json").read_text())["skipped"]
    assert skipped and all(s["id"] == "f12" and "telemetry" in s["reason"] for s in skipped)


# ------------------------------------------------------------------ the copied H100 campaign

H100 = REPO / "rerun" / "hosts" / "h100" / "final" / "campaign-h100"


@pytest.mark.skipif(not (H100 / "round-4" / "gates.json").is_file(), reason="the final H100 campaign is not in this checkout")
def test_h100_final_spark_gpu_baseline_sf300_matches_a_recomputation_from_the_result_files():
    stats = AC.analyze(H100, energy=True, label="H100")
    gt = stats["secondary"]["gpu_telemetry"]
    assert gt["meta"]["power_limit_w"] == 700.0 and gt["meta"]["max_sm_clock_mhz"] == 1980.0 and 190 < gt["meta"]["sample_interval_ms"] < 215
    util, used, short = independent_cell(H100, "baseline", "300", "spark_gpu", rounds=(1, 2, 3, 4))
    cell = gt["cells"]["baseline"]["sf300"]["spark_gpu"]
    assert (cell["n_actions"], cell["n_actions_excluded_short"], cell["n_samples"]) == (used, short, len(util))
    assert cell["util_median_pct"] == pytest.approx(float(np.median(util))) and cell["idle_fraction"] == pytest.approx(sum(1 for u in util if u < 10) / len(util))
    assert gt["cells"]["baseline"]["sf1"]["polars_gpu"]["n_actions"] == 0                        # every SF1 action is under a second
