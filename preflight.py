#!/usr/bin/env python3
"""H100 bring-up gate: environment checks, an SF1 smoke run of every engine, strict validation.

1. Environment: GPU (H100 unless --allow-any-gpu), driver, Java 17, Python and package pins
   (evidence/requirements.lock.txt; overlaid by requirements-gpu.txt when the GPU stack is in use, or by
   requirements-cpu.txt in the CPU-only Polars 1.44.2 environment), RAPIDS jar and sha256, cudf-polars import and
   Polars compatibility, free disk, SF1 inputs, CPU energy (RAPL present but unreadable is a failure: see --cpu-energy)
   and the CPU pinning (--socket/--cpu-list) together with the memory binding to its NUMA nodes (set_mempolicy or numactl;
   --no-membind is the recorded opt-out). A CPU-only host (e.g. GCP bare metal) passes this stage with --cpu-only.
2. Run: run_experiment.py at SF1, --runs 1, baseline and complex (verified_gc) suites, every
   selected engine, into a NEW directory. Skipped with --skip-run (validate an existing directory).
3. Validation of every results.json against the interface contract in docs/H100_PLAN.md:
   every query ran on every engine; polars_gpu used raise_on_fail=True and recorded its executor;
   spark_gpu plans hold GPU scan and aggregate/join operators; outputs agree with the reference
   engine (polars) within rel 1e-8 / abs 1e-6, integers exactly; both Polars engines report the
   same Polars version; GPU memory peaks are recorded.

Campaign mode (--formal --skip-run): validates one finished round of the locked design
(docs/H100_EXPERIMENT_DESIGN.md) against the section 7 integrity gates, as amended by A1:
  G1 every session exits, no engine timeout        G4 spark_gpu plan rules (baseline / complex)
  G2 output agreement with polars                  G5 every verified_gc barrier passed (complex)
  G3 polars_gpu strict guard; failures classified  other: inputs link, controls, executed order
Errors are classified (ERROR_RULES): a GPU engine rejecting an operation or exhausting memory is a
coverage outcome (reported, not a failure); everything else, including an unclassified error, is an
integrity failure. The report carries a top-level "verdict": "pass" | "fail" and per-gate results.
Exit status: 0 pass, 1 fail (report written), 2 usage error. Never based on timings.

Writes a JSON report and exits nonzero on any failure. Examples:
  python preflight.py --data-root data
  python preflight.py --cpu-only --socket 0 --data-root data          # CPU-only bare metal, engines on socket 0
  python preflight.py --skip-run --baseline-dir rerun/x/baseline --complex-dir rerun/x/complex \\
      --engines polars spark_cpu --skip-env
  python preflight.py --formal --skip-run --skip-env --skip-inputs --round 1 \\
      --baseline-dir R/baseline --complex-dir R/complex --output R/preflight
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
import random
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import power_telemetry  # noqa: E402

REL_TOL, ABS_TOL = 1e-8, 1e-6
ALL_ENGINES = ("polars", "polars_gpu", "spark_cpu", "spark_gpu")
GPU_ENGINES = ("polars_gpu", "spark_gpu")
CPU_ENGINES = ("polars", "spark_cpu")
PIN_SETS = ("lock", "lock+cpu", "lock+gpu")
SUITE_MODULES = {"baseline": "workloads", "complex": "complex_workloads"}
MIN_DRIVER = "525.60.13"  # CUDA 12 minimum documented for RAPIDS wheels and the cuda12 Spark jar
JAR_RELATIVE = "jars/rapids-4-spark_2.12-26.08.1.jar"
# aarch64 (GH200, amendment A3): the -cuda12-arm64 classifier of the same release. Its sha256 is recorded here and
# in setup_h100.sh because evidence/DEPENDENCY_SHA256.txt lists the x86_64 jar; an entry for it there must agree.
JAR_RELATIVE_AARCH64 = "jars/rapids-4-spark_2.12-26.08.1-cuda12-arm64.jar"
JAR_SHA256_AARCH64 = "44ac76f748b2d1091dcb057eb8effa624967622cdf648ff8d0940f7f19dd4735"
STATUSES = ("PASS", "FAIL", "WARN", "INFO", "SKIP", "XFAIL", "COVERAGE")
POLARS_GPU_REQUIRED = ("executor", "executor_options", "raise_on_fail", "device", "cudf_polars_version", "memory_resource")


# Integrity gates of docs/H100_EXPERIMENT_DESIGN.md section 7 (+ "other" for controls and linkage).
G1, G2, G3, G4, G5, GO = ("G1_sessions_complete", "G2_output_agreement", "G3_polars_gpu_strict",
                          "G4_spark_gpu_plans", "G5_verified_gc", "other")
GATES = (G1, G2, G3, G4, G5, GO)
GATE_TITLES = {
    G1: "every session exits, no engine timeout, every query ran (or is a classified coverage outcome)",
    G2: "outputs agree with polars: rel 1e-8 / abs 1e-6, integers exact",
    G3: "polars_gpu strict session guard passed; failures are classified, none unclassified",
    G4: "spark_gpu final plans: GPU scan + GPU compute (+ no CPU core operators for complex)",
    G5: "complex suite: every verified_gc cleanup barrier passed",
    GO: "controls and linkage: inputs, cpus, executed order, contract fields",
}
NON_FAILING = ("PASS", "INFO", "SKIP", "XFAIL", "COVERAGE")  # statuses that count as evaluated-and-fine

# Locked design (section 3, 5, 8): used by --formal and by the executed-order check (--round).
CAMPAIGN_SEED = 20261029
ROUND_ROWS = ("ABDC", "BCAD", "CDBA", "DACB")  # Williams square, n = 4 (section 5)
FORMAL = {
    "engines": ALL_ENGINES, "runs": 3, "cpus": 26, "spark_gpu_profile": "a100-parity",
    "polars_gpu_executor": "streaming", "expect_gpu_unsupported": ("cx_q13", "cx_window"),
    "scales": {"baseline": ("1", "10", "30", "100", "300"), "complex": ("30", "100", "300")},
}


def campaign_letters() -> dict[str, str]:
    """A..D -> engine, from the section 5 seeded shuffle."""
    order = list(ALL_ENGINES)
    random.Random(CAMPAIGN_SEED).shuffle(order)
    return dict(zip("ABCD", order))


def round_engine_order(round_number: int) -> list[str]:
    letters = campaign_letters()
    return [letters[c] for c in ROUND_ROWS[round_number - 1]]


def applicable_gates(suite: str, engine: str) -> list[str]:
    gates = [G1, G2]
    if engine == "polars_gpu":
        gates.append(G3)
    if engine == "spark_gpu":
        gates.append(G4)
    if suite == "complex":
        gates.append(G5)
    return gates


class Problems:
    """Problem texts tagged with the gate(s) they violate."""

    def __init__(self):
        self.items: list[tuple[tuple[str, ...], str]] = []

    def add(self, text: str, gates=GO) -> None:
        self.items.append(((gates,) if isinstance(gates, str) else tuple(gates), text))

    def __bool__(self) -> bool:
        return bool(self.items)

    def texts(self) -> list[str]:
        return [text for _, text in self.items]

    def gates(self) -> list[str]:
        return sorted({gate for gates, _ in self.items for gate in gates})


class Checks:
    def __init__(self, quiet: bool = False):
        self.items: list[dict] = []
        self.quiet = quiet

    def add(self, check_id: str, status: str, detail: str, gates=(), session: dict | None = None, passed_gates=(), **data) -> None:
        assert status in STATUSES, status
        item = {"id": check_id, "status": status, "detail": detail}
        if status in ("COVERAGE", "XFAIL"):
            item["kind"] = "coverage"  # amendment A1: reported under RQ4, not a failure
        elif status == "FAIL":
            item["kind"] = "integrity"
        if gates:
            item["gates"] = list(gates)
        if passed_gates:  # a failing check can still be passing evidence for the gates it also covers
            item["passed_gates"] = list(passed_gates)
        if session:
            item["session"] = dict(session)
        item.update(data)
        self.items.append(item)
        if not self.quiet:
            print(f"[{status:8s}] {check_id}: {detail}", flush=True)

    def counts(self) -> dict:
        return {s: sum(1 for c in self.items if c["status"] == s) for s in STATUSES}

    def failed(self, strict: bool = False) -> bool:
        counts = self.counts()
        return counts["FAIL"] > 0 or (strict and counts["WARN"] > 0)

    def summarize(self, check_id: str, problems, ok_detail: str, limit: int = 6, gates=(), session: dict | None = None) -> None:
        texts = problems.texts() if isinstance(problems, Problems) else list(problems)
        if texts:
            more = f" (+{len(texts) - limit} more)" if len(texts) > limit else ""
            fail_gates = problems.gates() if isinstance(problems, Problems) else list(gates)
            self.add(check_id, "FAIL", f"{len(texts)} problem(s): " + "; ".join(texts[:limit]) + more,
                     gates=fail_gates, session=session, passed_gates=[g for g in gates if g not in fail_gates], problems=texts)
        else:
            self.add(check_id, "PASS", ok_detail, gates=gates, session=session)


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


def is_aarch64(machine: str | None = None) -> bool:
    return (machine or platform.machine()).lower() in ("aarch64", "arm64")


def jar_relative(machine: str | None = None) -> str:
    """The RAPIDS Spark jar of this CPU architecture (default: the host's)."""
    return JAR_RELATIVE_AARCH64 if is_aarch64(machine) else JAR_RELATIVE


def expected_jar_sha256(checksum_text: str, machine: str | None = None) -> tuple[str | None, str | None]:
    """(expected sha256, problem) for the host's jar, given the text of evidence/DEPENDENCY_SHA256.txt.

    x86_64: the file's entry for the jar (None when absent), exactly as before. aarch64: the sha256 recorded in this
    module; an entry for the arm64 jar in the file is honoured but must agree with it (a disagreement is the problem)."""
    name = Path(jar_relative(machine)).name
    listed = None
    for line in checksum_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].endswith(name):
            listed = parts[0]
    if not is_aarch64(machine):
        return listed, None
    if listed is not None and listed != JAR_SHA256_AARCH64:
        return None, (f"evidence/DEPENDENCY_SHA256.txt lists {listed} for {name} "
                      f"but preflight.py records {JAR_SHA256_AARCH64}")
    return JAR_SHA256_AARCH64, None


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
            elif isinstance(left, int) or isinstance(right, int):
                # an integer on either side is exact: 600037902 vs 600037902.5 must not pass on rel 1e-8
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
    pin_set, pins = resolve_pin_set(args.pins, engines)
    problems = []
    for name, wanted in sorted(pins.items()):
        have = _installed(name)
        if have is None:
            problems.append(f"{name}: not installed (want {wanted})")
        elif have != wanted:
            problems.append(f"{name}: installed {have}, want {wanted}")
    checks.summarize("env.package_pins", problems,
                     f"{len(pins)} pins of the {pin_set} set ({PIN_SET_FILES[pin_set]}) match the installed packages")
    check_cpu_energy_and_pinning(checks, args)
    check_membind(checks, args)

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
        expected, problem = expected_jar_sha256((HERE / "evidence" / "DEPENDENCY_SHA256.txt").read_text())
        if not jar.is_file():
            checks.add("env.jar", "FAIL", f"{jar} not found")
        elif problem:
            checks.add("env.jar", "FAIL", problem)
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


