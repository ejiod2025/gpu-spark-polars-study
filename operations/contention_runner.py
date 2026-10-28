#!/usr/bin/env python3
"""Freeze and run a separate, synchronized two-engine H100 contention study.

This never resumes, changes or signals an existing campaign executor.
The operator must first park the isolated scheduler and prove its workers gone.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

import psutil

SOURCE_COMMIT = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
REFERENCE_SHA = "3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641"
PROFILE_SHA = "a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69"
SPARK_OPTS = "-XX:+UnlockDiagnosticVMOptions -XX:GCLockerRetryAllocationCount=100 -XX:+PrintFlagsFinal"
PAIRS = {"spark_gpu__polars_cpu_200": ("spark_gpu", "polars_cpu_200"),
         "spark_cpu__polars_gpu": ("spark_cpu", "polars_gpu")}
EXPECTED_UNSUPPORTED = {"cx_q13", "cx_window"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def waves(phase):
    """The matrix/order is prospective and independent of observed timings."""
    result = []
    cells = [(pair, suite, scale, repetition)
             for repetition in range(1, 2 if phase == "pilot" else 3)
             for scale in (("30",) if phase == "pilot" else ("30", "100"))
             for suite in ("baseline", "complex") for pair in PAIRS]
    random.Random(20261028 if phase == "pilot" else 20261029).shuffle(cells)
    for number, (pair, suite, scale, repetition) in enumerate(cells, 1):
        result.append({"wave_id": f"{phase}-{number:02d}-{pair}-{suite}-sf{scale}-r{repetition}",
                       "pair": pair, "participants": list(PAIRS[pair]), "suite": suite,
                       "scale": scale, "repetition": repetition,
                       "query_seed": 838471203 + number + (1000 if phase == "main" else 0)})
    return result


def verify_source(plan, source=None):
    root = Path(source or plan["source_root"])
    for relative, digest in plan["source_files_sha256"].items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or sha(path) != digest:
            raise ValueError(f"Frozen source changed: {relative}")
    return root


def input_gate(plan, scales):
    checked = []
    for scale in sorted(set(scales), key=int):
        entry = plan["datasets"][scale]
        root = Path(entry["path"])
        if sha(root / "manifest.json") != entry["manifest_sha256"]:
            raise ValueError(f"Manifest changed at SF{scale}")
        manifest = read(root / "manifest.json")
        expected = {item["path"]: item for table in manifest["tables"].values() for item in table["files"]}
        if {str(p.relative_to(root)) for p in root.glob("*/*.parquet")} != set(expected):
            raise ValueError(f"Input file set changed at SF{scale}")
        for relative, item in expected.items():
            path = root / relative
            if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
                raise ValueError("Unsafe physical input")
            if path.stat().st_size != item["bytes"] or sha(path) != item["sha256"]:
                raise ValueError(f"Input bytes changed: SF{scale}/{relative}")
        checked.append({"scale": scale, "files": len(expected), "status": "PASS"})
    return {"completed_at_utc": utc(), "checks": checked}


def freeze(args):
    if sha(args.reference_campaign) != REFERENCE_SHA or sha(args.reference_profile) != PROFILE_SHA:
        raise ValueError("Only the genuine replacement recovery supplies reference identities")
    reference, profile = read(args.reference_campaign), read(args.reference_profile)
    if reference["source"]["git_head"] != SOURCE_COMMIT:
        raise ValueError("Unexpected frozen experimental source")
    original_source = args.source.resolve(strict=True)
    mapping = reference["source"]["files_sha256"]
    if len(mapping) != 52:
        raise ValueError("All 52 frozen source files are required")
    for relative, digest in mapping.items():
        if Path(relative).is_absolute() or ".." in Path(relative).parts or sha(original_source / relative) != digest:
            raise ValueError("Frozen source identity differs")
    if profile["spark_submit_opts"] != SPARK_OPTS:
        raise ValueError("Reference JVM execution profile differs")
    root = args.output_root.absolute()
    root.mkdir(parents=True, exist_ok=False)
    for relative in mapping:
        target = root / "FROZEN_SOURCE" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original_source / relative, target)
    (root / "tools").mkdir()
    tool_map = {}
    for path in (Path(__file__).resolve(), args.barrier_worker.resolve(), args.protocol.resolve()):
        target = root / "tools" / path.name
        shutil.copyfile(path, target)
        tool_map[str(target)] = sha(target)
    shutil.copyfile(args.reference_campaign, root / "REFERENCE_CAMPAIGN.json")
    shutil.copyfile(args.reference_profile, root / "REFERENCE_EXECUTION_PROFILE.json")
    (root / "oracles").mkdir()
    (root / "manifests").mkdir()
    oracle_map = {}
    for suite in ("baseline", "complex"):
        for scale in ("30", "100"):
            key = f"{suite}-sf{scale}"
            origin = Path(reference["oracles"][key]["path"])
            if sha(origin) != reference["oracles"][key]["sha256"]:
                raise ValueError(f"Independent oracle changed: {key}")
            target = root / "oracles" / (key + ".json")
            shutil.copyfile(origin, target)
            oracle_map[key] = {"path": str(target), "sha256": sha(target)}
    for scale in ("30", "100"):
        source = Path(reference["datasets"][scale]["path"]) / "manifest.json"
        if sha(source) != reference["datasets"][scale]["manifest_sha256"]:
            raise ValueError("Physical input manifest identity differs")
        shutil.copyfile(source, root / "manifests" / f"sf{scale}.json")
    plan = {"schema_version": "h100-contention-plan-v1", "experiment_id": "h100-contention-20261028",
            "author": "Daniel Ejiofor", "created_at_utc": utc(), "synthetic": False,
            "scope": "Separate descriptive shared-host, synchronized-query contention experiment; not an isolated speed comparison or primary 400-session observation",
            "source_commit": SOURCE_COMMIT, "source_root": str(root / "FROZEN_SOURCE"),
            "original_source_root": str(original_source), "source_files_sha256": mapping,
            "reference_campaign_sha256": REFERENCE_SHA, "reference_execution_profile_sha256": PROFILE_SHA,
            "root": str(root), "tools_sha256": tool_map, "barrier_worker": str(root / "tools" / args.barrier_worker.name),
            "protocol_sha256": sha(args.protocol), "oracles": oracle_map,
            "configs": reference["configs"], "datasets": {s: reference["datasets"][s] for s in ("30", "100")},
            "settings": reference["settings"] | {"runs": 1}, "spark_submit_opts": SPARK_OPTS,
            "barrier_timeout_seconds": 1800, "wave_timeout_seconds": 7200,
            "pilot": waves("pilot"), "main": waves("main"),
            "main_gate": "All four pilot waves pass output/cleanup/version/plan checks and every common measured action pair has positive actual action overlap"}
    path = root / "CONTENTION_PLAN.json"
    write(path, plan)
    (root / "CONTENTION_PLAN.sha256").write_text(sha(path) + "\n")
    prestart = root / "CONTENTION_PRESTART_MANIFEST.json"
    write(prestart, {"schema_version": "h100-contention-prestart-v1", "status": "FROZEN_NOT_RUN", "created_at_utc": utc(),
                     "plan_sha256": sha(path), "scope": "Genuine new contention plan and source identities before any contention query",
                     "files_sha256": {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}})
    return {"status": "FROZEN_NOT_RUN", "plan": str(path), "sha256": sha(path), "prestart": str(prestart),
            "prestart_sha256": sha(prestart), "pilot_waves": 4, "main_waves": 16}


def load_plan(path):
    plan = read(path)
    if sha(path) != path.with_suffix(".sha256").read_text().strip():
        raise ValueError("Contention plan checksum differs")
    if plan.get("schema_version") != "h100-contention-plan-v1" or plan.get("reference_campaign_sha256") != REFERENCE_SHA:
        raise ValueError("Unknown contention plan")
    for name, digest in plan["tools_sha256"].items():
        if sha(name) != digest:
            raise ValueError("Contention instrumentation changed after freeze")
    frozen_runner = Path(plan["root"]) / "tools" / "contention_runner.py"
    if sha(__file__) != sha(frozen_runner):
        raise ValueError("Executing runner differs from the frozen instrument")
    verify_source(plan)
    return plan


def runtime_gate(plan):
    """Fresh checks run outside contention timers and before any new workers."""
    profile = read(Path(plan["root"]) / "REFERENCE_EXECUTION_PROFILE.json")
    if sha(Path(plan["root"]) / "REFERENCE_EXECUTION_PROFILE.json") != PROFILE_SHA:
        raise ValueError("Reference profile bytes differ")
    java = Path(os.environ["JAVA_HOME"]) / "bin/java" if os.environ.get("JAVA_HOME") else Path(shutil.which("java") or "/missing-java")
    if str(java.resolve(strict=True)) != str(Path(profile["java"]["binary"]).resolve(strict=True)):
        raise ValueError("Selected Java executable differs from the sealed runtime")
    for path, digest in profile["java"]["files_sha256"].items():
        if sha(path) != digest:
            raise ValueError("Java executable/runtime differs from the sealed profile")
    sys.path.insert(0, plan["source_root"])
    campaign = importlib.import_module("research_campaign")
    checked = {}
    for config in plan["configs"]:
        python = config["python"]
        if python not in checked:
            checked[python] = campaign.runtime_identity(python)
        if checked[python] != config["runtime"]:
            raise ValueError(f"Python inventory changed: {config['id']}")
    return {"at_utc": utc(), "status": "PASS", "java_files_sha256": profile["java"]["files_sha256"],
            "python_runtime_identities": checked}


def require_idle(plan):
    sys.path.insert(0, plan["source_root"])
    campaign = importlib.import_module("research_campaign")
    value = campaign.observe_exclusive_host()
    if not value.get("eligible"):
        raise ValueError("A benchmark worker or GPU client is already active; do not overlap the isolated study")
    return value


def driver_flags(text):
    expected = {"GCLockerRetryAllocationCount": 100, "MaxHeapSize": 103079215104,
                "PrintFlagsFinal": True, "UnlockDiagnosticVMOptions": True, "UseG1GC": True}
    for name, wanted in expected.items():
        found = re.findall(r"^\s*\w+\s+" + name + r"\s*(?::=|=)\s*(\S+).*$", text, re.MULTILINE)
        if len(found) != 1 or found[0] != (str(wanted).lower() if isinstance(wanted, bool) else str(wanted)):
            raise ValueError(f"Actual Spark driver flag differs or is ambiguous: {name}")
    return expected


def command(plan, wave, config_id, target, barriers):
    config = next(c for c in plan["configs"] if c["id"] == config_id)
    engine, python = config["engine"], config["python"]
    worker_args = ["--source", plan["source_root"], "--barrier-root", str(barriers),
                   "--participant", config_id, "--participants", *wave["participants"],
                   "--barrier-timeout-seconds", str(plan["barrier_timeout_seconds"])]
    if "polars_gpu" in wave["participants"]:
        worker_args.append("--pair-has-polars-gpu")
    worker_args += ["--", "--engine", engine, "--data", plan["datasets"][wave["scale"]]["path"],
                    "--output", str(target), "--runs", "1", "--seed", str(wave["query_seed"]),
                    "--suite", wave["suite"], "--query-cleanup", plan["settings"]["query_cleanup_by_suite"][wave["suite"]],
                    "--cleanup-timeout-seconds", str(plan["settings"]["cleanup_timeout_seconds"])]
    if engine == "polars_gpu":
        worker_args += ["--polars-gpu-executor", "streaming", "--polars-gpu-executor-options", "{}"]
    if engine in ("polars", "polars_gpu"):
        return [python, plan["barrier_worker"], *worker_args]
    events = target.parent / (config_id + "-events")
    scratch = target.parent / (config_id + "-scratch")
    events.mkdir()
    scratch.mkdir()
    argv = [str(Path(python).parent / "spark-submit"), "--master", "local[26]", "--driver-memory", "96g"]
    conf = {"spark.sql.shuffle.partitions": "120", "spark.sql.adaptive.enabled": "true",
            "spark.sql.files.maxPartitionBytes": "134217728", "spark.sql.session.timeZone": "UTC",
            "spark.eventLog.enabled": "true", "spark.eventLog.dir": events.as_uri(),
            "spark.ui.enabled": "false", "spark.driver.maxResultSize": "1g", "spark.local.dir": str(scratch)}
    if engine == "spark_gpu":
        argv += ["--jars", plan["settings"]["jar"]]
        conf |= {"spark.plugins": "com.nvidia.spark.SQLPlugin", "spark.rapids.sql.enabled": "true",
                 "spark.rapids.sql.explain": "NOT_ON_GPU", "spark.rapids.sql.concurrentGpuTasks": "2",
                 "spark.rapids.memory.pinnedPool.size": "2G", "spark.rapids.sql.variableFloatAgg.enabled": "true"}
    for name, value in conf.items():
        argv += ["--conf", f"{name}={value}"]
    return [*argv, plan["barrier_worker"], *worker_args]


def stop_owned(process):
    """Only this runner's fresh session/process group can be signaled."""
    if process.pid == os.getpgrp():
        raise RuntimeError("Refusing to signal caller group")
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def telemetry(path, stop, processes):
    with path.open("w") as handle:
        while not stop.is_set():
            memory = psutil.virtual_memory()
            row = {"at_utc": utc(), "monotonic_ns": time.monotonic_ns(), "cpu_percent": psutil.cpu_percent(),
                   "available_bytes": memory.available, "swap_used_bytes": psutil.swap_memory().used, "process_trees": []}
            for config, process in list(processes.items()):
                try:
                    leader = psutil.Process(process.pid)
                    entries = []
                    for child in [leader, *leader.children(recursive=True)]:
                        try:
                            entries.append({"pid": child.pid, "created": child.create_time(), "rss": child.memory_info().rss,
                                            "cpu_times": child.cpu_times()._asdict(), "status": child.status()})
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    row["process_trees"].append({"config_id": config, "members": entries})
                except psutil.NoSuchProcess:
                    pass
            handle.write(json.dumps(row) + "\n")
            handle.flush()
            stop.wait(0.5)


