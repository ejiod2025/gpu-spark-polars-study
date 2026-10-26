#!/usr/bin/env python3
"""One isolated engine session, with untimed warmups and materialized results."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import random
import re
import threading
import time

import psutil

from workloads import QUERY_IDS, REQUIRED_TABLES, FLOAT_COLUMNS, polars_query, spark_sql


def normalized(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalized(v) for v in value]
    return value


def save(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


class MemorySampler:
    """Sample Python descendants and the SparkSubmit JVM parent, if present."""
    def __init__(self):
        self.peak = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.spark_parent = None
        try:
            parent = psutil.Process().parent()
            if parent and "org.apache.spark.deploy.SparkSubmit" in " ".join(parent.cmdline()):
                self.spark_parent = parent
        except psutil.Error:
            pass

    def sample(self):
        while not self.stop.is_set():
            processes = [psutil.Process()]
            try:
                processes += processes[0].children(recursive=True)
            except psutil.Error:
                pass
            if self.spark_parent is not None:
                processes.append(self.spark_parent)
            rss = 0
            for proc in processes:
                try:
                    rss += proc.memory_info().rss
                except psutil.Error:
                    pass
            self.peak = max(self.peak, rss)
            self.stop.wait(0.1)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["polars", "spark_cpu", "spark_gpu"], required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--queries", nargs="+", default=QUERY_IDS, choices=QUERY_IDS)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    plans = args.output / "plans"
    plans.mkdir()
    manifest_path = args.data / "manifest.json"
    metadata = {
        "engine": args.engine,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scale_factor": json.loads(manifest_path.read_text())["scale_factor"],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "runs": args.runs,
        "queries": args.queries,
        "cache_policy": "preheated filesystem; no application dataframe cache",
        "timing_scope": "query construction, optimization, parquet read, execution, result collection; excludes session/table setup and plan serialization",
        "pid": os.getpid(),
        "polars_execution_engine": "streaming" if args.engine == "polars" else None,
        "records": [],
    }
    needed = sorted(set(t for q in args.queries for t in REQUIRED_TABLES[q]))
    setup_start = time.perf_counter()
    spark = None
    if args.engine == "polars":
        import polars as pl
        metadata["version"] = pl.__version__
        metadata["cpu_threads"] = pl.thread_pool_size()
        tables = {}
        for table in needed:
            frame = pl.scan_parquet(str(args.data / table / "*.parquet"))
            tables[table] = frame.with_columns([
                pl.col(col).cast(pl.Float64) for col in FLOAT_COLUMNS.get(table, ())
            ])
    else:
        from pyspark.sql import SparkSession, functions as F
        spark = SparkSession.builder.appName("three-engine-benchmark-" + args.engine).getOrCreate()
        spark.sparkContext.setLogLevel("WARN")
        metadata["version"] = spark.version
        metadata["spark_conf"] = dict(spark.sparkContext.getConf().getAll())
        metadata["cpu_threads"] = spark.sparkContext.defaultParallelism
        for table in needed:
            frame = spark.read.parquet(str(args.data / table))
            for column in FLOAT_COLUMNS.get(table, ()):
                frame = frame.withColumn(column, F.col(column).cast("double"))
            frame.createOrReplaceTempView(table)
    metadata["session_and_table_setup_seconds"] = time.perf_counter() - setup_start
    if "BENCHMARK_LAUNCH_UNIX" in os.environ:
        metadata["launch_to_engine_ready_seconds"] = time.time() - float(os.environ["BENCHMARK_LAUNCH_UNIX"])
    save(args.output / "results.json", metadata)

    def execute(query, phase, trial):
        # Materialization is essential: timing only a lazy plan gives false speedups.
        start_utc = datetime.now(timezone.utc).isoformat()
        metadata["active_query"] = {"query": query, "phase": phase, "trial": trial, "started_at_utc": start_utc}
        save(args.output / "results.json", metadata)
        with MemorySampler() as memory:
            start = time.perf_counter()
            if args.engine == "polars":
                frame = polars_query(query, tables)
                result = frame.collect(engine="streaming")
                elapsed = time.perf_counter() - start
                rows = result.to_dicts()
                plan = frame.explain(engine="streaming")
            else:
                frame = spark.sql(spark_sql(query))
                result = frame.collect()
                elapsed = time.perf_counter() - start
                rows = [r.asDict(recursive=True) for r in result]
                plan = frame._jdf.queryExecution().executedPlan().toString()
        rows = normalized(rows)
        gpu_operators = sorted(set(re.findall(r"\bGpu[A-Za-z0-9_]+", plan)))
        plan_name = f"{query}-{phase}-{trial}.txt"
        (plans / plan_name).write_text(plan + "\n")
        record = {
            "query": query, "phase": phase, "trial": trial,
            "seconds": elapsed, "rows": rows,
            "started_at_utc": start_utc,
            "ended_at_utc": datetime.now(timezone.utc).isoformat(),
            "peak_process_tree_rss_bytes": memory.peak,
            "plan": "plans/" + plan_name,
            "gpu_plan_observed": "Gpu" in plan,
            "gpu_operators": gpu_operators,
        }
        metadata["records"].append(record)
        metadata["active_query"] = None
        save(args.output / "results.json", metadata)
        print(json.dumps({k: v for k, v in record.items() if k != "rows"}), flush=True)
        if args.engine == "spark_gpu" and not (
            any("Scan" in op for op in gpu_operators)
            and any("Aggregate" in op for op in gpu_operators)
        ):
            raise RuntimeError(f"Missing GPU scan/aggregate in executed plan for {query}: {gpu_operators}")

    try:
        for query in args.queries:
            execute(query, "warmup", 0)
        rng = random.Random(args.seed)
        for trial in range(1, args.runs + 1):
            order = list(args.queries)
            rng.shuffle(order)
            for query in order:
                execute(query, "measured", trial)
        metadata["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        save(args.output / "results.json", metadata)
    except BaseException as exc:
        metadata["failed_at_utc"] = datetime.now(timezone.utc).isoformat()
        metadata["failure"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
        save(args.output / "results.json", metadata)
        raise
    finally:
        if spark is not None:
            spark.stop()


if __name__ == "__main__":
    main()
