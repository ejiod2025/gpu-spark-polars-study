#!/usr/bin/env python3
"""Validate three-engine benchmark evidence and summarize measured query times.

Uses only Python's standard library. A performance report is written only after
all recorded outputs and saved GPU executed plans pass validation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys


ENGINES = ("polars", "spark_cpu", "spark_gpu")
QUERIES = ("q01", "q03", "q06", "q10", "q12", "q14")
REL_TOL = 1e-8
ABS_TOL = 1e-6


def read_json(path):
    with path.open() as handle:
        return json.load(handle)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def compare_values(expected, actual):
    """Compare complete ordered results, keeping bounded diagnostic examples."""
    differences = []
    mismatch_count = 0
    max_abs = 0.0
    max_rel = 0.0

    def mismatch(path, reason, left, right):
        nonlocal mismatch_count
        mismatch_count += 1
        if len(differences) < 20:
            differences.append({"path": path, "reason": reason,
                                "expected": left, "actual": right})

    def visit(left, right, path):
        nonlocal max_abs, max_rel
        if isinstance(left, bool) or isinstance(right, bool):
            if type(left) is not type(right) or left != right:
                mismatch(path, "boolean differs", left, right)
        elif isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if not math.isfinite(left) or not math.isfinite(right):
                mismatch(path, "non-finite numeric output", str(left), str(right))
                return
            absolute = abs(left - right)
            relative = absolute / max(abs(left), abs(right)) if left or right else 0.0
            max_abs = max(max_abs, absolute)
            max_rel = max(max_rel, relative)
            equal = left == right if isinstance(left, int) and isinstance(right, int) else math.isclose(
                left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)
            if not equal:
                mismatch(path, "numeric value differs", left, right)
        elif isinstance(left, dict) and isinstance(right, dict):
            if left.keys() != right.keys():
                mismatch(path, "column names differ", sorted(left), sorted(right))
            for key in sorted(left.keys() & right.keys()):
                visit(left[key], right[key], path + "." + key)
        elif isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                mismatch(path, "row/list count differs", len(left), len(right))
            for index, (a, b) in enumerate(zip(left, right)):
                visit(a, b, f"{path}[{index}]")
        elif type(left) is not type(right) or left != right:
            mismatch(path, "value or type differs", left, right)

    visit(expected, actual, "$")
    return {"passed": mismatch_count == 0, "mismatch_count": mismatch_count,
            "differences": differences, "max_absolute_numeric_difference": max_abs,
            "max_relative_numeric_difference": max_rel}


def inspect_gpu_plan(path):
    plan = path.read_text()
    # AQE toString() can contain both the actual final and unused initial plan.
    if "AdaptiveSparkPlan" in plan and "isFinalPlan=true" not in plan:
        raise ValueError("adaptive plan is not marked isFinalPlan=true")
    if "== Final Plan ==" in plan:
        plan = plan.split("== Final Plan ==", 1)[1].split("== Initial Plan ==", 1)[0]
    names = Counter(re.findall(r"\bGpu[A-Z][A-Za-z0-9]*\b", plan))
    scans = {name: count for name, count in names.items() if "Scan" in name}
    aggregates = {name: count for name, count in names.items() if "Aggregate" in name}
    joins = {name: count for name, count in names.items() if "Join" in name}
    if not scans:
        raise ValueError("no GPU scan operator in final executed plan")
    if not aggregates and not joins:
        raise ValueError("no GPU aggregate or join operator in final executed plan")
    cpu_names = Counter(re.findall(
        r"\b(?:HashAggregate|ObjectHashAggregate|SortAggregate|BroadcastHashJoin|"
        r"SortMergeJoin|ShuffledHashJoin|BroadcastNestedLoopJoin|FileScan|BatchScan)\b", plan))
    return {"gpu_operators": dict(sorted(names.items())), "gpu_scans": scans,
            "gpu_aggregates": aggregates, "gpu_joins": joins,
            "cpu_scan_aggregate_join_operators": dict(sorted(cpu_names.items())),
            "source": str(path)}


def load_and_validate(root):
    evidence = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "correctness_verified": False, "gpu_execution_verified": False,
                "relative_tolerance": REL_TOL, "absolute_tolerance": ABS_TOL,
                "integer_comparison": "exact", "row_comparison": "ordered; dictionary key order ignored",
                "errors": [], "comparisons": [], "gpu_plans": [], "datasets": []}
    sessions = {}
    scales = sorted(p for p in root.glob("sf*") if p.is_dir())
    if not scales:
        evidence["errors"].append("No sf* result directories found")
    for scale in scales:
        sessions[scale.name] = {}
        for engine in ENGINES:
            path = scale / engine / "results.json"
            label = f"{scale.name}/{engine}"
            try:
                session = read_json(path)
                if session.get("engine") != engine:
                    raise ValueError("engine metadata does not match directory")
                if not session.get("completed_at_utc"):
                    raise ValueError("session missing completed_at_utc")
                datetime.fromisoformat(session["completed_at_utc"])
                runs = session.get("runs")
                if isinstance(runs, bool) or runs not in (1, 3):
                    raise ValueError("expected 3 measured trials, or 1 for a smoke run")
                if set(session.get("queries", [])) != set(QUERIES) or len(session["queries"]) != len(QUERIES):
                    raise ValueError("expected all six queries: " + ", ".join(QUERIES))
                if not session.get("manifest_sha256"):
                    raise ValueError("missing dataset manifest SHA-256")
                if "scale_factor" not in session:
                    raise ValueError("missing scale_factor")
                setup = session.get("session_and_table_setup_seconds")
                if isinstance(setup, bool) or not isinstance(setup, (float, int)) or not math.isfinite(setup) or setup < 0:
                    raise ValueError("invalid session_and_table_setup_seconds")
                ready = session.get("launch_to_engine_ready_seconds")
                if ready is not None and (isinstance(ready, bool) or not isinstance(ready, (float, int)) or
                                          not math.isfinite(ready) or ready < 0):
                    raise ValueError("invalid launch_to_engine_ready_seconds")
                expected = {(q, "warmup", 0) for q in QUERIES} | {
                    (q, "measured", trial) for q in QUERIES for trial in range(1, runs + 1)}
                found = {}
                for record in session.get("records", []):
                    key = (record.get("query"), record.get("phase"), record.get("trial"))
                    if key not in expected:
                        raise ValueError(f"unexpected query/phase/trial: {key}")
                    if key in found:
                        raise ValueError(f"duplicate query/phase/trial: {key}")
                    if isinstance(record["trial"], bool) or not isinstance(record["trial"], int):
                        raise ValueError(f"invalid trial number for {key}")
                    seconds = record.get("seconds")
                    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
                        raise ValueError(f"invalid materialized query duration for {key}")
                    if not isinstance(record.get("rows"), list) or not all(isinstance(row, dict) for row in record["rows"]):
                        raise ValueError(f"missing result rows for {key}")
                    rss = record.get("peak_process_tree_rss_bytes")
                    if isinstance(rss, bool) or not isinstance(rss, int) or rss < 0:
                        raise ValueError(f"invalid process-tree peak RSS for {key}")
                    plan_path = (path.parent / record["plan"]).resolve()
                    if not plan_path.is_relative_to(path.parent.resolve()) or not plan_path.is_file():
                        raise ValueError(f"missing or out-of-directory saved plan for {key}")
                    if engine == "spark_gpu":
                        plan = inspect_gpu_plan(plan_path)
                        evidence["gpu_plans"].append({"scale": scale.name, "query": key[0],
                            "phase": key[1], "trial": key[2],
                            "worker_gpu_plan_observed": record.get("gpu_plan_observed"), **plan})
                    found[key] = record
                missing = expected - found.keys()
                if missing:
                    raise ValueError(f"missing query/phase/trial records: {sorted(missing)}")
                session["indexed_records"] = found
                sessions[scale.name][engine] = session
            except (OSError, ValueError, KeyError, TypeError) as exc:
                evidence["errors"].append(f"{label}: {exc}")
        loaded = sessions[scale.name]
        if set(loaded) != set(ENGINES):
            continue
        baseline = loaded["polars"]
        manifest_path = scale / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = read_json(manifest_path)
                checksum = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                if checksum != baseline["manifest_sha256"]:
                    raise ValueError("copied manifest SHA-256 differs from engine evidence")
                if manifest["scale_factor"] != baseline["scale_factor"]:
                    raise ValueError("copied manifest scale_factor differs from engine evidence")
                tables = manifest["tables"]
                row_total = sum(table["rows"] for table in tables.values())
                byte_total = sum(table["parquet_bytes"] for table in tables.values())
                if manifest.get("total_rows", row_total) != row_total or manifest.get("total_parquet_bytes", byte_total) != byte_total:
                    raise ValueError("copied manifest totals differ from its table metadata")
                evidence["datasets"].append({"scale": scale.name, "total_parquet_bytes": byte_total,
                    "total_parquet_gib": byte_total / 1024**3, "total_rows": row_total,
                    "lineitem_rows": tables["lineitem"]["rows"], "orders_rows": tables["orders"]["rows"],
                    "manifest_sha256": checksum})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                evidence["errors"].append(f"{scale.name}/manifest.json: {exc}")
        for engine, session in loaded.items():
            for field in ("manifest_sha256", "scale_factor", "runs", "cpu_threads", "cache_policy", "timing_scope"):
                if session.get(field) != baseline.get(field):
                    evidence["errors"].append(f"{scale.name}/{engine}: {field} differs from Polars")
            for key, record in session["indexed_records"].items():
                reference = baseline["indexed_records"][(key[0], "warmup", 0)]["rows"]
                check = compare_values(reference, record["rows"])
                evidence["comparisons"].append({"scale": scale.name, "engine": engine,
                    "query": key[0], "phase": key[1], "trial": key[2],
                    "expected_row_count": len(reference), "actual_row_count": len(record["rows"]), **check})
                if not check["passed"]:
                    evidence["errors"].append(f"{scale.name}/{engine}/{key}: result differs from Polars warmup")
    valid = not evidence["errors"]
    evidence["correctness_verified"] = valid and bool(evidence["comparisons"])
    evidence["gpu_execution_verified"] = valid and bool(evidence["gpu_plans"])
    evidence["passed"] = evidence["correctness_verified"] and evidence["gpu_execution_verified"]
    return sessions, evidence


def summarize(sessions):
    rows = []
    for scale, engines in sessions.items():
        for query in QUERIES:
            row = {"scale": scale, "query": query, "measured_trials": engines["polars"]["runs"]}
            for engine in ENGINES:
                records = [r for (q, phase, _), r in engines[engine]["indexed_records"].items()
                           if q == query and phase == "measured"]
                times = [r["seconds"] for r in records]
                row[engine + "_median_seconds"] = statistics.median(times)
                row[engine + "_min_seconds"] = min(times)
                row[engine + "_max_seconds"] = max(times)
                row[engine + "_max_peak_process_tree_rss_bytes"] = max(r["peak_process_tree_rss_bytes"] for r in records)
            row["spark_cpu_over_spark_gpu"] = row["spark_cpu_median_seconds"] / row["spark_gpu_median_seconds"]
            row["polars_over_spark_gpu"] = row["polars_median_seconds"] / row["spark_gpu_median_seconds"]
            rows.append(row)
    return rows


def report_text(root, sessions, evidence, rows):
    smoke = any(s["runs"] == 1 for engines in sessions.values() for s in engines.values())
    text = ["# Polars, CPU Spark, and GPU Spark experiment", "",
            "**Correctness verified** for every saved warmup and measured output against the Polars warmup baseline. "
            "Final saved GPU plans contain GPU scan and aggregation/join operators. "
            f"Floating-point tolerance: relative {REL_TOL:g}, absolute {ABS_TOL:g}; integer pairs, strings, dates, and nulls match exactly.", ""]
    if smoke:
        text += ["**Smoke run:** at least one scale has one measured trial. These timings verify the harness and provide no estimate of repeatability.", ""]
    text += ["Polars runs on the CPU using its streaming execution engine. Both Spark modes use the recorded Spark version; "
             "GPU Spark adds the NVIDIA cuDF plugin. Software versions and CPU thread allocations below come from the engine sessions.", "",
             "| Scale / engine | Engine version | CPU threads | GPU plugin artifact |",
             "|---|---|---:|---|"]
    for scale, engines in sessions.items():
        for engine in ENGINES:
            session = engines[engine]
            jars = session.get("spark_conf", {}).get("spark.jars", "")
            plugin = next((jar.rsplit("/", 1)[-1] for jar in jars.split(",") if "rapids-4-spark_" in jar), "—")
            label = "polars (CPU streaming)" if engine == "polars" else engine
            text.append(f"| {scale} / {label} | {session.get('version', 'unavailable')} | "
                        f"{session.get('cpu_threads', 'unavailable')} | {plugin} |")
    text.append("")
    if evidence["datasets"]:
        text += ["Dataset sizes are from the copied, checksum-verified manifests. Parquet GiB counts stored file bytes "
                 "across all generated tables, not uncompressed or per-query input size.", "",
                 "| Scale | Stored Parquet (GiB) | Lineitem rows | Orders rows | All-table rows |",
                 "|---|---:|---:|---:|---:|"]
        for dataset in evidence["datasets"]:
            text.append(f"| {dataset['scale']} | {dataset['total_parquet_gib']:.3f} | {dataset['lineitem_rows']:,} | "
                        f"{dataset['orders_rows']:,} | {dataset['total_rows']:,} |")
        if len(evidence["datasets"]) != len(sessions):
            text += ["", "Some scales have no copied manifest; dataset sizes are omitted for those scales."]
        text.append("")
    text += ["The table reports the **sum of six per-query medians**, in seconds. This is a derived workload summary, "
             "not a measured complete-suite runtime. Ratios above 1 favor GPU Spark; below 1 favor the named CPU engine.", "",
             "| Scale | Trials/query | Polars sum | CPU Spark sum | GPU Spark sum | CPU Spark / GPU | Polars / GPU |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for scale, engines in sessions.items():
        group = [r for r in rows if r["scale"] == scale]
        totals = {e: sum(r[e + "_median_seconds"] for r in group) for e in ENGINES}
        text.append(f"| {scale} | {engines['polars']['runs']} | {totals['polars']:.3f} | {totals['spark_cpu']:.3f} | "
                    f"{totals['spark_gpu']:.3f} | {totals['spark_cpu'] / totals['spark_gpu']:.2f}x | "
                    f"{totals['polars'] / totals['spark_gpu']:.2f}x |")
    text += ["", "Per-query measured times are median [min, max] seconds. The full precision values and sampled process-tree peak RSS are in `summary.csv`.", "",
             "| Scale / query | Polars | CPU Spark | GPU Spark | CPU Spark / GPU | Polars / GPU |",
             "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        cells = [f"{row[e + '_median_seconds']:.3f} [{row[e + '_min_seconds']:.3f}, {row[e + '_max_seconds']:.3f}]" for e in ENGINES]
        text.append(f"| {row['scale']} / {row['query']} | " + " | ".join(cells) +
                    f" | {row['spark_cpu_over_spark_gpu']:.2f}x | {row['polars_over_spark_gpu']:.2f}x |")
    text += ["", "For each comparator, the strongest and weakest GPU cases by median-time ratio are:", ""]
    for label, field in (("CPU Spark", "spark_cpu_over_spark_gpu"), ("Polars", "polars_over_spark_gpu")):
        best, worst = max(rows, key=lambda r: r[field]), min(rows, key=lambda r: r[field])
        text.append(f"- Against {label}: strongest {best['scale']}/{best['query']} ({best[field]:.2f}x); "
                    f"weakest {worst['scale']}/{worst['query']} ({worst[field]:.2f}x).")
    text += ["", "Startup and setup are excluded from query timings. Launch-to-engine-ready spans the orchestrator's subprocess launch "
             "through launcher/imports, session initialization, and table/view setup; it includes the session-and-table-setup field measured inside the worker. "
             "It ends before warmup queries and does not represent dataset loading or first-query execution. Older results may lack this field. "
             "Process wall time includes startup, setup, all warmup and measured queries, plan serialization, and shutdown. "
             "These are overlapping scopes; do not add them together.", "",
             "| Scale / engine | Launch → engine ready (s) | Session + table setup (s) | Warmup query sum (s) | Process wall time (s) |",
             "|---|---:|---:|---:|---:|"]
    schedule_path = root / "schedule.json"
    schedule = read_json(schedule_path) if schedule_path.is_file() else []
    for scale, engines in sessions.items():
        for engine in ENGINES:
            session = engines[engine]
            warm = sum(r["seconds"] for (_, phase, _), r in session["indexed_records"].items() if phase == "warmup")
            process = next((r.get("process_wall_seconds") for r in schedule if r.get("engine") == engine and
                            "sf" + str(r.get("scale", "")).replace(".", "p") == scale), None)
            wall = f"{process:.3f}" if isinstance(process, (int, float)) else "unavailable"
            ready = session.get("launch_to_engine_ready_seconds")
            ready_text = f"{ready:.3f}" if ready is not None else "unavailable"
            text.append(f"| {scale} / {engine} | {ready_text} | {session['session_and_table_setup_seconds']:.3f} | {warm:.3f} | {wall} |")
    operators = Counter()
    cpu_operators = Counter()
    for plan in evidence["gpu_plans"]:
        operators.update(plan["gpu_operators"])
        cpu_operators.update(plan["cpu_scan_aggregate_join_operators"])
    text += ["", "GPU evidence counts below sum operator occurrences across all saved final warmup/measured plans; "
             "they are not runtime percentages or unique executed stages.", "",
             "- GPU scans: " + str(sum(count for name, count in operators.items() if "Scan" in name)) +
             "; GPU aggregates: " + str(sum(count for name, count in operators.items() if "Aggregate" in name)) +
             "; GPU joins: " + str(sum(count for name, count in operators.items() if "Join" in name)) + ".",
             "- CPU scan/aggregate/join operators retained in GPU plans: " +
             (", ".join(f"{name}: {count}" for name, count in sorted(cpu_operators.items())) or "none detected by this operator-name check") + ".",
             "- Complete operator counts and per-record checks: `correctness.json`; original plans, event logs, configuration, and GPU telemetry remain in the result directory.", "",
             "Interpretation and limits:", "",
             "- All engines use one host and the CPU allocation recorded in the evidence. CPU engines run on the same GPU VM. This does not compare equivalent-cost CPU instances, multi-node scaling, or total production cost; no pricing advantage is established.",
             "- Files are preheated before each engine. Timings cover construction/optimization, Parquet reads, execution, and materialized result collection, with no application DataFrame cache. They measure a warm-filesystem workload, not cold remote storage. Preheating cannot guarantee every page remains resident.",
             "- The six selected TPC-H-derived queries are a bounded workload, not a complete or certified TPC-H benchmark. Monetary and quantity inputs use Float64/DOUBLE arithmetic rather than TPC-H decimal semantics. Parallel reductions are checked with the declared tolerance.",
             "- One untimed warmup per query precedes the measured trials; query order and engine order are seeded. Range values expose sample variability but three trials do not establish robust tail latency. Engine sessions run sequentially and are not isolated independent experiment repetitions.",
             "- Sampled process-tree RSS includes the Spark JVM and may double-count shared pages; it is not total machine memory, GPU memory, or a precise peak. GPU-plan evidence establishes acceleration of listed operators, not full GPU execution or the fraction of elapsed time spent there.",
             "- Results support conclusions for these datasets, query shapes, versions, settings, and this host. They do not establish a global winning engine.", "",
             "Sources: [NVIDIA cuDF Spark overview](https://docs.nvidia.com/cudf-spark/latest/overview.html), "
             "[supported versions](https://nvidia.github.io/cudf-spark/docs/download.html), "
             "[CPU/GPU semantic differences](https://nvidia.github.io/cudf-spark/docs/compatibility.html).", ""]
    return "\n".join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.results.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sessions, evidence = load_and_validate(root)
    write_json(output / "correctness.json", evidence)
    if not evidence["passed"]:
        # Avoid leaving a previous successful report beside current failed checks.
        for name in ("summary.csv", "REPORT.md"):
            (output / name).unlink(missing_ok=True)
        for error in evidence["errors"]:
            print(error, file=sys.stderr)
        return 1
    rows = summarize(sessions)
    with (output / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / "REPORT.md").write_text(report_text(root, sessions, evidence, rows))
    print(f"Verified {len(evidence['comparisons'])} outputs and {len(evidence['gpu_plans'])} GPU plans; report: {output / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