PIN_SET_FILES = {"lock": "requirements.lock.txt, the Polars 2.0.0 environment without cudf-polars",
                 "lock+cpu": "requirements.lock.txt minus its Polars pins + requirements-cpu.txt, Polars 1.44.2 without cudf-polars",
                 "lock+gpu": "requirements.lock.txt minus its Polars pins + requirements-gpu.txt, Polars 1.44.2 with cudf-polars"}


def resolve_pin_set(choice: str, engines, installed=None) -> tuple[str, dict]:
    """(name, pins): the package pins this host must have, one of PIN_SETS.

    lock      evidence/requirements.lock.txt exactly (Polars 2.0.0; the a100compat environment, CPU engines only)
    lock+cpu  the lock with its Polars pins replaced by requirements-cpu.txt (Polars 1.44.2, no cudf-polars: CPU-only hosts)
    lock+gpu  the lock with its Polars pins replaced by requirements-gpu.txt (Polars 1.44.2 + cudf-polars)
    auto      lock+gpu when polars_gpu is selected or cudf-polars is installed; otherwise lock+cpu when the installed Polars
              is the one requirements-cpu.txt pins (and not the lock's); otherwise lock. An environment that fits none of the
              sets then fails the pin check against the lock, naming each package."""
    installed = installed or _installed
    lock = parse_pins(HERE / "evidence" / "requirements.lock.txt")
    cpu_file = HERE / "requirements-cpu.txt"
    cpu = parse_pins(cpu_file) if cpu_file.is_file() else {}
    if choice == "auto":
        if "polars_gpu" in engines or installed("cudf-polars-cu12") or installed("cudf-polars-cu13"):
            choice = "lock+gpu"
        elif cpu.get("polars") and installed("polars") == cpu["polars"] != lock.get("polars"):
            choice = "lock+cpu"
        else:
            choice = "lock"
    pins = dict(lock)
    if choice == "lock+gpu":
        pins.update(parse_pins(HERE / "requirements-gpu.txt"))
    elif choice == "lock+cpu":
        pins.update(cpu)
    return choice, pins


def check_cpu_energy_and_pinning(checks: Checks, args) -> None:
    """CPU energy counters that exist but cannot be read are a failure (never a silent 'unavailable'); then the pinning."""
    gate = power_telemetry.check_cpu_energy(args.cpu_energy)
    detail = "; ".join(gate["problems"] + gate["warnings"]) or f"{gate['zones']['total']} counters readable and advancing"
    if args.cpu_energy == "off":
        checks.add("env.cpu_energy", "SKIP", "--cpu-energy off: CPU energy is not checked")
    elif not gate["ok"]:
        checks.add("env.cpu_energy", "FAIL", detail, state=gate["state"])
    elif gate["state"] == "absent":
        checks.add("env.cpu_energy", "INFO", detail, state=gate["state"])
    else:
        checks.add("env.cpu_energy", "WARN" if gate["warnings"] else "PASS", detail, state=gate["state"])
    if args.socket is None and args.cpu_list is None:
        checks.add("env.cpu_pinning", "SKIP", "no --socket/--cpu-list: the engines may use every CPU")
        return
    try:
        pinning = power_telemetry.resolve_pinning(args.socket, args.cpu_list)
    except power_telemetry.PinningError as exc:
        checks.add("env.cpu_pinning", "FAIL", str(exc))
        return
    text = (f"CPUs {pinning['cpus']} ({pinning['cpu_count']}); sockets {pinning['sockets']}, NUMA nodes {pinning['numa_nodes']}")
    if args.cpus > pinning["cpu_count"]:
        checks.add("env.cpu_pinning", "FAIL", f"--cpus {args.cpus} engine threads on {text}: oversubscribed")
    else:
        checks.add("env.cpu_pinning", "PASS", text)


def check_membind(checks: Checks, args) -> None:
    """Can engine memory be bound to the pinned socket's NUMA nodes (set_mempolicy through ctypes, else numactl)?

    The design attributes S2-pinned DRAM energy to the pinned socket only, so a pinned run must bind memory: FAIL when it
    cannot be done (--no-membind is the recorded opt-out). On a CPU-only host without a pinning the same probe runs for the
    first socket's nodes and is only information, so a missing numactl/libnuma is known before --socket is chosen."""
    pinned = args.socket is not None or args.cpu_list is not None
    if not pinned and not args.cpu_only:
        return
    try:
        pinning = power_telemetry.resolve_pinning(args.socket, args.cpu_list) if pinned else None
        if pinning is None:
            sockets = power_telemetry.cpu_topology()["sockets"]
            pinning = {"numa_nodes": sockets[0]["numa_nodes"] if sockets else []}
    except power_telemetry.PinningError:
        checks.add("env.membind", "SKIP", "the pinning is invalid (see env.cpu_pinning)")
        return
    plan = power_telemetry.plan_membind(pinning, args.no_membind)
    if args.no_membind:
        checks.add("env.membind", "SKIP", f"--no-membind: {plan['reason']}")
        return
    if not plan["enabled"]:
        checks.add("env.membind", "INFO", plan["reason"])
        return
    probe = power_telemetry.probe_membind(plan["nodes"])
    tools = f"numactl: {probe.get('numactl') or 'not installed'}; libnuma: {probe.get('libnuma') or 'not loadable'}"
    if probe["ok"]:
        checks.add("env.membind", "PASS", f"memory can be bound to NUMA node(s) {plan['nodes']} via {probe['mechanism']} ({tools})")
    else:
        detail = (f"memory cannot be bound to NUMA node(s) {plan['nodes']}: {'; '.join(probe.get('reasons') or ['unknown'])} "
                  f"({tools}). Install numactl (apt install numactl) or libnuma; --no-membind runs without binding (recorded)")
        checks.add("env.membind", "FAIL" if pinned else "INFO", detail)


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
               "--output", str(run_dir), "--scales", *args.suite_scales[suite], "--engines", *engines, "--runs", str(args.runs),
               "--cpus", str(args.cpus), "--driver-memory", args.driver_memory, "--jar", str(args.jar),
               "--keep-going", "--engine-timeout-seconds", str(args.engine_timeout_seconds)]
    if suite == "complex":
        command += ["--query-cleanup", "verified_gc", "--cleanup-timeout-seconds", "60"]
    if "polars_gpu" in engines:
        command += ["--polars-gpu-executor", args.polars_gpu_executor]
        if args.polars_gpu_executor_options:
            command += ["--polars-gpu-executor-options", args.polars_gpu_executor_options]
    # The smoke run is a correctness check, not a measurement: no idle window per session (30 s each by default), but the
    # same CPU pinning and CPU-energy rule as the campaign, so a host that cannot run the campaign fails here.
    command += ["--idle-baseline-seconds", "0", "--cpu-energy", args.cpu_energy]
    if args.socket is not None:
        command += ["--socket", str(args.socket)]
    if args.cpu_list is not None:
        command += ["--cpu-list", args.cpu_list]
    if args.no_membind:
        command.append("--no-membind")
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


# ---- error classification (design amendment A1) -----------------------------------------------------
#
# Coverage outcome (reported under RQ4, never fails a round): a GPU engine rejects a query because an
# operation is unsupported, or any engine runs out of device/host memory. Integrity failure (fails the
# round): silent CPU fallback, engine timeout, failed cleanup barrier, failed session guard, and any error
# no rule matches. Rules are evaluated in order; the first match wins; the table is written to the report.

