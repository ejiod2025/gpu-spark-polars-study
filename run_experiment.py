#!/usr/bin/env python3
"""Run engines sequentially on one host and retain reproducible evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import signal
import shutil
import subprocess
import sys
import threading
import time

import psutil
import power_telemetry

HERE = Path(__file__).resolve().parent
# polars_gpu is opt-in: the default stays the three engines of the saved A100 evidence.
DEFAULT_ENGINES = ["polars", "spark_cpu", "spark_gpu"]
ENGINES = [*DEFAULT_ENGINES, "polars_gpu"]
GPU_ENGINES = ("polars_gpu", "spark_gpu")
SPARK_GPU_PROFILES = ["a100-parity", "h100-tuned"]
# 200 ms GPU telemetry. New columns are only ever appended: existing gpu.csv columns keep their position.
GPU_TELEMETRY_QUERY = ("timestamp,index,utilization.gpu,utilization.memory,memory.used,power.draw,"
                       "temperature.gpu,clocks.sm,clocks.mem")
GPU_TELEMETRY_INTERVAL_MS = 200
# Lines of `nvidia-smi -q` that identify one physical GPU. Product name, driver, CUDA and memory lines stay.
GPU_IDENTIFIER_FIELD = re.compile(
    r"^(?P<lead>[ \t]*(?:GPU UUID|MIG UUID|Serial Number|Chassis Serial Number|GPU PDI|Board ID|GPU Fabric GUID|ClusterUUID|Bus Id)[ \t]*:[ \t]*)"
    r"(?P<value>\S.*?)[ \t]*$|"
    r"^(?P<pci_lead>[ \t]*(?:Bus|Device|Domain)[ \t]*:[ \t]*)(?P<pci_value>0x[0-9A-Fa-f]+)[ \t]*$", re.M)
GPU_HEADER_BUS_ID = re.compile(r"^GPU[ \t]+[0-9A-Fa-f]{4,8}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-9A-Fa-f][ \t]*$", re.M)
REDACTED = "<redacted>"
GPU_IDLE_POLL_SECONDS = 1.0

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


def list_compute_apps():
    """(rows, error): compute processes on the GPU, or None rows when nvidia-smi cannot say."""
    result = capture(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                      "--format=csv,noheader,nounits"], timeout=30)
    if result["returncode"] != 0:
        return None, (result.get("error") or result["output"].strip())[:300]
    return [line.strip() for line in result["output"].splitlines() if line.strip()], None


def wait_for_idle_gpu(timeout, poll=GPU_IDLE_POLL_SECONDS, list_apps=list_compute_apps,
                      sleep=time.sleep, clock=time.monotonic):
    """Poll until no compute process is on the GPU, for at most `timeout` seconds.

    Returns the observation recorded with the session. A failing nvidia-smi is never
    read as idle: it keeps polling and ends as not idle with the error recorded.
    """
    started = clock()
    polls = 0
    while True:
        apps, error = list_apps()
        polls += 1
        waited = clock() - started
        if apps == []:
            idle = True
        elif waited >= timeout:
            idle = False
        else:
            sleep(poll)
            continue
        return {"idle": idle, "waited_seconds": waited, "polls": polls, "timeout_seconds": timeout,
                "compute_apps": apps or [], "error": error,
                "checked_at_utc": datetime.now(timezone.utc).isoformat()}


def redact_gpu_identifiers(text):
    """(text, counts): `nvidia-smi -q` output with the GPU UUID, serial number and PCI bus id replaced by <redacted>.

    The facts the analysis reads (Product Name, Driver Version, CUDA Version, Attached GPUs, FB Memory Usage Total) are
    untouched, and N/A values stay as they are. Idempotent."""
    counts = {}

    def field(match):
        if match["lead"] is not None:
            name = match["lead"].split(":")[0].strip()
            if match["value"] in (REDACTED, "N/A", "[N/A]", "[Not Supported]"):
                return match[0]
            counts[name] = counts.get(name, 0) + 1
            return match["lead"] + REDACTED
        name = "PCI " + match["pci_lead"].split(":")[0].strip().lower()
        counts[name] = counts.get(name, 0) + 1
        return match["pci_lead"] + REDACTED
    text = GPU_IDENTIFIER_FIELD.sub(field, text)

    def header(match):
        counts["GPU bus id header"] = counts.get("GPU bus id header", 0) + 1
        return "GPU " + REDACTED
    return GPU_HEADER_BUS_ID.sub(header, text), counts


def probe_gpu_energy_counter(duration=1.0):
    """Source and resolution of the NVML energy counter, from `power_telemetry.py --probe-gpu-energy` in a child."""
    result = capture([sys.executable, str(HERE / "power_telemetry.py"), "--probe-gpu-energy", "--duration", str(duration)],
                     timeout=120)
    output = result.get("output", "")
    try:
        return json.loads(output[output.index("{"):])
    except ValueError:
        return {"source": "unknown", "error": f"no JSON from the probe (exit {result.get('returncode')}): {output.strip()[-300:]}"}


def run_idle_baseline(seconds, active, gpu_selected, gpu_timeout, measurable=True,
                      sleep=time.sleep, wait_idle=wait_for_idle_gpu):
    """Hold the host idle for `seconds` before a session; the window is labelled phase=idle-baseline in host.csv and power.csv.

    With a GPU engine the GPU is first required to have no compute process (same gate as the launch gate), so the idle
    GPU power is not that of the previous session's tail. Returns the record stored as schedule item["idle_baseline"].
    """
    record = {"requested_seconds": seconds, "phase_label": "idle-baseline"}
    if not measurable:
        return {**record, "skipped": "no_power_source"}
    if gpu_selected:
        active["phase"] = "gpu_idle_gate"
        record["gpu_idle_precheck"] = wait_idle(gpu_timeout)
        if not record["gpu_idle_precheck"]["idle"]:
            return {**record, "skipped": "gpu_not_idle"}
    active["phase"] = "idle-baseline"
    start_utc, start_mono = power_telemetry.stamp_open()
    sleep(seconds)
    end_utc, end_mono = power_telemetry.stamp_close()
    active["phase"] = "idle"
    return {**record, "start_utc": start_utc, "end_utc": end_utc, "start_monotonic_ns": start_mono,
            "end_monotonic_ns": end_mono, "actual_seconds": (end_mono - start_mono) / 1e9}


def terminate_session(process):
    """Stop a worker's whole process group: SIGTERM, then SIGKILL after 15 s."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def interrupted(signum, _frame):
    raise KeyboardInterrupt(f"terminated by signal {signum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scales", nargs="+", default=["1", "10", "30"])
    parser.add_argument("--engines", nargs="+", choices=ENGINES, default=DEFAULT_ENGINES,
                        help="default: the three engines of the A100 study; add polars_gpu explicitly")
    parser.add_argument("--engine-order", nargs="+", choices=ENGINES, metavar="ENGINE",
                        help="run the engines in exactly this order at every scale (a permutation of "
                             "--engines); default: a seeded shuffle per scale")
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
    parser.add_argument("--gpu-idle-timeout-seconds", type=float, default=60,
                        help="with a GPU engine selected: wait up to this long before each session for the "
                             "GPU to have no compute processes, else fail it with failure_reason gpu_not_idle")
    parser.add_argument("--power-telemetry", choices=["auto", "off"], default="auto",
                        help="auto: sample CPU/node power into power.csv from the best readable source (RAPL, amd_energy, "
                             "perf, turbostat, IPMI) and record why a source is unavailable; off: no power sampler")
    parser.add_argument("--power-interval-ms", type=float, default=float(GPU_TELEMETRY_INTERVAL_MS),
                        help="CPU power sampling interval (default: the 200 ms of the GPU sampler)")
    parser.add_argument("--ipmi-interval-seconds", type=float, default=power_telemetry.DEFAULT_IPMI_INTERVAL_SECONDS,
                        help="IPMI whole-node power sampling interval; the BMC averages over about a second")
    parser.add_argument("--idle-baseline-seconds", type=float, default=30.0,
                        help="before each session, record this long an idle window (phase=idle-baseline) of GPU and CPU "
                             "power with no workload; 0 disables it")
    parser.add_argument("--cpu-energy", choices=power_telemetry.CPU_ENERGY_MODES, default="auto",
                        help="what to do about CPU energy before anything starts (needs --power-telemetry auto). auto: stop "
                             "if RAPL/amd_energy counters exist but this user cannot read them (fix: ./setup_h100.sh "
                             "--power-only --enable-rapl-read); absent counters are recorded as unavailable. require: stop "
                             "unless a CPU energy source works. off: no check")
    pinning = parser.add_mutually_exclusive_group()
    pinning.add_argument("--socket", type=int, metavar="N",
                         help="pin the runner and so every engine process to the CPUs of socket N (physical_package_id N), "
                              "e.g. on a CPU-only bare-metal host; recorded in environment.json and power_sources.json")
    pinning.add_argument("--cpu-list", metavar="LIST",
                         help="pin the runner and every engine process to exactly these CPUs (e.g. 0-25 or 0-47,96-143)")
    parser.add_argument("--no-membind", action="store_true",
                        help="with --socket/--cpu-list: do not bind memory to the NUMA nodes of the pinned CPUs (default: bind, "
                             "set_mempolicy or numactl, and stop if neither works); recorded in environment.json")
    parser.add_argument("--gpu-energy", choices=["auto", "off"], default="auto",
                        help="auto: workers store the NVML cumulative GPU energy delta in every timed record and the "
                             "counter's resolution is probed once and recorded in environment.json; off: neither")
    parser.add_argument("--redact-gpu-identifiers", choices=["on", "off"], default="on",
                        help="on: environment.json stores nvidia-smi -q without the GPU UUID, serial number and PCI bus id "
                             "(everything the analysis reads is kept); off: store it verbatim")
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
    if not (math.isfinite(args.power_interval_ms) and args.power_interval_ms > 0
            and math.isfinite(args.ipmi_interval_seconds) and args.ipmi_interval_seconds > 0):
        parser.error("--power-interval-ms and --ipmi-interval-seconds must be positive numbers")
    if not (math.isfinite(args.idle_baseline_seconds) and args.idle_baseline_seconds >= 0):
        parser.error("--idle-baseline-seconds must be a non-negative number")
    if args.engine_order is not None and sorted(args.engine_order) != sorted(args.engines):
        parser.error(f"--engine-order {args.engine_order} must be a permutation of --engines {args.engines}")
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
    try:
        pinning = power_telemetry.resolve_pinning(args.socket, args.cpu_list)
    except power_telemetry.PinningError as exc:
        parser.error(str(exc))
    if args.no_membind and pinning is None:
        parser.error("--no-membind needs --socket or --cpu-list: memory is only bound in a pinned run")
    if pinning is not None and args.cpus > pinning["cpu_count"]:
        parser.error(f"--cpus {args.cpus} engine threads on {pinning['cpu_count']} pinned CPUs ({pinning['cpus']}): "
                     "oversubscribed; pin more CPUs or lower --cpus")
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
    cpu_energy_gate = None
    if args.power_telemetry == "auto" and args.cpu_energy != "off":
        cpu_energy_gate = power_telemetry.check_cpu_energy(args.cpu_energy)
        if not cpu_energy_gate["ok"]:
            fail("; ".join(cpu_energy_gate["problems"]) + " (--cpu-energy off or --power-telemetry off runs without it)")
    runner_affinity_before = power_telemetry.process_affinity()
    if pinning is not None:
        # Memory first: a re-execution under numactl (when set_mempolicy is refused) then starts from the original affinity.
        membind = power_telemetry.plan_membind(pinning, args.no_membind)
        if membind["enabled"]:
            try:
                membind.update(power_telemetry.apply_membind(membind["nodes"]))
            except power_telemetry.PinningError as exc:
                fail(str(exc))
        pinning["membind"] = membind
        try:
            pinning["applied_affinity"] = power_telemetry.apply_pinning(pinning["cpus"])
        except power_telemetry.PinningError as exc:
            fail(str(exc))
        pinning["mechanism"] = ("os.sched_setaffinity(0, cpus) in run_experiment.py before any child process starts; "
                                "workers, the Spark JVM and the samplers inherit it, and each worker records its own affinity")
    args.output.mkdir(parents=True, exist_ok=False)
    environment = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "arguments": vars(args) | {"output": str(args.output), "data_root": str(args.data_root), "jar": str(args.jar)},
        "platform": platform.platform(),
        "python": sys.version,
        "inventory": [capture(cmd) for cmd in [
            ["lscpu"], ["free", "-b"], ["df", "-B1", str(args.data_root)],
            ["nvidia-smi", "-q"], ["java", "-version"], [sys.executable, "-m", "pip", "freeze"],
            ["nvidia-smi", "topo", "-m"],
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv"],
        ]] + [capture([sys.executable, "-c", GPU_STACK_PROBE], timeout=300)],
        "engine_order": args.engine_order,
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
    environment["cpu_pinning"] = pinning if pinning is not None else {"pinned": False, "affinity": runner_affinity_before}
    environment["cpu_energy_gate"] = cpu_energy_gate
    environment["redactions"] = {"gpu_identifiers": args.redact_gpu_identifiers, "fields": [], "counts": {}}
    if args.redact_gpu_identifiers == "on":
        for item in environment["inventory"]:
            if isinstance(item, dict) and item.get("command", [])[:2] == ["nvidia-smi", "-q"]:
                item["output"], counts = redact_gpu_identifiers(item.get("output") or "")
                environment["redactions"]["counts"] = counts
        environment["redactions"]["fields"] = ["GPU UUID", "MIG UUID", "Serial Number", "Chassis Serial Number", "GPU PDI",
                                               "Board ID", "GPU Fabric GUID", "ClusterUUID", "Bus Id",
                                               "PCI Bus/Device/Domain", "GPU <bus id> header"]
        environment["redactions"]["scope"] = "the nvidia-smi -q inventory entry only; command-line paths are stored as given"
    telemetry = (args.output / "gpu.csv").open("w")
    anchor_before = power_telemetry.clock_anchor()
    try:
        monitor = subprocess.Popen([
            "nvidia-smi", f"--query-gpu={GPU_TELEMETRY_QUERY}",
            "--format=csv", "-lms", str(GPU_TELEMETRY_INTERVAL_MS),
        ], stdout=telemetry, stderr=subprocess.STDOUT)
        environment["gpu_telemetry"] = {"started": True}
    except OSError as exc:  # only reachable without a GPU engine: those fail fast above
        monitor = None
        environment["gpu_telemetry"] = {"started": False, "error": f"{type(exc).__name__}: {exc}"}
    # nvidia-smi stamps rows with the host's local time and no zone; the zone and a UTC/monotonic pair taken around
    # the launch let a reader place every gpu.csv row on the UTC and monotonic clocks of the per-query windows.
    environment["gpu_telemetry"].update(
        interval_ms=GPU_TELEMETRY_INTERVAL_MS, timestamp_column="timestamp",
        timestamp_format="%Y/%m/%d %H:%M:%S.%f, host local time of the nvidia-smi process, no zone suffix",
        timezone=power_telemetry.host_timezone(), anchor_before_launch=anchor_before,
        anchor_after_launch=power_telemetry.clock_anchor())
    schedule = []
    host_stop = threading.Event()
    active = {"scale": "", "engine": "", "phase": "idle"}
    environment["gpu_energy_counter"] = {"source": "disabled"}
    if args.gpu_energy == "auto" and monitor is not None:
        # In a child process: the runner itself never loads NVML or holds a GPU context. Nothing else is running yet.
        environment["gpu_energy_counter"] = probe_gpu_energy_counter()
    power = None
    if args.power_telemetry == "auto":
        try:  # instrumentation must never stop a campaign: a failed start is recorded and the run goes on
            power = power_telemetry.PowerSampler(
                args.output, interval=args.power_interval_ms / 1000, ipmi_interval=args.ipmi_interval_seconds,
                labels=lambda: (active["scale"], active["engine"], active["phase"]), pinning=pinning)
            report = power.start()
            environment["power_telemetry"] = {
                "enabled": True, "started": True, "status": report["status"], "csv": report["csv"],
                "sidecar": power.sidecar_path.name, "cpu_energy_source": report["cpu_energy_source"],
                "node_power_source": report["node_power_source"], "interval_seconds": report["interval_seconds"],
                "reasons": report["reasons"]}
        except Exception as exc:
            power = None
            environment["power_telemetry"] = {"enabled": True, "started": False, "error": f"{type(exc).__name__}: {exc}"}
    else:
        environment["power_telemetry"] = {"enabled": False}
    (args.output / "environment.json").write_text(json.dumps(environment, indent=2, default=str) + "\n")
    def sample_host():
        import csv
        with (args.output / "host.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp_utc", "scale", "engine", "phase", "cpu_percent", "available_bytes", "cached_bytes", "swap_used_bytes", "disk_free_bytes", "monotonic_ns"])
            while not host_stop.is_set():
                memory = psutil.virtual_memory()
                writer.writerow([datetime.now(timezone.utc).isoformat(), active["scale"], active["engine"], active["phase"],
                                 psutil.cpu_percent(), memory.available, getattr(memory, "cached", 0),
                                 psutil.swap_memory().used, shutil.disk_usage(args.data_root).free, time.monotonic_ns()])
                handle.flush()
                host_stop.wait(1)
    host_thread = threading.Thread(target=sample_host, daemon=True)
    host_thread.start()
    spark_submit = Path(sys.executable).parent / "spark-submit"
    env = os.environ.copy()
    env.update({"POLARS_MAX_THREADS": str(args.cpus), "OMP_NUM_THREADS": str(args.cpus),
                "OPENBLAS_NUM_THREADS": str(args.cpus), "PYSPARK_PYTHON": sys.executable,
                "SPARK_LOCAL_IP": "127.0.0.1"})
    # A signal to the runner must not leave a session running: turn SIGTERM into KeyboardInterrupt
    # so the worker's process group is stopped below and the telemetry threads are cleaned up.
    signal.signal(signal.SIGTERM, interrupted)
    try:
        for scale_index, scale in enumerate(args.scales):
            data = args.data_root / ("sf" + scale.replace(".", "p"))
            if not (data / "manifest.json").is_file():
                raise RuntimeError(f"Missing complete dataset at {data}")
            scale_output = args.output / data.name
            scale_output.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(data / "manifest.json", scale_output / "manifest.json")
            if args.engine_order is not None:
                engines = list(args.engine_order)
            else:
                engines = list(args.engines)
                random.Random(42 + scale_index).shuffle(engines)
            for position, engine in enumerate(engines, start=1):
                active.update(scale=scale, engine=engine, phase="preheat")
                target = args.output / data.name / engine
                target.parent.mkdir(parents=True, exist_ok=True)
                # Warming identical files removes an engine-order disk-cache advantage.
                warming = preheat(data)
                baseline = None
                if args.idle_baseline_seconds > 0:
                    measurable = monitor is not None or (power is not None and power.report["status"] != "unavailable")
                    baseline = run_idle_baseline(args.idle_baseline_seconds, active, bool(gpu_selected),
                                                 args.gpu_idle_timeout_seconds, measurable)
                gpu_gate = None
                if gpu_selected:
                    # Last thing before launch, so the previous session's exit is covered too.
                    active["phase"] = "gpu_idle_gate"
                    gpu_gate = wait_for_idle_gpu(args.gpu_idle_timeout_seconds)
                worker_args = [str(HERE / "worker.py"), "--engine", engine, "--data", str(data),
                               "--output", str(target), "--runs", str(args.runs), "--suite", args.suite,
                               "--query-cleanup", args.query_cleanup,
                               "--cleanup-timeout-seconds", str(args.cleanup_timeout_seconds)]
                if args.queries:
                    worker_args += ["--queries", *args.queries]
                if args.gpu_energy == "off":  # the default is not passed, so command lines match earlier campaigns
                    worker_args += ["--gpu-energy", "off"]
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
                if args.engine_order is not None:
                    item["engine_order_position"] = position
                if baseline is not None:
                    item["idle_baseline"] = baseline
                if gpu_gate is not None:
                    item["gpu_idle_gate"] = gpu_gate
                schedule.append(item)
                if gpu_gate is not None and not gpu_gate["idle"]:
                    # Not launched: a busy GPU would contaminate this session's timings.
                    item.update(failure_reason="gpu_not_idle", process_wall_seconds=0.0,
                                returncode=None, status="failed")
                    active["phase"] = "idle"
                    (args.output / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
                    if not args.keep_going:
                        raise RuntimeError(f"{engine} {data.name} not started: GPU not idle after "
                                           f"{gpu_gate['waited_seconds']:.0f}s: {gpu_gate['compute_apps'] or gpu_gate['error']}")
                    print(json.dumps({"event": "engine_failed", **item}), flush=True)
                    continue
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
                        terminate_session(process)
                        returncode = 124
                    except BaseException:  # interrupted: never leave the session running
                        terminate_session(process)
                        raise
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
        if power is not None:
            power.stop()
        if monitor is not None:
            monitor.terminate()
            monitor.wait(timeout=10)
        telemetry.close()


if __name__ == "__main__":
    main()
