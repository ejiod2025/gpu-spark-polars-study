#!/usr/bin/env python3
"""Merge benchmark roots and report measured CPU Polars / GPU Spark crossovers.

Each scale is validated independently with the original strict validator. A
failed scale is reported but cannot suppress completed scales or become a win.
For duplicate numeric scale factors, the last --results root takes precedence.
Exit status is zero when at least one complete scale validates, one otherwise.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys
import tempfile

from analyze_results import ENGINES, QUERIES, load_and_validate, summarize


def read_json(path):
    return json.loads(path.read_text())


def number(value):
    value = str(value)
    if value.startswith("sf"):
        value = value[2:]
    result = Decimal(value.replace("p", "."))
    if not result.is_finite() or result <= 0:
        raise ValueError("scale must be finite and positive")
    return result


def cell(value):
    if value is None:
        return "unavailable"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\n", " ")


def duration(value):
    return f"{value:.3f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "unavailable"


def load_schedule(root):
    path = root / "schedule.json"
    if not path.is_file():
        return [], "schedule.json is absent"
    try:
        result = read_json(path)
        if not isinstance(result, list):
            raise ValueError("expected a list")
        return result, None
    except (OSError, ValueError, TypeError) as exc:
        return [], f"cannot read schedule.json: {exc}"


def schedule_for(schedule, scale, engine):
    matches = []
    for item in schedule:
        if not isinstance(item, dict) or item.get("engine") != engine:
            continue
        try:
            if number(item.get("scale")) == number(scale):
                matches.append(item)
        except (InvalidOperation, ValueError):
            pass
    return matches[-1] if matches else {}


def engine_state(scale_path, schedule, engine):
    item = schedule_for(schedule, scale_path.name, engine)
    path = scale_path / engine / "results.json"
    session = {}
    problem = None
    try:
        session = read_json(path)
        if not isinstance(session, dict):
            raise ValueError("expected an object")
    except (OSError, ValueError, TypeError) as exc:
        problem = str(exc)
        session = {}
    status = item.get("status") or session.get("status")
    if not status:
        if item.get("returncode") not in (None, 0):
            status = "failed"
        elif session.get("completed_at_utc"):
            status = "completed"
        elif path.exists() or item:
            status = "incomplete"
        else:
            status = "not run / no evidence"
    return {"engine": engine, "status": status, "returncode": item.get("returncode"),
            "active_query": session.get("active_query") or item.get("active_query"),
            "completed_records": len(session.get("records", [])),
            "error": item.get("error") or session.get("error") or session.get("failure") or problem,
            "schedule": item, "results_path": str(path),
            "session_completed_at_utc": session.get("completed_at_utc")}


def collect(roots):
    selected = {}
    overrides = []
    root_notes = []
    for root in roots:
        schedule, problem = load_schedule(root)
        found = []
        for path in root.glob("sf*"):
            if not path.is_dir():
                continue
            try:
                key = number(path.name)
            except (InvalidOperation, ValueError):
                continue
            found.append((key, path))
        for key, path in sorted(found):
            if key in selected:
                previous = selected[key]
                overrides.append({"scale_factor": str(key), "discarded_path": str(previous["path"]),
                                  "selected_path": str(path)})
            selected[key] = {"path": path, "root": root, "schedule": schedule}
        root_notes.append({"root": str(root), "scales_found": [p.name for _, p in sorted(found)],
                           "schedule_note": problem})

    sessions = {}
    evidence = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "scope": "complete, independently validated selected scales only",
                "duplicate_scale_policy": "last supplied result root wins; no fallback to earlier evidence",
                "source_roots": root_notes, "overrides": overrides, "included_scales": [],
                "excluded_scales": [], "comparisons": [], "gpu_plans": [], "datasets": [],
                "errors": [], "correctness_verified": False, "gpu_execution_verified": False,
                "passed": False}
    provenance = {}
    for _, source in sorted(selected.items()):
        path = source["path"]
        provenance[path.name] = source
        states = [engine_state(path, source["schedule"], engine) for engine in ENGINES]
        try:
            with tempfile.TemporaryDirectory(prefix="spark-crossover-") as temp:
                (Path(temp) / path.name).symlink_to(path, target_is_directory=True)
                scale_sessions, check = load_and_validate(Path(temp))
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
            scale_sessions, check = {}, {"passed": False, "errors": [str(exc)]}
        # A completed JSON file does not override a failed orchestrator process.
        failed_processes = [s for s in states if s["returncode"] not in (None, 0)
                            or str(s["status"]).lower() in {
                                "failed", "timeout", "timed_out", "cancelled", "killed", "running", "started", "incomplete"}
                            or s["active_query"]
                            or (s["schedule"] and s["returncode"] is None)]
        if failed_processes:
            check["passed"] = False
            check.setdefault("errors", []).extend(
                f"{s['engine']}: orchestrator status={s['status']}, returncode={s['returncode']}"
                for s in failed_processes)
        if not check.get("passed"):
            evidence["excluded_scales"].append({"scale": path.name, "source_root": str(source["root"]),
                "errors": check.get("errors", ["validation failed"]), "engines": states})
            continue
        sessions[path.name] = scale_sessions[path.name]
        evidence["included_scales"].append({"scale": path.name, "source_root": str(source["root"]),
                                            "engines": states})
        for field in ("comparisons", "gpu_plans", "datasets"):
            for item in check[field]:
                evidence[field].append({**item, "source_root": str(source["root"])})
        for field in ("relative_tolerance", "absolute_tolerance", "integer_comparison", "row_comparison"):
            evidence[field] = check[field]
        manifest = read_json(path / "manifest.json") if (path / "manifest.json").is_file() else {}
        source["manifest"] = manifest
        evidence["included_scales"][-1]["generation_metadata"] = {
            key: value for key, value in manifest.items() if key != "tables"}
    evidence["correctness_verified"] = bool(sessions) and bool(evidence["comparisons"])
    evidence["gpu_execution_verified"] = bool(sessions) and bool(evidence["gpu_plans"])
    evidence["passed"] = evidence["correctness_verified"] and evidence["gpu_execution_verified"]
    evidence["all_selected_scales_validated"] = bool(selected) and not evidence["excluded_scales"]
    if not selected:
        evidence["errors"].append("No sf* result directories with positive numeric scales found")
    return sessions, evidence, provenance


def brackets(points):
    """Return only observed changes in the sign of GPU/Polars time minus one."""
    changes = []
    for (left, a), (right, b) in zip(points, points[1:]):
        if a >= 1 and b < 1:
            changes.append(f"{left}–{right}: GPU becomes faster")
        elif a < 1 and b >= 1:
            changes.append(f"{left}–{right}: GPU ceases to be faster")
    return changes


def report(sessions, evidence, provenance, rows):
    scales = list(sessions)
    totals = {scale: {engine: sum(r[engine + "_median_seconds"] for r in rows if r["scale"] == scale)
                      for engine in ENGINES} for scale in scales}
    points = [(scale, totals[scale]["spark_gpu"] / totals[scale]["polars"]) for scale in scales]
    wins = [scale for scale, ratio in points if ratio < 1]
    lines = ["# Measured GPU Spark crossover search", ""]
    if wins:
        lines.append("GPU Spark has a lower sum of six query medians than CPU Polars at " +
                     ", ".join(wins) + f". The smallest observed winning scale is {wins[0]}; this does not establish an exact threshold.")
    elif scales:
        lines.append("No overall GPU Spark crossover over CPU Polars was observed across the complete, validated scales: " +
                     ", ".join(scales) + ".")
    else:
        lines.append("No scale has complete, validated results for all three engines and all six queries; no timing winner or crossover can be established.")
    if evidence["excluded_scales"]:
        lines += ["", f"**{len(evidence['excluded_scales'])} selected scale(s) were excluded from timing comparisons.** "
                  "Their failures or incomplete evidence are listed below. An OOM, timeout, or missing result is not a latency win."]
    lines += ["", "The aggregate is the sum of the six per-query medians, not a directly measured complete-suite runtime. "
              "GPU / Polars is GPU elapsed time divided by Polars elapsed time: below 1 favors GPU Spark. "
              "Every included scale passes the original strict output and final GPU-plan validator for all six queries and all saved trials.", "",
              "| Scale | Trials/query | Polars sum (s) | CPU Spark sum (s) | GPU Spark sum (s) | GPU / Polars | CPU Spark / GPU |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for scale, ratio in points:
        t = totals[scale]
        lines.append(f"| {scale} | {sessions[scale]['polars']['runs']} | {t['polars']:.3f} | {t['spark_cpu']:.3f} | "
                     f"{t['spark_gpu']:.3f} | {ratio:.3f}x | {t['spark_cpu'] / t['spark_gpu']:.3f}x |")
    lines += ["", "## Observed crossover brackets", "",
              "Brackets use adjacent complete, validated observations in numeric scale order. They locate an observed change of winner, "
              "not a fitted threshold, and do not imply monotonic performance between or beyond the measurements. "
              "Excluded scales inside a bracket provide no crossover evidence.", ""]
    overall = brackets(points)
    lines.append("Overall: " + ("; ".join(overall) if overall else "no observed change from Polars winning to GPU winning, or back, across the validated observations.") )
    lines += ["", "| Query | Observed scales where GPU is faster than Polars | Observed winner-change brackets |",
              "|---|---|---|"]
    for query in QUERIES:
        qpoints = [(r["scale"], r["spark_gpu_median_seconds"] / r["polars_median_seconds"])
                   for r in rows if r["query"] == query]
        q_wins = [scale for scale, ratio in qpoints if ratio < 1]
        changes = brackets(qpoints)
        lines.append(f"| {query} | {', '.join(q_wins) or 'none'} | {'; '.join(changes) or 'none observed'} |")
    lines += ["", "## Per-query measured times", "", "Median [minimum, maximum] seconds; ranges describe the saved trials.", "",
              "| Scale / query | Polars | CPU Spark | GPU Spark | GPU / Polars |", "|---|---:|---:|---:|---:|"]
    for row in rows:
        values = [f"{row[e + '_median_seconds']:.3f} [{row[e + '_min_seconds']:.3f}, {row[e + '_max_seconds']:.3f}]" for e in ENGINES]
        ratio = row["spark_gpu_median_seconds"] / row["polars_median_seconds"]
        lines.append(f"| {row['scale']} / {row['query']} | " + " | ".join(values) + f" | {ratio:.3f}x |")
    lines += ["", "## Startup and observed process time", "",
              "Query timing excludes engine startup, table setup, warmup and plan serialization. Launch-to-ready includes session/table setup; "
              "do not add these overlapping fields. Process wall time includes startup, all warmups and measured trials, serialization and shutdown; "
              "it is not a single six-query latency measurement.", "",
              "| Scale / engine | Launch → ready (s) | Session + table setup (s) | Warmup sum (s) | Process wall (s) |",
              "|---|---:|---:|---:|---:|"]
    for scale, engines in sessions.items():
        for engine in ENGINES:
            session = engines[engine]
            warmup = sum(r["seconds"] for (_, phase, _), r in session["indexed_records"].items() if phase == "warmup")
            item = schedule_for(provenance[scale]["schedule"], scale, engine)
            lines.append(f"| {scale} / {engine} | {duration(session.get('launch_to_engine_ready_seconds'))} | "
                         f"{duration(session.get('session_and_table_setup_seconds'))} | {warmup:.3f} | {duration(item.get('process_wall_seconds'))} |")
    lines += ["", "## Dataset and raw evidence", "",
              "Stored Parquet counts all generated tables, not uncompressed bytes or projected per-query inputs. "
              "Raw roots retain manifests, configuration, plans, logs, event logs and telemetry. Later supplied roots replace earlier roots for duplicate numeric scales; "
              "an incomplete replacement is excluded rather than silently falling back to an earlier run.", "",
              "| Scale | Stored Parquet (GiB) | Lineitem rows | Orders rows | Selected raw root |", "|---|---:|---:|---:|---|"]
    dataset_map = {d["scale"]: d for d in evidence["datasets"]}
    for scale in scales:
        dataset = dataset_map.get(scale, {})
        size = dataset.get("total_parquet_gib")
        root = provenance[scale]["root"]
        lines.append(f"| {scale} | {size:.3f}" +
                     f" | {dataset.get('lineitem_rows', 'unavailable'):,} | {dataset.get('orders_rows', 'unavailable'):,} | [{cell(root)}](<{root}>) |"
                     if size is not None else f"| {scale} | unavailable | unavailable | unavailable | [{cell(root)}](<{root}>) |")
    lines += ["", "| Supplied raw root | Scales found | Schedule note |", "|---|---|---|"]
    for source in evidence["source_roots"]:
        root = source["root"]
        lines.append(f"| [{cell(root)}](<{root}>) | {', '.join(source['scales_found']) or 'none'} | {cell(source['schedule_note'] or 'readable')} |")
    if evidence["overrides"]:
        lines += ["", "Duplicate selections:", ""]
        for item in evidence["overrides"]:
            lines.append(f"- SF {item['scale_factor']}: selected `{item['selected_path']}` instead of `{item['discarded_path']}`.")
    lines += ["", "## Failures and incomplete scales", ""]
    if not evidence["excluded_scales"]:
        lines.append("None among the selected scales.")
    for excluded in evidence["excluded_scales"]:
        lines += ["", f"### {excluded['scale']}", "", f"Raw root: [{cell(excluded['source_root'])}](<{excluded['source_root']}>)", "",
                  "| Engine | Status | Return code | Saved records | Active query at last save | Diagnostic |", "|---|---|---:|---:|---|---|"]
        for state in excluded["engines"]:
            lines.append(f"| {state['engine']} | {cell(state['status'])} | {cell(state['returncode'])} | {state['completed_records']} | "
                         f"{cell(state['active_query'])} | {cell(state['error'])} |")
        lines += ["", "Validation exclusions:", ""] + ["- " + cell(error) for error in excluded["errors"]]
    versions = sorted({str(source.get("manifest", {}).get("duckdb_version", "unrecorded"))
                       for scale, source in provenance.items() if scale in sessions})
    lines += ["", "## Interpretation and limits", "",
              "- The experiment targets one A100 40 GB VM with 30 CPU threads and 216 GiB host RAM. Each raw root's `environment.json` and engine configuration "
              "are the evidence for the actual host and allocation. This is local Spark, not a distributed scaling or equivalent-cost comparison. "
              "A recorded 96 GiB Spark driver heap and Polars' use of host memory are different memory budgets; host RAM, JVM heap and GPU memory are separate limits.",
              "- Files are read before each engine to preheat the filesystem, with no application DataFrame cache. Preheating cannot guarantee residency: "
              "compressed inputs, engine memory and OS memory compete for host RAM. Scales exceeding available cache introduce storage effects and cannot be "
              "interpreted as pure compute scaling. The same preheat policy does not establish an identical cache state.",
              "- These are six selected TPC-H-derived queries with Float64/DOUBLE inputs and tolerance-checked parallel reductions, not a complete or certified TPC-H result. "
              "Correctness is relative to the saved Polars warmup baseline at each included scale.",
              "- One warmup per query and three measured trials provide descriptive medians and ranges, not strong tail-latency or statistical claims. "
              "A one-trial smoke session, if present, has no repeatability estimate. Fresh process sessions across different scales are not independent repeated experiments.",
              "- Generation uses DuckDB's TPC-H generator (recorded version(s): " + ", ".join(versions or ["none validated"]) + "). "
              "The larger-data generation workflow may export parallel dbgen children while retaining the same DuckDB 1.5.6 generator. "
              "Parallel export can change physical row/file ordering, partition counts, compression and cache behavior; manifests and their source-generation metadata "
              "remain authoritative. Equal scale factors do not imply byte-identical files across separate generations.",
              "- Generation storage is separate from query working memory: a complete DuckDB database, Parquet exports and temporary spill can coexist. "
              "A scale that cannot be generated or executed within the host's disk/RAM/GPU limits does not establish that another engine is faster.",
              "- Sampled process RSS can miss peaks or double-count shared pages; GPU-plan checks demonstrate listed GPU operators, not the fraction of total runtime on GPU. "
              "No crossover is extrapolated beyond the complete, validated measurements.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    roots = [root.expanduser().resolve() for root in args.results]
    for root in roots:
        if not root.is_dir():
            parser.error(f"Result root is not a directory: {root}")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    sessions, evidence, provenance = collect(roots)
    rows = summarize(sessions)
    (output / "correctness.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    fields = ["scale", "query", "measured_trials"]
    for engine in ENGINES:
        fields += [engine + suffix for suffix in ("_median_seconds", "_min_seconds", "_max_seconds", "_max_peak_process_tree_rss_bytes")]
    fields += ["spark_cpu_over_spark_gpu", "polars_over_spark_gpu"]
    with (output / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    target = output / "CROSSOVER_REPORT.md"
    target.write_text(report(sessions, evidence, provenance, rows))
    for scale in evidence["excluded_scales"]:
        print(f"Excluded {scale['scale']} ({scale['source_root']}): " + "; ".join(scale["errors"]), file=sys.stderr)
    print(f"Validated {len(sessions)} scales; excluded {len(evidence['excluded_scales'])}; report: {target}")
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