class ErrorRule:
    def __init__(self, rule_id: str, outcome: str, klass: str, engines, type_pattern: str | None,
                 message_pattern: str | None, doc: str):
        self.id, self.outcome, self.klass, self.engines, self.doc = rule_id, outcome, klass, tuple(engines), doc
        self.type_pattern, self.message_pattern = type_pattern, message_pattern
        self.type_re = re.compile(type_pattern) if type_pattern else None
        self.message_re = re.compile(message_pattern, re.I | re.S) if message_pattern else None

    def matches(self, engine: str, type_name, message) -> bool:
        if engine not in self.engines:
            return False
        return bool((self.type_re and self.type_re.search(str(type_name or "")))
                    or (self.message_re and self.message_re.search(str(message or ""))))

    def describe(self) -> dict:
        return {"id": self.id, "outcome": self.outcome, "class": self.klass, "engines": list(self.engines),
                "type_pattern": self.type_pattern, "message_pattern": self.message_pattern, "doc": self.doc}


RESOURCE_MESSAGE = (r"out[ _-]?of[ _-]?(?:device |host |gpu )?memory|\bOutOfMemory(?:Error)?\b|\bOOM\b|bad_alloc|"
                    r"\bMemoryError\b|cannot allocate memory|cudaErrorMemoryAllocation|CUDA_ERROR_OUT_OF_MEMORY|"
                    r"\brmm::|\bRMM\b.{0,80}(?:alloc|memory)|failed to allocate|unable to allocate|\ballocation (?:failed|failure)\b|RetryOOM|GpuOOM|"
                    r"Java heap space|GC overhead limit exceeded")
UNSUPPORTED_POLARS_GPU = (r"\bnot (?:yet )?(?:implemented|supported)\b|\bunsupported\b|\binline (?:regex )?flags?\b|"
                          r"\bregex\b.{0,100}\b(?:invalid|unsupported|not supported)\b")
UNSUPPORTED_SPARK_GPU = (r"(?:gpu|rapids|cudf).{0,160}\b(?:not (?:yet )?(?:implemented|supported)|unsupported)\b|"
                         r"\b(?:not (?:yet )?(?:implemented|supported)|unsupported)\b.{0,160}(?:gpu|rapids|cudf)")
SILENT_FALLBACK = (r"fall(?:ing)? back to (?:the )?(?:cpu|polars)|Missing GPU scan|did not raise with raise_on_fail|"
                   r"cpu_fallback|session guard")

ERROR_RULES = (
    ErrorRule("I1-silent-cpu-fallback", "integrity", "silent_cpu_fallback", ALL_ENGINES, None, SILENT_FALLBACK,
              "evidence that work ran, or could run, on the CPU while labelled GPU; checked first"),
    ErrorRule("C1-resource-exhaustion", "coverage", "resource_exhaustion", ALL_ENGINES,
              r"OutOfMemory|MemoryError|RetryOOM|GpuOOM", RESOURCE_MESSAGE,
              "device or host memory exhausted (RMM, CUDA, Spark GPU retry exhaustion, JVM heap, ENOMEM)"),
    ErrorRule("C2-polars-gpu-unsupported-operation", "coverage", "unsupported_operation", ("polars_gpu",),
              r"^NotImplementedError$", UNSUPPORTED_POLARS_GPU,
              "cudf-polars cannot translate an expression under raise_on_fail=True"),
    ErrorRule("C3-spark-gpu-unsupported-operation", "coverage", "unsupported_operation", ("spark_gpu",),
              None, UNSUPPORTED_SPARK_GPU, "the RAPIDS plugin raised that an operation is not supported on the GPU"),
)


def classify_error(engine: str, type_name, message) -> dict:
    for rule in ERROR_RULES:
        if rule.matches(engine, type_name, message):
            return {"rule": rule.id, "outcome": rule.outcome, "class": rule.klass}
    return {"rule": "none", "outcome": "integrity", "class": "unclassified"}


def read_log_tail(path: Path, limit: int = 6000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - limit))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def short(text, limit: int = 160) -> str:
    return str(text).replace("\n", " ")[:limit]


# ---- one engine session -----------------------------------------------------------------------------

def warm_rows(session: dict) -> dict:
    """{query: rows of its single warmup record} (the reference side of the agreement gate)."""
    found: dict[str, list] = {}
    for record in session.get("records") or []:
        if record.get("phase") == "warmup":
            found.setdefault(record["query"], []).append(record.get("rows"))
    return {query: rows[0] for query, rows in found.items() if len(rows) == 1 and isinstance(rows[0], list) and rows[0]}


