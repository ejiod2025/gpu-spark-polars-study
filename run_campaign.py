#!/usr/bin/env python3
"""Run the pre-registered H100 campaign (docs/H100_EXPERIMENT_DESIGN.md): 4 balanced rounds plus the side study.

Idempotent and resumable: every decision is derived from what is on disk under --root.

  <root>/campaign.json              resolved design, design-doc sha256, git/source/input hashes (written once); "lease"
                                    (checked, or the --no-lease-check marker); optional "cost" (instance price),
                                    "telemetry" (power sampler options) and "pinning" (resolved CPUs) blocks when supplied
  <root>/invocations.jsonl          one line per invocation (argv, hashes, drift against campaign.json)
  <root>/status.json                live: current round/suite/scale/engine, sessions done/total, ETA
  <root>/round-<k>/<suite>/         a run_experiment.py output root
  <root>/round-<k>/gates.json       integrity verdict (design section 7) from preflight.py
  <root>/round-<k>/attempt.json     what was launched, when, with which exit codes
  <root>/round-<k>-repeat-<n>/      a repeat of a failed or interrupted round; no round directory is reused
  <root>/side-study/round-<k>/<arm>/<suite>/ and gates.json   section 10 (Polars 1.44.2 vs 2.0.0)

A campaign refuses to start (exit 6) without a valid lease: an instance name p3-<purpose>-owner-<id>-expires-<YYYYMMDDTHHMMZ>
with the expiry in the future, from --lease-name or the first line of /etc/paper3-lease (docs/PAPER3_INFRA.md section 5).
--no-lease-check is for local tests only; it is recorded in campaign.json and marks the campaign informal.

Examples:
  python run_campaign.py --dry-run
  python run_campaign.py --root /data/campaign --jar $RAPIDS_JAR --data-root $DATA_ROOT --lease-name p3-main-owner-ab12-expires-20261201T1800Z
  python run_campaign.py ... --socket 0 --cpu-energy require     # CPU-only bare metal: engines on socket 0, RAPL mandatory

Paper 3 campaign mode (docs/PAPER3_ENERGY_DESIGN.md; opt-in, Paper 1 campaigns are not affected): --paper3-host {a100,h100,gh200,
b200,c-gcp,c-vm} fixes the engines, scales, GPU-energy scope and 10 s window of the host class; --paper3-launch 1|2 runs one
Williams square (rounds 1-4 / 5-8, each launch with its own --root and lease); --paper3-lock-date YYYYMMDD is the seed. Rounds are
named by the design's numbers (round-5 ... round-8), the per-round gate adds G6-G12 (paper3_gates.py, paper3-gates.json), and a
formal campaign (no test flag) verifies the lock tag/commit/date and refuses a host whose t_min or sources are unset in
paper3_hosts.json. See paper3_campaign.py for the schedule and the choices the design leaves open.
  python run_campaign.py --dry-run --paper3-host h100 --paper3-launch 1 --paper3-lock-date 20261116
  python run_campaign.py --root R --data-root D --jar J --lease-name L --host-label gpu_1x_h100_sxm5 --paper3-host h100 \
      --paper3-launch 1 --paper3-lock-date 20261116 --paper3-lock-commit <sha> --paper3-lock-doi <doi>
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import secrets
import signal
import statistics
import subprocess
import sys
import threading
import time

import paper3_campaign
import paper3_gates
import power_telemetry
from probe_power import LEASE_FILE, lease_status

HERE = Path(__file__).resolve().parent
RUN_EXPERIMENT = HERE / "run_experiment.py"
PREFLIGHT = HERE / "preflight.py"
DESIGN_DOC = HERE / "docs" / "H100_EXPERIMENT_DESIGN.md"

# ---- the locked design (sections 3, 5, 7, 10) -----------------------------------------------------
SEED = 20261029
ENGINES = ("polars", "polars_gpu", "spark_cpu", "spark_gpu")
GPU_ENGINES = ("polars_gpu", "spark_gpu")
LETTERS = {"A": "spark_cpu", "B": "polars_gpu", "C": "polars", "D": "spark_gpu"}
SEQUENCES = ("ABDC", "BCAD", "CDBA", "DACB")
SUITES = {
    "baseline": {"scales": ["1", "10", "30", "100", "300"], "query_cleanup": "none"},
    "complex": {"scales": ["30", "100", "300"], "query_cleanup": "verified_gc", "cleanup_timeout_seconds": 60},
}
BASELINE_FIRST_ROUNDS = (1, 3)
FORMAL_FLAGS = {"cpus": 26, "driver_memory": "96g", "runs": 3, "spark_gpu_profile": "a100-parity",
                "polars_gpu_executor": "streaming"}
EXPECT_GPU_UNSUPPORTED = ("cx_q13", "cx_window")
MAIN_POLARS_VERSION = "1.44.2"
SIDE_ARMS = (("A", "polars-1.44.2", MAIN_POLARS_VERSION), ("B", "polars-2.0.0", "2.0.0"))
SIDE_SEQUENCES = ("AB", "BA", "BA", "AB")
MAX_ATTEMPTS = 50
# Files whose change between invocations can alter what a session measures or how a round is judged.
# Analysis and plotting scripts are hashed too, but their drift is not worth a warning mid-campaign.
CRITICAL_SOURCES = ("run_experiment.py", "worker.py", "power_telemetry.py", "query_cleanup.py", "workloads.py", "complex_workloads.py",
                    "complex_joins.py", "complex_analytics.py", "complex_oracle.py", "preflight.py", "run_campaign.py",
                    "docs/H100_EXPERIMENT_DESIGN.md")

# Measurement instrumentation passed through to run_experiment.py only when given (campaign key -> its flag).
TELEMETRY_FLAGS = {"redact_gpu_identifiers": "--redact-gpu-identifiers", "gpu_energy": "--gpu-energy", "power_telemetry": "--power-telemetry", "power_interval_ms": "--power-interval-ms",
                   "ipmi_interval_seconds": "--ipmi-interval-seconds", "idle_baseline_seconds": "--idle-baseline-seconds",
                   "cpu_energy": "--cpu-energy",
                   # Paper 3 energy mode (docs/PAPER3_ENERGY_DESIGN.md sections 2.1, 2.3, 2.4); absent = Paper 1 command lines
                   "gpu_energy_scope": "--gpu-energy-scope", "energy_window_s": "--energy-window-s"}
COST_FIELDS = ("instance_type", "provider", "region", "hourly_price_usd", "price_source_url", "price_snapshot_utc")
# Paper 3 campaign mode (docs/PAPER3_ENERGY_DESIGN.md): opt-in with --paper3-host; Paper 1 campaigns never touch these.
PAPER3_HOSTS_CONFIG = HERE / "paper3_hosts.json"
PAPER3_DESIGN_DOC = HERE / paper3_campaign.DESIGN_DOC_REL
PAPER3_CRITICAL_SOURCES = ("paper3_campaign.py", "paper3_gates.py", "paper3_hosts.json", "gpu_pulse.py")
PAPER3_TELEMETRY_FLAGS = {"session_idle_seconds": "--session-idle-seconds",      # keys only the Paper 3 host classes set
                          "session_cooldown_seconds": "--session-cooldown-seconds",
                          "g8_pulse_seconds": "--g8-pulse-seconds", "g8_pulse_engines": "--g8-pulse-engines"}
PAPER3_COST_FIELDS = ("instance_id", "launch_utc", "credit_funded")             # section 2.6 P-BILLED; the terminate time is not known at start

EXIT_STOPPED, EXIT_GATE_TOOL, EXIT_DESIGN, EXIT_LEASE, EXIT_INTERRUPTED = 3, 4, 5, 6, 130


class CampaignError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class Interrupted(BaseException):
    """SIGINT/SIGTERM: stop the running suite, leave evidence, exit; resume starts a repeat."""


def now():
    return datetime.now(timezone.utc).isoformat()


def log(message):
    print(f"[{now()}] {message}", flush=True)


# ---- small file helpers -----------------------------------------------------------------------------

def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scale_dir_name(scale: str) -> str:
    return "sf" + scale.replace(".", "p")


# ---- design -----------------------------------------------------------------------------------------

def derive_letters() -> dict:
    """Letters from the pre-registered seed; must reproduce the table in section 5."""
    engines = ["polars", "polars_gpu", "spark_cpu", "spark_gpu"]
    random.Random(SEED).shuffle(engines)
    derived = dict(zip("ABCD", engines))
    if derived != LETTERS:
        raise CampaignError(f"seed {SEED} yields letters {derived}, design section 5 says {LETTERS}", EXIT_DESIGN)
    return derived


def check_williams(sequences, letters) -> None:
    """Each engine once per position; each ordered pair of engines adjacent exactly once."""
    orders = [[letters[c] for c in seq] for seq in sequences]
    n = len(letters)
    if len(orders) != n or any(sorted(order) != sorted(letters.values()) for order in orders):
        raise CampaignError("sequences are not permutations of the four engines", EXIT_DESIGN)
    for position in range(n):
        if sorted(order[position] for order in orders) != sorted(letters.values()):
            raise CampaignError(f"position {position + 1} does not hold each engine exactly once", EXIT_DESIGN)
    pairs = [(a, b) for order in orders for a, b in zip(order, order[1:])]
    expected = set(itertools.permutations(letters.values(), 2))
    if len(pairs) != len(set(pairs)) or set(pairs) != expected:
        raise CampaignError("ordered adjacent pairs are not each exactly once", EXIT_DESIGN)


def check_side_sequences() -> None:
    """Section 10: orders AB, BA, BA, AB, so each arm runs first twice and second twice."""
    if sorted(SIDE_SEQUENCES) != ["AB", "AB", "BA", "BA"] or SIDE_SEQUENCES != ("AB", "BA", "BA", "AB"):
        raise CampaignError(f"side-study sequences {SIDE_SEQUENCES} are not AB, BA, BA, AB", EXIT_DESIGN)


def suite_order(round_number: int) -> list:
    return ["baseline", "complex"] if round_number in BASELINE_FIRST_ROUNDS else ["complex", "baseline"]


@dataclass
class Job:
    """One run_experiment.py invocation: one suite (and, in the side study, one Polars arm)."""
    suite: str
    scales: list
    engines: list
    order: list
    python: str
    arm: str | None = None

    @property
    def key(self) -> str:
        return f"{self.arm}/{self.suite}" if self.arm else self.suite

    @property
    def out_rel(self) -> Path:
        return Path(self.arm) / self.suite if self.arm else Path(self.suite)

    @property
    def sessions(self) -> int:
        return len(self.scales) * len(self.engines)


@dataclass
class Unit:
    """One round of the campaign or of the side study."""
    kind: str  # "main" | "side"
    round: int
    label: str
    base: Path
    jobs: list = field(default_factory=list)

    @property
    def uid(self) -> str:
        return f"{self.kind}-r{self.round}"

    @property
    def name(self) -> str:
        return f"round-{self.round}"

    @property
    def sessions(self) -> int:
        return sum(job.sessions for job in self.jobs)

    def attempt_path(self, index: int) -> Path:
        return self.base / (self.name if index == 0 else f"{self.name}-repeat-{index}")


@dataclass
class Paper3Setup:
    """Everything --paper3-host adds to a campaign; None on Config for a Paper 1 campaign."""
    plan: paper3_campaign.Plan
    launch: str
    rounds: list                              # the RoundPlans this launch runs (all of the design's, or its --rounds prefix)
    lock: dict                                # date_utc, seed, tag, commit, zenodo_doi: part of the resolved design
    host_config: Path
    host: paper3_gates.HostGates
    host_entry_sha256: str
    vm_pin: dict | None = None                # C-VM: the pin read from the VM's own topology and what it rests on (paper3_campaign.vm_pin)
    lock_evidence: dict | None = None         # verify_lock() result of a formal campaign, written to campaign.json


@dataclass
class Config:
    root: Path | None
    data_root: Path | None
    jar: Path | None
    side_python: str | None
    cpus: int
    driver_memory: str
    rounds: int
    only_side_study: bool
    scales: dict
    engines: list
    formal: bool
    test_flags: list
    allow_any_gpu: bool = False
    engine_timeout_seconds: float | None = None
    inputs_reports: list = field(default_factory=list)
    host_label: str | None = None
    main_python: str = sys.executable
    cost: dict | None = None
    telemetry: dict = field(default_factory=dict)
    include_hostname: bool = False         # opt-in: the raw hostname in campaign.json (cloud hostnames can encode the IP)
    host_salt: bytes | None = None         # per-campaign secret kept on the host, never written under --root
    host_salt_created: bool = False
    no_lease_check: bool = False           # local tests only; a test flag, so the campaign is informal
    lease_name: str | None = None
    lease_min_minutes: float = 0
    lease: dict | None = None              # what the lease check found (or that it was skipped), written to campaign.json
    pin_socket: int | None = None          # --socket / --cpu-list as given, passed to run_experiment.py
    pin_cpu_list: str | None = None
    no_membind: bool = False               # pinned runs bind memory to the pinned NUMA nodes unless this says otherwise
    pinning: dict | None = None            # power_telemetry.resolve_pinning() on this host, written to campaign.json
    paper3: Paper3Setup | None = None      # Paper 3 campaign mode (--paper3-host); None = the Paper 1 campaign


def build_units_paper3(cfg: Config) -> list:
    """The rounds of one launch of a Paper 3 campaign: the design's own rounds (numbers 1-8), no side study."""
    units = []
    for plan in cfg.paper3.rounds:
        order = [e for e in plan.engine_order if e in cfg.engines]
        jobs = [Job(suite=suite, scales=list(cfg.scales[suite]), engines=[e for e in ENGINES if e in cfg.engines],
                    order=order, python=cfg.main_python) for suite in plan.suites]
        units.append(Unit("main", plan.round, plan.sequence, cfg.root or Path("<root>"), jobs))
    return units


