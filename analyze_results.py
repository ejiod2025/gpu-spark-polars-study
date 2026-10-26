#!/usr/bin/env python3
"""Validate benchmark evidence and summarize measured query times.

Handles the original three engines (polars, spark_cpu, spark_gpu) and, when its
results are present, polars_gpu (Polars with the cuDF GPU engine in strict mode).
Uses only Python's standard library. A performance report is written only after
all recorded outputs and saved GPU executed plans pass validation.

Engine set: the three original engines are always required (a missing one stays
a visible error); polars_gpu is required in every scale as soon as any scale or
schedule.json mentions it. Pass --engines to analyze an explicit subset; engines
present in the evidence but left out by the flag are listed, never dropped
silently. A polars_gpu failure never invalidates the other engines: the affected
cells are reported as FAILED/INVALID/MISMATCH with the reason and carry no timing.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys


LEGACY_ENGINES = ("polars", "spark_cpu", "spark_gpu")
ENGINE_ORDER = ("polars", "polars_gpu", "spark_cpu", "spark_gpu")
ENGINE_LABELS = {"polars": "Polars", "polars_gpu": "Polars GPU", "spark_cpu": "CPU Spark", "spark_gpu": "GPU Spark"}
QUERIES = ("q01", "q03", "q06", "q10", "q12", "q14")
REL_TOL = 1e-8
ABS_TOL = 1e-6
# Ratios beyond the legacy pair; each is numerator time / denominator time, so > 1 favors the denominator.
EXTRA_RATIOS = (("polars_over_polars_gpu", "polars", "polars_gpu"),
                ("polars_gpu_over_spark_gpu", "polars_gpu", "spark_gpu"))
# A host the committed three-engine study used; reports keep their legacy wording for it.
LEGACY_HOST = {"gpu": "NVIDIA A100-SXM4-40GB", "vcpus": 30, "host_ram_gib": 216}


class IntegrityError(ValueError):
    """Evidence that must never be presented as a valid measurement of its engine."""


def read_json(path):
    with path.open() as handle:
        return json.load(handle)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def order_engines(names):
    names = set(names)
    return tuple(e for e in ENGINE_ORDER if e in names) + tuple(sorted(names - set(ENGINE_ORDER)))


def scale_value(value):
    """Numeric scale from 'sf10', 'sf1p5', '10' or 10; None when not a positive scale."""
    text = str(value)
    if text.startswith("sf"):
        text = text[2:]
    try:
        number = Decimal(text.replace("p", "."))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number > 0 else None


def read_schedule(root):
    try:
        value = read_json(root / "schedule.json")
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    except (OSError, ValueError, TypeError):
        return []


def discover_engines(scale_dir, schedule=()):
    """Engine names evidenced for one scale: result directories and schedule entries."""
    names = set()
    if scale_dir.is_dir():
        for child in scale_dir.iterdir():
            if child.is_dir() and not child.name.endswith("-events") and (
                    child.name in ENGINE_ORDER or (child / "results.json").is_file()):
                names.add(child.name)
    wanted = scale_value(scale_dir.name)
    for item in schedule:
        if item.get("engine") and wanted is not None and scale_value(item.get("scale")) == wanted:
            names.add(str(item["engine"]))
    return names


def resolve_engines(requested, discovered):
    """Expected engines: --engines when given, else the original three plus anything discovered."""
    if requested:
        names = set(requested)
        if "polars" not in names:
            raise ValueError("--engines must include polars, the correctness baseline")
    else:
        names = set(LEGACY_ENGINES) | set(discovered)
    return order_engines(names)


def is_extended(engines):
    return tuple(engines) != LEGACY_ENGINES


def failure_entry(scale, engine, query, kind, reason, **extra):
    """kind: failed (no usable run), invalid (integrity violation), mismatch (output differs from Polars)."""
    return {"scale": scale, "engine": engine, "query": query, "kind": kind,
            "reason": " ".join(str(reason).split())[:500], **extra}


def failure_index(failures):
    return {(f["scale"], f["engine"], f["query"]): f for f in failures}


def problem(index, scale, engine, query):
    return index.get((scale, engine, query)) or index.get((scale, engine, None))


def safe_ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator is not None else None


def ratio_text(value, digits=2):
    return f"{value:.{digits}f}x" if value is not None else "n/a"


def problem_cell(kind, reason):
    """Explicit non-timing cell: a failed/invalid/mismatched engine is never shown as a time."""
    text = " ".join(str(reason).split())
    if len(text) > 70:
        text = text[:67] + "..."
    return f"{str(kind).upper()}: " + text.replace("|", "\\|")


def row_cell(row, engine):
    median = row.get(engine + "_median_seconds")
    if median is None:
        return problem_cell(row.get(engine + "_status") or "failed", row.get(engine + "_failure") or "no usable timing")
    return f"{median:.3f} [{row[engine + '_min_seconds']:.3f}, {row[engine + '_max_seconds']:.3f}]"


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


def query_failures(session):
    """Recorded per-query errors (worker query_errors) as {query: 'Type: message'}; first error wins."""
    failed = {}
    for key in ("query_errors", "query_failures"):
        for item in session.get(key) or []:
            if isinstance(item, dict) and item.get("query") in QUERIES:
                failed.setdefault(item["query"], f"{item.get('type', 'error')}: {item.get('message', '')}".strip())
    return failed


def check_polars_gpu_metadata(session):
    if session.get("polars_execution_engine") != "gpu":
        raise IntegrityError(f"polars_execution_engine is {session.get('polars_execution_engine')!r}, expected 'gpu'")
    meta = session.get("polars_gpu")
    if not isinstance(meta, dict):
        raise IntegrityError("polars_gpu metadata object is missing")
    if meta.get("raise_on_fail") is not True:
        raise IntegrityError("polars_gpu.raise_on_fail is not true; silent CPU fallback cannot be excluded")
    if meta.get("cpu_fallback_policy") != "raise":
        raise IntegrityError("Polars GPU CPU-fallback policy must be explicitly 'raise'")
    guard = meta.get("session_guard")
    if not isinstance(guard, dict):
        raise IntegrityError("polars_gpu.session_guard is missing; strict CPU-fallback prevention is unverified")
    for field in ("effective_config_verified", "tiny_query_passed", "unsupported_canary_raised", "unsupported_canary_reason_verified"):
        if guard.get(field) is not True:
            raise IntegrityError(f"Polars GPU session guard {field} is not verified true")
    effective = guard.get("effective_config")
    if not isinstance(effective, dict) or effective.get("raise_on_fail") is not True:
        raise IntegrityError("cudf-polars effective raise_on_fail is not verified true")
    executor = effective.get("executor")
    if not isinstance(executor, dict) or executor.get("name") != meta.get("executor"):
        raise IntegrityError("cudf-polars effective executor differs from the declared executor")
    if meta.get("executor") == "streaming" and meta.get("streaming_fallback_policy") != executor.get("fallback_mode"):
        raise IntegrityError("cudf-polars streaming GPU fallback policy differs from the effective config")
    canary = guard.get("unsupported_canary_error")
    if not isinstance(canary, str) or "NotImplementedError" not in canary or not any(
            token in canary.lower() for token in ("anonymousfunction", "unsupported", "not supported")):
        raise IntegrityError("unsupported GPU canary did not establish rejection of CPU-only execution")
    # Streaming fallback is from partitioned GPU execution to the in-memory GPU
    # executor. Its warn/raise setting is separate from CPU translation fallback.


def validate_session(scale, engine, evidence):
    """Return (session, {failed_query: reason}); legacy engines have no tolerated failures."""
    path = scale / engine / "results.json"
    session = read_json(path)
    if session.get("engine") != engine:
        raise ValueError("engine metadata does not match directory")
    if engine == "polars_gpu":
        check_polars_gpu_metadata(session)
        if session.get("failure") or session.get("failed_at_utc"):
            raise ValueError("session recorded a global failure: " + json.dumps(session.get("failure"), sort_keys=True))
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
    failed_queries = query_failures(session) if engine not in LEGACY_ENGINES else {}
    needed = [q for q in QUERIES if q not in failed_queries]
    expected = {(q, "warmup", 0) for q in needed} | {
        (q, "measured", trial) for q in needed for trial in range(1, runs + 1)}
    found = {}
    for record in session.get("records", []):
        if record.get("query") in failed_queries:
            continue
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
        if "peak_gpu_memory_bytes" in record:
            gpu_memory = record["peak_gpu_memory_bytes"]
            if gpu_memory is not None and (isinstance(gpu_memory, bool) or not isinstance(gpu_memory, int) or gpu_memory < 0):
                raise ValueError(f"invalid peak GPU memory for {key}")
        if engine == "polars_gpu" and record.get("gpu_execution_class") != "gpu_engine_strict":
            raise IntegrityError(f"{key}: gpu_execution_class is {record.get('gpu_execution_class')!r}, expected 'gpu_engine_strict'")
        if engine == "polars_gpu" and "plan" not in record:
            plan_path = None
        else:
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
    return session, failed_queries


def load_and_validate(root, engines=None, external_failures=None):
    """Validate every scale under root.

    engines: expected engine tuple (default: original three plus whatever the evidence shows).
    external_failures: {scale_name: {engine: reason}} for engines the orchestrator reported as failed;
    they are recorded as failed without trusting their results.json.
    """
    evidence = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "correctness_verified": False, "gpu_execution_verified": False,
                "relative_tolerance": REL_TOL, "absolute_tolerance": ABS_TOL,
                "integer_comparison": "exact", "row_comparison": "ordered; dictionary key order ignored",
                "errors": [], "comparisons": [], "gpu_plans": [], "datasets": []}
    sessions = {}
    scales = sorted(p for p in root.glob("sf*") if p.is_dir())
    if not scales:
        evidence["errors"].append("No sf* result directories found")
    schedule = read_schedule(root)
    discovered = set().union(*(discover_engines(scale, schedule) for scale in scales)) if scales else set()
    engines = tuple(engines) if engines else resolve_engines(None, discovered)
    external_failures = external_failures or {}
    failures, warnings = [], []
    if is_extended(engines):
        evidence["engines"] = list(engines)
        evidence["source_host"] = host_shape(read_environment(root))
    if discovered - set(engines):
        evidence["engines_excluded_by_flag"] = sorted(discovered - set(engines))
    for scale in scales:
        sessions[scale.name] = {}
        for engine in engines:
            label = f"{scale.name}/{engine}"
            if engine in external_failures.get(scale.name, {}):
                failures.append(failure_entry(scale.name, engine, None, "failed", external_failures[scale.name][engine]))
                continue
            try:
                session, failed_queries = validate_session(scale, engine, evidence)
                sessions[scale.name][engine] = session
                for query, reason in failed_queries.items():
                    failures.append(failure_entry(scale.name, engine, query, "failed", reason))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                if engine in LEGACY_ENGINES:
                    evidence["errors"].append(f"{label}: {exc}")
                else:
                    reason = f"results.json unavailable ({type(exc).__name__})" if isinstance(exc, OSError) else exc
                    failures.append(failure_entry(scale.name, engine, None,
                                                  "invalid" if isinstance(exc, IntegrityError) else "failed", reason))
        loaded = sessions[scale.name]
        if not {e for e in engines if e in LEGACY_ENGINES} <= set(loaded) or "polars" not in loaded:
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
        for engine, session in list(loaded.items()):
            degradable = engine not in LEGACY_ENGINES
            for field in ("manifest_sha256", "scale_factor", "runs", "cpu_threads", "cache_policy", "timing_scope"):
                if session.get(field) != baseline.get(field):
                    if not degradable:
                        evidence["errors"].append(f"{scale.name}/{engine}: {field} differs from Polars")
                    elif field in ("cpu_threads", "timing_scope"):
                        warnings.append({"scale": scale.name, "engine": engine,
                                         "warning": f"{field} differs from Polars: {session.get(field)!r} vs {baseline.get(field)!r}"})
                    else:
                        failures.append(failure_entry(scale.name, engine, None, "invalid", f"{field} differs from Polars"))
                        del loaded[engine]
                        break
            else:
                if degradable and engine == "polars_gpu" and session.get("version") != baseline.get("version"):
                    warnings.append({"scale": scale.name, "engine": engine,
                                     "warning": f"Polars version differs: {session.get('version')!r} vs {baseline.get('version')!r}"})
                mismatched = {}
                for key, record in session["indexed_records"].items():
                    reference = baseline["indexed_records"][(key[0], "warmup", 0)]["rows"]
                    check = compare_values(reference, record["rows"])
                    evidence["comparisons"].append({"scale": scale.name, "engine": engine,
                        "query": key[0], "phase": key[1], "trial": key[2],
                        "expected_row_count": len(reference), "actual_row_count": len(record["rows"]), **check})
                    if not check["passed"]:
                        if not degradable:
                            evidence["errors"].append(f"{scale.name}/{engine}/{key}: result differs from Polars warmup")
                        else:
                            mismatched.setdefault(key[0], []).append(check["max_relative_numeric_difference"])
                for query, errors in mismatched.items():
                    failures.append(failure_entry(scale.name, engine, query, "mismatch",
                        f"{len(errors)} saved output(s) differ from the Polars warmup beyond tolerance (max relative difference {max(errors):.3g})"))
    valid = not evidence["errors"]
    evidence["correctness_verified"] = valid and bool(evidence["comparisons"])
    gpu_evidence = bool(evidence["gpu_plans"]) or ("spark_gpu" not in engines and any(
        "polars_gpu" in loaded for loaded in sessions.values()))
    evidence["gpu_execution_verified"] = valid and gpu_evidence
    if is_extended(engines):
        evidence.update(extended_evidence(sessions, evidence["comparisons"], failures, warnings, engines))
        evidence["all_requested_engine_cases_validated"] = valid and not failures and bool(evidence["comparisons"])
        evidence["correctness_verified"] = evidence["correctness_verified"] and not evidence["integrity_violations"]
    evidence["passed"] = evidence["correctness_verified"] and evidence["gpu_execution_verified"]
    return sessions, evidence


def extended_evidence(sessions, comparisons, failures, warnings, engines):
    """Keys that exist only when engines beyond the original three are analyzed."""
    gpu_sessions = []
    for scale, loaded in sessions.items():
        session = loaded.get("polars_gpu")
        if session:
            warnings.extend(polars_gpu_warning_findings(scale, session))
            gpu_sessions.append({"scale": scale, "polars_gpu": session.get("polars_gpu"),
                                 "version": session.get("version"),
                                 "gpu_inventory": session.get("gpu_inventory"),
                                 "query_execution_status": session.get("query_execution_status")})
    agreement = {}
    for item in comparisons:
        if item["engine"] != "polars_gpu":
            continue
        entry = agreement.setdefault((item["scale"], item["query"]), {
            "scale": item["scale"], "query": item["query"], "comparisons": 0, "failed_comparisons": 0,
            "max_relative_numeric_difference": 0.0, "max_absolute_numeric_difference": 0.0})
        entry["comparisons"] += 1
        entry["failed_comparisons"] += not item["passed"]
        entry["max_relative_numeric_difference"] = max(entry["max_relative_numeric_difference"], item["max_relative_numeric_difference"])
        entry["max_absolute_numeric_difference"] = max(entry["max_absolute_numeric_difference"], item["max_absolute_numeric_difference"])
    return {"engine_failures": failures, "engine_warnings": warnings,
            "integrity_violations": [f for f in failures if f["kind"] in ("invalid", "mismatch")],
            "polars_gpu_sessions": gpu_sessions,
            "polars_gpu_numeric_agreement": list(agreement.values()),
            "polars_gpu_agreement_note": "max_relative_numeric_difference is the largest relative difference to the Polars warmup over all "
                                         f"compared numeric values (tolerance: relative {REL_TOL:g}, absolute {ABS_TOL:g}); it is reported, not loosened."}


def polars_gpu_warning_findings(scale, session):
    """Retain per-query GPU executor warnings without calling GPU-to-GPU fallback CPU execution."""
    findings, seen = [], set()
    for record in session.get("records", []):
        for warning in record.get("gpu_engine_warnings", []):
            key = (record.get("query"), str(warning))
            if key in seen:
                continue
            seen.add(key)
            findings.append({"scale": scale, "engine": "polars_gpu", "query": key[0], "warning": key[1]})
    return findings


def gpu_memory_engines(sessions):
    """Engines whose records carry peak_gpu_memory_bytes (absent in the original three-engine evidence)."""
    return tuple(order_engines({engine for loaded in sessions.values() for engine, session in loaded.items()
                                if any("peak_gpu_memory_bytes" in r for r in session["indexed_records"].values())}))


def summary_columns(engines, memory_engines=()):
    fields = ["scale", "query", "measured_trials"]
    for engine in engines:
        fields += [engine + suffix for suffix in ("_median_seconds", "_min_seconds", "_max_seconds", "_max_peak_process_tree_rss_bytes")]
        if engine in memory_engines:
            fields.append(engine + "_max_peak_gpu_memory_bytes")
        if engine not in LEGACY_ENGINES:
            fields += [engine + "_status", engine + "_failure"]
    if "spark_gpu" in engines and "spark_cpu" in engines:
        fields.append("spark_cpu_over_spark_gpu")
    if "spark_gpu" in engines and "polars" in engines:
        fields.append("polars_over_spark_gpu")
    return fields + extra_ratio_columns(engines)


def extra_ratio_columns(engines):
    return [name for name, numerator, denominator in EXTRA_RATIOS if numerator in engines and denominator in engines]


def add_extra_ratios(row, engines):
    for name, numerator, denominator in EXTRA_RATIOS:
        if numerator in engines and denominator in engines:
            row[name] = safe_ratio(row[numerator + "_median_seconds"], row[denominator + "_median_seconds"])


def summarize(sessions, engines=None, failures=()):
    """One row per scale and query. A failed/invalid/mismatched engine gets empty timing cells and a status."""
    index = failure_index(failures)
    memory = gpu_memory_engines(sessions)
    rows = []
    for scale, loaded in sessions.items():
        here = tuple(engines) if engines else order_engines(loaded)
        for query in QUERIES:
            row = {"scale": scale, "query": query, "measured_trials": loaded["polars"]["runs"]}
            for engine in here:
                issue = problem(index, scale, engine, query)
                session = loaded.get(engine)
                records = [] if issue or session is None else [
                    r for (q, phase, _), r in session["indexed_records"].items() if q == query and phase == "measured"]
                times = [r["seconds"] for r in records]
                row[engine + "_median_seconds"] = statistics.median(times) if times else None
                row[engine + "_min_seconds"] = min(times) if times else None
                row[engine + "_max_seconds"] = max(times) if times else None
                row[engine + "_max_peak_process_tree_rss_bytes"] = max(r["peak_process_tree_rss_bytes"] for r in records) if records else None
                if engine in memory:
                    values = [r["peak_gpu_memory_bytes"] for r in records if r.get("peak_gpu_memory_bytes") is not None]
                    row[engine + "_max_peak_gpu_memory_bytes"] = max(values) if values else None
                if engine not in LEGACY_ENGINES:
                    row[engine + "_status"] = issue["kind"] if issue else ("ok" if times else "failed")
                    row[engine + "_failure"] = issue["reason"] if issue else ("" if times else "no measured records")
            if "spark_gpu" in here and "spark_cpu" in here:
                row["spark_cpu_over_spark_gpu"] = safe_ratio(row["spark_cpu_median_seconds"], row["spark_gpu_median_seconds"])
            if "spark_gpu" in here and "polars" in here:
                row["polars_over_spark_gpu"] = safe_ratio(row["polars_median_seconds"], row["spark_gpu_median_seconds"])
            add_extra_ratios(row, here)
            rows.append(row)
    return rows


def incomplete_note(group, engine, total):
    """Why an engine has no suite total: which queries have no timing and in what state ('5/6 queries; q03 failed')."""
    missing = [(r["query"], r.get(engine + "_status") or "failed") for r in group if r[engine + "_median_seconds"] is None]
    timed = total - len(missing)
    if len(missing) == total and len({kind for _, kind in missing}) == 1:
        return f"{timed}/{total} queries; all {missing[0][1]}"
    return f"{timed}/{total} queries; " + ", ".join(f"{query} {kind}" for query, kind in missing)


def engine_sums(rows, engines):
    """Per engine, per scale: (sum of medians, or None unless every query was timed; queries timed; queries total; note).

    A sum over only the surviving queries would understate that engine, so an engine with any failed,
    invalid or mismatching query has no total at that scale; the note says which queries are missing.
    """
    sums = {}
    for scale in dict.fromkeys(r["scale"] for r in rows):
        group = [r for r in rows if r["scale"] == scale]
        for engine in engines:
            medians = [r[engine + "_median_seconds"] for r in group]
            timed = [m for m in medians if m is not None]
            complete = len(timed) == len(medians)
            sums[(scale, engine)] = (sum(timed) if complete else None, len(timed), len(medians),
                                     "" if complete else incomplete_note(group, engine, len(medians)))
    return sums


def pairwise(rows, engines):
    """Ordered engine pairs (numerator earlier in canonical order) per scale and per query.

    A suite-level (query "ALL") ratio exists only when both engines were timed on every query: a sum over
    surviving queries would not be a total. Per-query ratios exist wherever both engines have a timing.
    """
    records = []
    pairs = [(a, b) for i, a in enumerate(engines) for b in engines[i + 1:]]
    for scale in dict.fromkeys(r["scale"] for r in rows):
        group = [r for r in rows if r["scale"] == scale]
        for numerator, denominator in pairs:
            usable = [r for r in group if r[numerator + "_median_seconds"] is not None and r[denominator + "_median_seconds"] is not None]
            complete = bool(group) and len(usable) == len(group)
            top = sum(r[numerator + "_median_seconds"] for r in usable) if complete else None
            bottom = sum(r[denominator + "_median_seconds"] for r in usable) if complete else None
            records.append({"scale": scale, "query": "ALL", "numerator": numerator, "denominator": denominator,
                            "numerator_seconds": top, "denominator_seconds": bottom,
                            "ratio": top / bottom if complete else None,
                            "queries_used": len(usable), "queries_total": len(group)})
            for r in group:
                a, b = r[numerator + "_median_seconds"], r[denominator + "_median_seconds"]
                records.append({"scale": scale, "query": r["query"], "numerator": numerator, "denominator": denominator,
                                "numerator_seconds": a, "denominator_seconds": b, "ratio": safe_ratio(a, b),
                                "queries_used": int(a is not None and b is not None), "queries_total": 1})
    return records


def write_pairwise_csv(path, rows, engines):
    fields = ["scale", "query", "numerator", "denominator", "numerator_seconds", "denominator_seconds",
              "ratio", "queries_used", "queries_total"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(pairwise(rows, engines))


def matrix_lines(rows, engines):
    """Markdown speedup matrices (sum of medians) per scale: cell = row-engine time / column-engine time."""
    lines = ["## Pairwise speedup matrices", "",
             "Each cell is the row engine's sum of per-query medians divided by the column engine's: above 1, the column engine is faster. "
             "A cell is `n/a` when either engine lacks a timing for at least one query at that scale (a failed, invalid or mismatching query): "
             "a sum over only the surviving queries is not a suite total and is never used. Per-query pairs, which exist wherever both engines "
             "have a timing, are in `pairwise_speedups.csv`.", ""]
    records = {(r["scale"], r["numerator"], r["denominator"]): r for r in pairwise(rows, engines) if r["query"] == "ALL"}
    sums = engine_sums(rows, engines)
    for scale in dict.fromkeys(r["scale"] for r in rows):
        lines += [f"### {scale}", "", "| Row / column | " + " | ".join(ENGINE_LABELS.get(e, e) for e in engines) + " |",
                  "|---|" + "---:|" * len(engines)]
        for a in engines:
            cells = []
            for b in engines:
                if a == b:
                    cells.append("—")
                    continue
                forward = records.get((scale, a, b))
                record = forward or records.get((scale, b, a))
                if not record or record["ratio"] is None:
                    cells.append("n/a")
                else:
                    cells.append(f"{(record['ratio'] if forward else 1 / record['ratio']):.2f}x")
            lines.append(f"| {ENGINE_LABELS.get(a, a)} | " + " | ".join(cells) + " |")
        incomplete = [f"{ENGINE_LABELS.get(e, e)}: {sums[(scale, e)][3]}" for e in engines if sums[(scale, e)][0] is None]
        if incomplete:
            lines += ["", "Engines without a complete suite at this scale: " + "; ".join(incomplete) + "."]
        lines.append("")
    return lines


def gpu_memory_lines(rows, engines):
    present = [e for e in engines if any(e + "_max_peak_gpu_memory_bytes" in r for r in rows)]
    if not present:
        return []
    lines = ["## Peak GPU memory", "",
             "`peak_gpu_memory_bytes` is the worker's per-process-tree device-memory peak sampled during the timed action; the table shows the "
             "maximum over measured trials, in GiB. Empty means sampling was unavailable. It is a sampled peak, not an allocator-level trace.", "",
             "| Scale / query | " + " | ".join(ENGINE_LABELS.get(e, e) for e in present) + " |", "|---|" + "---:|" * len(present)]
    for row in rows:
        cells = []
        for engine in present:
            value = row.get(engine + "_max_peak_gpu_memory_bytes")
            cells.append(f"{value / 1024**3:.2f}" if value is not None else ("n/a" if row.get(engine + "_median_seconds") is None else "unrecorded"))
        lines.append(f"| {row['scale']} / {row['query']} | " + " | ".join(cells) + " |")
    return lines + [""]


def agreement_lines(evidence):
    items = evidence.get("polars_gpu_numeric_agreement")
    if not items:
        return []
    lines = ["## Polars GPU numeric agreement with Polars CPU", "",
             f"Every saved Polars GPU output is compared with the Polars CPU warmup at relative {REL_TOL:g}, absolute {ABS_TOL:g}, exact integers. "
             "The tolerance is not loosened for the GPU engine; the largest observed relative difference per query is reported as a finding, "
             "including when it is within tolerance. Values are maxima over all numeric cells and all saved outputs of the query.", "",
             "| Scale / query | Outputs compared | Outputs outside tolerance | Max relative difference | Max absolute difference |",
             "|---|---:|---:|---:|---:|"]
    for item in items:
        lines.append(f"| {item['scale']} / {item['query']} | {item['comparisons']} | {item['failed_comparisons']} | "
                     f"{item['max_relative_numeric_difference']:.3e} | {item['max_absolute_numeric_difference']:.3e} |")
    return lines + [""]


def failure_lines(evidence):
    failures = evidence.get("engine_failures") or []
    warnings = evidence.get("engine_warnings") or []
    lines = ["## Engine failures and integrity findings", ""]
    if not failures:
        lines.append("No failed, invalid or mismatching engine results among the analyzed non-legacy engines.")
    else:
        lines += ["`failed`: no usable run (error recorded by the engine, incomplete or failed session; for Polars GPU in strict mode, a query that cannot run "
                  "fully on the GPU). `invalid`: integrity violation (strict-GPU metadata missing or inconsistent). `mismatch`: output differs from the Polars "
                  "CPU baseline beyond tolerance. None of these cells carries a timing, and none enters a sum or ratio.", "",
                  "| Scale | Engine | Query | Kind | Reason |", "|---|---|---|---|---|"]
        for f in failures:
            lines.append(f"| {f['scale']} | {f['engine']} | {f['query'] or 'all queries (session)'} | {f['kind']} | {f['reason'].replace('|', chr(92) + '|')} |")
    if warnings:
        lines += ["", "Warnings (not invalidating):", "",
                  "Polars GPU streaming-to-single-partition fallback still executes on the GPU; CPU fallback is disabled independently.", ""]
        lines += [f"- {w['scale']}/{w['engine']}" + (f"/{w['query']}" if w.get("query") else "") + f": {w['warning']}" for w in warnings]
    if evidence.get("engines_excluded_by_flag"):
        lines += ["", "Engines present in the evidence but excluded by --engines: " + ", ".join(evidence["engines_excluded_by_flag"]) + "."]
    return lines + [""]


def read_environment(root):
    try:
        value = read_json(Path(root) / "environment.json")
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def inventory_output(environment, command, contains=None):
    for item in environment.get("inventory") or []:
        words = [str(w) for w in item.get("command", [])] if isinstance(item, dict) else []
        if words and Path(words[0]).name == command and (contains is None or contains in words):
            return str(item.get("output") or "")
        if words and command == "pip" and contains in words:
            return str(item.get("output") or "")
    return ""


def host_shape(environment):
    """Best-effort host description from environment.json.

    Reads the raw inventory (lscpu, free -b, nvidia-smi -q, pip freeze) when present and falls back to the
    sanitized resource_allocation block of the committed A100 evidence. Unknown fields stay None.
    """
    shape = {"gpu": None, "gpu_count": None, "gpu_memory_mib": None, "vcpus": None, "host_ram_gib": None,
             "cpu_model": None, "driver_version": None, "cuda_version": None, "cpus_arg": None,
             "driver_memory_arg": None, "packages": {}, "source": []}
    if not isinstance(environment, dict):
        return shape
    allocation = environment.get("resource_allocation")
    if isinstance(allocation, dict):
        shape.update(gpu=allocation.get("gpu"), gpu_count=allocation.get("gpu_count"),
                     vcpus=allocation.get("vcpus"), host_ram_gib=allocation.get("host_ram_gib_approx"))
        shape["source"].append("resource_allocation")
    arguments = environment.get("arguments") if isinstance(environment.get("arguments"), dict) else {}
    shape["cpus_arg"], shape["driver_memory_arg"] = arguments.get("cpus"), arguments.get("driver_memory")
    lscpu = inventory_output(environment, "lscpu")
    if lscpu:
        match = re.search(r"^CPU\(s\):\s+(\d+)\s*$", lscpu, re.M)
        shape["vcpus"] = int(match[1]) if match else shape["vcpus"]
        match = re.search(r"^Model name:\s+(.+?)\s*$", lscpu, re.M)
        shape["cpu_model"] = match[1] if match else None
        shape["source"].append("inventory:lscpu")
    free = inventory_output(environment, "free")
    match = re.search(r"^Mem:\s+(\d+)", free, re.M)
    if match:
        shape["host_ram_gib"] = round(int(match[1]) / 1024**3, 1)
        shape["source"].append("inventory:free")
    smi = inventory_output(environment, "nvidia-smi")
    if smi:
        match = re.search(r"^\s*Product Name\s*:\s*(.+?)\s*$", smi, re.M)
        shape["gpu"] = match[1] if match else shape["gpu"]
        match = re.search(r"^Attached GPUs\s*:\s*(\d+)", smi, re.M)
        shape["gpu_count"] = int(match[1]) if match else shape["gpu_count"]
        match = re.search(r"FB Memory Usage\s*\n\s*Total\s*:\s*(\d+)\s*MiB", smi)
        shape["gpu_memory_mib"] = int(match[1]) if match else None
        match = re.search(r"^Driver Version\s*:\s*(\S+)", smi, re.M)
        shape["driver_version"] = match[1] if match else None
        match = re.search(r"^CUDA Version\s*:\s*(\S+)", smi, re.M)
        shape["cuda_version"] = match[1] if match else None
        shape["source"].append("inventory:nvidia-smi")
    for line in inventory_output(environment, "pip", "freeze").splitlines():
        name, _, version = line.partition("==")
        if version and re.match(r"(?i)(polars|cudf|pylibcudf|rmm|pyspark|duckdb)", name):
            shape["packages"][name.strip().lower()] = version.strip()
    for item in environment.get("inventory") or []:
        # run_experiment's CUDA/RAPIDS stack probe prints a JSON object with a "distributions" map.
        text = str(item.get("output") or "").strip() if isinstance(item, dict) else ""
        if text.startswith("{") and '"distributions"' in text:
            try:
                probe = json.loads(text)
            except ValueError:
                continue
            for name, version in (probe.get("distributions") or {}).items():
                if re.match(r"(?i)(polars|cudf|pylibcudf|rmm)", name):
                    shape["packages"][name.lower()] = str(version)
            shape["cuda_runtime_version"] = probe.get("cuda_runtime_version")
    return shape


def host_label(shape):
    gpu = f"{shape['gpu_count'] or 1} x {shape['gpu']}" if shape.get("gpu") else "GPU not recorded"
    ram = f"{shape['host_ram_gib']:g} GiB" if shape.get("host_ram_gib") is not None else "RAM not recorded"
    vcpus = f"{shape['vcpus']} vCPU" if shape.get("vcpus") is not None else "vCPU count not recorded"
    return f"{gpu}, {vcpus}, {ram}"


def legacy_host_applies(shapes):
    """True when the recorded hosts are the committed A100 study host (or nothing contradicts it)."""
    return all(s.get("gpu") in (None, LEGACY_HOST["gpu"]) and s.get("vcpus") in (None, LEGACY_HOST["vcpus"]) and
               s.get("host_ram_gib") in (None, LEGACY_HOST["host_ram_gib"]) for s in shapes)


def gpu_short_name(name):
    """'NVIDIA H100 80GB HBM3' -> 'H100'; 'NVIDIA A100-SXM4-40GB' -> 'A100'."""
    match = re.search(r"\b(GH200|[ABHLV]\d{2,3}[A-Z]?|T4)\b", str(name or ""))
    return match[1] if match else (str(name or "").replace("NVIDIA", "").strip() or "GPU")


def evidence_hosts(evidence):
    """Host shapes recorded in an analyzer's correctness.json (only written when non-legacy engines are analyzed)."""
    hosts = [s["host"] for s in evidence.get("source_roots", []) if isinstance(s, dict) and isinstance(s.get("host"), dict)]
    if isinstance(evidence.get("source_host"), dict):
        hosts.append(evidence["source_host"])
    return hosts