def validate_member(plan, wave, config_id, directory, source=None):
    source_root = verify_source(plan, source)
    sys.path.insert(0, str(source_root))
    analyze = importlib.import_module("analyze_results")
    complex_a = importlib.import_module("analyze_complex")
    suite = importlib.import_module("workloads" if wave["suite"] == "baseline" else "complex_workloads")
    config = next(c for c in plan["configs"] if c["id"] == config_id)
    engine = config["engine"]
    path = directory / config_id / "results.json"
    report = {"config_id": config_id, "status": "FAIL", "errors": [], "comparisons": [], "coverage": {}}
    try:
        value = read(path)
        oracle_entry = plan["oracles"][f"{wave['suite']}-sf{wave['scale']}"]
        oracle_path = Path(oracle_entry["path"])
        if sha(oracle_path) != oracle_entry["sha256"]:
            raise ValueError("Independent oracle changed")
        oracle = read(oracle_path)
        version = {"polars_cpu_200": "2.0.0", "polars_gpu": "1.44.2", "spark_cpu": "3.5.9", "spark_gpu": "3.5.9"}[config_id]
        if (value.get("engine") != engine or value.get("suite") != wave["suite"] or value.get("version") != version
                or value.get("queries") != suite.QUERY_IDS or value.get("runs") != 1 or value.get("query_seed") != wave["query_seed"]
                or value.get("manifest_sha256") != plan["datasets"][wave["scale"]]["manifest_sha256"]
                or not value.get("completed_at_utc") or value.get("failure") or value.get("cleanup_failure")
                or value.get("cpu_threads") != 26):
            raise ValueError("Worker completion/runtime/query identity differs")
        if engine == "polars_gpu":
            analyze.check_polars_gpu_metadata(value)
        if engine in ("spark_cpu", "spark_gpu"):
            report["actual_driver_flags"] = driver_flags((directory / (config_id + ".log")).read_text())
            conf = value.get("spark_conf", {})
            required = {"spark.master": "local[26]", "spark.driver.memory": "96g", "spark.sql.shuffle.partitions": "120",
                        "spark.sql.adaptive.enabled": "true", "spark.sql.files.maxPartitionBytes": "134217728",
                        "spark.sql.session.timeZone": "UTC", "spark.driver.maxResultSize": "1g"}
            if engine == "spark_gpu":
                required |= {"spark.plugins": "com.nvidia.spark.SQLPlugin", "spark.rapids.sql.enabled": "true",
                             "spark.rapids.sql.concurrentGpuTasks": "2", "spark.rapids.memory.pinnedPool.size": "2G",
                             "spark.rapids.sql.variableFloatAgg.enabled": "true"}
            if any(conf.get(key) != expected for key, expected in required.items()):
                raise ValueError("Actual Spark configuration differs")
        keys = [(r["query"], r["phase"], r["trial"]) for r in value.get("records", [])]
        if any(n != 1 for n in Counter(keys).values()):
            raise ValueError("Duplicate output record")
        unsupported = EXPECTED_UNSUPPORTED if engine == "polars_gpu" and wave["suite"] == "complex" else set()
        expected = {(q, phase, trial) for q in suite.QUERY_IDS if q not in unsupported for phase, trial in (("warmup", 0), ("measured", 1))}
        if set(keys) != expected:
            raise ValueError("Successful action coverage differs")
        errors = value.get("query_errors", [])
        if len(errors) != len(unsupported) or {e.get("query") for e in errors} != unsupported:
            raise ValueError("Unexpected query errors or missing expected strict rejection")
        for error in errors:
            if error.get("phase") != "warmup" or error.get("trial") != 0 or not any(t in str(error).lower() for t in ("unsupported", "not supported", "notimplementederror")):
                raise ValueError("Strict rejection has a different phase/cause")
        for record in value["records"]:
            for endpoint in ("seconds", "host_result_seconds"):
                number = record.get(endpoint)
                if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number) or number <= 0:
                    raise ValueError("Invalid action endpoint")
            if record["host_result_seconds"] < record["seconds"]:
                raise ValueError("Host endpoint precedes action")
            comparison = analyze.compare_values(oracle["queries"][record["query"]]["rows"], record["rows"])
            report["comparisons"].append({"query": record["query"], "phase": record["phase"], "trial": record["trial"], **comparison})
            if not comparison["passed"]:
                raise ValueError("Independent SQL output mismatch")
            plan_path = path.parent / record["plan"]
            if not plan_path.resolve().is_relative_to(path.parent.resolve()) or not plan_path.is_file():
                raise ValueError("Unsafe or missing actual plan")
            if engine == "spark_gpu":
                inspected = analyze.inspect_gpu_plan(plan_path) if wave["suite"] == "baseline" else complex_a.inspect_plan(plan_path)
                if not inspected.get("gpu_operators"):
                    raise ValueError("Missing Spark GPU operators")
        if wave["suite"] == "complex":
            cleanup = [complex_a.inspect_cleanup(e, engine) for e in value.get("cleanup_records", [])]
            failed_cleanup = [complex_a.inspect_cleanup(e, engine) for e in value.get("failed_query_cleanup_records", [])]
            if Counter((e["query"], e["phase"], e["trial"]) for e in cleanup) != Counter(keys):
                raise ValueError("Successful cleanup coverage differs")
            if Counter((e["query"], e["phase"], e["trial"]) for e in failed_cleanup) != Counter((e["query"], e["phase"], e["trial"]) for e in errors):
                raise ValueError("Failed-action cleanup coverage differs")
        report.update(status="PASS", results_sha256=sha(path), successful_outputs=len(keys), expected_rejections=len(errors))
        report["coverage"] = {q: "unsupported" if q in unsupported else "complete" for q in suite.QUERY_IDS}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    return report


