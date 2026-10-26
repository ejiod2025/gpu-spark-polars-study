#!/usr/bin/env python3
"""Inspect saved Spark query task metrics without changing benchmark evidence.

Uses two streaming passes through uncompressed JSON event logs. Query records
must map one-to-one to collect SQL executions; jobs then map executions to stages.
Ambiguous attribution is flagged and its metrics are withheld. Failed task
attempts and duplicate successful partitions do not inflate successful work.

Only Spark engines (spark_cpu, spark_gpu and any other spark_* result directory)
have event logs. Other non-legacy engines such as polars_gpu are listed under
non_spark_engines_skipped rather than inspected; their GPU memory is reported by
the analyzers from the worker's peak_gpu_memory_bytes field.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
import sys


STANDARD = {
    "memory_bytes_spilled": ("Memory Bytes Spilled", "bytes", "sum"),
    "disk_bytes_spilled": ("Disk Bytes Spilled", "bytes", "sum"),
    "peak_execution_memory": ("Peak Execution Memory", "bytes", "maximum"),
    "executor_run_time": ("Executor Run Time", "milliseconds", "sum"),
    "executor_cpu_time": ("Executor CPU Time", "nanoseconds", "sum"),
    "jvm_gc_time": ("JVM GC Time", "milliseconds", "sum"),
}
RAPIDS = {
    "gpuMaxDeviceMemoryBytes": ("bytes", "maximum"),
    "gpuMaxTaskFootprint": ("bytes", "maximum"),
    "gpuMaxHostMemoryBytes": ("bytes", "maximum"),
    "gpuMaxPinnedMemoryBytes": ("bytes", "maximum"),
    "gpuMaxPageableMemoryBytes": ("bytes", "maximum"),
    "gpuTime": ("seconds", "sum"),
    "gpuSemaphoreWait": ("seconds", "sum"),
}


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.timestamp() * 1000


def numeric(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a metric")
    if isinstance(value, (int, float)):
        result = value
    else:
        text = str(value)
        byte_count = re.search(r"\((\d+) bytes\)", text)
        if byte_count:
            result = int(byte_count[1])
        elif re.fullmatch(r"\d+:\d\d:\d\d(?:\.\d+)?", text):
            hours, minutes, seconds = text.split(":")
            result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        else:
            result = float(text)
    if not math.isfinite(result) or result < 0:
        raise ValueError("non-finite or negative metric")
    return result


def events(paths, issues):
    for path in paths:
        try:
            with path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict) or "Event" not in event:
                            raise ValueError("not a Spark event object")
                        yield event
                    except (ValueError, TypeError) as exc:
                        issues.append(f"{path}:{line_number}: {exc}")
        except (OSError, UnicodeError) as exc:
            issues.append(f"{path}: cannot read uncompressed UTF-8 JSON event log: {exc}")


def event_paths(directory):
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*") if path.is_file()
                  and not path.name.startswith(".") and not path.name.startswith("appstatus"))


def aggregate(selected):
    standard = {}
    custom = {}
    missing = []
    count = len(selected)
    for output, (name, unit, operation) in STANDARD.items():
        values = [task["standard"][name] for task in selected if name in task["standard"]]
        standard[output] = {"value": (max(values) if operation == "maximum" else sum(values)) if values else None,
                            "unit": unit, "aggregation": operation,
                            "covered_tasks": len(values), "missing_tasks": count - len(values),
                            "nonzero_tasks": sum(value != 0 for value in values),
                            "source_field": "SparkListenerTaskEnd.Task Metrics." + name}
        if len(values) < count or not values:
            missing.append(standard[output]["source_field"])
    names = set(RAPIDS) | {name for task in selected for name in task["custom"]}
    for name in sorted(names):
        unit, operation = RAPIDS.get(name, ("raw numeric; inspect source name", "maximum" if "Max" in name else "sum"))
        values = [task["custom"][name] for task in selected if name in task["custom"]]
        custom[name] = {"value": (max(values) if operation == "maximum" else sum(values)) if values else None,
                        "unit": unit, "aggregation": operation,
                        "covered_tasks": len(values), "missing_tasks": count - len(values),
                        "source_field": "SparkListenerTaskEnd.Task Info.Accumulables[Name=" + name + "].Update"}
        if not values:
            missing.append(custom[name]["source_field"])
    spill_names = sorted(name for name in names if "spill" in name.lower())
    return {"selected_successful_tasks": count, "spark_task_metrics": standard,
            "rapids_task_metrics": custom, "missing_metric_fields": missing,
            "explicit_gpu_spill_metric_names": spill_names,
            "gpu_device_host_spill_quantified": False,
            "gpu_spill_coverage_note": "Spark memory/disk spill fields do not quantify RAPIDS device-to-host spilling. "
            + ("Additional spill-named custom task updates are retained with their exact names; their transfer semantics require interpretation."
               if spill_names else "No explicit spill-named GPU task accumulator was present in these selected tasks.")}


def inspect(root, scale, engine):
    issues = []
    result_path = scale / engine / "results.json"
    paths = event_paths(scale / (engine + "-events"))
    session_result = {"source_root": str(root), "scale": scale.name, "engine": engine,
                      "results_path": str(result_path), "event_log_paths": [str(path) for path in paths],
                      "records": [], "issues": issues}
    try:
        session = json.loads(result_path.read_text())
        if not isinstance(session, dict):
            raise ValueError("results.json is not an object")
    except (OSError, ValueError, TypeError) as exc:
        issues.append(f"{result_path}: {exc}")
        return session_result
    if not paths:
        issues.append("No event logs found")
    records = []
    for index, record in enumerate(session.get("records", [])):
        records.append({**record, "source_record_index": index, "record_status": "saved"})
    active = session.get("active_query")
    if isinstance(active, dict) and active.get("started_at_utc"):
        records.append({**active, "ended_at_utc": session.get("failed_at_utc"), "source_record_index": None,
                        "record_status": "active_or_failed"})

    starts, ends, stage_executions = {}, {}, defaultdict(set)
    ambiguous_execution_ids = set()
    successful_stage_attempts = defaultdict(set)
    event_counts = Counter()
    for event in events(paths, issues):
        kind = event["Event"]
        event_counts[kind] += 1
        if kind.endswith("SparkListenerSQLExecutionStart"):
            execution = event["executionId"]
            if execution in starts:
                issues.append(f"Duplicate SQL execution start {execution}; event logs may contain multiple applications")
                ambiguous_execution_ids.add(execution)
            starts[execution] = {key: event.get(key) for key in ("executionId", "rootExecutionId", "description", "time")}
        elif kind.endswith("SparkListenerSQLExecutionEnd"):
            ends[event["executionId"]] = {key: event.get(key) for key in ("time", "errorMessage")}
        elif kind == "SparkListenerJobStart":
            execution = event.get("Properties", {}).get("spark.sql.execution.id")
            if execution is not None:
                try:
                    for stage in event.get("Stage IDs", []):
                        stage_executions[stage].add(int(execution))
                except (ValueError, TypeError):
                    issues.append(f"Invalid spark.sql.execution.id: {execution}")
        elif kind == "SparkListenerStageCompleted":
            info = event["Stage Info"]
            if not info.get("Failure Reason"):
                successful_stage_attempts[info["Stage ID"]].add(info.get("Stage Attempt ID", 0))

    record_executions, execution_records = {}, defaultdict(list)
    for index, record in enumerate(records):
        matches = []
        try:
            lower = timestamp(record["started_at_utc"])
            upper = timestamp(record["ended_at_utc"]) if record.get("ended_at_utc") else math.inf
            if upper < lower:
                raise ValueError("query end precedes query start")
            matches = [execution for execution, event in starts.items()
                       if str(event["description"]).startswith("collect") and lower <= event["time"] <= upper]
        except (ValueError, TypeError, KeyError) as exc:
            issues.append(f"Record {index}: invalid query time window: {exc}")
        record_executions[index] = matches
        for execution in matches:
            execution_records[execution].append(index)

    task_counts = defaultdict(Counter)
    chosen = defaultdict(dict)
    seen_task_ends = set()
    custom_names = set()
    ignored = Counter()
    metric_parse_errors = Counter()
    for event in events(paths, issues):
        if event["Event"] != "SparkListenerTaskEnd":
            continue
        info = event.get("Task Info", {})
        stage = event["Stage ID"]
        attempt = event.get("Stage Attempt ID", 0)
        identity = (stage, attempt, info.get("Task ID"))
        if info.get("Task ID") is None:
            issues.append(f"Stage {stage} attempt {attempt}: task ID is absent; attribution omitted")
            ignored["tasks_without_task_id"] += 1
            continue
        if identity in seen_task_ends:
            ignored["duplicate_task_end_events"] += 1
            continue
        seen_task_ends.add(identity)
        executions = stage_executions.get(stage, set())
        if len(executions) != 1:
            ignored["tasks_with_ambiguous_stage_execution" if executions else "tasks_without_sql_execution"] += 1
            continue
        execution = next(iter(executions))
        if execution not in execution_records:
            ignored["tasks_without_worker_query_record"] += 1
            continue
        counts = task_counts[execution]
        counts["all_attempts"] += 1
        reason = event.get("Task End Reason", {}).get("Reason", "unknown")
        success = reason == "Success" and not info.get("Failed") and not info.get("Killed")
        if not success:
            counts["killed_attempts" if info.get("Killed") or reason == "TaskKilled" else "failed_attempts"] += 1
            continue
        counts["successful_attempts"] += 1
        valid_attempts = successful_stage_attempts.get(stage, set())
        if not valid_attempts or attempt != max(valid_attempts):
            counts["successes_outside_final_successful_stage_attempt"] += 1
            continue
        partition = info.get("Partition ID", info.get("Index"))
        if partition is None:
            issues.append(f"Task {identity}: partition/index is absent; cannot deduplicate attempts")
            continue
        key = (stage, attempt, partition)
        standard, custom = {}, {}
        for _, (name, _, _) in STANDARD.items():
            if name in event.get("Task Metrics", {}):
                try:
                    standard[name] = numeric(event["Task Metrics"][name])
                except (ValueError, TypeError):
                    metric_parse_errors[name] += 1
        for accumulator in info.get("Accumulables", []):
            name = accumulator.get("Name", "")
            if not name.startswith("gpu") or "Update" not in accumulator:
                continue
            custom_names.add(name)
            if name not in RAPIDS and "spill" not in name.lower():
                continue
            try:
                custom[name] = numeric(accumulator["Update"])
            except (ValueError, TypeError):
                metric_parse_errors[name] += 1
        task = {"finish_time": info.get("Finish Time", math.inf), "standard": standard, "custom": custom}
        if key in chosen[execution]:
            counts["duplicate_successful_partition_attempts"] += 1
            # The first successful speculative attempt normally commits a partition.
            if chosen[execution][key]["finish_time"] <= task["finish_time"]:
                continue
        chosen[execution][key] = task

    ambiguous_stages = {stage: sorted(executions) for stage, executions in stage_executions.items() if len(executions) > 1}
    for index, record in enumerate(records):
        matches = record_executions[index]
        mapping_issues = []
        if len(matches) != 1:
            mapping_issues.append(f"Expected one collect SQL execution in record time window, found {len(matches)}")
        execution = matches[0] if len(matches) == 1 else None
        stages = sorted(stage for stage, executions in stage_executions.items() if execution is not None and execution in executions)
        if execution is not None and len(execution_records[execution]) != 1:
            mapping_issues.append("SQL execution falls in multiple worker record windows")
        if execution in ambiguous_execution_ids:
            mapping_issues.append("SQL execution ID has duplicate start events; attribution withheld")
        if any(stage in ambiguous_stages for stage in stages):
            mapping_issues.append("Stage IDs are shared across multiple SQL executions; attribution withheld")
        if execution is not None and not stages:
            mapping_issues.append("SQL execution has no stage IDs in job-start events")
        if execution is not None and not chosen[execution] and record["record_status"] == "saved":
            mapping_issues.append("No tasks from a completed successful stage attempt were available")
        row = {key: record.get(key) for key in ("query", "phase", "trial", "seconds", "started_at_utc", "ended_at_utc",
                                               "source_record_index", "record_status")}
        row.update({"source_root": str(root), "scale": scale.name, "engine": engine,
                    "results_path": str(result_path), "event_log_paths": [str(path) for path in paths],
                    "mapping_status": "ambiguous_or_incomplete" if mapping_issues else "matched",
                    "mapping_issues": mapping_issues, "candidate_sql_execution_ids": matches,
                    "sql_execution_id": execution, "sql_execution_start": starts.get(execution),
                    "sql_execution_end": ends.get(execution), "stage_ids": stages,
                    "selected_successful_stage_attempts": {str(stage): max(successful_stage_attempts[stage])
                        for stage in stages if successful_stage_attempts.get(stage)},
                    "task_attempt_counts": {name: task_counts[execution][name] for name in (
                        "all_attempts", "successful_attempts", "failed_attempts", "killed_attempts",
                        "successes_outside_final_successful_stage_attempt", "duplicate_successful_partition_attempts")}
                        if execution is not None else {},
                    "metrics": aggregate(list(chosen[execution].values())) if execution is not None and not mapping_issues else None})
        session_result["records"].append(row)
    session_result.update({"event_counts": dict(event_counts), "ambiguous_stage_execution_ids": ambiguous_stages,
                           "ignored_task_counts": dict(ignored), "observed_rapids_accumulator_names": sorted(custom_names),
                           "metric_parse_error_counts": dict(metric_parse_errors),
                           "unmatched_collect_executions": [event for execution, event in starts.items()
                               if str(event["description"]).startswith("collect") and execution not in execution_records],
                           "active_query": active, "completed_at_utc": session.get("completed_at_utc"),
                           "failed_at_utc": session.get("failed_at_utc")})
    issues[:] = list(dict.fromkeys(issues))
    return session_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    roots = [path.expanduser().resolve() for path in args.results]
    for root in roots:
        if not root.is_dir():
            parser.error(f"Result root is not a directory: {root}")
    sessions = []
    skipped = set()
    for root in roots:
        scales = []
        for path in root.glob("sf*"):
            try:
                numeric_scale = Decimal(path.name[2:].replace("p", "."))
                if path.is_dir() and numeric_scale.is_finite() and numeric_scale > 0:
                    scales.append((numeric_scale, path))
            except InvalidOperation:
                continue
        for _, scale in sorted(scales):
            present = [child.name for child in scale.iterdir() if child.is_dir() and not child.name.endswith("-events")]
            for engine in dict.fromkeys(("spark_cpu", "spark_gpu", *sorted(n for n in present if n.startswith("spark_")))):
                sessions.append(inspect(root, scale, engine))
            skipped.update(n for n in present if not n.startswith("spark_") and n not in ("polars", "plans"))
    records = [record for session in sessions for record in session["records"]]
    matched = sum(record["mapping_status"] == "matched" for record in records)
    passed = bool(records) and matched == len(records) and not any(session["issues"] or session.get("metric_parse_error_counts") for session in sessions)
    result = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "source_roots": [str(root) for root in roots],
              "passed": passed, "coverage": {"sessions": len(sessions), "worker_query_records": len(records),
                  "matched_records": matched, "ambiguous_or_incomplete_records": len(records) - matched},
              "interpretation": [
                  "Diagnostic attribution only: this tool does not validate query outputs or declare performance winners.",
                  "Spark Memory Bytes Spilled / Disk Bytes Spilled do not establish GPU device-to-host spill volume or absence.",
                  "RAPIDS gpuMax* values are maxima of task-reported updates, not preallocated RMM pool size or an independent device-memory trace.",
                  "gpuTime, gpuSemaphoreWait, executor runtime and GC totals are cumulative task metrics. Overlapping tasks mean they are not wall-time fractions or kernel-time percentages.",
                  "Task Info.Accumulables.Update is aggregated once per selected successful partition; cumulative Value and Stage Info.Accumulables are not added again.",
                  "The latest completed successful stage attempt is selected; for duplicate successful speculative partitions, the earliest finishing successful task is selected.",
                  "Zero values are distinct from absent metrics. Missing or unparseable task fields remain explicitly uncovered.",
                  "Collect SQL executions map by worker UTC time windows, then SparkListenerJobStart.Properties.spark.sql.execution.id and Stage IDs. Ambiguous mappings withhold metrics.",
                  "Uncompressed UTF-8 JSON event logs are supported. Read/parse failures are explicit issues; no result evidence is modified."],
              "sessions": sessions}
    if skipped:
        result["non_spark_engines_skipped"] = sorted(skipped)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(f"Matched {matched}/{len(records)} query records in {len(sessions)} sessions; diagnostics: {output}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