def plot_hardware(evidence):
    """(short GPU name, footer text) for figures; (A100, None) keeps the legacy figure wording."""
    hosts = evidence_hosts(evidence)
    if not hosts:
        return "A100", None
    labels = list(dict.fromkeys(host_label(h) for h in hosts))
    gpus = list(dict.fromkeys(gpu_short_name(h["gpu"]) for h in hosts if h.get("gpu")))
    short = gpus[0] if len(gpus) == 1 else ("/".join(gpus) if gpus else "GPU")
    return short, ("One host: " if len(labels) == 1 else "Hosts: ") + "; ".join(labels)


def config_text(engine, session):
    """Run configuration worth printing next to the version: GPU profile / Polars GPU executor."""
    if engine == "polars_gpu":
        meta = session.get("polars_gpu") or {}
        return (f"executor={meta.get('executor')}; cudf-polars {meta.get('cudf_polars_version')}; "
                f"device {meta.get('device')}; raise_on_fail={meta.get('raise_on_fail')}")
    if engine == "spark_gpu" and session.get("spark_gpu_profile"):
        return "profile=" + str(session["spark_gpu_profile"])
    return "—"


def report_text(root, sessions, evidence, rows):
    engines = tuple(evidence.get("engines", LEGACY_ENGINES))
    ext = is_extended(engines)
    index = failure_index(evidence.get("engine_failures", []))
    names = [ENGINE_LABELS.get(e, e) for e in engines]
    title = ", ".join(names[:-1]) + ", and " + names[-1] if len(names) > 2 else " and ".join(names)
    smoke = any(s["runs"] == 1 for loaded in sessions.values() for s in loaded.values())
    text = [f"# {title} experiment" if ext else "# Polars, CPU Spark, and GPU Spark experiment", "",
            "**Correctness verified** for every saved warmup and measured output against the Polars warmup baseline. "
            "Final saved GPU plans contain GPU scan and aggregation/join operators. "
            f"Floating-point tolerance: relative {REL_TOL:g}, absolute {ABS_TOL:g}; integer pairs, strings, dates, and nulls match exactly.", ""]
    if ext and "polars_gpu" in engines:
        text += ["Polars GPU (cuDF GPU engine, `raise_on_fail=True`) results are accepted only when the session records the GPU engine in strict "
                 "mode and every record is classified `gpu_engine_strict`. A query that cannot run fully on the GPU is a failed cell, never a CPU "
                 "timing; failed, invalid or mismatching Polars GPU cells are listed under *Engine failures and integrity findings* and excluded from sums and ratios.", ""]
    if smoke:
        text += ["**Smoke run:** at least one scale has one measured trial. These timings verify the harness and provide no estimate of repeatability.", ""]
    text += ["Polars runs on the CPU using its streaming execution engine. Both Spark modes use the recorded Spark version; "
             "GPU Spark adds the NVIDIA cuDF plugin. Software versions and CPU thread allocations below come from the engine sessions.", "",
             "| Scale / engine | Engine version | CPU threads | GPU plugin artifact |" + (" Configuration |" if ext else ""),
             "|---|---|---:|---|" + ("---|" if ext else "")]
    for scale, loaded in sessions.items():
        for engine in engines:
            session = loaded.get(engine)
            if session is None:
                issue = problem(index, scale, engine, None)
                text.append(f"| {scale} / {engine} | {problem_cell(issue['kind'], issue['reason']) if issue else 'FAILED: no session evidence'} | — | — | — |")
                continue
            jars = session.get("spark_conf", {}).get("spark.jars", "")
            plugin = next((jar.rsplit("/", 1)[-1] for jar in jars.split(",") if "rapids-4-spark_" in jar), "—")
            label = "polars (CPU streaming)" if engine == "polars" else "polars_gpu (cuDF GPU engine)" if engine == "polars_gpu" else engine
            text.append(f"| {scale} / {label} | {session.get('version', 'unavailable')} | "
                        f"{session.get('cpu_threads', 'unavailable')} | {plugin} |" + (f" {config_text(engine, session)} |" if ext else ""))
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
    ratio_names = [("spark_cpu_over_spark_gpu", "CPU Spark / GPU"), ("polars_over_spark_gpu", "Polars / GPU")]
    if ext:
        ratio_names += [("polars_over_polars_gpu", "Polars / Polars GPU"), ("polars_gpu_over_spark_gpu", "Polars GPU / GPU Spark")]
    ratio_names = [(key, label) for key, label in ratio_names if key in rows[0]]
    text += ["The table reports the **sum of six per-query medians**, in seconds. This is a derived workload summary, "
             "not a measured complete-suite runtime. Ratios above 1 favor GPU Spark; below 1 favor the named CPU engine.", "",
             "| Scale | Trials/query | " + " | ".join(f"{ENGINE_LABELS.get(e, e)} sum" for e in engines) + " | " + " | ".join(label for _, label in ratio_names) + " |",
             "|---|---:|" + "---:|" * (len(engines) + len(ratio_names))]
    sums = engine_sums(rows, engines)
    for scale, loaded in sessions.items():
        totals = {e: sums[(scale, e)][0] for e in engines}
        cells = [f"{totals[e]:.3f}" if totals[e] is not None else f"n/a ({sums[(scale, e)][3]})" for e in engines]
        ratios = [ratio_text(safe_ratio(totals[key.split("_over_")[0]], totals[key.split("_over_")[1]])) for key, _ in ratio_names]
        text.append(f"| {scale} | {loaded['polars']['runs']} | " + " | ".join(cells) + " | " + " | ".join(ratios) + " |")
    text += ["", "Per-query measured times are median [min, max] seconds. The full precision values and sampled process-tree peak RSS are in `summary.csv`.", "",
             "| Scale / query | " + " | ".join(ENGINE_LABELS.get(e, e) for e in engines) + " | " + " | ".join(label for _, label in ratio_names) + " |",
             "|---|" + "---:|" * (len(engines) + len(ratio_names))]
    for row in rows:
        cells = [row_cell(row, e) for e in engines]
        text.append(f"| {row['scale']} / {row['query']} | " + " | ".join(cells) + " | " +
                    " | ".join(ratio_text(row[key]) for key, _ in ratio_names) + " |")
    text += ["", "For each comparator, the strongest and weakest GPU cases by median-time ratio are:", ""]
    comparators = [("Against CPU Spark", "spark_cpu_over_spark_gpu"), ("Against Polars", "polars_over_spark_gpu")]
    if ext:
        comparators += [("Polars GPU against Polars CPU (Polars / Polars GPU)", "polars_over_polars_gpu"),
                        ("GPU Spark against Polars GPU (Polars GPU / GPU Spark)", "polars_gpu_over_spark_gpu")]
    for label, field in comparators:
        if field not in rows[0]:
            continue
        usable = [r for r in rows if r[field] is not None]
        if not usable:
            text.append(f"- {label}: no query was timed on both engines.")
            continue
        best, worst = max(usable, key=lambda r: r[field]), min(usable, key=lambda r: r[field])
        text.append(f"- {label}: strongest {best['scale']}/{best['query']} ({best[field]:.2f}x); "
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
    for scale, loaded in sessions.items():
        for engine in engines:
            session = loaded.get(engine)
            if session is None:
                issue = problem(index, scale, engine, None)
                text.append(f"| {scale} / {engine} | {problem_cell(issue['kind'], issue['reason']) if issue else 'FAILED'} | unavailable | unavailable | unavailable |")
                continue
            warm = sum(r["seconds"] for (_, phase, _), r in session["indexed_records"].items() if phase == "warmup")
            process = next((r.get("process_wall_seconds") for r in schedule if r.get("engine") == engine and
                            "sf" + str(r.get("scale", "")).replace(".", "p") == scale), None)
            wall = f"{process:.3f}" if isinstance(process, (int, float)) else "unavailable"
            ready = session.get("launch_to_engine_ready_seconds")
            ready_text = f"{ready:.3f}" if ready is not None else "unavailable"
            text.append(f"| {scale} / {engine} | {ready_text} | {session['session_and_table_setup_seconds']:.3f} | {warm:.3f} | {wall} |")
    if "spark_gpu" in engines:
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
                 "- Complete operator counts and per-record checks: `correctness.json`; original plans, event logs, configuration, and GPU telemetry remain in the result directory."]
    if ext:
        text += ["", "- Polars GPU: strict-mode validation applies to " + str(len(evidence.get("polars_gpu_sessions", []))) + " session(s); "
                 "strict mode raises instead of falling back, so GPU plan-operator counts do not apply to it."] if "polars_gpu" in engines else []
        text += [""] + matrix_lines(rows, engines) + agreement_lines(evidence) + gpu_memory_lines(rows, engines) + failure_lines(evidence)
    if text[-1] == "":
        text.pop()
    text += ["", "Interpretation and limits:", "",
             "- All engines use one host and the CPU allocation recorded in the evidence. CPU engines run on the same GPU VM. This does not compare equivalent-cost CPU instances, multi-node scaling, or total production cost; no pricing advantage is established.",
             "- Files are preheated before each engine. Timings cover construction/optimization, Parquet reads, execution, and materialized result collection, with no application DataFrame cache. They measure a warm-filesystem workload, not cold remote storage. Preheating cannot guarantee every page remains resident.",
             "- The six selected TPC-H-derived queries are a bounded workload, not a complete or certified TPC-H benchmark. Monetary and quantity inputs use Float64/DOUBLE arithmetic rather than TPC-H decimal semantics. Parallel reductions are checked with the declared tolerance.",
             "- One untimed warmup per query precedes the measured trials; query order and engine order are seeded. Range values expose sample variability but three trials do not establish robust tail latency. Engine sessions run sequentially and are not isolated independent experiment repetitions.",
             "- Sampled process-tree RSS includes the Spark JVM and may double-count shared pages; it is not total machine memory, GPU memory, or a precise peak. GPU-plan evidence establishes acceleration of listed operators, not full GPU execution or the fraction of elapsed time spent there.",
             "- Results support conclusions for these datasets, query shapes, versions, settings, and this host. They do not establish a global winning engine."]
    if ext and "polars_gpu" in engines:
        text += ["- Polars GPU timings include device-to-host transfer of the collected result and exclude `to_dicts()`, like Polars CPU. Strict mode (`raise_on_fail=True`) "
                 "turns any unsupported operation or device-memory exhaustion into a recorded failure; a failure at a scale says nothing about other scales or executors."]
    text += ["",
             "Sources: [NVIDIA cuDF Spark overview](https://docs.nvidia.com/cudf-spark/latest/overview.html), "
             "[supported versions](https://nvidia.github.io/cudf-spark/docs/download.html), "
             "[CPU/GPU semantic differences](https://nvidia.github.io/cudf-spark/docs/compatibility.html).", ""]
    return "\n".join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engines", nargs="+", choices=ENGINE_ORDER,
                        help="analyze exactly these engines (must include polars); default: original three plus any discovered")
    args = parser.parse_args()
    root, output = args.results.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        engines = resolve_engines(args.engines, ()) if args.engines else None
    except ValueError as exc:
        parser.error(str(exc))
    sessions, evidence = load_and_validate(root, engines)
    write_json(output / "correctness.json", evidence)
    if not evidence["passed"] and (not evidence.get("integrity_violations") or evidence["errors"]):
        # Avoid leaving a previous successful report beside current failed checks.
        for name in ("summary.csv", "REPORT.md", "pairwise_speedups.csv"):
            (output / name).unlink(missing_ok=True)
        for error in evidence["errors"]:
            print(error, file=sys.stderr)
        return 1
    engines = tuple(evidence.get("engines", LEGACY_ENGINES))
    rows = summarize(sessions, engines, evidence.get("engine_failures", ()))
    with (output / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_columns(engines, gpu_memory_engines(sessions)))
        writer.writeheader()
        writer.writerows(rows)
    if is_extended(engines):
        write_pairwise_csv(output / "pairwise_speedups.csv", rows, engines)
    (output / "REPORT.md").write_text(report_text(root, sessions, evidence, rows))
    print(f"Verified {len(evidence['comparisons'])} outputs and {len(evidence['gpu_plans'])} GPU plans; report: {output / 'REPORT.md'}")
    violations = evidence.get("integrity_violations", [])
    for item in violations:
        print(f"{item['scale']}/{item['engine']}/{item['query'] or 'session'}: {item['kind']}: {item['reason']}", file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
