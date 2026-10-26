#!/usr/bin/env python3
"""Plot verified measurements. Requires matplotlib; benchmark itself does not."""
import argparse
import csv
import json
from pathlib import Path


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
        rows = list(csv.DictReader(handle))
    scales = sorted({row["scale"] for row in rows}, key=lambda s: float(s[2:].replace("p", ".")))
    engines = ["polars", "spark_cpu", "spark_gpu"]
    labels = ["Polars CPU (streaming)", "CPU Spark", "GPU Spark (A100)"]
    colors = ["#18708A", "#727B88", "#76B900"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.3))
    width = 0.24
    for index, (engine, label, color) in enumerate(zip(engines, labels, colors)):
        values = [sum(float(r[engine + "_median_seconds"]) for r in rows if r["scale"] == scale)
                  for scale in scales]
        bars = axes[0].bar(np.arange(len(scales)) + (index - 1) * width, values,
                           width, label=label, color=color)
        axes[0].bar_label(bars, fmt="%.2f", fontsize=9, padding=3)
    axes[0].set_xticks(np.arange(len(scales)), [s.upper() for s in scales])
    axes[0].set_ylabel("Sum of six query medians (seconds)")
    axes[0].set_title("Workload time across data scales", loc="left", fontweight="bold")
    axes[0].set_ylim(0, axes[0].get_ylim()[1] * 1.16)

    largest = [r for r in rows if r["scale"] == scales[-1]]
    for index, (engine, label, color) in enumerate(zip(engines, labels, colors)):
        medians = np.array([float(r[engine + "_median_seconds"]) for r in largest])
        lower = medians - [float(r[engine + "_min_seconds"]) for r in largest]
        upper = [float(r[engine + "_max_seconds"]) for r in largest] - medians
        axes[1].bar(np.arange(len(largest)) + (index - 1) * width, medians, width,
                    yerr=np.array([lower, upper]), capsize=2, label=label, color=color)
    axes[1].set_xticks(np.arange(len(largest)), [r["query"].upper() for r in largest])
    axes[1].set_ylabel("Median query time (seconds)")
    axes[1].set_title(f"{scales[-1].upper()}: per-query time and min–max range", loc="left", fontweight="bold")
    for axis in axes:
        axis.set_axisbelow(True)
        axis.grid(axis="y", alpha=0.18)
    fig.suptitle("Polars vs CPU Spark vs GPU Spark", x=0.055, y=0.99, ha="left", fontsize=20, fontweight="bold")
    fig.legend(labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, 0.055), frameon=False)
    fig.text(0.055, 0.01,
             "One A100 40 GB VM · 30 CPU threads · warm filesystem · "
             + "/".join(sorted({r["measured_trials"] for r in rows}))
             + " trials/query · Float64 · selected TPC-H-derived queries",
             fontsize=9, color="#4B5563")
    fig.tight_layout(rect=(0.02, 0.14, 1, 0.94), w_pad=3)
    for suffix in ("png", "svg"):
        fig.savefig(args.report / ("comparison." + suffix), dpi=180, facecolor="white")


if __name__ == "__main__":
    main()