def build_units(cfg: Config) -> list:
    if cfg.paper3 is not None:
        return build_units_paper3(cfg)
    letters = derive_letters()
    check_williams(SEQUENCES, letters)
    check_side_sequences()
    units = []
    if not cfg.only_side_study:
        for number in range(1, cfg.rounds + 1):
            sequence = SEQUENCES[number - 1]
            order = [e for e in (letters[c] for c in sequence) if e in cfg.engines]
            jobs = [Job(suite=suite, scales=list(cfg.scales[suite]), engines=[e for e in ENGINES if e in cfg.engines],
                        order=order, python=cfg.main_python)
                    for suite in suite_order(number)]
            units.append(Unit("main", number, sequence, cfg.root or Path("<root>"), jobs))
    if "polars" in cfg.engines:
        arms = {letter: (arm_id, version) for letter, arm_id, version in SIDE_ARMS}
        pythons = {SIDE_ARMS[0][1]: cfg.main_python, SIDE_ARMS[1][1]: cfg.side_python or "$PYTHON_A100COMPAT_ENV/bin/python"}
        for number in range(1, cfg.rounds + 1):
            sequence = SIDE_SEQUENCES[number - 1]
            jobs = [Job(suite=suite, scales=list(cfg.scales[suite]), engines=["polars"], order=["polars"],
                        python=pythons[arms[letter][0]], arm=arms[letter][0])
                    for suite in suite_order(number) for letter in sequence]
            units.append(Unit("side", number, sequence, (cfg.root or Path("<root>")) / "side-study", jobs))
    return units


def design_dict_paper3(cfg: Config, units: list) -> dict:
    """The resolved design of a Paper 3 launch: the pre-registered one (both squares), and which rounds this launch runs."""
    setup = cfg.paper3
    plan = setup.plan
    return {
        "format": 1,
        "design_doc": paper3_campaign.DESIGN_DOC_REL,
        "seed": plan.seed,
        "letters": plan.letters,
        "sequences": [r.sequence for r in plan.rounds],
        "williams_check": ("each engine once per position; each ordered pair adjacent exactly once, in each square"
                           if plan.spec.kind == "gpu" else "two-engine orders as written in section 3.2"),
        "engines": [e for e in ENGINES if e in cfg.engines],
        "suites": {s: {**SUITES[s], "scales": list(cfg.scales[s])} for s in plan.spec.suites},
        "main_rounds": [{"round": u.round, "sequence": u.label, "engine_order": u.jobs[0].order,
                         "suite_order": [j.suite for j in u.jobs]} for u in units],
        "flags": {"cpus": cfg.cpus, "driver_memory": cfg.driver_memory, "runs": FORMAL_FLAGS["runs"],
                  "keep_going": True, "spark_gpu_profile": FORMAL_FLAGS["spark_gpu_profile"],
                  "polars_gpu_executor": FORMAL_FLAGS["polars_gpu_executor"]},
        "expect_gpu_unsupported": list(EXPECT_GPU_UNSUPPORTED),
        "gpu_policy": "any_gpu (amendment A2 hosts)" if cfg.allow_any_gpu else "h100_required",
        "totals": {"main_sessions": sum(u.sessions for u in units), "side_sessions": 0, "sessions": sum(u.sessions for u in units)},
        "formal": cfg.formal,
        "test_flags": cfg.test_flags,
        **({"pinning": {"socket": cfg.pin_socket, "cpu_list": cfg.pin_cpu_list, "membind": not cfg.no_membind}}
           if cfg.pin_socket is not None or cfg.pin_cpu_list is not None else {}),
        "energy": {"gpu_energy_scope": cfg.telemetry.get("gpu_energy_scope"), "energy_window_s": cfg.telemetry.get("energy_window_s")},
        "telemetry": {k: v for k, v in sorted(cfg.telemetry.items())},
        "paper3": paper3_campaign.design_block(plan, setup.launch, setup.rounds, host_config_sha256=setup.host_entry_sha256,
                                               t_min_s=setup.host.t_min_s, lock=setup.lock, vm_pin=setup.vm_pin),
    }


def design_dict(cfg: Config, units: list) -> dict:
    if cfg.paper3 is not None:
        return design_dict_paper3(cfg, units)
    letters = derive_letters()
    main = [u for u in units if u.kind == "main"]
    side = [u for u in units if u.kind == "side"]
    return {
        "format": 1,
        "design_doc": "docs/H100_EXPERIMENT_DESIGN.md",
        "seed": SEED,
        "letters": letters,
        "sequences": list(SEQUENCES),
        "williams_check": "each engine once per position; each ordered pair adjacent exactly once",
        "engines": [e for e in ENGINES if e in cfg.engines],
        "suites": {s: {**spec, "scales": list(cfg.scales[s])} for s, spec in SUITES.items()},
        "main_rounds": [{"round": u.round, "sequence": u.label, "engine_order": u.jobs[0].order,
                         "suite_order": [j.suite for j in u.jobs]} for u in main],
        "flags": {"cpus": cfg.cpus, "driver_memory": cfg.driver_memory, "runs": FORMAL_FLAGS["runs"],
                  "keep_going": True, "spark_gpu_profile": FORMAL_FLAGS["spark_gpu_profile"],
                  "polars_gpu_executor": FORMAL_FLAGS["polars_gpu_executor"]},
        "expect_gpu_unsupported": list(EXPECT_GPU_UNSUPPORTED),
        "gpu_policy": "any_gpu (amendment A2 hosts)" if cfg.allow_any_gpu else "h100_required",
        "side_study": {
            "arms": [{"letter": letter, "id": arm_id, "polars_version": version} for letter, arm_id, version in SIDE_ARMS],
            "sequences": list(SIDE_SEQUENCES),
            "interleave": "per suite: both arms run back to back in the round's order, then the next suite",
            "rounds": [{"round": u.round, "sequence": u.label, "suite_order": suite_order(u.round),
                        "arm_order": [SIDE_ARMS[0][1] if c == "A" else SIDE_ARMS[1][1] for c in u.label]} for u in side],
        },
        "totals": {"main_sessions": sum(u.sessions for u in main), "side_sessions": sum(u.sessions for u in side),
                   "sessions": sum(u.sessions for u in units)},
        "formal": cfg.formal,
        "test_flags": cfg.test_flags,
        **({"pinning": {"socket": cfg.pin_socket, "cpu_list": cfg.pin_cpu_list, "membind": not cfg.no_membind}}
           if cfg.pin_socket is not None or cfg.pin_cpu_list is not None else {}),
        # The declared GPU counter scope and window rule are part of the resolved design: a resume with other values is refused.
        **({"energy": {"gpu_energy_scope": cfg.telemetry.get("gpu_energy_scope"), "energy_window_s": cfg.telemetry.get("energy_window_s")}}
           if cfg.telemetry.get("gpu_energy_scope") is not None else {}),
    }


# ---- command lines ----------------------------------------------------------------------------------

def job_command(job: Job, out_dir: Path, cfg: Config) -> list:
    command = [job.python, str(RUN_EXPERIMENT), "--suite", job.suite,
               "--data-root", str(cfg.data_root), "--output", str(out_dir),
               "--scales", *job.scales, "--engines", *job.engines, "--engine-order", *job.order,
               "--runs", str(FORMAL_FLAGS["runs"]), "--cpus", str(cfg.cpus), "--driver-memory", cfg.driver_memory,
               "--keep-going", "--spark-gpu-profile", FORMAL_FLAGS["spark_gpu_profile"],
               "--polars-gpu-executor", FORMAL_FLAGS["polars_gpu_executor"], "--jar", str(cfg.jar)]
    spec = SUITES[job.suite]
    command += ["--query-cleanup", spec["query_cleanup"]]
    if spec["query_cleanup"] == "verified_gc":
        command += ["--cleanup-timeout-seconds", str(spec["cleanup_timeout_seconds"])]
    if cfg.engine_timeout_seconds is not None:
        command += ["--engine-timeout-seconds", str(cfg.engine_timeout_seconds)]
    for key, flag in TELEMETRY_FLAGS.items():  # only what was asked for: run_experiment.py's defaults otherwise
        if key in cfg.telemetry:
            command += [flag, str(cfg.telemetry[key])]
    for key, flag in PAPER3_TELEMETRY_FLAGS.items():
        if key in cfg.telemetry:
            command += [flag, str(cfg.telemetry[key])]
    if cfg.pin_socket is not None:
        command += ["--socket", str(cfg.pin_socket)]
    if cfg.pin_cpu_list is not None:
        command += ["--cpu-list", cfg.pin_cpu_list]
    if cfg.no_membind:
        command.append("--no-membind")
    return command


def gate_command(unit: Unit, attempt: Path, cfg: Config, output: Path, prior_reports=()) -> list:
    """preflight.py in campaign mode. A formal campaign uses --formal, which pins the locked design itself;
    a test campaign passes the same facts as explicit flags so an engine/scale subset can be validated."""
    if cfg.paper3 is not None:
        return gate_command_paper3(unit, attempt, cfg, output, prior_reports)
    command = [sys.executable, str(PREFLIGHT), *(["--formal"] if cfg.formal else []),
               "--skip-run", "--skip-env", "--skip-inputs", "--round", str(unit.round),
               "--baseline-dir", str(attempt / "baseline"), "--complex-dir", str(attempt / "complex"),
               "--output", str(output)]
    if not cfg.formal:
        command += ["--engines", *[e for e in ENGINES if e in cfg.engines],
                    "--expect-gpu-unsupported", *EXPECT_GPU_UNSUPPORTED,
                    "--runs", str(FORMAL_FLAGS["runs"]), "--expect-cpus", str(cfg.cpus),
                    "--baseline-scales", *cfg.scales["baseline"], "--complex-scales", *cfg.scales["complex"],
                    "--spark-gpu-profile", FORMAL_FLAGS["spark_gpu_profile"],
                    "--polars-gpu-executor", FORMAL_FLAGS["polars_gpu_executor"], "--error-policy", "classified"]
    if cfg.allow_any_gpu:
        command.append("--allow-any-gpu")
    if cfg.pinning is not None:
        command += ["--expect-affinity", cfg.pinning["cpus"]]
    if cfg.inputs_reports:
        command += ["--inputs-report", *map(str, cfg.inputs_reports)]
    if prior_reports:
        command += ["--prior-reports", *map(str, prior_reports)]
    return command


def gate_command_paper3(unit: Unit, attempt: Path, cfg: Config, output: Path, prior_reports=()) -> list:
    """preflight.py (Paper 1 gates G1-G5 and the energy-window checks) for one Paper 3 round.

    --formal pins Paper 1's seed, rows and four engines and --round to 1-4; --paper3 is its strictness with the engines, scales,
    suites, engine order and first suite of this round taken from the flags. G6-G12 are run by the campaign itself
    (paper3_gates.py, paper3-gates.json beside gates.json); conformance_problems() checks the executed order a second time."""
    command = [sys.executable, str(PREFLIGHT), "--paper3", "--skip-run", "--skip-env", "--skip-inputs", "--round", str(unit.round),
               "--expect-engine-order", *unit.jobs[0].order, "--expect-suite-first", unit.jobs[0].suite,
               "--baseline-dir", str(attempt / "baseline"), "--complex-dir", str(attempt / "complex"), "--output", str(output),
               "--suites", *[j.suite for j in unit.jobs],
               "--engines", *[e for e in ENGINES if e in cfg.engines],
               "--expect-gpu-unsupported", *EXPECT_GPU_UNSUPPORTED,
               "--runs", str(FORMAL_FLAGS["runs"]), "--expect-cpus", str(cfg.cpus),
               "--baseline-scales", *cfg.scales["baseline"],
               *(["--complex-scales", *cfg.scales["complex"]] if "complex" in cfg.scales else []),
               "--spark-gpu-profile", FORMAL_FLAGS["spark_gpu_profile"],
               "--polars-gpu-executor", FORMAL_FLAGS["polars_gpu_executor"], "--error-policy", "classified"]
    if cfg.allow_any_gpu:
        command.append("--allow-any-gpu")
    if cfg.pinning is not None:
        command += ["--expect-affinity", cfg.pinning["cpus"]]
    if cfg.inputs_reports:
        command += ["--inputs-report", *map(str, cfg.inputs_reports)]
    if prior_reports:
        command += ["--prior-reports", *map(str, prior_reports)]
    return command


# ---- provenance -------------------------------------------------------------------------------------

def run_text(command, timeout=120):
    try:
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return result.returncode, result.stdout


def git_info() -> dict:
    def git(*args):
        code, out = run_text(["git", "-C", str(HERE), *args], timeout=60)
        return out.strip() if code == 0 else None
    status = git("status", "--porcelain")
    return {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_files": status.splitlines() if status else [],
            "diff_sha256": hashlib.sha256((git("diff", "HEAD") or "").encode()).hexdigest()}


def source_hashes(paper3: bool = False) -> dict:
    files = sorted(HERE.glob("*.py")) + [HERE / "setup_h100.sh", HERE / "requirements-gpu.txt"]
    hashes = {str(p.relative_to(HERE)): sha256_file(p) for p in files if p.is_file()}
    hashes["docs/H100_EXPERIMENT_DESIGN.md"] = sha256_file(DESIGN_DOC)
    if paper3:
        hashes[paper3_campaign.DESIGN_DOC_REL] = sha256_file(PAPER3_DESIGN_DOC)
        hashes["paper3_hosts.json"] = sha256_file(PAPER3_HOSTS_CONFIG)
    return hashes


