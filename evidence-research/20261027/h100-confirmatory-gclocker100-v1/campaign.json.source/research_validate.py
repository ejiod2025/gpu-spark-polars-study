#!/usr/bin/env python3
"""Gate prospective campaign outputs against independent SQL oracle evidence.

Operational completion is separate from correctness. No warmup or measured
output is sampled away, and no failed case is converted into a timing.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path

from analyze_results import compare_values, check_polars_gpu_metadata, inspect_gpu_plan
from analyze_complex import inspect_cleanup, inspect_plan
from research_campaign import atomic_json, load_plan, sha256

EXPECTED_UNSUPPORTED = {"cx_q13", "cx_window"}


def read_json(path):
    return json.loads(Path(path).read_text())


def load_oracle(path, suite, scale, manifest_sha256):
    value = read_json(path)
    module = importlib.import_module("workloads" if suite == "baseline" else "complex_workloads")
    if (value.get("suite") != suite or float(value.get("scale_factor", -1)) != float(scale)
            or value.get("manifest_sha256") != manifest_sha256
            or value.get("suite_version") != getattr(module, "SUITE_VERSION", "baseline-v1")
            or not value.get("completed_at_utc") or value.get("status", "PASS") != "PASS"):
        raise ValueError(f"Oracle identity/completion differs: {path}")
    if set(value.get("queries", {})) != set(module.QUERY_IDS):
        raise ValueError(f"Oracle query set differs: {path}")
    for query, result in value["queries"].items():
        if result.get("query_sha256") != hashlib.sha256(module.spark_sql(query).encode()).hexdigest():
            raise ValueError(f"Oracle SQL differs: {query}")
        if not isinstance(result.get("rows"), list) or not all(isinstance(row, dict) for row in result["rows"]):
            raise ValueError(f"Oracle rows are invalid: {query}")
        if not compare_values(result["rows"], result["rows"])["passed"]:
            raise ValueError(f"Oracle contains non-finite numeric output: {query}")
    return value


def remap_path(plan, path, artifacts_root=None):
    path = Path(path)
    if artifacts_root is None:
        return path
    return Path(artifacts_root) / path.relative_to(Path(plan["output_root"]))


def audit_session(plan, session, item, oracle, artifacts_root=None):
    out = {"session_id": session["session_id"], "status": "FAIL", "errors": [],
           "coverage": {}, "comparisons": [], "plans": []}
    if item.get("status") != "succeeded":
        out["errors"].append(f"Session status is {item.get('status')}")
        return out
    try:
        attempts = item["attempts"]
        if len(attempts) != 1:
            raise ValueError("Replacement attempt requires a separately reviewed protocol-deviation analysis")
        attempt = attempts[0]
        frozen_run_root = Path(attempt["run_root"])
        run_root = remap_path(plan, frozen_run_root, artifacts_root)
        record = read_json(run_root.parent / "session.json")
        if record["session"] != session or record["attempt"] != attempt:
            raise ValueError("Session record differs from journal/plan")
        if record["campaign_sha256"] != plan["_sha256"]:
            raise ValueError("Session belongs to a different campaign")
        path = run_root / f"sf{session['scale']}" / session["engine"] / "results.json"
        frozen_path = frozen_run_root / f"sf{session['scale']}" / session["engine"] / "results.json"
        if (str(frozen_path) != attempt["artifacts"]["results_path"]
                or sha256(path) != attempt["artifacts"]["results_sha256"]
                or sha256(run_root / "schedule.json") != attempt["artifacts"]["schedule_sha256"]):
            raise ValueError("Successful artifact identity changed")
        value = read_json(path)
        module = importlib.import_module("workloads" if session["suite"] == "baseline" else "complex_workloads")
        if (value.get("suite") != session["suite"] or value.get("engine") != session["engine"]
                or value.get("runs") != 3 or value.get("query_seed") != session["query_seed"]
                or float(value.get("scale_factor", -1)) != float(session["scale"])
                or value.get("manifest_sha256") != oracle["manifest_sha256"]
                or value.get("suite_version") != getattr(module, "SUITE_VERSION", "baseline-v1")
                or value.get("queries") != module.QUERY_IDS or not value.get("completed_at_utc")
                or value.get("failure")):
            raise ValueError("Worker metadata differs from frozen session")
        version = {"polars_cpu_144": "1.44.2", "polars_cpu_200": "2.0.0", "polars_gpu": "1.44.2",
                   "spark_cpu": "3.5.9", "spark_gpu": "3.5.9"}[session["config_id"]]
        if value.get("version") != version:
            raise ValueError("Engine version differs")
        if session["engine"] == "polars_gpu":
            check_polars_gpu_metadata(value)
        records = value.get("records", [])
        keys = [(r["query"], r["phase"], r["trial"]) for r in records]
        if any(count != 1 for count in Counter(keys).values()):
            raise ValueError("Duplicate action records")
        if any(key[0] not in module.QUERY_IDS for key in keys):
            raise ValueError("Unknown query in records")
        for query in module.QUERY_IDS:
            expected = {(query, "warmup", 0), *((query, "measured", t) for t in (1, 2, 3))}
            observed = {key for key in keys if key[0] == query}
            failures = [error for error in value.get("query_errors", []) if error["query"] == query]
            if not observed and failures and session["engine"] == "polars_gpu" and query in EXPECTED_UNSUPPORTED:
                if len(failures) != 1 or failures[0].get("phase") != "warmup" or failures[0].get("trial") != 0:
                    raise ValueError(f"Invalid unsupported-case failure records: {query}")
                if not any(token in str(failures[0]).lower() for token in ("notimplementederror", "not supported", "unsupported")):
                    raise ValueError(f"Expected GPU rejection has a different cause: {query}")
                out["coverage"][query] = {"status": "unsupported", "errors": failures}
            elif observed != expected or failures:
                out["coverage"][query] = {"status": "failed", "errors": failures}
                out["errors"].append(f"Incomplete/failed query: {query}")
            else:
                out["coverage"][query] = {"status": "complete"}
        if any(error.get("query") not in module.QUERY_IDS for error in value.get("query_errors", [])):
            raise ValueError("Unknown query failure")
        for record in records:
            for endpoint in ("seconds", "host_result_seconds"):
                timing = record.get(endpoint)
                if isinstance(timing, bool) or not isinstance(timing, (float, int)) or not math.isfinite(timing) or timing <= 0:
                    raise ValueError(f"Invalid {endpoint}")
            if record["host_result_seconds"] < record["seconds"]:
                raise ValueError("Host-result endpoint precedes action endpoint")
            query = record["query"]
            check = compare_values(oracle["queries"][query]["rows"], record["rows"])
            out["comparisons"].append({"query": query, "phase": record["phase"], "trial": record["trial"], **check})
            if not check["passed"]:
                out["errors"].append(f"Oracle mismatch: {query}/{record['phase']}/{record['trial']}")
            plan_path = path.parent / record["plan"]
            if not plan_path.resolve().is_relative_to(path.parent.resolve()) or not plan_path.is_file():
                raise ValueError("Missing or unsafe plan path")
            plan_result = {"query": query, "phase": record["phase"], "trial": record["trial"], "sha256": sha256(plan_path)}
            if session["engine"] == "spark_gpu":
                observed_plan = inspect_gpu_plan(plan_path) if session["suite"] == "baseline" else inspect_plan(plan_path)
                if not observed_plan.get("gpu_operators"):
                    raise ValueError(f"GPU Spark plan has no GPU operators: {query}")
                plan_result.update(observed_plan)
            out["plans"].append(plan_result)
        if session["suite"] == "complex":
            cleaned = [inspect_cleanup(entry, session["engine"]) for entry in value.get("cleanup_records", [])]
            cleaned_keys = [(r["query"], r["phase"], r["trial"]) for r in cleaned]
            if Counter(cleaned_keys) != Counter(keys):
                raise ValueError("Successful complex cleanup barriers do not match actions")
            failed_keys = [(r["query"], r["phase"], r["trial"]) for r in value.get("query_errors", [])]
            failed_cleaned = [inspect_cleanup(entry, session["engine"]) for entry in value.get("failed_query_cleanup_records", [])]
            if Counter((r["query"], r["phase"], r["trial"]) for r in failed_cleaned) != Counter(failed_keys):
                raise ValueError("Failed complex action cleanup barriers do not match errors")
        out["results_sha256"] = sha256(path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        out["errors"].append(f"{type(exc).__name__}: {exc}")
    out["status"] = "PASS" if not out["errors"] else "FAIL"
    return out


def validate(campaign, oracles, partial=False, artifacts_root=None):
    plan, digest = load_plan(campaign)
    plan["_sha256"] = digest
    journal_path = remap_path(plan, Path(plan["output_root"]) / "journal.json", artifacts_root)
    journal = read_json(journal_path)
    if journal.get("campaign_sha256") != digest or set(journal["sessions"]) != {s["session_id"] for s in plan["sessions"]}:
        raise ValueError("Journal identity/session set differs from campaign")
    gates = journal.get("input_integrity_gates", [])
    if not gates or (not partial and (not journal.get("completed_at_utc") or len(gates) < 2)):
        raise ValueError("Required campaign input-integrity gates/completion are missing")
    for gate in gates:
        if {row.get("scale") for row in gate.get("checks", [])} != set(plan["datasets"]):
            raise ValueError("Input-integrity gate does not cover all five scales")
        if any(row.get("status") != "pass" for row in gate["checks"]):
            raise ValueError("Input-integrity gate did not pass")
    previous_end = None
    for session in plan["sessions"]:
        item = journal["sessions"][session["session_id"]]
        for attempt in item.get("attempts", []):
            if attempt.get("ended_at_utc"):
                started = datetime.fromisoformat(attempt["started_at_utc"])
                ended = datetime.fromisoformat(attempt["ended_at_utc"])
                if started > ended or (previous_end is not None and started < previous_end):
                    raise ValueError("Campaign sessions overlap or have reversed timestamps")
                previous_end = ended
    oracle_values = {}
    oracle_paths = {}
    for block in plan["blocks"]:
        for cell in block["cells"]:
            key = (cell["suite"], cell["scale"])
            if key not in oracle_values:
                path = oracles / f"{key[0]}-sf{key[1]}.json"
                if sha256(path) != plan["oracles"][f"{key[0]}-sf{key[1]}"]["sha256"]:
                    raise ValueError(f"Oracle bytes differ from frozen campaign: {path}")
                oracle_values[key] = load_oracle(path, *key, plan["datasets"][key[1]]["manifest_sha256"])
                oracle_paths[f"{key[0]}-sf{key[1]}"] = {"path": str(path), "sha256": sha256(path)}
    audits = []
    pending = []
    for session in plan["sessions"]:
        item = journal["sessions"][session["session_id"]]
        if partial and item["status"] in ("planned", "running"):
            pending.append(session["session_id"])
            continue
        audits.append(audit_session(plan, session, item, oracle_values[(session["suite"], session["scale"])], artifacts_root))
    failed = [a["session_id"] for a in audits if a["status"] != "PASS"]
    return {"schema_version": "research-validation-v1", "campaign_sha256": digest,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "FAIL" if failed else "PARTIAL" if pending else "PASS",
            "journal_sha256": sha256(journal_path),
            "oracle_files": oracle_paths, "planned_sessions": len(plan["sessions"]),
            "audited_sessions": len(audits), "pending_sessions": pending, "failed_sessions": failed,
            "successful_outputs_compared": sum(len(a["comparisons"]) for a in audits), "sessions": audits}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--oracles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--partial", action="store_true")
    parser.add_argument("--artifacts-root", type=Path,
                        help="local copy of the frozen output_root; retain original JSON metadata bytes")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite a validation artifact")
    report = validate(args.campaign, args.oracles, args.partial, args.artifacts_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, report)
    print(json.dumps({k: report[k] for k in ("status", "planned_sessions", "audited_sessions", "successful_outputs_compared", "failed_sessions")}))
    raise SystemExit(1 if report["status"] == "FAIL" else 0)
