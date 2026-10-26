#!/usr/bin/env python3
"""Render verified research-analysis-v1 artifacts as standalone publication figures.

    python research_reporting/render.py --analysis /path/to/analysis --output /path/to/figures

Exports PNG, SVG and PDF by default, together with all 26 primary contrasts,
complete-case/coverage tables, captions and an input/output hash manifest. The
renderer never starts a benchmark, rewrites an estimate or invents a missing
time. It requires complete, validated ten-block analysis and cross-checks the
primary raw CSV against session scores. Hardware names are fixed study context,
not a new hardware inventory. Existing frozen analysis sources are not imported.

Synthetic fixtures require --allow-synthetic and receive a visible watermark.
They are rendering tests, never experimental result artifacts.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import textwrap

import matplotlib
matplotlib.use("Agg")
from matplotlib import colors
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter
import matplotlib.pyplot as plt
import numpy as np
import scipy
from scipy.stats import t

CONFIGS = ("polars_cpu_144", "polars_cpu_200", "polars_gpu", "spark_cpu", "spark_gpu")
BLOCKS = tuple(f"block-{i:02d}" for i in range(1, 11))
CELLS = tuple(("baseline", str(s)) for s in (1, 10, 30, 100, 300)) + tuple(
    ("complex", str(s)) for s in (30, 100, 300))
QUERIES = {"baseline": ("q01", "q03", "q06", "q10", "q12", "q14"),
           "complex": ("cx_q09", "cx_q13", "cx_semi_anti", "cx_window", "cx_distinct", "cx_self_join")}
QUERY_LABELS = {"q01": "Q1 · grouped scan", "q03": "Q3 · three-table join", "q06": "Q6 · filtered sum",
                "q10": "Q10 · four-table join", "q12": "Q12 · conditional counts", "q14": "Q14 · part join",
                "cx_q09": "Q9 · six-table join", "cx_q13": "Q13 · outer join",
                "cx_semi_anti": "Correlated semi/anti", "cx_window": "LAG / ROW_NUMBER",
                "cx_distinct": "Exact distinct", "cx_self_join": "Fact self-join"}
UNSUPPORTED = {"cx_q13", "cx_window"}
LABELS = {"polars_cpu_144": "CPU Polars 1.44.2", "polars_cpu_200": "CPU Polars 2.0.0",
          "polars_gpu": "Polars GPU 1.44.2", "spark_cpu": "CPU Spark 3.5.9", "spark_gpu": "GPU Spark 3.5.9"}
TABLE_LABELS = {"polars_cpu_144": "CPU Polars\n1.44.2", "polars_cpu_200": "CPU Polars\n2.0.0",
                "polars_gpu": "Polars GPU\n1.44.2 / cuDF 26.10.2", "spark_cpu": "CPU Spark\n3.5.9",
                "spark_gpu": "GPU Spark\n3.5.9 / RAPIDS 26.08.1"}
PAIRS = (("polars_cpu_200", "spark_gpu"), ("spark_cpu", "spark_gpu"),
         ("polars_cpu_144", "polars_gpu"), ("polars_cpu_200", "polars_gpu"))
HOST = "Single H100 study host · fixed configuration · four engine paths, five software configurations"
PRIMARY_CAPTION = (
    "Each small point is one of ten paired block ratios. Diamonds and bars show the geometric mean and "
    "pointwise 95% Student-t interval on block log ratios; intervals are not simultaneous. The 26 prespecified "
    "suite tests form one Holm family; adjusted p values are in primary_contrasts.csv. Ratio >1 favors the GPU. "
    "Each session score sums six query medians from three inner actions; this is not an observed suite latency. "
    "Action time excludes startup. Blocks, rather than inner actions, are the inference units. "
    "Independent, approximately normal block log ratios are assumed; ten blocks do not guarantee power.")
QUERY_CAPTION = (
    "Cells show geometric means of ten paired block query ratios for CPU Polars 2.0.0 / GPU, using the action "
    "endpoint. Color encodes log2(ratio), centered symmetrically on 1×. These query comparisons are descriptive; "
    "no query significance tests or multiplicity-adjusted query claims are made. Gray N/A cells retain the original "
    "strict GPU regex and grouped-window rejections. They are not a ratio of 1 and receive no imputed latency. "
    "The original correlated query does not establish an executed nonempty anti join.")
TABLE_CAPTION = (
    "Values are descriptive geometric means of ten complete session scores in seconds. Each score sums six "
    "query medians, not an observed whole-suite latency. Every cell reports planned-block completeness and "
    "query coverage. Polars GPU complex retains four of six supported original queries and has zero complete "
    "six-query scores; a partial four-query sum is never substituted. Versions are the fixed study configuration; "
    "the renderer does not independently inventory hardware or runtimes.")
STYLE = {"font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 11,
         "axes.labelsize": 10, "axes.spines.top": False, "axes.spines.right": False,
         "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
         "svg.hashsalt": "gpu-spark-polars-study-research-reporting-v1", "svg.fonttype": "none",
         "pdf.fonttype": 42, "ps.fonttype": 42}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError(f"Nonfinite JSON constant: {value}")

    return json.loads(Path(path).read_text(), object_pairs_hook=pairs, parse_constant=nonfinite)


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def close(a, b, name):
    if not math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError(f"Inconsistent {name}: {a} versus {b}")


def expected_primary():
    entries = []
    for numerator, denominator in PAIRS:
        for suite, scale in CELLS:
            if denominator == "polars_gpu" and suite == "complex":
                continue
            entries.append({"contrast_id": f"{numerator}_over_{denominator}-{suite}-sf{scale}",
                            "numerator": numerator, "denominator": denominator, "suite": suite, "scale": scale})
    return entries


def ratio_statistics(paired_times):
    # Match the frozen analysis's arithmetic, including near-zero variance cases.
    logs = [math.log(positive(a, "numerator score")) - math.log(positive(b, "denominator score"))
            for a, b in paired_times]
    mean = math.fsum(logs) / len(logs)
    sd = statistics.stdev(logs)
    se = sd / math.sqrt(len(logs))
    margin = float(t.ppf(.975, len(logs) - 1)) * se
    p = (1.0 if mean == 0 else 0.0) if sd == 0 else float(2 * t.sf(abs(mean / se), len(logs) - 1))
    return math.exp(mean), [math.exp(mean - margin), math.exp(mean + margin)], p


def check_estimate(entry, paired_times, primary=False):
    if entry.get("status") != "complete" or entry.get("n_blocks") != 10 or entry.get("missing_blocks"):
        raise ValueError("Every plotted estimate requires all ten complete blocks")
    ci = entry.get("ci95_pointwise")
    estimate = positive(entry.get("geometric_mean_speedup"), "estimate")
    if not isinstance(ci, list) or len(ci) != 2 or not positive(ci[0], "CI lower") <= estimate <= positive(ci[1], "CI upper"):
        raise ValueError("Invalid or reversed confidence interval")
    actual, interval, pvalue = ratio_statistics(paired_times)
    close(estimate, actual, "geometric mean")
    for given, computed in zip(ci, interval):
        close(given, computed, "pointwise interval")
    if primary:
        close(entry["p_raw"], pvalue, "raw p value")


def load_analysis(directory, allow_synthetic=False):
    directory = Path(directory)
    path, csv_path = directory / "analysis.json", directory / "block_ratios.csv"
    report = read_json(path)
    synthetic = report.get("synthetic") is True
    if synthetic and (not allow_synthetic or "SYNTHETIC" not in report.get("campaign_id", "")):
        raise ValueError("Synthetic input needs --allow-synthetic and an explicit SYNTHETIC campaign label")
    if "SYNTHETIC" in report.get("campaign_id", "") and not synthetic:
        raise ValueError("SYNTHETIC campaign must carry synthetic=true")
    if (report.get("schema_version") != "research-analysis-v1" or report.get("validation_status") != "PASS"
            or report.get("primary_family_size") != 26 or report.get("primary_family_status") != "complete"
            or report.get("primary_endpoint") != "seconds"):
        raise ValueError("A complete PASS research-analysis-v1 action analysis is required")
    sessions = {}
    session_ids = set()
    for session in report.get("sessions", []):
        key = tuple(session[k] for k in ("block_id", "suite", "scale", "config_id"))
        if key in sessions or session["session_id"] in session_ids:
            raise ValueError("Duplicate session identity/cell")
        session_ids.add(session["session_id"])
        sessions[key] = session
        suite, config = session["suite"], session["config_id"]
        if suite not in QUERIES or set(session.get("queries", {})) != set(QUERIES[suite]):
            raise ValueError("Session query set differs from the unchanged study suite")
        unsupported = UNSUPPORTED if suite == "complex" and config == "polars_gpu" else set()
        for query, summary in session["queries"].items():
            if query in unsupported:
                if summary.get("status") != "unsupported" or summary.get("endpoints"):
                    raise ValueError("Original GPU rejection is absent or has an imputed time")
                continue
            if summary.get("status") != "complete":
                raise ValueError("A supported query is incomplete")
            for endpoint in ("seconds", "host_result_seconds"):
                observed = summary["endpoints"][endpoint]
                trials = observed.get("trials", [])
                if len(trials) != 3:
                    raise ValueError("Session query must contain three nested actions")
                for value in trials:
                    positive(value, "inner action time")
                close(observed["median"], statistics.median(trials), "session query median")
                close(observed["min"], min(trials), "session query minimum")
                close(observed["max"], max(trials), "session query maximum")
        if unsupported:
            if (session.get("coverage") != "4/6" or session.get("suite_status") != "unsupported"
                    or any(session["suite_scores"].get(endpoint) is not None for endpoint in ("seconds", "host_result_seconds"))):
                raise ValueError("GPU complex must have 4/6 coverage and no complete-suite score")
        else:
            if session.get("coverage") != "6/6" or session.get("suite_status") != "complete":
                raise ValueError("Complete suite must retain 6/6 coverage")
            for endpoint in ("seconds", "host_result_seconds"):
                actual = math.fsum(q["endpoints"][endpoint]["median"] for q in session["queries"].values())
                close(positive(session["suite_scores"][endpoint], "suite score"), actual, "suite score")
    expected_sessions = {(block, suite, scale, config) for block in BLOCKS for suite, scale in CELLS for config in CONFIGS}
    if set(sessions) != expected_sessions or len(session_ids) != 400:
        raise ValueError("All 400 unique sessions and ten blocks are required")
    contrasts = {}
    expected = {c["contrast_id"]: c for c in expected_primary()}
    raw = {}
    for entry in report.get("primary", []):
        identity = entry["contrast_id"]
        if identity in contrasts or identity not in expected:
            raise ValueError("Duplicate or unknown primary contrast")
        if any(entry.get(key) != value for key, value in expected[identity].items()) or entry.get("endpoint") != "seconds":
            raise ValueError("Primary contrast identity differs")
        contrasts[identity] = entry
        paired_times = []
        for block in BLOCKS:
            a = sessions[(block, entry["suite"], entry["scale"], entry["numerator"])]["suite_scores"]["seconds"]
            b = sessions[(block, entry["suite"], entry["scale"], entry["denominator"])]["suite_scores"]["seconds"]
            raw[(identity, block)] = {"numerator_seconds": a, "denominator_seconds": b, "ratio": a / b,
                                      "log_ratio": math.log(a) - math.log(b)}
            paired_times.append((a, b))
        check_estimate(entry, paired_times, primary=True)
    if set(contrasts) != set(expected):
        raise ValueError("All 26 primary contrasts are required")
    running = 0.0
    for rank, entry in enumerate(sorted(contrasts.values(), key=lambda e: e["p_raw"])):
        running = max(running, min(1.0, (26 - rank) * entry["p_raw"]))
        close(entry["p_holm"], running, "26-test Holm adjustment")
    observed_raw = set()
    with csv_path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["family"] != "primary_suite":
                continue
            key = (row["contrast_id"], row["block_id"])
            if key in observed_raw or key not in raw or row["endpoint"] != "seconds" or row.get("query"):
                raise ValueError("Duplicate/unknown primary CSV observation")
            entry = contrasts[key[0]]
            for field in ("suite", "scale", "numerator", "denominator"):
                if row[field] != entry[field]:
                    raise ValueError("Primary CSV identity differs")
            for side in ("numerator", "denominator"):
                session = sessions[(key[1], entry["suite"], entry["scale"], entry[side])]
                if row[side + "_session_id"] != session["session_id"] or row[side + "_status"] != "complete":
                    raise ValueError("Primary CSV session binding differs")
            for field, value in raw[key].items():
                observed = float(row[field])
                if field != "log_ratio":
                    positive(observed, field)
                close(observed, value, "primary CSV " + field)
            observed_raw.add(key)
    if observed_raw != set(raw):
        raise ValueError("Primary CSV must retain exactly 260 paired observations")
    exploratory = {}
    for entry in report.get("exploratory_queries", []):
        key = (entry["numerator"], entry["denominator"], entry["suite"], entry["scale"], entry["query"], entry["endpoint"])
        if key in exploratory:
            raise ValueError("Duplicate exploratory query contrast")
        if (entry["denominator"] == "polars_gpu" and entry["suite"] == "complex" and entry["query"] in UNSUPPORTED
                and (entry.get("geometric_mean_speedup") is not None or entry.get("ci95_pointwise") is not None
                     or entry.get("status") != "incomplete")):
            raise ValueError("Unsupported query has an imputed estimate")
        exploratory[key] = entry
    # Validate every cell that will be drawn; no values are interpolated or replaced.
    for denominator in ("spark_gpu", "polars_gpu"):
        for suite, scale in CELLS:
            for query in QUERIES[suite]:
                entry = exploratory[("polars_cpu_200", denominator, suite, scale, query, "seconds")]
                if denominator == "polars_gpu" and suite == "complex" and query in UNSUPPORTED:
                    if entry.get("geometric_mean_speedup") is not None or entry.get("ci95_pointwise") is not None or entry.get("status") != "incomplete":
                        raise ValueError("Unsupported query heatmap cell has an imputed estimate")
                else:
                    paired_times = [(sessions[(b, suite, scale, "polars_cpu_200")]["queries"][query]["endpoints"]["seconds"]["median"],
                                     sessions[(b, suite, scale, denominator)]["queries"][query]["endpoints"]["seconds"]["median"]) for b in BLOCKS]
                    check_estimate(entry, paired_times)
    return {"report": report, "sessions": sessions, "contrasts": contrasts, "raw": raw,
            "exploratory": exploratory, "synthetic": synthetic,
            "inputs": {"analysis.json": sha256(path), "block_ratios.csv": sha256(csv_path)}}


def footer(fig, data, caption):
    provenance = f"Campaign: {data['report']['campaign_id']} · analysis SHA256 {data['inputs']['analysis.json'][:16]}"
    if data["synthetic"]:
        fig.text(.5, .985, "SYNTHETIC DATA — rendering test, not study results", ha="center", va="top",
                 fontsize=12, weight="bold", color="#9b1c20")
    fig.text(.055, .16, textwrap.fill(caption, width=177), fontsize=8, va="top", color="#363d46", linespacing=1.35)
    fig.text(.055, .017, provenance, fontsize=7, color="#58616c")


def ratio_axis(ax, data, numerator, denominator, suite, scales):
    color = "#176b78" if denominator == "spark_gpu" else "#b66013"
    extents = [1.0]
    offsets = np.linspace(-.15, .15, 10)
    for x, scale in enumerate(scales):
        identity = f"{numerator}_over_{denominator}-{suite}-sf{scale}"
        entry = data["contrasts"][identity]
        values = [data["raw"][(identity, block)]["ratio"] for block in BLOCKS]
        mean, (lo, hi) = entry["geometric_mean_speedup"], entry["ci95_pointwise"]
        ax.scatter(x + offsets, values, color="#8a929c", s=18, alpha=.65, linewidths=0, zorder=2)
        ax.errorbar(x, mean, yerr=[[mean - lo], [hi - mean]], fmt="D", color=color,
                    markersize=6, capsize=4, elinewidth=1.7, zorder=4)
        extents += values + [lo, hi]
    ax.axhline(1, color="#4f5964", linestyle="--", linewidth=1, zorder=1)
    ax.set_yscale("log", base=2)
    ax.set_ylim(min(extents) / 1.25, max(extents) * 1.3)
    ax.yaxis.set_major_locator(LogLocator(base=2, numticks=8))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:g}×"))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.set_xlim(-.5, len(scales) - .5)
    ax.set_xticks(range(len(scales)), [f"SF{s}" for s in scales])
    ax.set_ylabel("Reference / GPU score · log scale")
    ax.grid(axis="y", color="#e5e8ed", linewidth=.7)
    ax.set_axisbelow(True)
    ax.set_title(f"{LABELS[numerator]} / {LABELS[denominator]}", loc="left", pad=10, weight="bold")


def suite_figures(data):
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 10))
    fig.subplots_adjust(left=.075, right=.975, top=.84, bottom=.22, wspace=.27, hspace=.38)
    fig.suptitle("Baseline suite · measured scale points", x=.055, y=.946, ha="left", fontsize=19, weight="bold")
    fig.text(.055, .905, HOST, fontsize=10, color="#4f5964")
    pairs = (("polars_cpu_200", "spark_gpu"), ("polars_cpu_200", "polars_gpu"),
             ("spark_cpu", "spark_gpu"), ("polars_cpu_144", "polars_gpu"))
    for axis, (numerator, denominator) in zip(axes.flat, pairs):
        ratio_axis(axis, data, numerator, denominator, "baseline", ("1", "10", "30", "100", "300"))
    footer(fig, data, PRIMARY_CAPTION)
    yield "suite_ratios_baseline", fig, PRIMARY_CAPTION
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 6.7), gridspec_kw={"width_ratios": [1, 1, .75]})
    fig.subplots_adjust(left=.075, right=.975, top=.77, bottom=.29, wspace=.35)
    fig.suptitle("Complex suite · complete comparisons and coverage", x=.055, y=.935, ha="left", fontsize=18, weight="bold")
    fig.text(.055, .875, HOST, fontsize=10, color="#4f5964")
    for axis, numerator in zip(axes[:2], ("polars_cpu_200", "spark_cpu")):
        ratio_axis(axis, data, numerator, "spark_gpu", "complex", ("30", "100", "300"))
    axes[2].set_axis_off()
    axes[2].text(.05, .92, "Polars GPU complex\ncomplete suite: N/A", transform=axes[2].transAxes,
                 va="top", fontsize=14, weight="bold", color="#565f6a")
    axes[2].text(.05, .64, "4 of 6 original queries supported\nat each measured scale.\n\nQ13 regex: unsupported\nGrouped window: unsupported\n\nNo six-query score;\nno partial-sum comparison.",
                 transform=axes[2].transAxes, va="top", fontsize=10, color="#565f6a", linespacing=1.5)
    footer(fig, data, PRIMARY_CAPTION)
    yield "suite_ratios_complex", fig, PRIMARY_CAPTION


def query_heatmap(data):
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 10.2), gridspec_kw={"width_ratios": [5, 3]})
    fig.subplots_adjust(left=.185, right=.88, top=.83, bottom=.23, wspace=.65, hspace=.42)
    fig.suptitle("Query ratios · descriptive action endpoint", x=.055, y=.945, ha="left", fontsize=19, weight="bold")
    fig.text(.055, .902, "CPU Polars 2.0.0 / GPU · " + HOST, fontsize=10, color="#4f5964")
    values = []
    matrices = []
    for denominator in ("spark_gpu", "polars_gpu"):
        for suite in ("baseline", "complex"):
            scales = [scale for cell_suite, scale in CELLS if cell_suite == suite]
            matrix = np.full((6, len(scales)), np.nan)
            for i, query in enumerate(QUERIES[suite]):
                for j, scale in enumerate(scales):
                    entry = data["exploratory"][("polars_cpu_200", denominator, suite, scale, query, "seconds")]
                    ratio = entry.get("geometric_mean_speedup")
                    if ratio is not None:
                        matrix[i, j] = math.log2(ratio)
                        values.append(abs(math.log2(ratio)))
            matrices.append((denominator, suite, scales, matrix))
    bound = max(1., math.ceil(max(values)))
    norm = colors.TwoSlopeNorm(vmin=-bound, vcenter=0, vmax=bound)
    cmap = plt.get_cmap("RdBu").copy()
    cmap.set_bad("#e1e4e8")
    image = None
    for axis, (denominator, suite, scales, matrix) in zip(axes.flat, matrices):
        image = axis.imshow(np.ma.masked_invalid(matrix), cmap=cmap, norm=norm, aspect="auto")
        axis.set_title(f"{LABELS[denominator]} · {suite}", loc="left", pad=10, weight="bold")
        axis.set_xticks(range(len(scales)), [f"SF{s}" for s in scales])
        axis.set_yticks(range(6), [QUERY_LABELS[q] for q in QUERIES[suite]], fontsize=9)
        axis.tick_params(length=0)
        for i in range(6):
            for j in range(len(scales)):
                log_value = matrix[i, j]
                label = "N/A" if np.isnan(log_value) else f"{2 ** log_value:.2f}×"
                color = "#4d5660" if np.isnan(log_value) else ("white" if abs(log_value) > bound * .56 else "#202832")
                axis.text(j, i, label, ha="center", va="center", fontsize=9, color=color)
        axis.set_xticks(np.arange(-.5, len(scales), 1), minor=True)
        axis.set_yticks(np.arange(-.5, 6, 1), minor=True)
        axis.grid(which="minor", color="white", linewidth=1)
        axis.tick_params(which="minor", length=0)
    bar_axis = fig.add_axes([.925, .29, .015, .48])
    bar = fig.colorbar(image, cax=bar_axis)
    ticks = np.linspace(-bound, bound, 5)
    bar.set_ticks(ticks, labels=[f"{2 ** value:g}×" for value in ticks])
    bar.set_label("CPU Polars 2.0.0 / GPU · logarithmic colors", labelpad=10)
    footer(fig, data, QUERY_CAPTION)
    return fig


def complete_case_rows(data):
    rows = []
    for suite, scale in CELLS:
        for config in CONFIGS:
            sessions = [data["sessions"][(block, suite, scale, config)] for block in BLOCKS]
            complete = [session["suite_scores"]["seconds"] for session in sessions if session["suite_status"] == "complete"]
            rows.append({"suite": suite, "scale": scale, "config_id": config, "configuration": LABELS[config],
                         "planned_blocks": 10, "complete_blocks": len(complete), "query_coverage": sessions[0]["coverage"],
                         "geometric_mean_score_seconds": math.exp(math.fsum(math.log(v) for v in complete) / 10) if len(complete) == 10 else None})
    return rows


def complete_case_figure(data, rows):
    indexed = {(r["suite"], r["scale"], r["config_id"]): r for r in rows}
    table_rows = []
    for suite, scale in CELLS:
        row = [f"{suite.capitalize()}\nSF{scale}"]
        for config in CONFIGS:
            value = indexed[(suite, scale, config)]
            score = value["geometric_mean_score_seconds"]
            row.append(f"{score:.3f} s\n10/10 blocks · 6/6 queries" if score is not None else "N/A\n0/10 complete · 4/6 queries")
        table_rows.append(row)
    fig, axis = plt.subplots(figsize=(14.5, 8.2))
    fig.subplots_adjust(left=.055, right=.98, top=.79, bottom=.24)
    axis.set_axis_off()
    fig.suptitle("Complete-case scores and original-query coverage", x=.055, y=.93, ha="left", fontsize=19, weight="bold")
    fig.text(.055, .875, HOST + " · primary action endpoint", fontsize=10, color="#4f5964")
    table = axis.table(cellText=table_rows, colLabels=["Condition"] + [TABLE_LABELS[c] for c in CONFIGS],
                       colWidths=[.115] + [.177] * 5,
                       cellLoc="center", loc="center", bbox=[0, 0, 1, 1])
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    for (row, column), cell in table.get_celld().items():
        cell.set_edgecolor("#dce1e6")
        cell.set_linewidth(.6)
        if row == 0:
            cell.set_facecolor("#e9eef3")
            cell.set_text_props(weight="bold", fontsize=9)
        elif column == 0:
            cell.set_facecolor("#f1f4f7")
            cell.set_text_props(weight="bold")
        elif row > 5 and column == 3:
            cell.set_facecolor("#e1e4e8")
            cell.set_text_props(color="#59636f")
        else:
            cell.set_facecolor("white" if row % 2 else "#f8fafc")
    footer(fig, data, TABLE_CAPTION)
    return fig


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def export_figure(fig, output, stem, formats, caption):
    for extension in formats:
        metadata = ({"Creator": "gpu-spark-polars-study reporting", "CreationDate": None, "ModDate": None,
                     "Title": stem, "Subject": caption} if extension == "pdf" else
                    {"Date": None, "Creator": "gpu-spark-polars-study reporting", "Title": stem,
                     "Description": caption} if extension == "svg" else
                    {"Software": "gpu-spark-polars-study reporting", "Title": stem, "Description": caption})
        fig.savefig(output / f"{stem}.{extension}", format=extension, dpi=300, bbox_inches="tight", pad_inches=.15,
                    metadata=metadata)
    plt.close(fig)


def render(analysis, output, formats=("png", "svg", "pdf"), allow_synthetic=False):
    output = Path(output)
    if output.exists():
        raise ValueError("Refusing to overwrite a reporting directory")
    if not formats or len(set(formats)) != len(formats) or set(formats) - {"png", "svg", "pdf"}:
        raise ValueError("Formats must be unique choices from png, svg, pdf")
    data = load_analysis(analysis, allow_synthetic)
    output.mkdir(parents=True)
    captions = {}
    with plt.rc_context(STYLE):
        for stem, fig, caption in suite_figures(data):
            export_figure(fig, output, stem, formats, caption)
            captions[stem] = caption
        export_figure(query_heatmap(data), output, "query_ratios_action", formats, QUERY_CAPTION)
        captions["query_ratios_action"] = QUERY_CAPTION
        coverage = complete_case_rows(data)
        export_figure(complete_case_figure(data, coverage), output, "complete_case_scores", formats, TABLE_CAPTION)
        captions["complete_case_scores"] = TABLE_CAPTION
    primary_rows = []
    for identity in expected_primary():
        entry = data["contrasts"][identity["contrast_id"]]
        primary_rows.append({**identity, "ratio": entry["geometric_mean_speedup"],
                             "ci95_pointwise_lower": entry["ci95_pointwise"][0], "ci95_pointwise_upper": entry["ci95_pointwise"][1],
                             "n_paired_blocks": 10, "p_raw": entry["p_raw"], "p_holm_family26": entry["p_holm"],
                             "reference_query_coverage": "6/6", "gpu_query_coverage": "6/6"})
    write_csv(output / "primary_contrasts.csv", primary_rows)
    write_csv(output / "complete_case_scores.csv", coverage)
    caption_text = "# Figure captions\n\n" + ("**SYNTHETIC DATA — rendering test, not study results.**\n\n" if data["synthetic"] else "")
    caption_text += "\n\n".join(f"## {stem}\n\n{caption}" for stem, caption in captions.items()) + "\n"
    (output / "CAPTIONS.md").write_text(caption_text)
    manifest = {"schema_version": "research-reporting-v1", "campaign_id": data["report"]["campaign_id"],
                "campaign_sha256": data["report"].get("campaign_sha256"), "synthetic": data["synthetic"],
                "input_files_sha256": data["inputs"], "renderer_sha256": sha256(Path(__file__)),
                "packages": {"matplotlib": matplotlib.__version__, "numpy": np.__version__, "scipy": scipy.__version__},
                "python": platform.python_version(), "hardware_label_provenance": "fixed study context; not independently inventoried by renderer",
                "ci_scope": "pointwise 95%; not simultaneous", "primary_test_family_size": 26,
                "query_comparisons": "descriptive; no significance tests", "formats": list(formats), "png_dpi": 300,
                "output_files_sha256": {p.name: sha256(p) for p in sorted(output.iterdir())},
                "zero_observed_log_variance_contrasts": [e["contrast_id"] for e in data["report"]["primary"]
                                                        if e.get("zero_observed_log_variance")]}
    (output / "RENDER_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", type=Path, required=True, help="directory containing analysis.json and block_ratios.csv")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--formats", nargs="+", choices=("png", "svg", "pdf"), default=["png", "svg", "pdf"])
    parser.add_argument("--allow-synthetic", action="store_true", help="only for visibly watermarked rendering tests")
    args = parser.parse_args(argv)
    try:
        manifest = render(args.analysis, args.output, tuple(args.formats), args.allow_synthetic)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Rendering refused: {type(exc).__name__}: {exc}\n")
    print(json.dumps({"campaign_id": manifest["campaign_id"], "synthetic": manifest["synthetic"],
                      "exports": len(manifest["output_files_sha256"]), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