def probe_python(python: str) -> dict:
    code = ("import importlib.util, json, sys, polars; print(json.dumps({'python': sys.version.split()[0], "
            "'polars': polars.__version__, 'executable': sys.executable, "
            "'cudf_polars_importable_spec': importlib.util.find_spec('cudf_polars') is not None, "
            "'spark_submit': __import__('os').path.isfile(__import__('os').path.join("
            "__import__('os').path.dirname(sys.executable), 'spark-submit'))}))")
    returncode, output = run_text([python, "-I", "-c", code])
    try:
        return json.loads(output.strip().splitlines()[-1]) if returncode == 0 else {"error": (output or "")[-300:]}
    except (ValueError, IndexError):
        return {"error": (output or "")[-300:]}


def resolve_side_python(value: str | None) -> str | None:
    """$PYTHON_A100COMPAT_ENV may name the venv directory or its python binary."""
    if not value:
        return None
    path = Path(value).expanduser()
    return str(path / "bin" / "python") if path.is_dir() else str(path)


def input_hashes(cfg: Config) -> dict:
    scales = sorted({s for spec in cfg.scales.values() for s in spec}, key=float)
    manifests = {}
    for scale in scales:
        path = cfg.data_root / scale_dir_name(scale) / "manifest.json"
        manifests[scale_dir_name(scale)] = sha256_file(path) if path.is_file() else None
    jar = cfg.jar if (cfg.jar and "spark_gpu" in cfg.engines and cfg.jar.is_file()) else None
    reports = {str(path): sha256_file(path) if path.is_file() else None for path in cfg.inputs_reports}
    return {"manifest_sha256": manifests, "rapids_jar": str(cfg.jar),
            "rapids_jar_sha256": sha256_file(jar) if jar else None, "inputs_reports_sha256": reports}


def host_salt_path(root: Path) -> Path:
    base = os.environ.get("CAMPAIGN_HOST_SALT_DIR") or Path.home() / ".local" / "state" / "gpu-spark-polars-study" / "host-salts"
    return Path(base) / (hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16] + ".salt")


def load_or_create_salt(root: Path):
    """(salt, created): the campaign's secret host-id salt, kept beside the user's state and never under the campaign root.

    The campaign root is what gets published; a salt inside it would let anyone test guessed hostnames against the
    published hash. Created once with mode 0600 and never overwritten."""
    path = host_salt_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return bytes.fromhex(path.read_text().strip()), False
    salt = secrets.token_bytes(32)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(salt.hex() + "\n")
    return salt, True


def host_id(salt: bytes) -> str:
    """sha256(salt, hostname, machine-id): equal within one campaign means the same host; unlinkable across campaigns."""
    try:
        machine_id = Path("/etc/machine-id").read_text().strip()
    except OSError:
        machine_id = ""
    return hashlib.sha256(salt + b"\0" + platform.node().encode() + b"\0" + machine_id.encode()).hexdigest()


def detect_virt() -> str | None:
    """What systemd-detect-virt says (kvm, google, none ...), also when it exits 1 to say "none"; None when it cannot be asked."""
    _code, out = run_text(["systemd-detect-virt"], timeout=30)
    lines = [line.strip() for line in (out or "").splitlines() if line.strip()]
    return lines[0] if lines and " " not in lines[0] and "Error" not in lines[0] else None


def storage_identity(data_root) -> dict:
    """The device under the data root, as lsblk and findmnt report it (no serial numbers): the storage type of design 3.1.
    Best effort; a field the host cannot answer is None."""
    out = {"data_root_source": None, "data_root_fstype": None, "device": None, "rotational": None, "type": None, "model": None, "transport": None}
    if data_root is None:
        return out
    code, text = run_text(["findmnt", "-n", "-T", str(data_root), "-o", "SOURCE,FSTYPE"], timeout=30)
    parts = text.split() if code == 0 and text else []
    if len(parts) >= 2:
        out["data_root_source"], out["data_root_fstype"] = parts[0], parts[1]
        code, parent = run_text(["lsblk", "-n", "-o", "PKNAME", parts[0]], timeout=30)
        device = (parent.split() or [None])[0] if code == 0 and parent else None
        device = f"/dev/{device}" if device else parts[0]
        code, text = run_text(["lsblk", "-d", "-n", "-P", "-o", "NAME,ROTA,TYPE,TRAN,MODEL", device], timeout=30)
        fields = dict(re.findall(r'(\w+)="([^"]*)"', text or "")) if code == 0 else {}
        if fields.get("NAME"):
            out.update(device=fields["NAME"], rotational={"1": True, "0": False}.get(fields.get("ROTA")), type=fields.get("TYPE") or None,
                       transport=fields.get("TRAN") or None, model=fields.get("MODEL", "").strip() or None)
    return out