def validate_engine(checks: Checks, tag: str, suite: str, queries, engine: str, session: dict, run_dir: Path,
                    scale_dir: Path, runs: int, args, reference_rows: dict | None, schedule_item: dict | None,
                    scale: str | None = None, is_reference: bool = False, schedule_entries: int = 1) -> dict:
    """Validate one results.json against the gates. Returns {query: warmup rows}."""
    scale = scale if scale is not None else tag.rsplit("/sf", 1)[-1].replace("p", ".")
    sess = {"suite": suite, "scale": scale, "engine": engine}
    classified = args.error_policy == "classified"
    formal = bool(args.formal)
    declared = set(args.expect_gpu_unsupported) if engine == "polars_gpu" else set()
    gpu_gates = (G1, G3) if engine == "polars_gpu" else (G1,)

    def add(suffix, status, detail, gates, **data):
        checks.add(f"{tag}:{engine}:{suffix}", status, detail, gates=gates, session=sess, **data)

    def outcome_record(scope, klass, rule, error, query=None, predeclared=False):
        return {"suite": suite, "scale": scale, "engine": engine, "scope": scope, "query": query, "class": klass,
                "rule": rule, "predeclared": predeclared, "phase": error.get("phase"),
                "error_type": error.get("type"), "error_message": short(error.get("message"), 400)}

    # --- query errors, classified
    errors = session.get("query_errors") or []
    unexpected, coverage = Problems(), {}
    for error in errors:
        query = error.get("query")
        label = f"{query}/{error.get('phase')}: {error.get('type')}: {short(error.get('message'))}"
        if classified:
            verdict = classify_error(engine, error.get("type"), error.get("message"))
            if verdict["outcome"] == "coverage":
                coverage.setdefault(query, {**verdict, "error": error})
            else:
                unexpected.add(f"{label} [{verdict['class']}]", gpu_gates)
        elif query in declared:
            coverage.setdefault(query, {"rule": "expect-gpu-unsupported", "outcome": "coverage",
                                        "class": "unsupported_operation", "error": error})
        else:
            unexpected.add(label, gpu_gates)

    # --- run / session health (G1)
    problems = Problems()
    timeout = session_coverage = session_outcome = None
    nonzero = False
    if schedule_item is None:
        problems.add("engine missing from schedule.json", G1)
    else:
        code = schedule_item.get("returncode")
        if schedule_item.get("failure_reason") == "engine_timeout" or code == 124:
            timeout = True
            problems.add(f"engine timeout (returncode {code}, failure_reason {schedule_item.get('failure_reason')!r})", G1)
        elif schedule_item.get("failure_reason") == "gpu_not_idle":
            timeout = True  # never classified as coverage: the session did not run at all
            problems.add("session not launched: the GPU was not idle (section 6 control)", G1)
        elif schedule_item.get("status") != "complete" or code != 0:
            nonzero = True
        gate = schedule_item.get("gpu_idle_gate")
        if formal and (not isinstance(gate, dict) or gate.get("idle") is not True):
            problems.add(f"no passing gpu_idle_gate recorded for the session (section 6 control): {gate!r}")
    if schedule_entries > 1:
        problems.add(f"{schedule_entries} schedule.json entries for this session (a repeat belongs in a new round directory)", G1)
    failure, cleanup_failure = session.get("failure"), session.get("cleanup_failure")
    if cleanup_failure:
        problems.add(f"cleanup_failure: {short(json.dumps(cleanup_failure, default=str), 300)}", (G5, G1))
    if failure:
        text = f"failure: {short(json.dumps(failure, default=str), 300)}"
        if failure.get("stage") == "polars_gpu_session_guard":
            problems.add(text, (G3, G1))
        elif classified and not timeout and not cleanup_failure:
            verdict = classify_error(engine, failure.get("type"), failure.get("message"))
            if verdict["outcome"] == "coverage":
                session_coverage = {**verdict, "error": failure}
            else:
                problems.add(f"{text} [{verdict['class']}]", gpu_gates)
        else:
            problems.add(text, gpu_gates)
        if schedule_item is not None and not nonzero and not timeout:
            problems.add("results.json records a session failure but the schedule says the process exited 0", G1)
    elif nonzero:
        status, code = (schedule_item or {}).get("status"), (schedule_item or {}).get("returncode")
        verdict = None
        if classified and not timeout:
            tail = read_log_tail(scale_dir / f"{engine}.log")
            verdict = classify_error(engine, "", tail)
        if verdict and verdict["outcome"] == "coverage":
            session_coverage = {**verdict, "error": {"type": "ProcessExit", "message": f"returncode {code}; log tail: {short(tail[-300:], 300)}"}}
        else:
            hint = " (SIGKILL: host OOM killer? check dmesg; unclassified)" if code in (-9, 137) else ""
            problems.add(f"schedule status {status!r} returncode {code}, no classifiable failure{hint}", G1)
    if not session.get("completed_at_utc") and not (timeout or session_coverage or problems):
        problems.add("completed_at_utc missing (session did not finish)", G1)
    if session.get("completed_at_utc") and session_coverage:
        problems.add("session reports completion but also a coverage-class session failure", G1)
    state = session.get("query_execution_status")
    if state not in (None, "complete", "partial"):
        problems.add(f"query_execution_status {state!r}", G1)
    elif state == "partial" and not errors:
        problems.add("query_execution_status 'partial' without any query_errors", G1)
    elif state == "complete" and errors:
        problems.add(f"query_execution_status 'complete' with {len(errors)} query_errors", G1)
    if session.get("completed_at_utc") and session.get("active_query"):
        problems.add(f"completed session still has active_query {session['active_query']}", G1)
    if session.get("engine") != engine:
        problems.add(f"results.json engine is {session.get('engine')!r}", G1)
    if session.get("runs") != runs:
        problems.add(f"runs {session.get('runs')} != {runs}", G1)
    if session.get("suite") not in (None, suite) or (formal and session.get("suite") != suite):
        problems.add(f"results.json suite is {session.get('suite')!r}, expected {suite!r}", G1)
    module = importlib.import_module(SUITE_MODULES[suite])
    expected_version = getattr(module, "SUITE_VERSION", "baseline-v1")
    if session.get("suite_version") not in (None, expected_version) or (formal and session.get("suite_version") != expected_version):
        problems.add(f"suite_version {session.get('suite_version')!r}, expected {expected_version!r}", G1)
    if session.get("scale_factor") is None and formal:
        problems.add("scale_factor missing", G1)
    elif session.get("scale_factor") is not None:
        try:
            if float(session["scale_factor"]) != float(scale):
                problems.add(f"results.json scale_factor {session['scale_factor']} != directory scale {scale}", G1)
        except (TypeError, ValueError):
            problems.add(f"scale_factor {session['scale_factor']!r} is not numeric", G1)
    if session.get("queries") is not None and list(session["queries"]) != list(queries):
        problems.add(f"queries {session['queries']} != suite queries {list(queries)}", G1)
    if args.expect_cpus is not None and session.get("cpu_threads") != args.expect_cpus:
        problems.add(f"cpu_threads {session.get('cpu_threads')!r} != --cpus {args.expect_cpus} (section 6 control)")
    if args.expect_affinity is not None:
        reported = (session.get("cpu_affinity") or {}).get("cpus") if isinstance(session.get("cpu_affinity"), dict) else None
        try:
            same = reported is not None and power_telemetry.parse_cpu_list(reported) == power_telemetry.parse_cpu_list(args.expect_affinity)
        except ValueError:
            same = False
        if not same:
            problems.add(f"cpu_affinity {reported!r} != the campaign's pinning {args.expect_affinity!r}: the session did not "
                         "run on the CPUs it was pinned to")
    if problems:
        checks.summarize(f"{tag}:{engine}:session", problems, "", gates=(G1,), session=sess)
    elif session_coverage:
        session_outcome = outcome_record("session", session_coverage["class"], session_coverage["rule"], session_coverage["error"])
        add("session", "COVERAGE",
            f"session did not complete: {session_coverage['class']} ({session_coverage['rule']}): "
            f"{short(session_coverage['error'].get('message'), 200)}", (G1,), outcome=session_outcome)
    else:
        add("session", "PASS", "completed, no session failure", (G1,))

    # --- query errors verdict + per-query coverage outcomes
    ok_detail = "no unexpected query errors" if not coverage else f"no unclassified query errors ({len(coverage)} coverage outcome(s))"
    checks.summarize(f"{tag}:{engine}:query_errors", unexpected, ok_detail, gates=gpu_gates, session=sess)
    for query, found in sorted(coverage.items()):
        predeclared = query in declared
        error = found["error"]
        record = outcome_record("query", found["class"], found["rule"], error, query, predeclared)
        message = f"{found['class']} [{found['rule']}] {error.get('type')}: {short(error.get('message'), 200)}"
        if predeclared and found["class"] == "unsupported_operation":
            add(query, "XFAIL", f"expected unsupported on polars_gpu: {message}", (G3,), outcome=record)
        else:
            add(query, "COVERAGE", message + ("" if predeclared else "; not pre-declared in section 8"), gpu_gates, outcome=record)
            why = ("declared as unsupported but failed for another reason" if predeclared
                   else "coverage outcome that section 8 did not pre-declare")
            add(f"{query}:coverage_note", "WARN", f"{why}: {found['class']} (reported under RQ4; not a gate failure)", (), outcome=record)
    excluded = set(coverage)

    # --- records
    records = session.get("records") or []
    by_query: dict[str, list[dict]] = {}
    for record in records:
        by_query.setdefault(record["query"], []).append(record)
    problems = Problems()
    rows_by_query, not_completed = {}, []
    for query in queries:
        recs = by_query.get(query, [])
        warm = [r for r in recs if r.get("phase") == "warmup"]
        measured = [r for r in recs if r.get("phase") == "measured"]
        complete = len(warm) == 1 and len(measured) == runs
        if query in declared and query not in excluded and recs:
            add(query, "WARN", "listed as expected-unsupported but it ran (XPASS): drop it from --expect-gpu-unsupported", ())
        for record in recs:
            seconds = record.get("seconds")
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
                problems.add(f"{query}/{record.get('phase')}{record.get('trial')}: bad seconds {seconds!r}", G1)
            if not isinstance(record.get("rows"), list) or not record["rows"]:
                problems.add(f"{query}/{record.get('phase')}{record.get('trial')}: empty or missing result rows", G1)
        if query in excluded:
            continue
        if not complete:
            if session_coverage:
                not_completed.append(query)
            else:
                problems.add(f"{query}: {len(warm)} warmup / {len(measured)} measured records (want 1 / {runs})", G1)
            continue
        if sorted(r.get("trial") for r in measured) != list(range(1, runs + 1)):
            problems.add(f"{query}: measured trials {sorted(r.get('trial') for r in measured)} != 1..{runs}", G1)
        rows_by_query[query] = warm[0].get("rows")
    unknown = sorted(set(by_query) - set(queries))
    if unknown:
        problems.add(f"records for unknown queries {unknown}", G1)
    if session_coverage and not_completed:
        excluded |= set(not_completed)
        session_outcome["queries_not_completed"] = not_completed
        add("session_queries", "COVERAGE", f"{len(not_completed)} queries not completed after the session failure: {not_completed}", (G1,))
    checks.summarize(f"{tag}:{engine}:records", problems,
                     f"{len(queries) - len(excluded)} queries x (1 warmup + {runs} measured), finite times, non-empty results",
                     gates=(G1,), session=sess)

    # --- engine-specific contract
    problems, notes = Problems(), []
    expected_engine = {"polars": "streaming", "polars_gpu": "gpu"}.get(engine)
    if session.get("polars_execution_engine") != expected_engine:
        problems.add(f"polars_execution_engine {session.get('polars_execution_engine')!r} != {expected_engine!r}")
    guard = {}
    if engine == "polars_gpu":
        gpu = session.get("polars_gpu")
        if not isinstance(gpu, dict):
            problems.add("polars_gpu metadata object missing", G3)
        else:
            missing = [k for k in POLARS_GPU_REQUIRED if k not in gpu]
            if missing:
                problems.add(f"polars_gpu metadata missing keys {missing}", G3)
            if gpu.get("raise_on_fail") is not True:
                problems.add(f"raise_on_fail is {gpu.get('raise_on_fail')!r}, must be true", G3)
            if gpu.get("executor") != args.polars_gpu_executor:
                problems.add(f"executor {gpu.get('executor')!r} != requested {args.polars_gpu_executor!r}", G3)
            if not isinstance(gpu.get("device"), int):
                problems.add("device is not an int", G3)
            if not gpu.get("cudf_polars_version"):
                problems.add("cudf_polars_version empty", G3)
            guard = gpu.get("session_guard") or {}
            notes.append(f"executor={gpu.get('executor')} cudf-polars={gpu.get('cudf_polars_version')} raise_on_fail=true")
    elif "polars_gpu" in session:
        problems.add("polars_gpu metadata present on a non-polars_gpu engine")
    if engine == "spark_gpu":
        if session.get("spark_gpu_profile") not in ("a100-parity", "h100-tuned"):
            problems.add(f"spark_gpu_profile {session.get('spark_gpu_profile')!r}", G4)
        if args.spark_gpu_profile and session.get("spark_gpu_profile") != args.spark_gpu_profile:
            problems.add(f"spark_gpu_profile {session.get('spark_gpu_profile')!r} != {args.spark_gpu_profile!r}", G4)
        if "com.nvidia.spark.SQLPlugin" not in (session.get("spark_conf") or {}).get("spark.plugins", ""):
            problems.add("spark.plugins does not include com.nvidia.spark.SQLPlugin", G4)
    elif "spark_gpu_profile" in session and session["spark_gpu_profile"] is not None:
        problems.add("spark_gpu_profile present on a non-spark_gpu engine")
    if engine in GPU_ENGINES:
        inventory = session.get("gpu_inventory")
        if not isinstance(inventory, dict) or not inventory.get("name"):
            problems.add(f"gpu_inventory missing or has no device name: {inventory}")
        elif "H100" not in inventory["name"] and not args.allow_any_gpu:
            problems.add(f"gpu_inventory device {inventory['name']!r} is not an H100")
    elif "gpu_inventory" in session:
        problems.add("gpu_inventory present on a CPU engine")
    expected_class = {"polars": "cpu_engine", "spark_cpu": "cpu_engine", "polars_gpu": "gpu_engine_strict"}.get(engine)
    class_gate = {"polars_gpu": G3, "spark_gpu": G4}.get(engine, GO)
    for record in records:
        cls, label = record.get("gpu_execution_class"), f"{record['query']}/{record['phase']}{record['trial']}"
        if engine == "spark_gpu":
            if cls not in ("hybrid", "gpu_operators_present"):
                problems.add(f"{label}: gpu_execution_class {cls!r}", G4)
        elif cls != expected_class:
            problems.add(f"{label}: gpu_execution_class {cls!r} != {expected_class!r}", class_gate)
        if (engine in GPU_ENGINES) != ("peak_gpu_memory_bytes" in record):
            problems.add(f"{label}: peak_gpu_memory_bytes presence wrong for {engine}")
    if suite == "complex":
        cleanup = session.get("cleanup_records") or []
        failed_cleanup = session.get("failed_query_cleanup_records") or []
        if session.get("query_cleanup_policy") != "verified_gc":
            problems.add(f"query_cleanup_policy {session.get('query_cleanup_policy')!r}, complex runs need 'verified_gc'", G5)
        if len(cleanup) != len(records):
            if not session_coverage:
                problems.add(f"{len(cleanup)} cleanup_records for {len(records)} query records", G5)
        elif cleanup and all("phase" in c and "trial" in c for c in cleanup):
            have = sorted((c.get("query"), c.get("phase"), c.get("trial")) for c in cleanup)
            want = sorted((r["query"], r["phase"], r["trial"]) for r in records)
            if have != want:
                problems.add("cleanup_records do not pair one-to-one with the query records", G5)
        elif formal and cleanup:
            problems.add("cleanup_records carry no phase/trial; cannot pair barriers with actions", G5)
        not_passed = [c.get("query") for c in cleanup + failed_cleanup if c.get("status") != "passed"]
        if not_passed:
            problems.add(f"cleanup barrier did not pass for {not_passed[:5]}", G5)
        if formal and len(failed_cleanup) != len(errors):
            problems.add(f"{len(failed_cleanup)} cleanup barriers after {len(errors)} failed actions (each must run one)", G5)
        if not problems:
            notes.append(f"{len(cleanup) + len(failed_cleanup)} verified_gc barriers passed")
    else:
        if formal and session.get("query_cleanup_policy") != "none":
            problems.add(f"query_cleanup_policy {session.get('query_cleanup_policy')!r}, baseline runs need 'none'")
    contract_gates = tuple(g for g in (G3 if engine == "polars_gpu" else None, G4 if engine == "spark_gpu" else None,
                                       G5 if suite == "complex" else None, GO) if g)
    checks.summarize(f"{tag}:{engine}:contract", problems, "; ".join(n for n in notes if n) or "interface-contract fields present and consistent",
                     gates=contract_gates, session=sess)

    # --- polars_gpu strict session guard (G3)
    if engine == "polars_gpu" and isinstance(session.get("polars_gpu"), dict):
        problems = Problems()
        if not guard.get("unsupported_canary_error"):
            problems.add("the session guard recorded no unsupported-operation canary error: strictness was never demonstrated", G3)
        config = guard.get("effective_config")
        if isinstance(config, dict):
            # worker.json_safe flattens cudf-polars' ConfigOptions; only an explicit contradiction is a finding
            # (the worker already refused to start on a mismatch, this is the second look at the record).
            if "raise_on_fail" in config and config["raise_on_fail"] is not True:
                problems.add(f"cudf-polars effective_config raise_on_fail is {config['raise_on_fail']!r}", G3)
            executor = config.get("executor")
            named = executor.get("name") if isinstance(executor, dict) else executor
            other = {"streaming": "in-memory", "in-memory": "streaming"}.get(args.polars_gpu_executor)
            if isinstance(named, str) and named.strip().lower().replace("_", "-") == other:
                problems.add(f"cudf-polars effective_config executor {named!r}, requested {args.polars_gpu_executor!r}", G3)
        elif config is None and "effective_config_error" not in guard and formal:
            problems.add("session guard recorded neither effective_config nor effective_config_error", G3)
        checks.summarize(f"{tag}:{engine}:strict_guard", problems,
                         "canary raised under raise_on_fail=True; cudf-polars resolved the requested executor", gates=(G3,), session=sess)
        if "effective_config_error" in guard:
            add("strict_guard_config", "WARN", f"cudf-polars effective config unavailable: {short(guard['effective_config_error'])}", (G3,))

    # --- plans
    problems = Problems()
    plan_gate = G4 if engine == "spark_gpu" else GO
    for record in records:
        label = f"{record['query']}/{record['phase']}{record['trial']}"
        plan_path = scale_dir / engine / record.get("plan", "")
        if not record.get("plan") or not plan_path.is_file():
            problems.add(f"{label}: plan file missing", plan_gate)
            continue
        text = plan_path.read_text()
        if engine == "spark_gpu":
            if record.get("gpu_plan_observed") is not True:
                problems.add(f"{record['query']}: gpu_plan_observed false", G4)
            for problem in gpu_plan_problems(text, suite):
                problems.add(f"{label}: {problem}", G4)
        elif engine == "spark_cpu" and re.search(r"\bGpu[A-Z]", text):
            problems.add(f"{record['query']}: GPU operator in a CPU Spark plan")
    checks.summarize(f"{tag}:{engine}:plans", _limit(problems, 50),
                     {"spark_gpu": "every plan has GPU scan and aggregate/join operators"
                                   if suite == "baseline" else "every plan has a GPU scan, a GPU compute operator and no CPU core operator",
                      "spark_cpu": "no GPU operators in CPU Spark plans"}.get(engine, "plan files present"),
                     gates=(plan_gate,), session=sess)

    # --- GPU memory and polars_gpu warnings
    if engine in GPU_ENGINES:
        total = (session.get("gpu_inventory") or {}).get("memory_total_bytes")
        peaks = [r.get("peak_gpu_memory_bytes") for r in records]
        nulls = sum(1 for p in peaks if p is None)
        too_big = [p for p in peaks if isinstance(p, int) and total and p > total]
        if too_big:
            add("gpu_memory", "FAIL", f"peak {max(too_big)} exceeds device memory {total}", (GO,))
        elif peaks and nulls == len(peaks):
            add("gpu_memory", "WARN", "peak_gpu_memory_bytes is null for every record (sampling unavailable)", ())
        elif nulls:
            add("gpu_memory", "WARN", f"peak_gpu_memory_bytes null for {nulls}/{len(peaks)} records", ())
        else:
            add("gpu_memory", "PASS",
                f"peak device memory recorded for all {len(peaks)} records (max {max(peaks) / 2**20:.0f} MiB)" if peaks else "no records", (GO,))
    if engine == "polars_gpu":
        cpu_fallback, single_partition = [], []
        for record in records:
            for warning in record.get("gpu_engine_warnings") or []:
                (cpu_fallback if re.search(r"\bCPU\b|polars (cpu )?engine", warning, re.I) else single_partition).append(
                    f"{record['query']}: {warning[:160]}")
        if cpu_fallback:
            add("fallback_warnings", "FAIL", "warnings that mention CPU fallback: " + "; ".join(sorted(set(cpu_fallback))[:3]), (G3,))
        elif single_partition:
            add("fallback_warnings", "WARN", "cudf-polars warnings (streaming fallback, still GPU): " + "; ".join(sorted(set(single_partition))[:3]), (G3,))
        else:
            add("fallback_warnings", "PASS", "no cudf-polars warnings recorded", (G3,))

    # --- agreement with the reference engine (G2)
    if reference_rows is not None:
        problems = Problems()
        compared = 0
        for query in queries:
            if query not in by_query:
                continue
            if query not in reference_rows:
                problems.add(f"{query}: no usable reference result to compare with", G2)
                continue
            for record in by_query[query]:
                result = compare_values(reference_rows[query], record.get("rows"))
                compared += 1
                if not result["passed"]:
                    first = result["differences"][0] if result["differences"] else {}
                    problems.add(f"{query}/{record['phase']}{record['trial']}: {result['mismatch_count']} mismatch(es), first {first}", G2)
        suffix = "self_agreement" if is_reference else "agreement"
        if not compared and not problems and not records and len(excluded) == len(queries):
            add(suffix, "COVERAGE", "no outputs exist to compare: every query is a coverage outcome of this session", (G2,))
            return rows_by_query
        if not compared and not problems:
            problems.add("no record could be compared with the reference engine", G2)
        checks.summarize(f"{tag}:{engine}:{'self_agreement' if is_reference else 'agreement'}", _limit(problems, 20),
                         f"all {compared} records match the reference engine within rel {REL_TOL:g} / abs {ABS_TOL:g} (integers exact)"
                         if not is_reference else "reference engine is self-consistent across records",
                         gates=(G2,), session=sess)
    return rows_by_query


