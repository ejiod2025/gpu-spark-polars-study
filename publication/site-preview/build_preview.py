#!/usr/bin/env python3
"""Build a private, buildless publication preview from validated report data.

No hosting registration, source synchronization, server, or deployment occurs.
The only generated files are inside this site-preview directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import zipfile


SITE = Path(__file__).resolve().parent
BENCHMARK = SITE.parents[1]
ENGINES = ("polars", "spark_cpu", "spark_gpu")
ORIGINAL_IDS = ("q01", "q03", "q06", "q10", "q12", "q14")
COMPLEX_IDS = ("cx_q09", "cx_q13", "cx_semi_anti", "cx_window", "cx_distinct", "cx_self_join")
COMPLEX_VERSION = "complex-v1"
COVERAGE = {"gpu_operators_present", "hybrid", "cpu_fallback"}
COMPLEX_PROTOCOL = {
    "id": "verified_gc", "label": "Verified cleanup after every query",
    "description": "The revised complex suite requests Python garbage collection for Polars and Python/JVM garbage collection for Spark after each query returns. Spark then waits until the observed owned shuffle files and bytes are zero, outside the query timer.",
    "timing_note": "Explicit garbage collection may alter the next query's heap state. All displayed complex scales and engines use this same protocol; the earlier attempt is excluded. Query medians exclude cleanup, while process wall time includes it. Cleanup totals cover the warmup and all three measured actions: adding them to a sum of query medians would not produce an end-to-end runtime.",
}
CLEANUP_SCOPE = "Session totals cover all saved warmup and measured barriers, including any cases excluded from the displayed performance comparison. Process wall time also includes startup, setup and other process overhead; it is not a cleanup-only measure."


def read_json(path):
    return json.loads(path.read_text())


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def scale_number(value):
    number = float(value.removeprefix("sf").replace("p", "."))
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Invalid scale label")
    return number


def positive(value, label):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"Invalid positive number: {label}")
    return number


def nonnegative(value, label, integer=False):
    if isinstance(value, bool) or integer and not isinstance(value, int):
        raise ValueError(f"Invalid nonnegative number: {label}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Invalid nonnegative number: {label}")
    return int(number) if integer else number


def query_definitions():
    # These modules contain definitions only; Polars is imported inside builders.
    sys.path.insert(0, str(BENCHMARK))
    import workloads
    import complex_workloads
    original_tables = workloads.REQUIRED_TABLES
    joins = {"q01": 0, "q03": 2, "q06": 0, "q10": 3, "q12": 1, "q14": 1}
    categories = {
        "q01": "Broad grouped aggregation", "q03": "Three-table join + top 10",
        "q06": "Selective scalar aggregation", "q10": "Four-table join + top 20",
        "q12": "Join + conditional counts", "q14": "Join + revenue ratio",
    }
    originals = [{"id": q, "description": workloads.DESCRIPTIONS[q],
        "category": categories[q], "sql": workloads.spark_sql(q),
        "tables": list(original_tables[q]), "join_count": joins[q]} for q in ORIGINAL_IDS]
    complexes = [{"id": q, "description": complex_workloads.DESCRIPTIONS[q],
        "category": complex_workloads.CATEGORIES[q], "sql": complex_workloads.spark_sql(q),
        "tables": list(complex_workloads.REQUIRED_TABLES[q]),
        "join_count": {"cx_q09": 5, "cx_q13": 1, "cx_semi_anti": 3,
                       "cx_window": 0, "cx_distinct": 3, "cx_self_join": 1}[q]}
        for q in COMPLEX_IDS]
    for query in complexes:
        if query["id"] == "cx_semi_anti":
            query.update({"description": "Correlated EXISTS/NOT EXISTS input design",
                "category": "Correlated subquery input", "join_count": None,
                "plan_note": "The SQL expresses correlated EXISTS and NOT EXISTS conditions. Saved SF30/SF100 plans from the earlier v1 attempt removed the LeftAnti branch and retained two LeftSemi joins. This input design is not evidence of observed GPU anti-join execution; final plans determine the executed operator coverage."})
    return originals, complexes


def load_rows(report, query_ids, complex_suite=False):
    source = report / "summary.csv"
    with source.open(newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)):
            raise ValueError("Duplicate summary CSV header")
        raw = list(reader)
    rows, seen = [], set()
    for record in raw:
        if None in record or record.get("query") not in query_ids:
            raise ValueError("Unknown query or malformed summary row")
        scale, query = record["scale"], record["query"]
        key = (scale, query)
        if key in seen:
            raise ValueError("Duplicate summary case")
        seen.add(key)
        trials = int(record["measured_trials"])
        if str(trials) != record["measured_trials"] or trials not in (1, 3):
            raise ValueError("Expected one smoke trial or three measured trials")
        times = {}
        for engine in ENGINES:
            low, median, high = [positive(record[engine + suffix], engine + suffix)
                for suffix in ("_min_seconds", "_median_seconds", "_max_seconds")]
            if not low <= median <= high or trials == 1 and not low == median == high:
                raise ValueError("Invalid trial range")
            times[engine] = {"min": low, "median": median, "max": high}
        ratios = {"polars_over_gpu": positive(record["polars_over_spark_gpu"], "Polars/GPU ratio"),
                  "spark_cpu_over_gpu": positive(record["spark_cpu_over_spark_gpu"], "CPU Spark/GPU ratio")}
        for name, engine in (("polars_over_gpu", "polars"), ("spark_cpu_over_gpu", "spark_cpu")):
            if not math.isclose(ratios[name], times[engine]["median"] / times["spark_gpu"]["median"], rel_tol=1e-10):
                raise ValueError("Summary ratio differs from actual median times")
        coverage = record.get("gpu_execution_class", "gpu_operators_present")
        if coverage not in COVERAGE:
            raise ValueError("Unknown GPU plan coverage class")
        policy = record.get("query_cleanup_policy", "none") or "none"
        if policy not in {"none", "verified_gc"}:
            raise ValueError("Unknown query cleanup policy")
        cleanup = {}
        if complex_suite and policy == "verified_gc":
            for engine in ENGINES:
                count = int(record[engine + "_cleanup_count"])
                if str(count) != record[engine + "_cleanup_count"] or count != trials + 1:
                    raise ValueError("Complex cleanup count must cover one warmup and every measured action")
                cleanup[engine] = {"barrier_count": count,
                    "cleanup_seconds": nonnegative(record[engine + "_cleanup_seconds"], "per-case cleanup duration")}
        rows.append({"scale": scale, "scale_factor": scale_number(scale), "query": query,
            "description": record.get("description", ""), "category": record.get("category", ""),
            "measured_trials": trials, "times": times, "ratios": ratios, "coverage": coverage,
            "query_cleanup_policy": policy, "cleanup": cleanup})
    rows.sort(key=lambda row: (row["scale_factor"], query_ids.index(row["query"])))
    if not rows:
        raise ValueError("No timing cases in report")
    return rows, source


def verify_saved_checks(checks, rows, complex_suite=False):
    keys = {(row["scale"], row["query"]): row for row in rows}
    comparisons = [item for item in checks.get("comparisons", []) if (item.get("scale"), item.get("query")) in keys]
    actual = Counter((v.get("scale"), v.get("query"), v.get("engine"), v.get("phase"), v.get("trial")) for v in comparisons)
    expected = Counter()
    for (scale, query), row in keys.items():
        for engine in ENGINES:
            expected[(scale, query, engine, "warmup", 0)] = 1
            for trial in range(1, row["measured_trials"] + 1):
                expected[(scale, query, engine, "measured", trial)] = 1
    if actual != expected or any(v.get("passed") is not True for v in comparisons):
        raise ValueError("Saved output comparisons are missing, duplicated, or failed")
    plans = [item for item in checks.get("gpu_plans", []) if (item.get("scale"), item.get("query")) in keys]
    actual_plans = Counter((v.get("scale"), v.get("query"), v.get("phase"), v.get("trial")) for v in plans)
    expected_plans = Counter((s, q, phase, trial) for s, q, engine, phase, trial in expected if engine == "spark_gpu")
    if actual_plans != expected_plans:
        raise ValueError("Saved final GPU-plugin plan coverage is incomplete")
    if not complex_suite:
        if any(not p.get("gpu_scans") or not (p.get("gpu_aggregates") or p.get("gpu_joins")) for p in plans):
            raise ValueError("Baseline final plans lack GPU scan plus aggregate/join evidence")
    elif any(p.get("gpu_execution_class") not in COVERAGE for p in plans):
        raise ValueError("Unclassified complex final plan")
    return {"outputs": len(comparisons), "gpu_plans": len(plans),
        "measured_outputs": sum(v["phase"] == "measured" for v in comparisons),
        "warmup_outputs": sum(v["phase"] == "warmup" for v in comparisons),
        "relative_tolerance": checks.get("relative_tolerance"),
        "absolute_tolerance": checks.get("absolute_tolerance"),
        "scope": "Cross-engine agreement with saved Polars warmup; includes warmups and baseline self-comparisons, not independent full-scale oracle proofs"}


def verify_cleanup(checks, rows):
    """Reconcile the portable query summary with the saved barrier evidence."""
    if {row["query_cleanup_policy"] for row in rows} != {"verified_gc"}:
        raise ValueError("Formal complex results must use uniform verified_gc; v1/legacy or mixed protocol curves are not accepted")
    if checks.get("verified_gc_cleanup_validated") is not True:
        raise ValueError("Complex report lacks the verified cleanup validation gate")
    keys = {(row["scale"], row["query"]): row for row in rows}
    expected = Counter()
    for (scale, query), row in keys.items():
        for engine in ENGINES:
            expected[(scale, query, engine, "warmup", 0)] = 1
            for trial in range(1, row["measured_trials"] + 1):
                expected[(scale, query, engine, "measured", trial)] = 1
    barriers = [item for item in checks.get("cleanup_checks", [])
        if (item.get("scale"), item.get("query")) in keys]
    actual = Counter((item.get("scale"), item.get("query"), item.get("engine"), item.get("phase"), item.get("trial"))
        for item in barriers)
    if actual != expected:
        raise ValueError("Cleanup evidence is missing, duplicated, or outside the expected saved-output slots")
    for item in barriers:
        if item.get("policy") != "verified_gc" or item.get("status") != "passed":
            raise ValueError("Complex cleanup barrier is not passed verified_gc")
        nonnegative(item.get("seconds"), "barrier duration")
        for field in ("scratch_before", "scratch_after"):
            scratch = item.get(field)
            if not isinstance(scratch, dict):
                raise ValueError("Complex cleanup lacks observed scratch counts")
            for measure in ("files", "bytes"):
                nonnegative(scratch.get(measure), "observed scratch count", integer=True)
        if item["engine"].startswith("spark_") and any(item["scratch_after"][key] != 0 for key in ("files", "bytes")):
            raise ValueError("Complex Spark cleanup leaves owned shuffle files or bytes")
    cases = []
    for (scale, query), row in keys.items():
        for engine in ENGINES:
            group = [item for item in barriers if (item["scale"], item["query"], item["engine"]) == (scale, query, engine)]
            elapsed = sum(item["seconds"] for item in group)
            recorded = row["cleanup"][engine]
            if len(group) != recorded["barrier_count"] or not math.isclose(elapsed, recorded["cleanup_seconds"], rel_tol=1e-10, abs_tol=1e-9):
                raise ValueError("Per-case cleanup summary differs from saved barriers")
            cases.append({"scale": scale, "query": query, "engine": engine,
                "barrier_count": len(group), "cleanup_seconds": elapsed})
    sessions = []
    for scale in sorted({row["scale"] for row in rows}, key=scale_number):
        for engine in ENGINES:
            matching = [item for item in checks.get("sessions", []) if item.get("scale") == scale and item.get("engine") == engine]
            if len(matching) != 1 or matching[0].get("query_cleanup_policy") != "verified_gc":
                raise ValueError("Complex session cleanup policy is missing, ambiguous, or inconsistent")
            session = matching[0]
            if session.get("cleanup_failure") or session.get("cleanup_failures"):
                raise ValueError("Complex session records a cleanup failure")
            count = nonnegative(session.get("explicit_cleanup_count"), "session barrier count", integer=True)
            elapsed = nonnegative(session.get("explicit_cleanup_sum_seconds"), "session cleanup duration")
            wall = positive(session.get("process_wall_seconds"), "session process wall")
            selected = [item for item in cases if item["scale"] == scale and item["engine"] == engine]
            if count < sum(item["barrier_count"] for item in selected) or elapsed + 1e-9 < sum(item["cleanup_seconds"] for item in selected) or wall + 1e-9 < elapsed:
                raise ValueError("Session cleanup totals cannot contain the displayed barriers or exceed process wall")
            sessions.append({"scale": scale, "engine": engine, "barrier_count": count,
                "cleanup_seconds": elapsed, "process_wall_seconds": wall})
    return {"status": "verified", "scope": CLEANUP_SCOPE, "sessions": sessions, "cases": cases}


def datasets(checks):
    allowed = ("scale", "scale_factor", "total_parquet_bytes", "lineitem_rows", "orders_rows", "total_rows", "manifest_sha256")
    result = {}
    for item in checks.get("datasets", []):
        if "scale" not in item:
            continue
        entry = {key: item[key] for key in allowed if key in item}
        entry["scale_factor"] = scale_number(entry["scale"])
        result[entry["scale"]] = entry
    return result


def totals(rows, query_ids, dataset_map, verified_totals=None):
    output = []
    for scale in sorted({row["scale"] for row in rows}, key=scale_number):
        group = [row for row in rows if row["scale"] == scale]
        if {row["query"] for row in group} != set(query_ids) or len(group) != len(query_ids):
            continue
        sums = {engine: sum(row["times"][engine]["median"] for row in group) for engine in ENGINES}
        if verified_totals is not None:
            matching = [item for item in verified_totals if item.get("scale") == scale and item.get("measured_trials") == 3]
            if len(matching) != 1:
                continue
            if matching[0].get("query_cleanup_policy") != "verified_gc":
                raise ValueError("Complex complete-suite score lacks the uniform verified_gc protocol")
            for engine in ENGINES:
                if not math.isclose(sums[engine], matching[0][engine + "_sum_medians_seconds"], rel_tol=1e-12):
                    raise ValueError("Complete-suite total differs from verified per-query medians")
        data = dataset_map.get(scale, {})
        output.append({"scale": scale, "scale_factor": scale_number(scale),
            "lineitem_rows": data.get("lineitem_rows"), "parquet_bytes": data.get("total_parquet_bytes"),
            "measured_trials": group[0]["measured_trials"],
            "times": {engine: {"sum_medians_seconds": value} for engine, value in sums.items()},
            "ratios": {"polars_over_gpu": sums["polars"] / sums["spark_gpu"],
                       "spark_cpu_over_gpu": sums["spark_cpu"] / sums["spark_gpu"]}})
    return output


def safe_exclusions(checks):
    result = []
    for item in checks.get("excluded_cases", []):
        if item.get("query") not in COMPLEX_IDS:
            raise ValueError("Unknown excluded complex case")
        result.append({"scale": item["scale"], "query": item["query"],
            "reasons": ["Incomplete or invalid case; excluded from performance comparisons"]})
    return result


def write(staging, relative, data):
    path = staging / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def evidence_link(label, url=None, note="", enabled=True, **extra):
    return {"label": label, "url": url, "enabled": bool(enabled and url), "note": note, **extra}


def original_suite(report, queries, staging):
    checks_path = report / "correctness.json"
    checks = read_json(checks_path)
    if any(checks.get(flag) is not True for flag in ("correctness_verified", "gpu_execution_verified", "all_selected_scales_validated")):
        raise ValueError("Baseline report is not completely verified")
    if checks.get("excluded_scales"):
        raise ValueError("Baseline report has excluded scales")
    rows, source_csv = load_rows(report, ORIGINAL_IDS)
    if any(row["measured_trials"] != 3 for row in rows):
        raise ValueError("Baseline must use three measured trials, not smoke timings")
    if {row["scale_factor"] for row in rows} != {1, 10, 30, 100, 300}:
        raise ValueError("Expected the five verified baseline scales")
    validation = verify_saved_checks(checks, rows)
    dataset_map = datasets(checks)
    score_rows = totals(rows, ORIGINAL_IDS, dataset_map)
    if len(score_rows) != 5:
        raise ValueError("Baseline must have six complete cases at all five scales")
    definitions = {q["id"]: q for q in queries}
    for row in rows:
        row["description"] = definitions[row["query"]]["description"]
        row["category"] = definitions[row["query"]]["category"]
    csv_data = source_csv.read_bytes()
    write(staging, "evidence/original-summary.csv", csv_data)
    public_checks = {"suite": "original", "status": "verified", "validation": validation,
        "datasets": list(dataset_map.values()), "source_csv_sha256": sha(csv_data),
        "private_validation_source_sha256": sha(checks_path.read_bytes()),
        "note": "Derived portable validation summary; inspect the attached draft bundle scope for which original saved outputs and plans are included"}
    write(staging, "evidence/original-validation.json", json_bytes(public_checks))
    chart = report / "crossover.png"
    if not chart.is_file():
        raise ValueError("Verified original crossover chart is missing")
    write(staging, "assets/original-crossover.png", chart.read_bytes())
    return {"id": "original", "label": "Original six queries", "status": "verified",
        "status_text": "Verified observations at five scales; no overall GPU-plugin Spark win was observed on this six-query workload.",
        "queries": queries, "rows": rows, "totals": score_rows, "validation": validation,
        "datasets": list(dataset_map.values()), "excluded_cases": [], "not_selected_cases": [],
        "chart": {"url": "assets/original-crossover.png", "alt": "Verified original workload scores and per-query relative GPU/Polars times across scale factors 1, 10, 30, 100, and 300"},
        "evidence": [evidence_link("Original query medians & ranges (CSV)", "evidence/original-summary.csv"),
                     evidence_link("Portable validation summary (JSON)", "evidence/original-validation.json")]}


def complex_suite(report, queries, staging, pending_scales=()):
    pending = sorted({"sf" + format(scale_number(str(value)), "g").replace(".", "p")
        for value in pending_scales}, key=scale_number)
    result = {"id": "complex", "label": "Complex query extension", "status": "pending",
        "status_text": "Formal complex-query results under the revised, uniform verified-cleanup protocol are pending. These designs are a separate suite; no smoke timings are presented as performance results.",
        "queries": queries, "rows": [], "totals": [], "validation": {"outputs": 0, "gpu_plans": 0,
            "measured_outputs": 0, "warmup_outputs": 0, "relative_tolerance": 1e-8, "absolute_tolerance": 1e-6},
        "protocol": COMPLEX_PROTOCOL,
        "cleanup": {"status": "pending", "scope": CLEANUP_SCOPE, "sessions": [], "cases": []},
        "chart": None, "excluded_cases": [], "not_selected_cases": [], "functional_smoke_cases": 0,
        "pending_scales": pending,
        "evidence": [evidence_link("Formal complex timing data", note="Pending verified three-trial results", enabled=False)]}
    if report is None:
        return result
    checks_path = report / "correctness.json"
    checks = read_json(checks_path)
    if checks.get("suite") != "complex" or checks.get("suite_version") != COMPLEX_VERSION or any(checks.get(flag) is not True for flag in ("correctness_verified", "gpu_plan_coverage_verified")):
        raise ValueError("Complex report lacks verified output/plan-coverage gates")
    all_rows, source_csv = load_rows(report, COMPLEX_IDS, complex_suite=True)
    definitions = {query["id"]: query for query in queries}
    for row in all_rows:
        if row["query"] in definitions:
            row["description"] = definitions[row["query"]]["description"]
            row["category"] = definitions[row["query"]]["category"]
    included = checks.get("included_cases", [])
    keys = [(item.get("scale"), item.get("query")) for item in included]
    if len(set(keys)) != len(keys) or set(keys) != {(row["scale"], row["query"]) for row in all_rows}:
        raise ValueError("Complex CSV cases do not match verified inclusion scope")
    if any(item.get("gpu_execution_class") != next(row["coverage"] for row in all_rows
           if (row["scale"], row["query"]) == (item.get("scale"), item.get("query"))) for item in included):
        raise ValueError("Complex coverage labels differ between CSV and validation")
    if any(item.get("query_cleanup_policy", "none") != next(row["query_cleanup_policy"] for row in all_rows
           if (row["scale"], row["query"]) == (item.get("scale"), item.get("query"))) for item in included):
        raise ValueError("Complex resource protocol differs between CSV and validation")
    excluded = safe_exclusions(checks)
    if {(e["scale"], e["query"]) for e in excluded} & set(keys):
        raise ValueError("Complex case is both included and excluded")
    # Verify all source checks before suppressing smoke values from the preview.
    verify_saved_checks(checks, all_rows, complex_suite=True)
    formal = [row for row in all_rows if row["measured_trials"] == 3]
    result["functional_smoke_cases"] = len(all_rows) - len(formal)
    result["excluded_cases"] = excluded
    result["not_selected_cases"] = [{"scale": item["scale"], "query": item["query"]}
        for item in checks.get("not_selected_cases", []) if item.get("query") in COMPLEX_IDS]
    if not formal:
        result["status"] = "smoke_only"
        result["status_text"] = "Functional smoke checks are available, but formal complex-query performance results remain pending. One-trial timings are intentionally not shown."
        return result
    validation = verify_saved_checks(checks, formal, complex_suite=True)
    cleanup = verify_cleanup(checks, formal)
    data = datasets(checks)
    score_rows = totals(formal, COMPLEX_IDS, data, checks.get("full_suite_totals", []))
    scales = {row["scale"] for row in formal}
    if set(pending) & scales:
        raise ValueError("A declared pending complex scale already has supplied formal cases; update --pending-complex-scales")
    complete = len(score_rows) == len(scales) and not excluded and not result["not_selected_cases"] and not pending and checks.get("all_selected_cases_validated") is True
    progress_text = ("Validated progress at " + ", ".join(scale.upper() for scale in sorted(scales, key=scale_number)) +
        "; " + ", ".join(scale.upper() for scale in pending) + " remains pending. Only supplied complete cases are shown; no larger-scale conclusion is available.") if pending else None
    result.update({"status": "complete" if complete else "partial", "rows": formal,
        "totals": score_rows, "validation": validation, "cleanup": cleanup, "datasets": list(data.values()),
        "status_text": progress_text or ("Complete for the formal cases in the supplied report; no claim is made about unreported scales."
                        if complete else "Partial formal coverage: only verified complete cases are shown. A six-case score appears only where all six cases passed.")})
    oracle_pairs = {(item["scale"], item["query"]) for item in checks.get("oracle_scope", {}).get("verified_cases", [])}
    result["independent_oracle_scope"] = {"formal_verified_cases": sum((r["scale"], r["query"]) in oracle_pairs for r in formal),
        "formal_uncovered_cases": sum((r["scale"], r["query"]) not in oracle_pairs for r in formal),
        "note": "Independent DuckDB checks apply only to matching covered cases; a small-scale oracle does not establish independent full-scale oracle correctness"}
    # Write formal rows only: source smoke timings never enter the preview payload.
    buffer = io.StringIO(newline="")
    columns = ["scale", "query", "measured_trials", "gpu_execution_class", "query_cleanup_policy",
        *[engine + "_" + name + "_seconds" for engine in ENGINES for name in ("median", "min", "max")],
        *[engine + "_" + name for engine in ENGINES for name in ("cleanup_count", "cleanup_seconds")]]
    writer = csv.DictWriter(buffer, fieldnames=columns); writer.writeheader()
    for row in formal:
        record = {"scale": row["scale"], "query": row["query"], "measured_trials": 3, "gpu_execution_class": row["coverage"], "query_cleanup_policy": "verified_gc"}
        record.update({engine + "_" + name + "_seconds": row["times"][engine][name] for engine in ENGINES for name in ("median", "min", "max")})
        record.update({engine + "_cleanup_count": row["cleanup"][engine]["barrier_count"] for engine in ENGINES})
        record.update({engine + "_cleanup_seconds": row["cleanup"][engine]["cleanup_seconds"] for engine in ENGINES})
        writer.writerow(record)
    write(staging, "evidence/complex-formal-summary.csv", buffer.getvalue().encode())
    write(staging, "evidence/complex-validation.json", json_bytes({"suite": "complex", "status": result["status"],
        "pending_scales": pending,
        "validation": validation, "protocol": COMPLEX_PROTOCOL, "cleanup": cleanup,
        "excluded_cases": excluded, "independent_oracle_scope": result["independent_oracle_scope"],
        "private_validation_source_sha256": sha(checks_path.read_bytes()), "source_csv_sha256": sha(source_csv.read_bytes())}))
    result["evidence"] = [evidence_link("Formal complex timings (CSV)", "evidence/complex-formal-summary.csv"),
                          evidence_link("Complex validation summary (JSON)", "evidence/complex-validation.json")]
    cleanup_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(cleanup_buffer, fieldnames=["scale", "engine", "barrier_count", "cleanup_seconds", "process_wall_seconds"])
    writer.writeheader(); writer.writerows(cleanup["sessions"])
    write(staging, "evidence/complex-cleanup-sessions.csv", cleanup_buffer.getvalue().encode())
    result["evidence"].append(evidence_link("Observed cleanup and process costs (CSV)", "evidence/complex-cleanup-sessions.csv",
        "All saved session barriers; separate from query medians. Process wall also includes startup, setup, warmups and query work."))
    chart = report / "complex.png"
    if chart.is_file() and len(formal) <= 12 and not result["functional_smoke_cases"]:
        write(staging, "assets/complex-formal.png", chart.read_bytes())
        result["chart"] = {"url": "assets/complex-formal.png", "alt": "Verified formal complex-query medians, trial ranges, and GPU-plugin to Polars comparison"}
    scale_charts = []
    for scale in sorted(scales, key=scale_number):
        chart = report / f"complex-{scale}.png"
        if chart.is_file() and not result["functional_smoke_cases"]:
            target = f"assets/complex-{scale}.png"
            write(staging, target, chart.read_bytes())
            scale_charts.append({"url": target, "scope": f"This figure covers {scale.upper()} only; other supplied scale figures are linked in the evidence section.",
                "alt": f"Verified {scale.upper()} complex-query medians, trial ranges, and CPU-to-GPU-plugin ratios; only this observed scale is shown"})
            result["evidence"].append(evidence_link(f"Complex {scale.upper()} figure (PNG)", target,
                f"Observed {scale.upper()} cases only; separate from the original workload and from any pending scale."))
    if result["chart"] is None and scale_charts:
        result["chart"] = scale_charts[-1]
    return result


def incident_summary(index, source_hash, staging):
    """Keep incident provenance without turning failures or saved trials into scores."""
    if not isinstance(index, dict) or index.get("evidence_kind") != "nonperformance_incident_index":
        raise ValueError("Draft bundle incident index has an invalid scope")
    safe = []
    kinds = {"recorded_disk_space_failure", "failed_or_incomplete_schedule_entry", "missing_engine_session"}
    for incident in index.get("incidents", []):
        if incident.get("eligible_for_complete_performance_summary") is not False or incident.get("evidence_kind") != "nonperformance_resource_or_incomplete_run_incident":
            raise ValueError("Incident is not explicitly excluded from performance summaries")
        run = incident.get("run", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", run):
            raise ValueError("Unsafe incident run identifier")
        observations = []
        for item in incident.get("observations", []):
            if item.get("kind") not in kinds or item.get("engine") not in ENGINES:
                continue
            scale = item.get("scale", "")
            normalized = scale if str(scale).startswith("sf") else "sf" + str(scale)
            scale_number(normalized)
            entry = {"kind": item["kind"], "engine": item["engine"], "scale": normalized}
            for key in ("query_error_count", "saved_result_records"):
                if key in item:
                    entry[key] = nonnegative(item[key], "incident count", integer=True)
            observations.append(entry)
        safe.append({"run": run, "eligible_for_performance_comparison": False, "observations": observations})
    if not safe:
        return pending_incident()
    source = {"evidence_kind": "nonperformance_incident_summary", "source_index_sha256": source_hash,
        "incidents": safe,
        "interpretation": "The earlier attempt is retained as resource-failure and recovery evidence. Its saved trials and failures are excluded from this page's performance tables and curves; they do not establish a GPU win, a performance bound, or an inherent single-query capacity limit."}
    write(staging, "evidence/prior-attempt-incident.json", json_bytes(source))
    disk_failure = any(item["kind"] == "recorded_disk_space_failure" for incident in safe for item in incident["observations"])
    summary = ("The earlier complex-suite attempt recorded disk-space errors in the SF300 GPU Spark session before completion. "
               if disk_failure else "An earlier complex-suite attempt has failed or incomplete session evidence. ")
    summary += "The attempt is preserved separately in the draft evidence bundle. Its timing records do not enter the displayed complex comparisons."
    return {"status": "documented", "title": "Earlier complex attempt: incident evidence", "summary": summary,
        "performance_use": "Excluded from performance comparisons",
        "evidence": [evidence_link("Prior-attempt incident summary (JSON)", "evidence/prior-attempt-incident.json",
            "Portable nonperformance summary; original saved outputs and errors remain in their separate draft-bundle namespace.")]}


def pending_incident():
    return {"status": "pending", "title": "Earlier complex attempt: incident evidence",
        "summary": "A separate incident record will document the earlier complex-suite attempt. No incident timings are included in the displayed comparisons.",
        "performance_use": "Excluded from performance comparisons",
        "evidence": [evidence_link("Prior-attempt incident summary", note="Incident evidence not attached to this preview", enabled=False)]}


def copy_draft_bundle(source, staging):
    if source is None:
        return [evidence_link("Draft reproducibility ZIP", note="Draft bundle not attached to this preview", enabled=False)], pending_incident()
    checksum = source.with_name(source.name + ".sha256")
    expected = checksum.read_text().split()[0]
    data = source.read_bytes()
    digest = sha(data)
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest != expected:
        raise ValueError("Draft evidence ZIP checksum differs")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename for info in files]
        if len(names) != len(set(names)) or sum(info.file_size for info in files) > 128 * 1024 * 1024:
            raise ValueError("Evidence ZIP has duplicate files or exceeds preview safety size")
        for info in files:
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Unsafe ZIP member")
        manifests = [name for name in names if name.endswith("/SHA256SUMS")]
        if len(manifests) != 1:
            raise ValueError("Draft ZIP lacks one content-checksum manifest")
        prefix = manifests[0].removesuffix("SHA256SUMS")
        covered = set()
        for line in archive.read(manifests[0]).decode().splitlines():
            wanted, relative = line.split("  ", 1)
            path = prefix + relative
            if path in covered or path not in names or sha(archive.read(path)) != wanted:
                raise ValueError("Draft ZIP content checksum failed")
            covered.add(path)
        if covered != set(names) - {manifests[0]}:
            raise ValueError("Draft ZIP content-checksum coverage is incomplete")
        incident_path = prefix + "INCIDENTS.json"
        incident = incident_summary(json.loads(archive.read(incident_path)), sha(archive.read(incident_path)), staging) if incident_path in names else pending_incident()
        bundle_path = prefix + "BUNDLE.json"
        bundle = json.loads(archive.read(bundle_path)) if bundle_path in names else {}
        integrity_links = []
        integrity_path = prefix + "artifacts/complex/postrun-input-integrity.json"
        if integrity_path in names:
            integrity = json.loads(archive.read(integrity_path))
            checked = integrity.get("datasets", [])
            if (integrity.get("all_pass") is not True or
                    {row.get("scale_factor") for row in checked} != {1, 10, 30, 100, 300} or
                    len(checked) != 5 or any(row.get("all_file_hashes_match") is not True or
                    row.get("no_missing_or_extra_parquet_files") is not True for row in checked)):
                raise ValueError("Post-run input integrity proof is incomplete or failed")
            count = sum(nonnegative(row.get("parquet_files_checked"), "integrity file count", integer=True)
                        for row in checked)
            write(staging, "evidence/postrun-input-integrity.json", archive.read(integrity_path))
            integrity_links.append(evidence_link("Post-run input checksums (JSON)", "evidence/postrun-input-integrity.json",
                f"All {count:,} Parquet files across five scales matched their original manifests after the final run."))
        scope = []
        for run in bundle.get("runs", []):
            name = run.get("run", "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                raise ValueError("Unsafe draft-bundle run identifier")
            sessions = []
            for session in run.get("sessions", []):
                if session.get("engine") not in ENGINES:
                    raise ValueError("Unknown engine in draft-bundle scope")
                number = float(session["scale_factor"])
                if not math.isfinite(number) or number <= 0:
                    raise ValueError("Invalid scale in draft-bundle scope")
                sessions.append({"scale_factor": number, "engine": session["engine"],
                    "query_cleanup_policy": session.get("query_cleanup_policy", "none"),
                    "saved_result_records": nonnegative(session.get("records"), "saved bundle record count", integer=True)})
            scope.append({"run": name, "eligible_for_complete_performance_summary": run.get("eligible_for_complete_performance_summary"),
                "sessions": sessions})
        write(staging, "evidence/attached-bundle-scope.json", json_bytes({"publication_status": "draft_not_cleared",
            "scope_note": "Only the runs and sessions listed here are included in the attached ZIP. The page's source reports may contain additional cases.",
            "runs": scope, "zip_sha256": digest}))
    write(staging, "downloads/reproducibility-draft.zip", data)
    write(staging, "downloads/reproducibility-draft.zip.sha256", f"{digest}  reproducibility-draft.zip\n".encode())
    note = "Private local draft evidence only; not cleared for external publication or redistribution."
    return [evidence_link("Draft reproducibility ZIP", "downloads/reproducibility-draft.zip", note, sha256=digest),
            evidence_link("ZIP checksum", "downloads/reproducibility-draft.zip.sha256", note),
            evidence_link("Attached draft-bundle scope (JSON)", "evidence/attached-bundle-scope.json",
                "Only the listed runs and sessions are included in the attached ZIP; source reports may cover additional cases."),
            *integrity_links], incident


def assert_portable(staging, data):
    forbidden = [re.compile(rb"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
                 re.compile(rb"GPU-[0-9a-fA-F-]{8,}"),
                 re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")]
    for path in staging.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".html", ".js", ".css", ".json", ".csv", ".txt"}:
            blob = path.read_bytes()
            if any(pattern.search(blob) for pattern in forbidden):
                raise ValueError("Private identifier/path detected in generated preview")
    links = [item for item in [*data["evidence"], *data["incident"]["evidence"]] if item["enabled"]]
    for suite_data in data["suites"].values():
        links += [item for item in suite_data["evidence"] if item["enabled"]]
        if suite_data["chart"]:
            links.append(suite_data["chart"])
    for link in links:
        name = PurePosixPath(link["url"])
        if name.is_absolute() or ".." in name.parts or not (staging / name).is_file():
            raise ValueError("Preview evidence or chart link is missing/unsafe")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report", type=Path, default=BENCHMARK / "reports/crossover-20261025")
    parser.add_argument("--complex-report", type=Path)
    parser.add_argument("--pending-complex-scales", nargs="+", default=[], metavar="SCALE",
        help="Planned complex scales still pending (e.g. 300); keeps validated supplied results visibly partial")
    parser.add_argument("--evidence-bundle", type=Path)
    parser.add_argument("--output", type=Path, default=SITE / "dist")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.output.is_symlink() or not output.is_relative_to(SITE) or output == SITE:
        parser.error("Generated output must be a child directory of site-preview")
    if output.exists() and not (output / "local-preview.json").is_file():
        parser.error("Refusing to overwrite a directory not owned by this preview builder")
    SITE.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".preview-build-", dir=SITE))
    try:
        for name in ("index.html", "styles.css", "app.js"):
            source = SITE / "template" / name
            if not source.is_file():
                raise ValueError(f"Preview template is missing: {name}")
            write(staging, name, source.read_bytes())
        original_queries, complex_queries = query_definitions()
        original = original_suite(args.baseline_report.resolve(), original_queries, staging)
        complex_data = complex_suite(args.complex_report.resolve() if args.complex_report else None, complex_queries, staging, args.pending_complex_scales)
        evidence, incident = copy_draft_bundle(args.evidence_bundle.resolve() if args.evidence_bundle else None, staging)
        banner = "Draft: complex results/public release/notices pending"
        if complex_data["status"] == "complete":
            banner = "Draft: public release/notices pending · complex results verified for supplied cases"
        elif complex_data["status"] == "partial":
            banner = "Draft: validated complex progress · " + (", ".join(s.upper() for s in complex_data["pending_scales"]) + " pending · " if complex_data["pending_scales"] else "") + "public release/notices pending"
        data = {"schema_version": 2, "publication": {"status": "draft_not_cleared", "banner": banner,
                "local_only": True, "external_publication": False},
            "study": {"title": "Does GPU-accelerated Spark beat Polars?",
                "subtitle": "A fixed-configuration, single-host experiment from 6 million to 1.8 billion line items.",
                "hardware": [{"label": "GPU", "value": "One NVIDIA A100-SXM4-40GB"},
                    {"label": "CPU allocation", "value": "30-vCPU AMD EPYC 7J13 host; Spark local[30], Polars pool 30"},
                    {"label": "Host memory", "value": "Approximately 216 GiB RAM; Spark driver heap 96 GiB"}],
                "software": [{"label": "Polars CPU", "value": "2.0.0 · streaming"},
                    {"label": "Spark / PySpark", "value": "3.5.9"},
                    {"label": "cuDF / RAPIDS plugin", "value": "26.08.1"},
                    {"label": "Python / Java", "value": "3.10.12 / OpenJDK 17.0.20.1"},
                    {"label": "Generator", "value": "DuckDB 1.5.6 · Snappy Parquet"},
                    {"label": "NVIDIA driver", "value": "580.105.08"}],
                "limitations": ["Six selected original queries, with four join queries already present; this is not a complete or certified TPC-H result.",
                    "Scores sum six per-query medians. They are not once-through suite runtimes or physical throughput measurements of the full 108 GB dataset.",
                    "One recorded warmup and three measured trials within one engine session per scale; descriptive ranges, not independent statistical experiments.",
                    "Filesystem preheating is attempted before each engine; residency is not guaranteed. No application DataFrame cache is used. Startup is excluded from query medians.",
                    "Monetary and quantity inputs use Float64/DOUBLE. Outputs are tolerance-checked against saved Polars warmups; these are not independent full-scale oracle proofs.",
                    "A single A100 host with different native memory budgets does not establish distributed scaling, equivalent-cost performance, energy use, or a universal product winner.",
                    "Final-plan operator names show selected GPU, hybrid, or CPU-fallback work; they do not measure runtime fractions. The complex suite remains separate from the original score.",
                    "The revised complex suite observes explicit cleanup outside each query timer. Garbage collection can affect the next query's heap state; all displayed complex cases use one uniform protocol, separate from the earlier attempt. Cleanup totals over warmup plus three actions cannot be added to a sum of medians to estimate end-to-end time.",
                    "An owner-private Sites review draft is being prepared. Public release, notices and any required permission/clearance remain unresolved. This page is the local review preview."],
                "sources": [{"label": "NVIDIA cuDF plugin for Apache Spark overview", "url": "https://docs.nvidia.com/cudf-spark/latest/overview.html"},
                    {"label": "DuckDB 1.5 TPC-H extension and generation parameters", "url": "https://duckdb.org/docs/1.5/core_extensions/tpch"}]},
            "suites": {"original": original, "complex": complex_data},
            "incident": incident, "evidence": evidence}
        write(staging, "data.json", json_bytes(data))
        write(staging, "local-preview.json", json_bytes({"owner": "spark-gpu-site-preview", "local_only": True,
            "publication_status": "draft_not_cleared", "built_at_utc": datetime.now(timezone.utc).isoformat(),
            "data_sha256": sha((staging / "data.json").read_bytes())}))
        assert_portable(staging, data)
        if output.exists():
            shutil.rmtree(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(output)
        print(json.dumps({"local_only": True, "publication_status": "draft_not_cleared",
            "output": str(output), "original_cases": len(original["rows"]),
            "complex_status": complex_data["status"], "formal_complex_cases": len(complex_data["rows"]),
            "external_registration_or_deployment": False}))
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


if __name__ == "__main__":
    main()