def host_identity(cfg: Config) -> dict:
    """Amendment A2: each host's campaign records which host it ran on (by salted hash; raw hostname only on request)."""
    def probe(command, field=None):
        code, out = run_text(command, timeout=30)
        lines = [line for line in out.splitlines() if line.strip() and (field is None or line.startswith(field))] if code == 0 else []
        return (lines[0].split(":", 1)[-1] if field else lines[0]).strip() if lines else None
    try:
        import psutil
        memory = psutil.virtual_memory().total
    except ImportError:
        memory = None
    identity = {"instance_type": cfg.host_label, "platform": platform.platform(),
                "cpus": os.cpu_count(), "cpu_model": probe(["lscpu"], "Model name"), "memory_total_bytes": memory,
                "gpu": probe(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"])}
    if cfg.paper3 is not None:   # design 3.1 records the hypervisor and the storage type with the host; Paper 1's campaign.json is unchanged
        identity["hypervisor"] = detect_virt()
        identity["storage"] = storage_identity(cfg.data_root)
    if cfg.host_salt is not None:
        identity["host_id_sha256"] = host_id(cfg.host_salt)
        identity["host_id_basis"] = ("sha256(per-campaign secret salt, hostname, /etc/machine-id); the salt stays on the host "
                                     "and is not published; equal hashes within a campaign mean the same host")
    if cfg.include_hostname:
        identity["hostname"] = platform.node()
    return identity


def design_doc_hashes(cfg: Config) -> dict:
    """design_doc_sha256 is what a resume compares. Paper 1: the whole locked file. Paper 3: sections 1-11 only, because after
    the lock only section 12 may grow (an amendment must not stop a running campaign; the whole-file hash is kept as drift)."""
    if cfg.paper3 is None:
        return {"design_doc_sha256": sha256_file(DESIGN_DOC)}
    text = PAPER3_DESIGN_DOC.read_text()
    return {"design_doc_sha256": hashlib.sha256(paper3_campaign.sections_1_to_11(text).rstrip().encode()).hexdigest(),
            "design_doc_basis": "sections 1-11 of docs/PAPER3_ENERGY_DESIGN.md (text before the amendments heading)",
            "design_doc_full_sha256": sha256_file(PAPER3_DESIGN_DOC)}


def provenance(cfg: Config, units: list) -> dict:
    side_needed = any(u.kind == "side" for u in units)
    return {
        "created_utc": now(),
        **design_doc_hashes(cfg),
        "git": git_info(),
        "source_sha256": source_hashes(cfg.paper3 is not None),
        "host": host_identity(cfg),
        "environments": {"main": probe_python(cfg.main_python),
                         **({"side_a100compat": probe_python(cfg.side_python)} if side_needed and cfg.side_python else {})},
        "paths": {"root": str(cfg.root), "data_root": str(cfg.data_root), "jar": str(cfg.jar),
                  "side_python": cfg.side_python},
        "inputs": input_hashes(cfg),
    }


def running_workloads() -> list:
    """Benchmark processes already on this host (e.g. orphans of a killed campaign): design section 6 forbids them."""
    try:
        import psutil
    except ImportError:
        return []
    mine = {os.getpid(), *(p.pid for p in psutil.Process().parents())}
    found = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        if process.info["pid"] in mine:
            continue
        words = process.info["cmdline"] or []
        if any(w.endswith(("/run_experiment.py", "/worker.py")) or w == "org.apache.spark.deploy.SparkSubmit"
               or w in ("run_experiment.py", "worker.py") for w in words):
            found.append(f"{process.info['pid']}: {' '.join(words)[:160]}")
    return found


def check_lease(cfg: Config) -> dict:
    """The lease this campaign runs under (the record for campaign.json), or CampaignError with exit code 6.

    Paper 3 hosts are rented under a name that carries the owner and the expiry,
    p3-<purpose>-owner-<id>-expires-<YYYYMMDDTHHMMZ> (docs/PAPER3_INFRA.md section 5): a campaign refuses to start without a
    valid one, so no host outlives the intent of whoever rented it. The name is --lease-name or the first line of
    /etc/paper3-lease. The record keeps the purpose, the expiry and a hash of the name, not the owner id: campaign.json is
    published. --no-lease-check (local tests only) is recorded as such."""
    if cfg.no_lease_check:
        return {"checked": False, "reason": "--no-lease-check (local tests only)"}
    name, source = cfg.lease_name, "--lease-name"
    if not name:
        try:
            lines = Path(LEASE_FILE).read_text().splitlines()
        except OSError:
            lines = []
        name, source = (lines[0].strip() if lines else None), LEASE_FILE
    if not name:
        raise CampaignError("no lease: pass --lease-name p3-<purpose>-owner-<id>-expires-<YYYYMMDDTHHMMZ>, or write that "
                            f"name to {LEASE_FILE} at launch (--no-lease-check is for local tests only)", EXIT_LEASE)
    status = lease_status(name, min_minutes=cfg.lease_min_minutes)
    if not status["valid"]:
        left = status["minutes_left"]
        timing = ("" if left is None else f" ({-left:.0f} minutes ago)" if left <= 0 else f" (actually {left:g})")
        margin = f"at least {cfg.lease_min_minutes:g} minutes " if cfg.lease_min_minutes else ""
        raise CampaignError(f"lease {name!r} (from {source}) is not valid: {status['problem']}{timing}; the expected form is "
                            f"p3-<purpose>-owner-<id>-expires-<YYYYMMDDTHHMMZ> with the expiry {margin}in the future", EXIT_LEASE)
    return {"checked": True, "source": source, "purpose": status["purpose"], "expires_utc": status["expires_utc"],
            "minutes_left_at_start": status["minutes_left"], "min_minutes_required": cfg.lease_min_minutes,
            "name_sha256": hashlib.sha256(name.encode()).hexdigest(),
            "note": "the lease name embeds the owner id; only its sha256 is recorded"}


def logged_argv(argv) -> list:
    """The command line for invocations.jsonl, without the lease name: it embeds the owner id and the file is published.
    The value of --lease-name (or its unambiguous abbreviations) is replaced by "sha256:<hex>", the hash campaign.json keeps."""
    def hashed(value):
        return "sha256:" + hashlib.sha256(value.encode()).hexdigest()

    def is_lease_flag(flag):
        return flag.startswith("--lease-n") and "--lease-name".startswith(flag)
    out, hide_next = [], False
    for word in argv:
        flag, equals, value = word.partition("=")
        if hide_next:
            out.append(hashed(word))
            hide_next = False
        elif equals and is_lease_flag(flag):
            out.append(f"{flag}={hashed(value)}")
        elif is_lease_flag(word):
            out.append(word)
            hide_next = True
        else:
            out.append(word)
    return out


def resolve_pin(cfg: Config):
    """power_telemetry.resolve_pinning() for the --socket/--cpu-list of this campaign; CampaignError when it cannot apply."""
    try:
        return power_telemetry.resolve_pinning(cfg.pin_socket, cfg.pin_cpu_list)
    except power_telemetry.PinningError as exc:
        raise CampaignError(f"CPU pinning: {exc}", EXIT_DESIGN)


def paper3_startup_checks(cfg: Config, resolved_pinning: bool) -> None:
    """What a Paper 3 campaign needs on top of the Paper 1 checks, refused in seconds. A formal campaign (no test flag) must
    name its instance type, have every value the gates need set in the host config (t_min, probe-declared sources) and, on the
    CPU-only host, pin 26 vCPUs of socket 0 (section 3.1). A test campaign only reports what is missing."""
    setup = cfg.paper3
    spec, missing = setup.plan.spec, setup.host.unset()
    if not resolved_pinning:
        if cfg.host_label and spec.instance_types and cfg.host_label not in spec.instance_types:
            problem = f"--host-label {cfg.host_label!r} is not an instance type of Paper 3 host {spec.host} ({', '.join(spec.instance_types)})"
            if cfg.formal:
                raise CampaignError(problem, EXIT_DESIGN)
            log(f"WARNING: {problem}")
        if cfg.formal and not cfg.host_label:
            raise CampaignError(f"a formal Paper 3 campaign records its instance type: pass --host-label "
                                f"({', '.join(spec.instance_types)})", EXIT_DESIGN)
        if missing:
            problem = (f"host {spec.host!r} has values unset in {setup.host_config.name}: {', '.join(missing)} (null = pending in "
                       "the design); the gates refuse to evaluate such a host")
            if cfg.formal:
                raise CampaignError("formal Paper 3 campaign: " + problem, EXIT_DESIGN)
            log(f"WARNING: {problem}; gates will not be evaluated")
        return
    if spec.vm_pin and cfg.formal and (cfg.pinning or {}).get("cpu_count") != cfg.cpus:
        raise CampaignError(f"formal Paper 3 campaign on {spec.host}: engines are pinned to {cfg.cpus} vCPUs read from the VM's "
                            f"topology (section 3.1); the pinning resolves to {(cfg.pinning or {}).get('cpu_count')}", EXIT_DESIGN)
    if spec.pinned and cfg.formal:
        pinning = cfg.pinning or {}
        if pinning.get("cpu_count") != cfg.cpus or pinning.get("sockets") != [0]:
            raise CampaignError(f"formal Paper 3 campaign on {spec.host}: engines are pinned to {cfg.cpus} vCPUs on socket 0 "
                                f"(section 3.1); the pinning resolves to {pinning.get('cpu_count')} CPUs on socket(s) "
                                f"{pinning.get('sockets')}", EXIT_DESIGN)


def synthetic_smoke_manifests(data_root, scale_dirs) -> list:
    """Scale directories of the data root whose manifest.json is marked synthetic_smoke (tools/paper3_smoke/generate_smoke_data.py): the
    Paper 3 readiness test's invented data, which must never feed a study campaign. An unreadable manifest is not judged here (the input
    gate does that)."""
    found = []
    for name in scale_dirs:
        manifest = read_json(Path(data_root) / name / "manifest.json")
        if isinstance(manifest, dict) and manifest.get("synthetic_smoke"):
            found.append(name)
    return found


def startup_checks(cfg: Config, units: list, prov: dict) -> None:
    """Fail in seconds, not hours into the campaign."""
    if cfg.paper3 is not None:
        paper3_startup_checks(cfg, resolved_pinning=False)
    busy = running_workloads()
    if busy and cfg.formal:
        raise CampaignError("benchmark processes are already running on this host (design section 6: no other "
                            "workload during rounds); stop them first:\n  " + "\n  ".join(busy), EXIT_DESIGN)
    if busy:
        log(f"WARNING: benchmark processes already running (test campaign, continuing): {busy}")
    absent = [path for path, h in prov["inputs"]["inputs_reports_sha256"].items() if h is None]
    if absent:
        raise CampaignError(f"--inputs-report file(s) not found: {absent}", EXIT_DESIGN)
    missing = [scale_dir_name(s) for s, h in prov["inputs"]["manifest_sha256"].items() if h is None]
    if missing:
        raise CampaignError(f"no manifest.json under {cfg.data_root} for {missing}; generate and verify inputs first", EXIT_DESIGN)
    if cfg.paper3 is not None:
        synthetic = synthetic_smoke_manifests(cfg.data_root, prov["inputs"]["manifest_sha256"])
        if synthetic:
            raise CampaignError(f"the data root {cfg.data_root} holds synthetic readiness data (manifest marked synthetic_smoke: {synthetic}); "
                                "a Paper 3 campaign runs only on the TPC-H-derived inputs that passed verify_generated_inputs.py", EXIT_DESIGN)
    if "spark_gpu" in cfg.engines and not cfg.jar.is_file():
        raise CampaignError(f"spark_gpu selected but --jar {cfg.jar} does not exist", EXIT_DESIGN)
    if cfg.telemetry.get("gpu_energy_scope") == "none" and [e for e in cfg.engines if e in GPU_ENGINES]:
        raise CampaignError("--gpu-energy-scope none declares a host with no GPU counter in use, but GPU engine(s) "
                            f"{[e for e in cfg.engines if e in GPU_ENGINES]} are selected (use --engines-override for a CPU-only host)",
                            EXIT_DESIGN)
    side_needed = any(u.kind == "side" for u in units)
    if side_needed and not cfg.side_python:
        raise CampaignError("the side study needs --a100compat-env (or $PYTHON_A100COMPAT_ENV)", EXIT_DESIGN)
    envs = prov["environments"]
    for label, env in envs.items():
        if "error" in env:
            raise CampaignError(f"cannot run {label} python: {env['error']}", EXIT_DESIGN)
    main_env = envs["main"]
    if "polars_gpu" in cfg.engines and not main_env["cudf_polars_importable_spec"]:
        raise CampaignError(f"polars_gpu selected but cudf_polars is not installed for {cfg.main_python}", EXIT_DESIGN)
    if any(e.startswith("spark") for e in cfg.engines) and not main_env["spark_submit"]:
        raise CampaignError(f"a Spark engine is selected but there is no spark-submit next to {cfg.main_python}", EXIT_DESIGN)
    if cfg.formal and not cfg.host_label:
        log("WARNING: no --host-label (instance type); campaign.json will identify the host by GPU/CPU only")
    if cfg.formal:
        # The environment is part of the pre-registered design: wrong versions would silently invalidate it.
        if envs["main"]["polars"] != MAIN_POLARS_VERSION:
            raise CampaignError(f"formal campaign: main environment has polars {envs['main']['polars']}, "
                                f"design section 2 says {MAIN_POLARS_VERSION}", EXIT_DESIGN)
        if side_needed and envs["side_a100compat"]["polars"] != SIDE_ARMS[1][2]:
            raise CampaignError(f"formal campaign: side-study environment has polars "
                                f"{envs['side_a100compat']['polars']}, design section 10 says {SIDE_ARMS[1][2]}", EXIT_DESIGN)
    cfg.pinning = resolve_pin(cfg)
    if cfg.pinning is not None and cfg.cpus > cfg.pinning["cpu_count"]:
        raise CampaignError(f"CPU pinning: {cfg.cpus} engine threads (--cpus) on {cfg.pinning['cpu_count']} pinned CPUs "
                            f"({cfg.pinning['cpus']}) is oversubscribed", EXIT_DESIGN)
    if cfg.paper3 is not None:
        paper3_startup_checks(cfg, resolved_pinning=True)
    if cfg.pinning is not None:
        cfg.pinning["membind"] = membind = power_telemetry.plan_membind(cfg.pinning, cfg.no_membind)
        if membind["enabled"]:  # fail now, not when the first session starts: the design attributes DRAM energy to this socket
            probe = power_telemetry.probe_membind(membind["nodes"])
            membind["probe"] = {k: probe.get(k) for k in ("ok", "mechanism", "numactl", "libnuma")}
            if not probe["ok"]:
                raise CampaignError(f"CPU pinning: memory cannot be bound to NUMA node(s) {membind['nodes']} on this host "
                                    f"({'; '.join(probe.get('reasons') or ['unknown'])}). Install numactl (apt install numactl) "
                                    "or libnuma, or pass --no-membind to run without memory binding (recorded; DRAM energy "
                                    "attribution to the pinned socket is then not guaranteed)", EXIT_DESIGN)
        else:
            log(f"WARNING: memory is not bound: {membind['reason']}")
    energy_mode = cfg.telemetry.get("cpu_energy", "auto")
    if cfg.telemetry.get("power_telemetry", "auto") == "auto" and energy_mode != "off":
        gate = power_telemetry.check_cpu_energy(energy_mode)
        for warning in gate["warnings"]:
            log(f"WARNING: {warning}")
        if not gate["ok"]:
            raise CampaignError("CPU energy: " + "; ".join(gate["problems"]) +
                                " (--cpu-energy off or --power-telemetry off runs the campaign without it)", EXIT_DESIGN)


# ---- status, progress and ETA -----------------------------------------------------------------------

def session_seconds(item: dict):
    wall = item.get("process_wall_seconds")
    if not isinstance(wall, (int, float)) or wall <= 0:
        return None
    preheat = item.get("preheat") or {}
    baseline = (item.get("idle_baseline") or {}).get("actual_seconds")  # idle window before the launch (power telemetry)
    return float(wall) + float(preheat.get("seconds") or 0) + (float(baseline) if isinstance(baseline, (int, float)) else 0.0)


def history_key(kind: str, suite: str, scale: str, who: str) -> tuple:
    return (kind, suite, scale, who)


class Status:
    """Live status.json. Thread-safe; a heartbeat thread refreshes it so a long session stays visibly alive."""

    def __init__(self, path: Path, cfg: Config, units: list):
        self.path, self.cfg, self.units = path, cfg, units
        self.lock = threading.RLock()
        self.started = now()
        self.state = "starting"
        self.message = ""
        self.current: dict = {}
        self.accepted: dict = {}          # uid -> accepted attempt dir
        self.finished: dict = {}          # uid -> {(job key, scale, engine)} in the active attempt
        self.attempts: dict = {}          # uid -> number of attempt directories
        self.failed_sessions = 0
        self.sessions_run = 0
        self.history: dict = {}           # history_key -> [seconds]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.beat, daemon=True)

    # -- bookkeeping
    def total(self) -> int:
        return sum(u.sessions for u in self.units)

    def completed(self) -> int:
        with self.lock:
            return sum(u.sessions if u.uid in self.accepted else len(self.finished.get(u.uid, ())) for u in self.units)

    def add_history(self, job: Job, kind: str, item: dict):
        seconds = session_seconds(item)
        if seconds is not None:
            who = job.arm if job.arm else item.get("engine")
            self.history.setdefault(history_key(kind, job.suite, str(item.get("scale")), who), []).append(seconds)

    def load_history(self, unit: Unit, path: Path):
        with self.lock:
            for job in unit.jobs:
                for item in read_json(path / job.out_rel / "schedule.json") or []:
                    if item.get("status") in ("complete", "failed"):
                        self.add_history(job, unit.kind, item)

    # -- ETA
    def estimate(self, kind, job, scale, who):
        exact = self.history.get(history_key(kind, job.suite, scale, who))
        if exact:
            return statistics.median(exact), "exact"
        if kind == "side":  # same engine, same host: the main rounds' polars sessions are the best proxy
            proxy = self.history.get(history_key("main", job.suite, scale, "polars"))
            if proxy:
                return statistics.median(proxy), "main_polars_proxy"
        same_scale = [statistics.median(v) for (k, s, sc, _), v in self.history.items() if (k, s, sc) == (kind, job.suite, scale) and v]
        if same_scale:
            return statistics.mean(same_scale), "same_scale"
        for suite_filter, label in ((job.suite, "scale_extrapolated"), (None, "scale_extrapolated_cross_suite")):
            rows = [(float(sc), statistics.median(v)) for (k, s, sc, _), v in self.history.items()
                    if v and k == kind and (suite_filter is None or s == suite_filter)]
            total_scale = sum(r[0] for r in rows)
            if total_scale > 0:
                return sum(r[1] for r in rows) / total_scale * float(scale), label
        return None, "none"

    def eta(self) -> dict:
        remaining, basis, unknown = 0.0, {}, 0
        cur = self.current
        for unit in self.units:
            if unit.uid in self.accepted:
                continue
            done = self.finished.get(unit.uid, set())
            for job in unit.jobs:
                for scale in job.scales:
                    for engine in job.engines:
                        who = job.arm or engine
                        if (job.key, scale, engine) in done:
                            continue
                        seconds, how = self.estimate(unit.kind, job, scale, who)
                        if seconds is None:
                            unknown += 1
                            continue
                        if (cur.get("phase") == "session" and cur.get("uid") == unit.uid and cur.get("job") == job.key
                                and cur.get("scale") == scale and cur.get("engine") == engine):
                            seconds = max(0.0, seconds - (time.time() - cur["session_started_unix"]))
                        remaining += seconds
                        basis[how] = basis.get(how, 0) + 1
        out = {"remaining_seconds": round(remaining), "unestimated_sessions": unknown, "basis": basis,
               "note": "sum of per-session estimates; repeats and gates are not included"}
        if unknown == self.total() - self.completed() and unknown:
            out["remaining_seconds"] = None
            out["note"] = "no completed sessions yet to estimate from"
        else:
            out["eta_utc"] = datetime.fromtimestamp(time.time() + remaining, timezone.utc).isoformat()
        return out

    # -- output
    def snapshot(self) -> dict:
        with self.lock:
            current = {k: v for k, v in self.current.items() if k != "session_started_unix"}
            if self.current.get("phase") == "session":
                current["session_elapsed_seconds"] = round(time.time() - self.current["session_started_unix"], 1)
            return {
                "format": 1, "state": self.state, "message": self.message, "pid": os.getpid(),
                "root": str(self.cfg.root), "formal": self.cfg.formal,
                "started_utc": self.started, "updated_utc": now(),
                "current": current,
                "sessions": {"completed": self.completed(), "total": self.total(),
                             "failed_among_run": self.failed_sessions, "run_including_repeats": self.sessions_run},
                "rounds": {u.uid: {"sessions": u.sessions, "attempts": self.attempts.get(u.uid, 0),
                                   "accepted_dir": str(self.accepted[u.uid]) if u.uid in self.accepted else None}
                           for u in self.units},
                "eta": self.eta(),
                **({"paper3": self.paper3_progress()} if self.cfg.paper3 is not None else {}),
            }

    def paper3_progress(self) -> dict:
        """Per square of this host: its rounds (the design's, not just this launch's prefix) and which are accepted.

        Confirmatory estimates use complete squares only (one complete square gives R = 4); the accepted rounds of an incomplete
        square are a sensitivity only; a host with no complete square is reported descriptively (design 3.2, lead's decision)."""
        setup = self.cfg.paper3
        squares = {}
        for plan in setup.rounds:
            squares.setdefault(str(plan.square), {"rounds": [r.round for r in setup.plan.rounds if r.square == plan.square], "accepted": []})
        for plan in setup.rounds:
            if f"main-r{plan.round}" in self.accepted:
                squares[str(plan.square)]["accepted"].append(plan.round)
        complete = sorted(int(sq) for sq, e in squares.items() if e["rounds"] == e["accepted"])
        return {"host": setup.plan.spec.host, "launch": setup.launch, "squares": squares, "squares_complete": complete,
                "confirmatory_rounds": sorted(r for sq in complete for r in squares[str(sq)]["rounds"]),
                "sensitivity_only_rounds": sorted(r for sq, e in squares.items() if int(sq) not in complete for r in e["accepted"]),
                "rule": "confirmatory estimates use complete squares only; one complete square gives R = 4; accepted rounds of an "
                        "incomplete square are a sensitivity; no complete square: the host is reported descriptively"}

    def flush(self):
        with self.lock:
            write_json(self.path, self.snapshot())

    def update(self, **fields):
        with self.lock:
            for key, value in fields.items():
                setattr(self, key, value)
            self.flush()

    def set_current(self, **fields):
        with self.lock:
            self.current = fields
            self.flush()

    def beat(self):
        while not self.stop.wait(30):
            try:
                self.flush()
            except OSError:
                pass

    def close(self, state: str, message: str = ""):
        self.stop.set()
        self.update(state=state, message=message, current={})


# ---- running one attempt ----------------------------------------------------------------------------

def job_meta(job: Job, cfg: Config, attempt: Path) -> dict:
    return {"key": job.key, "suite": job.suite, "arm": job.arm, "out_rel": str(job.out_rel), "scales": job.scales,
            "engines": job.engines, "engine_order": job.order, "expected_sessions": job.sessions,
            "command": job_command(job, attempt / job.out_rel, cfg), "state": "pending"}


def read_attempt(path: Path):
    return read_json(path / "attempt.json")


def classify(path: Path) -> dict:
    """On-disk state of one attempt directory: pass | fail | ungated | resumable | abandoned."""
    gates = read_json(path / "gates.json")
    if gates and gates.get("verdict") in ("pass", "fail"):
        return {"state": gates["verdict"]}
    meta = read_attempt(path)
    if meta is None:
        return {"state": "abandoned", "reason": "no readable attempt.json"}
    jobs = meta.get("jobs", [])
    if jobs and all(job.get("state") == "finished" for job in jobs):
        return {"state": "ungated"}
    if all(job.get("state") == "finished" or not (path / job["out_rel"]).exists() for job in jobs):
        return {"state": "resumable"}
    return {"state": "abandoned", "reason": "a suite run was interrupted part-way; its output root cannot be reused"}


def list_attempts(unit: Unit) -> list:
    return [(i, unit.attempt_path(i)) for i in range(MAX_ATTEMPTS) if unit.attempt_path(i).exists()]


class Campaign:
    def __init__(self, cfg: Config, units: list, status: Status, design_hash: str, args):
        self.cfg, self.units, self.status, self.args = cfg, units, status, args
        self.design_hash = design_hash
        self.sleep = time.sleep                 # tests replace it
        self.last_finished = None               # UTC time the last suite run ended (this invocation or an earlier one)

    def cool_down(self):
        """Paper 3: at least the host class's cool-down of no load since the last suite run ended (section 3.2). It is 0 on the hosts
        with the section 2.2 idle brackets, which leave >= 150 s without query load between sessions, and 60 s on the time-only C-VM,
        which has none. The first suite of a campaign has nothing to cool down from; a resume after a long stop waits for nothing."""
        setup = self.cfg.paper3
        if setup is None or not setup.plan.spec.cooldown_s or self.last_finished is None:
            return
        wait = setup.plan.spec.cooldown_s - (datetime.now(timezone.utc) - self.last_finished).total_seconds()
        if wait > 0:
            log(f"cool-down: {wait:.0f} s without load before the next suite run")
            self.status.set_current(phase="cooldown", seconds=round(wait, 1))
            self.sleep(wait)

    # -- one suite
    def run_job(self, unit: Unit, index: int, path: Path, meta: dict, job_record: dict, job: Job):
        out = path / job.out_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        log_path = path / (job.key.replace("/", "--") + ".run.log")
        job_record.update(state="running", started_utc=now())
        write_json(path / "attempt.json", meta)
        command = job_record["command"]
        log(f"{unit.uid} attempt {index}: {job.key} ({job.sessions} sessions) -> {out}")
        env = os.environ.copy()
        with log_path.open("a") as sink:
            sink.write("$ " + " ".join(command) + "\n")
            process = subprocess.Popen(command, cwd=HERE, env=env, text=True, bufsize=1,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            try:
                for line in process.stdout:
                    sink.write(line)
                    sink.flush()
                    if line.startswith('{"event"'):
                        self.on_event(unit, index, path, job, line)
                returncode = process.wait()
            except BaseException:
                self.stop_child(process)
                job_record.update(state="interrupted", interrupted_utc=now())
                write_json(path / "attempt.json", meta)
                raise
        job_record.update(state="finished", finished_utc=now(), returncode=returncode, log=str(log_path))
        self.last_finished = datetime.now(timezone.utc)
        write_json(path / "attempt.json", meta)
        if returncode != 0:
            log(f"{job.key}: run_experiment.py exited {returncode}; see {log_path}")

    @staticmethod
    def stop_child(process):
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def on_event(self, unit, index, path, job, line):
        try:
            event = json.loads(line)
        except ValueError:
            return
        kind, scale, engine = event.get("event"), str(event.get("scale")), event.get("engine")
        st = self.status
        with st.lock:
            if kind == "engine_started":
                st.set_current(phase="session", uid=unit.uid, round=unit.round, side_study=unit.kind == "side",
                               attempt=index, attempt_dir=str(path), job=job.key, suite=job.suite, arm=job.arm,
                               scale=scale, engine=engine, engine_order=job.order,
                               session_started_utc=now(), session_started_unix=time.time())
            elif kind in ("engine_complete", "engine_failed"):
                st.finished.setdefault(unit.uid, set()).add((job.key, scale, engine))
                st.sessions_run += 1
                if kind == "engine_failed":
                    st.failed_sessions += 1
                st.add_history(job, unit.kind, event)
                st.set_current(phase="between_sessions", uid=unit.uid, round=unit.round, attempt=index,
                               attempt_dir=str(path), job=job.key, suite=job.suite, arm=job.arm,
                               last_session=f"{scale}/{engine}", last_status=kind)

    # -- gates
    def gate(self, unit: Unit, index: int, path: Path):
        meta = read_attempt(path)
        self.status.set_current(phase="gate", uid=unit.uid, round=unit.round, attempt=index, attempt_dir=str(path))
        conformance = conformance_problems(unit, path, self.cfg)
        run_status = {j["key"]: {"returncode": j.get("returncode"), "started_utc": j.get("started_utc"),
                                 "finished_utc": j.get("finished_utc")} for j in meta["jobs"]}
        failed_runs = [k for k, v in run_status.items() if v["returncode"] != 0]
        gates = {"unit": unit.uid, "round": unit.round, "attempt": index, "attempt_dir": str(path),
                 "side_study": unit.kind == "side", "decided_at_utc": now(), "run": run_status,
                 "design_conformance": {"problems": conformance, "passed": not conformance}}
        if unit.kind == "main":
            report = self.run_preflight(unit, path)
            gates["preflight"] = report
            verdict = "pass" if report["verdict"] == "pass" else "fail"
            reasons = [f"preflight verdict {report['verdict']}"] if verdict == "fail" else []
            if self.cfg.paper3 is not None:
                summary = self.run_paper3_gates(unit, path)
                gates["paper3"] = summary
                if summary.get("round_failed"):
                    verdict = "fail"
                    reasons.append(f"Paper 3 gate(s) failed the round: {summary['round_failures'][:5]}")
        else:
            verdict, reasons = "pass", []
            gates["preflight"] = None
            reasons += side_study_problems(unit, path, self.cfg)
            if reasons:
                verdict = "fail"
        if failed_runs:
            verdict = "fail"
            reasons.append(f"run_experiment.py exited non-zero for {failed_runs}")
        if conformance:
            verdict = "fail"
            reasons.append(f"{len(conformance)} design-conformance problem(s)")
        gates.update(verdict=verdict, reasons=reasons)
        write_json(path / "gates.json", gates)
        log(f"{unit.uid} attempt {index}: gate verdict {verdict.upper()} {reasons if reasons else ''}")
        return verdict

    def run_preflight(self, unit: Unit, path: Path) -> dict:
        output = next(path / name for name in ["preflight", *(f"preflight-{n}" for n in range(2, 100))]
                      if not (path / name).exists())
        prior = []
        for earlier in self.units:
            if earlier.kind == "main" and earlier.round < unit.round and earlier.uid in self.status.accepted:
                report = (read_json(self.status.accepted[earlier.uid] / "gates.json") or {}).get("preflight") or {}
                if report.get("report"):
                    prior.append(report["report"])
        command = gate_command(unit, path, self.cfg, output, prior)
        log_path = path / f"{output.name}.log"
        with log_path.open("w") as sink:
            sink.write("$ " + " ".join(command) + "\n")
            sink.flush()
            code = subprocess.run(command, cwd=HERE, stdout=sink, stderr=subprocess.STDOUT).returncode
        report_path = output / "preflight-report.json"
        report = read_json(report_path)
        if code not in (0, 1) or not isinstance(report, dict):
            raise CampaignError(
                f"preflight.py could not produce a verdict for {path} (exit {code}, report "
                f"{'ok' if report else 'missing'}); see {log_path}. The round is NOT counted as failed: "
                f"fix the tool and re-run to gate it again.", EXIT_GATE_TOOL)
        verdict = report.get("verdict") or ("pass" if report.get("ok") else "fail")
        if (code == 0) != (verdict == "pass"):
            raise CampaignError(f"preflight.py exit {code} contradicts report verdict {verdict!r} ({report_path})", EXIT_GATE_TOOL)
        failing = [{"id": c.get("id"), "detail": str(c.get("detail"))[:400], **({"kind": c["kind"]} if "kind" in c else {})}
                   for c in report.get("checks", []) if c.get("status") == "FAIL"]
        return {"command": command, "returncode": code, "report": str(report_path),
                "report_sha256": sha256_file(report_path), "verdict": verdict, "counts": report.get("counts"),
                "failing_checks": failing[:50], "failing_check_count": len(failing)}

    def capture_exclusivity(self, path: Path, label: str):
        """Host-exclusivity evidence (G12) at the start or end of an attempt; kept if it already exists (a resume or a re-gate)."""
        target = path / f"host-exclusivity-{label}.json"
        if not target.exists():
            write_json(target, paper3_gates.capture_host_exclusivity(label, gpu=self.cfg.paper3.plan.spec.kind == "gpu"))

    def run_paper3_gates(self, unit: Unit, path: Path) -> dict:
        """G6-G12 for one round attempt: writes paper3-gates.json beside gates.json, returns the summary gates.json keeps."""
        setup = self.cfg.paper3
        runs = {job.suite: path / job.out_rel for job in unit.jobs}
        absent = [s for s, root in runs.items() if not isinstance(read_json(root / "schedule.json"), list)]
        if absent:
            return {"evaluated": False, "round_failed": False,
                    "reason": f"schedule.json missing for {absent}; the design-conformance check reports the round"}
        self.capture_exclusivity(path, "end")
        captures = [read_json(path / f"host-exclusivity-{label}.json") for label in ("start", "end")]
        try:
            report = paper3_gates.evaluate_round(runs, setup.host, captures)
        except paper3_gates.GateConfigError as exc:
            if self.cfg.formal:
                raise CampaignError(f"Paper 3 gates cannot be evaluated: {exc}. The round is NOT counted as failed: fix the "
                                    "host config and re-run to gate it again.", EXIT_GATE_TOOL)
            return {"evaluated": False, "round_failed": False, "reason": str(exc)}
        write_json(path / "paper3-gates.json", report)
        return {"evaluated": True, "report": str(path / "paper3-gates.json"), "report_sha256": sha256_file(path / "paper3-gates.json"),
                "round_failed": report["round_failed"], "round_failures": report["round_failures"][:50],
                "gates": {g: {k: v for k, v in c.items() if k != "consequence"} for g, c in report["gates"].items()},
                "ineligible_keys": len(report["ineligible"])}

    # -- one attempt (new or resumed), then its gate
    def run_attempt(self, unit: Unit, index: int, path: Path, resume: bool):
        if resume:
            meta = read_attempt(path)
            meta.setdefault("resumed_utc", []).append(now())
        else:
            path.mkdir(parents=True, exist_ok=False)
            meta = {"unit": unit.uid, "round": unit.round, "attempt": index, "created_utc": now(),
                    "design_sha256": self.design_hash, "jobs": [job_meta(job, self.cfg, path) for job in unit.jobs]}
            write_json(path / "attempt.json", meta)
        self.status.finished[unit.uid] = {
            (j["key"], scale, engine) for j in meta["jobs"] if j.get("state") == "finished"
            for scale in j["scales"] for engine in j["engines"]}
        self.status.attempts[unit.uid] = index + 1
        by_key = {job.key: job for job in unit.jobs}
        if self.cfg.paper3 is not None:
            self.capture_exclusivity(path, "start")
        for record in meta["jobs"]:
            if record.get("state") != "finished":
                self.cool_down()
                self.run_job(unit, index, path, meta, record, by_key[record["key"]])
        meta["finished_utc"] = now()
        write_json(path / "attempt.json", meta)
        self.gate(unit, index, path)

    def drive(self, unit: Unit) -> str:
        """Bring one round to an accepted (passing) attempt, or stop the campaign. State comes from disk."""
        st = self.status
        while True:
            attempts = list_attempts(unit)
            info = [classify(path) for _, path in attempts]
            st.attempts[unit.uid] = len(attempts)
            passed = [path for (_, path), item in zip(attempts, info) if item["state"] == "pass"]
            if passed:
                st.accepted[unit.uid] = passed[-1]
                st.flush()
                return "pass"
            failures = sum(1 for item in info if item["state"] == "fail")
            if failures >= 2:
                raise CampaignError(f"{unit.uid}: the round and its repeat both failed their integrity gates "
                                    f"({[str(p) for _, p in attempts]}); stopping the campaign", EXIT_STOPPED)
            last_state = info[-1]["state"] if info else None
            if last_state == "ungated":
                self.gate(unit, attempts[-1][0], attempts[-1][1])
            elif last_state == "resumable":
                self.run_attempt(unit, attempts[-1][0], attempts[-1][1], resume=True)
            else:
                if last_state == "abandoned":
                    index, path = attempts[-1]
                    note = {"abandoned_utc": now(), "reason": info[-1].get("reason"),
                            "note": "kept as evidence; the round is redone in a new directory"}
                    write_json(path / "abandoned.json", note)
                    log(f"{unit.uid}: {path.name} was interrupted ({note['reason']}); starting a repeat")
                elif last_state == "fail":
                    log(f"{unit.uid}: {attempts[-1][1].name} failed its gate; repeating the round once")
                new_index = (attempts[-1][0] + 1) if attempts else 0
                self.run_attempt(unit, new_index, unit.attempt_path(new_index), resume=False)

    def note_last_finish(self, path: Path):
        """Paper 3 cool-down after a resume: the latest end of a suite run found on disk."""
        for job in (read_attempt(path) or {}).get("jobs", []):
            for key in ("finished_utc", "interrupted_utc"):
                try:
                    stamp = datetime.fromisoformat(job[key]) if job.get(key) else None
                except ValueError:
                    stamp = None
                if stamp is not None and (self.last_finished is None or stamp > self.last_finished):
                    self.last_finished = stamp

    def scan(self):
        """Pick up what earlier invocations left on disk: accepted rounds and session durations for the ETA."""
        st = self.status
        for unit in self.units:
            attempts = list_attempts(unit)
            st.attempts[unit.uid] = len(attempts)
            for _, path in attempts:
                st.load_history(unit, path)
                if self.cfg.paper3 is not None:
                    self.note_last_finish(path)
            passed = [path for _, path in attempts if classify(path)["state"] == "pass"]
            if passed:
                st.accepted[unit.uid] = passed[-1]

    def run(self) -> str:
        st = self.status
        self.scan()
        plan = [u for u in self.units if u.kind == "main"] + [u for u in self.units if u.kind == "side"]
        for unit in plan:
            if unit.kind == "side" and self.args.stop_after_round is not None:
                break
            unit.base.mkdir(parents=True, exist_ok=True)
            st.update(state="running", message=f"{unit.uid}")
            self.drive(unit)
            log(f"{unit.uid}: accepted {st.accepted[unit.uid]}")
            if (unit.kind == "main" and self.args.stop_after_round is not None
                    and unit.round >= self.args.stop_after_round):
                return "paused"
        return "complete"


# ---- gate helpers that do not need preflight --------------------------------------------------------

def conformance_problems(unit: Unit, path: Path, cfg: Config) -> list:
    """Was the registered design executed? Engine order per scale and session completeness, from schedule.json."""
    problems = []
    for job in unit.jobs:
        schedule = read_json(path / job.out_rel / "schedule.json")
        if not isinstance(schedule, list):
            problems.append(f"{job.key}: schedule.json missing or unreadable")
            continue
        seen_scales = []
        for scale in job.scales:
            items = [i for i in schedule if str(i.get("scale")) == scale]
            if not items:
                problems.append(f"{job.key}: no sessions scheduled at scale {scale}")
                continue
            seen_scales.append(scale)
            if [i.get("engine") for i in items] != job.order:
                problems.append(f"{job.key} sf{scale}: engine order {[i.get('engine') for i in items]} != planned {job.order}")
            for item in items:
                if item.get("status") != "complete" or item.get("returncode") != 0:
                    problems.append(f"{job.key} sf{scale} {item.get('engine')}: status {item.get('status')!r}, "
                                    f"returncode {item.get('returncode')}, failure_reason {item.get('failure_reason')!r}")
        order = [str(i.get("scale")) for i in schedule]
        if [scale for scale, _ in itertools.groupby(order)] != [s for s in job.scales if s in seen_scales]:
            problems.append(f"{job.key}: scales did not run in ascending design order")
    if cfg.paper3 is not None and len(unit.jobs) > 1:   # suite order alternates by round (section 3.2); preflight --round cannot check Paper 3 rounds
        firsts = []
        for job in unit.jobs:
            schedule = read_json(path / job.out_rel / "schedule.json")
            stamp = None
            if isinstance(schedule, list) and schedule and isinstance(schedule[0], dict):
                try:
                    stamp = datetime.fromisoformat(str(schedule[0].get("start_utc")))
                except ValueError:
                    stamp = None
            firsts.append(stamp if stamp is not None and stamp.tzinfo is not None else None)
        if None in firsts or any(a >= b for a, b in zip(firsts, firsts[1:])):
            problems.append(f"suites did not start in the planned order {[j.suite for j in unit.jobs]} (first start_utc {firsts})")
    return problems[:60]


def side_study_problems(unit: Unit, path: Path, cfg: Config) -> list:
    """Side study integrity: every session complete (conformance covers it) and the arm's Polars version is right."""
    problems = []
    arm_version = {arm_id: version for _, arm_id, version in SIDE_ARMS}
    for job in unit.jobs:
        for scale in job.scales:
            session = read_json(path / job.out_rel / scale_dir_name(scale) / "polars" / "results.json") or {}
            if cfg.formal and session.get("version") != arm_version[job.arm]:
                problems.append(f"{job.key} sf{scale}: ran polars {session.get('version')!r}, arm needs {arm_version[job.arm]!r}")
            if session.get("failure") or not session.get("completed_at_utc"):
                problems.append(f"{job.key} sf{scale}: session did not finish cleanly")
            if session.get("query_errors"):
                problems.append(f"{job.key} sf{scale}: query errors {[e.get('query') for e in session['query_errors']][:5]}")
    return problems[:60]


# ---- dry run ----------------------------------------------------------------------------------------

def print_schedule_paper3(cfg: Config, units: list, design: dict, verbose: bool) -> None:
    setup, block = cfg.paper3, design["paper3"]
    spec = setup.plan.spec
    print(f"Paper 3 campaign ({paper3_campaign.DESIGN_DOC_REL}): host {spec.host} ({spec.kind}), launch {setup.launch}, "
          f"lock date {setup.lock['date_utc']} -> seed {design['seed']}")
    print(f"letters {design['letters']}" + (f" (from the four-engine assignment {block['letters_all']})" if spec.kind != "gpu" else "")
          + f"; rows {block['rows']}")
    if spec.vm_pin:
        print(f"pin: {block['vm_pin']}")
    print(f"formal: {cfg.formal}" + (f"  (test flags: {', '.join(cfg.test_flags)})" if cfg.test_flags else ""))
    print(f"engines: {design['engines']}   flags: {design['flags']}")
    print(f"energy mode: {design['energy']}   telemetry: {design['telemetry']}   cool-down: {spec.cooldown_s:g} s")
    print(f"t_min: {setup.host.t_min_s!r}   host config unset: {setup.host.unset() or 'nothing'}")
    print("full design (both squares):")
    for entry in block["schedule"]:
        print(f"  round {entry['round']} (square {entry['square']}, row {entry['row']})  {entry['sequence']}  "
              f"{' -> '.join(entry['engine_order'])}  suites {' then '.join(entry['suite_order'])}")
    print(f"\nTHIS LAUNCH: rounds {block['launch_rounds']}, {sum(u.sessions for u in units)} sessions "
          f"({block['sessions_per_round']} per round)")
    for unit in units:
        print(f"  round {unit.round}  {unit.label}  {unit.attempt_path(0)}  ({unit.sessions} sessions)")
        for number, job in enumerate(unit.jobs, 1):
            print(f"    {number}. {job.suite}: SF {', '.join(job.scales)} x engines {' -> '.join(job.order)}  = {job.sessions} sessions")
            if verbose:
                for scale in job.scales:
                    print(f"         SF{scale}: " + " -> ".join(job.order))
    first = next(iter(units), None)
    if first:
        job = first.jobs[0]
        print("\nexample command (" + f"{first.uid} {job.key}" + "):\n  "
              + " ".join(job_command(job, Path(f"<root>/{first.name}") / job.out_rel, cfg)))
        print("gate command:\n  " + " ".join(gate_command(first, Path(f"<root>/{first.name}"), cfg, Path(f"<root>/{first.name}/preflight"))))


def print_schedule(cfg: Config, units: list, design: dict, verbose: bool) -> None:
    if cfg.paper3 is not None:
        return print_schedule_paper3(cfg, units, design, verbose)
    print(f"Pre-registered design: seed {SEED}, letters {design['letters']}, sequences {design['sequences']}")
    print("Latin square check: each engine once per position, each ordered pair adjacent exactly once: OK")
    print(f"formal: {cfg.formal}" + (f"  (test flags: {', '.join(cfg.test_flags)})" if cfg.test_flags else ""))
    print(f"flags: {design['flags']}   engines: {design['engines']}")
    if "energy" in design:
        print(f"Paper 3 energy mode: {design['energy']}")
    for spec_name, spec in design["suites"].items():
        print(f"suite {spec_name}: SF {', '.join(spec['scales'])}, query cleanup {spec['query_cleanup']}")
    for kind, title in (("main", "MAIN ROUNDS"), ("side", "SIDE STUDY (polars 1.44.2 vs 2.0.0)")):
        group = [u for u in units if u.kind == kind]
        if not group:
            continue
        print(f"\n{title}: {len(group)} round(s), {sum(u.sessions for u in group)} sessions")
        for unit in group:
            where = unit.attempt_path(0)
            print(f"  round {unit.round}  {unit.label}  {where}  ({unit.sessions} sessions)")
            for number, job in enumerate(unit.jobs, 1):
                arm = f"[{job.arm}] " if job.arm else ""
                print(f"    {number}. {arm}{job.suite}: SF {', '.join(job.scales)} x engines "
                      f"{' -> '.join(job.order)}  = {job.sessions} sessions")
                if verbose:
                    for scale in job.scales:
                        print(f"         SF{scale}: " + " -> ".join(job.order))
    print(f"\nTOTAL: {design['totals']['main_sessions']} main + {design['totals']['side_sessions']} side = "
          f"{design['totals']['sessions']} sessions")
    first = next((u for u in units), None)
    if first:
        job = first.jobs[0]
        sample = job_command(job, Path(f"<root>/{first.name}") / job.out_rel, cfg)
        print("\nexample command (" + f"{first.uid} {job.key}" + "):\n  " + " ".join(sample))
        if first.kind == "main":
            print("gate command:\n  " + " ".join(gate_command(first, Path(f"<root>/{first.name}"), cfg, Path(f"<root>/{first.name}/preflight"))))


# ---- main -------------------------------------------------------------------------------------------

def env(name):
    """An environment variable, with unset and empty both meaning 'not supplied'."""
    return os.environ.get(name) or None


def positive_number(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}")
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a finite number above 0, got {text!r}")
    return value


def non_negative_number(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}")
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"must be a finite number of at least 0, got {text!r}")
    return value


def http_url(text):
    if not text.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError(f"must start with http:// or https://, got {text!r}")
    return text


def utc_timestamp(text):
    """ISO-8601 with an explicit offset (or Z), normalised to UTC; a naive time would silently mean 'local'."""
    candidate = text.strip()
    if candidate[-1:] in ("Z", "z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO-8601 timestamp: {text!r}")
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"needs an explicit UTC offset or Z: {text!r}")
    return parsed.astimezone(timezone.utc).isoformat()


def cost_block(args, host_label):
    """The optional campaign.json "cost" block, or None when no cost field was supplied. Missing fields are null.

    Paper 3 launches add instance_id, launch_utc and credit_funded when given (section 2.6, P-BILLED); the terminate time is
    not known when campaign.json is written, so the operator's launch record carries it."""
    extra = {name: getattr(args, "cost_" + name, None) for name in PAPER3_COST_FIELDS} if getattr(args, "paper3_host", None) else {}
    values = {"instance_type": args.cost_instance_type or None, "provider": args.cost_provider,
              "region": args.cost_region, "hourly_price_usd": args.cost_hourly_price_usd,
              "price_source_url": args.cost_price_source_url, "price_snapshot_utc": args.cost_price_snapshot_utc}
    if all(value is None for value in values.values()) and not any(extra.values()):
        return None
    if values["instance_type"] is None:
        values["instance_type"] = host_label
    return {**{name: values[name] for name in COST_FIELDS}, **{name: v for name, v in extra.items() if v is not None}}


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, help="campaign directory (created on first run, resumed afterwards)")
    parser.add_argument("--data-root", type=Path, default=os.environ.get("DATA_ROOT"))
    parser.add_argument("--jar", type=Path, default=os.environ.get("RAPIDS_JAR"))
    parser.add_argument("--a100compat-env", default=os.environ.get("PYTHON_A100COMPAT_ENV"),
                        help="venv directory (or python binary) for the Polars 2.0.0 side-study arm")
    parser.add_argument("--host-label", default=os.environ.get("CAMPAIGN_HOST_LABEL"),
                        help="instance type for campaign.json, e.g. gpu_1x_h100_sxm5 (not discoverable from inside the VM)")
    parser.add_argument("--inputs-report", type=Path, nargs="+",
                        help="verify_generated_inputs.py report(s), passed to the gate and hashed into campaign.json")
    parser.add_argument("--allow-any-gpu", action="store_true",
                        help="host other than an H100 (amendment A2); recorded in the design, passed to preflight.py")
    parser.add_argument("--dry-run", action="store_true", help="print the resolved schedule and exit; writes nothing")
    parser.add_argument("--verbose", action="store_true", help="with --dry-run: list the engine order of every scale")
    parser.add_argument("--stop-after-round", type=int, choices=range(1, 9), metavar="K",
                        help="pause after main round K passes (for the integrity review); does not mark the "
                             "campaign informal. Resume by running again. Rounds 1-4 (Paper 3: 1-8, by the design's round numbers)")
    parser.add_argument("--engine-timeout-seconds", type=float, default=None,
                        help="pass through to run_experiment.py (default: none, as in the design)")
    parser.add_argument("--include-hostname", action="store_true",
                        help="record the raw hostname in campaign.json (default: only a salted hash, see host_id_sha256)")
    parser.add_argument("--accept-design-doc-change", action="store_true",
                        help="resume although docs/H100_EXPERIMENT_DESIGN.md changed (e.g. a section 11 amendment); "
                             "recorded in invocations.jsonl")
    cost = parser.add_argument_group(
        "cost (optional; recorded in campaign.json as the \"cost\" block, absent when none is given)",
        "Each flag has an environment fallback, e.g. CAMPAIGN_COST_PROVIDER. The price is a fact you looked up: "
        "nothing is filled in automatically.")
    cost.add_argument("--cost-instance-type", default=env("CAMPAIGN_COST_INSTANCE_TYPE"),
                      help="billed instance type (default: --host-label)")
    cost.add_argument("--cost-provider", default=env("CAMPAIGN_COST_PROVIDER"), help="e.g. lambda-labs, aws")
    cost.add_argument("--cost-region", default=env("CAMPAIGN_COST_REGION"))
    cost.add_argument("--cost-hourly-price-usd", type=positive_number, default=env("CAMPAIGN_COST_HOURLY_PRICE_USD"),
                      help="on-demand USD per instance-hour at the time of the campaign")
    cost.add_argument("--cost-price-source-url", type=http_url, default=env("CAMPAIGN_COST_PRICE_SOURCE_URL"),
                      help="page the price was read from")
    cost.add_argument("--cost-price-snapshot-utc", type=utc_timestamp, default=env("CAMPAIGN_COST_PRICE_SNAPSHOT_UTC"),
                      help="when the price was read, ISO-8601 with an explicit offset or Z")
    instrument = parser.add_argument_group(
        "power telemetry (optional; passed to run_experiment.py only when given, see its --help for the defaults)")
    instrument.add_argument("--power-telemetry", choices=["auto", "off"], dest="power_telemetry")
    instrument.add_argument("--redact-gpu-identifiers", choices=["on", "off"], dest="redact_gpu_identifiers",
                            help="run_experiment.py default is on: environment.json stores nvidia-smi -q without the GPU "
                                 "UUID, serial number and PCI bus id")
    instrument.add_argument("--gpu-energy", choices=["auto", "off"], dest="gpu_energy",
                            help="per-record NVML GPU energy delta read in the workers")
    instrument.add_argument("--power-interval-ms", type=positive_number, dest="power_interval_ms")
    instrument.add_argument("--ipmi-interval-seconds", type=positive_number, dest="ipmi_interval_seconds")
    instrument.add_argument("--idle-baseline-seconds", type=non_negative_number, dest="idle_baseline_seconds",
                            help="idle window before every session; 0 disables it")
    instrument.add_argument("--cpu-energy", choices=power_telemetry.CPU_ENERGY_MODES, dest="cpu_energy",
                            help="auto (run_experiment.py default): refuse to start when RAPL/amd_energy counters exist but "
                                 "are unreadable (fix: ./setup_h100.sh --power-only --enable-rapl-read); absent counters are "
                                 "recorded as unavailable. require: refuse unless a CPU energy source works (CPU-only metal). "
                                 "off: no check")
    instrument.add_argument("--gpu-energy-scope", choices=power_telemetry.GPU_ENERGY_SCOPES, dest="gpu_energy_scope",
                            help="Paper 3 energy mode: what this host class's GPU energy counter measures, declared and never "
                                 "inferred (board: S1-counter; module: S1-module on GH200; none: CPU-only host). Recorded in the "
                                 "design and in campaign.json; absent = Paper 1 behaviour")
    instrument.add_argument("--energy-window-s", type=positive_number, dest="energy_window_s", metavar="SECONDS",
                            help="Paper 3 window rule (needs --gpu-energy-scope): measured trials become windows of n back-to-back "
                                 "runs, n = clamp(ceil(SECONDS / t_hat), 1, 50); absent = one run per trial")
    pin = instrument.add_mutually_exclusive_group()
    pin.add_argument("--socket", type=int, metavar="N", dest="pin_socket",
                     help="pin every engine process to the CPUs of socket N (CPU-only bare metal); the resolved CPU list is "
                          "recorded in campaign.json (\"pinning\") and every session must report that affinity")
    pin.add_argument("--cpu-list", metavar="LIST", dest="pin_cpu_list",
                     help="pin every engine process to exactly these CPUs, e.g. 0-25 or 0-47,96-143")
    instrument.add_argument("--no-membind", action="store_true",
                            help="with --socket/--cpu-list: do not bind memory to the NUMA nodes of the pinned CPUs. Default: "
                                 "bind (set_mempolicy, else numactl) and refuse to start when neither works. Recorded in the "
                                 "design and in campaign.json's pinning block")
    paper3 = parser.add_argument_group(
        "Paper 3 campaign mode (docs/PAPER3_ENERGY_DESIGN.md; absent = the Paper 1 campaign, unchanged)",
        "One host class per campaign root. GPU hosts run R = 8 rounds as two Williams squares on two launches (--paper3-launch 1 "
        "= rounds 1-4, 2 = rounds 5-8, each with its own --root and lease); the letters come from the lock-date seed. "
        "The host class fixes the engines, scales, GPU-energy scope and window (10 s); see paper3_campaign.py.")
    paper3.add_argument("--paper3-host", choices=sorted(paper3_campaign.HOSTS), help="host class of this campaign")
    paper3.add_argument("--paper3-launch", choices=paper3_campaign.LAUNCHES,
                        help="1: first square (rounds 1-4); 2: second square (rounds 5-8); both: all eight rounds on one "
                             "instance (the design runs the squares on separate launches where capacity allows)")
    paper3.add_argument("--paper3-lock-date", metavar="YYYYMMDD", help="date of the design lock (UTC); its integer is the seed")
    paper3.add_argument("--paper3-lock-commit", metavar="HASH", help="commit of the lock (tag paper3-design-lock); formal campaigns verify it")
    paper3.add_argument("--paper3-lock-doi", metavar="DOI", help="Zenodo DOI of the locked design, recorded in campaign.json")
    paper3.add_argument("--paper3-host-config", type=Path, default=PAPER3_HOSTS_CONFIG, metavar="JSON",
                        help="per-host t_min and probe-declared sources for the gates (default: paper3_hosts.json)")
    paper3.add_argument("--cost-instance-id", default=env("CAMPAIGN_COST_INSTANCE_ID"), help="provider instance id (section 2.6 P-BILLED)")
    paper3.add_argument("--cost-launch-utc", type=utc_timestamp, default=env("CAMPAIGN_COST_LAUNCH_UTC"),
                        help="launch time, ISO-8601 with an explicit offset or Z")
    paper3.add_argument("--cost-credit-funded", choices=["yes", "no"], default=env("CAMPAIGN_COST_CREDIT_FUNDED"),
                        help="whether the provider bills this instance against credits")
    lease = parser.add_argument_group(
        "lease (a campaign refuses to start without a valid one)",
        "Instance name p3-<purpose>-owner-<id>-expires-<YYYYMMDDTHHMMZ>, expiry in the future (docs/PAPER3_INFRA.md section 5).")
    lease.add_argument("--lease-name", help=f"the lease name (default: first line of {LEASE_FILE})")
    lease.add_argument("--lease-min-minutes", type=non_negative_number, default=0,
                       help="require at least this many minutes before the expiry at start (default 0: only 'not expired')")
    test = parser.add_argument_group("test-only flags (any of these marks campaign.json formal=false)")
    test.add_argument("--rounds", type=int, choices=range(1, 5), metavar="N", help="run only the first N rounds (and N side-study rounds)")
    test.add_argument("--only-side-study", action="store_true")
    test.add_argument("--scales-override", nargs="+", metavar="SF", help="use these scales for both suites")
    test.add_argument("--engines-override", nargs="+", choices=ENGINES, metavar="ENGINE")
    test.add_argument("--cpus-override", type=int)
    test.add_argument("--driver-memory-override")
    test.add_argument("--no-lease-check", action="store_true",
                      help="start without a lease (local tests only; recorded in campaign.json)")
    args = parser.parse_args(argv)
    if args.no_membind and args.pin_socket is None and args.pin_cpu_list is None:
        parser.error("--no-membind needs --socket or --cpu-list: memory is only bound in a pinned run")
    if args.energy_window_s is not None and args.gpu_energy_scope is None and args.paper3_host is None:
        parser.error("--energy-window-s needs an explicit --gpu-energy-scope {board,module,none}: the scope is declared, never inferred")
    if args.gpu_energy_scope in ("board", "module") and args.gpu_energy == "off":
        parser.error(f"--gpu-energy-scope {args.gpu_energy_scope} needs the counter: drop --gpu-energy off")
    check_paper3_args(parser, args)
    return args