def _limit(problems: Problems, count: int) -> Problems:
    limited = Problems()
    limited.items = problems.items[:count]
    return limited


# ---- one (suite, scale) ---------------------------------------------------------------------------

REFERENCE_FALLBACK = ("spark_cpu", "polars_gpu", "spark_gpu")


def scale_key(scale) -> str:
    return "sf" + str(scale).replace(".", "p")


def read_schedule(path: Path) -> list[dict]:
    data = read_json(path)
    if not isinstance(data, list):
        raise ValueError(f"{path} is not a list")
    return data


def validate_suite(checks: Checks, suite: str, run_dir: Path, scale: str, engines, runs: int, args) -> None:
    tag = f"{suite}/sf{scale.replace('.', 'p')}"
    module = importlib.import_module(SUITE_MODULES[suite])
    queries = list(module.QUERY_IDS)
    schedule_path = run_dir / "schedule.json"
    if not schedule_path.is_file():
        checks.add(f"{tag}:schedule", "FAIL", f"{schedule_path} missing", gates=(G1,))
        return
    try:
        schedule_items = read_schedule(schedule_path)
    except (ValueError, OSError) as error:
        checks.add(f"{tag}:schedule", "FAIL", f"{schedule_path} unreadable: {error}", gates=(G1,))
        return
    entries: dict[tuple, list] = {}
    for item in schedule_items:
        entries.setdefault((str(item.get("scale")), item.get("engine")), []).append(item)
    scale_dir = run_dir / scale_key(scale)
    sessions, problems = {}, Problems()
    for engine in engines:
        path = scale_dir / engine / "results.json"
        if not path.is_file():
            problems.add(f"{engine}: results.json missing", G1)
            continue
        try:
            sessions[engine] = read_json(path)
        except ValueError as error:
            problems.add(f"{engine}: results.json unreadable: {error}", G1)
    checks.summarize(f"{tag}:results_present", problems, f"results.json for {', '.join(engines)}", gates=(G1,))

    # --- the inputs every engine read
    manifests = {s.get("manifest_sha256") for s in sessions.values()}
    problems = Problems()
    if len(manifests) > 1:
        problems.add(f"engines read different manifests: {sorted(map(str, manifests))}", G2)
    copied = scale_dir / "manifest.json"
    only = next(iter(manifests), None)
    if copied.is_file() and len(manifests) == 1 and sha256_file(copied) != only:
        problems.add(f"{copied} sha256 differs from the manifest the engines read ({only})", G2)
    verified = getattr(args, "input_manifest_sha256", None)
    if verified is not None and len(manifests) == 1:
        want = verified.get(float(scale))
        if want is None:
            problems.add(f"scale {scale} is not covered by --inputs-report", GO)
        elif want != only:
            problems.add(f"engines read manifest {only}, but verify_generated_inputs verified {want}", GO)
    checks.summarize(f"{tag}:same_input", problems,
                     f"all engines read manifest {only}" + (" (matches the verified inputs report)" if verified is not None else ""),
                     gates=(G2, GO))
    if len(manifests) == 1 and only:
        checks.items[-1]["manifest_sha256"] = only  # lets main() compare suites and rounds
    versions = {e: sessions[e].get("version") for e in ("polars", "polars_gpu") if e in sessions}
    if len(versions) == 2:
        checks.add(f"{tag}:polars_versions", "PASS" if len(set(versions.values())) == 1 else "FAIL",
                   f"polars and polars_gpu both report Polars {versions['polars']}" if len(set(versions.values())) == 1
                   else f"Polars versions differ between engines: {versions}", gates=(GO,))

    # --- reference engine: polars (section 7 gate 2); anything else only outside --formal
    wanted = args.reference_engine
    reference_engine = wanted if wanted in sessions else (
        None if args.formal else next((e for e in ("polars", "spark_cpu", "polars_gpu", "spark_gpu") if e in sessions), None))
    reference_rows = None
    if reference_engine is None and sessions:
        checks.add(f"{tag}:reference", "FAIL", f"reference engine {wanted} has no results.json; agreement cannot be established",
                   gates=(G2,))
    if reference_engine:
        reference_rows = dict(warm_rows(sessions[reference_engine]))
        detail = f"{reference_engine} is the reference for output agreement"
        if args.error_policy == "classified":
            substitutes = {}
            for query in queries:
                if query in reference_rows:
                    continue
                for other in REFERENCE_FALLBACK:
                    rows = warm_rows(sessions[other]).get(query) if other in sessions and other != reference_engine else None
                    if rows:
                        reference_rows[query], substitutes[query] = rows, other
                        break
            if substitutes:
                detail += f"; substituted per query: {substitutes}"
                checks.add(f"{tag}:reference_substitution", "WARN",
                           f"{reference_engine} has no result for {sorted(substitutes)}; compared against {substitutes}", gates=())
        checks.add(f"{tag}:reference", "INFO", detail, gates=(G2,))
    ordered = ([reference_engine] if reference_engine else []) + [e for e in engines if e in sessions and e != reference_engine]
    for engine in ordered:
        validate_engine(checks, tag, suite, queries, engine, sessions[engine], run_dir, scale_dir, runs, args,
                        reference_rows, (entries.get((str(scale), engine)) or [None])[0], scale=scale,
                        is_reference=engine == reference_engine, schedule_entries=len(entries.get((str(scale), engine)) or []))


