#!/usr/bin/env python3
"""Run engines sequentially on one host and retain reproducible evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import random
import signal
import shutil
import subprocess
import sys
import threading
import time

import psutil

HERE = Path(__file__).resolve().parent
# polars_gpu is opt-in: the default stays the three engines of the saved A100 evidence.
DEFAULT_ENGINES = ["polars", "spark_cpu", "spark_gpu"]
ENGINES = [*DEFAULT_ENGINES, "polars_gpu"]
GPU_ENGINES = ("polars_gpu", "spark_gpu")
SPARK_GPU_PROFILES = ["a100-parity", "h100-tuned"]

# Versions of the Python-level CUDA/RAPIDS stack, probed in a child process so the
# runner itself never loads CUDA libraries or holds a GPU context.
GPU_STACK_PROBE = """
import importlib, importlib.metadata as metadata, json
out = {"distributions": {}, "imports": {}}
for dist in ("polars", "cudf-polars-cu12", "cudf-polars-cu13", "cudf-cu12", "cudf-cu13",
             "pylibcudf-cu12", "pylibcudf-cu13", "rmm-cu12", "rmm-cu13", "rapidsmpf-cu12",
             "rapidsmpf-cu13", "cuda-python", "cuda-bindings", "nvidia-ml-py"):
    try:
        out["distributions"][dist] = metadata.version(dist)
    except metadata.PackageNotFoundError:
        pass
for name in ("cudf_polars", "pylibcudf", "rmm", "cuda.bindings", "pynvml"):
    try:
        module = importlib.import_module(name)
        out["imports"][name] = str(getattr(module, "__version__", "imported"))
    except Exception as exc:
        out["imports"][name] = ("unavailable: %s: %s" % (type(exc).__name__, exc))[:300]
try:
    from cuda.bindings import runtime
    out["cuda_runtime_version"] = runtime.cudaRuntimeGetVersion()[1]
    out["cuda_driver_version"] = runtime.cudaDriverGetVersion()[1]
except Exception as exc:
    out["cuda_versions_error"] = ("%s: %s" % (type(exc).__name__, exc))[:300]
print(json.dumps(out, indent=2))
"""


def capture(command, timeout=None):
    """Run a command and keep its output; a missing binary is recorded, not fatal."""
    try:
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "returncode": None, "output": "",
                "error": f"{type(exc).__name__}: {exc}"}
    return {"command": command, "returncode": result.returncode, "output": result.stdout}


def fail(message):
    sys.exit(f"run_experiment.py: error: {message}")


def load_spark_gpu_conf(path):
    """Read the h100-tuned overrides: a JSON object of flat Spark conf key -> scalar value."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read --spark-gpu-conf-file {path}: {exc}")
    if not isinstance(raw, dict) or not raw:
        fail(f"--spark-gpu-conf-file {path} must hold a non-empty JSON object of Spark confs")
    conf = {}
    for key, value in raw.items():
        if not key or "=" in key or key != key.strip():
            fail(f"--spark-gpu-conf-file has an invalid conf key {key!r}")
        if isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, (int, float, str)):
            value = str(value)
        else:
            fail(f"--spark-gpu-conf-file value for {key!r} must be a string, number or boolean")
        conf[key] = value
    return conf