PAPER3_FLAGS = ("paper3_launch", "paper3_lock_date", "paper3_lock_commit", "paper3_lock_doi",
                "cost_instance_id", "cost_launch_utc", "cost_credit_funded")


def check_paper3_args(parser, args) -> None:
    """Usage errors of the Paper 3 flags; a Paper 1 invocation only fails here if it uses one of them or asks for round 5-8."""
    used = [flag for flag in PAPER3_FLAGS if getattr(args, flag) is not None]
    if args.paper3_host is None:
        if used:
            parser.error(f"--{used[0].replace('_', '-')} belongs to Paper 3 campaign mode: add --paper3-host")
        if args.stop_after_round is not None and args.stop_after_round > 4:
            parser.error(f"argument --stop-after-round: invalid choice: {args.stop_after_round} (choose from 1, 2, 3, 4)")
        return
    for name, flag in (("paper3_launch", "--paper3-launch"), ("paper3_lock_date", "--paper3-lock-date")):
        if getattr(args, name) is None:
            parser.error(f"Paper 3 campaign mode needs {flag}")
    spec = paper3_campaign.HOSTS[args.paper3_host]
    if args.only_side_study:
        parser.error("--only-side-study is Paper 1's side study; Paper 3 has none")
    if (args.pin_socket is not None or args.pin_cpu_list is not None) and not spec.pinned:
        parser.error(f"--socket/--cpu-list: Paper 3 host {spec.host} takes no pin from the operator (c-gcp is given one; "
                     "c-vm reads its own from the VM's topology; section 3.1)")
    if spec.pinned and args.pin_socket is None and args.pin_cpu_list is None:
        parser.error(f"Paper 3 host {spec.host} pins its engines to 26 vCPUs of socket 0 (section 3.1): pass --cpu-list")
    for key, wanted in spec.telemetry.items():  # what the host class fixes may be repeated, not changed
        given = getattr(args, key, None)
        if wanted is not None and given is not None and given != wanted:
            parser.error(f"--{key.replace('_', '-')} {given} conflicts with Paper 3 host {spec.host} ({wanted}): the host class fixes it")
    if spec.scope is None and args.gpu_energy_scope not in ("board", "module"):
        parser.error(f"Paper 3 host {spec.host} has no fixed GPU-energy scope (it comes from its own semantics step): "
                     "declare it with --gpu-energy-scope board|module")
    if args.engines_override and not set(args.engines_override) <= set(spec.engines):
        parser.error(f"--engines-override {args.engines_override}: Paper 3 host {spec.host} runs {list(spec.engines)}")