# ---- run roots: schedule coverage and executed order ---------------------------------------------------

def check_schedule_coverage(checks: Checks, suite: str, run_dir: Path, scales, engines) -> None:
    path = run_dir / "schedule.json"
    if not path.is_file():
        return  # validate_suite already failed on it
    try:
        items = read_schedule(path)
    except (ValueError, OSError):
        return
    seen: dict[tuple, int] = {}
    for item in items:
        key = (str(item.get("scale")), item.get("engine"))
        seen[key] = seen.get(key, 0) + 1
    expected = {(str(s), e) for s in scales for e in engines}
    problems = Problems()
    for key in sorted(expected - set(seen)):
        problems.add(f"missing session sf{key[0]}/{key[1]}", G1)
    for key in sorted(set(seen) - expected):
        problems.add(f"unexpected session sf{key[0]}/{key[1]}", G1)
    for key, count in sorted(seen.items()):
        if count > 1:
            problems.add(f"session sf{key[0]}/{key[1]} scheduled {count} times", G1)
    checks.summarize(f"{suite}:schedule_coverage", problems,
                     f"schedule.json holds exactly the {len(expected)} expected sessions ({len(list(scales))} scales x {len(list(engines))} engines), each once",
                     gates=(G1,))


def parse_utc(text):
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def check_executed_order(checks: Checks, args, suites, run_dirs, scales_by_suite, engines) -> None:
    """Section 5: engine order within every (suite, scale) follows the round's row; scales ascend; suites alternate."""
    expected_engines = [e for e in round_engine_order(args.round) if e in engines]
    first_start = {}
    for suite in suites:
        path = run_dirs[suite] / "schedule.json"
        if not path.is_file():
            continue
        try:
            items = read_schedule(path)
        except (ValueError, OSError):
            continue
        executed = [(str(i.get("scale")), i.get("engine")) for i in items]
        ordered_scales = sorted({str(s) for s in scales_by_suite[suite]}, key=float)
        wanted = [(s, e) for s in ordered_scales for e in expected_engines]
        starts = [parse_utc(i.get("start_utc")) for i in items]
        problems = Problems()
        if executed != wanted:
            problems.add(f"executed {[f'sf{s}/{e}' for s, e in executed]} != section 5 round {args.round} order "
                         f"{[f'sf{s}/{e}' for s, e in wanted]}")
        seen: dict[str, int] = {}
        for item in items:  # the runner stamps the 1-based position within the scale (--engine-order)
            seen[str(item.get("scale"))] = seen.get(str(item.get("scale")), 0) + 1
            if (args.formal or "engine_order_position" in item) and item.get("engine_order_position") != seen[str(item.get("scale"))]:
                problems.add(f"sf{item.get('scale')}/{item.get('engine')}: engine_order_position "
                             f"{item.get('engine_order_position')!r}, executed {seen[str(item.get('scale'))]}th")
        if any(t is None for t in starts) or any(a > b for a, b in zip(starts, starts[1:]) if a and b):
            problems.add("start_utc missing or not monotonic in schedule.json")
        if starts and starts[0]:
            first_start[suite] = starts[0]
        checks.summarize(f"{suite}:executed_order", problems,
                         f"round {args.round}: engines {' -> '.join(expected_engines)} within each scale, scales ascending")
    if len(first_start) == 2:
        baseline_first = first_start["baseline"] < first_start["complex"]
        want = args.round % 2 == 1
        checks.add("suite_order", "PASS" if baseline_first == want else "FAIL",
                   f"round {args.round}: {'baseline' if baseline_first else 'complex'} suite ran first "
                   f"(design: {'baseline' if want else 'complex'} first)", gates=(GO,))


def load_inputs_report(checks: Checks, args) -> None:
    """Link the timed sessions to the inputs verified by verify_generated_inputs.py (same manifest bytes)."""
    args.input_manifest_sha256 = None
    if not args.inputs_report:
        return
    mapping, problems = {}, Problems()
    for path in args.inputs_report:
        try:
            report = read_json(path)
        except (OSError, ValueError) as error:
            problems.add(f"{path}: unreadable ({error})")
            continue
        if report.get("tool") != "verify_generated_inputs":
            problems.add(f"{path}: not a verify_generated_inputs report")
        if report.get("ok") is not True:
            problems.add(f"{path}: the input verification did not pass (ok={report.get('ok')!r})")
        if report.get("complete") is False:
            problems.add(f"{path}: the input verification report is marked incomplete")
        for dataset in report.get("datasets", []):
            digest = next((c.get("sha256") for c in dataset.get("checks", []) if c.get("id") == "new_manifest"), None)
            if digest is None or not dataset.get("ok"):
                problems.add(f"{path}: sf{dataset.get('scale_factor')} has no verified manifest digest")
            else:
                mapping[float(dataset["scale_factor"])] = digest
    args.input_manifest_sha256 = mapping
    checks.summarize("inputs_report", problems, f"{len(mapping)} verified dataset manifests from {len(args.inputs_report)} report(s)", gates=(GO,))


