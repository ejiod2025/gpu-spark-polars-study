#!/usr/bin/env python3
"""Plot complete, validated crossover observations from analyze_crossover.py.

Only the Python standard library and Matplotlib are required. Run the analyzer
first, then pass its output directory with --report. Saves crossover.png/.svg.
Lines connect observed points; no fitted or extrapolated threshold is shown.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path


ENGINES = ("polars", "spark_cpu", "spark_gpu")
QUERIES = ("q01", "q03", "q06", "q10", "q12", "q14")
JOIN_QUERIES = {"q03", "q10", "q12", "q14"}


def scale_number(value: str) -> Decimal:
    try:
        number = Decimal(value.removeprefix("sf").replace("p", "."))
    except (InvalidOperation, AttributeError) as error:
        raise ValueError(f"Invalid scale factor: {value!r}") from error
    if not number.is_finite() or number <= 0:
        raise ValueError(f"Scale factor must be positive and finite: {value!r}")
    return number


def positive_number(value, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {label}: {value!r}") from error
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return result


def validate_report(report: Path) -> tuple[list[str], dict, dict, dict]:
    """Fail closed on absent proof, partial scales, or inconsistent summary rows."""
    evidence = json.loads((report / "correctness.json").read_text())
    if any(evidence.get(field) is not True for field in
           ("passed", "correctness_verified", "gpu_execution_verified")):
        raise ValueError("Plotting requires passed correctness and GPU-execution verification")
    included = [item["scale"] for item in evidence.get("included_scales", [])]
    numeric = [scale_number(scale) for scale in included]
    if not included or len(set(included)) != len(included) or len(set(numeric)) != len(numeric):
        raise ValueError("Included scales must be nonempty, unique positive scale factors")
    scales = sorted(included, key=scale_number)
    excluded = {scale_number(item["scale"]) for item in evidence.get("excluded_scales", [])}
    if set(numeric) & excluded:
        raise ValueError("A plotted scale is also listed as excluded")
    with (report / "summary.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if {row.get("scale") for row in rows} != set(scales):
        raise ValueError("CSV scales do not match the independently verified included scales")
    values, trials = {}, {}
    for row in rows:
        key = (row["scale"], row["query"])
        if key in values or row["query"] not in QUERIES:
            raise ValueError(f"Duplicate or unexpected query row: {key}")
        try:
            count = int(row["measured_trials"])
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid trial count for {key}") from error
        if count < 1 or (row["scale"] in trials and trials[row["scale"]] != count):
            raise ValueError(f"Nonpositive or inconsistent trial count for {key}")
        trials[row["scale"]] = count
        values[key] = {}
        for engine in ENGINES:
            low, median, high = [positive_number(row[engine + suffix], f"{key} {engine}{suffix}")
                                 for suffix in ("_min_seconds", "_median_seconds", "_max_seconds")]
            if not low <= median <= high:
                raise ValueError(f"Median is outside the trial range for {key} {engine}")
            values[key][engine] = median
        for column, numerator in (("polars_over_spark_gpu", "polars"),
                                  ("spark_cpu_over_spark_gpu", "spark_cpu")):
            ratio = values[key][numerator] / values[key]["spark_gpu"]
            if not math.isclose(positive_number(row[column], column), ratio, rel_tol=1e-10):
                raise ValueError(f"Inconsistent summary ratio for {key}: {column}")
    expected_rows = {(scale, query) for scale in scales for query in QUERIES}
    if set(values) != expected_rows:
        raise ValueError("Each plotted scale requires exactly all six query rows")

    expected_comparisons = {(scale, engine, query, phase, trial)
        for scale in scales for engine in ENGINES for query in QUERIES
        for phase, trial in [("warmup", 0), *[("measured", n) for n in range(1, trials[scale] + 1)]]}
    observed_comparisons = set()
    for item in evidence.get("comparisons", []):
        key = tuple(item.get(field) for field in ("scale", "engine", "query", "phase", "trial"))
        if (key in observed_comparisons or key not in expected_comparisons
                or item.get("passed") is not True or item.get("mismatch_count") != 0
                or item.get("expected_row_count") != item.get("actual_row_count")):
            raise ValueError(f"Invalid correctness evidence for {key}")
        observed_comparisons.add(key)
    if observed_comparisons != expected_comparisons:
        raise ValueError("Correctness evidence does not cover every plotted engine/query/trial")
    expected_plans = {(scale, query, phase, trial)
                      for scale, engine, query, phase, trial in expected_comparisons if engine == "spark_gpu"}
    observed_plans = set()
    for item in evidence.get("gpu_plans", []):
        key = tuple(item.get(field) for field in ("scale", "query", "phase", "trial"))
        if (key in observed_plans or key not in expected_plans
                or item.get("worker_gpu_plan_observed") is not True
                or not item.get("gpu_scans") or not item.get("gpu_aggregates")
                or (item.get("query") in JOIN_QUERIES and not item.get("gpu_joins"))
                or item.get("cpu_scan_aggregate_join_operators")):
            raise ValueError(f"Invalid GPU-plan evidence for {key}")
        observed_plans.add(key)
    if observed_plans != expected_plans:
        raise ValueError("GPU-plan evidence does not cover every plotted GPU query/trial")
    return scales, values, trials, evidence


def plot(report: Path) -> tuple[Path, Path]:
    scales, values, trials, evidence = validate_report(report)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter, NullLocator

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "svg.fonttype": "none"})
    x = [float(scale_number(scale)) for scale in scales]
    totals = {engine: [sum(values[(scale, query)][engine] for query in QUERIES)
                       for scale in scales] for engine in ENGINES}
    overall = [polars / gpu for polars, gpu in zip(totals["polars"], totals["spark_gpu"])]
    ratios = {query: [values[(scale, query)]["polars"] / values[(scale, query)]["spark_gpu"]
                     for scale in scales] for query in QUERIES}
    fig, (times, ratio) = plt.subplots(1, 2, figsize=(14.0, 7.2))
    fig.subplots_adjust(left=0.075, right=0.975, top=0.77, bottom=0.25, wspace=0.26)
    fig.suptitle("GPU Spark crossover: measured observations", x=0.075, y=0.965,
                 ha="left", fontsize=21, fontweight="bold")
    winning = [f"{float(scale_number(scale)):g}" for scale, value in zip(scales, overall) if value > 1]
    if winning:
        subtitle = "GPU Spark has the lower six-query aggregate at observed SF " + ", ".join(winning) + "."
    else:
        subtitle = f"No overall GPU Spark win observed through SF {float(scale_number(scales[-1])):g}."
    fig.text(0.075, 0.905, subtitle, fontsize=12, color="#374151")

    for engine, label, color, marker in (
            ("polars", "Polars CPU (streaming)", "#18708A", "o"),
            ("spark_cpu", "CPU Spark", "#6B7280", "s"),
            ("spark_gpu", "GPU Spark (A100)", "#619700", "^")):
        times.plot(x, totals[engine], marker=marker, markersize=6, linewidth=2.0,
                   color=color, label=label)
    times.set_yscale("log")
    times.set_ylabel("Sum of six query medians (seconds; log scale)")
    times.set_title("A  Overall workload time", loc="left", fontsize=12, fontweight="bold", pad=17)
    times.legend(loc="upper left", frameon=False, fontsize=9)
    times.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    times.yaxis.set_minor_formatter(NullFormatter())

    colors = ("#2878B5", "#D47316", "#169774", "#AE5AA5", "#B05B51", "#77852C")
    markers = ("o", "s", "^", "D", "v", "P")
    for query, color, marker in zip(QUERIES, colors, markers):
        ratio.plot(x, ratios[query], color=color, marker=marker, markersize=4.5,
                   linewidth=1.25, alpha=0.85, label=query.upper(), zorder=3)
    ratio.plot(x, overall, color="#111827", marker="o", markersize=7,
               linewidth=3, label="Overall (sum of medians)", zorder=5)
    ratio.set_yscale("log")
    all_ratios = [item for series in ratios.values() for item in series] + overall
    low, high = min(all_ratios + [1]) / 1.6, max(all_ratios + [1]) * 1.7
    ratio.set_ylim(low, high)
    ratio.axhspan(low, 1, color="#18708A", alpha=0.055, zorder=0)
    ratio.axhspan(1, high, color="#619700", alpha=0.075, zorder=0)
    ratio.axhline(1, color="#374151", linestyle=(0, (5, 3)), linewidth=1.25, zorder=2)
    ratio.text(0.98, 0.975, "> 1×: GPU Spark faster", transform=ratio.transAxes,
               ha="right", va="top", color="#436600", fontsize=9)
    ratio.text(0.98, 0.025, "< 1×: Polars CPU faster", transform=ratio.transAxes,
               ha="right", va="bottom", color="#14566A", fontsize=9)
    ratio.annotate("1× break-even", xy=(x[0], 1), xytext=(6, 5), textcoords="offset points",
                   fontsize=9, color="#374151", va="bottom")
    ratio.set_ylabel("Polars CPU time / GPU Spark time (log scale)")
    ratio.set_title("B  Relative time by query and overall", loc="left", fontsize=12, fontweight="bold", pad=17)
    ratio.yaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5), numticks=12))
    ratio.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}×"))
    ratio.yaxis.set_minor_formatter(NullFormatter())
    ratio.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=4,
                 frameon=False, fontsize=8.7, handlelength=2, columnspacing=1.2)
    for axis in (times, ratio):
        axis.set_xscale("log")
        axis.set_xticks(x, [f"{value:g}" for value in x])
        axis.xaxis.set_minor_locator(NullLocator())
        axis.set_xlabel("TPC-H-derived scale factor (log scale)", labelpad=8)
        axis.set_xlim(x[0] / 1.18, x[-1] * 1.18)
        axis.set_axisbelow(True)
        axis.grid(which="major", color="#9CA3AF", alpha=0.23, linewidth=0.65)
    trial_counts = "/".join(str(count) for count in sorted(set(trials.values())))
    fig.text(0.075, 0.105,
             f"One A100 40 GB host · 30 CPU threads · {trial_counts} measured trials/query · Float64 · preheated filesystem policy; residency not guaranteed",
             fontsize=9, color="#4B5563")
    fig.text(0.075, 0.072,
             "Aggregate = sum of per-query medians; startup excluded. Lines connect observations; no fitted threshold or extrapolation.",
             fontsize=9, color="#4B5563")
    excluded = len(evidence.get("excluded_scales", []))
    fig.text(0.075, 0.039,
             "Six selected, TPC-H-derived queries; non-certified. Single-host observations, not a distributed or equivalent-cost comparison."
             + (f" {excluded} incomplete/failed scale(s) excluded." if excluded else ""),
             fontsize=9, color="#4B5563")
    paths = tuple(report / f"crossover.{suffix}" for suffix in ("png", "svg"))
    for path in paths:
        fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        paths = plot(args.report.expanduser().resolve())
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Cannot plot crossover: {error}\n")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