def resolve_paper3(args) -> Paper3Setup:
    """The Paper 3 plan, host entry and lock of this invocation (no I/O beyond the host config); CampaignError when the design is not met."""
    try:
        plan = paper3_campaign.build_plan(args.paper3_host, args.paper3_lock_date)
        rounds = paper3_campaign.launch_rounds(plan, args.paper3_launch)
        host = paper3_gates.load_host_config(args.paper3_host_config, args.paper3_host)
        digest = paper3_gates.host_entry_digest(args.paper3_host_config, args.paper3_host)
    except (paper3_campaign.PlanError, paper3_gates.GateConfigError) as exc:
        raise CampaignError(f"Paper 3 design: {exc}", EXIT_DESIGN)
    if args.rounds:
        rounds = rounds[:args.rounds]
    lock = {"date_utc": plan.lock_date, "seed": plan.seed, "tag": paper3_campaign.LOCK_TAG,
            "commit": args.paper3_lock_commit, "zenodo_doi": args.paper3_lock_doi}
    return Paper3Setup(plan=plan, launch=args.paper3_launch, rounds=rounds, lock=lock,
                       host_config=Path(args.paper3_host_config).resolve(), host=host, host_entry_sha256=digest)


def resolve_config(args) -> Config:
    paper3 = resolve_paper3(args) if getattr(args, "paper3_host", None) else None
    used = {"--rounds": args.rounds is not None, "--only-side-study": args.only_side_study,
            "--scales-override": args.scales_override is not None,
            "--engines-override": args.engines_override is not None,
            "--cpus-override": args.cpus_override is not None,
            "--driver-memory-override": args.driver_memory_override is not None,
            "--no-lease-check": args.no_lease_check}
    test_flags = [flag for flag, on in used.items() if on]
    scales = {suite: list(args.scales_override or spec["scales"]) for suite, spec in SUITES.items()}
    engines = list(args.engines_override or ENGINES)
    telemetry = {key: getattr(args, key) for key in TELEMETRY_FLAGS if getattr(args, key) is not None}
    if paper3 is not None:
        spec = paper3.plan.spec
        scales = {suite: list(args.scales_override or suite_scales) for suite, suite_scales in spec.suites.items()}
        engines = list(args.engines_override or spec.engines)
        # one cool-down number for the sessions of a suite run (run_experiment.py) and for the suite runs of a round (the campaign):
        # 0 on the hosts with the section 2.2 idle brackets (>= 150 s without query load, design 3.2, 2026-11-15); 60 s on the C-VM, which has none
        telemetry = {**telemetry, **{k: v for k, v in spec.telemetry.items() if v is not None},
                     "session_cooldown_seconds": spec.cooldown_s}
    return Config(
        root=args.root.resolve() if args.root else None,
        data_root=Path(args.data_root).resolve() if args.data_root else None,
        jar=Path(args.jar).resolve() if args.jar else None,
        side_python=resolve_side_python(args.a100compat_env),
        cpus=args.cpus_override or FORMAL_FLAGS["cpus"],
        driver_memory=args.driver_memory_override or FORMAL_FLAGS["driver_memory"],
        rounds=args.rounds or 4, only_side_study=args.only_side_study, scales=scales,
        engines=engines, formal=not test_flags, test_flags=test_flags,
        allow_any_gpu=args.allow_any_gpu or (paper3 is not None and paper3.plan.spec.kind == "gpu" and paper3.plan.spec.host != "h100"),
        engine_timeout_seconds=args.engine_timeout_seconds,
        inputs_reports=[p.resolve() for p in args.inputs_report or []], host_label=args.host_label,
        include_hostname=args.include_hostname, cost=cost_block(args, args.host_label),
        telemetry=telemetry,
        no_lease_check=args.no_lease_check, lease_name=args.lease_name, lease_min_minutes=args.lease_min_minutes,
        pin_socket=args.pin_socket, pin_cpu_list=args.pin_cpu_list, no_membind=args.no_membind, paper3=paper3)


