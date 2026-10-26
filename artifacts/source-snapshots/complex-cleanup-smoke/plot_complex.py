#!/usr/bin/env python3
"""Plot verified complex-query observations from analyze_complex.py.

Usage: python plot_complex.py --report DIR
Matplotlib is optional: uv run --no-project --with matplotlib python plot_complex.py --report DIR
Creates complex.png/.svg for up to twelve cases, otherwise complex-sfX.png/.svg
per scale. Only cases explicitly included by the analyzer may be plotted.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path


ENGINES = ("polars", "spark_cpu", "spark_gpu")
QUERIES = ("cx_q09", "cx_q13", "cx_semi_anti", "cx_window", "cx_distinct", "cx_self_join")
COVERAGE = {
    "gpu_operators_present": ("GPU operators present", ""),
    "hybrid": ("Hybrid CPU/GPU operators", "///"),
    "cpu_fallback": ("CPU fallback", "xxx"),
}


@dataclass(frozen=True)
class Case:
    scale: str
    query: str
    description: str
    measured_trials: int
    times: dict[str, tuple[float, float, float]]  # minimum, median, maximum
    polars_over_spark_gpu: float
    spark_cpu_over_spark_gpu: float
    gpu_execution_class: str


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
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {label}: {value!r}") from error
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return number


def case_key(item: dict) -> tuple[str, str]:
    if not isinstance(item, dict):
        raise ValueError("Each included case must be an object with scale and query")
    scale, query = item.get("scale"), item.get("query")
    scale_number(scale)
    if query not in QUERIES:
        raise ValueError(f"Unexpected complex query: {query!r}")
    return scale, query


def validate_report(report: Path) -> tuple[list[Case], dict]:
    """Validate the analyzer's inclusion gate before importing plotting packages."""
    evidence = json.loads((report / "correctness.json").read_text())
    if not isinstance(evidence, dict) or any(evidence.get(flag) is not True for flag in
            ("correctness_verified", "gpu_plan_coverage_verified")):
        raise ValueError("Plotting requires verified correctness and GPU-plan coverage")
    included = evidence.get("included_cases")
    if not isinstance(included, list) or not included:
        raise ValueError("Included cases must be a nonempty list")
    expected = {}
    scale_aliases = {}
    for item in included:
        key = case_key(item)
        number = scale_number(key[0])
        if key in expected:
            raise ValueError(f"Duplicate included case: {key}")
        if number in scale_aliases and scale_aliases[number] != key[0]:
            raise ValueError(f"Multiple names for the same scale factor: {key[0]}")
        if item.get("correctness_verified", True) is not True:
            raise ValueError(f"Included case is not correctness verified: {key}")
        scale_aliases[number] = key[0]
        expected[key] = item
    excluded = evidence.get("excluded_cases", [])
    if not isinstance(excluded, list):
        raise ValueError("Excluded cases must be a list")
    for item in excluded:
        if isinstance(item, dict) and (item.get("scale"), item.get("query")) in expected:
            raise ValueError("A case is both included and excluded")

    required = {"scale", "query", "description", "measured_trials", "gpu_execution_class",
                "polars_over_spark_gpu", "spark_cpu_over_spark_gpu"}
    required.update(engine + suffix for engine in ENGINES for suffix in
                    ("_min_seconds", "_median_seconds", "_max_seconds"))
    with (report / "summary.csv").open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not required.issubset(fields):
            raise ValueError("Summary CSV has missing or duplicate required columns")
        rows = list(reader)
    cases, seen = [], set()
    for row in rows:
        if None in row:
            raise ValueError("Summary CSV row has more fields than its header")
        key = case_key(row)
        if key in seen:
            raise ValueError(f"Duplicate summary case: {key}")
        if key not in expected:
            raise ValueError(f"CSV case is not in verified included cases: {key}")
        seen.add(key)
        try:
            trials = int(row["measured_trials"])
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid measured trial count: {key}") from error
        if trials < 1:
            raise ValueError(f"Measured trial count must be positive: {key}")
        if "measured_trials" in expected[key] and expected[key]["measured_trials"] != trials:
            raise ValueError(f"Trial count differs from included-case evidence: {key}")
        description = row["description"]
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"Missing query description: {key}")
        coverage = row["gpu_execution_class"]
        if coverage not in COVERAGE:
            raise ValueError(f"Invalid GPU execution class for {key}: {coverage!r}")
        recorded_coverage = expected[key].get("gpu_execution_class", coverage)
        if recorded_coverage != coverage:
            raise ValueError(f"GPU execution class differs from included-case evidence: {key}")
        times = {}
        for engine in ENGINES:
            low, median, high = (positive_number(row[engine + suffix], f"{key} {engine}{suffix}")
                                 for suffix in ("_min_seconds", "_median_seconds", "_max_seconds"))
            if not low <= median <= high:
                raise ValueError(f"Median falls outside min/max for {key} {engine}")
            if trials == 1 and not low == median == high:
                raise ValueError(f"One-trial case cannot have a nonzero trial range: {key} {engine}")
            times[engine] = low, median, high
        ratios = {}
        for column, engine in (("polars_over_spark_gpu", "polars"),
                               ("spark_cpu_over_spark_gpu", "spark_cpu")):
            ratio = positive_number(row[column], f"{key} {column}")
            calculated = times[engine][1] / times["spark_gpu"][1]
            if not math.isfinite(calculated) or not math.isclose(ratio, calculated, rel_tol=1e-10):
                raise ValueError(f"Inconsistent summary ratio for {key}: {column}")
            ratios[column] = ratio
        cases.append(Case(*key, description.strip(), trials, times,
                          ratios["polars_over_spark_gpu"], ratios["spark_cpu_over_spark_gpu"], coverage))
    if seen != set(expected):
        raise ValueError("Summary CSV coverage does not match verified included cases")
    cases.sort(key=lambda case: (scale_number(case.scale), QUERIES.index(case.query)))
    return cases, evidence


