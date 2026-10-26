#!/usr/bin/env python3
"""Run engines sequentially on one host and retain reproducible evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ENGINES = ["polars", "spark_cpu", "spark_gpu"]


def capture(command):
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {"command": command, "returncode": result.returncode, "output": result.stdout}


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
    parser.add_argument("--engines", nargs="+", choices=ENGINES, default=ENGINES)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--cpus", type=int, default=30)
    parser.add_argument("--driver-memory", default="96g")
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--queries", nargs="+")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.data_root = args.data_root.resolve()
    args.jar = args.jar.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    environment = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "arguments": vars(args) | {"output": str(args.output), "data_root": str(args.data_root), "jar": str(args.jar)},
        "platform": platform.platform(),
        "python": sys.version,
        "inventory": [capture(cmd) for cmd in [
            ["lscpu"], ["free", "-b"], ["df", "-B1", str(args.data_root)],
            ["nvidia-smi", "-q"], ["java", "-version"], [sys.executable, "-m", "pip", "freeze"],
        ]],
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob("*.py")},
    }
    (args.output / "environment.json").write_text(json.dumps(environment, indent=2, default=str) + "\n")
    telemetry = (args.output / "gpu.csv").open("w")
    monitor = subprocess.Popen([
        "nvidia-smi", "--query-gpu=timestamp,index,utilization.gpu,utilization.memory,memory.used,power.draw",
        "--format=csv", "-lms", "200",
    ], stdout=telemetry, stderr=subprocess.STDOUT)
    schedule = []
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
                target = args.output / data.name / engine
                target.parent.mkdir(parents=True, exist_ok=True)
                # Warming identical files removes an engine-order disk-cache advantage.
                warming = preheat(data)
                worker_args = [str(HERE / "worker.py"), "--engine", engine, "--data", str(data),
                               "--output", str(target), "--runs", str(args.runs)]
                if args.queries:
                    worker_args += ["--queries", *args.queries]
                if engine == "polars":
                    command = [sys.executable, *worker_args]
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
                    command += worker_args
                item = {"scale": scale, "engine": engine, "command": command, "preheat": warming,
                        "start_utc": datetime.now(timezone.utc).isoformat()}
                schedule.append(item)
                print(json.dumps({"event": "engine_started", **item}), flush=True)
                (args.output / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
                started = time.perf_counter()
                env["BENCHMARK_LAUNCH_UNIX"] = str(time.time())
                with (target.parent / (engine + ".log")).open("w") as log:
                    result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
                item["process_wall_seconds"] = time.perf_counter() - started
                item["returncode"] = result.returncode
                (args.output / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
                if result.returncode:
                    raise RuntimeError(f"{engine} {data.name} failed; see {target.parent / (engine + '.log')}")
                print(json.dumps({"event": "engine_complete", **item}), flush=True)
    finally:
        monitor.terminate()
        monitor.wait(timeout=10)
        telemetry.close()


if __name__ == "__main__":
    main()