def preheat(data):
    start = time.perf_counter()
    total = 0
    for path in sorted(data.glob("*/*.parquet")):
        with path.open("rb") as handle:
            while chunk := handle.read(16 * 1024 * 1024):
                total += len(chunk)
    return {"bytes": total, "seconds": time.perf_counter() - start}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scales", nargs="+", default=["1", "10", "30"])
    parser.add_argument("--engines", nargs="+", choices=ENGINES, default=DEFAULT_ENGINES,
                        help="default: the three engines of the A100 study; add polars_gpu explicitly")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--cpus", type=int, default=30)
    parser.add_argument("--driver-memory", default="96g")
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--queries", nargs="+")
    parser.add_argument("--suite", choices=["baseline", "complex"], default="baseline")
    parser.add_argument("--query-cleanup", choices=["none", "verified_gc"], default="none")
    parser.add_argument("--cleanup-timeout-seconds", type=float, default=60)
    parser.add_argument("--keep-going", action="store_true", help="Record engine failures and continue other engines/scales")
    parser.add_argument("--engine-timeout-seconds", type=float, default=None)
    parser.add_argument("--polars-gpu-executor", choices=["in-memory", "streaming"], default="streaming",
                        help="cudf-polars executor for polars_gpu")
    parser.add_argument("--polars-gpu-executor-options", default="{}",
                        help="JSON object of cudf-polars executor_options for polars_gpu")
    parser.add_argument("--spark-gpu-profile", choices=SPARK_GPU_PROFILES, default="a100-parity",
                        help="a100-parity keeps the original spark_gpu confs; h100-tuned appends "
                             "--spark-gpu-conf-file after them")
    parser.add_argument("--spark-gpu-conf-file", type=Path,
                        help="JSON object of Spark confs appended for spark_gpu under h100-tuned")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.data_root = args.data_root.resolve()
    args.jar = args.jar.resolve()

    # Everything that can be known to fail is checked before the output directory exists
    # and before any engine runs, so a misconfigured GPU run costs nothing and leaves nothing.
    try:
        polars_gpu_options = json.loads(args.polars_gpu_executor_options)
    except json.JSONDecodeError as exc:
        parser.error(f"--polars-gpu-executor-options is not valid JSON: {exc}")
    if not isinstance(polars_gpu_options, dict):
        parser.error("--polars-gpu-executor-options must be a JSON object")
    if args.spark_gpu_profile == "h100-tuned" and args.spark_gpu_conf_file is None:
        parser.error("--spark-gpu-profile h100-tuned requires --spark-gpu-conf-file")
    if args.spark_gpu_profile == "a100-parity" and args.spark_gpu_conf_file is not None:
        parser.error("--spark-gpu-conf-file is only valid with --spark-gpu-profile h100-tuned")
    spark_gpu_conf, spark_gpu_conf_sha256 = {}, None
    if args.spark_gpu_conf_file is not None:
        args.spark_gpu_conf_file = args.spark_gpu_conf_file.resolve()
        spark_gpu_conf = load_spark_gpu_conf(args.spark_gpu_conf_file)
        spark_gpu_conf_sha256 = hashlib.sha256(args.spark_gpu_conf_file.read_bytes()).hexdigest()
    gpu_selected = [e for e in args.engines if e in GPU_ENGINES]
    if gpu_selected:
        smi = capture(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv"], timeout=60)
        if smi["returncode"] != 0:
            fail(f"GPU engine(s) {gpu_selected} selected but nvidia-smi is unavailable or failing: "
                 f"{smi.get('error') or smi['output'].strip()}")
    if "polars_gpu" in args.engines and importlib.util.find_spec("cudf_polars") is None:
        fail("engine polars_gpu selected but cudf_polars is not installed for "
             f"{sys.executable}; install the cudf-polars build matching the host CUDA version")
    if "spark_gpu" in args.engines and not args.jar.is_file():
        fail(f"engine spark_gpu selected but --jar {args.jar} does not exist")
    args.output.mkdir(parents=True, exist_ok=False)
    environment = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "arguments": vars(args) | {"output": str(args.output), "data_root": str(args.data_root), "jar": str(args.jar)},
        "platform": platform.platform(),
        "python": sys.version,
        "inventory": [capture(cmd) for cmd in [
            ["lscpu"], ["free", "-b"], ["df", "-B1", str(args.data_root)],
            ["nvidia-smi", "-q"], ["java", "-version"], [sys.executable, "-m", "pip", "freeze"],
            [sys.executable, "-c", "import importlib.metadata as m,json; "
             "print(json.dumps(sorted((d.metadata['Name'], d.version) for d in m.distributions())))"],
            ["nvidia-smi", "topo", "-m"],
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv"],
        ]] + [capture([sys.executable, "-c", GPU_STACK_PROBE], timeout=300)],
        "gpu_engine_options": {
            "polars_gpu_executor": args.polars_gpu_executor,
            "polars_gpu_executor_options": polars_gpu_options,
            "spark_gpu_profile": args.spark_gpu_profile,
            "spark_gpu_conf_file": str(args.spark_gpu_conf_file) if args.spark_gpu_conf_file else None,
            "spark_gpu_conf_file_sha256": spark_gpu_conf_sha256,
            "spark_gpu_extra_conf": spark_gpu_conf,
        },
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob("*.py")},
    }
    telemetry = (args.output / "gpu.csv").open("w")
    try:
        monitor = subprocess.Popen([
            "nvidia-smi", "--query-gpu=timestamp,index,utilization.gpu,utilization.memory,memory.used,power.draw",
            "--format=csv", "-lms", "200",
        ], stdout=telemetry, stderr=subprocess.STDOUT)
        environment["gpu_telemetry"] = {"started": True}
    except OSError as exc:  # only reachable without a GPU engine: those fail fast above
        monitor = None
        environment["gpu_telemetry"] = {"started": False, "error": f"{type(exc).__name__}: {exc}"}
    (args.output / "environment.json").write_text(json.dumps(environment, indent=2, default=str) + "\n")
    schedule = []
    host_stop = threading.Event()
    active = {"scale": "", "engine": "", "phase": "idle"}
    def sample_host():
        import csv
        with (args.output / "host.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp_utc", "scale", "engine", "phase", "cpu_percent", "available_bytes", "cached_bytes", "swap_used_bytes", "disk_free_bytes"])
            while not host_stop.is_set():
                memory = psutil.virtual_memory()
                writer.writerow([datetime.now(timezone.utc).isoformat(), active["scale"], active["engine"], active["phase"],
                                 psutil.cpu_percent(), memory.available, getattr(memory, "cached", 0),
                                 psutil.swap_memory().used, shutil.disk_usage(args.data_root).free])
                handle.flush()
                host_stop.wait(1)
    host_thread = threading.Thread(target=sample_host, daemon=True)
    host_thread.start()
    spark_submit = Path(sys.executable).parent / "spark-submit"
    env = os.environ.copy()
    env.update({"POLARS_MAX_THREADS": str(args.cpus), "OMP_NUM_THREADS": str(args.cpus),
                "OPENBLAS_NUM_THREADS": str(args.cpus), "PYSPARK_PYTHON": sys.executable,
                "SPARK_LOCAL_IP": "127.0.0.1"})
    try:
        for scale_index, scale in enumerate(args.scales):
            data = args.data_root / ("sf" + scale.replace(".", "p"))
            if not (data / "manifest.json").is_file():
                raise RuntimeError(f"Missing complete dataset at {data}")
            scale_output = args.output / data.name
            scale_output.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(data / "manifest.json", scale_output / "manifest.json")
            engines = list(args.engines)
            random.Random(42 + scale_index).shuffle(engines)
            for engine in engines:
                active.update(scale=scale, engine=engine, phase="preheat")
                target = args.output / data.name / engine
                target.parent.mkdir(parents=True, exist_ok=True)
                # Warming identical files removes an engine-order disk-cache advantage.
                warming = preheat(data)
                worker_args = [str(HERE / "worker.py"), "--engine", engine, "--data", str(data),
                               "--output", str(target), "--runs", str(args.runs), "--suite", args.suite,
                               "--query-cleanup", args.query_cleanup,
                               "--cleanup-timeout-seconds", str(args.cleanup_timeout_seconds)]
                if args.queries:
                    worker_args += ["--queries", *args.queries]
                spark_gpu_overrides = []
                if engine == "polars":
                    command = [sys.executable, *worker_args]
                elif engine == "polars_gpu":
                    command = [sys.executable, *worker_args,
                               "--polars-gpu-executor", args.polars_gpu_executor,
                               "--polars-gpu-executor-options", json.dumps(polars_gpu_options, sort_keys=True)]
                else:
                    events = target.parent / (engine + "-events")
                    events.mkdir()
                    command = [str(spark_submit), "--master", f"local[{args.cpus}]",
                               "--driver-memory", args.driver_memory,
                               "--conf", "spark.sql.shuffle.partitions=120",
                               "--conf", "spark.sql.adaptive.enabled=true",
                               "--conf", "spark.sql.files.maxPartitionBytes=134217728",
                               "--conf", "spark.sql.session.timeZone=UTC",
                               "--conf", "spark.eventLog.enabled=true",
                               "--conf", f"spark.eventLog.dir={events.as_uri()}",
                               "--conf", "spark.ui.enabled=false",
                               "--conf", "spark.driver.maxResultSize=1g"]
                    if engine == "spark_gpu":
                        command += ["--jars", str(args.jar),
                                    "--conf", "spark.plugins=com.nvidia.spark.SQLPlugin",
                                    "--conf", "spark.rapids.sql.enabled=true",
                                    "--conf", "spark.rapids.sql.explain=NOT_ON_GPU",
                                    "--conf", "spark.rapids.sql.concurrentGpuTasks=2",
                                    "--conf", "spark.rapids.memory.pinnedPool.size=2G",
                                    "--conf", "spark.rapids.sql.variableFloatAgg.enabled=true"]
                        # h100-tuned: overrides come last so they win over the parity confs
                        # (spark-submit keeps the last value of a repeated --conf key).
                        parity_keys = {c.partition("=")[0] for prev, c in zip(command, command[1:]) if prev == "--conf"}
                        spark_gpu_overrides = sorted(parity_keys & spark_gpu_conf.keys())
                        for key, value in spark_gpu_conf.items():
                            command += ["--conf", f"{key}={value}"]
                    command += worker_args
                item = {"scale": scale, "engine": engine, "command": command, "preheat": warming,
                        "start_utc": datetime.now(timezone.utc).isoformat()}
                if engine == "polars_gpu":
                    item["polars_gpu_options"] = {"executor": args.polars_gpu_executor,
                                                  "executor_options": polars_gpu_options}
                elif engine == "spark_gpu":
                    item["spark_gpu_options"] = {"profile": args.spark_gpu_profile,
                                                 "conf_file_sha256": spark_gpu_conf_sha256,
                                                 "extra_conf": spark_gpu_conf,
                                                 "overridden_parity_confs": spark_gpu_overrides}
                schedule.append(item)
                print(json.dumps({"event": "engine_started", **item}), flush=True)
                (args.output / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
                started = time.perf_counter()
                env["BENCHMARK_LAUNCH_UNIX"] = str(time.time())
                # Environment, not a command-line flag, so spark command lines match the A100 runs.
                if engine == "spark_gpu":
                    env["BENCHMARK_SPARK_GPU_PROFILE"] = args.spark_gpu_profile
                else:
                    env.pop("BENCHMARK_SPARK_GPU_PROFILE", None)
                active["phase"] = "execute"
                with (target.parent / (engine + ".log")).open("w") as log:
                    process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    try:
                        returncode = process.wait(timeout=args.engine_timeout_seconds)
                    except subprocess.TimeoutExpired:
                        item["failure_reason"] = "engine_timeout"
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                        returncode = 124
                item["process_wall_seconds"] = time.perf_counter() - started
                item["returncode"] = returncode
                item["status"] = "complete" if returncode == 0 else "failed"
                active["phase"] = "idle"
                (args.output / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
                if returncode and not args.keep_going:
                    raise RuntimeError(f"{engine} {data.name} failed; see {target.parent / (engine + '.log')}")
                print(json.dumps({"event": "engine_complete" if returncode == 0 else "engine_failed", **item}), flush=True)
    finally:
        host_stop.set()
        host_thread.join(timeout=5)
        if monitor is not None:
            monitor.terminate()
            monitor.wait(timeout=10)
        telemetry.close()


if __name__ == "__main__":
    main()
