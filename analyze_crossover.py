#!/usr/bin/env python3
"""Merge benchmark roots and report measured CPU Polars / GPU Spark crossovers.

Each scale is validated independently with the original strict validator. A
failed scale is reported but cannot suppress completed scales or become a win.
For duplicate numeric scale factors, the last --results root takes precedence.
Exit status is zero when at least one complete scale validates, one otherwise.

When polars_gpu results exist (Polars with the cuDF GPU engine, strict mode) the
report gains Polars GPU columns, pairwise speedup matrices and failure tables.
A polars_gpu failure, invalid strict-GPU evidence or output mismatch is shown as
an explicit cell and never excludes a scale for the other engines; it does make
the exit status nonzero when it is an integrity violation (invalid/mismatch).
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

from analyze_results import (ENGINE_LABELS, ENGINE_ORDER, LEGACY_ENGINES, QUERIES, agreement_lines, config_text,
                             discover_engines, engine_sums, failure_index, failure_lines, gpu_memory_engines,
                             gpu_memory_lines, host_label, host_shape, is_extended, legacy_host_applies,
                             load_and_validate, matrix_lines, problem, problem_cell, ratio_text, read_environment,
                             resolve_engines, row_cell, safe_ratio, summarize, summary_columns, write_pairwise_csv)


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


def orchestrator_failure(state):
    """Reason string when the orchestrator reports this engine's process as failed/unfinished, else None."""
    if (state["returncode"] not in (None, 0) or str(state["status"]).lower() in {
            "failed", "timeout", "timed_out", "cancelled", "killed", "running", "started", "incomplete"}
            or state["active_query"] or (state["schedule"] and state["returncode"] is None)):
        return f"orchestrator status={state['status']}, returncode={state['returncode']}"
    return None


def collect(roots, requested_engines=None):
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

    discovered = set()
    for source in selected.values():
        discovered |= discover_engines(source["path"], source["schedule"])
    engines = resolve_engines(requested_engines, discovered)
    extended = is_extended(engines)
    sessions = {}
    evidence = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "scope": "complete, independently validated selected scales only",
                "duplicate_scale_policy": "last supplied result root wins; no fallback to earlier evidence",
                "source_roots": root_notes, "overrides": overrides, "included_scales": [],
                "excluded_scales": [], "comparisons": [], "gpu_plans": [], "datasets": [],
                "errors": [], "correctness_verified": False, "gpu_execution_verified": False,
                "passed": False}
    if extended:
        evidence["engines"] = list(engines)
        evidence["engine_failures"], evidence["engine_warnings"] = [], []
        evidence["polars_gpu_sessions"], evidence["polars_gpu_numeric_agreement"] = [], []
        for note in root_notes:
            note["host"] = host_shape(read_environment(note["root"]))
    if discovered - set(engines):
        evidence["engines_excluded_by_flag"] = sorted(discovered - set(engines))
    provenance = {}
    for _, source in sorted(selected.items()):
        path = source["path"]
        provenance[path.name] = source
        states = [engine_state(path, source["schedule"], engine) for engine in engines]
        core_states = [s for s in states if s["engine"] in LEGACY_ENGINES]
        external = {s["engine"]: reason for s in states if s["engine"] not in LEGACY_ENGINES
                    for reason in [orchestrator_failure(s)] if reason}
        try:
            with tempfile.TemporaryDirectory(prefix="spark-crossover-") as temp:
                (Path(temp) / path.name).symlink_to(path, target_is_directory=True)
                scale_sessions, check = load_and_validate(Path(temp), engines, {path.name: external})
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
            scale_sessions, check = {}, {"passed": False, "errors": [str(exc)]}
        # A completed JSON file does not override a failed orchestrator process.
        failed_processes = [s for s in core_states if orchestrator_failure(s)]
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
        if extended:
            for field in ("engine_failures", "engine_warnings"):
                evidence[field].extend({**item, "source_root": str(source["root"])} for item in check[field])
            evidence["polars_gpu_sessions"].extend(check["polars_gpu_sessions"])
            evidence["polars_gpu_numeric_agreement"].extend(check["polars_gpu_numeric_agreement"])
        for field in ("relative_tolerance", "absolute_tolerance", "integer_comparison", "row_comparison"):
            evidence[field] = check[field]
        manifest = read_json(path / "manifest.json") if (path / "manifest.json").is_file() else {}
        source["manifest"] = manifest
        evidence["included_scales"][-1]["generation_metadata"] = {
            key: value for key, value in manifest.items() if key != "tables"}
    evidence["correctness_verified"] = bool(sessions) and bool(evidence["comparisons"])
    gpu_evidence = bool(evidence["gpu_plans"]) or ("spark_gpu" not in engines and any(
        "polars_gpu" in loaded for loaded in sessions.values()))
    evidence["gpu_execution_verified"] = bool(sessions) and gpu_evidence
    evidence["passed"] = evidence["correctness_verified"] and evidence["gpu_execution_verified"]
    evidence["all_selected_scales_validated"] = bool(selected) and not evidence["excluded_scales"]
    if extended:
        evidence["polars_gpu_agreement_note"] = ("max_relative_numeric_difference is the largest relative difference to the Polars warmup over all compared "
                                                 "numeric values; the tolerance is reported, not loosened.")
        evidence["integrity_violations"] = [f for f in evidence["engine_failures"] if f["kind"] in ("invalid", "mismatch")]
    if not selected:
        evidence["errors"].append("No sf* result directories with positive numeric scales found")
    return sessions, evidence, provenance


