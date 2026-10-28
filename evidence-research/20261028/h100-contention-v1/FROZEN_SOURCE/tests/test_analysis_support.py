"""Synthetic result trees for the analysis tests (contract: docs/H100_PLAN.md).

build_root() writes a run root the way run_experiment.py lays it out, with small fake
rows and timings, so analyzers can be exercised for four engines, a Polars GPU query
error, a fallback/invalid GPU session, an output mismatch and an orchestrator failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import complex_workloads  # noqa: E402

BASELINE_QUERIES = ["q01", "q03", "q06", "q10", "q12", "q14"]
COMPLEX_QUERIES = list(complex_workloads.QUERY_IDS)
ENGINES4 = ["polars", "polars_gpu", "spark_cpu", "spark_gpu"]
BASE_TIME = {"polars": 1.0, "polars_gpu": 0.5, "spark_cpu": 3.0, "spark_gpu": 2.0}
SCALE_FACTOR_TIME = {"1": 1.0, "10": 4.0, "30": 9.0}
GPU_PLAN = ("AdaptiveSparkPlan isFinalPlan=true\n+- == Final Plan ==\n   GpuColumnarToRow\n"
            "   +- GpuHashAggregate\n      +- GpuBroadcastHashJoin\n         +- GpuFileGpuScan parquet\n"
            "+- == Initial Plan ==\n   HashAggregate\n")
CPU_PLAN = ("AdaptiveSparkPlan isFinalPlan=true\n+- == Final Plan ==\n   HashAggregate\n"
            "   +- BroadcastHashJoin\n      +- FileScan parquet\n+- == Initial Plan ==\n   HashAggregate\n")
POLARS_PLAN = "STREAMING:\n  AGGREGATE\n    Parquet SCAN\n"

A100_ENV = {"arguments": {"cpus": 30, "driver_memory": "96g"},
            "resource_allocation": {"gpu": "NVIDIA A100-SXM4-40GB", "gpu_count": 1, "host_ram_gib_approx": 216, "vcpus": 30}}
H100_ENV = {"arguments": {"cpus": 26, "driver_memory": "96g"}, "inventory": [
    {"command": ["lscpu"], "returncode": 0, "output": "Architecture:  x86_64\nCPU(s):                          26\nOn-line CPU(s) list:  0-25\n"
                  "Model name:                      Intel(R) Xeon(R) Platinum 8480+\nNUMA node0 CPU(s):  0-25\n"},
    {"command": ["free", "-b"], "returncode": 0, "output": "               total        used\nMem:    241591910400    123456\nSwap:   0 0\n"},
    {"command": ["nvidia-smi", "-q"], "returncode": 0, "output": "Driver Version                            : 580.105.08\nCUDA Version                              : 13.0\n"
                  "Attached GPUs                             : 1\nGPU 00000000:08:00.0\n    Product Name                          : NVIDIA H100 80GB HBM3\n"
                  "    FB Memory Usage\n        Total                             : 81559 MiB\n"},
    {"command": ["/usr/bin/python", "-m", "pip", "freeze"], "returncode": 0, "output": "polars==2.0.0\ncudf-polars-cu12==25.12.0\npyspark==3.5.9\nnumpy==2.2.6\n"}]}


def rows_for(query, scale="1"):
    """Deterministic fake result rows: a float aggregate, an exact integer count and a string key."""
    base = (BASELINE_QUERIES + COMPLEX_QUERIES).index(query) + 1
    return [{"key": f"k{i}", "total": base * 1000.5 * (i + 1) * float(scale) ** 0.5, "n": base * 10 + i} for i in range(3)]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def manifest_for(scale, *, rows=None, fingerprint=None, file_hash="a" * 64):
    tables = {"lineitem": {"rows": 600 * int(float(scale)), "parquet_bytes": 1000, "schema": [{"name": "l_orderkey", "type": "BIGINT"}],
                           "files": [{"path": "lineitem/part-0.parquet", "bytes": 1000, "sha256": file_hash}]},
              "orders": {"rows": 150 * int(float(scale)), "parquet_bytes": 500, "schema": [{"name": "o_orderkey", "type": "BIGINT"}],
                         "files": [{"path": "orders/part-0.parquet", "bytes": 500, "sha256": "b" * 64}]}}
    if rows:
        tables["lineitem"]["rows"] = rows
    manifest = {"format_version": 1, "scale_factor": float(scale), "duckdb_version": "1.5.6", "tables": tables,
                "total_rows": sum(t["rows"] for t in tables.values()), "total_parquet_bytes": 1500}
    if fingerprint:
        manifest["fingerprint_algorithm"] = "test-fingerprint"
        for table in tables.values():
            table.update(row_hash_sum=fingerprint, row_hash_xor=fingerprint, key_hash_sum=fingerprint, key_hash_xor=fingerprint)
    return manifest


def default_spark_conf(engine):
    conf = {"spark.master": "local[26]", "spark.driver.memory": "96g", "spark.sql.shuffle.partitions": "120",
            "spark.sql.adaptive.enabled": "true", "spark.sql.files.maxPartitionBytes": "134217728",
            "spark.sql.session.timeZone": "UTC", "spark.driver.maxResultSize": "1g"}
    if engine == "spark_gpu":
        conf.update({"spark.plugins": "com.nvidia.spark.SQLPlugin", "spark.rapids.sql.enabled": "true",
                     "spark.jars": "/jars/rapids-4-spark_2.12-26.08.1.jar"})
    return conf


def build_session(engine, suite, queries, scale, runs, manifest_sha, spec, *, cleanup="none", gpu_memory=False):
    """One fake results.json (and its plan files are written by the caller from the returned plan map)."""
    queries = list(queries)
    failed = dict(spec.get("query_errors", {}))
    session = {"engine": engine, "suite": suite, "queries": queries, "runs": runs, "scale_factor": float(scale),
               "manifest_sha256": manifest_sha, "cache_policy": "preheated filesystem; no application dataframe cache",
               "timing_scope": "query construction, optimization, parquet read, execution, result collection",
               "cpu_threads": 26, "version": spec.get("version", "2.0.0" if engine.startswith("polars") else "3.5.9"),
               "session_and_table_setup_seconds": 1.5, "launch_to_engine_ready_seconds": 2.0,
               "started_at_utc": "2026-11-01T00:00:00+00:00", "records": [], "query_errors": [], "active_query": None,
               "polars_execution_engine": {"polars": "streaming", "polars_gpu": "gpu"}.get(engine)}
    if suite == "complex":
        session.update(suite_version=complex_workloads.SUITE_VERSION, query_cleanup_policy=cleanup, cleanup_records=[])
    if engine.startswith("spark"):
        session["spark_conf"] = default_spark_conf(engine)
    if engine == "spark_gpu" and spec.get("profile"):
        session["spark_gpu_profile"] = spec["profile"]
    if engine == "polars_gpu":
        session["polars_gpu"] = {"executor": "streaming", "executor_options": {}, "raise_on_fail": True, "device": 0,
                                 "cudf_polars_version": "25.12.0", "memory_resource": None,
                                 "cpu_fallback_policy": "raise", "streaming_fallback_policy": "warn",
                                 "session_guard": {"effective_config": {"raise_on_fail": True,
                                     "executor": {"name": "streaming", "fallback_mode": "warn"}},
                                     "effective_config_verified": True, "tiny_query_passed": True,
                                     "unsupported_canary_raised": True, "unsupported_canary_reason_verified": True,
                                     "unsupported_canary_error": "NotImplementedError: unsupported anonymousfunction"}}
        session["gpu_inventory"] = {"name": "NVIDIA H100 80GB HBM3"}
        session.update(spec.get("session_overrides", {}))
    plans = {}
    for query in queries:
        for phase, trial in [("warmup", 0), *[("measured", n) for n in range(1, runs + 1)]]:
            if query in failed:
                continue
            seconds = BASE_TIME[engine] * SCALE_FACTOR_TIME.get(str(scale), 1.0) * (1 + 0.02 * trial) * spec.get("time_factor", 1.0)
            rows = rows_for(query, scale)
            perturb = spec.get("perturb", {}).get(query)
            if perturb:
                rows = copy.deepcopy(rows)
                rows[0]["total"] *= 1 + perturb
            name = f"plans/{query}-{phase}-{trial}.txt"
            record = {"query": query, "phase": phase, "trial": trial, "seconds": seconds, "rows": rows,
                      "started_at_utc": "2026-11-01T00:00:00+00:00", "ended_at_utc": "2026-11-01T00:00:01+00:00",
                      "peak_process_tree_rss_bytes": 1_000_000, "plan": name,
                      "gpu_execution_class": {"polars": "cpu_engine", "spark_cpu": "cpu_engine", "polars_gpu": "gpu_engine_strict",
                                              "spark_gpu": "gpu_operators_present"}[engine],
                      "gpu_plan_observed": engine == "spark_gpu"}
            if engine == "spark_gpu":
                record.update(gpu_operators=["GpuHashAggregate"], cpu_core_operators=[])
            if gpu_memory and engine in ("polars_gpu", "spark_gpu"):
                record["peak_gpu_memory_bytes"] = (8 * 1024**3 if engine == "polars_gpu" else 12 * 1024**3) if phase == "measured" else None
            if engine == "polars_gpu" and "fallback_class" in spec:
                if spec["fallback_class"] is None:
                    del record["gpu_execution_class"]
                else:
                    record["gpu_execution_class"] = spec["fallback_class"]
            plans[name] = {"polars": POLARS_PLAN, "polars_gpu": POLARS_PLAN, "spark_cpu": CPU_PLAN, "spark_gpu": GPU_PLAN}[engine]
            session["records"].append(record)
            if cleanup == "verified_gc" and suite == "complex":
                session["cleanup_records"].append({"query": query, "phase": phase, "trial": trial, "policy": "verified_gc",
                                                   "status": "passed", "seconds": 0.1,
                                                   "scratch_before": {"files": 0, "bytes": 0}, "scratch_after": {"files": 0, "bytes": 0}})
    for query, (kind, message) in failed.items():
        session["query_errors"].append({"query": query, "phase": "warmup", "trial": 0, "type": kind, "message": message,
                                        "failed_at_utc": "2026-11-01T00:00:00+00:00"})
    if failed:
        session["query_execution_status"] = "partial"
    if not spec.get("incomplete"):
        session["completed_at_utc"] = "2026-11-01T01:00:00+00:00"
    if spec.get("global_failure"):
        session["failed_at_utc"] = "2026-11-01T00:30:00+00:00"
        session["failure"] = {"type": "MemoryError", "message": "out of device memory"}
    return session, plans


def build_root(base, *, suite="baseline", scales=("1", "10"), engines=ENGINES4, specs=None, runs=3, env=None,
               schedule_overrides=None, cleanup="none", gpu_memory=False, manifests=None, skip=()):
    """Write a run root; specs[(scale, engine)] customizes one session (see build_session)."""
    base = Path(base)
    specs, schedule_overrides, manifests = specs or {}, schedule_overrides or {}, manifests or {}
    queries = BASELINE_QUERIES if suite == "baseline" else COMPLEX_QUERIES
    schedule = []
    for scale in scales:
        scale_dir = base / f"sf{scale}"
        manifest = manifests.get(scale) or manifest_for(scale)
        manifest_path = scale_dir / "manifest.json"
        write(manifest_path, manifest)
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        for engine in engines:
            item = {"scale": scale, "engine": engine, "status": "complete", "returncode": 0, "process_wall_seconds": 30.0,
                    "start_utc": "2026-11-01T00:00:00+00:00"}
            item.update(schedule_overrides.get((scale, engine), {}))
            schedule.append(item)
            if (scale, engine) in skip:
                continue
            spec = specs.get((scale, engine), {})
            session, plans = build_session(engine, suite, queries, scale, runs, digest, spec, cleanup=cleanup, gpu_memory=gpu_memory)
            write(scale_dir / engine / "results.json", session)
            for name, text in plans.items():
                (scale_dir / engine / name).parent.mkdir(parents=True, exist_ok=True)
                (scale_dir / engine / name).write_text(text)
    write(base / "schedule.json", schedule)
    write(base / "environment.json", env if env is not None else H100_ENV)
    return base


def run_main(module, argv):
    """Run an analyzer/plotter main() with argv; return (exit code, stdout, stderr)."""
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    old = sys.argv
    sys.argv = [module.__name__, *[str(a) for a in argv]]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = module.main()
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.argv = old
    return code, out.getvalue(), err.getvalue()


def test_fixture_is_self_consistent(tmp_path):
    root = build_root(tmp_path / "r")
    assert (root / "sf1" / "polars_gpu" / "results.json").is_file()
    session = json.loads((root / "sf1" / "polars_gpu" / "results.json").read_text())
    assert session["polars_gpu"]["raise_on_fail"] is True and len(session["records"]) == 24
