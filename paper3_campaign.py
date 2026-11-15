#!/usr/bin/env python3
"""Paper 3 campaign schedule (docs/PAPER3_ENERGY_DESIGN.md sections 2.1, 3.1, 3.2, 7.1): hosts, seed, squares, orders.

Pure functions plus the git checks of the lock; run_campaign.py calls them only when --paper3-host is given, so Paper 1
campaigns never reach this module. Nothing here is guessed: a choice the design leaves open is derived by a stated rule
(the CPU-only letters, below) or is a constant named in the report.

Seed rule (section 3.2). seed = int(YYYYMMDD of the lock date). random.Random(seed).shuffle(
["polars", "polars_gpu", "spark_cpu", "spark_gpu"]) gives the engines of letters A, B, C, D in that order. The Williams square of
Paper 1 has rows ABDC / BCAD / CDBA / DACB. A GPU host runs R = 8 rounds as two squares: rounds 1-4 use rows 1, 2, 3, 4 and
rounds 5-8 the same rows in reverse order (4, 3, 2, 1). Suite order alternates (odd rounds baseline first); scales ascend.

CPU-only host (C-GCP): two engines, rounds AB BA BA AB | BA AB AB BA, in the same two halves (rounds 1-4 and 5-8). The two CPU
engines take A and B in the order their letters appear in the four-engine assignment of the same seed (lock 20261116: spark_cpu
is A and polars is B; seed 20261029: spark_cpu A, polars B). C-VM: the baseline suite at SF10, SF30, SF300 for two rounds with
the same letters and the orders AB, BA, time only, pinned to 13 physical cores and their SMT siblings (26 vCPUs) read from the
VM's own topology (vm_pin).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re

import power_telemetry

DESIGN_DOC_REL = "docs/PAPER3_ENERGY_DESIGN.md"
LOCK_TAG = "paper3-design-lock"
AMENDMENTS_HEADING = "## 12. Amendments"       # sections 1-11 are the text before this line (section 7.1: never edited after the lock)

SHUFFLE_BASE = ("polars", "polars_gpu", "spark_cpu", "spark_gpu")   # the list the design shuffles, in its order
CPU_ENGINES = ("polars", "spark_cpu")
ROWS = ("ABDC", "BCAD", "CDBA", "DACB")                            # Williams square, n = 4 (Paper 1 section 5)
SQUARE_ROW_INDEX = {1: (0, 1, 2, 3), 2: (3, 2, 1, 0)}              # row indices per square: the second square reverses the rows
CPU_ONLY_SEQUENCES = ("AB", "BA", "BA", "AB", "BA", "AB", "AB", "BA")
CVM_SEQUENCES = CPU_ONLY_SEQUENCES[:2]                             # AB, BA with the C-GCP letters (design 3.1 "C-VM protocol")
VM_CORES, VM_VCPUS = 13, 26                                        # the C-VM pin: 13 physical cores + SMT siblings = 26 vCPUs (design 3.1)
SUITE_SCALES = {"baseline": ("1", "10", "30", "100", "300"), "complex": ("30", "100", "300")}
CVM_SCALES = {"baseline": ("10", "30", "300")}
WINDOW_S = 10.0                                                    # section 2.1: the energy window target
COOLDOWN_S = 60.0                                                  # section 3.2 controls: >= 60 s between sessions
G8_PULSE_S = 3.0                                                   # section 4, G8
LAUNCHES = ("1", "2", "both")
LAUNCH_SQUARES = {"1": (1,), "2": (2,), "both": (1, 2)}

LAMBDA_GPU_EXPECTED_COUNTER = "nvml-ctypes:nvmlDeviceGetTotalEnergyConsumption"


class PlanError(ValueError):
    """The requested Paper 3 campaign does not match the pre-registered design."""


@dataclass(frozen=True)
class HostSpec:
    host: str
    kind: str                       # "gpu" | "cpu-metal" | "cpu-vm"
    instance_types: tuple           # what --host-label may say
    scope: str | None               # declared GPU energy scope of the host class; None: the operator declares it (B200)
    engines: tuple
    suites: dict                    # suite -> scales
    pinned: bool                    # engines pinned to 26 vCPUs on one socket (--socket / --cpu-list required)
    energy: bool                    # energy measured (G6-G12 apply); False: time only
    telemetry: dict                 # campaign telemetry keys -> run_experiment.py flag values for this host class
    sequences: tuple = ()           # CPU hosts: the letter orders of the full design (GPU hosts: from the seed)
    vm_pin: bool = False            # the pin is read from the VM's own topology at start (design 3.1 "C-VM protocol")
    cooldown_s: float = 0.0         # sleep between sessions and between suite runs. 0 on the hosts with the section 2.2 idle brackets, which
                                    # give >= 150 s without query load (design 3.2, 2026-11-15, lead's decision). The time-only C-VM has no
                                    # brackets, so section 3.2's >= 60 s cool-down applies to it literally (COOLDOWN_S, lead's decision)


def _spec(**kwargs) -> HostSpec:
    return HostSpec(**kwargs)


# idle_baseline_seconds 0: run_experiment.py's Paper 1 idle window before each session (default 30 s, no engine running) is not in
# the Paper 3 design: P_idle comes from the two engine-ready brackets of section 2.2, which are what section 9 costs.
# g8_pulse_*: the 3 s load pulse at the start and end of every session on a GPU host (G8; CPU and GPU engines, lead's decision 2026-11-15)
GPU_TELEMETRY = {"gpu_energy_scope": None, "energy_window_s": WINDOW_S, "idle_baseline_seconds": 0, "g8_pulse_seconds": G8_PULSE_S,
                 "g8_pulse_engines": "all"}


def _gpu(host, instance, scope):
    return _spec(host=host, kind="gpu", instance_types=(instance,), scope=scope, engines=SHUFFLE_BASE, suites=SUITE_SCALES,
                 pinned=False, energy=True, telemetry={**GPU_TELEMETRY, "gpu_energy_scope": scope})


HOSTS = {
    "a100": _gpu("a100", "gpu_1x_a100_sxm4", "board"),
    "h100": _gpu("h100", "gpu_1x_h100_sxm5", "board"),
    "gh200": _gpu("gh200", "gpu_1x_gh200", "module"),          # the counter covers the whole Grace-Hopper module (S1-module)
    # B200 is conditional (section 3.1): its scope comes from its own semantics step, so the operator declares it.
    "b200": _gpu("b200", "gpu_1x_b200_sxm6", None),
    "c-gcp": _spec(host="c-gcp", kind="cpu-metal",
                   instance_types=("c3-highmem-192-metal", "c3-standard-192-metal", "c3-highcpu-192-metal", "m7i.metal-24xl"),
                   scope="none", engines=CPU_ENGINES, suites=SUITE_SCALES, pinned=True, energy=True,
                   telemetry={"gpu_energy_scope": "none", "energy_window_s": WINDOW_S, "cpu_energy": "require", "idle_baseline_seconds": 0},
                   sequences=CPU_ONLY_SEQUENCES),
    "c-vm": _spec(host="c-vm", kind="cpu-vm", instance_types=("c3-highmem-44",), scope="none", engines=CPU_ENGINES,
                  suites=CVM_SCALES, pinned=False, energy=False,
                  # time only: the window rule is kept so its per-run times are measured like the metal host's; no power
                  # sampler, no idle brackets (nothing to measure)
                  telemetry={"gpu_energy_scope": "none", "energy_window_s": WINDOW_S, "power_telemetry": "off",
                             "cpu_energy": "off", "idle_baseline_seconds": 0, "session_idle_seconds": 0},
                  sequences=CVM_SEQUENCES, cooldown_s=COOLDOWN_S, vm_pin=True),
}


@dataclass(frozen=True)
class RoundPlan:
    round: int                      # round number in the full design (1-8; C-VM 1-2); the directory is round-<round>
    square: int                     # 1 or 2
    row: int | None                 # 1-based Williams row (GPU hosts), else None
    sequence: str                   # letters, e.g. "ABDC" or "AB"
    engine_order: tuple
    suites: tuple                   # suite run order of the round


@dataclass
class Plan:
    spec: HostSpec
    lock_date: str                  # YYYYMMDD
    seed: int
    letters: dict                   # letter -> engine (CPU-only hosts: A and B of the two CPU engines)
    all_letters: dict = field(default_factory=dict)   # the four-engine assignment of the seed, from which the CPU letters are derived
    rounds: list = field(default_factory=list)   # the full design, every round of both squares


def parse_lock_date(text: str, today: date | None = None) -> tuple:
    """(seed, YYYYMMDD) of the lock date: exactly eight digits naming a real date, not after `today` when given."""
    if not isinstance(text, str) or not re.fullmatch(r"\d{8}", text):
        raise PlanError(f"lock date {text!r}: expected YYYYMMDD (eight digits)")
    try:
        day = datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        raise PlanError(f"lock date {text!r} is not a calendar date") from None
    if today is not None and day > today:
        raise PlanError(f"lock date {text} is in the future: the lock precedes every timed Paper 3 session (section 7.1)")
    return int(text), text


def derive_letters(seed: int) -> dict:
    """Letters A-D from the seed, exactly as section 3.2 words it: shuffle the four engines, assign A, B, C, D in order."""
    engines = list(SHUFFLE_BASE)
    random.Random(seed).shuffle(engines)
    return dict(zip("ABCD", engines))


def check_square(sequences, letters) -> None:
    """A Williams square: each engine once per position, each ordered pair of engines adjacent exactly once."""
    orders = [[letters[c] for c in seq] for seq in sequences]
    n = len(letters)
    if len(orders) != n or any(sorted(order) != sorted(letters.values()) for order in orders):
        raise PlanError("sequences are not permutations of the engines")
    for position in range(n):
        if sorted(order[position] for order in orders) != sorted(letters.values()):
            raise PlanError(f"position {position + 1} does not hold each engine exactly once")
    pairs = [(a, b) for order in orders for a, b in zip(order, order[1:])]
    expected = [(a, b) for a in letters.values() for b in letters.values() if a != b]
    if sorted(pairs) != sorted(expected):
        raise PlanError("ordered adjacent pairs are not each exactly once")


def suite_order(round_number: int, suites) -> tuple:
    """Odd rounds run the baseline suite first, even rounds the complex suite first (section 3.2)."""
    ordered = [s for s in ("baseline", "complex") if s in suites]
    return tuple(ordered if round_number % 2 == 1 else ordered[::-1])


def cpu_letters(all_letters: dict) -> dict:
    """A and B of the two CPU engines: they take the letters in the order they appear in the four-engine assignment of the seed."""
    return dict(zip("AB", [all_letters[c] for c in "ABCD" if all_letters[c] in CPU_ENGINES]))


def build_plan(host: str, lock_date: str, today: date | None = None) -> Plan:
    """The full pre-registered design of one host: every round of both squares, whatever launch runs them."""
    spec = HOSTS.get(host)
    if spec is None:
        raise PlanError(f"unknown Paper 3 host {host!r}; expected one of {sorted(HOSTS)}")
    seed, lock = parse_lock_date(lock_date, today)
    all_letters = derive_letters(seed)
    plan = Plan(spec=spec, lock_date=lock, seed=seed, letters=all_letters, all_letters=all_letters)
    if spec.kind == "gpu":
        for square, indexes in SQUARE_ROW_INDEX.items():
            check_square([ROWS[i] for i in indexes], plan.letters)
        for number in range(1, 9):
            square = 1 if number <= 4 else 2
            row = SQUARE_ROW_INDEX[square][(number - 1) % 4]
            sequence = ROWS[row]
            plan.rounds.append(RoundPlan(number, square, row + 1, sequence, tuple(plan.letters[c] for c in sequence),
                                         suite_order(number, spec.suites)))
        return plan
    plan.letters = cpu_letters(all_letters)
    for number, sequence in enumerate(spec.sequences, start=1):
        plan.rounds.append(RoundPlan(number, 1 if number <= 4 else 2, None, sequence, tuple(plan.letters[c] for c in sequence),
                                     suite_order(number, spec.suites)))
    return plan


def vm_pin(cpu_root=None, node_root=None, *, cores: int = VM_CORES, vcpus: int = VM_VCPUS) -> dict:
    """The C-VM pin (design 3.1): `cores` physical cores of the VM's first socket that has that many, with their SMT siblings,
    `vcpus` vCPUs in all, read from the VM's own topology (sysfs core_id and physical_package_id of every online CPU).

    A VM that reports no SMT siblings (every core holds one CPU) is pinned to the first `vcpus` CPUs of that socket and the record
    says so (caveat). Memory is bound only if the VM reports more than one NUMA node. PlanError when the topology cannot give the
    pin: a formal campaign then refuses to start."""
    cpu_root = Path(cpu_root or power_telemetry.SYS_CPU_ROOT)
    node_root = Path(node_root or power_telemetry.SYS_NODE_ROOT)
    groups = {}
    try:
        entries = [p for p in cpu_root.iterdir() if re.fullmatch(r"cpu\d+", p.name)]
    except OSError as exc:
        raise PlanError(f"cannot list the VM's CPUs under {cpu_root}: {exc}") from None
    for entry in entries:
        try:
            package = int((entry / "topology" / "physical_package_id").read_text())
            core = int((entry / "topology" / "core_id").read_text())
        except (OSError, ValueError):
            continue                                       # offline, or no topology exported
        groups.setdefault((package, core), []).append(int(entry.name[3:]))
    if not groups:
        raise PlanError(f"the VM reports no CPU topology under {cpu_root} (cpu*/topology/physical_package_id, core_id)")
    per_package = {}
    for package, core in sorted(groups):
        per_package.setdefault(package, []).append(core)
    package = next((p for p, found in sorted(per_package.items()) if len(found) >= cores), None)
    if package is None:
        raise PlanError(f"no socket of the VM reports {cores} physical cores (cores per socket: "
                        f"{ {p: len(c) for p, c in sorted(per_package.items())} })")
    chosen = per_package[package][:cores]
    selected = sorted(cpu for core in chosen for cpu in groups[(package, core)])
    siblings = any(len(groups[(package, core)]) > 1 for core in chosen)
    caveat = None
    if siblings:
        if len(selected) != vcpus:
            raise PlanError(f"{cores} physical cores of socket {package} hold {len(selected)} vCPUs, not {vcpus}")
    else:
        everything = sorted(cpu for (p, _), found in groups.items() if p == package for cpu in found)
        if len(everything) < vcpus:
            raise PlanError(f"socket {package} of the VM has {len(everything)} vCPUs, fewer than {vcpus}")
        selected = everything[:vcpus]
        caveat = (f"the VM reports no SMT siblings: {vcpus} vCPUs (the first of socket {package}) instead of {cores} physical cores "
                  "and their siblings; the engines may share physical cores the VM does not show")
    try:
        nodes = len([p for p in node_root.iterdir() if re.fullmatch(r"node\d+", p.name)])
    except OSError:
        nodes = 0
    return {"cpu_list": power_telemetry.format_cpu_list(selected), "cpu_count": len(selected), "socket": package,
            "physical_cores": chosen if siblings else None, "smt_siblings_reported": siblings,
            "numa_nodes_reported": nodes, "bind_memory": nodes > 1, "caveat": caveat,
            "basis": "sysfs cpu*/topology/{physical_package_id,core_id} of the VM, read at campaign start"}


def launch_rounds(plan: Plan, launch: str) -> list:
    """The rounds one launch runs: launch 1 the first square (rounds 1-4), launch 2 the second (5-8), both all eight."""
    if launch not in LAUNCHES:
        raise PlanError(f"launch {launch!r}: expected one of {LAUNCHES}")
    if plan.spec.kind == "cpu-vm":
        if launch == "2":
            raise PlanError("c-vm is one launch of two rounds (section 3.1); use --paper3-launch 1")
        return list(plan.rounds)
    squares = LAUNCH_SQUARES[launch]
    return [r for r in plan.rounds if r.square in squares]


def full_schedule(plan: Plan) -> list:
    return [{"round": r.round, "square": r.square, "row": r.row, "sequence": r.sequence, "engine_order": list(r.engine_order),
             "suite_order": list(r.suites)} for r in plan.rounds]


def sessions_per_round(plan: Plan) -> int:
    return len(plan.spec.engines) * sum(len(scales) for scales in plan.spec.suites.values())


def design_block(plan: Plan, launch: str, selected: list, *, host_config_sha256: str | None, t_min_s, lock: dict, vm_pin=None) -> dict:
    """The "paper3" block of the resolved design (compared byte for byte on every resume).

    common_sha256 covers everything the launches of one host must share; a second launch's campaign.json must carry the same one."""
    common = {"host": plan.spec.host, "lock": lock, "seed": plan.seed, "letters": plan.letters, "letters_all": plan.all_letters,
              "schedule": full_schedule(plan),
              "engines": list(plan.spec.engines), "suites": {s: list(v) for s, v in plan.spec.suites.items()},
              "telemetry": plan.spec.telemetry, "cooldown_s": plan.spec.cooldown_s, "host_config_sha256": host_config_sha256,
              "t_min_s": t_min_s}
    return {
        "design_doc": DESIGN_DOC_REL, "host": plan.spec.host, "kind": plan.spec.kind,
        "instance_types": list(plan.spec.instance_types), "energy_measured": plan.spec.energy,
        "launch": launch, "separate_launches": launch != "both",
        "launch_rounds": [r.round for r in selected], "squares": sorted({r.square for r in selected}),
        "lock": lock,
        "seed_rule": ("seed = int(YYYYMMDD of the lock date); random.Random(seed).shuffle(['polars', 'polars_gpu', 'spark_cpu', "
                      "'spark_gpu']) gives letters A-D; rounds 1-4 use rows ABDC/BCAD/CDBA/DACB, rounds 5-8 the same rows in "
                      "reverse order (4, 3, 2, 1)" if plan.spec.kind == "gpu" else
                      "two-engine orders as written in section 3.2 (C-GCP) or AB, BA (C-VM); the two CPU engines take A and B in the order "
                      "of their letters in the four-engine assignment of the same seed"),
        "letters": plan.letters, "letters_all": plan.all_letters, "rows": list(ROWS) if plan.spec.kind == "gpu" else None,
        **({"vm_pin": vm_pin} if plan.spec.vm_pin else {}),
        "schedule": full_schedule(plan), "sessions_per_round": sessions_per_round(plan),
        "window": {"energy_window_s": WINDOW_S, "max_runs": 50}, "cooldown_s": plan.spec.cooldown_s,
        "host_config_sha256": host_config_sha256, "t_min_s": t_min_s,
        "common_sha256": hashlib.sha256(json.dumps(common, sort_keys=True, default=str).encode()).hexdigest(),
    }