def overlap(left, right):
    """Same-host frozen action clocks, not process lifetime, establish overlap."""
    a = {(r["query"], r["phase"], r["trial"]): r for r in left["records"] if r["phase"] == "measured"}
    b = {(r["query"], r["phase"], r["trial"]): r for r in right["records"] if r["phase"] == "measured"}
    rows = []
    for key in sorted(a.keys() & b.keys()):
        x, y = a[key], b[key]
        xs, xe = float(x["action_start_monotonic_seconds"]), float(x["action_end_monotonic_seconds"])
        ys, ye = float(y["action_start_monotonic_seconds"]), float(y["action_end_monotonic_seconds"])
        if not all(math.isfinite(v) for v in (xs, xe, ys, ye)) or xe <= xs or ye <= ys:
            raise ValueError("Invalid actual action clock interval")
        utc_xs = datetime.fromisoformat(x["action_started_at_utc"]).timestamp()
        utc_xe = datetime.fromisoformat(x["action_ended_at_utc"]).timestamp()
        utc_ys = datetime.fromisoformat(y["action_started_at_utc"]).timestamp()
        utc_ye = datetime.fromisoformat(y["action_ended_at_utc"]).timestamp()
        seconds = max(0.0, min(xe, ye) - max(xs, ys))
        utc_seconds = max(0.0, min(utc_xe, utc_ye) - max(utc_xs, utc_ys))
        rows.append({"query": key[0], "phase": key[1], "trial": key[2], "overlap_seconds": seconds,
                     "utc_overlap_seconds": utc_seconds, "actual_start_skew_seconds": abs(xs - ys),
                     "left_seconds": xe - xs, "right_seconds": ye - ys,
                     "positive_actual_overlap": seconds > 0 and utc_seconds > 0})
    return rows


