#!/usr/bin/env python3
"""Validate the separate complex suite, including honest hybrid/fallback plans.

Only complete query/scale cases across the required engines enter timing tables.
An incomplete case cannot suppress other verified cases or create a full-suite
score. Optional DuckDB oracle files apply only to their matching input scale.

The original three engines (polars, spark_cpu, spark_gpu) are always required.
When polars_gpu results exist (Polars with the cuDF GPU engine, strict mode) it is
required in every scale; its failures, invalid strict-GPU evidence and output
mismatches are reported per case as explicit cells and never exclude the case for
the other engines. --engines selects an explicit subset (must include polars).
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

from analyze_results import (ABS_TOL, ENGINE_LABELS, ENGINE_ORDER, EXTRA_RATIOS, LEGACY_ENGINES, REL_TOL,
                             IntegrityError, add_extra_ratios, agreement_lines, check_polars_gpu_metadata, config_text, incomplete_note,
                             compare_values, discover_engines, failure_entry, failure_lines, gpu_memory_lines,
                             host_shape, is_extended, matrix_lines, polars_gpu_warning_findings, problem_cell, ratio_text,
                             read_environment, read_schedule, resolve_engines, row_cell, safe_ratio,
                             write_pairwise_csv)
import complex_workloads as suite


QUERY_IDS = tuple(suite.QUERY_IDS)
COMPLEX_LABELS = {**ENGINE_LABELS, "polars": "Polars CPU", "spark_gpu": "GPU-plugin Spark"}
SHARED_CONF = ("spark.master", "spark.driver.memory", "spark.sql.shuffle.partitions",
               "spark.sql.adaptive.enabled", "spark.sql.files.maxPartitionBytes",
               "spark.sql.session.timeZone", "spark.driver.maxResultSize")
CPU_CORE = {
    "FileScan", "BatchScan", "Scan", "RowDataSourceScan", "InMemoryTableScan",
    "Project", "Filter", "HashAggregate", "ObjectHashAggregate", "SortAggregate",
    "Sort", "Window", "WindowGroupLimit", "BroadcastHashJoin", "SortMergeJoin",
    "ShuffledHashJoin", "BroadcastNestedLoopJoin", "CartesianProduct", "Union",
    "Expand", "Generate", "TakeOrderedAndProject", "CollectLimit", "GlobalLimit",
    "LocalLimit", "Sample", "Aggregate", "PythonEval", "BatchEvalPython",
    "ArrowEvalPython", "FlatMapGroupsInPandas", "MapInPandas",
}


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def finite_number(value, name, positive=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite numeric")
    if value <= 0 if positive else value < 0:
        raise ValueError(f"{name} has invalid sign")
    return value


def scale_key(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("invalid scale factor")
    return number


def cleanup_policy(session):
    value = session.get("query_cleanup_policy")
    if value is None or value == "none":
        return "none"
    if value != "verified_gc":
        raise ValueError("unknown query cleanup policy")
    return value


def inspect_cleanup(item, engine):
    if not isinstance(item, dict) or item.get("policy") != "verified_gc" or item.get("status") != "passed":
        raise ValueError("cleanup record is missing a passed verified_gc status")
    finite_number(item.get("seconds"), "cleanup duration", positive=False)
    if not isinstance(item.get("query"), str) or isinstance(item.get("trial"), bool) or not isinstance(item.get("trial"), int):
        raise ValueError("cleanup query/trial types are invalid")
    for phase in ("scratch_before", "scratch_after"):
        scratch = item.get(phase)
        if not isinstance(scratch, dict):
            raise ValueError(f"cleanup {phase} counts are missing")
        for key in ("files", "bytes"):
            value = scratch.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"cleanup {phase}.{key} must be a nonnegative integer")
    if engine.startswith("spark_") and (item["scratch_after"]["files"] != 0 or item["scratch_after"]["bytes"] != 0):
        raise ValueError("verified_gc left owned Spark shuffle files or bytes after a query")
    return {key: item[key] for key in ("query", "phase", "trial", "policy", "status", "seconds", "scratch_before", "scratch_after")}


def final_plan(path):
    text = path.read_text()
    if "AdaptiveSparkPlan" in text and "isFinalPlan=true" not in text:
        raise ValueError("AQE plan is not marked isFinalPlan=true")
    if "== Final Plan ==" in text:
        text = text.split("== Final Plan ==", 1)[1].split("== Initial Plan ==", 1)[0]
    return text


def inspect_plan(path):
    text = final_plan(path)
    gpu, cpu = Counter(), Counter()
    for line in text.splitlines():
        node = line.lstrip(" |:+-!")
        node = re.sub(r"^\*?\(\d+\)\s*", "", node)
        match = re.match(r"([A-Za-z][A-Za-z0-9_]*)\b", node)
        if not match:
            continue
        name = match.group(1)
        if name.startswith("Gpu"):
            gpu[name] += 1
        elif name in CPU_CORE:
            cpu[name] += 1
    classification = "hybrid" if gpu and cpu else "gpu_operators_present" if gpu else "cpu_fallback"
    return {"source": str(path), "gpu_operators": dict(sorted(gpu.items())),
            "cpu_core_operators": dict(sorted(cpu.items())),
            "gpu_execution_class": classification}


def discover(roots):
    selected, overrides, sources = {}, [], []
    for raw in roots:
        root = raw.resolve()
        source = {"root": str(root), "scales": [], "errors": []}
        if not root.is_dir():
            source["errors"].append("results root is missing")
        else:
            for directory in sorted(root.glob("sf*")):
                if not directory.is_dir():
                    continue
                try:
                    number = scale_key(directory.name[2:].replace("p", "."))
                except ValueError:
                    continue
                if number in selected:
                    overrides.append({"scale": directory.name, "replaced_root": str(selected[number][0]),
                                      "selected_root": str(root)})
                selected[number] = (root, directory)
                source["scales"].append(directory.name)
        sources.append(source)
    return selected, overrides, sources


def load_oracles(paths):
    oracles, errors = {}, []
    for path in paths:
        try:
            value = read_json(path)
            if value.get("suite") != "complex" or value.get("suite_version") != suite.SUITE_VERSION:
                raise ValueError("oracle suite/version differs from registry")
            if not value.get("completed_at_utc"):
                raise ValueError("oracle is incomplete")
            datetime.fromisoformat(value["completed_at_utc"])
            number = scale_key(value["scale_factor"])
            if number in oracles:
                raise ValueError("multiple oracle files for one scale are ambiguous")
            if not re.fullmatch(r"[0-9a-f]{64}", value.get("manifest_sha256", "")):
                raise ValueError("oracle manifest SHA-256 missing/invalid")
            queries = value.get("queries", {})
            if not queries or any(q not in QUERY_IDS for q in queries):
                raise ValueError("oracle query set is empty or contains unknown IDs")
            for query, result in queries.items():
                expected_hash = hashlib.sha256(suite.spark_sql(query).encode()).hexdigest()
                if result.get("query_sha256") != expected_hash:
                    raise ValueError(f"oracle SQL SHA-256 differs from current registry: {query}")
                if not isinstance(result.get("rows"), list) or not all(isinstance(row, dict) for row in result["rows"]):
                    raise ValueError(f"oracle result rows missing/invalid: {query}")
            value["source"] = str(path.resolve())
            oracles[number] = value
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append({"source": str(path), "error": str(exc)})
    return oracles, errors


def load_session(root, directory, engine, schedule):
    path = directory / engine / "results.json"
    value = read_json(path)
    if value.get("engine") != engine or value.get("suite") != "complex":
        raise ValueError("engine or suite metadata differs from selected case")
    if engine == "polars_gpu":
        check_polars_gpu_metadata(value)
    if value.get("suite_version", suite.SUITE_VERSION) != suite.SUITE_VERSION:
        raise ValueError("suite version differs from current registry")
    if not value.get("completed_at_utc") or value.get("active_query"):
        raise ValueError("engine session is incomplete or has an active query")
    datetime.fromisoformat(value["completed_at_utc"])
    if value.get("failed_at_utc") or value.get("failure"):
        raise ValueError("engine session records a global failure")
    if value.get("cleanup_failure") or value.get("cleanup_failures"):
        raise ValueError("engine session records a cleanup protocol failure")
    policy = cleanup_policy(value)
    if policy == "verified_gc" and not isinstance(value.get("cleanup_records"), list):
        raise ValueError("verified_gc session lacks cleanup_records")
    runs = value.get("runs")
    if isinstance(runs, bool) or runs not in (1, 3):
        raise ValueError("expected three measured trials or one smoke trial")
    queries = value.get("queries", [])
    if not queries or len(set(queries)) != len(queries) or any(q not in QUERY_IDS for q in queries):
        raise ValueError("invalid or missing declared query IDs")
    saved_records = value.get("records")
    if not isinstance(saved_records, list) or any(not isinstance(record, dict) for record in saved_records):
        raise ValueError("saved query records must be a list of objects")
    if policy == "verified_gc":
        cleanup_slots = Counter()
        for item in value["cleanup_records"]:
            inspected = inspect_cleanup(item, engine)
            if inspected["query"] not in queries:
                raise ValueError("cleanup references an undeclared query")
            if (inspected["phase"], inspected["trial"]) not in {("warmup", 0), *[("measured", i) for i in range(1, runs + 1)]} or isinstance(inspected["trial"], bool):
                raise ValueError("cleanup has an invalid phase/trial")
            cleanup_slots[(inspected["query"], inspected["phase"], inspected["trial"])] += 1
        saved_slots = Counter((record.get("query"), record.get("phase"), record.get("trial")) for record in saved_records)
        if cleanup_slots != saved_slots or any(count != 1 for count in cleanup_slots.values()):
            raise ValueError("verified_gc does not have exactly one passed cleanup for every saved output")
    finite_number(value.get("session_and_table_setup_seconds"), "setup seconds", positive=False)
    if value.get("launch_to_engine_ready_seconds") is not None:
        finite_number(value["launch_to_engine_ready_seconds"], "launch-to-ready seconds", positive=False)
    cpu_threads = value.get("cpu_threads")
    if isinstance(cpu_threads, bool) or not isinstance(cpu_threads, int) or cpu_threads < 1:
        raise ValueError("invalid CPU thread allocation")
    number = scale_key(value["scale_factor"])
    matches = [item for item in schedule if item.get("engine") == engine
               and scale_key(item["scale"]) == number]
    if len(matches) != 1 or matches[0].get("returncode") != 0 or matches[0].get("status") != "complete":
        raise ValueError("engine schedule is missing, ambiguous, failed, or incomplete")
    finite_number(matches[0].get("process_wall_seconds"), "process wall seconds")
    if engine == "spark_gpu":
        conf = value.get("spark_conf", {})
        plugins = [part.strip() for part in conf.get("spark.plugins", "").split(",")]
        if "com.nvidia.spark.SQLPlugin" not in plugins or str(conf.get("spark.rapids.sql.enabled")).lower() != "true":
            raise ValueError("GPU Spark session does not establish enabled NVIDIA plugin")
    elif engine == "polars" and value.get("polars_execution_engine") != "streaming":
        raise ValueError("expected Polars CPU streaming engine")
    value["source_results"] = str(path.resolve())
    value["source_root"] = str(root)
    value["schedule_item"] = matches[0]
    return value


def case_records(directory, engine, session, query):
    expected = {(query, "warmup", 0)} | {(query, "measured", n) for n in range(1, session["runs"] + 1)}
    failures = [v for key in ("query_errors", "query_failures") for v in session.get(key, []) if v.get("query") == query]
    if failures:
        raise ValueError("query execution errors: " + json.dumps(failures, sort_keys=True))
    cleanups = {}
    if cleanup_policy(session) == "verified_gc":
        for item in session["cleanup_records"]:
            if item.get("query") != query:
                continue
            key = (query, item.get("phase"), item.get("trial"))
            if key not in expected or key in cleanups or isinstance(item.get("trial"), bool):
                raise ValueError("unexpected or duplicate cleanup phase/trial")
            cleanups[key] = inspect_cleanup(item, engine)
        if cleanups.keys() != expected:
            raise ValueError(f"missing verified_gc cleanup records: {sorted(expected - cleanups.keys())}")
    records = {}
    for record in session.get("records", []):
        if record.get("query") != query:
            continue
        key = (query, record.get("phase"), record.get("trial"))
        if key not in expected or key in records or isinstance(record.get("trial"), bool):
            raise ValueError("unexpected/duplicate phase or trial")
        finite_number(record.get("seconds"), "query duration")
        if not isinstance(record.get("rows"), list) or not all(isinstance(row, dict) for row in record["rows"]):
            raise ValueError("missing or malformed materialized result rows")
        if "peak_gpu_memory_bytes" in record and record["peak_gpu_memory_bytes"] is not None:
            finite_number(record["peak_gpu_memory_bytes"], "peak GPU memory", positive=False)
        if engine == "polars_gpu" and record.get("gpu_execution_class") != "gpu_engine_strict":
            raise IntegrityError(f"gpu_execution_class is {record.get('gpu_execution_class')!r}, expected 'gpu_engine_strict'")
        path = None if engine == "polars_gpu" and "plan" not in record else (directory / engine / record["plan"]).resolve()
        if path is not None and (not path.is_relative_to((directory / engine).resolve()) or not path.is_file()):
            raise ValueError("saved plan missing or outside engine results")
        inspected = inspect_plan(path) if engine.startswith("spark_") else None
        if engine == "spark_cpu" and inspected["gpu_operators"]:
            raise ValueError("CPU Spark baseline unexpectedly has GPU plan operators")
        records[key] = {"record": record, "plan": inspected, "cleanup": cleanups.get(key)}
    if records.keys() != expected:
        raise ValueError(f"incomplete case; missing {sorted(expected - records.keys())}")
    return records


def query_error_reason(session, query):
    """Readable 'Type: message' for the first recorded error of one query, else None."""
    for key in ("query_errors", "query_failures"):
        for item in session.get(key) or []:
            if isinstance(item, dict) and item.get("query") == query:
                return f"{item.get('type', 'error')}: {item.get('message', '')}".strip()
    return None


def check_engine_consistency(engine, session, baseline):
    for field in ("manifest_sha256", "scale_factor", "runs", "cpu_threads", "cache_policy", "timing_scope", "suite"):
        if session.get(field) != baseline.get(field):
            raise ValueError(f"{engine}: {field} differs from Polars")
    if set(session["queries"]) != set(baseline["queries"]):
        raise ValueError(f"{engine}: declared query set differs")
    if cleanup_policy(session) != cleanup_policy(baseline):
        raise ValueError(f"{engine}: query_cleanup_policy differs from Polars")


def optional_engine_warnings(label, engine, session, baseline):
    """Soft differences for non-legacy engines: reported, never invalidating."""
    found = []
    for field in ("cpu_threads", "timing_scope"):
        if session.get(field) != baseline.get(field):
            found.append({"scale": label, "engine": engine,
                          "warning": f"{field} differs from Polars: {session.get(field)!r} vs {baseline.get(field)!r}"})
    if engine == "polars_gpu" and session.get("version") != baseline.get("version"):
        found.append({"scale": label, "engine": engine,
                      "warning": f"Polars version differs: {session.get('version')!r} vs {baseline.get('version')!r}"})
    return found


def optional_check_engine_consistency(engine, session, baseline):
    """Non-legacy engines need the same dataset, trial count and protocol, but not identical thread/timing text."""
    for field in ("manifest_sha256", "scale_factor", "runs", "cache_policy", "suite"):
        if session.get(field) != baseline.get(field):
            raise ValueError(f"{engine}: {field} differs from Polars")
    if set(session["queries"]) != set(baseline["queries"]):
        raise ValueError(f"{engine}: declared query set differs")
    if cleanup_policy(session) != cleanup_policy(baseline):
        raise ValueError(f"{engine}: query_cleanup_policy differs from Polars")


def analyze(roots, oracle_paths, requested_engines=None):
    selected, overrides, sources = discover(roots)
    oracles, oracle_errors = load_oracles(oracle_paths)
    discovered = set()
    for root, directory in selected.values():
        discovered |= discover_engines(directory, read_schedule(root))
    engines = resolve_engines(requested_engines, discovered)
    extended = is_extended(engines)
    evidence = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "suite": "complex", "suite_version": suite.SUITE_VERSION,
        "scope": "Only included complete query/scale cases across all three engines are verified",
        "source_roots": sources, "overrides": overrides, "duplicate_scale_policy": "last root wins; no fallback",
        "relative_tolerance": REL_TOL, "absolute_tolerance": ABS_TOL, "integer_comparison": "exact integer pairs",
        "included_cases": [], "excluded_cases": [], "not_selected_cases": [],
        "comparisons": [], "gpu_plans": [], "warnings": [], "session_errors": [],
        "oracle_comparisons": [], "oracle_errors": oracle_errors,
        "oracle_scope": {"provided_files": [str(p.resolve()) for p in oracle_paths],
                         "verified_cases": [], "uncovered_cases": [],
                         "note": "Independent DuckDB checks apply only to matching manifest/SQL/scale cases; other cases use Polars warmup cross-engine agreement"},
        "datasets": [], "sessions": [], "full_suite_totals": [], "cleanup_checks": [],
        "cleanup_scope": "verified_gc barriers occur after each execute returns, outside query timers; explicit barrier duration is reported separately and remains inside process wall time. Legacy none sessions have no verified barrier measurement."}
    if extended:
        evidence["scope"] = ("Only included complete query/scale cases across the required engines are verified; "
                             "non-legacy engine failures are recorded per case without excluding the case")
        evidence["engines"] = list(engines)
        evidence["engine_failures"], evidence["engine_warnings"], evidence["polars_gpu_sessions"] = [], [], []
        for source in sources:
            source["host"] = host_shape(read_environment(source["root"]))
    if discovered - set(engines):
        evidence["engines_excluded_by_flag"] = sorted(discovered - set(engines))
    rows = []
    agreement = {}
    if not selected:
        evidence["session_errors"].append({"error": "No sf* result directories found"})
    for number, (root, directory) in sorted(selected.items()):
        label = directory.name
        loaded, load_errors, scale_failures = {}, [], []
        try:
            schedule = read_json(root / "schedule.json")
            if not isinstance(schedule, list):
                raise ValueError("schedule.json must contain a list")
        except (OSError, ValueError, TypeError) as exc:
            schedule = []
            load_errors.append(f"schedule: {exc}")
        for engine in engines:
            try:
                loaded[engine] = load_session(root, directory, engine, schedule)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                if engine in LEGACY_ENGINES:
                    load_errors.append(f"{engine}: {exc}")
                else:
                    reason = f"results.json unavailable ({type(exc).__name__})" if isinstance(exc, OSError) else exc
                    scale_failures.append(failure_entry(label, engine, None,
                        "invalid" if isinstance(exc, IntegrityError) else "failed", reason))
        requested = set(q for s in loaded.values() for q in s["queries"]) or set(QUERY_IDS)
        for query in set(QUERY_IDS) - requested:
            evidence["not_selected_cases"].append({"scale": label, "query": query})
        if not load_errors:
            baseline = loaded["polars"]
            try:
                manifest_path = directory / "manifest.json"
                manifest = read_json(manifest_path)
                digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                if digest != baseline.get("manifest_sha256") or scale_key(manifest["scale_factor"]) != number:
                    raise ValueError("manifest SHA-256 or scale differs from session")
                for engine in [e for e in loaded if e not in LEGACY_ENGINES]:
                    try:
                        optional_check_engine_consistency(engine, loaded[engine], baseline)
                        evidence.get("engine_warnings", []).extend(optional_engine_warnings(label, engine, loaded[engine], baseline))
                    except (ValueError, KeyError, TypeError) as exc:
                        scale_failures.append(failure_entry(label, engine, None, "invalid", exc))
                        del loaded[engine]
                for engine, session in loaded.items():
                    if engine not in LEGACY_ENGINES:
                        continue
                    check_engine_consistency(engine, session, baseline)
                if "spark_cpu" in loaded and "spark_gpu" in loaded:
                    for key in SHARED_CONF:
                        a, b = loaded["spark_cpu"].get("spark_conf", {}), loaded["spark_gpu"].get("spark_conf", {})
                        if key not in a or key not in b or a[key] != b[key]:
                            raise ValueError(f"recorded Spark control differs or is missing: {key}")
                    if loaded["spark_cpu"].get("version") != loaded["spark_gpu"].get("version"):
                        raise ValueError("Spark engine versions differ")
                if number in oracles and oracles[number]["manifest_sha256"] != digest:
                    raise ValueError("matching-scale oracle manifest SHA-256 differs")
                evidence["datasets"].append({"scale": label, "scale_factor": number,
                    "manifest_sha256": digest, "total_parquet_bytes": manifest.get("total_parquet_bytes"),
                    "lineitem_rows": manifest.get("tables", {}).get("lineitem", {}).get("rows"),
                    "source_root": str(root)})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                load_errors.append(str(exc))
        for engine, session in loaded.items():
            evidence["sessions"].append({"scale": label, "engine": engine,
                "version": session.get("version"), "cpu_threads": session["cpu_threads"],
                "source_results": session["source_results"],
                "launch_to_engine_ready_seconds": session.get("launch_to_engine_ready_seconds"),
                "session_and_table_setup_seconds": session["session_and_table_setup_seconds"],
                "warmup_query_sum_seconds": sum(r.get("seconds", 0) for r in session.get("records", []) if r.get("phase") == "warmup"),
                "process_wall_seconds": session["schedule_item"]["process_wall_seconds"],
                "query_errors": session.get("query_errors", []),
                "query_cleanup_policy": cleanup_policy(session),
                "explicit_cleanup_count": len(session.get("cleanup_records", [])) if cleanup_policy(session) == "verified_gc" else 0,
                "explicit_cleanup_sum_seconds": sum(c.get("seconds", 0) for c in session.get("cleanup_records", [])) if cleanup_policy(session) == "verified_gc" else None})
            if extended:
                evidence["sessions"][-1]["configuration"] = config_text(engine, session)
            if engine == "polars_gpu":
                evidence["engine_warnings"].extend(polars_gpu_warning_findings(label, session))
                evidence["polars_gpu_sessions"].append({"scale": label, "polars_gpu": session.get("polars_gpu"),
                    "version": session.get("version"), "gpu_inventory": session.get("gpu_inventory"),
                    "query_execution_status": session.get("query_execution_status")})
        evidence["session_errors"].extend({"scale": label, "error": error} for error in load_errors)
        if extended:
            evidence["engine_failures"].extend(scale_failures)
        scale_rows = []
        for query in QUERY_IDS:
            if query not in requested:
                continue
            reasons = list(load_errors)
            records, comparisons, plans, oracle_checks, warnings, cleanup_checks = {}, [], [], [], [], []
            degraded = {failure["engine"]: failure for failure in scale_failures}
            if not reasons:
                for engine in engines:
                    if engine not in loaded:
                        continue
                    try:
                        records[engine] = case_records(directory, engine, loaded[engine], query)
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        if engine in LEGACY_ENGINES:
                            reasons.append(f"{engine}: {exc}")
                        else:
                            degraded[engine] = failure_entry(label, engine, query,
                                "invalid" if isinstance(exc, IntegrityError) else "failed",
                                query_error_reason(loaded[engine], query) or exc)
            if not reasons:
                baseline_rows = records["polars"][(query, "warmup", 0)]["record"]["rows"]
                oracle = oracles.get(number, {}).get("queries", {}).get(query)
                for engine in engines:
                    if engine not in records:
                        continue
                    bad = []
                    for key, item in records[engine].items():
                        record = item["record"]
                        if item["cleanup"] is not None:
                            cleanup_checks.append({"scale": label, "engine": engine, **item["cleanup"]})
                        check = compare_values(baseline_rows, record["rows"])
                        comparisons.append({"scale": label, "query": query, "engine": engine,
                            "phase": key[1], "trial": key[2], **check})
                        if engine == "polars_gpu":
                            entry = agreement.setdefault((label, query), {"scale": label, "query": query,
                                "comparisons": 0, "failed_comparisons": 0,
                                "max_relative_numeric_difference": 0.0, "max_absolute_numeric_difference": 0.0})
                            entry["comparisons"] += 1
                            entry["failed_comparisons"] += not check["passed"]
                            entry["max_relative_numeric_difference"] = max(entry["max_relative_numeric_difference"], check["max_relative_numeric_difference"])
                            entry["max_absolute_numeric_difference"] = max(entry["max_absolute_numeric_difference"], check["max_absolute_numeric_difference"])
                        if not check["passed"]:
                            if engine in LEGACY_ENGINES:
                                reasons.append(f"{engine}/{key[1]}/{key[2]} differs from Polars warmup")
                            else:
                                bad.append(("differs from Polars warmup", check["max_relative_numeric_difference"]))
                        if oracle is not None:
                            check = compare_values(oracle["rows"], record["rows"])
                            oracle_checks.append({"scale": label, "query": query, "engine": engine,
                                "phase": key[1], "trial": key[2], "oracle_source": oracles[number]["source"],
                                "query_sha256": oracle["query_sha256"], **check})
                            if not check["passed"]:
                                if engine in LEGACY_ENGINES:
                                    reasons.append(f"{engine}/{key[1]}/{key[2]} differs from independent DuckDB oracle")
                                else:
                                    bad.append(("differs from independent DuckDB oracle", check["max_relative_numeric_difference"]))
                        if engine == "spark_gpu":
                            plan = {"scale": label, "query": query, "phase": key[1], "trial": key[2], **item["plan"]}
                            plan["recorded_gpu_execution_class"] = record.get("gpu_execution_class")
                            plans.append(plan)
                            if record.get("gpu_execution_class") not in (None, plan["gpu_execution_class"]):
                                warnings.append({"scale": label, "query": query, "phase": key[1], "trial": key[2],
                                    "warning": "recorded GPU classification differs; saved final plan governs",
                                    "recorded": record.get("gpu_execution_class"), "inspected": plan["gpu_execution_class"]})
                    if bad:
                        degraded[engine] = failure_entry(label, engine, query, "mismatch",
                            f"{len(bad)} saved output(s) {bad[0][0]} beyond tolerance (max relative difference {max(v for _, v in bad):.3g})")
            evidence["comparisons"].extend(comparisons)
            evidence["oracle_comparisons"].extend(oracle_checks)
            evidence["warnings"].extend(warnings)
            if reasons or oracle_errors:
                evidence["excluded_cases"].append({"scale": label, "query": query, "source_root": str(root),
                    "reasons": reasons + (["oracle input validation failed; inspect oracle_errors"] if oracle_errors else [])})
                continue
            if extended:
                evidence["engine_failures"].extend(f for f in degraded.values() if f["query"] is not None)
            usable = {engine: records[engine] for engine in engines if engine in records and engine not in degraded}
            gpu_ops = sorted({op for p in plans for op in p["gpu_operators"]})
            cpu_ops = sorted({op for p in plans for op in p["cpu_core_operators"]})
            classification = "hybrid" if gpu_ops and cpu_ops else "gpu_operators_present" if gpu_ops else "cpu_fallback"
            description = loaded["polars"].get("query_descriptions", {}).get(query, suite.DESCRIPTIONS.get(query, query))
            row = {"scale": label, "query": query, "description": description,
                   "category": suite.CATEGORIES.get(query, "Complex analytical query"),
                   "measured_trials": loaded["polars"]["runs"],
                   "query_cleanup_policy": cleanup_policy(loaded["polars"])}
            for engine in engines:
                if engine in usable:
                    timed = [item["record"] for key, item in usable[engine].items() if key[1] == "measured"]
                    times = [r["seconds"] for r in timed]
                    row[engine + "_median_seconds"] = statistics.median(times)
                    row[engine + "_min_seconds"] = min(times)
                    row[engine + "_max_seconds"] = max(times)
                    row[engine + "_max_peak_process_tree_rss_bytes"] = max((r.get("peak_process_tree_rss_bytes", 0) for r in timed), default=0)
                    engine_cleanups = [item["cleanup"] for item in usable[engine].values() if item["cleanup"] is not None]
                    row[engine + "_cleanup_count"] = len(engine_cleanups)
                    row[engine + "_cleanup_seconds"] = sum(item["seconds"] for item in engine_cleanups) if engine_cleanups else None
                    if any("peak_gpu_memory_bytes" in r for r in timed):
                        values = [r["peak_gpu_memory_bytes"] for r in timed if r.get("peak_gpu_memory_bytes") is not None]
                        row[engine + "_max_peak_gpu_memory_bytes"] = max(values) if values else None
                else:
                    for suffix in ("_median_seconds", "_min_seconds", "_max_seconds", "_max_peak_process_tree_rss_bytes",
                                   "_cleanup_count", "_cleanup_seconds"):
                        row[engine + suffix] = None
                if engine not in LEGACY_ENGINES:
                    issue = degraded.get(engine)
                    row[engine + "_status"] = issue["kind"] if issue else "ok"
                    row[engine + "_failure"] = issue["reason"] if issue else ""
            if "spark_gpu" in engines:
                row["polars_over_spark_gpu"] = row["polars_median_seconds"] / row["spark_gpu_median_seconds"]
                row["spark_cpu_over_spark_gpu"] = row["spark_cpu_median_seconds"] / row["spark_gpu_median_seconds"]
            add_extra_ratios(row, engines)
            if "spark_gpu" in engines:
                row["gpu_execution_class"] = classification
                row["gpu_execution_classes_observed"] = "|".join(sorted({p["gpu_execution_class"] for p in plans}))
                row["gpu_operators"] = "|".join(gpu_ops)
                row["cpu_core_operators"] = "|".join(cpu_ops)
            row["oracle_checked"] = bool(oracle_checks)
            rows.append(row); scale_rows.append(row)
            case = {"scale": label, "query": query, "source_root": str(root),
                    **({"gpu_execution_class": classification} if "spark_gpu" in engines else {}),
                    "query_cleanup_policy": row["query_cleanup_policy"]}
            if extended and "polars_gpu" in engines:
                case["polars_gpu_status"] = row["polars_gpu_status"]
            evidence["included_cases"].append(case)
            evidence["gpu_plans"].extend(plans)
            evidence["cleanup_checks"].extend(cleanup_checks)
            evidence["oracle_scope"]["verified_cases" if oracle_checks else "uncovered_cases"].append(case)
        if len(scale_rows) == len(QUERY_IDS) and {r["query"] for r in scale_rows} == set(QUERY_IDS):
            totals = {engine: (sum(r[engine + "_median_seconds"] for r in scale_rows)
                               if all(r[engine + "_median_seconds"] is not None for r in scale_rows) else None)
                      for engine in engines}
            total = {"scale": label, "measured_trials": scale_rows[0]["measured_trials"],
                "query_cleanup_policy": scale_rows[0]["query_cleanup_policy"],
                **{engine + "_sum_medians_seconds": value for engine, value in totals.items()}}
            if "spark_gpu" in engines:
                total["polars_over_spark_gpu"] = totals["polars"] / totals["spark_gpu"]
                total["spark_cpu_over_spark_gpu"] = totals["spark_cpu"] / totals["spark_gpu"]
            for name, numerator, denominator in EXTRA_RATIOS:
                if numerator in engines and denominator in engines:
                    total[name] = safe_ratio(totals[numerator], totals[denominator])
            if extended:
                for engine in engines:
                    if engine not in LEGACY_ENGINES:
                        total[engine + "_queries_timed"] = sum(r[engine + "_median_seconds"] is not None for r in scale_rows)
                        if totals[engine] is None:
                            total[engine + "_incomplete"] = incomplete_note(scale_rows, engine, len(scale_rows)).replace("queries", "cases")
            evidence["full_suite_totals"].append(total)
    evidence["correctness_verified"] = bool(rows)
    evidence["query_cleanup_policies"] = sorted({row["query_cleanup_policy"] for row in rows})
    evidence["mixed_cleanup_protocols"] = len(evidence["query_cleanup_policies"]) > 1
    evidence["verified_gc_cleanup_validated"] = bool(evidence["cleanup_checks"]) and "verified_gc" in evidence["query_cleanup_policies"]
    strict_gpu = "spark_gpu" not in engines and any(row.get("polars_gpu_status") == "ok" for row in rows)
    evidence["gpu_plan_coverage_verified"] = bool(rows) and (bool(evidence["gpu_plans"]) or strict_gpu)
    evidence["gpu_operator_presence_observed"] = any(p["gpu_operators"] for p in evidence["gpu_plans"])
    evidence["gpu_execution_verified"] = evidence["gpu_operator_presence_observed"] or strict_gpu
    evidence["gpu_coverage_scope"] = "Named operators in saved final plans; CPU fallback is valid product behavior; no runtime-fraction or full-GPU claim"
    evidence["all_selected_cases_validated"] = bool(rows) and not evidence["excluded_cases"] and not evidence["session_errors"] and not oracle_errors
    evidence["all_full_suites_validated"] = bool(selected) and len(evidence["full_suite_totals"]) == len(selected)
    evidence["passed"] = evidence["correctness_verified"] and evidence["gpu_plan_coverage_verified"]
    if extended:
        evidence["polars_gpu_numeric_agreement"] = list(agreement.values())
        evidence["polars_gpu_agreement_note"] = ("max_relative_numeric_difference is the largest relative difference to the Polars warmup over all compared "
                                                 f"numeric values (tolerance: relative {REL_TOL:g}, absolute {ABS_TOL:g}); it is reported, not loosened.")
        evidence["integrity_violations"] = [f for f in evidence["engine_failures"] if f["kind"] in ("invalid", "mismatch")]
        evidence["correctness_verified"] = evidence["correctness_verified"] and not evidence["integrity_violations"]
        evidence["passed"] = evidence["passed"] and not evidence["integrity_violations"]
        evidence["all_selected_cases_validated"] = evidence["all_selected_cases_validated"] and not evidence["engine_failures"]
        evidence["all_full_suites_validated"] = evidence["all_full_suites_validated"] and all(
            total.get(engine + "_sum_medians_seconds") is not None
            for total in evidence["full_suite_totals"] for engine in engines)
    return rows, evidence


def report(rows, evidence):
    engines = tuple(evidence.get("engines", LEGACY_ENGINES))
    ext = is_extended(engines)
    labels = {**COMPLEX_LABELS, "spark_gpu": "GPU Spark"} if ext else COMPLEX_LABELS
    spark = "spark_gpu" in engines
    ratio_specs = ([("Polars / GPU", "polars_over_spark_gpu"), ("CPU Spark / GPU", "spark_cpu_over_spark_gpu")] if spark else [])
    if ext:
        ratio_specs = [(label, key) for label, key, num, den in (
            ("Polars CPU / GPU Spark", "polars_over_spark_gpu", "polars", "spark_gpu"),
            ("CPU Spark / GPU Spark", "spark_cpu_over_spark_gpu", "spark_cpu", "spark_gpu"),
            ("Polars CPU / Polars GPU", "polars_over_polars_gpu", "polars", "polars_gpu"),
            ("Polars GPU / GPU Spark", "polars_gpu_over_spark_gpu", "polars_gpu", "spark_gpu")) if num in engines and den in engines]
    title = ("Four-engine" if len(engines) == 4 else f"{len(engines)}-engine") + " complex-query comparison"
    text = ["# " + title if ext else "# Complex-query experiment", "",
        "This separate suite tests six joins, nested-query, window, exact-distinct, and self-join cases. "
        "Its score is not added to the original six-query experiment." +
        (" Engines: " + ", ".join(labels[e] for e in engines) + ". Polars GPU uses the cuDF engine for Polars; "
         "GPU Spark uses NVIDIA's Spark plugin. These are separate GPU engines." if ext else ""), "",
        f"Verified cases: **{len(rows)}**; excluded incomplete/invalid cases: **{len(evidence['excluded_cases'])}**. " +
        ("Only included cases enter timing tables. All saved warmup and measured outputs in each included case agree "
         if not ext else "Only validated engine cells carry timings. Failed, invalid or mismatching Polars GPU cells remain explicit without timings; "
         "each retained cell's saved warmup and measured outputs agree ") +
        f"with its Polars warmup baseline (relative tolerance {REL_TOL:g}, absolute {ABS_TOL:g}; integer pairs exact).", "",
        "Times are per-query median [minimum, maximum] seconds. " +
        ("Each ratio divides the first named engine's time by the second's; above 1 favors the second engine. "
         "GPU Spark coverage is classified separately from strict Polars GPU execution."
         if ext else "Ratios are CPU-engine time divided by GPU-plugin Spark time; values above 1 favor the GPU-plugin session. "
         "That session can contain GPU operators, a hybrid plan, or CPU fallback."), "",
        "Resource protocol is part of the result: `none` is the preserved legacy session method; `verified_gc` "
        "adds a verified post-query resource barrier outside query timers. Do not silently combine those methods into one uniform scaling series.", "",
        "| Scale / query | Type | Resource protocol | " + " | ".join(labels.get(e, e) for e in engines) + " | " +
        " | ".join(label for label, _ in ratio_specs) + ((" | GPU Spark coverage |" if ext else " | Inspected coverage |") if spark else " |"),
        "|---|---|---|" + "---:|" * (len(engines) + len(ratio_specs)) + ("---|" if spark else "")]
    for row in rows:
        cells = [row_cell(row, e) for e in engines]
        text.append(f"| {row['scale']} / {row['query']} | {row['category']} | {row['query_cleanup_policy']} | " + " | ".join(cells) +
                    " | " + " | ".join(ratio_text(row[key], 3) for _, key in ratio_specs) + (f" | {row['gpu_execution_class']} |" if spark else " |"))
    if evidence["mixed_cleanup_protocols"]:
        text += ["", "**Mixed resource protocols are present.** Each row is labeled. Results from the legacy and verified-cleanup protocols are separate methodological strata, not one uniformly controlled crossover series."]
    if any(r["measured_trials"] == 1 for r in rows):
        text += ["", "**Smoke evidence:** one or more cases have only one measured trial. These check functionality and cannot estimate repeatability."]
    text += ["", "## Full-suite summaries", "",
        ("A sum of six per-query medians is shown only when all six cases at that scale pass complete three-engine validation. "
         if not ext else "Each engine's sum of six per-query medians is shown only when all six of its cases at that scale pass validation. "
         "An engine's sum is `n/a` when any case failed, was invalid or mismatched; other engines retain their own complete scores. ") +
        "It is a derived workload score, not a measured once-through suite runtime.", "",
        "| Scale | Trials/query | Resource protocol | " + " | ".join(f"{labels.get(e, e) if ext else labels.get(e, e).replace('Polars CPU', 'Polars')} sum (s)" for e in engines) + " | " +
        " | ".join(label for label, _ in ratio_specs) + " |",
        "|---|---:|---|" + "---:|" * (len(engines) + len(ratio_specs))]
    for total in evidence["full_suite_totals"]:
        sums = [total[e + "_sum_medians_seconds"] for e in engines]
        text.append(f"| {total['scale']} | {total['measured_trials']} | {total['query_cleanup_policy']} | " +
            " | ".join(f"{v:.3f}" if v is not None else f"n/a ({total.get(e + '_incomplete', 'incomplete')})" for e, v in zip(engines, sums)) +
            " | " + " | ".join(ratio_text(total[key], 3) for _, key in ratio_specs) + " |")
    if not evidence["full_suite_totals"]:
        text += ["", "No scale has all six complete cases; no full-suite score is available."]
    if ext:
        complete = sum(all(total.get(e + "_sum_medians_seconds") is not None for e in engines)
                       for total in evidence["full_suite_totals"])
        text += ["", f"Complete {len(engines)}-engine suites: **{complete}**."]
    if spark:
        text += ["", "## GPU Spark final-plan coverage" if ext else "## GPU and CPU plan coverage", "",
            "Coverage is inspected from the saved final AQE plan, excluding unused initial plans. Classification is based on "
            "named plan nodes, not runtime percentages. `gpu_operators_present` does not mean every operation ran on the GPU. "
            "`hybrid` means GPU and checked CPU core operators appear; `cpu_fallback` means no GPU-prefixed nodes were observed "
            "despite the enabled plugin. Union labels summarize all saved runs; per-record classifications and any recorded-label "
            "disagreements remain in `correctness.json`.", "",
            "| Scale / query | GPU node names | Checked CPU core node names | Classes observed |",
            "|---|---|---|---|"]
        for row in rows:
            text.append(f"| {row['scale']} / {row['query']} | {row['gpu_operators'].replace('|', ', ') or 'none observed'} | "
                        f"{row['cpu_core_operators'].replace('|', ', ') or 'none detected by name check'} | {row['gpu_execution_classes_observed'].replace('|', ', ')} |")
    scope = evidence["oracle_scope"]
    text += ["", "## Independent oracle scope", "",
        f"Oracle-checked included cases: **{len(scope['verified_cases'])}**. Included cases without an independent oracle: "
        f"**{len(scope['uncovered_cases'])}**. {scope['note']}. Oracle completion, suite/version, manifest SHA-256, and exact "
        "registry SQL SHA-256 must match before outputs are compared. A small-scale oracle does not prove independent "
        "oracle correctness at larger scales."]
    for case in scope["verified_cases"]:
        text.append(f"- Independent DuckDB check: {case['scale']} / {case['query']}.")
    if ext:
        text += ["", "## Engine versions and configuration", "", "| Scale / engine | Version | Configuration |", "|---|---|---|"]
        text += [f"| {s['scale']} / {s['engine']} | {s.get('version', 'unavailable')} | {s.get('configuration', '—')} |" for s in evidence["sessions"]]
        profiles = sorted({p for s in evidence["sessions"] for p in [s.get("configuration", "")] if p.startswith("profile=")})
        if len(profiles) > 1:
            text += ["", f"**Mixed Spark GPU profiles ({', '.join(profiles)}).** Do not read curves across profiles as one controlled series."]
    text += ["", "## Startup and process scopes", "",
        "Query timings exclude startup, table/view setup, warmups and plan serialization. Launch-to-ready includes session "
        "setup and overlaps that field. Process wall time also contains all warmups, measured queries, serialization, and "
        "shutdown. Under verified_gc it also includes the explicit resource barriers. Do not add overlapping fields or treat process wall time as one six-query pass.", "",
        "| Scale / engine | Resource protocol | Launch → ready (s) | Session + table setup (s) | Saved warmup sum (s) | Explicit cleanup count / sum (s) | Process wall (s) |",
        "|---|---|---:|---:|---:|---:|---:|"]
    for session in evidence["sessions"]:
        launch = session.get("launch_to_engine_ready_seconds")
        text.append(f"| {session['scale']} / {session['engine']} | {session['query_cleanup_policy']} | {launch:.3f}" if launch is not None else f"| {session['scale']} / {session['engine']} | {session['query_cleanup_policy']} | unavailable")
        cleanup = (f"{session['explicit_cleanup_count']} / {session['explicit_cleanup_sum_seconds']:.3f}"
                   if session['explicit_cleanup_sum_seconds'] is not None else "unrecorded")
        text[-1] += f" | {session['session_and_table_setup_seconds']:.3f} | {session['warmup_query_sum_seconds']:.3f} | {cleanup} | {session['process_wall_seconds']:.3f} |"
    for failure in evidence.get("engine_failures", []):
        if failure["query"] is None:
            text.append(f"| {failure['scale']} / {failure['engine']} | — | {problem_cell(failure['kind'], failure['reason'])} | unavailable | unavailable | unavailable | unavailable |")
    text += ["", "## Post-query resource barriers", "",
        evidence["cleanup_scope"], "",
        "Under `verified_gc`, every saved warmup and measured output must have exactly one matching passed cleanup record. "
        "Polars receives Python garbage collection; Spark receives Python/JVM garbage collection followed by a wait for "
        "the owned shuffle-file count and bytes to reach zero. A request for garbage collection alone is insufficient. "
        "A global cleanup failure invalidates the engine session. The barrier occurs after query execution returns and "
        "changes the lifecycle of a long-lived session; it is a declared protocol change, not evidence that the legacy failed run succeeded. "
        "Legacy `none` sessions have no explicit verified-barrier measurement; that does not mean automatic cleanup took no time.", "",
        "Per-case explicit cleanup below sums all saved barriers, including the warmup and measured trials. It is separate "
        "from the query medians and already lies inside process wall time.", "",
        "| Scale / query | Engine | Passed barriers | Explicit cleanup sum (s) |",
        "|---|---|---:|---:|"]
    cleanup_rows = [row for row in rows if row["query_cleanup_policy"] == "verified_gc"]
    for row in cleanup_rows:
        for engine in engines:
            count, seconds = row[engine + "_cleanup_count"], row[engine + "_cleanup_seconds"]
            text.append(f"| {row['scale']} / {row['query']} | {engine} | {'n/a' if count is None else count} | "
                        f"{'n/a' if seconds is None else format(seconds, '.3f')} |")
    if not cleanup_rows:
        text += ["", "No included case uses the verified_gc protocol; explicit barrier costs are unrecorded in this legacy evidence."]
    text += ["", "## Exclusions and errors", ""]
    if not evidence["excluded_cases"] and not evidence["oracle_errors"]:
        text.append("No selected cases were excluded.")
    for case in evidence["excluded_cases"]:
        text.append(f"- {case['scale']} / {case['query']}: " + "; ".join(case["reasons"]))
    for error in evidence["oracle_errors"]:
        text.append(f"- Oracle {error['source']}: {error['error']}")
    if ext:
        text += [""] + matrix_lines(rows, engines) + agreement_lines(evidence) + gpu_memory_lines(rows, engines) + failure_lines(evidence)
        text.pop()
    text += ["", "## Interpretation and reproducibility", "",
        "- This is a fixed-configuration single-host experiment on the same shared Parquet inputs per scale. It does not establish distributed scaling, equivalent-cost performance, or a product-wide winner.",
        "- Polars uses CPU streaming; both Spark modes share recorded Spark controls and CPU allocation. The GPU-plugin session adds NVIDIA's plugin and its GPU settings. Host RAM, the Spark heap, and GPU VRAM are different budgets.",
        "- Filesystem preheating is attempted before each engine; no application DataFrame cache is used. Residency is not guaranteed, so these are not certified pure-compute or cold-storage measurements.",
        "- One warmup plus three measured trials within one engine session is descriptive. It is not three independently restarted experiments and does not establish tail latency or statistical significance.",
        "- Q9/Q13 are TPC-H-derived and the other cases are custom queries on the same derived tables; this is not a complete or certified TPC-H run. Float64/DOUBLE casts and deterministic output ordering are retained across engines.",
        "- A failed or incomplete engine/case is excluded rather than assigned an infinite latency or counted as a GPU win. A full-suite score requires all six cases, even when only five succeed.",
        "- Raw roots retain actual outputs, plans, configuration, manifests, logs, source hashes and telemetry. Later roots replace earlier roots for a duplicate scale; an incomplete replacement is never silently replaced by older success."]
    if ext and "polars_gpu" in engines:
        text.append("- Polars GPU runs the cuDF GPU engine with `raise_on_fail=True`. A case it cannot run fully on the GPU is a recorded failure for that engine only: "
                    "the case stays in the tables for the other engines, the Polars GPU cell states the reason, and the case never enters a Polars GPU sum or ratio. "
                    "Timings include device-to-host transfer of the collected result and exclude conversion to Python dictionaries, as for Polars CPU.")
    for source in evidence["source_roots"]:
        text.append(f"- Raw results: `{source['root']}`.")
    return "\n".join(text) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, nargs="+", default=[])
    parser.add_argument("--engines", nargs="+", choices=ENGINE_ORDER,
                        help="analyze exactly these engines (must include polars); default: original three plus any discovered")
    args = parser.parse_args()
    if args.engines and "polars" not in args.engines:
        parser.error("--engines must include polars, the correctness baseline")
    args.output.mkdir(parents=True, exist_ok=True)
    rows, evidence = analyze(args.results, args.oracle, args.engines)
    write_json(args.output / "correctness.json", evidence)
    if not rows:
        for name in ("summary.csv", "COMPLEX_REPORT.md", "pairwise_speedups.csv"):
            (args.output / name).unlink(missing_ok=True)
        print("No complete verified complex cases; see correctness.json", file=sys.stderr)
        return 1
    engines = tuple(evidence.get("engines", LEGACY_ENGINES))
    with (args.output / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(dict.fromkeys(key for row in rows for key in row)))
        writer.writeheader(); writer.writerows(rows)
    if is_extended(engines):
        write_pairwise_csv(args.output / "pairwise_speedups.csv", rows, engines)
    (args.output / "COMPLEX_REPORT.md").write_text(report(rows, evidence))
    coverage = f"full suites {len(evidence['full_suite_totals'])}"
    if is_extended(engines):
        complete = sum(all(total.get(e + "_sum_medians_seconds") is not None for e in engines)
                       for total in evidence["full_suite_totals"])
        coverage = f"scale summaries {len(evidence['full_suite_totals'])}; complete {len(engines)}-engine suites {complete}"
    print(f"Validated {len(rows)} complex cases; excluded {len(evidence['excluded_cases'])}; "
          f"{coverage}; report: {args.output.resolve() / 'COMPLEX_REPORT.md'}")
    violations = evidence.get("integrity_violations", [])
    for item in violations:
        print(f"{item['scale']}/{item['engine']}/{item['query'] or 'session'}: {item['kind']}: {item['reason']}", file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