def lock_root(root: Path):
    handle = (root / ".campaign.lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise CampaignError(f"another run_campaign.py already holds {root / '.campaign.lock'}", 1)
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def start_campaign_file(cfg: Config, design: dict, prov: dict, args) -> str:
    """Create campaign.json once; on resume require the same design and report drift. Returns the design hash."""
    path = cfg.root / "campaign.json"
    design_hash = hashlib.sha256(json.dumps(design, sort_keys=True).encode()).hexdigest()
    drift = []
    if path.exists():
        stored = read_json(path)
        if not isinstance(stored, dict):
            raise CampaignError(f"{path} exists but is unreadable; refusing to continue", EXIT_DESIGN)
        if stored.get("design") != json.loads(json.dumps(design)):
            raise CampaignError(f"{path} holds a different resolved design than this invocation "
                                f"(flags or test overrides changed). Use the original arguments, or a new --root.", EXIT_DESIGN)
        before = stored["provenance"]
        if before["design_doc_sha256"] != prov["design_doc_sha256"]:
            if not args.accept_design_doc_change:
                if cfg.paper3 is not None:
                    raise CampaignError("sections 1-11 of docs/PAPER3_ENERGY_DESIGN.md changed since the campaign started; after "
                                        "the lock only section 12 may grow (section 7.1). Re-run with --accept-design-doc-change "
                                        "only for a change you will report.", EXIT_DESIGN)
                raise CampaignError("docs/H100_EXPERIMENT_DESIGN.md changed since the campaign started. If this is a "
                                    "deliberate section 11 amendment, re-run with --accept-design-doc-change.", EXIT_DESIGN)
            drift.append("design_doc_sha256 (accepted)")
        if before.get("design_doc_full_sha256") != prov.get("design_doc_full_sha256"):
            drift.append({"design_doc_full_sha256_changed (section 12 amendments)": {"stored": before.get("design_doc_full_sha256"),
                                                                                    "this_invocation": prov.get("design_doc_full_sha256")}})
        if before["inputs"]["manifest_sha256"] != prov["inputs"]["manifest_sha256"]:
            raise CampaignError("input manifests changed since the campaign started; refusing to mix datasets", EXIT_DESIGN)
        if before["inputs"].get("inputs_reports_sha256") != prov["inputs"]["inputs_reports_sha256"]:
            raise CampaignError("the --inputs-report files changed since the campaign started", EXIT_DESIGN)
        if before["inputs"]["rapids_jar_sha256"] != prov["inputs"]["rapids_jar_sha256"]:
            raise CampaignError("the RAPIDS jar changed since the campaign started", EXIT_DESIGN)
        changed = sorted(k for k in set(before["source_sha256"]) | set(prov["source_sha256"])
                         if before["source_sha256"].get(k) != prov["source_sha256"].get(k))
        critical = [k for k in changed if k in CRITICAL_SOURCES or (cfg.paper3 is not None and k in PAPER3_CRITICAL_SOURCES)]
        if changed:
            drift.append({"source_files_changed_since_start": changed, "critical": critical})
        if critical:
            log(f"WARNING: run-critical source files changed since the campaign started: {critical}")
        stored_host, this_host = (before.get("host") or {}).get("host_id_sha256"), (prov.get("host") or {}).get("host_id_sha256")
        if stored_host and this_host and stored_host != this_host:
            drift.append({"host_id_differs_from_campaign_json": {"salt_created_this_invocation": cfg.host_salt_created}})
            log("WARNING: this invocation's host id differs from campaign.json: a different host, or the salt kept on the "
                f"original host is missing (created now: {cfg.host_salt_created}); the campaign must run on one host")
        if (stored.get("pinning") or {}).get("cpus") != (cfg.pinning or {}).get("cpus"):
            raise CampaignError(f"CPU pinning resolves to {(cfg.pinning or {}).get('cpus')!r} on this host but campaign.json "
                                f"recorded {(stored.get('pinning') or {}).get('cpus')!r}: the CPUs of a campaign must not "
                                "change (CPUs offlined, or a different host)", EXIT_DESIGN)
        for key, supplied in (("cost", cfg.cost), ("telemetry", cfg.telemetry or None)):
            if supplied != stored.get(key):  # campaign.json is written once: report, never rewrite
                drift.append({f"{key}_differs_from_campaign_json": {"stored": stored.get(key), "this_invocation": supplied}})
    else:
        record = {"design": design, "design_sha256": design_hash, "provenance": prov}
        if cfg.cost:
            record["cost"] = cfg.cost
        if cfg.telemetry:
            record["telemetry"] = cfg.telemetry
        if cfg.pinning:
            record["pinning"] = cfg.pinning
        if cfg.lease is not None:
            record["lease"] = cfg.lease
        if cfg.paper3 is not None:
            record["paper3"] = {"lock_evidence": cfg.paper3.lock_evidence, "host_config": cfg.paper3.host_config.name,
                                "host_entry_sha256": cfg.paper3.host_entry_sha256,
                                **({"vm_pin": cfg.paper3.vm_pin} if cfg.paper3.plan.spec.vm_pin else {})}
        with path.open("x") as handle:  # never overwrite
            handle.write(json.dumps(record, indent=2, default=str) + "\n")
    with (cfg.root / "invocations.jsonl").open("a") as handle:
        handle.write(json.dumps({"utc": now(), "argv": logged_argv(sys.argv), "pid": os.getpid(), "design_sha256": design_hash,
                                 "design_doc_sha256": prov["design_doc_sha256"], "git_commit": prov["git"]["commit"],
                                 "source_sha256": prov["source_sha256"], "drift": drift,
                                 **({"host_id_sha256": prov["host"]["host_id_sha256"]} if (prov.get("host") or {}).get("host_id_sha256") else {}),
                                 **({"lease": cfg.lease} if cfg.lease is not None else {}),
                                 **({"pinning": cfg.pinning} if cfg.pinning else {}),
                                 **({"paper3": {"host": cfg.paper3.plan.spec.host, "launch": cfg.paper3.launch,
                                                "lock_date": cfg.paper3.lock["date_utc"]}} if cfg.paper3 is not None else {}),
                                 **({"cost": cfg.cost} if cfg.cost else {}),
                                 **({"telemetry": cfg.telemetry} if cfg.telemetry else {})}, default=str) + "\n")
    return design_hash


def resolve_vm_pin(cfg: Config, dry_run: bool) -> None:
    """C-VM (design 3.1 "C-VM protocol"): pin to 13 physical cores + their SMT siblings = 26 vCPUs, read from the VM's own topology,
    and bind memory only if the VM reports more than one NUMA node. Sets the pin on the config before the design is resolved, so
    the resolved list is part of the design. A formal campaign refuses to start if the pin cannot be resolved; a test campaign
    warns and runs unpinned (recorded); a dry run only says it will be read."""
    setup = cfg.paper3
    if setup is None or not setup.plan.spec.vm_pin:
        return
    if dry_run:
        setup.vm_pin = {"resolved": False, "note": "read from the VM's topology when the campaign starts"}
        return
    try:
        pin = paper3_campaign.vm_pin()
    except paper3_campaign.PlanError as exc:
        if cfg.formal:
            raise CampaignError(f"C-VM pin cannot be resolved: {exc}", EXIT_DESIGN)
        log(f"WARNING: C-VM pin cannot be resolved ({exc}); this test campaign runs unpinned")
        setup.vm_pin = {"resolved": False, "reason": str(exc)}
        return
    cfg.pin_cpu_list, cfg.no_membind = pin["cpu_list"], not pin["bind_memory"]
    setup.vm_pin = {"resolved": True, **pin}
    if pin["caveat"]:
        log(f"WARNING: {pin['caveat']}")


def git_run(*args):
    return run_text(["git", "-C", str(HERE), *args], timeout=60)


def verify_paper3_lock(cfg: Config) -> None:
    """A formal Paper 3 campaign runs only under the lock (section 7.1): its commit, DOI and date are given, the tag says the same
    commit and date, and sections 1-11 of the design file are the locked ones. A test campaign skips this and says so (formal=false)."""
    setup = cfg.paper3
    if not cfg.formal:
        return
    for flag, value in (("--paper3-lock-commit", setup.lock["commit"]), ("--paper3-lock-doi", setup.lock["zenodo_doi"])):
        if not value:
            raise CampaignError(f"a formal Paper 3 campaign records the lock in campaign.json: pass {flag} (section 7.1)", EXIT_DESIGN)
    try:
        paper3_campaign.parse_lock_date(setup.lock["date_utc"], today=datetime.now(timezone.utc).date())
        setup.lock_evidence = paper3_campaign.verify_lock(setup.lock["date_utc"], setup.lock["commit"], git=git_run,
                                                          design_doc=PAPER3_DESIGN_DOC)
    except paper3_campaign.PlanError as exc:
        raise CampaignError(f"Paper 3 lock: {exc}", EXIT_DESIGN)


def on_signal(signum, _frame):
    raise Interrupted(f"signal {signum}")


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        cfg = resolve_config(args)
        resolve_vm_pin(cfg, args.dry_run)
        units = build_units(cfg)
        design = design_dict(cfg, units)
        if args.dry_run:
            cfg.data_root = cfg.data_root or Path("$DATA_ROOT")
            cfg.jar = cfg.jar or Path("$RAPIDS_JAR")
            print_schedule(cfg, units, design, args.verbose)
            return 0
        for name, value in (("--root", cfg.root), ("--data-root", cfg.data_root), ("--jar", cfg.jar)):
            if value is None:
                raise CampaignError(f"{name} is required (--data-root/--jar default to $DATA_ROOT/$RAPIDS_JAR)", 2)
        cfg.lease = check_lease(cfg)
        if cfg.lease["checked"]:
            log(f"lease valid until {cfg.lease['expires_utc']} ({cfg.lease['minutes_left_at_start']} minutes left)")
        else:
            log(f"WARNING: {cfg.lease['reason']}")
        if cfg.paper3 is not None:
            verify_paper3_lock(cfg)
        cfg.root.mkdir(parents=True, exist_ok=True)
        lock = lock_root(cfg.root)
        try:
            cfg.host_salt, cfg.host_salt_created = load_or_create_salt(cfg.root)
        except (OSError, ValueError) as exc:  # the host id is optional provenance: say so and go on without it
            log(f"WARNING: no host-id salt ({type(exc).__name__}: {exc}); campaign.json will carry no host_id_sha256")
        prov = provenance(cfg, units)
        startup_checks(cfg, units, prov)
        design_hash = start_campaign_file(cfg, design, prov, args)
    except CampaignError as exc:
        print(f"run_campaign.py: error: {exc}", file=sys.stderr)
        return exc.code

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    status = Status(cfg.root / "status.json", cfg, units)
    status.thread.start()
    campaign = Campaign(cfg, units, status, design_hash, args)
    try:
        outcome = campaign.run()
        status.close(outcome, "all rounds accepted" if outcome == "complete" else f"paused after round {args.stop_after_round}")
        log(f"campaign {outcome}")
        return 0
    except CampaignError as exc:
        status.close("stopped", str(exc))
        print(f"run_campaign.py: {exc}", file=sys.stderr)
        return exc.code
    except Interrupted as exc:
        status.close("interrupted", str(exc))
        log("interrupted; the running suite was stopped and is kept as evidence. Re-run to continue with a repeat.")
        return EXIT_INTERRUPTED
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
