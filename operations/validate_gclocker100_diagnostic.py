#!/usr/bin/env python3
"""Audit saved feasibility outputs; executes no benchmark or SQL query."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--profile-helper", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source))
    from analyze_results import compare_values
    from analyze_complex import inspect_cleanup
    from complex_workloads import QUERY_IDS
    sys.path.insert(0, str(args.profile_helper.parent))
    from jvm_execution_profile import parse_driver_flags
    root = args.diagnostic
    if (root / "diagnostic.exit").read_text().strip() != "0":
        raise ValueError("Diagnostic process has not completed successfully")
    result_path = root / "run/sf100/spark_cpu/results.json"
    driver_log = root / "run/sf100/spark_cpu.log"
    value = json.loads(result_path.read_text())
    oracle = json.loads(args.oracle.read_text())
    if (value.get("failure") or value.get("query_errors") or value.get("engine") != "spark_cpu"
            or value.get("suite") != "complex" or value.get("runs") != 1
            or value.get("query_seed") != 838471203 or float(value.get("scale_factor", -1)) != 100
            or value.get("version") != "3.5.9" or value.get("manifest_sha256") != oracle["manifest_sha256"]
            or value.get("queries") != QUERY_IDS or not value.get("completed_at_utc")):
        raise ValueError("Diagnostic metadata or coverage differs from its declaration")
    records = value.get("records", [])
    keys = [(r["query"], r["phase"], r["trial"]) for r in records]
    expected = {(q, phase, trial) for q in QUERY_IDS for phase, trial in (("warmup", 0), ("measured", 1))}
    if len(keys) != 12 or len(set(keys)) != 12 or set(keys) != expected:
        raise ValueError("Diagnostic requires all six warmups and six measured outputs")
    checked = []
    for record in records:
        comparison = compare_values(oracle["queries"][record["query"]]["rows"], record["rows"])
        if not comparison["passed"]:
            raise ValueError("Diagnostic output disagrees with independent oracle")
        checked.append({"query": record["query"], "phase": record["phase"], "trial": record["trial"],
                        "passed": True, "comparison": comparison})
    cleanup = [inspect_cleanup(row, "spark_cpu") for row in value.get("cleanup_records", [])]
    if (Counter((r["query"], r["phase"], r["trial"]) for r in cleanup) != Counter(keys)
            or value.get("failed_query_cleanup_records")):
        raise ValueError("Diagnostic cleanup evidence is incomplete")
    report = {"schema_version": "h100-gclocker100-feasibility-validation-v1", "status": "PASS",
              "source_commit": "c84ef6a07341f350dae6636ab709c1b65fd894e0", "engine": "spark_cpu",
              "suite": "complex", "scale": "100", "runs": 1, "query_seed": 838471203,
              "manifest_sha256": value["manifest_sha256"], "oracle_sha256": sha(args.oracle),
              "results_sha256": sha(result_path), "successful_outputs_compared": 12,
              "queries": QUERY_IDS, "records": checked, "cleanup_records_checked": len(cleanup),
              "driver_flags": parse_driver_flags(driver_log.read_text()),
              "completed_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "Separate feasibility diagnostic; timings excluded from primary inference."}
    report_path = root / "VALIDATION.json"
    with report_path.open("x") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    receipt = {"status": "PASS", "completed_at_utc": report["completed_at_utc"],
               "driver_log_path": str(driver_log), "driver_log_sha256": sha(driver_log),
               "validation_path": str(report_path), "validation_sha256": sha(report_path),
               "results_path": str(result_path), "results_sha256": sha(result_path)}
    with (root / "RECEIPT.json").open("x") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"status": "PASS", "outputs_compared": 12, "cleanup_records_checked": len(cleanup),
                      "validation_sha256": receipt["validation_sha256"]}))


if __name__ == "__main__":
    main()
