#!/usr/bin/env python3
"""One isolated engine session, with untimed warmups and materialized results."""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import threading
import time
import warnings

import psutil
from query_cleanup import CleanupBarrier, CleanupTimeout, discover_blockmgr_dirs

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


POLARS_ENGINES = ("polars", "polars_gpu")
GPU_ENGINES = ("polars_gpu", "spark_gpu")


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

    def process_tree(self):
        processes = [psutil.Process()]
        try:
            processes += processes[0].children(recursive=True)
        except psutil.Error:
            pass
        if self.spark_parent is not None:
            processes.append(self.spark_parent)
        return processes

    def sample(self):
        while not self.stop.is_set():
            rss = 0
            for proc in self.process_tree():
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


def json_safe(value):
    """Reduce config objects (dataclasses, enums, handles) to plain JSON values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class NvmlProbe:
    """Per-pid compute memory from NVML in-process (microseconds per read)."""
    name, interval = "pynvml", 0.1

    def __init__(self, device):
        import pynvml
        pynvml.nvmlInit()
        self.nvml = pynvml
        self.handle = pynvml.nvmlDeviceGetHandleByIndex(device)

    def read(self, pids):
        used = []
        for proc in self.nvml.nvmlDeviceGetComputeRunningProcesses(self.handle):
            memory = getattr(proc, "usedGpuMemory", None)
            # NVML reports "not available" as None or all-ones, e.g. under vGPU/WSL.
            if proc.pid in pids and memory is not None and memory < 2 ** 63:
                used.append(int(memory))
        return sum(used) if used else None


class NvidiaSmiProbe:
    """Fallback: poll `nvidia-smi --query-compute-apps` (a process spawn per read)."""
    name, interval = "nvidia-smi", 0.25

    def __init__(self, device):
        self.command = ["nvidia-smi", "-i", str(device),
                        "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"]
        self.read(set())

    def read(self, pids):
        result = subprocess.run(self.command, text=True, timeout=10, check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        used = []
        for line in result.stdout.splitlines():
            pid, _, mib = (field.strip() for field in line.partition(","))
            if pid.isdigit() and int(pid) in pids and mib.isdigit():
                used.append(int(mib) * 1024 * 1024)  # nounits => MiB
        return sum(used) if used else None


def make_gpu_probe(device):
    """First working backend, or None when device memory cannot be observed."""
    for backend in (NvmlProbe, NvidiaSmiProbe):
        try:
            probe = backend(device)
            probe.read(set())
            return probe
        except Exception:
            continue
    return None


class GpuMemorySampler:
    """Peak device memory of this process tree while a GPU engine runs its action.

    peak stays None when no sample ever matched a process of the tree, so an
    unobservable device is recorded as null rather than as zero. stop() is
    idempotent; call it as soon as the timed action returns so untimed work
    (to_dicts, plan text) is not sampled.
    """
    def __init__(self, probe, process_tree):
        self.probe, self.process_tree = probe, process_tree
        self.peak = None
        self.done = threading.Event()
        self.thread = None

    def take(self):
        try:
            used = self.probe.read({p.pid for p in self.process_tree()})
        except Exception:  # sampling must never fail a benchmark action
            return
        if used is not None:
            self.peak = used if self.peak is None else max(self.peak, used)

    def run(self):
        while not self.done.wait(self.probe.interval):
            self.take()

    def __enter__(self):
        if self.probe is not None:
            self.take()
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
        return self

    def stop(self):
        if self.thread is not None and not self.done.is_set():
            self.done.set()
            self.thread.join()
            self.take()

    def __exit__(self, *_):
        self.stop()


def format_cuda_version(number):
    return f"{number // 1000}.{number % 1000 // 10}" if isinstance(number, int) and number > 0 else None


def gpu_inventory(device):
    """Best-effort device name, driver and CUDA versions; never raises."""
    info = {"device_index": device}
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(device)
        name = pynvml.nvmlDeviceGetName(handle)
        driver = pynvml.nvmlSystemGetDriverVersion()
        info.update(source="pynvml",
                    name=name.decode() if isinstance(name, bytes) else name,
                    driver_version=driver.decode() if isinstance(driver, bytes) else driver,
                    memory_total_bytes=int(pynvml.nvmlDeviceGetMemoryInfo(handle).total))
        try:
            info["cuda_driver_version"] = format_cuda_version(pynvml.nvmlSystemGetCudaDriverVersion_v2())
        except Exception:
            pass
    except Exception as exc:
        info["pynvml_error"] = f"{type(exc).__name__}: {exc}"[:300]
        try:
            out = subprocess.run(
                ["nvidia-smi", "-i", str(device), "--query-gpu=name,driver_version,memory.total",
                 "--format=csv,noheader,nounits"],
                text=True, timeout=10, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            ).stdout.strip().splitlines()[0].split(",")
            info.update(source="nvidia-smi", name=out[0].strip(), driver_version=out[1].strip(),
                        memory_total_bytes=int(out[2]) * 1024 * 1024)
        except Exception as smi_exc:
            info["nvidia_smi_error"] = f"{type(smi_exc).__name__}: {smi_exc}"[:300]
    try:
        from cuda.bindings import runtime
        status, version = runtime.cudaRuntimeGetVersion()
        if int(status) == 0:
            info["cuda_runtime_version"] = format_cuda_version(version)
    except Exception:
        pass
    return info


def cudf_polars_version(module):
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    for dist in ("cudf-polars-cu12", "cudf-polars-cu13", "cudf-polars"):
        try:
            return importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def init_polars_gpu(pl, device, executor, executor_options):
    """Build the strict GPU engine and prove it works before any benchmark action.

    Nothing here is timed. The engine always has raise_on_fail=True, so a query
    cudf-polars cannot run is an exception, never a CPU timing labelled GPU.
    """
    try:
        cudf_polars = importlib.import_module("cudf_polars")
    except Exception as exc:  # ImportError, or CUDA libraries missing at import
        raise RuntimeError(
            "polars_gpu requires the cudf-polars package matching the host CUDA version "
            f"(for example cudf-polars-cu12), but importing cudf_polars failed: "
            f"{type(exc).__name__}: {exc}") from exc
    if not hasattr(pl, "GPUEngine"):
        raise RuntimeError(f"Installed polars {pl.__version__} has no GPUEngine")
    settings = {"device": device, "raise_on_fail": True, "executor": executor}
    if executor_options:
        settings["executor_options"] = executor_options
    version = cudf_polars_version(cudf_polars)
    try:
        engine = pl.GPUEngine(**settings)
    except Exception as exc:
        raise RuntimeError(f"polars {pl.__version__} rejected GPUEngine settings {settings}: "
                           f"{type(exc).__name__}: {exc}") from exc
    guard = {}

    # Resolve cudf-polars' own view of the engine: proves executor/raise_on_fail were
    # honored (an old or changed cudf-polars could otherwise ignore them silently).
    try:
        from cudf_polars.utils.config import ConfigOptions
        options = ConfigOptions.from_polars_engine(engine)
    except ImportError as exc:
        guard["effective_config_error"] = f"ConfigOptions unavailable: {exc}"[:300]
    except Exception as exc:
        raise RuntimeError(
            f"cudf-polars {version} rejected executor={executor!r} "
            f"executor_options={executor_options!r}: {type(exc).__name__}: {exc}") from exc
    else:
        resolved = getattr(options.executor, "name", None)
        if resolved != executor or getattr(options, "raise_on_fail", None) is not True:
            raise RuntimeError(
                f"cudf-polars {version} resolved executor={resolved!r} "
                f"raise_on_fail={getattr(options, 'raise_on_fail', None)!r}, "
                f"expected executor={executor!r} raise_on_fail=True")
        guard["effective_config"] = json_safe(options)

    start = time.perf_counter()
    expected = {"k": [1, 2, 3], "total": [3.0, 7.0, 5.0]}
    tiny = (pl.LazyFrame({"k": [1, 1, 2, 2, 3], "v": [1.0, 2.0, 3.0, 4.0, 5.0]})
            .group_by("k").agg(pl.col("v").sum().alias("total")).sort("k"))
    try:
        got = tiny.collect(engine=engine).to_dict(as_series=False)
    except Exception as exc:
        raise RuntimeError(f"polars_gpu session guard: tiny GPU query failed on cudf-polars "
                           f"{version}: {type(exc).__name__}: {exc}") from exc
    if got != expected:
        raise RuntimeError(f"polars_gpu session guard: tiny GPU query returned {got}, expected {expected}")
    # Canary: a Python UDF cannot run on the GPU, so with raise_on_fail=True it must raise.
    # Success would mean the query silently ran on the Polars CPU engine.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        canary = pl.LazyFrame({"x": [1, 2, 3]}).select(
            pl.col("x").map_elements(lambda value: value, return_dtype=pl.Int64))
        try:
            canary.collect(engine=engine)
        except Exception as exc:
            guard["unsupported_canary_error"] = f"{type(exc).__name__}: {exc}"[:300]
        else:
            raise RuntimeError("polars_gpu session guard: an unsupported query did not raise with "
                               "raise_on_fail=True; the engine may be falling back to the CPU silently")
    guard["seconds"] = time.perf_counter() - start

    describe = {
        "executor": executor,
        "executor_options": executor_options,
        "raise_on_fail": True,
        "device": device,
        "cudf_polars_version": version,
        "memory_resource": None,  # cudf-polars default; see session_guard.effective_config
        "environment": {k: v for k, v in sorted(os.environ.items())
                        if k.startswith(("CUDF_POLARS", "POLARS_GPU", "RMM_", "LIBCUDF_", "CUDA_VISIBLE_DEVICES"))},
        "session_guard": guard,
    }
    return engine, describe


def gpu_physical_plan(frame, engine):
    """Text of cudf-polars' lowered physical plan, or why it is unavailable."""
    for module in ("cudf_polars.streaming.explain", "cudf_polars.experimental.explain"):
        try:
            explain_query = importlib.import_module(module).explain_query
        except (ImportError, AttributeError):
            continue
        try:
            return explain_query(frame, engine, physical=True)
        except Exception as exc:
            return f"unavailable: {module}.explain_query raised {type(exc).__name__}: {exc}"[:500]
    return "unavailable: no cudf-polars explain_query API in this version"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["polars", "polars_gpu", "spark_cpu", "spark_gpu"], required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--suite", choices=["baseline", "complex"], default="baseline")
    parser.add_argument("--queries", nargs="+")
    parser.add_argument("--query-cleanup", choices=["none", "verified_gc"], default="none")
    parser.add_argument("--cleanup-timeout-seconds", type=float, default=60)
    parser.add_argument("--polars-gpu-executor", choices=["in-memory", "streaming"],
                        help="cudf-polars executor for --engine polars_gpu (default: streaming)")
    parser.add_argument("--polars-gpu-executor-options", default=None,
                        help="JSON object of cudf-polars executor_options for --engine polars_gpu")
    parser.add_argument("--gpu-device", type=int, default=0,
                        help="CUDA device index for the GPU engines' memory sampling and polars_gpu")
    args = parser.parse_args()
    if args.engine != "polars_gpu" and (args.polars_gpu_executor or args.polars_gpu_executor_options):
        parser.error("--polars-gpu-executor and --polars-gpu-executor-options require --engine polars_gpu")
    args.polars_gpu_executor = args.polars_gpu_executor or "streaming"
    try:
        args.polars_gpu_executor_options = json.loads(args.polars_gpu_executor_options or "{}")
    except json.JSONDecodeError as exc:
        parser.error(f"--polars-gpu-executor-options is not valid JSON: {exc}")
    if not isinstance(args.polars_gpu_executor_options, dict):
        parser.error("--polars-gpu-executor-options must be a JSON object")
    # What a failed query does: "raise" aborts the session (legacy baseline behaviour);
    # "record_and_continue" stores it in query_errors, never measures it, and carries on.
    # The complex suite always recorded; polars_gpu records in both suites so a query the
    # GPU cannot run (unsupported op, out of memory) is a visible result, not a lost scale.
    failure_policy = "record_and_continue" if args.suite == "complex" or args.engine == "polars_gpu" else "raise"
    suite = importlib.import_module("complex_workloads" if args.suite == "complex" else "workloads")
    args.queries = args.queries or suite.QUERY_IDS
    if any(query not in suite.QUERY_IDS for query in args.queries):
        parser.error("Query IDs must belong to the selected suite")
    REQUIRED_TABLES, FLOAT_COLUMNS = suite.REQUIRED_TABLES, suite.FLOAT_COLUMNS
    polars_query, spark_sql = suite.polars_query, suite.spark_sql
    args.output.mkdir(parents=True, exist_ok=False)
    plans = args.output / "plans"
    plans.mkdir()
    manifest_path = args.data / "manifest.json"
    metadata = {
        "engine": args.engine,
        "suite": args.suite,
        "suite_version": getattr(suite, "SUITE_VERSION", "baseline-v1"),
        "query_descriptions": {q: suite.DESCRIPTIONS[q] for q in args.queries},
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scale_factor": json.loads(manifest_path.read_text())["scale_factor"],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "runs": args.runs,
        "queries": args.queries,
        "cache_policy": "preheated filesystem; no application dataframe cache",
        "timing_scope": "query construction, optimization, parquet read, execution, result collection; excludes session/table setup and plan serialization",
        "pid": os.getpid(),
        "polars_execution_engine": {"polars": "streaming", "polars_gpu": "gpu"}.get(args.engine),
        "records": [],
        "query_errors": [],
        "query_cleanup_policy": args.query_cleanup,
        "cleanup_records": [],
        "failure_policy": failure_policy,
    }
    needed = sorted(set(t for q in args.queries for t in REQUIRED_TABLES[q]))
    setup_start = time.perf_counter()
    spark = None
    cleanup_barrier = None
    blockmgr_before = discover_blockmgr_dirs() if args.query_cleanup == "verified_gc" else set()
    polars_engine = "streaming"
    if args.engine in POLARS_ENGINES:
        import polars as pl
        metadata["version"] = pl.__version__
        metadata["cpu_threads"] = pl.thread_pool_size()
        tables = {}
        for table in needed:
            frame = pl.scan_parquet(str(args.data / table / "*.parquet"))
            tables[table] = frame.with_columns([
                pl.col(col).cast(pl.Float64) for col in FLOAT_COLUMNS.get(table, ())
            ])
        if args.engine == "polars_gpu":
            try:
                polars_engine, metadata["polars_gpu"] = init_polars_gpu(
                    pl, args.gpu_device, args.polars_gpu_executor, args.polars_gpu_executor_options)
            except BaseException as exc:  # leave evidence of a failed session guard
                metadata["failed_at_utc"] = datetime.now(timezone.utc).isoformat()
                metadata["failure"] = {"type": type(exc).__name__, "message": str(exc)[:2000],
                                       "stage": "polars_gpu_session_guard"}
                save(args.output / "results.json", metadata)
                raise
            metadata["polars_gpu_physical_plan"] = (
                "Plan files hold Polars' CPU-optimized logical plan followed by cudf-polars' lowered "
                "plan from explain_query, captured once per query after its warmup when the installed "
                "cudf-polars provides that API; per-query status is in polars_gpu_physical_plan_status")
    else:
        from pyspark.sql import SparkSession, functions as F
        spark = SparkSession.builder.appName("three-engine-benchmark-" + args.engine).getOrCreate()
        spark.sparkContext.setLogLevel("WARN")
        metadata["version"] = spark.version
        metadata["spark_conf"] = dict(spark.sparkContext.getConf().getAll())
        if args.engine == "spark_gpu" and "com.nvidia.spark.SQLPlugin" not in metadata["spark_conf"].get("spark.plugins", ""):
            raise RuntimeError("GPU engine requires the NVIDIA Spark plugin")
        metadata["cpu_threads"] = spark.sparkContext.defaultParallelism
        if args.engine == "spark_gpu":
            # The runner passes the profile by environment so spark-submit command lines stay unchanged.
            metadata["spark_gpu_profile"] = os.environ.get("BENCHMARK_SPARK_GPU_PROFILE")
        for table in needed:
            frame = spark.read.parquet(str(args.data / table))
            for column in FLOAT_COLUMNS.get(table, ()):
                frame = frame.withColumn(column, F.col(column).cast("double"))
            frame.createOrReplaceTempView(table)
    if args.query_cleanup == "verified_gc":
        owned_dirs = set()
        if spark is not None:
            local_dirs = spark.sparkContext.getConf().get("spark.local.dir", "")
            owned_dirs = discover_blockmgr_dirs([p for p in local_dirs.split(",") if p]) - blockmgr_before
            if not owned_dirs:
                raise RuntimeError("Cannot verify shuffle cleanup without a newly owned blockmgr directory")
        cleanup_barrier = CleanupBarrier(args.engine, spark, owned_dirs,
                                         timeout_seconds=args.cleanup_timeout_seconds)
    gpu_probe = make_gpu_probe(args.gpu_device) if args.engine in GPU_ENGINES else None
    if args.engine in GPU_ENGINES:
        metadata["gpu_inventory"] = gpu_inventory(args.gpu_device)
        metadata["gpu_memory_sampler"] = {
            "backend": gpu_probe.name if gpu_probe else None,
            "interval_seconds": gpu_probe.interval if gpu_probe else None,
            "device": args.gpu_device,
            "scope": "sum of device memory of the worker process tree (plus the SparkSubmit JVM parent), "
                     "sampled during the timed action; a sampled peak can miss short transients",
        }
    metadata["session_and_table_setup_seconds"] = time.perf_counter() - setup_start
    if "BENCHMARK_LAUNCH_UNIX" in os.environ:
        metadata["launch_to_engine_ready_seconds"] = time.time() - float(os.environ["BENCHMARK_LAUNCH_UNIX"])
    save(args.output / "results.json", metadata)

    def execute(query, phase, trial):
        # Materialization is essential: timing only a lazy plan gives false speedups.
        start_utc = datetime.now(timezone.utc).isoformat()
        metadata["active_query"] = {"query": query, "phase": phase, "trial": trial, "started_at_utc": start_utc}
        save(args.output / "results.json", metadata)
        # Only polars_gpu records warnings: cudf-polars reports streaming single-partition
        # fallbacks there. CPU engines keep their original, unwrapped warning behaviour.
        warning_scope = (warnings.catch_warnings(record=True) if args.engine == "polars_gpu"
                         else contextlib.nullcontext())
        with (MemorySampler() as memory,
              GpuMemorySampler(gpu_probe, memory.process_tree) as gpu_memory,
              warning_scope as caught):
            if caught is not None:
                warnings.simplefilter("default")  # one entry per distinct warning, not per emission
            start = time.perf_counter()
            if args.engine in POLARS_ENGINES:
                frame = polars_query(query, tables)
                result = frame.collect(engine=polars_engine)
                elapsed = time.perf_counter() - start
                gpu_memory.stop()
                rows = result.to_dicts()
                plan = frame.explain(engine="streaming") if args.engine == "polars" else frame.explain()
            else:
                frame = spark.sql(spark_sql(query))
                result = frame.collect()
                elapsed = time.perf_counter() - start
                gpu_memory.stop()
                rows = [r.asDict(recursive=True) for r in result]
                plan = frame._jdf.queryExecution().executedPlan().toString()
        rows = normalized(rows)
        final_plan = plan.split("== Final Plan ==", 1)[-1].split("== Initial Plan ==", 1)[0]
        gpu_operators = sorted(set(re.findall(r"\bGpu[A-Za-z0-9_]+", final_plan)))
        cpu_core_operators = sorted(set(re.findall(
            r"\b(?:HashAggregate|ObjectHashAggregate|SortAggregate|BroadcastHashJoin|SortMergeJoin|"
            r"ShuffledHashJoin|BroadcastNestedLoopJoin|CartesianProduct|Window|WindowGroupLimit|"
            r"Expand|Sort|FileScan|BatchScan|TakeOrderedAndProject)\b", final_plan)))
        plan_text = plan
        if args.engine == "polars_gpu":
            if query not in gpu_plans:  # deterministic per query: explain once, after the timed region
                gpu_plans[query] = gpu_physical_plan(frame, polars_engine)
                metadata.setdefault("polars_gpu_physical_plan_status", {})[query] = (
                    "unavailable" if gpu_plans[query].startswith("unavailable:") else "captured")
            plan_text = (f"== Polars optimized plan (CPU optimizer; executed by cudf-polars, "
                         f"executor={args.polars_gpu_executor}) ==\n{plan}\n"
                         f"== cudf-polars physical plan ==\n{gpu_plans[query]}")
        plan_name = f"{query}-{phase}-{trial}.txt"
        (plans / plan_name).write_text(plan_text + "\n")
        record = {
            "query": query, "phase": phase, "trial": trial,
            "seconds": elapsed, "rows": rows,
            "started_at_utc": start_utc,
            "ended_at_utc": datetime.now(timezone.utc).isoformat(),
            "peak_process_tree_rss_bytes": memory.peak,
            "plan": "plans/" + plan_name,
            "gpu_plan_observed": "Gpu" in plan,
            "gpu_operators": gpu_operators,
            "cpu_core_operators": cpu_core_operators,
            "gpu_execution_class": ("cpu_fallback" if not gpu_operators else
                                    "hybrid" if cpu_core_operators else "gpu_operators_present")
                                   if args.engine == "spark_gpu" else
                                   "gpu_engine_strict" if args.engine == "polars_gpu" else "cpu_engine",
        }
        if args.engine in GPU_ENGINES:
            record["peak_gpu_memory_bytes"] = gpu_memory.peak
        if args.engine == "polars_gpu":
            record["gpu_engine_warnings"] = sorted(
                {f"{w.category.__name__}: {str(w.message)[:300]}" for w in caught})[:10]
        metadata["records"].append(record)
        metadata["active_query"] = None
        save(args.output / "results.json", metadata)
        print(json.dumps({k: v for k, v in record.items() if k != "rows"}), flush=True)
        if args.engine == "spark_gpu" and args.suite == "baseline" and not (
            any("Scan" in op for op in gpu_operators)
            and any("Aggregate" in op for op in gpu_operators)
        ):
            raise RuntimeError(f"Missing GPU scan/aggregate in executed plan for {query}: {gpu_operators}")

    failed_queries = set()
    gpu_plans = {}
    def attempt(query, phase, trial):
        succeeded = False
        try:
            execute(query, phase, trial)
            succeeded = True
        except Exception as exc:
            if failure_policy == "raise":
                raise
            failed_queries.add(query)
            error = {"query": query, "phase": phase, "trial": trial,
                     "type": type(exc).__name__, "message": str(exc)[:2000],
                     "failed_at_utc": datetime.now(timezone.utc).isoformat()}
            metadata["query_errors"].append(error)
            metadata["active_query"] = None
            save(args.output / "results.json", metadata)
            print(json.dumps({"event": "query_failed", **error}), flush=True)
        # execute() must return before collection: its DataFrame/JavaObject
        # references would otherwise keep shuffle dependencies alive.
        if cleanup_barrier is not None:
            context = {"query": query, "phase": phase, "trial": trial,
                       "policy": args.query_cleanup}
            try:
                cleaned = cleanup_barrier.cleanup()
                cleaned.update(context)
                cleaned["scratch_before"] = {"files": cleaned["before"]["shuffle_files"],
                                             "bytes": cleaned["before"]["shuffle_bytes"]}
                cleaned["scratch_after"] = {"files": cleaned["after"]["shuffle_files"],
                                            "bytes": cleaned["after"]["shuffle_bytes"]}
                key = "cleanup_records" if succeeded else "failed_query_cleanup_records"
                metadata.setdefault(key, []).append(cleaned)
                save(args.output / "results.json", metadata)
                print(json.dumps({"event": "query_cleanup", **context,
                    "status": cleaned["status"], "seconds": cleaned["seconds"],
                    "scratch_before": cleaned["scratch_before"],
                    "scratch_after": cleaned["scratch_after"]}), flush=True)
            except BaseException as exc:
                metadata["cleanup_failure"] = {**context, "type": type(exc).__name__,
                    "message": str(exc)[:2000], "record": getattr(exc, "record", None)}
                save(args.output / "results.json", metadata)
                raise

    try:
        for query in args.queries:
            attempt(query, "warmup", 0)
        rng = random.Random(args.seed)
        for trial in range(1, args.runs + 1):
            order = list(args.queries)
            rng.shuffle(order)
            for query in order:
                if query not in failed_queries:
                    attempt(query, "measured", trial)
        metadata["query_execution_status"] = "partial" if failed_queries else "complete"
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