def check_input_consistency(checks: Checks, prior_reports) -> dict:
    """One dataset per scale: baseline and complex read the same manifest, and so did every earlier round."""
    digests = {c["id"].split(":")[0]: c["manifest_sha256"] for c in checks.items
               if c["id"].endswith(":same_input") and c.get("manifest_sha256")}
    by_scale: dict[str, set] = {}
    for key, digest in digests.items():
        by_scale.setdefault(key.split("/")[1], set()).add(digest)
    problems = Problems()
    for scale, found in sorted(by_scale.items()):
        if len(found) > 1:
            problems.add(f"{scale}: baseline and complex read different manifests {sorted(found)}")
    for path in prior_reports or []:
        try:
            earlier = read_json(path).get("inputs", {}).get("manifest_sha256", {})
        except (OSError, ValueError, AttributeError):
            continue  # reported by the unstable-coverage reader
        for key, digest in digests.items():
            if key in earlier and earlier[key] != digest:
                problems.add(f"{key}: manifest {digest[:12]} differs from the earlier round's {earlier[key][:12]} ({Path(path).name})")
    if digests:
        checks.summarize("input_consistency", problems, f"{len(digests)} (suite, scale) datasets: one manifest per scale"
                         + (", identical to the earlier rounds" if prior_reports else ""), gates=(GO,))
    return digests


def evidence_digests(run_dirs, suites, scales_by_suite, engines) -> dict:
    """sha256 of every file the verdict was computed from, so the verdict can be tied to exact bytes."""
    out = {}
    for suite in suites:
        root = run_dirs[suite]
        paths = [root / "schedule.json"] + [root / scale_key(scale) / engine / "results.json"
                                              for scale in scales_by_suite[suite] for engine in engines]
        for path in paths:
            if path.is_file():
                out[f"{suite}/{path.relative_to(root).as_posix()}"] = sha256_file(path)
    return out


# ---- verdict ----------------------------------------------------------------------------------------

def evaluate_gates(checks: Checks, expected_sessions, strict: bool = False) -> dict:
    """Per-gate and per-session verdicts from the tagged checks. A gate passes only when every applicable
    session has at least one passing check for it and none failing (no vacuous passes)."""
    def failing(item):
        return item["status"] == "FAIL" or (strict and item["status"] == "WARN")

    by_session: dict[tuple, dict[str, list[str]]] = {}
    gate_failures: dict[str, list[str]] = {g: [] for g in GATES}
    gate_warnings: dict[str, list[str]] = {g: [] for g in GATES}
    for item in checks.items:
        gates = item.get("gates") or ([GO] if item["status"] in ("FAIL", "WARN") else [])
        for gate in item.get("passed_gates", []):
            if item.get("session"):
                sess = item["session"]
                by_session.setdefault((sess["suite"], str(sess["scale"]), sess["engine"]), {}).setdefault(gate, []).append("PASS")
        for gate in gates:
            if failing(item):
                gate_failures[gate].append(item["id"])
            elif item["status"] == "WARN":
                gate_warnings[gate].append(item["id"])
            sess = item.get("session")
            if sess:
                key = (sess["suite"], str(sess["scale"]), sess["engine"])
                by_session.setdefault(key, {}).setdefault(gate, []).append("FAIL" if failing(item) else item["status"])
    sessions, applicable = [], {g: 0 for g in GATES}
    unverified: dict[str, list[str]] = {g: [] for g in GATES}
    for suite, scale, engine in expected_sessions:
        verdicts = {}
        for gate in applicable_gates(suite, engine):
            applicable[gate] += 1
            statuses = by_session.get((suite, str(scale), engine), {}).get(gate, [])
            if "FAIL" in statuses:
                verdicts[gate] = "fail"
            elif any(s in ("PASS", "XFAIL", "COVERAGE") for s in statuses):
                verdicts[gate] = "pass"
            else:
                verdicts[gate] = "not_evaluated"
                unverified[gate].append(f"{suite}/sf{scale}/{engine}")
        sessions.append({"suite": suite, "scale": str(scale), "engine": engine, "gates": verdicts})
    gates = {}
    for gate in GATES:
        failed_sessions = [f"{s['suite']}/sf{s['scale']}/{s['engine']}" for s in sessions if s["gates"].get(gate) == "fail"]
        if gate == GO:
            verdict = "fail" if gate_failures[gate] else "pass"
        elif not applicable[gate] and not gate_failures[gate]:
            verdict = "not_applicable"
        else:
            verdict = "fail" if (gate_failures[gate] or unverified[gate]) else "pass"
        gates[gate] = {"title": GATE_TITLES[gate], "verdict": verdict, "sessions_applicable": applicable[gate],
                       "failing_checks": gate_failures[gate], "failing_sessions": failed_sessions,
                       "sessions_not_evaluated": unverified[gate], "warnings": gate_warnings[gate]}
    return {"gates": gates, "sessions": sessions}


def coverage_outcomes(checks: Checks) -> list[dict]:
    return [c["outcome"] for c in checks.items if c.get("outcome") and c["status"] in ("XFAIL", "COVERAGE")]


def coverage_cell(outcome: dict) -> tuple:
    return (outcome["engine"], outcome["suite"], str(outcome["scale"]), outcome.get("query") or "<session>")


def detect_engines(dirs) -> list[str]:
    found = []
    for run_dir in dirs:
        schedule = run_dir / "schedule.json"
        if schedule.is_file():
            for item in read_json(schedule):
                if item["engine"] not in found:
                    found.append(item["engine"])
    return [e for e in ALL_ENGINES if e in found]


# ---- command line -------------------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=Path(os.environ.get("DATA_ROOT", "data")))
    parser.add_argument("--output", type=Path, help="new directory for baseline/, complex/, logs and the report "
                        "(default rerun/preflight-<utc>)")
    parser.add_argument("--scales", nargs="+", default=None, help="scales for both suites (default 1)")
    parser.add_argument("--baseline-scales", nargs="+", default=None, help="override --scales for the baseline suite")
    parser.add_argument("--complex-scales", nargs="+", default=None, help="override --scales for the complex suite")
    parser.add_argument("--engines", nargs="+", choices=ALL_ENGINES)
    parser.add_argument("--runs", type=int, default=None, help="measured trials per query (default 1; 3 with --formal)")
    parser.add_argument("--cpus", type=int, default=None,
                        help="engine threads of the smoke run (default: every CPU, or every pinned CPU with --socket/--cpu-list)")
    parser.add_argument("--driver-memory", default=default_driver_memory())
    parser.add_argument("--jar", type=Path, default=HERE / jar_relative())
    parser.add_argument("--suites", nargs="+", choices=list(SUITE_MODULES), default=list(SUITE_MODULES))
    parser.add_argument("--polars-gpu-executor", choices=["in-memory", "streaming"], default="streaming")
    parser.add_argument("--polars-gpu-executor-options", default=None, help="JSON object passed through to run_experiment.py")
    parser.add_argument("--spark-gpu-profile", choices=["a100-parity", "h100-tuned"], default="a100-parity")
    parser.add_argument("--engine-timeout-seconds", type=float, default=1800)
    parser.add_argument("--allow-any-gpu", action="store_true", help="A100 rehearsal: accept a non-H100 GPU")
    parser.add_argument("--expect-gpu-unsupported", nargs="*", default=[], metavar="QUERY",
                        help="queries whose polars_gpu failure is an accepted, documented gap (reported XFAIL); default none")
    parser.add_argument("--pins", choices=["auto", *PIN_SETS], default="auto",
                        help="package pin set to verify: lock (Polars 2.0.0), lock+cpu (Polars 1.44.2, requirements-cpu.txt), "
                             "lock+gpu (Polars 1.44.2 + cudf-polars); auto picks by the selected engines and installed packages")
    parser.add_argument("--cpu-only", action="store_true",
                        help="a host without a GPU: engines default to polars and spark_cpu, GPU engines are refused")
    parser.add_argument("--cpu-energy", choices=power_telemetry.CPU_ENERGY_MODES, default="auto",
                        help="auto: FAIL when RAPL/amd_energy counters exist but this user cannot read them (fix: "
                             "./setup_h100.sh --power-only --enable-rapl-read); absent counters are INFO. require: FAIL unless "
                             "a CPU energy source works. off: not checked")
    pinning = parser.add_mutually_exclusive_group()
    pinning.add_argument("--socket", type=int, metavar="N", help="check (and smoke-run with) pinning to the CPUs of socket N")
    pinning.add_argument("--cpu-list", metavar="LIST", help="check (and smoke-run with) pinning to exactly these CPUs")
    parser.add_argument("--no-membind", action="store_true",
                        help="with --socket/--cpu-list: do not require (or smoke-run with) memory binding to the pinned NUMA nodes")
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
    campaign = parser.add_argument_group("campaign mode (docs/H100_EXPERIMENT_DESIGN.md)")
    campaign.add_argument("--formal", action="store_true",
                          help="validate one finished round of the locked design: all four engines, 3 measured runs, baseline "
                               "scales 1 10 30 100 300, complex scales 30 100 300, cx_q13/cx_window pre-declared, --cpus 26, "
                               "A1 error classification; requires --skip-run and --round")
    campaign.add_argument("--round", type=int, choices=[1, 2, 3, 4],
                          help="check that the executed engine/scale/suite order matches section 5 for this round")
    campaign.add_argument("--error-policy", choices=["strict", "classified"], default=None,
                          help="strict: any query error outside --expect-gpu-unsupported fails; classified: amendment A1 "
                               "(coverage outcomes do not fail, unclassified errors do); --formal implies classified")
    campaign.add_argument("--reference-engine", choices=ALL_ENGINES, default="polars",
                          help="engine every other engine's outputs are compared with (default polars)")
    campaign.add_argument("--expect-cpus", type=int, default=None, help="require cpu_threads == N in every session (--formal: 26)")
    campaign.add_argument("--expect-affinity", metavar="LIST", default=None,
                          help="require every session's results.json cpu_affinity to equal this CPU list (a pinned campaign)")
    campaign.add_argument("--inputs-report", type=Path, nargs="+",
                          help="verify_generated_inputs.py report(s): sessions must have read exactly the verified manifests")
    campaign.add_argument("--prior-reports", type=Path, nargs="+",
                          help="preflight reports of earlier rounds: coverage outcomes that differ are reported as unstable")
    return parser


