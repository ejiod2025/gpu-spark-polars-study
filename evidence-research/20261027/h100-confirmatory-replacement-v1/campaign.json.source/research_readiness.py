#!/usr/bin/env python3
"""Untimed SF1 bring-up and independent-oracle gate for all five configurations.

This is separate from the prospective sample and must finish before campaign
planning. Existing result folders are never reused or overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys

from analyze_results import compare_values, check_polars_gpu_metadata, inspect_gpu_plan
from analyze_complex import inspect_plan, inspect_cleanup
from research_campaign import CONFIG_ENGINES, atomic_json, source_identity, terminate_owned_group
from research_validate import load_oracle, EXPECTED_UNSUPPORTED

HERE = Path(__file__).resolve().parent


def run_owned(command, log, env=None, timeout=7200):
    process = None
    try:
        with log.open("w") as handle:
            process = subprocess.Popen(command, cwd=HERE, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            rc = process.wait(timeout=timeout)
        if rc:
            raise RuntimeError(f"Command exited {rc}; see {log}")
    finally:
        if process is not None:
            handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGTERM, signal.SIGINT)}
            try:
                terminate_owned_group(process)
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--polars-cpu-200-python", required=True)
    args = parser.parse_args()
    args.data_root = args.data_root.resolve()
    args.output = args.output.absolute()
    args.jar = args.jar.resolve()
    args.python = os.path.abspath(args.python)
    args.polars_cpu_200_python = os.path.abspath(args.polars_cpu_200_python)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": "research-readiness-v1", "status": "RUNNING",
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "source": source_identity(), "successful_outputs_compared": 0, "sessions": [],
              "semantic_fixtures": [], "errors": []}
    old_handlers = {sig: signal.signal(sig, lambda number, frame: (_ for _ in ()).throw(
        InterruptedError(f"Readiness received signal {number}"))) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for suite in ("baseline", "complex"):
            oracle_path = args.output / f"{suite}-sf1.json"
            run_owned([args.python, str(HERE / "research_oracle.py"), "--suite", suite,
                       "--data", str(args.data_root / "sf1"), "--output", str(oracle_path),
                       "--threads", "26"], args.output / f"{suite}-oracle.log")
            import importlib
            import hashlib
            module = importlib.import_module("workloads" if suite == "baseline" else "complex_workloads")
            manifest = args.data_root / "sf1" / "manifest.json"
            oracle = load_oracle(oracle_path, suite, "1", hashlib.sha256(manifest.read_bytes()).hexdigest())
            for config, engine in CONFIG_ENGINES.items():
                output = args.output / f"{suite}-{config}"
                python = args.polars_cpu_200_python if config == "polars_cpu_200" else args.python
                run_owned([python, str(HERE / "run_experiment.py"), "--data-root", str(args.data_root),
                           "--output", str(output), "--engines", engine, "--scales", "1", "--suite", suite,
                           "--runs", "1", "--query-seed", "987123", "--cpus", "26", "--driver-memory", "96g",
                           "--jar", str(args.jar), "--query-cleanup", "verified_gc" if suite == "complex" else "none",
                           "--cache-observation", "--engine-timeout-seconds", "7200"],
                          args.output / f"{suite}-{config}.log")
                value = json.loads((output / "sf1" / engine / "results.json").read_text())
                if engine == "polars_gpu":
                    check_polars_gpu_metadata(value)
                expected_version = "2.0.0" if config == "polars_cpu_200" else "1.44.2" if engine.startswith("polars") else "3.5.9"
                if (value.get("version") != expected_version or value.get("query_seed") != 987123 or value.get("failure")
                        or value.get("suite") != suite or value.get("engine") != engine or value.get("runs") != 1
                        or float(value.get("scale_factor", -1)) != 1.0
                        or value.get("manifest_sha256") != oracle["manifest_sha256"]
                        or value.get("suite_version") != getattr(module, "SUITE_VERSION", "baseline-v1")
                        or value.get("queries") != module.QUERY_IDS or not value.get("completed_at_utc")):
                    raise ValueError(f"Invalid session metadata: {config}/{suite}")
                errors = value.get("query_errors", [])
                for error in errors:
                    if engine != "polars_gpu" or suite != "complex" or error["query"] not in EXPECTED_UNSUPPORTED:
                        raise ValueError(f"Unexpected query failure: {error}")
                    if error.get("phase") != "warmup" or error.get("trial") != 0 or not any(
                            token in str(error).lower() for token in ("notimplementederror", "not supported", "unsupported")):
                        raise ValueError(f"Failure is not an expected strict GPU rejection: {error}")
                if len({error["query"] for error in errors}) != len(errors):
                    raise ValueError("Duplicate failed-query records")
                records = value["records"]
                keys = [(r["query"], r["phase"], r["trial"]) for r in records]
                failed = {r["query"] for r in errors}
                expected = {(q, phase, trial) for q in module.QUERY_IDS if q not in failed
                            for phase, trial in (("warmup", 0), ("measured", 1))}
                if len(keys) != len(set(keys)) or set(keys) != expected:
                    raise ValueError(f"Missing/duplicate records: {config}/{suite}")
                for record in records:
                    check = compare_values(oracle["queries"][record["query"]]["rows"], record["rows"])
                    if not check["passed"]:
                        raise ValueError(f"Oracle disagreement {config}/{suite}/{record['query']}: {check}")
                    if (not math.isfinite(record.get("host_result_seconds", float("nan")))
                            or not math.isfinite(record["seconds"]) or record["seconds"] <= 0
                            or record.get("host_result_seconds", 0) < record["seconds"]):
                        raise ValueError("Common host-result timing field is missing/invalid")
                    if engine == "spark_gpu":
                        path = output / "sf1" / engine / record["plan"]
                        plan = inspect_gpu_plan(path) if suite == "baseline" else inspect_plan(path)
                        if not plan.get("gpu_operators"):
                            raise ValueError(f"No GPU operators: {path}")
                if suite == "complex":
                    clean = [inspect_cleanup(row, engine) for row in value.get("cleanup_records", [])]
                    failed_clean = [inspect_cleanup(row, engine) for row in value.get("failed_query_cleanup_records", [])]
                    key = lambda row: (row["query"], row["phase"], row["trial"])
                    if Counter(map(key, clean)) != Counter(keys) or Counter(map(key, failed_clean)) != Counter(map(key, errors)):
                        raise ValueError(f"Cleanup evidence does not cover all actions: {config}/{suite}")
                report["successful_outputs_compared"] += len(records)
                report["sessions"].append({"suite": suite, "config_id": config, "output": str(output),
                                           "successful_outputs": len(records), "query_errors": errors})
                atomic_json(args.output / "READINESS.json", report)
                print(json.dumps({"suite": suite, "config_id": config, "status": "PASS", "outputs": len(records)}), flush=True)
        for config, engine in CONFIG_ENGINES.items():
            python = args.polars_cpu_200_python if config == "polars_cpu_200" else args.python
            output = args.output / "fixtures" / config
            fixture_args = [str(HERE / "research_fixture_runner.py"), "--engine", engine, "--output", str(output)]
            if engine.startswith("polars"):
                command = [python, *fixture_args]
            else:
                command = [str(Path(python).parent / "spark-submit"), "--master", "local[2]",
                           "--driver-memory", "8g", "--conf", "spark.sql.shuffle.partitions=2",
                           "--conf", "spark.ui.enabled=false", "--conf", "spark.sql.session.timeZone=UTC"]
                if engine == "spark_gpu":
                    command += ["--jars", str(args.jar), "--conf", "spark.plugins=com.nvidia.spark.SQLPlugin",
                                "--conf", "spark.rapids.sql.enabled=true", "--conf", "spark.rapids.sql.variableFloatAgg.enabled=true"]
                command += fixture_args
            env = os.environ.copy()
            env.update(PYSPARK_PYTHON=python, PYSPARK_DRIVER_PYTHON=python,
                       POLARS_MAX_THREADS="26", SPARK_LOCAL_IP="127.0.0.1")
            run_owned(command, args.output / f"fixtures-{config}.log", env)
            fixture = json.loads((output / "FIXTURES.json").read_text())
            if fixture.get("status") != "PASS" or (engine != "polars_gpu" and not fixture.get("nonempty_anti_semantics_passed")):
                raise ValueError(f"Adversarial semantic fixtures did not pass: {config}")
            core = ("fx_inner_duplicates_nulls", "fx_outer_counts", "fx_semi_anti")
            if any(fixture.get("queries", {}).get(query, {}).get("status") != "PASS" for query in core):
                raise ValueError(f"Core join diagnostics did not pass: {config}")
            if engine == "spark_gpu" and not fixture.get("gpu_left_anti_plan_observed"):
                raise ValueError("GPU Spark diagnostic did not execute a GPU left anti join")
            report["semantic_fixtures"].append({"config_id": config, "output": str(output),
                                                "report_sha256": hashlib.sha256((output / "FIXTURES.json").read_bytes()).hexdigest(),
                                                "supported_queries": fixture["supported_queries"],
                                                "unsupported_queries": fixture["unsupported_queries"],
                                                "gpu_left_anti_plan_observed": fixture.get("gpu_left_anti_plan_observed")})
            atomic_json(args.output / "READINESS.json", report)
            print(json.dumps({"event": "adversarial_fixtures_complete", "config_id": config,
                              "supported_queries": fixture["supported_queries"], "unsupported_queries": fixture["unsupported_queries"]}), flush=True)
        report["status"] = "PASS"
    except BaseException as exc:
        report["status"] = "FAIL"
        report["errors"].append({"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_json(args.output / "READINESS.json", report)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    main()
