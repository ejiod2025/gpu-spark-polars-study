#!/usr/bin/env python3
"""Plot complete, validated crossover observations from analyze_crossover.py.

Only the Python standard library and Matplotlib are required. Run the analyzer
first, then pass its output directory with --report. Saves crossover.png/.svg.
Lines connect observed points; no fitted or extrapolated threshold is shown.

Engines come from summary.csv. A Polars GPU cell with no timing (failed, invalid
or mismatching) is accepted only when correctness.json records that failure; it
leaves a gap in the line and is marked, never drawn as a zero or interpolated.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path

from analyze_results import ENGINE_ORDER, LEGACY_ENGINES, plot_hardware


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
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        rows = list(reader)
    engines = tuple(e for e in ENGINE_ORDER if e + "_median_seconds" in header)
    if tuple(evidence.get("engines", LEGACY_ENGINES)) != engines:
        raise ValueError("Summary CSV engines do not match the analyzer's engine set")
    if not set(LEGACY_ENGINES) <= set(engines):
        raise ValueError("Plotting requires the polars, spark_cpu and spark_gpu columns")
    # (scale, engine, query|None) -> recorded failure; None query = whole session.
    recorded = {(f.get("scale"), f.get("engine"), f.get("query")): f for f in evidence.get("engine_failures", [])}
    if any(engine in LEGACY_ENGINES for _, engine, _ in recorded):
        raise ValueError("A legacy engine failure cannot coexist with an included scale")
    if {row.get("scale") for row in rows} != set(scales):
        raise ValueError("CSV scales do not match the independently verified included scales")

    def failure_for(scale, engine, query):
        return recorded.get((scale, engine, query)) or recorded.get((scale, engine, None))

    values, trials, gaps = {}, {}, {}
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
        for engine in engines:
            if engine not in LEGACY_ENGINES and not row[engine + "_median_seconds"]:
                failure = failure_for(row["scale"], engine, row["query"])
                if failure is None or row.get(engine + "_status") != failure.get("kind"):
                    raise ValueError(f"Missing {engine} timing without a matching recorded failure: {key}")
                values[key][engine] = None
                gaps[(key, engine)] = failure["kind"]
                continue
            low, median, high = [positive_number(row[engine + suffix], f"{key} {engine}{suffix}")
                                 for suffix in ("_min_seconds", "_median_seconds", "_max_seconds")]
            if not low <= median <= high:
                raise ValueError(f"Median is outside the trial range for {key} {engine}")
            if engine not in LEGACY_ENGINES and row.get(engine + "_status") != "ok":
                raise ValueError(f"{engine} has a timing but status {row.get(engine + '_status')!r}: {key}")
            values[key][engine] = median
        ratio_columns = [("polars_over_spark_gpu", "polars", "spark_gpu"), ("spark_cpu_over_spark_gpu", "spark_cpu", "spark_gpu"),
                         ("polars_over_polars_gpu", "polars", "polars_gpu"), ("polars_gpu_over_spark_gpu", "polars_gpu", "spark_gpu")]
        for column, numerator, denominator in ratio_columns:
            if column not in header:
                continue
            if values[key][numerator] is None or values[key][denominator] is None:
                if row[column]:
                    raise ValueError(f"Ratio reported for a cell without timing: {key}: {column}")
                continue
            ratio = values[key][numerator] / values[key][denominator]
            if not math.isclose(positive_number(row[column], column), ratio, rel_tol=1e-10):
                raise ValueError(f"Inconsistent summary ratio for {key}: {column}")
    expected_rows = {(scale, query) for scale in scales for query in QUERIES}
    if set(values) != expected_rows:
        raise ValueError("Each plotted scale requires exactly all six query rows")

    expected_comparisons = {(scale, engine, query, phase, trial)
        for scale in scales for engine in engines for query in QUERIES
        for phase, trial in [("warmup", 0), *[("measured", n) for n in range(1, trials[scale] + 1)]]
        if values[(scale, query)][engine] is not None or (gaps.get(((scale, query), engine)) == "mismatch")}
    observed_comparisons = set()
    for item in evidence.get("comparisons", []):
        key = tuple(item.get(field) for field in ("scale", "engine", "query", "phase", "trial"))
        mismatch_allowed = gaps.get((((key[0], key[2])), key[1])) == "mismatch"
        if (key in observed_comparisons or key not in expected_comparisons
                or (item.get("passed") is not True and not mismatch_allowed)
                or (item.get("mismatch_count") != 0 and not mismatch_allowed)
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
    engines = tuple(evidence.get("engines", LEGACY_ENGINES))
    ext = "polars_gpu" in engines
    gpu, host_text = plot_hardware(evidence)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter, NullLocator

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "svg.fonttype": "none"})
    nan = float("nan")
    x = [float(scale_number(scale)) for scale in scales]
    # An engine's aggregate exists only when all six queries were timed; otherwise the scale is a gap.
    totals = {engine: [sum(values[(scale, query)][engine] for query in QUERIES)
                       if all(values[(scale, query)][engine] is not None for query in QUERIES) else None
                       for scale in scales] for engine in engines}
    overall = [polars / gpu_time for polars, gpu_time in zip(totals["polars"], totals["spark_gpu"])]
    ratios = {query: [values[(scale, query)]["polars"] / values[(scale, query)]["spark_gpu"]
                     for scale in scales] for query in QUERIES}
    fig, axes = plt.subplots(1, 3 if ext else 2, figsize=(20.0 if ext else 14.0, 7.2))
    times, ratio = axes[0], axes[1]
    fig.subplots_adjust(left=0.055 if ext else 0.075, right=0.985 if ext else 0.975, top=0.77, bottom=0.25,
                        wspace=0.30 if ext else 0.26)
    fig.suptitle("GPU crossover: measured observations" if ext else "GPU Spark crossover: measured observations",
                 x=0.055 if ext else 0.075, y=0.965, ha="left", fontsize=21, fontweight="bold")
    winning = [f"{float(scale_number(scale)):g}" for scale, value in zip(scales, overall) if value > 1]
    if winning:
        subtitle = "GPU Spark has the lower six-query aggregate at observed SF " + ", ".join(winning) + "."
    else:
        subtitle = f"No overall GPU Spark win observed through SF {float(scale_number(scales[-1])):g}."
    left = 0.055 if ext else 0.075
    fig.text(left, 0.905, subtitle, fontsize=12, color="#374151")
    if ext:
        gpu_wins = [f"{x_:g}" for x_, a, b in zip(x, totals["polars"], totals["polars_gpu"]) if b is not None and a / b > 1]
        missing = [f"{x_:g}" for x_, b in zip(x, totals["polars_gpu"]) if b is None]
        fig.text(left, 0.872, ("Polars GPU has the lower six-query aggregate at observed SF " + ", ".join(gpu_wins) + "."
                               if gpu_wins else "No overall Polars GPU win observed over the scales where it completed all six queries.")
                 + (" No Polars GPU aggregate (query failed) at SF " + ", ".join(missing) + "." if missing else ""),
                 fontsize=12, color="#374151")

    for engine, label, color, marker in (
            ("polars", "Polars CPU (streaming)", "#18708A", "o"),
            ("polars_gpu", "Polars GPU (cuDF, strict)", "#D9822B", "D"),
            ("spark_cpu", "CPU Spark", "#6B7280", "s"),
            ("spark_gpu", f"GPU Spark ({gpu})", "#619700", "^")):
        if engine not in engines:
            continue
        times.plot(x, [v if v is not None else nan for v in totals[engine]], marker=marker, markersize=6, linewidth=2.0,
                   color=color, label=label)
    for xi, value in zip(x, totals.get("polars_gpu", [])):
        if value is None:
            times.text(xi, 0.02, "Polars GPU\nfailed", transform=times.get_xaxis_transform(), ha="center",
                       va="bottom", fontsize=8, color="#B42318")
    times.set_yscale("log")
    times.set_ylabel("Sum of six query medians (seconds; log scale)")
    times.set_title("A  Overall workload time", loc="left", fontsize=12, fontweight="bold", pad=17)
    times.legend(loc="upper left", frameon=False, fontsize=9)
    times.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    times.yaxis.set_minor_formatter(NullFormatter())

    colors = ("#2878B5", "#D47316", "#169774", "#AE5AA5", "#B05B51", "#77852C")
    markers = ("o", "s", "^", "D", "v", "P")

    def ratio_panel(axis, series, overall_series, label, faster, slower, letter, title):
        for query, color, marker in zip(QUERIES, colors, markers):
            axis.plot(x, [v if v is not None else nan for v in series[query]], color=color, marker=marker, markersize=4.5,
                      linewidth=1.25, alpha=0.85, label=query.upper(), zorder=3)
        axis.plot(x, [v if v is not None else nan for v in overall_series], color="#111827", marker="o", markersize=7,
                  linewidth=3, label="Overall (sum of medians)", zorder=5)
        axis.set_yscale("log")
        all_ratios = [item for line in series.values() for item in line if item is not None] + [v for v in overall_series if v is not None]
        low, high = min(all_ratios + [1]) / 1.6, max(all_ratios + [1]) * 1.7
        axis.set_ylim(low, high)
        axis.axhspan(low, 1, color="#18708A", alpha=0.055, zorder=0)
        axis.axhspan(1, high, color="#619700", alpha=0.075, zorder=0)
        axis.axhline(1, color="#374151", linestyle=(0, (5, 3)), linewidth=1.25, zorder=2)
        axis.text(0.98, 0.975, faster, transform=axis.transAxes,
                  ha="right", va="top", color="#436600", fontsize=9)
        axis.text(0.98, 0.025, slower, transform=axis.transAxes,
                  ha="right", va="bottom", color="#14566A", fontsize=9)
        axis.annotate("1× break-even", xy=(x[0], 1), xytext=(6, 5), textcoords="offset points",
                      fontsize=9, color="#374151", va="bottom")
        axis.set_ylabel(label)
        axis.set_title(f"{letter}  {title}", loc="left", fontsize=12, fontweight="bold", pad=17)
        axis.yaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5), numticks=12))
        axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}×"))
        axis.yaxis.set_minor_formatter(NullFormatter())
        axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=4,
                    frameon=False, fontsize=8.7, handlelength=2, columnspacing=1.2)

    ratio_panel(ratio, ratios, overall, "Polars CPU time / GPU Spark time (log scale)", "> 1×: GPU Spark faster",
                "< 1×: Polars CPU faster", "B", "Relative time by query and overall")
    if ext:
        gpu_ratios = {query: [values[(scale, query)]["polars"] / values[(scale, query)]["polars_gpu"]
                              if values[(scale, query)]["polars_gpu"] is not None else None for scale in scales]
                      for query in QUERIES}
        gpu_overall = [a / b if b is not None else None for a, b in zip(totals["polars"], totals["polars_gpu"])]
        ratio_panel(axes[2], gpu_ratios, gpu_overall, "Polars CPU time / Polars GPU time (log scale)",
                    "> 1×: Polars GPU faster", "< 1×: Polars CPU faster", "C", "Polars GPU against Polars CPU")
    for axis in axes:
        axis.set_xscale("log")
        axis.set_xticks(x, [f"{value:g}" for value in x])
        axis.xaxis.set_minor_locator(NullLocator())
        axis.set_xlabel("TPC-H-derived scale factor (log scale)", labelpad=8)
        axis.set_xlim(x[0] / 1.18, x[-1] * 1.18)
        axis.set_axisbelow(True)
        axis.grid(which="major", color="#9CA3AF", alpha=0.23, linewidth=0.65)
    trial_counts = "/".join(str(count) for count in sorted(set(trials.values())))
    fig.text(left, 0.105,
             (f"One {gpu} 40 GB host · 30 CPU threads" if host_text is None else host_text) +
             f" · {trial_counts} measured trials/query · Float64 · preheated filesystem policy; residency not guaranteed",
             fontsize=9, color="#4B5563")
    fig.text(left, 0.072,
             "Aggregate = sum of per-query medians; startup excluded. Lines connect observations; no fitted threshold or extrapolation.",
             fontsize=9, color="#4B5563")
    excluded = len(evidence.get("excluded_scales", []))
    fig.text(left, 0.039,
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
