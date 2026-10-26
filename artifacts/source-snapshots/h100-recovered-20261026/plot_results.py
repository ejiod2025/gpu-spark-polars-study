#!/usr/bin/env python3
"""Plot verified measurements. Requires matplotlib; benchmark itself does not.

Engines are read from summary.csv, so a report with Polars GPU columns gets a
fourth bar per group. A Polars GPU query that failed, was invalid or mismatched
has no timing: it is drawn as a labelled FAILED marker, never as a zero-height bar.
"""
import argparse
import csv
import json
from pathlib import Path

from analyze_results import ENGINE_ORDER, plot_hardware

COLORS = {"polars": "#18708A", "polars_gpu": "#D9822B", "spark_cpu": "#727B88", "spark_gpu": "#76B900"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads((args.report / "correctness.json").read_text())
    if not evidence.get("correctness_verified") or not evidence.get("gpu_execution_verified"):
        raise RuntimeError("Only plot measurements after validation passes")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    with (args.report / "summary.csv").open() as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        header = reader.fieldnames or []
    scales = sorted({row["scale"] for row in rows}, key=lambda s: float(s[2:].replace("p", ".")))
    engines = [e for e in ENGINE_ORDER if e + "_median_seconds" in header]
    gpu, host_text = plot_hardware(evidence)
    names = {"polars": "Polars CPU (streaming)", "polars_gpu": "Polars GPU (cuDF, strict)",
             "spark_cpu": "CPU Spark", "spark_gpu": f"GPU Spark ({gpu})"}
    labels = [names[e] for e in engines]
    colors = [COLORS[e] for e in engines]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.3))
    width = 0.24 if len(engines) == 3 else 0.72 / len(engines)
    shift = (len(engines) - 1) / 2

    def timed(row, engine):
        return row[engine + "_median_seconds"] != ""

    def failed_marker(axis, x, text):
        axis.annotate(text, (x, 0), xytext=(0, 4), textcoords="offset points", rotation=90,
                      ha="center", va="bottom", fontsize=8, color="#B42318")

    for index, (engine, label, color) in enumerate(zip(engines, labels, colors)):
        values = []
        for scale in scales:
            group = [r for r in rows if r["scale"] == scale]
            values.append(sum(float(r[engine + "_median_seconds"]) for r in group) if all(timed(r, engine) for r in group) else None)
        positions = np.arange(len(scales)) + (index - shift) * width
        bars = axes[0].bar(positions, [v or 0 for v in values], width, label=label, color=color)
        axes[0].bar_label(bars, labels=[f"{v:.2f}" if v is not None else "" for v in values], fontsize=9, padding=3)
        for x, value in zip(positions, values):
            if value is None:
                failed_marker(axes[0], x, "no sum: query failed")
    axes[0].set_xticks(np.arange(len(scales)), [s.upper() for s in scales])
    axes[0].set_ylabel("Sum of six query medians (seconds)")
    axes[0].set_title("Workload time across data scales", loc="left", fontweight="bold")
    axes[0].set_ylim(0, axes[0].get_ylim()[1] * 1.16)

    largest = [r for r in rows if r["scale"] == scales[-1]]
    for index, (engine, label, color) in enumerate(zip(engines, labels, colors)):
        ok = [r for r in largest if timed(r, engine)]
        positions = np.array([i for i, r in enumerate(largest) if timed(r, engine)]) + (index - shift) * width
        medians = np.array([float(r[engine + "_median_seconds"]) for r in ok])
        lower = medians - [float(r[engine + "_min_seconds"]) for r in ok]
        upper = [float(r[engine + "_max_seconds"]) for r in ok] - medians
        axes[1].bar(positions, medians, width,
                    yerr=np.array([lower, upper]), capsize=2, label=label, color=color)
        for i, r in enumerate(largest):
            if not timed(r, engine):
                failed_marker(axes[1], i + (index - shift) * width, (r.get(engine + "_status") or "failed").upper())
    axes[1].set_xticks(np.arange(len(largest)), [r["query"].upper() for r in largest])
    axes[1].set_ylabel("Median query time (seconds)")
    axes[1].set_title(f"{scales[-1].upper()}: per-query time and min–max range", loc="left", fontweight="bold")
    for axis in axes:
        axis.set_axisbelow(True)
        axis.grid(axis="y", alpha=0.18)
    title = "Polars vs CPU Spark vs GPU Spark" if len(engines) == 3 else "Polars vs Polars GPU vs CPU Spark vs GPU Spark"
    fig.suptitle(title, x=0.055, y=0.99, ha="left", fontsize=20, fontweight="bold")
    fig.legend(labels, loc="lower center", ncol=len(engines), bbox_to_anchor=(0.5, 0.055), frameon=False)
    fig.text(0.055, 0.01,
             (f"One {gpu} 40 GB VM · 30 CPU threads" if host_text is None else host_text) + " · warm filesystem · "
             + "/".join(sorted({r["measured_trials"] for r in rows}))
             + " trials/query · Float64 · selected TPC-H-derived queries",
             fontsize=9, color="#4B5563")
    fig.tight_layout(rect=(0.02, 0.14, 1, 0.94), w_pad=3)
    for suffix in ("png", "svg"):
        fig.savefig(args.report / ("comparison." + suffix), dpi=180, facecolor="white")


if __name__ == "__main__":
    main()