def trial_note(cases: list[Case]) -> str:
    counts = sorted({case.measured_trials for case in cases})
    if counts == [1]:
        return "1 measured trial/case (smoke); no variability estimate."
    note = "/".join(map(str, counts)) + " measured trials/case; medians and min–max ranges are descriptive."
    if 1 in counts:
        note += " One-trial cases are smoke observations."
    return note


def draw(cases: list[Case], evidence: dict, stem: Path) -> tuple[Path, Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "svg.fonttype": "none", "hatch.linewidth": 0.7})
    fig, (times, ratio) = plt.subplots(1, 2, figsize=(max(13.5, len(cases) * 1.15), 7.6))
    fig.subplots_adjust(left=0.065, right=0.98, top=0.71, bottom=0.31, wspace=0.24)
    fig.suptitle("Complex queries: measured engine observations", x=0.065, y=0.965,
                 ha="left", fontsize=20, fontweight="bold")
    fig.text(0.065, 0.91, "Validated included cases only · individual query times · no extrapolation",
             fontsize=11, color="#374151")
    labels = [f"{case.scale.upper()}\n{case.query.removeprefix('cx_')}" for case in cases]
    x = list(range(len(cases)))
    width = 0.245
    engine_legend = []
    for index, (engine, label, color) in enumerate((
            ("polars", "Polars CPU (streaming)", "#18708A"),
            ("spark_cpu", "CPU Spark", "#727B88"),
            ("spark_gpu", "Spark + GPU plugin (A100)", "#76A500"))):
        medians = [case.times[engine][1] for case in cases]
        lower = [value - case.times[engine][0] for value, case in zip(medians, cases)]
        upper = [case.times[engine][2] - value for value, case in zip(medians, cases)]
        bars = times.bar([value + (index - 1) * width for value in x], medians, width,
                         color=color, yerr=[lower, upper], capsize=2,
                         error_kw={"elinewidth": 0.8, "capthick": 0.8}, zorder=3)
        if engine == "spark_gpu":
            for bar, case in zip(bars, cases):
                bar.set_hatch(COVERAGE[case.gpu_execution_class][1])
                bar.set_edgecolor("#243008")
                bar.set_linewidth(0.5)
        engine_legend.append(Patch(facecolor=color, label=label))
    times.set_yscale("log")
    all_times = [value for case in cases for triple in case.times.values() for value in triple]
    times.set_ylim(min(all_times) / 1.7, max(all_times) * 1.8)
    times.set_ylabel("Query time (seconds; log scale)")
    times.set_title("A  Median time and min–max trial range", loc="left", fontweight="bold", pad=14)
    times.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    times.yaxis.set_minor_formatter(NullFormatter())

    values = [case.polars_over_spark_gpu for case in cases]
    low, high = min(values + [1]) / 1.8, max(values + [1]) * 2
    ratio.set_yscale("log")
    ratio.set_ylim(low, high)
    ratio.axhspan(low, 1, color="#18708A", alpha=0.055)
    ratio.axhspan(1, high, color="#76A500", alpha=0.075)
    ratio.axhline(1, color="#374151", linestyle=(0, (5, 3)), linewidth=1)
    for position, value, case in zip(x, values, cases):
        ratio.scatter(position, value, s=45, marker="o",
                      color="#4F7600" if value > 1 else "#18708A", zorder=4)
        ratio.annotate(f"{value:.2f}×", (position, value), xytext=(0, 8),
                       textcoords="offset points", ha="center", fontsize=8)
    ratio.text(0.98, 0.98, "> 1×: GPU-plugin Spark session faster", transform=ratio.transAxes,
               ha="right", va="top", fontsize=8.5, color="#436600")
    ratio.text(0.98, 0.02, "< 1×: Polars CPU faster", transform=ratio.transAxes,
               ha="right", va="bottom", fontsize=8.5, color="#14566A")
    ratio.set_ylabel("Polars median / GPU-plugin Spark median (log scale)")
    ratio.set_title("B  Relative query time · 1× break-even", loc="left", fontweight="bold", pad=14)
    ratio.yaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5), numticks=10))
    ratio.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}×"))
    ratio.yaxis.set_minor_formatter(NullFormatter())
    for axis in (times, ratio):
        axis.set_xticks(x, labels, fontsize=8, rotation=30 if len(cases) > 6 else 0)
        axis.set_xlim(-0.65, len(cases) - 0.35)
        axis.set_axisbelow(True)
        axis.grid(axis="y", which="major", color="#9CA3AF", alpha=0.25, linewidth=0.65)
    fig.legend(handles=engine_legend, loc="upper left", bbox_to_anchor=(0.06, 0.865),
               ncol=3, frameon=False, fontsize=9)
    present = {case.gpu_execution_class for case in cases}
    coverage_legend = [Patch(facecolor="#76A500", edgecolor="#243008", hatch=hatch, label=label)
                       for key, (label, hatch) in COVERAGE.items() if key in present]
    fig.legend(handles=coverage_legend, loc="upper left", bbox_to_anchor=(0.06, 0.815),
               ncol=3, frameon=False, fontsize=8.5, title="GPU-plugin session plan coverage")
    excluded = len(evidence.get("excluded_cases", []))
    footnotes = [
        "One A100 40 GB VM · 30 vCPU · preheated filesystem policy; residency not guaranteed · startup excluded.",
        trial_note(cases),
        "Coverage describes final-plan operator presence, including any CPU fallback; operator counts are not runtime fractions.",
        f"{len(cases)} included case(s) shown; {excluded} excluded case(s) in the report. Selected TPC-H-derived workload; non-certified.",
    ]
    for index, note in enumerate(footnotes):
        fig.text(0.065, 0.16 - index * 0.033, note, fontsize=8.5, color="#4B5563")
    paths = tuple(stem.with_suffix("." + suffix) for suffix in ("png", "svg"))
    for path in paths:
        fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)
    return paths


def plot(report: Path) -> tuple[Path, ...]:
    cases, evidence = validate_report(report)
    if len(cases) <= 12:
        return draw(cases, evidence, report / "complex")
    paths = []
    for scale in sorted({case.scale for case in cases}, key=scale_number):
        # Numeric-only file tokens keep output paths inside the report directory.
        token = format(scale_number(scale), "f").replace(".", "p")
        paths.extend(draw([case for case in cases if case.scale == scale], evidence,
                          report / ("complex-sf" + token)))
    return tuple(paths)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        paths = plot(args.report.expanduser().resolve())
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        parser.exit(1, f"Cannot plot complex queries: {error}\n")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
