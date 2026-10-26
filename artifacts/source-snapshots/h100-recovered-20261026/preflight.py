#!/usr/bin/env python3
"""H100 bring-up gate: environment checks, an SF1 smoke run of every engine, strict validation.

1. Environment: GPU (H100 unless --allow-any-gpu), driver, Java 17, Python and package pins
   (evidence/requirements.lock.txt, overlaid by requirements-gpu.txt when the GPU stack is in use),
   RAPIDS jar and sha256, cudf-polars import and Polars compatibility, free disk, SF1 inputs.
2. Run: run_experiment.py at SF1, --runs 1, baseline and complex (verified_gc) suites, every
   selected engine, into a NEW directory. Skipped with --skip-run (validate an existing directory).
3. Validation of every results.json against the interface contract in docs/H100_PLAN.md:
   every query ran on every engine; polars_gpu used raise_on_fail=True and recorded its executor;
   spark_gpu plans hold GPU scan and aggregate/join operators; outputs agree with the reference
   engine (polars) within rel 1e-8 / abs 1e-6, integers exactly; both Polars engines report the
   same Polars version; GPU memory peaks are recorded.

Writes a JSON report and exits nonzero on any failure. Examples:
  python preflight.py --data-root data
  python preflight.py --skip-run --baseline-dir rerun/x/baseline --complex-dir rerun/x/complex \\
      --engines polars spark_cpu --skip-env
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

REL_TOL, ABS_TOL = 1e-8, 1e-6
ALL_ENGINES = ("polars", "polars_gpu", "spark_cpu", "spark_gpu")
GPU_ENGINES = ("polars_gpu", "spark_gpu")
SUITE_MODULES = {"baseline": "workloads", "complex": "complex_workloads"}
MIN_DRIVER = "525.60.13"  # CUDA 12 minimum documented for RAPIDS wheels and the cuda12 Spark jar
JAR_RELATIVE = "jars/rapids-4-spark_2.12-26.08.1.jar"
STATUSES = ("PASS", "FAIL", "WARN", "INFO", "SKIP", "XFAIL")
POLARS_GPU_REQUIRED = ("executor", "executor_options", "raise_on_fail", "device", "cudf_polars_version", "memory_resource")


class Checks:
    def __init__(self, quiet: bool = False):
        self.items: list[dict] = []
        self.quiet = quiet

    def add(self, check_id: str, status: str, detail: str, **data) -> None:
        assert status in STATUSES, status
        self.items.append({"id": check_id, "status": status, "detail": detail, **data})
        if not self.quiet:
            print(f"[{status:5s}] {check_id}: {detail}", flush=True)

    def counts(self) -> dict:
        return {s: sum(1 for c in self.items if c["status"] == s) for s in STATUSES}

    def failed(self, strict: bool = False) -> bool:
        counts = self.counts()
        return counts["FAIL"] > 0 or (strict and counts["WARN"] > 0)

    def summarize(self, check_id: str, problems: list[str], ok_detail: str, limit: int = 6) -> None:
        if problems:
            more = f" (+{len(problems) - limit} more)" if len(problems) > limit else ""
            self.add(check_id, "FAIL", f"{len(problems)} problem(s): " + "; ".join(problems[:limit]) + more,
                     problems=problems)
        else:
            self.add(check_id, "PASS", ok_detail)


# ---- helpers ----------------------------------------------------------------------------------

def run_command(command, timeout=60):
    try:
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return result.returncode, result.stdout


def version_tuple(text: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:4])


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s#;]+)", line.strip())
        if match:
            pins[normalize_name(match.group(1))] = match.group(2)
    return pins


def sha256_file(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text())


def compare_values(expected, actual) -> dict:
    """Ordered comparison; ints exact, floats rel 1e-8 / abs 1e-6 (same rule as the analyzers)."""
    differences, count = [], 0

    def mismatch(path, reason, left, right):
        nonlocal count
        count += 1
        if len(differences) < 10:
            differences.append({"path": path, "reason": reason, "expected": left, "actual": right})

    def visit(left, right, path):
        if isinstance(left, bool) or isinstance(right, bool):
            if type(left) is not type(right) or left != right:
                mismatch(path, "boolean differs", left, right)
        elif isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if not math.isfinite(left) or not math.isfinite(right):
                mismatch(path, "non-finite numeric output", str(left), str(right))
            elif isinstance(left, int) and isinstance(right, int):
                if left != right:
                    mismatch(path, "integer differs", left, right)
            elif not math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL):
                mismatch(path, "numeric value differs", left, right)
        elif isinstance(left, dict) and isinstance(right, dict):
            if left.keys() != right.keys():
                mismatch(path, "column names differ", sorted(left), sorted(right))
            for key in sorted(left.keys() & right.keys()):
                visit(left[key], right[key], f"{path}.{key}")
        elif isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                mismatch(path, "row count differs", len(left), len(right))
            for index, (a, b) in enumerate(zip(left, right)):
                visit(a, b, f"{path}[{index}]")
        elif type(left) is not type(right) or left != right:
            mismatch(path, "value or type differs", left, right)

    visit(expected, actual, "$")
    return {"passed": count == 0, "mismatch_count": count, "differences": differences}


# ---- environment checks -------------------------------------------------------------------------

def check_environment(checks: Checks, args, engines) -> None:
    gpu_wanted = [e for e in engines if e in GPU_ENGINES]
    checks.add("env.platform", "INFO", f"{platform.platform()} python {platform.python_version()} cpus {os.cpu_count()}")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    checks.add("env.virtualenv", "PASS" if in_venv else "WARN", sys.prefix if in_venv else "not running inside a virtualenv")

    # GPU / driver
    if gpu_wanted:
        code, out = run_command(["nvidia-smi", "--query-gpu=index,name,driver_version,memory.total",
                                 "--format=csv,noheader,nounits"])
        if code != 0 or not out.strip():
            checks.add("env.gpu", "FAIL", f"GPU engine(s) {gpu_wanted} selected but nvidia-smi failed: {out.strip()[:200]}")
        else:
            rows = [[field.strip() for field in line.split(",")] for line in out.strip().splitlines()]
            index, name, driver, memory = rows[0]
            if len(rows) > 1:
                checks.add("env.gpu_count", "WARN", f"{len(rows)} GPUs visible; the study assumes one (device 0 is used)")
            if "H100" in name:
                checks.add("env.gpu", "PASS", f"{name}, driver {driver}, {memory} MiB")
            elif args.allow_any_gpu:
                checks.add("env.gpu", "WARN", f"{name} is not an H100 (--allow-any-gpu): rehearsal only; driver {driver}, {memory} MiB")
            else:
                checks.add("env.gpu", "FAIL", f"{name} is not an H100 (use --allow-any-gpu for a rehearsal)")
            if version_tuple(driver) >= version_tuple(MIN_DRIVER):
                checks.add("env.driver", "PASS", f"{driver} >= {MIN_DRIVER}")
            else:
                checks.add("env.driver", "FAIL", f"{driver} < {MIN_DRIVER} required by the CUDA 12 wheels and jar")
            _, header = run_command(["nvidia-smi"])
            cuda = re.search(r"CUDA Version:\s*([\d.]+)", header or "")
            checks.add("env.cuda_driver_api", "INFO", f"nvidia-smi reports CUDA {cuda.group(1) if cuda else 'unknown'}")
            code, apps = run_command(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                                      "--format=csv,noheader,nounits"])
            if code == 0 and apps.strip():
                checks.add("env.gpu_idle", "WARN", "other processes hold the GPU: " + apps.strip().replace("\n", " | ")[:300])
            elif code == 0:
                checks.add("env.gpu_idle", "PASS", "no compute processes on the GPU")
    else:
        checks.add("env.gpu", "SKIP", "no GPU engine selected")

    # Java
    if any(e.startswith("spark") for e in engines):
        code, out = run_command(["java", "-version"])
        first = (out or "").strip().splitlines()[0] if out and out.strip() else ""
        if code == 0 and re.search(r'"17\.', first):
            checks.add("env.java", "PASS", f"{first} (JAVA_HOME={os.environ.get('JAVA_HOME', 'unset')})")
        else:
            checks.add("env.java", "FAIL", f"Java 17 required for Spark 3.5.9 as recorded; got: {first or out}")
    else:
        checks.add("env.java", "SKIP", "no Spark engine selected")

    # Python version
    if "polars_gpu" in engines and sys.version_info < (3, 11):
        checks.add("env.python", "FAIL", f"Python {platform.python_version()}: cudf-polars >= 26.4 needs Python >= 3.11")
    else:
        checks.add("env.python", "PASS", platform.python_version())

    # package pins
    lock = parse_pins(HERE / "evidence" / "requirements.lock.txt")
    gpu_file = HERE / "requirements-gpu.txt"
    overlay = args.pins == "lock+gpu" or (args.pins == "auto" and ("polars_gpu" in engines or _installed("cudf-polars-cu12")
                                                                    or _installed("cudf-polars-cu13")))
    pins = dict(lock)
    if overlay:
        pins.update(parse_pins(gpu_file))
    problems = []
    for name, wanted in sorted(pins.items()):
        have = _installed(name)
        if have is None:
            problems.append(f"{name}: not installed (want {wanted})")
        elif have != wanted:
            problems.append(f"{name}: installed {have}, want {wanted}")
    checks.summarize("env.package_pins", problems,
                     f"{len(pins)} pins from requirements.lock.txt{' + requirements-gpu.txt' if overlay else ''} match the installed packages")

    # cudf-polars import and Polars compatibility, in a child so this process never holds a CUDA context
    if "polars_gpu" in engines:
        probe = (
            "import json, importlib.metadata as m, polars as pl\n"
            "out = {'polars': pl.__version__}\n"
            "import cudf_polars\n"
            "out['cudf_polars'] = getattr(cudf_polars, '__version__', None)\n"
            "from cudf_polars.utils.config import ConfigOptions\n"
            f"engine = pl.GPUEngine(raise_on_fail=True, executor={args.polars_gpu_executor!r})\n"
            "options = ConfigOptions.from_polars_engine(engine)\n"
            "out['executor'] = options.executor.name; out['raise_on_fail'] = options.raise_on_fail\n"
            "for dist in ('cudf-polars-cu12', 'cudf-polars-cu13'):\n"
            "    try:\n"
            "        out['requires'] = [r for r in (m.requires(dist) or []) if r.lower().startswith('polars')]; out['dist'] = dist; break\n"
            "    except m.PackageNotFoundError:\n"
            "        pass\n"
            "print(json.dumps(out))\n")
        code, out = run_command([sys.executable, "-c", probe], timeout=300)
        try:
            info = json.loads(out.strip().splitlines()[-1]) if code == 0 else None
        except (ValueError, IndexError):
            info = None
        if info is None:
            checks.add("env.cudf_polars", "FAIL", f"cudf_polars import/config probe failed: {(out or '').strip()[-400:]}")
        else:
            ok = info.get("executor") == args.polars_gpu_executor and info.get("raise_on_fail") is True
            checks.add("env.cudf_polars", "PASS" if ok else "FAIL",
                       f"cudf-polars {info.get('cudf_polars')} with polars {info['polars']}; resolved executor "
                       f"{info.get('executor')!r}, raise_on_fail {info.get('raise_on_fail')}; requires {info.get('requires')}")
            try:
                from packaging.requirements import Requirement
                bad = [r for r in info.get("requires", []) if not Requirement(r).specifier.contains(info["polars"], prereleases=True)]
                checks.add("env.polars_supported_by_cudf_polars", "FAIL" if bad else "PASS",
                           f"polars {info['polars']} violates {bad}" if bad else f"polars {info['polars']} satisfies {info.get('requires')}")
            except ImportError:
                checks.add("env.polars_supported_by_cudf_polars", "SKIP", "packaging not importable")
    else:
        checks.add("env.cudf_polars", "SKIP", "polars_gpu not selected")

    # jar
    if "spark_gpu" in engines:
        jar = args.jar
        expected = None
        for line in (HERE / "evidence" / "DEPENDENCY_SHA256.txt").read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].endswith(Path(JAR_RELATIVE).name):
                expected = parts[0]
        if not jar.is_file():
            checks.add("env.jar", "FAIL", f"{jar} not found")
        elif expected is None:
            checks.add("env.jar", "FAIL", "no checksum for the jar in evidence/DEPENDENCY_SHA256.txt")
        else:
            actual = sha256_file(jar)
            checks.add("env.jar", "PASS" if actual == expected else "FAIL",
                       f"{jar.name} sha256 {'matches' if actual == expected else 'MISMATCH ' + actual}")
    else:
        checks.add("env.jar", "SKIP", "spark_gpu not selected")

    # disk
    need = args.min_free_gib if args.min_free_gib is not None else (500 if gpu_wanted else 2)
    for label, path in (("data_root", args.data_root), ("output", args.output)):
        probe_path = path
        while not probe_path.exists() and probe_path != probe_path.parent:
            probe_path = probe_path.parent
        free = shutil.disk_usage(probe_path).free / 2**30
        checks.add(f"env.disk_{label}", "PASS" if free >= need else "FAIL", f"{free:.0f} GiB free at {probe_path} (need >= {need})")


def _installed(name: str):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def check_inputs(checks: Checks, args, scale: str) -> None:
    import verify_generated_inputs
    dataset = args.data_root / ("sf" + scale.replace(".", "p"))
    if not dataset.is_dir() and not args.no_generate:
        # Same parameters as the A100 manifests (30 threads sets the file layout); generate_data.py
        # never overwrites an existing dataset and publishes atomically.
        memory = default_driver_memory().replace("g", "GB") if default_driver_memory() != "96g" else "64GB"
        command = [sys.executable, str(HERE / "generate_data.py"), "--root", str(args.data_root), "--scales", scale,
                   "--threads", "30", "--memory-limit", memory]
        started = time.perf_counter()
        args.output.mkdir(parents=True, exist_ok=True)
        with (args.output / f"generate-sf{scale}.log").open("w") as handle:
            code = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, cwd=HERE).returncode
        checks.add(f"inputs.generate_sf{scale}", "PASS" if code == 0 else "FAIL",
                   f"generate_data.py exit {code} in {time.perf_counter() - started:.0f}s ({' '.join(command[1:])})")
    if not dataset.is_dir():
        checks.add("inputs", "FAIL", f"{dataset} missing; generate it first (generate_data.py --root {args.data_root} --scales {scale} --threads 30)")
        return
    report_path = args.output / f"inputs-sf{scale}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists():
        report_path.unlink()  # derived artifact of this very preflight directory
    code = verify_generated_inputs.main(["--data-root", str(args.data_root), "--scales", scale,
                                         "--report", str(report_path), "--threads", str(os.cpu_count() or 4)])
    report = read_json(report_path)
    counts = report["datasets"][0]["counts"]
    status = "FAIL" if code != 0 else ("WARN" if counts["WARN"] else "PASS")
    checks.add("inputs", status, f"verify_generated_inputs sf{scale}: {counts}; report {report_path}")


# ---- run ------------------------------------------------------------------------------------------

def default_driver_memory() -> str:
    try:
        import psutil
        total_gib = psutil.virtual_memory().total / 2**30
    except ImportError:
        return "96g"
    return "96g" if total_gib >= 200 else f"{max(2, int(total_gib * 0.4))}g"


def run_suite(checks: Checks, args, suite: str, run_dir: Path, engines) -> bool:
    command = [sys.executable, str(HERE / "run_experiment.py"), "--suite", suite, "--data-root", str(args.data_root),
               "--output", str(run_dir), "--scales", *args.scales, "--engines", *engines, "--runs", str(args.runs),
               "--cpus", str(args.cpus), "--driver-memory", args.driver_memory, "--jar", str(args.jar),
               "--keep-going", "--engine-timeout-seconds", str(args.engine_timeout_seconds)]
    if suite == "complex":
        command += ["--query-cleanup", "verified_gc", "--cleanup-timeout-seconds", "60"]
    if "polars_gpu" in engines:
        command += ["--polars-gpu-executor", args.polars_gpu_executor]
        if args.polars_gpu_executor_options:
            command += ["--polars-gpu-executor-options", args.polars_gpu_executor_options]
    log = args.output / f"run-{suite}.log"
    started = time.perf_counter()
    with log.open("w") as handle:
        handle.write(" ".join(command) + "\n")
        handle.flush()
        code = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, cwd=HERE).returncode
    seconds = time.perf_counter() - started
    checks.add(f"run.{suite}", "PASS" if code == 0 else "FAIL",
               f"run_experiment.py exit {code} in {seconds:.0f}s; log {log}", command=command, returncode=code)
    return code == 0


# ---- validation -----------------------------------------------------------------------------------

# Identical to the cpu_core_operators pattern in worker.py (Spark CPU operators that must not remain).
CPU_CORE_OPERATOR_RE = re.compile(
    r"\b(?:HashAggregate|ObjectHashAggregate|SortAggregate|BroadcastHashJoin|SortMergeJoin|"
    r"ShuffledHashJoin|BroadcastNestedLoopJoin|CartesianProduct|Window|WindowGroupLimit|"
    r"Expand|Sort|FileScan|BatchScan|TakeOrderedAndProject)\b")
COMPLEX_GPU_COMPUTE = ("Aggregate", "Join", "Window", "Sort", "TopN")


def gpu_plan_problems(plan_text: str, suite: str = "baseline") -> list[str]:
    """Acceptance rule for a spark_gpu final plan.

    baseline: GPU scan plus a GPU aggregate or join (same as analyze_results.inspect_gpu_plan).
    complex:  GPU scan plus at least one GPU compute operator (aggregate, join, window, sort/top-N)
              and no CPU core operator; cx_window has no aggregate or join by design.
    """
    if "AdaptiveSparkPlan" in plan_text and "isFinalPlan=true" not in plan_text:
        return ["adaptive plan not marked isFinalPlan=true"]
    final = plan_text.split("== Final Plan ==", 1)[-1].split("== Initial Plan ==", 1)[0]
    names = set(re.findall(r"\bGpu[A-Z][A-Za-z0-9]*\b", final))
    problems = []
    if not any("Scan" in n for n in names):
        problems.append("no GPU scan operator in the final plan")
    if suite == "complex":
        if not any(kind in n for n in names for kind in COMPLEX_GPU_COMPUTE):
            problems.append("no GPU aggregate, join, window or sort/top-N operator in the final plan")
        cpu = sorted(set(CPU_CORE_OPERATOR_RE.findall(final)))
        if cpu:
            problems.append(f"CPU core operators remain in the final plan: {cpu}")
    elif not any("Aggregate" in n or "Join" in n for n in names):
        problems.append("no GPU aggregate or join operator in the final plan")
    return problems


def validate_engine(checks: Checks, tag: str, suite: str, queries, engine: str, session: dict, run_dir: Path,
                    scale_dir: Path, runs: int, args, reference_rows: dict | None, schedule_item: dict | None) -> dict:
    """Validate one results.json. Returns {query: warmup rows} for use as a reference."""
    expected_unsupported = set(args.expect_gpu_unsupported) if engine == "polars_gpu" else set()

    # --- run / session health
    problems = []
    if schedule_item is None:
        problems.append("engine missing from schedule.json")
    elif schedule_item.get("status") != "complete" or schedule_item.get("returncode") != 0:
        problems.append(f"schedule status {schedule_item.get('status')!r} returncode {schedule_item.get('returncode')}")
    for key in ("failure", "cleanup_failure"):
        if session.get(key):
            problems.append(f"{key}: {json.dumps(session[key])[:300]}")
    if not session.get("completed_at_utc"):
        problems.append("completed_at_utc missing (session did not finish)")
    if session.get("query_execution_status") not in (None, "complete", "partial"):
        problems.append(f"query_execution_status {session.get('query_execution_status')!r}")
    if session.get("engine") != engine:
        problems.append(f"results.json engine is {session.get('engine')!r}")
    if session.get("runs") != runs:
        problems.append(f"runs {session.get('runs')} != {runs}")
    checks.summarize(f"{tag}:{engine}:session", problems, "completed, no session failure")

    # --- query errors
    errors = session.get("query_errors") or []
    unexpected, expected_hits = [], {}
    for error in errors:
        if error.get("query") in expected_unsupported:
            expected_hits.setdefault(error["query"], error)
        else:
            unexpected.append(f"{error.get('query')}/{error.get('phase')}: {error.get('type')}: {str(error.get('message'))[:160]}")
    checks.summarize(f"{tag}:{engine}:query_errors", unexpected, "no unexpected query errors")
    for query, error in sorted(expected_hits.items()):
        checks.add(f"{tag}:{engine}:{query}", "XFAIL", f"expected unsupported on polars_gpu: {error.get('type')}: {str(error.get('message'))[:200]}")

    # --- records
    records = session.get("records") or []
    by_query: dict[str, list[dict]] = {}
    for record in records:
        by_query.setdefault(record["query"], []).append(record)
    problems = []
    rows_by_query = {}
    for query in queries:
        recs = by_query.get(query, [])
        if query in expected_hits:
            continue
        if query in expected_unsupported and recs:
            checks.add(f"{tag}:{engine}:{query}", "WARN", "listed as expected-unsupported but it ran (XPASS): drop it from --expect-gpu-unsupported")
        warm = [r for r in recs if r.get("phase") == "warmup"]
        measured = [r for r in recs if r.get("phase") == "measured"]
        if len(warm) != 1 or len(measured) != runs:
            problems.append(f"{query}: {len(warm)} warmup / {len(measured)} measured records (want 1 / {runs})")
            continue
        for record in recs:
            seconds = record.get("seconds")
            if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
                problems.append(f"{query}/{record.get('phase')}{record.get('trial')}: bad seconds {seconds!r}")
            if not isinstance(record.get("rows"), list) or not record["rows"]:
                problems.append(f"{query}/{record.get('phase')}{record.get('trial')}: empty or missing result rows")
        rows_by_query[query] = warm[0].get("rows")
    unknown = sorted(set(by_query) - set(queries))
    if unknown:
        problems.append(f"records for unknown queries {unknown}")
    checks.summarize(f"{tag}:{engine}:records", problems,
                     f"{len(queries) - len(expected_hits)} queries x (1 warmup + {runs} measured), finite times, non-empty results")

    # --- engine-specific contract
    problems, notes = [], []
    expected_engine = {"polars": "streaming", "polars_gpu": "gpu"}.get(engine)
    if session.get("polars_execution_engine") != expected_engine:
        problems.append(f"polars_execution_engine {session.get('polars_execution_engine')!r} != {expected_engine!r}")
    if engine == "polars_gpu":
        gpu = session.get("polars_gpu")
        if not isinstance(gpu, dict):
            problems.append("polars_gpu metadata object missing")
        else:
            missing = [k for k in POLARS_GPU_REQUIRED if k not in gpu]
            if missing:
                problems.append(f"polars_gpu metadata missing keys {missing}")
            if gpu.get("raise_on_fail") is not True:
                problems.append(f"raise_on_fail is {gpu.get('raise_on_fail')!r}, must be true")
            if gpu.get("executor") != args.polars_gpu_executor:
                problems.append(f"executor {gpu.get('executor')!r} != requested {args.polars_gpu_executor!r}")
            if not isinstance(gpu.get("device"), int):
                problems.append("device is not an int")
            if not gpu.get("cudf_polars_version"):
                problems.append("cudf_polars_version empty")
            guard = gpu.get("session_guard") or {}
            if "unsupported_canary_error" not in guard:
                checks.add(f"{tag}:{engine}:strictness_canary", "WARN", "session guard recorded no unsupported-op canary error")
            notes.append(f"executor={gpu.get('executor')} cudf-polars={gpu.get('cudf_polars_version')} raise_on_fail=true")
    else:
        if "polars_gpu" in session:
            problems.append("polars_gpu metadata present on a non-polars_gpu engine")
    if engine == "spark_gpu":
        if session.get("spark_gpu_profile") not in ("a100-parity", "h100-tuned"):
            problems.append(f"spark_gpu_profile {session.get('spark_gpu_profile')!r}")
        if args.spark_gpu_profile and session.get("spark_gpu_profile") != args.spark_gpu_profile:
            problems.append(f"spark_gpu_profile {session.get('spark_gpu_profile')!r} != {args.spark_gpu_profile!r}")
        if "com.nvidia.spark.SQLPlugin" not in (session.get("spark_conf") or {}).get("spark.plugins", ""):
            problems.append("spark.plugins does not include com.nvidia.spark.SQLPlugin")
    elif "spark_gpu_profile" in session and session["spark_gpu_profile"] is not None:
        problems.append("spark_gpu_profile present on a non-spark_gpu engine")
    if engine in GPU_ENGINES:
        inventory = session.get("gpu_inventory")
        if not isinstance(inventory, dict) or not inventory.get("name"):
            problems.append(f"gpu_inventory missing or has no device name: {inventory}")
        elif "H100" not in inventory["name"] and not args.allow_any_gpu:
            problems.append(f"gpu_inventory device {inventory['name']!r} is not an H100")
    else:
        if "gpu_inventory" in session:
            problems.append("gpu_inventory present on a CPU engine")
    expected_class = {"polars": "cpu_engine", "spark_cpu": "cpu_engine", "polars_gpu": "gpu_engine_strict"}.get(engine)
    for record in records:
        cls = record.get("gpu_execution_class")
        if engine == "spark_gpu":
            if cls not in ("hybrid", "gpu_operators_present"):
                problems.append(f"{record['query']}/{record['phase']}{record['trial']}: gpu_execution_class {cls!r}")
        elif cls != expected_class:
            problems.append(f"{record['query']}/{record['phase']}{record['trial']}: gpu_execution_class {cls!r} != {expected_class!r}")
        if (engine in GPU_ENGINES) != ("peak_gpu_memory_bytes" in record):
            problems.append(f"{record['query']}/{record['phase']}{record['trial']}: peak_gpu_memory_bytes presence wrong for {engine}")
    if suite == "complex":
        cleanup = session.get("cleanup_records") or []
        if session.get("query_cleanup_policy") != "verified_gc":
            problems.append(f"query_cleanup_policy {session.get('query_cleanup_policy')!r}, complex runs need 'verified_gc'")
        if len(cleanup) != len(records):
            problems.append(f"{len(cleanup)} cleanup_records for {len(records)} query records")
        not_passed = [c.get("query") for c in cleanup if c.get("status") != "passed"]
        if not_passed:
            problems.append(f"cleanup barrier did not pass for {not_passed[:5]}")
        notes.append(f"{len(cleanup)} verified_gc barriers passed" if not problems else "")
    checks.summarize(f"{tag}:{engine}:contract", problems, "; ".join(n for n in notes if n) or "interface-contract fields present and consistent")

    # --- plans
    problems = []
    for record in records:
        plan_path = scale_dir / engine / record.get("plan", "")
        if not record.get("plan") or not plan_path.is_file():
            problems.append(f"{record['query']}/{record['phase']}{record['trial']}: plan file missing")
            continue
        text = plan_path.read_text()
        if engine == "spark_gpu":
            if record.get("gpu_plan_observed") is not True:
                problems.append(f"{record['query']}: gpu_plan_observed false")
            problems += [f"{record['query']}/{record['phase']}{record['trial']}: {p}" for p in gpu_plan_problems(text, suite)]
        elif engine == "spark_cpu" and re.search(r"\bGpu[A-Z]", text):
            problems.append(f"{record['query']}: GPU operator in a CPU Spark plan")
    checks.summarize(f"{tag}:{engine}:plans", problems[:50],
                     {"spark_gpu": "every plan has GPU scan and aggregate/join operators",
                      "spark_cpu": "no GPU operators in CPU Spark plans"}.get(engine, "plan files present"))

    # --- GPU memory and polars_gpu warnings
    if engine in GPU_ENGINES:
        total = (session.get("gpu_inventory") or {}).get("memory_total_bytes")
        peaks = [r.get("peak_gpu_memory_bytes") for r in records]
        nulls = sum(1 for p in peaks if p is None)
        too_big = [p for p in peaks if isinstance(p, int) and total and p > total]
        if too_big:
            checks.add(f"{tag}:{engine}:gpu_memory", "FAIL", f"peak {max(too_big)} exceeds device memory {total}")
        elif peaks and nulls == len(peaks):
            checks.add(f"{tag}:{engine}:gpu_memory", "WARN", "peak_gpu_memory_bytes is null for every record (sampling unavailable)")
        elif nulls:
            checks.add(f"{tag}:{engine}:gpu_memory", "WARN", f"peak_gpu_memory_bytes null for {nulls}/{len(peaks)} records")
        else:
            checks.add(f"{tag}:{engine}:gpu_memory", "PASS",
                       f"peak device memory recorded for all {len(peaks)} records (max {max(peaks) / 2**20:.0f} MiB)" if peaks else "no records")
    if engine == "polars_gpu":
        cpu_fallback, single_partition = [], []
        for record in records:
            for warning in record.get("gpu_engine_warnings") or []:
                (cpu_fallback if re.search(r"\bCPU\b|polars (cpu )?engine", warning, re.I) else single_partition).append(
                    f"{record['query']}: {warning[:160]}")
        if cpu_fallback:
            checks.add(f"{tag}:{engine}:fallback_warnings", "FAIL", "warnings that mention CPU fallback: " + "; ".join(sorted(set(cpu_fallback))[:3]))
        elif single_partition:
            checks.add(f"{tag}:{engine}:fallback_warnings", "WARN", "cudf-polars warnings (streaming fallback, still GPU): " + "; ".join(sorted(set(single_partition))[:3]))
        else:
            checks.add(f"{tag}:{engine}:fallback_warnings", "PASS", "no cudf-polars warnings recorded")

    # --- agreement with the reference engine
    if reference_rows is not None:
        problems = []
        for query in queries:
            if query in expected_hits or query not in by_query:
                continue
            if query not in reference_rows:
                problems.append(f"{query}: the reference engine has no usable result to compare with")
                continue
            for record in by_query[query]:
                result = compare_values(reference_rows[query], record.get("rows"))
                if not result["passed"]:
                    first = result["differences"][0] if result["differences"] else {}
                    problems.append(f"{query}/{record['phase']}{record['trial']}: {result['mismatch_count']} mismatch(es), first {first}")
        checks.summarize(f"{tag}:{engine}:agreement", problems[:20],
                         f"all records match the reference engine within rel {REL_TOL:g} / abs {ABS_TOL:g} (integers exact)")
    return rows_by_query


def validate_suite(checks: Checks, suite: str, run_dir: Path, scale: str, engines, runs: int, args) -> None:
    tag = f"{suite}/sf{scale.replace('.', 'p')}"
    module = importlib.import_module(SUITE_MODULES[suite])
    queries = list(module.QUERY_IDS)
    schedule_path = run_dir / "schedule.json"
    if not schedule_path.is_file():
        checks.add(f"{tag}:schedule", "FAIL", f"{schedule_path} missing")
        return
    schedule = {(item["scale"], item["engine"]): item for item in read_json(schedule_path)}
    scale_dir = run_dir / ("sf" + scale.replace(".", "p"))
    sessions, problems = {}, []
    for engine in engines:
        path = scale_dir / engine / "results.json"
        if not path.is_file():
            problems.append(f"{engine}: results.json missing")
            continue
        sessions[engine] = read_json(path)
    checks.summarize(f"{tag}:results_present", problems, f"results.json for {', '.join(engines)}")
    manifests = {s.get("manifest_sha256") for s in sessions.values()}
    checks.add(f"{tag}:same_input", "PASS" if len(manifests) <= 1 else "FAIL",
               f"all engines read manifest {next(iter(manifests), None)}" if len(manifests) <= 1 else f"engines read different manifests: {manifests}")
    versions = {e: sessions[e].get("version") for e in ("polars", "polars_gpu") if e in sessions}
    if len(versions) == 2:
        checks.add(f"{tag}:polars_versions", "PASS" if len(set(versions.values())) == 1 else "FAIL",
                   f"polars and polars_gpu both report Polars {versions['polars']}" if len(set(versions.values())) == 1
                   else f"Polars versions differ between engines: {versions}")
    reference_engine = next((e for e in ("polars", "spark_cpu", "polars_gpu", "spark_gpu") if e in sessions), None)
    reference_rows = None
    ordered = ([reference_engine] if reference_engine else []) + [e for e in engines if e in sessions and e != reference_engine]
    for engine in ordered:
        rows = validate_engine(checks, tag, suite, queries, engine, sessions[engine], run_dir, scale_dir, runs, args,
                               reference_rows if engine != reference_engine else None, schedule.get((scale, engine)))
        if engine == reference_engine:
            reference_rows = rows
            checks.add(f"{tag}:reference", "INFO", f"{engine} is the reference for output agreement")
            problems = []
            for query, expected in rows.items():
                for record in [r for r in sessions[engine]["records"] if r["query"] == query]:
                    if not compare_values(expected, record.get("rows"))["passed"]:
                        problems.append(f"{query}/{record.get('phase')}{record.get('trial')}")
            checks.summarize(f"{tag}:{engine}:self_agreement", problems, "reference engine is self-consistent across records")


def detect_engines(dirs) -> list[str]:
    found = []
    for run_dir in dirs:
        schedule = run_dir / "schedule.json"
        if schedule.is_file():
            for item in read_json(schedule):
                if item["engine"] not in found:
                    found.append(item["engine"])
    return [e for e in ALL_ENGINES if e in found]


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=Path(os.environ.get("DATA_ROOT", "data")))
    parser.add_argument("--output", type=Path, help="new directory for baseline/, complex/, logs and the report "
                        "(default rerun/preflight-<utc>)")
    parser.add_argument("--scales", nargs="+", default=["1"])
    parser.add_argument("--engines", nargs="+", choices=ALL_ENGINES)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--cpus", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--driver-memory", default=default_driver_memory())
    parser.add_argument("--jar", type=Path, default=HERE / JAR_RELATIVE)
    parser.add_argument("--suites", nargs="+", choices=list(SUITE_MODULES), default=list(SUITE_MODULES))
    parser.add_argument("--polars-gpu-executor", choices=["in-memory", "streaming"], default="streaming")
    parser.add_argument("--polars-gpu-executor-options", default=None, help="JSON object passed through to run_experiment.py")
    parser.add_argument("--spark-gpu-profile", choices=["a100-parity", "h100-tuned"], default="a100-parity")
    parser.add_argument("--engine-timeout-seconds", type=float, default=1800)
    parser.add_argument("--allow-any-gpu", action="store_true", help="A100 rehearsal: accept a non-H100 GPU")
    parser.add_argument("--expect-gpu-unsupported", nargs="*", default=[], metavar="QUERY",
                        help="queries whose polars_gpu failure is an accepted, documented gap (reported XFAIL); default none")
    parser.add_argument("--pins", choices=["auto", "lock", "lock+gpu"], default="auto")
    parser.add_argument("--min-free-gib", type=int, default=None, help="default 500 with a GPU engine, else 2")
    parser.add_argument("--skip-env", action="store_true")
    parser.add_argument("--skip-inputs", action="store_true", help="do not run verify_generated_inputs.py")
    parser.add_argument("--no-generate", action="store_true",
                        help="do not generate a missing SF dataset (generate_data.py --threads 30, as in the A100 manifests)")
    parser.add_argument("--skip-run", action="store_true", help="validate existing run directories only")
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--complex-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--strict", action="store_true", help="treat WARN as failure")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    args.output = (args.output or HERE / "rerun" / f"preflight-{stamp}").resolve()
    args.data_root = args.data_root.resolve()
    run_dirs = {"baseline": (args.baseline_dir or args.output / "baseline").resolve(),
                "complex": (args.complex_dir or args.output / "complex").resolve()}
    suites = [s for s in args.suites if s in run_dirs]
    if args.skip_run:
        engines = list(args.engines or detect_engines([run_dirs[s] for s in suites]))
        if not engines:
            parser.error("--skip-run found no engines in the run directories' schedule.json; pass --engines")
    else:
        engines = list(args.engines or ALL_ENGINES)
        for suite in suites:
            if run_dirs[suite].exists():
                parser.error(f"{run_dirs[suite]} already exists; preflight needs a new directory")
    args.engines = engines
    report_path = (args.report or args.output / "preflight-report.json").resolve()
    if report_path.exists():
        parser.error(f"refusing to overwrite {report_path}")
    args.output.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    print(f"preflight: engines={engines} suites={suites} scales={args.scales} output={args.output}", flush=True)

    if not args.skip_env:
        check_environment(checks, args, engines)
    else:
        checks.add("env", "SKIP", "--skip-env")
    if not args.skip_inputs and not args.skip_env and not checks.failed():
        for scale in args.scales:
            check_inputs(checks, args, scale)
    elif not args.skip_inputs and not args.skip_env:
        checks.add("inputs", "SKIP", "environment checks failed; not verifying inputs")
    if args.skip_run:
        checks.add("run", "SKIP", "--skip-run: validating existing directories")
    elif checks.failed():
        checks.add("run", "SKIP", "environment/input checks failed; the smoke run was not started")
    else:
        for suite in suites:
            run_suite(checks, args, suite, run_dirs[suite], engines)
    if not any(c["id"] == "run" and c["status"] == "SKIP" and "failed" in c["detail"] for c in checks.items):
        for suite in suites:
            if not run_dirs[suite].is_dir():
                checks.add(f"{suite}", "FAIL", f"{run_dirs[suite]} does not exist")
                continue
            for scale in args.scales:
                validate_suite(checks, suite, run_dirs[suite], scale, engines, args.runs, args)

    ok = not checks.failed(args.strict)
    report = {"tool": "preflight", "format": 1, "started_at_utc": started.isoformat(),
              "completed_at_utc": datetime.now(timezone.utc).isoformat(), "ok": ok, "counts": checks.counts(),
              "engines": engines, "suites": suites, "scales": args.scales, "runs": args.runs,
              "run_dirs": {k: str(v) for k, v in run_dirs.items()},
              "args": {k: str(v) for k, v in vars(args).items()},
              "host": {"platform": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count()},
              "checks": checks.items}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, default=str) + "\n")
    temporary.replace(report_path)
    print(f"report: {report_path}")
    print("PREFLIGHT:", "OK" if ok else "FAILED", checks.counts(), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