def brackets(points, winner="GPU"):
    """Return only observed changes in the sign of GPU/Polars time minus one."""
    changes = []
    for (left, a), (right, b) in zip(points, points[1:]):
        if a >= 1 and b < 1:
            changes.append(f"{left}–{right}: {winner} becomes faster")
        elif a < 1 and b >= 1:
            changes.append(f"{left}–{right}: {winner} ceases to be faster")
    return changes


def host_bullet(sessions, provenance, ext):
    shapes = [host_shape(read_environment(provenance[scale]["root"])) for scale in sessions]
    if not ext and legacy_host_applies(shapes):
        return ("- The experiment targets one A100 40 GB VM with 30 CPU threads and 216 GiB host RAM. Each raw root's `environment.json` and engine configuration "
                "are the evidence for the actual host and allocation. This is local Spark, not a distributed scaling or equivalent-cost comparison. "
                "A recorded 96 GiB Spark driver heap and Polars' use of host memory are different memory budgets; host RAM, JVM heap and GPU memory are separate limits.")
    labels = list(dict.fromkeys(host_label(shape) for shape in shapes)) or ["host not recorded"]
    drivers = sorted({str(shape["driver_memory_arg"]) for shape in shapes if shape["driver_memory_arg"]})
    return ("- The experiment targets one single-GPU VM per raw root: " + "; ".join(labels) + ". Each raw root's `environment.json` and engine configuration "
            "are the evidence for the actual host and allocation. This is local Spark, not a distributed scaling or equivalent-cost comparison. "
            + (f"A recorded {', '.join(drivers)} Spark driver heap and " if drivers else "The Spark driver heap and ")
            + "the engines' use of host memory are different memory budgets; host RAM, JVM heap and GPU memory are separate limits.")