# ---- the lock (section 7.1) -----------------------------------------------------------------------------------

def sections_1_to_11(text: str) -> str:
    """The part of the design document that is never edited after the lock: everything before the amendments heading."""
    index = text.find("\n" + AMENDMENTS_HEADING)
    return text if index < 0 else text[:index + 1]


def verify_lock(lock_date: str, lock_commit: str, *, git, design_doc: Path) -> dict:
    """Evidence that the design file in use is the one locked on lock_date, or PlanError.

    git(*args) -> (returncode, stdout). Checks: the annotated tag paper3-design-lock exists and points at lock_commit; its
    tagger date, in UTC, is lock_date (the seed is the lock date, so a seed that disagrees with the tag would silently
    change the pre-registered orders); the status line of the file at the lock says LOCKED; sections 1-11 of the file in the
    working tree are byte-identical to the locked ones (amendments may only be appended to section 12)."""
    def must(*args, raw=False):
        code, out = git(*args)
        if code != 0:
            raise PlanError(f"git {' '.join(args)} failed: {str(out).strip()[:200]}")
        return str(out) if raw else str(out).strip()
    tag_commit = must("rev-parse", "--verify", f"refs/tags/{LOCK_TAG}^{{commit}}")
    commit = must("rev-parse", "--verify", f"{lock_commit}^{{commit}}")
    if commit != tag_commit:
        raise PlanError(f"--paper3-lock-commit {lock_commit} is {commit}, but tag {LOCK_TAG} points at {tag_commit}")
    kind, _, stamp = must("for-each-ref", f"refs/tags/{LOCK_TAG}", "--format=%(objecttype)|%(taggerdate:iso-strict)").partition("|")
    if kind != "tag":
        raise PlanError(f"{LOCK_TAG} is a {kind or 'missing ref'}, not an annotated tag (section 7.1)")
    try:
        tagged = datetime.fromisoformat(stamp).astimezone(timezone.utc)
    except ValueError:
        raise PlanError(f"cannot read the tagger date of {LOCK_TAG}: {stamp!r}") from None
    if tagged.strftime("%Y%m%d") != lock_date:
        raise PlanError(f"lock date {lock_date} but {LOCK_TAG} was created {tagged.isoformat()} (UTC date {tagged:%Y%m%d}); "
                        "the seed is the lock date, so the two must agree")
    locked = must("show", f"{commit}:{DESIGN_DOC_REL}", raw=True)
    if "LOCKED" not in "\n".join(locked.splitlines()[:8]):
        raise PlanError(f"{DESIGN_DOC_REL} at {commit} does not say LOCKED in its status line")
    current = design_doc.read_text()
    if sections_1_to_11(locked).rstrip() != sections_1_to_11(current).rstrip():
        raise PlanError(f"{DESIGN_DOC_REL}: sections 1-11 differ from the locked file at {commit}; only section 12 may grow")
    return {"tag": LOCK_TAG, "commit": commit, "tagger_date_utc": tagged.isoformat(),
            "design_doc_sha256_at_lock": hashlib.sha256(locked.encode()).hexdigest(), "sections_1_11_unchanged": True}