def resolve_args(parser, args) -> None:
    """Fill defaults; in --formal mode pin every parameter to the locked design (or fail with exit 2)."""
    args.expect_gpu_unsupported = list(args.expect_gpu_unsupported or [])
    if args.cpus is None:
        args.cpus = os.cpu_count() or 4
        if args.socket is not None or args.cpu_list is not None:
            try:  # a pinned smoke run uses the pinned CPUs, not every CPU of the host; an impossible pinning fails env.cpu_pinning
                args.cpus = min(args.cpus, power_telemetry.resolve_pinning(args.socket, args.cpu_list)["cpu_count"])
            except power_telemetry.PinningError:
                pass
    if args.cpu_only:
        if args.formal:
            parser.error("--cpu-only cannot be combined with --formal (the locked design runs four engines, two on a GPU)")
        args.engines = list(args.engines or CPU_ENGINES)
        gpu = [e for e in args.engines if e in GPU_ENGINES]
        if gpu:
            parser.error(f"--cpu-only: GPU engine(s) {gpu} cannot run on a host without a GPU")
    if args.no_membind and args.socket is None and args.cpu_list is None:
        parser.error("--no-membind needs --socket or --cpu-list: memory is only bound in a pinned run")
    if args.expect_affinity is not None:
        try:
            power_telemetry.parse_cpu_list(args.expect_affinity)
        except ValueError as exc:
            parser.error(f"--expect-affinity: {exc}")
    if args.formal:
        if not args.skip_run:
            parser.error("--formal validates a finished round: add --skip-run")
        if args.round is None:
            parser.error("--formal requires --round N (the executed order is part of the design)")
        if args.error_policy == "strict":
            parser.error("--formal uses --error-policy classified (amendment A1)")
        args.error_policy = "classified"

        def pin(label, given, wanted):
            if given not in (None, [], ()) and sorted(map(str, given)) != sorted(map(str, wanted)):
                parser.error(f"--formal fixes {label} to {list(wanted)}; got {list(given)}")
            return list(wanted)

        args.engines = pin("--engines", args.engines, FORMAL["engines"])
        if args.runs not in (None, FORMAL["runs"]):
            parser.error(f"--formal fixes --runs to {FORMAL['runs']}; got {args.runs}")
        args.runs = FORMAL["runs"]
        if args.scales:
            parser.error("--formal fixes the scales per suite; do not pass --scales")
        args.baseline_scales = pin("--baseline-scales", args.baseline_scales, FORMAL["scales"]["baseline"])
        args.complex_scales = pin("--complex-scales", args.complex_scales, FORMAL["scales"]["complex"])
        args.expect_gpu_unsupported = pin("--expect-gpu-unsupported", args.expect_gpu_unsupported, FORMAL["expect_gpu_unsupported"])
        if args.expect_cpus not in (None, FORMAL["cpus"]):
            parser.error(f"--formal fixes --expect-cpus to {FORMAL['cpus']}; got {args.expect_cpus}")
        args.expect_cpus = FORMAL["cpus"]
        if args.spark_gpu_profile != FORMAL["spark_gpu_profile"] or args.polars_gpu_executor != FORMAL["polars_gpu_executor"]:
            parser.error(f"--formal requires --spark-gpu-profile {FORMAL['spark_gpu_profile']} and "
                         f"--polars-gpu-executor {FORMAL['polars_gpu_executor']}")
        if args.suites != list(SUITE_MODULES):
            parser.error("--formal validates both suites")
    if args.runs is None:
        args.runs = 1
    default_scales = list(args.scales or ["1"])
    args.suite_scales = {"baseline": [str(s) for s in (args.baseline_scales or default_scales)],
                         "complex": [str(s) for s in (args.complex_scales or default_scales)]}
    args.scales = sorted({s for suite in args.suites for s in args.suite_scales[suite]}, key=float)
    args.error_policy = args.error_policy or "strict"
    if args.round is not None and args.skip_run is False:
        parser.error("--round checks an existing round: add --skip-run")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    resolve_args(parser, args)
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
    engines = [e for e in ALL_ENGINES if e in engines]
    if args.reference_engine not in engines and args.formal:
        parser.error(f"--reference-engine {args.reference_engine} is not among the engines {engines}")
    args.engines = engines
    report_path = (args.report or args.output / "preflight-report.json").resolve()
    if report_path.exists():
        parser.error(f"refusing to overwrite {report_path}")
    args.output.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    print(f"preflight: engines={engines} suites={suites} scales={args.suite_scales} runs={args.runs} output={args.output}"
          f"{' FORMAL round ' + str(args.round) if args.formal else ''}", flush=True)

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
    expected_sessions = []
    if not any(c["id"] == "run" and c["status"] == "SKIP" and "failed" in c["detail"] for c in checks.items):
        load_inputs_report(checks, args)
        for suite in suites:
            if not run_dirs[suite].is_dir():
                checks.add(f"{suite}", "FAIL", f"{run_dirs[suite]} does not exist", gates=(G1,))
                continue
            scales = args.suite_scales[suite]
            check_schedule_coverage(checks, suite, run_dirs[suite], scales, engines)
            for scale in scales:
                expected_sessions += [(suite, scale, engine) for engine in engines]
                validate_suite(checks, suite, run_dirs[suite], scale, engines, args.runs, args)
        if args.round is not None:
            check_executed_order(checks, args, suites, run_dirs, args.suite_scales, engines)
    # sessions of a suite whose run root is missing still count as expected (and unevaluated -> fail)
    for suite in suites:
        for scale in args.suite_scales[suite]:
            for engine in engines:
                if (suite, scale, engine) not in expected_sessions:
                    expected_sessions.append((suite, scale, engine))

    digests = check_input_consistency(checks, args.prior_reports)
    outcomes = coverage_outcomes(checks)
    unstable = []
    if args.prior_reports:
        cells = [{coverage_cell(o) for o in outcomes}]
        for path in args.prior_reports:
            try:
                cells.append({coverage_cell(o) for o in read_json(path).get("coverage", {}).get("outcomes", [])})
            except (OSError, ValueError) as error:
                checks.add(f"prior_report:{path.name}", "FAIL", f"cannot read prior report: {error}", gates=(GO,))
        unstable = sorted(set().union(*cells) - set.intersection(*cells))
        for cell in unstable:
            checks.add("unstable_coverage:" + "/".join(cell), "WARN",
                       f"{cell} is a coverage outcome in some rounds but not all: excluded from contrasts in every round", gates=())
    evaluation = evaluate_gates(checks, expected_sessions, args.strict)
    by_session = {(s["suite"], s["scale"], s["engine"]): s for s in evaluation["sessions"]}
    for outcome in outcomes:
        by_session.get((outcome["suite"], str(outcome["scale"]), outcome["engine"]), {}).setdefault("coverage", []).append(outcome)
    gate_verdicts = {g: v["verdict"] for g, v in evaluation["gates"].items()}
    verdict = "fail" if ("fail" in gate_verdicts.values() or checks.failed(args.strict)) else "pass"
    failing_checks = [c["id"] for c in checks.items if c["status"] == "FAIL" or (args.strict and c["status"] == "WARN")]
    ok = verdict == "pass"
    report = {"tool": "preflight", "format": 2, "started_at_utc": started.isoformat(),
              "completed_at_utc": datetime.now(timezone.utc).isoformat(),
              "verdict": verdict, "ok": ok, "failing_checks": failing_checks, "counts": checks.counts(),
              "gates": evaluation["gates"], "sessions": evaluation["sessions"],
              "coverage": {"rules": [r.describe() for r in ERROR_RULES], "policy": args.error_policy,
                           "predeclared": list(args.expect_gpu_unsupported), "outcomes": outcomes, "unstable": [list(c) for c in unstable]},
              "expected": {"engines": engines, "suites": {s: {"scales": args.suite_scales[s], "run_dir": str(run_dirs[s])} for s in suites},
                           "runs": args.runs, "round": args.round, "formal": args.formal,
                           "round_engine_order": round_engine_order(args.round) if args.round else None,
                           "reference_engine": args.reference_engine, "expect_cpus": args.expect_cpus},
              "inputs": {"manifest_sha256": digests},
              "evidence_sha256": evidence_digests(run_dirs, suites, args.suite_scales, engines),
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
    for gate, info in evaluation["gates"].items():
        print(f"  gate {gate}: {info['verdict']}" + (f" ({len(info['failing_checks'])} failing check(s))" if info["failing_checks"] else ""))
    print("PREFLIGHT:", "OK" if ok else "FAILED", checks.counts(), flush=True)
    print(f"VERDICT: {verdict}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