def run_wave(plan, wave, root):
    directory = root / wave["wave_id"]
    directory.mkdir()
    barriers = directory / "barriers"
    barriers.mkdir()
    data = Path(plan["datasets"][wave["scale"]]["path"])
    sys.path.insert(0, plan["source_root"])
    cache = importlib.import_module("cache_residency")
    before = cache.snapshot(data)
    started = time.perf_counter()
    read_bytes = 0
    for path in sorted(data.glob("*/*.parquet")):
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
                read_bytes += len(chunk)
    after = cache.snapshot(data)
    if before.get("errors") or after.get("errors") or not before.get("files") or not after.get("files"):
        raise ValueError("Required cache observation failed")
    row = wave | {"started_at_utc": utc(), "status": "RUNNING", "preheat": {"bytes": read_bytes, "seconds": time.perf_counter()-started},
                  "cache_before_preheat": before, "cache_after_preheat": after, "members": {}}
    write(directory / "WAVE.json", row)
    processes, logs = {}, []
    stop = threading.Event()
    thread = threading.Thread(target=telemetry, args=(directory / "host-processes.jsonl", stop, processes), daemon=True)
    gpu_handle, gpu = None, None
    try:
        gpu_handle = (directory / "gpu.csv").open("w")
        gpu = subprocess.Popen(["nvidia-smi", "--query-gpu=timestamp,index,utilization.gpu,utilization.memory,memory.used,power.draw", "--format=csv", "-lms", "200"], stdout=gpu_handle, stderr=subprocess.STDOUT)
        thread.start()
        for config_id in wave["participants"]:
            config = next(c for c in plan["configs"] if c["id"] == config_id)
            argv = command(plan, wave, config_id, directory / config_id, barriers)
            env = os.environ.copy()
            for name in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "SPARK_LAUNCHER_OPTS"):
                env.pop(name, None)
            env.update(POLARS_MAX_THREADS="26", OMP_NUM_THREADS="26", OPENBLAS_NUM_THREADS="26",
                       PYSPARK_PYTHON=config["python"], SPARK_LOCAL_IP="127.0.0.1", SPARK_SUBMIT_OPTS=SPARK_OPTS,
                       BENCHMARK_LAUNCH_UNIX=str(time.time()))
            if config["engine"] == "spark_gpu":
                env["BENCHMARK_SPARK_GPU_PROFILE"] = "a100-parity"
            else:
                env.pop("BENCHMARK_SPARK_GPU_PROFILE", None)
            log = (directory / (config_id + ".log")).open("w")
            logs.append(log)
            process = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            processes[config_id] = process
            row["members"][config_id] = {"command": argv, "pid": process.pid, "pgid": process.pid,
                                        "created": psutil.Process(process.pid).create_time(), "launched_at_utc": utc()}
            write(directory / "WAVE.json", row)
        deadline = time.monotonic() + plan["wave_timeout_seconds"]
        while any(p.poll() is None for p in processes.values()):
            if any(p.poll() not in (None, 0) for p in processes.values()):
                raise RuntimeError("A contention participant failed; preserve both outcomes without retry")
            if time.monotonic() >= deadline:
                raise TimeoutError("Contention wave reached its declared timeout")
            time.sleep(0.25)
        if any(p.returncode != 0 for p in processes.values()):
            raise RuntimeError("A contention participant failed")
        audits = [validate_member(plan, wave, name, directory) for name in wave["participants"]]
        result_values = [read(directory / name / "results.json") for name in wave["participants"]]
        intersections = overlap(*result_values)
        expected_common = 4 if wave["suite"] == "complex" and "polars_gpu" in wave["participants"] else 6
        passed = all(a["status"] == "PASS" for a in audits) and len(intersections) == expected_common and all(r["positive_actual_overlap"] for r in intersections)
        row.update(status="PASS" if passed else "FAIL", validations=audits, measured_action_overlap=intersections,
                   correctness_status="PASS" if all(a["status"] == "PASS" for a in audits) else "FAIL",
                   overlap_status="PASS" if len(intersections)==expected_common and all(r["positive_actual_overlap"] for r in intersections) else "FAIL")
        write(directory / "VALIDATION.json", {"schema_version": "h100-contention-wave-validation-v1", "scope": "Descriptive contention only", **row})
        if not passed:
            raise ValueError("Contention correctness or actual measured-action overlap gate failed")
    except BaseException as exc:
        row.update(status="FAIL", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        cleanup_errors = []
        for name, process in processes.items():
            try:
                stop_owned(process)
            except BaseException as exc:
                cleanup_errors.append({"config_id": name, "type": type(exc).__name__, "message": str(exc)})
            row["members"][name]["returncode"] = process.returncode
        stop.set()
        if thread.ident is not None:
            thread.join(timeout=5)
        if gpu is not None:
            try:
                if gpu.poll() is not None:
                    cleanup_errors.append({"config_id": "telemetry", "type": "UnexpectedExit", "returncode": gpu.returncode})
                gpu.terminate()
                gpu.wait(timeout=10)
            except BaseException as exc:
                cleanup_errors.append({"config_id": "telemetry", "type": type(exc).__name__, "message": str(exc)})
                if gpu.poll() is None:
                    gpu.kill()
                    gpu.wait()
        if gpu_handle is not None:
            gpu_handle.close()
        for handle in logs:
            handle.close()
        host_path = directory / "host-processes.jsonl"
        if not host_path.is_file() or not host_path.stat().st_size:
            cleanup_errors.append({"config_id": "host-telemetry", "type": "NoSamples"})
        row["ended_at_utc"] = utc()
        row["cache_after_workers"] = cache.snapshot(data)
        if cleanup_errors:
            row.update(status="FAIL", cleanup_errors=cleanup_errors)
        write(directory / "WAVE.json", row)
        if cleanup_errors:
            raise RuntimeError("Owned contention cleanup failed; preserve evidence and inspect before resuming isolated work")
    return row


def verify_pilot(plan, plan_path):
    root = Path(plan["root"]) / "pilot"
    phase = read(root / "PHASE.json")
    expected = {wave["wave_id"]: wave for wave in plan["pilot"]}
    actual = phase.get("waves", [])
    if (phase.get("status") != "PASS" or phase.get("phase") != "pilot" or phase.get("plan_sha256") != sha(plan_path)
            or len(actual) != len(expected) or {w.get("wave_id") for w in actual} != set(expected)
            or any(g.get("status") != "PASS" for g in phase.get("post_input_gate", {}).get("checks", []))
            or not phase.get("post_input_gate", {}).get("checks")):
        raise ValueError("All exact genuine pilot waves and terminal input gates must pass")
    for entry in actual:
        wave = expected[entry["wave_id"]]
        directory = root / wave["wave_id"]
        if entry.get("wave_sha256") != sha(directory / "WAVE.json") or entry.get("validation_sha256") != sha(directory / "VALIDATION.json"):
            raise ValueError("Pilot wave/validation bytes changed")
        value = read(directory / "VALIDATION.json")
        if (value.get("status") != "PASS" or value.get("overlap_status") != "PASS"
                or any(value.get(key) != wave[key] for key in ("wave_id", "participants", "suite", "scale", "query_seed"))):
            raise ValueError("Pilot wave identity or overlap gate differs")
        for audit in value["validations"]:
            if audit.get("status") != "PASS" or audit.get("results_sha256") != sha(directory / audit["config_id"] / "results.json"):
                raise ValueError("Pilot result bytes changed")
        repeated = [validate_member(plan, wave, name, directory) for name in wave["participants"]]
        if any(report["status"] != "PASS" for report in repeated):
            raise ValueError("Pilot correctness no longer validates")
        intersections = overlap(*[read(directory / name / "results.json") for name in wave["participants"]])
        if intersections != value["measured_action_overlap"] or not all(r["positive_actual_overlap"] for r in intersections):
            raise ValueError("Pilot overlap data changed")
    return {"status": "PASS", "phase_sha256": sha(root / "PHASE.json"), "waves": len(actual)}


def run(args):
    plan = load_plan(args.plan)
    root = Path(plan["root"])
    if sha(plan["settings"]["jar"]) != plan["settings"]["jar_sha256"]:
        raise ValueError("RAPIDS JAR differs")
    if args.phase == "main":
        verify_pilot(plan, args.plan)
    phase_root = root / args.phase
    phase_root.mkdir(exist_ok=False)
    row = {"schema_version": "h100-contention-phase-v1", "scope": "Separate descriptive shared-host contention, excluded from original 400-session study",
           "phase": args.phase, "plan_sha256": sha(args.plan), "started_at_utc": utc(), "status": "RUNNING", "waves": []}
    scales = {wave["scale"] for wave in plan[args.phase]}
    write(phase_root / "PHASE.json", row)
    try:
        row["initial_idle_probe"] = require_idle(plan)
        row["runtime_gate"] = runtime_gate(plan)
        row["initial_input_gate"] = input_gate(plan, scales)
        write(phase_root / "PHASE.json", row)
        for wave in plan[args.phase]:
            verify_source(plan)
            row["last_idle_probe"] = require_idle(plan)
            row["active_wave"] = wave["wave_id"]
            write(phase_root / "PHASE.json", row)
            result = run_wave(plan, wave, phase_root)
            row["waves"].append({"wave_id": wave["wave_id"], "status": result["status"],
                                 "wave_sha256": sha(phase_root / wave["wave_id"] / "WAVE.json"),
                                 "validation_sha256": sha(phase_root / wave["wave_id"] / "VALIDATION.json")})
            write(phase_root / "PHASE.json", row)
        row["active_wave"] = None
        row["post_input_gate"] = input_gate(plan, scales)
        verify_source(plan)
        row["status"] = "PASS"
    except BaseException as exc:
        row.update(status="FAIL", error={"type": type(exc).__name__, "message": str(exc)})
        active = row.get("active_wave")
        if active:
            path = phase_root / active / "WAVE.json"
            if path.parent.is_dir() and not path.exists():
                write(path, {"wave_id": active, "status": "FAIL", "stage": "prelaunch", "error": row["error"], "ended_at_utc": utc()})
        raise
    finally:
        row["ended_at_utc"] = utc()
        write(phase_root / "PHASE.json", row)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("freeze")
    for name in ("reference-campaign", "reference-profile", "source", "protocol", "output-root"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--barrier-worker", type=Path, default=Path(__file__).with_name("contention_worker.py"))
    p = sub.add_parser("run")
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--phase", choices=("pilot", "main"), required=True)
    args = parser.parse_args(argv)
    def interrupted(number, _frame):
        raise InterruptedError(f"Contention runner received signal {number}")
    previous = {number: signal.signal(number, interrupted) for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        result = freeze(args) if args.mode == "freeze" else run(args)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
