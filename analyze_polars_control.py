#!/usr/bin/env python3
"""Validate a CPU software-stack control against the H100 baseline or complex study.

The primary four-engine run is revalidated before any timing is reported. The
control must run the same frozen sources, six queries, manifests and CPU protocol.
Python and Polars may both differ; this does not isolate a Polars version effect.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys

from analyze_crossover import collect
from analyze_results import (ABS_TOL, ENGINE_ORDER, REL_TOL, compare_values,
                             host_shape, validate_session)
import analyze_complex
import complex_workloads
import workloads


REQUIRED_SOURCES = ("worker.py", "workloads.py", "run_experiment.py", "query_cleanup.py")
COMPLEX_SOURCES = ("complex_workloads.py", "complex_joins.py", "complex_analytics.py")
PROTOCOL_FIELDS = ("suite", "suite_version", "queries", "query_descriptions", "runs", "cpu_threads",
                   "timing_scope", "cache_policy", "polars_execution_engine", "query_cleanup_policy",
                   "failure_policy")


def read_object(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def python_version(environment):
    match = re.match(r"(\d+\.\d+\.\d+)\b", str(environment.get("python", "")))
    if not match:
        raise ValueError("environment.json does not record a Python version")
    return match[1]


def scale_directories(root):
    found, numbers = {}, set()
    for path in sorted(root.glob("sf*")):
        if not path.is_dir():
            continue
        try:
            number = float(path.name[2:].replace("p", "."))
        except ValueError:
            raise ValueError(f"unrecognized scale directory: {path.name}") from None
        if not math.isfinite(number) or number <= 0 or number in numbers:
            raise ValueError(f"invalid or duplicate numeric scale: {path.name}")
        numbers.add(number)
        found[path.name] = path
    if not found:
        raise ValueError(f"no scale directories found: {root}")
    return dict(sorted(found.items(), key=lambda item: float(item[0][2:].replace("p", "."))))


def check_environment(primary, control, suite_name):
    required = REQUIRED_SOURCES + (COMPLEX_SOURCES if suite_name == "complex" else ())
    sources = []
    for label, environment in (("primary", primary), ("control", control)):
        hashes = environment.get("source_sha256")
        if not isinstance(hashes, dict) or not set(required) <= hashes.keys():
            raise ValueError(f"{label} source_sha256 lacks required benchmark/query sources")
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values()):
            raise ValueError(f"{label} contains invalid source SHA-256 values")
        sources.append(hashes)
        python_version(environment)
    if sources[0] != sources[1]:
        different = sorted(key for key in sources[0].keys() | sources[1].keys()
                           if sources[0].get(key) != sources[1].get(key))
        raise ValueError("frozen source SHA-256 maps differ: " + ", ".join(different))
    for name in (("workloads.py",) if suite_name == "baseline" else COMPLEX_SOURCES):
        current = hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
        if sources[0][name] != current:
            raise ValueError(f"recorded workload definitions differ from this analyzer's query registry: {name}")
    hosts = [host_shape(environment) for environment in (primary, control)]
    for field in ("vcpus", "cpus_arg", "host_ram_gib", "cpu_model"):
        if hosts[0].get(field) is None or hosts[1].get(field) != hosts[0][field]:
            raise ValueError(f"recorded host allocation differs or is missing: {field}")
    return {"source_sha256": sources[0], "primary_host": hosts[0], "control_host": hosts[1],
            "primary_python": python_version(primary), "control_python": python_version(control)}


def completed_schedule(root, scale, engine):
    schedule = json.loads((root / "schedule.json").read_text())
    if not isinstance(schedule, list):
        raise ValueError("schedule.json must be a list")
    matches = []
    for item in schedule:
        if not isinstance(item, dict) or item.get("engine") != engine:
            continue
        try:
            if float(item.get("scale")) == float(scale[2:].replace("p", ".")):
                matches.append(item)
        except (ValueError, TypeError):
            continue
    if len(matches) != 1:
        raise ValueError(f"expected exactly one completed {engine} schedule entry")
    item = matches[0]
    if item.get("status") != "complete" or type(item.get("returncode")) is not int or item["returncode"] != 0:
        raise ValueError(f"{engine} schedule did not finish successfully")
    return item


def cpu_contract(session, label, suite_name, registry):
    if session.get("suite") != suite_name or session.get("runs") != 3:
        raise ValueError(f"{label} must be the {suite_name} suite with three measured trials")
    if session.get("polars_execution_engine") != "streaming":
        raise ValueError(f"{label} is not CPU Polars streaming")
    if session.get("active_query") or any(session.get(field) for field in (
            "failure", "failed_at_utc", "cleanup_failure", "cleanup_failures", "query_errors", "query_failures")):
        raise ValueError(f"{label} records an execution or cleanup failure")
    if session.get("query_execution_status") != "complete":
        raise ValueError(f"{label} query execution is incomplete")
    policy = "verified_gc" if suite_name == "complex" else "none"
    if session.get("query_cleanup_policy") != policy or (policy == "none" and session.get("cleanup_records") != []):
        raise ValueError(f"{label} must preserve the {suite_name} {policy} cleanup lifecycle")
    if session.get("query_descriptions") != {query: registry.DESCRIPTIONS[query] for query in registry.QUERY_IDS}:
        raise ValueError(f"{label} query definitions/descriptions differ from the {suite_name} registry")
    if any(record.get("gpu_execution_class") != "cpu_engine" for record in session.get("records", [])):
        raise ValueError(f"{label} contains a record not classified as CPU execution")


def statistics_for(session, query):
    records = [record for (q, phase, _), record in session["indexed_records"].items()
               if q == query and phase == "measured"]
    times = [record["seconds"] for record in records]
    return {"median_seconds": statistics.median(times), "min_seconds": min(times), "max_seconds": max(times)}


def load_cpu(root, directory, suite_name, queries):
    if suite_name == "baseline":
        return validate_session(directory, "polars", {"gpu_plans": []})[0]
    schedule = json.loads((root / "schedule.json").read_text())
    session = analyze_complex.load_session(root, directory, "polars", schedule)
    session["indexed_records"] = {key: item["record"] for query in queries
                                  for key, item in analyze_complex.case_records(directory, "polars", session, query).items()}
    return session


def analyze(primary_root, control_root, include_gpu=False, suite_name="baseline"):
    registry = complex_workloads if suite_name == "complex" else workloads
    queries = tuple(registry.QUERY_IDS)
    evidence = {"tool": "analyze_polars_control", "format": 1,
                "suite": suite_name,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "primary_root": str(primary_root), "control_root": str(control_root),
                "scope": "CPU software-stack control; Python and Polars can both change, so this does not isolate a Polars version effect",
                "relative_tolerance": REL_TOL, "absolute_tolerance": ABS_TOL, "integer_comparison": "exact integer pairs",
                "comparisons": [], "failures": [], "scales": [], "primary_validation": {}, "passed": False,
                "counts": {"scales_expected": 0, "scales_validated": 0, "control_outputs_expected": 0,
                           "control_outputs_compared": 0, "control_output_mismatches": 0},
                "polars_gpu_reference_requested": include_gpu, "polars_gpu_reference_included": False}
    rows, totals = [], []

    def fail(stage, reason, scale=None):
        evidence["failures"].append({"stage": stage, "scale": scale, "reason": str(reason)})

    try:
        primary_dirs, control_dirs = scale_directories(primary_root), scale_directories(control_root)
        evidence["counts"].update(scales_expected=len(primary_dirs), control_outputs_expected=len(primary_dirs) * len(queries) * 4)
        if primary_dirs.keys() != control_dirs.keys():
            raise ValueError("primary/control scale sets differ; missing or extra control scales cannot form a complete control")
        gpu_stats = {}
        if suite_name == "baseline":
            primary_sessions, checked, _ = collect([primary_root], requested_engines=ENGINE_ORDER)
            primary_ok = checked.get("passed") and checked.get("all_selected_scales_validated") and not checked.get("engine_failures")
            for scale, sessions in primary_sessions.items():
                for query in queries:
                    gpu_stats[(scale, query)] = statistics_for(sessions["polars_gpu"], query)
        else:
            validated_rows, checked = analyze_complex.analyze([primary_root], [], requested_engines=ENGINE_ORDER)
            primary_ok = (checked.get("passed") and not any(checked.get(key) for key in (
                "excluded_cases", "session_errors", "oracle_errors", "integrity_violations"))
                and len(validated_rows) == len(primary_dirs) * len(queries))
            primary_sessions = {scale: {"polars": load_cpu(primary_root, directory, suite_name, queries)}
                                for scale, directory in primary_dirs.items()} if primary_ok else {}
            for row in validated_rows:
                gpu_stats[(row["scale"], row["query"])] = ({key: row["polars_gpu_" + key] for key in (
                    "median_seconds", "min_seconds", "max_seconds")} if row.get("polars_gpu_status") == "ok" else None)
        evidence["primary_validation"] = {key: checked.get(key) for key in (
            "passed", "correctness_verified", "gpu_execution_verified", "all_selected_scales_validated",
            "excluded_scales", "excluded_cases", "session_errors", "errors", "engine_failures", "integrity_violations")}
        evidence["primary_validation"].update(comparisons=len(checked["comparisons"]), gpu_plans=len(checked["gpu_plans"]))
        if not primary_ok:
            raise ValueError("primary four-engine evidence is not fully validated for its supported cells; no control timings or GPU references are reported")
        for scale in primary_dirs:
            for engine in ENGINE_ORDER:
                completed_schedule(primary_root, scale, engine)
        environment = check_environment(read_object(primary_root / "environment.json"), read_object(control_root / "environment.json"), suite_name)
        evidence["environment"] = environment
        evidence["polars_gpu_reference_included"] = include_gpu
    except (OSError, ValueError, TypeError, KeyError) as exc:
        fail("primary_and_environment", exc)
        return rows, totals, evidence

    for scale, directory in primary_dirs.items():
        primary = primary_sessions[scale]["polars"]
        control = None
        reason = None
        try:
            completed_schedule(control_root, scale, "polars")
            cpu_contract(primary, "primary CPU", suite_name, registry)
            control = load_cpu(control_root, control_dirs[scale], suite_name, queries)
            cpu_contract(control, "control CPU", suite_name, registry)
            if primary.get("version") != "1.44.2" or control.get("version") != "2.0.0":
                raise ValueError("expected matched primary CPU Polars 1.44.2 and control CPU Polars 2.0.0")
            for field in PROTOCOL_FIELDS:
                if field not in primary or field not in control or primary[field] != control[field]:
                    raise ValueError(f"CPU protocol differs or is missing: {field}")
            manifests = [(path / "manifest.json").read_bytes() for path in (directory, control_dirs[scale])]
            if manifests[0] != manifests[1] or control.get("manifest_sha256") != primary.get("manifest_sha256"):
                raise ValueError("primary/control input manifests differ")
            digest = hashlib.sha256(manifests[1]).hexdigest()
            if digest != control["manifest_sha256"] or control.get("scale_factor") != primary.get("scale_factor"):
                raise ValueError("copied manifest or scale differs from CPU session evidence")
            for (query, phase, trial), record in control["indexed_records"].items():
                expected = primary["indexed_records"][(query, "warmup", 0)]["rows"]
                comparison = {"scale": scale, "query": query, "phase": phase, "trial": trial,
                              **compare_values(expected, record["rows"])}
                evidence["comparisons"].append(comparison)
            bad = [item for item in evidence["comparisons"] if item["scale"] == scale and not item["passed"]]
            if bad:
                raise ValueError(f"{len(bad)} control output(s) differ from the matching primary CPU warmup beyond unchanged tolerances")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            reason = str(exc)
            fail("control_scale", reason, scale)
        evidence["scales"].append({"scale": scale, "passed": reason is None, "manifest_sha256": primary["manifest_sha256"],
                                   "control_outputs_compared": sum(item["scale"] == scale for item in evidence["comparisons"]),
                                   "failure": reason})
        scale_rows = []
        for query in queries:
            row = {"scale": scale, "query": query, "status": "ok" if reason is None else "failed", "failure": reason or ""}
            for label, session in (("primary_cpu", primary), ("control_cpu", control if reason is None else None)):
                row.update({label + "_" + key: value for key, value in (
                    statistics_for(session, query) if session else dict.fromkeys(("median_seconds", "min_seconds", "max_seconds"))).items()})
            row["primary_cpu_over_control_cpu"] = row["primary_cpu_median_seconds"] / row["control_cpu_median_seconds"] if reason is None else None
            if include_gpu:
                reference = gpu_stats.get((scale, query))
                row.update({"polars_gpu_" + key: value for key, value in (reference or dict.fromkeys((
                    "median_seconds", "min_seconds", "max_seconds"))).items()})
                row["control_cpu_over_polars_gpu"] = (row["control_cpu_median_seconds"] / row["polars_gpu_median_seconds"]
                                                       if reason is None and reference is not None else None)
            rows.append(row); scale_rows.append(row)
        score = {"scale": scale, "status": "ok" if reason is None else "failed", "failure": reason or ""}
        for label in ("primary_cpu", "control_cpu", *(("polars_gpu",) if include_gpu else ())):
            values = [row[label + "_median_seconds"] for row in scale_rows]
            score[label + "_sum_medians_seconds"] = sum(values) if all(value is not None for value in values) else None
        score["primary_cpu_over_control_cpu"] = score["primary_cpu_sum_medians_seconds"] / score["control_cpu_sum_medians_seconds"] if reason is None else None
        if include_gpu:
            score["control_cpu_over_polars_gpu"] = (score["control_cpu_sum_medians_seconds"] / score["polars_gpu_sum_medians_seconds"]
                                                     if reason is None and score["polars_gpu_sum_medians_seconds"] is not None else None)
        totals.append(score)
    evidence["counts"] = {"scales_expected": len(primary_dirs), "scales_validated": sum(item["passed"] for item in evidence["scales"]),
                          "control_outputs_expected": len(primary_dirs) * len(queries) * 4,
                          "control_outputs_compared": len(evidence["comparisons"]),
                          "control_output_mismatches": sum(not item["passed"] for item in evidence["comparisons"])}
    evidence["passed"] = not evidence["failures"] and evidence["counts"]["control_outputs_compared"] == evidence["counts"]["control_outputs_expected"]
    return rows, totals, evidence


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["scale", "query", "status", "failure"])
        writer.writeheader(); writer.writerows(rows)


def report(rows, totals, evidence):
    env = evidence.get("environment", {})
    gpu = evidence["polars_gpu_reference_included"]
    labels = {"primary_cpu": "Primary CPU Polars 1.44.2", "control_cpu": "Control CPU Polars 2.0.0"}
    if gpu:
        labels["polars_gpu"] = "Primary Polars GPU 1.44.2"
    text = ["# CPU software-stack control", "", f"Suite: **{evidence['suite']}**.", "",
            f"Primary CPU: Polars 1.44.2 / Python {env.get('primary_python', 'unverified')}. "
            f"Control CPU: Polars 2.0.0 / Python {env.get('control_python', 'unverified')}. Both use CPU streaming.", "",
            "Python and Polars change together. This is a CPU software-stack control, not an isolated Polars version effect. "
            "The same frozen benchmark/query sources, input manifests, CPU thread allocation, timing scope, cache policy, "
            "one warmup and three measured trials, and suite cleanup lifecycle must pass validation "
            "(`none` for baseline; `verified_gc` for complex). "
            "CPU result agreement uses the original tolerances and exact integer pairs.", "",
            "Validation: **" + ("PASS" if evidence["passed"] else "FAIL") + "**. " +
            f"Control outputs compared: {len(evidence['comparisons'])}; output mismatches: "
            f"{sum(not item['passed'] for item in evidence['comparisons'])}; validation failures: {len(evidence['failures'])}.", ""]
    if gpu:
        text += ["Polars GPU values are references from the separately revalidated primary run. Only validated GPU cells are included; "
                 "unsupported queries have no GPU timing or complete GPU suite score. "
                 "They were not rerun under the control Python/Polars stack.", ""]
    text += ["Ratios divide the first named engine's elapsed time by the second's. Above 1 favors the denominator. "
             "Suite scores sum six per-query medians; they are not once-through suite runtimes. A failed or incomplete control scale has no control score or ratio.", "",
             "| Scale | Status | " + " | ".join(label + " sum (s)" for label in labels.values()) + " | Primary CPU / control CPU |" +
             (" Control CPU / Polars GPU |" if gpu else ""),
             "|---|---|" + "---:|" * (len(labels) + 1 + gpu)]
    fmt = lambda value: "n/a" if value is None else f"{value:.6f}"
    for row in totals:
        values = [row[label + "_sum_medians_seconds"] for label in labels] + [row["primary_cpu_over_control_cpu"]]
        if gpu:
            values.append(row["control_cpu_over_polars_gpu"])
        text.append(f"| {row['scale']} | {row['status']} | " + " | ".join(map(fmt, values)) + " |")
    text += ["", "## Per-query measured times", "", "Median [minimum, maximum] seconds over the three measured trials.", "",
             "| Scale / query | Status | " + " | ".join(labels.values()) + " |", "|---|---|" + "---:|" * len(labels)]
    for row in rows:
        values = ["n/a" if row[label + "_median_seconds"] is None else
                  f"{row[label + '_median_seconds']:.6f} [{row[label + '_min_seconds']:.6f}, {row[label + '_max_seconds']:.6f}]" for label in labels]
        text.append(f"| {row['scale']} / {row['query']} | {row['status']} | " + " | ".join(values) + " |")
    text += ["", "## Validation failures", ""]
    text += [f"- {item['scale'] or 'all scales'} / {item['stage']}: {item['reason']}" for item in evidence["failures"]] or ["None."]
    return "\n".join(text) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-root", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new output directory; existing directories are never overwritten")
    parser.add_argument("--suite", choices=["baseline", "complex"], default="baseline")
    parser.add_argument("--include-polars-gpu", action="store_true", help="include GPU references only after the primary four-engine evidence fully validates")
    args = parser.parse_args()
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("--output must be a new directory")
    rows, totals, evidence = analyze(args.primary_root.resolve(), args.control_root.resolve(), args.include_polars_gpu, args.suite)
    (args.output / "correctness.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    write_csv(args.output / "summary.csv", rows)
    write_csv(args.output / "suite_scores.csv", totals)
    (args.output / "CPU_STACK_CONTROL.md").write_text(report(rows, totals, evidence))
    print(f"CPU software-stack control: {'PASS' if evidence['passed'] else 'FAIL'}; "
          f"{len(evidence['comparisons'])} outputs compared; {len(evidence['failures'])} validation failures; "
          f"report: {args.output.resolve() / 'CPU_STACK_CONTROL.md'}")
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