def report(sessions, evidence, provenance, rows):
    engines = tuple(evidence.get("engines", LEGACY_ENGINES))
    ext = is_extended(engines)
    index = failure_index(evidence.get("engine_failures", []))
    scales = list(sessions)
    sums = engine_sums(rows, engines)
    totals = {scale: {engine: sums[(scale, engine)][0] for engine in engines} for scale in scales}
    spark_pair = "spark_gpu" in engines and "polars" in engines
    pairs = []
    if spark_pair:
        pairs.append(("GPU / Polars", "spark_gpu", "polars"))
    if spark_pair and "spark_cpu" in engines:
        pairs.append(("CPU Spark / GPU", "spark_cpu", "spark_gpu"))
    if ext:
        pairs += [(label, num, den) for label, num, den in (("Polars / Polars GPU", "polars", "polars_gpu"),
                  ("Polars GPU / GPU Spark", "polars_gpu", "spark_gpu")) if num in engines and den in engines]
    points = [(scale, totals[scale]["spark_gpu"] / totals[scale]["polars"]) for scale in scales] if spark_pair else []
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
    gpu_points = []
    if ext and "polars_gpu" in engines and scales:
        gpu_points = [(scale, totals[scale]["polars_gpu"] / totals[scale]["polars"]) for scale in scales
                      if totals[scale]["polars_gpu"] is not None]
        gpu_wins = [scale for scale, ratio in gpu_points if ratio < 1]
        incomplete = [scale for scale in scales if totals[scale]["polars_gpu"] is None]
        lines += ["", ("Polars GPU has a lower sum of six query medians than CPU Polars at " + ", ".join(gpu_wins) + "."
                       if gpu_wins else "No overall Polars GPU win over CPU Polars was observed at the scales with a complete Polars GPU sum: "
                       + (", ".join(scale for scale, _ in gpu_points) or "none") + ".")]
        if incomplete:
            lines += ["", "Polars GPU has no six-query aggregate at " + ", ".join(incomplete) +
                         " because at least one query failed, was invalid or mismatched; see the failure table. A failed query is not a win."]
    if evidence["excluded_scales"]:
        lines += ["", f"**{len(evidence['excluded_scales'])} selected scale(s) were excluded from timing comparisons.** "
                  "Their failures or incomplete evidence are listed below. An OOM, timeout, or missing result is not a latency win."]
    lines += ["", "The aggregate is the sum of the six per-query medians, not a directly measured complete-suite runtime. "
              "GPU / Polars is GPU elapsed time divided by Polars elapsed time: below 1 favors GPU Spark. "
              "Every included scale passes the original strict output and final GPU-plan validator for all six queries and all saved trials."]
    if ext:
        lines += ["", "Ratios involving Polars GPU (`Polars / Polars GPU`, `Polars GPU / GPU Spark`) divide the first engine's time by the second's: above 1 favors the "
                  "GPU engine in the denominator. Polars GPU is validated in strict mode (`raise_on_fail=True`); its failures, invalid evidence and output mismatches "
                  "are reported per cell and never enter a sum, ratio or bracket."]
    lines += ["", "| Scale | Trials/query | " + " | ".join(f"{ENGINE_LABELS.get(e, e)} sum (s)" for e in engines) + " | " + " | ".join(l for l, _, _ in pairs) + " |",
              "|---|---:|" + "---:|" * (len(engines) + len(pairs))]
    for scale in scales:
        t = totals[scale]
        cells = [f"{t[e]:.3f}" if t[e] is not None else f"n/a ({sums[(scale, e)][3]})" for e in engines]
        ratios = [ratio_text(safe_ratio(t[num], t[den]), 3) for _, num, den in pairs]
        lines.append(f"| {scale} | {sessions[scale]['polars']['runs']} | " + " | ".join(cells) + " | " + " | ".join(ratios) + " |")
    if spark_pair:
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
    if gpu_points or (ext and "polars_gpu" in engines):
        lines += ["", "## Polars GPU against Polars CPU brackets", "",
                  "Same bracket rule using Polars GPU time / Polars CPU time at scales where Polars GPU has a complete aggregate (per query: scales where it ran). "
                  "A scale or query where Polars GPU failed contributes no observation and cannot be a bracket endpoint.", ""]
        overall = brackets(gpu_points, "Polars GPU")
        lines.append("Overall: " + ("; ".join(overall) if overall else "no observed change from Polars CPU winning to Polars GPU winning, or back, across the observations with a complete aggregate."))
        lines += ["", "| Query | Observed scales where Polars GPU is faster than Polars CPU | Observed winner-change brackets | Scales where Polars GPU did not run |",
                  "|---|---|---|---|"]
        for query in QUERIES:
            group = [r for r in rows if r["query"] == query]
            qpoints = [(r["scale"], r["polars_gpu_median_seconds"] / r["polars_median_seconds"]) for r in group
                       if r["polars_gpu_median_seconds"] is not None]
            q_wins = [scale for scale, ratio in qpoints if ratio < 1]
            lost = [r["scale"] for r in group if r["polars_gpu_median_seconds"] is None]
            lines.append(f"| {query} | {', '.join(q_wins) or 'none'} | {'; '.join(brackets(qpoints, 'Polars GPU')) or 'none observed'} | {', '.join(lost) or 'none'} |")
    per_query = [(l, n, d) for l, n, d in pairs if l != "CPU Spark / GPU"]
    lines += ["", "## Per-query measured times", "", "Median [minimum, maximum] seconds; ranges describe the saved trials.", "",
              "| Scale / query | " + " | ".join(ENGINE_LABELS.get(e, e) for e in engines) + " | " + " | ".join(l for l, _, _ in per_query) + " |",
              "|---|" + "---:|" * (len(engines) + len(per_query))]
    for row in rows:
        values = [row_cell(row, e) for e in engines]
        ratios = [ratio_text(safe_ratio(row[num + "_median_seconds"], row[den + "_median_seconds"]), 3) for _, num, den in per_query]
        lines.append(f"| {row['scale']} / {row['query']} | " + " | ".join(values) + " | " + " | ".join(ratios) + " |")
    if ext:
        lines += [""] + matrix_lines(rows, engines) + agreement_lines(evidence) + gpu_memory_lines(rows, engines)
    lines += ["", "## Startup and observed process time", "",
              "Query timing excludes engine startup, table setup, warmup and plan serialization. Launch-to-ready includes session/table setup; "
              "do not add these overlapping fields. Process wall time includes startup, all warmups and measured trials, serialization and shutdown; "
              "it is not a single six-query latency measurement.", "",
              "| Scale / engine | Launch → ready (s) | Session + table setup (s) | Warmup sum (s) | Process wall (s) |",
              "|---|---:|---:|---:|---:|"]
    for scale, loaded in sessions.items():
        for engine in engines:
            session = loaded.get(engine)
            item = schedule_for(provenance[scale]["schedule"], scale, engine)
            if session is None:
                issue = problem(index, scale, engine, None)
                lines.append(f"| {scale} / {engine} | {problem_cell(issue['kind'], issue['reason']) if issue else 'FAILED'} | unavailable | unavailable | {duration(item.get('process_wall_seconds'))} |")
                continue
            warmup = sum(r["seconds"] for (_, phase, _), r in session["indexed_records"].items() if phase == "warmup")
            lines.append(f"| {scale} / {engine} | {duration(session.get('launch_to_engine_ready_seconds'))} | "
                         f"{duration(session.get('session_and_table_setup_seconds'))} | {warmup:.3f} | {duration(item.get('process_wall_seconds'))} |")
    if ext:
        profiles = sorted({str(s["spark_gpu_profile"]) for loaded in sessions.values() for s in loaded.values() if s.get("spark_gpu_profile")})
        lines += ["", "## Engine versions and configuration", "",
                  "| Scale / engine | Version | Configuration |", "|---|---|---|"]
        for scale, loaded in sessions.items():
            for engine in engines:
                if engine in loaded:
                    lines.append(f"| {scale} / {engine} | {loaded[engine].get('version', 'unavailable')} | {config_text(engine, loaded[engine])} |")
        if len(profiles) > 1:
            lines += ["", f"**Mixed Spark GPU profiles across scales ({', '.join(profiles)}).** Curves that cross profiles are not one controlled series."]
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
    if ext:
        lines += [""] + failure_lines(evidence)[:-1]
    versions = sorted({str(source.get("manifest", {}).get("duckdb_version", "unrecorded"))
                       for scale, source in provenance.items() if scale in sessions})
    lines += ["", "## Interpretation and limits", "",
              host_bullet(sessions, provenance, ext),
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
              "No crossover is extrapolated beyond the complete, validated measurements."]
    if ext and "polars_gpu" in engines:
        lines += ["- Polars GPU runs the cuDF GPU engine with `raise_on_fail=True`: a query that cannot run fully on the GPU is a recorded failure, not a CPU timing. "
                  "Timings include host-to-device and device-to-host transfer inside the collected action and exclude conversion to Python dictionaries; "
                  "a failure at one scale (for example device-memory exhaustion) does not predict other scales."]
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engines", nargs="+", choices=ENGINE_ORDER,
                        help="analyze exactly these engines (must include polars); default: original three plus any discovered")
    args = parser.parse_args()
    roots = [root.expanduser().resolve() for root in args.results]
    for root in roots:
        if not root.is_dir():
            parser.error(f"Result root is not a directory: {root}")
    if args.engines and "polars" not in args.engines:
        parser.error("--engines must include polars, the correctness baseline")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    sessions, evidence, provenance = collect(roots, args.engines)
    engines = tuple(evidence.get("engines", LEGACY_ENGINES))
    rows = summarize(sessions, engines, evidence.get("engine_failures", ()))
    (output / "correctness.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    with (output / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_columns(engines, gpu_memory_engines(sessions)))
        writer.writeheader()
        writer.writerows(rows)
    if is_extended(engines):
        write_pairwise_csv(output / "pairwise_speedups.csv", rows, engines)
    target = output / "CROSSOVER_REPORT.md"
    target.write_text(report(sessions, evidence, provenance, rows))
    for scale in evidence["excluded_scales"]:
        print(f"Excluded {scale['scale']} ({scale['source_root']}): " + "; ".join(scale["errors"]), file=sys.stderr)
    print(f"Validated {len(sessions)} scales; excluded {len(evidence['excluded_scales'])}; report: {target}")
    violations = evidence.get("integrity_violations", [])
    for item in violations:
        print(f"{item['scale']}/{item['engine']}/{item['query'] or 'session'}: {item['kind']}: {item['reason']}", file=sys.stderr)
    return 0 if evidence["passed"] and not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
