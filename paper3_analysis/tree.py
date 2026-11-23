"""A Paper 3 campaign tree: every launch of every host, its accepted rounds, its squares (design 3.2, 4, amendment A1 item 2).

A launch is one run_campaign.py root (campaign.json, status.json, round-<k>[-repeat-<n>]/ ...), found by its campaign.json anywhere under
the tree; the operator's launch-record.json (price, instance id, launch and terminate times) is read from the launch directory above it.

  accepted round   the last attempt of round k whose gates.json says pass (what run_campaign.py itself accepts); a failed attempt, the
                   repeat that replaced it and an abandoned one are never pooled with it
  complete square  all rounds of one design square accepted within ONE instance (a relaunch after a loss, A1 item 2, is another instance
                   of the same launch number and is never pooled with the lost one)
  confirmatory     the accepted rounds of complete squares; the other accepted rounds are a sensitivity only, and a host with no complete
                   square is reported descriptively (3.2, 5.4)

status.json is read as a cross-check of the squares derived from the disk: a disagreement raises unless the caller allows it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re

ROUND_DIR = re.compile(r"^round-(\d+)(?:-repeat-(\d+))?$")
SKIP_DIRS = {"preflight-bringup", "bringup-artifacts", ".stage-scripts", ".incoming", ".verified", "snapshots", "side-study", "plans",
             "__pycache__", ".git"}                          # sealed or irrelevant: the bring-up preflight is "not analysed" (A1 item 3)
MAX_DEPTH = 8
FINISHED_STATES = ("complete", "stopped")          # run_campaign.py's final states: all rounds accepted, or stopped by the rules (design 7.3)
# The launches the design plans for the hosts that run (amendment A5: GH200 and B200 are not run): two launches (one square each) of the
# A100, H100 and C-GCP, one of the C-VM. A host outside this table that appears in a tree is expected to have both launches too.
PLANNED_LAUNCHES = {"a100": ("1", "2"), "h100": ("1", "2"), "c-gcp": ("1", "2"), "c-vm": ("1",), "gh200": ("1", "2"), "b200": ("1", "2")}


class AnalysisError(Exception):
    """The tree cannot be analysed as the design says; never turned into a number."""


def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scale_key(scale) -> str:
    return "sf" + str(scale).replace(".", "p")


def scale_sort(scales):
    return sorted(scales, key=lambda s: float(str(s).replace("sf", "").replace("p", ".")))


@dataclass
class Attempt:
    path: Path
    round: int
    index: int                      # 0 for round-<k>, n for round-<k>-repeat-<n>
    gates: object                   # gates.json or None
    report: object = None           # paper3-gates.json (loaded for accepted attempts)

    @property
    def verdict(self):
        return self.gates.get("verdict") if isinstance(self.gates, dict) else None


@dataclass
class Instance:
    """One run_campaign.py root: a launch of a host (a relaunch after a loss is a second instance of the same launch number)."""
    host: str
    launch: str
    ordinal: int                    # 1 for the first instance of (host, launch) by created_utc, 2 for a relaunch
    root: Path
    campaign: dict
    record: object                  # launch-record.json or None
    status: object                  # status.json or None
    attempts: dict = field(default_factory=dict)       # round -> [Attempt] by index
    accepted: dict = field(default_factory=dict)       # round -> Attempt

    @property
    def label(self) -> str:
        return f"{self.host}-l{self.launch}" + ("" if self.ordinal == 1 else f"r{self.ordinal}")

    @property
    def design(self) -> dict:
        return self.campaign.get("design") or {}

    @property
    def paper3(self) -> dict:
        return self.design.get("paper3") or {}

    @property
    def declared_types(self) -> dict:
        """The instance type each record names: campaign.json's cost block, its host provenance, the operator's launch record."""
        cost = self.campaign.get("cost") or {}
        host = (self.campaign.get("provenance") or {}).get("host") or {}
        found = {"cost": cost.get("instance_type"), "host": host.get("instance_type"), "launch_record": (self.record or {}).get("instance_type")}
        return {k: v for k, v in found.items() if v}

    @property
    def instance_type(self):
        """The type that ran: the records must agree (instance_type_problem says when they do not)."""
        declared = self.declared_types
        return next(iter(declared.values()), None)

    @property
    def instance_type_problem(self):
        distinct = set(self.declared_types.values())
        return f"{self.label}: the records name different instance types {self.declared_types}" if len(distinct) > 1 else None

    @property
    def scope(self) -> str:
        return ((self.design.get("energy") or {}).get("gpu_energy_scope")
                or (self.paper3.get("telemetry") or {}).get("gpu_energy_scope") or "none")

    @property
    def kind(self) -> str:
        return self.paper3.get("kind") or "gpu"

    @property
    def engines(self) -> tuple:
        return tuple(self.design.get("engines") or ())

    @property
    def suites(self) -> dict:
        return {suite: [str(s) for s in (spec or {}).get("scales") or []] for suite, spec in (self.design.get("suites") or {}).items()}

    @property
    def runs(self) -> int:
        return int((self.design.get("flags") or {}).get("runs") or 3)

    @property
    def synthetic(self) -> bool:
        return self.campaign.get("synthetic") is True or self.design.get("formal") is not True

    @property
    def billed_price(self):
        """P-BILLED: the provider's price at launch (campaign.json cost block, else the operator's launch record), USD per hour."""
        for source, key in ((self.campaign.get("cost") or {}, "hourly_price_usd"), (self.record or {}, "price_usd_h")):
            value = source.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return float(value)
        return None


@dataclass
class HostData:
    host: str
    instances: list
    square_rounds: dict             # square -> [rounds of the design]
    complete: dict                  # square -> Instance that completed it
    confirmatory: dict              # round -> (Instance, Attempt): the accepted rounds of complete squares
    analysis: dict                  # round -> (Instance, Attempt): what the main analysis uses (confirmatory, or the descriptive set of a host with no complete square)
    sensitivity: dict               # instance label -> {round: Attempt}: accepted rounds outside `analysis` (incomplete squares, lost and replaced launches)
    warnings: list = field(default_factory=list)

    @property
    def confirmatory_rounds(self) -> list:
        return sorted(self.confirmatory)

    @property
    def analysis_rounds(self) -> list:
        return sorted(self.analysis)

    @property
    def sensitivity_rounds(self) -> list:
        return sorted({r for rounds in self.sensitivity.values() for r in rounds})

    @property
    def has_complete_square(self) -> bool:
        return bool(self.complete)

    @property
    def basis(self) -> str:
        return "confirmatory" if self.complete else ("descriptive_no_complete_square" if self.analysis else "none")

    @property
    def analysis_instances(self) -> list:
        seen = []
        for inst, _ in (self.analysis[r] for r in sorted(self.analysis)):
            if inst not in seen:
                seen.append(inst)
        return seen

    @property
    def reference_instance(self):
        """An instance whose rounds the analysis uses (its type, grid and prices describe the host); the first instance if it uses none."""
        used = self.analysis_instances
        return used[0] if used else self.instances[0]

    @property
    def kind(self) -> str:
        return self.instances[0].kind

    @property
    def scope(self) -> str:
        return self.instances[0].scope


@dataclass
class Tree:
    root: Path
    hosts: dict
    instances: list
    warnings: list = field(default_factory=list)


def walk_for_campaigns(root: Path):
    """Directories under `root` (inclusive) that hold a campaign.json, not descending into a campaign root or a skipped directory."""
    found = []

    def visit(directory: Path, depth: int):
        if (directory / "campaign.json").is_file():
            found.append(directory)
            return
        if depth >= MAX_DEPTH:
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if entry.is_dir(follow_symlinks=False) and entry.name not in SKIP_DIRS:
                visit(Path(entry.path), depth + 1)

    visit(Path(root), 0)
    return found


def find_launch_record(root: Path, host: str, launch: str):
    """launch-record.json beside the copied campaign root: <launch dir>/rerun/paper3-<host>-l<N>/ -> <launch dir>/launch-record.json."""
    for ancestor in list(root.parents)[:4]:
        record = read_json(ancestor / "launch-record.json")
        if isinstance(record, dict) and str(record.get("host")) == host and str(record.get("launch")) == str(launch):
            return record
    return None


def list_attempts(root: Path) -> dict:
    attempts = {}
    for entry in sorted(root.iterdir()) if root.is_dir() else []:
        match = ROUND_DIR.match(entry.name)
        if match and entry.is_dir():
            number, index = int(match.group(1)), int(match.group(2) or 0)
            attempts.setdefault(number, []).append(Attempt(entry, number, index, read_json(entry / "gates.json")))
    for items in attempts.values():
        items.sort(key=lambda a: a.index)
    return attempts


class NotPaper3(AnalysisError):
    """A campaign.json that is not a Paper 3 root (a Paper 1 campaign, say): skipped with a warning when found under a tree, never analysed."""


def load_instance(root: Path) -> Instance:
    campaign = read_json(root / "campaign.json")
    if not isinstance(campaign, dict):
        raise AnalysisError(f"{root}/campaign.json is unreadable")
    block = ((campaign.get("design") or {}).get("paper3")) or None
    if not isinstance(block, dict):
        raise NotPaper3(f"{root}: campaign.json has no design.paper3 block: not a Paper 3 campaign (a Paper 1 root is never analysed here)")
    host, launch = str(block.get("host")), str(block.get("launch"))
    instance = Instance(host=host, launch=launch, ordinal=1, root=root, campaign=campaign, record=find_launch_record(root, host, launch),
                        status=read_json(root / "status.json"))
    instance.attempts = list_attempts(root)
    for number, items in instance.attempts.items():
        passed = [a for a in items if a.verdict == "pass"]
        if passed:
            instance.accepted[number] = passed[-1]                  # run_campaign.drive(): passed[-1]
    return instance


def design_rounds(instance: Instance) -> dict:
    """{square: [rounds]} of the host's full design (both squares), from the resolved design in campaign.json."""
    squares = {}
    for entry in instance.paper3.get("schedule") or []:
        squares.setdefault(int(entry["square"]), []).append(int(entry["round"]))
    return {sq: sorted(rounds) for sq, rounds in sorted(squares.items())}


def build_host(host: str, instances: list, *, allow_status_mismatch: bool = False, allow_design_mismatch: bool = False) -> HostData:
    ordered = sorted(instances, key=lambda i: (i.launch, (i.campaign.get("provenance") or {}).get("created_utc") or "", str(i.root)))
    seen = {}
    for instance in ordered:
        seen[instance.launch] = seen.get(instance.launch, 0) + 1
        instance.ordinal = seen[instance.launch]
    common = {i.paper3.get("common_sha256") for i in ordered}
    warnings = []
    if len(common) > 1:
        message = f"host {host}: the launches carry different common_sha256 ({sorted(map(str, common))}): they did not run one design"
        if not allow_design_mismatch:
            raise AnalysisError(message + " (pass allow_design_mismatch to analyse them as one host)")
        warnings.append(message)
    for instance in ordered:
        if instance.instance_type_problem:
            raise AnalysisError(instance.instance_type_problem + ": the type that ran is not known")
    squares = design_rounds(ordered[0])
    if not squares:
        raise AnalysisError(f"host {host}: campaign.json holds no resolved schedule (design.paper3.schedule)")
    complete = {}
    for square, rounds in squares.items():
        owners = [i for i in ordered if all(r in i.accepted for r in rounds)]
        if len(owners) > 1:
            raise AnalysisError(f"host {host}: square {square} is complete in more than one instance ({[i.label for i in owners]}); the rounds "
                                "of the two cannot both be confirmatory (one value per round number)")
        if owners:
            complete[square] = owners[0]
    confirmatory = {r: (complete[sq], complete[sq].accepted[r]) for sq, rounds in squares.items() if sq in complete for r in rounds}
    if complete:
        analysis = dict(confirmatory)
    else:
        # no complete square: the host is described, from the LAST instance of each launch (a lost launch's rounds are never pooled with its
        # relaunch's, A1 item 2(c); they stay in the sensitivities)
        last = {}
        for instance in ordered:
            last[instance.launch] = instance
        analysis = {}
        for launch in sorted(last):
            for number, attempt in sorted(last[launch].accepted.items()):
                analysis.setdefault(number, (last[launch], attempt))
    used = {(inst.label, r) for r, (inst, _) in analysis.items()}
    sensitivity = {}
    for instance in ordered:
        for number, attempt in sorted(instance.accepted.items()):
            if (instance.label, number) not in used:
                sensitivity.setdefault(instance.label, {})[number] = attempt
    types = {inst.instance_type for inst, _ in analysis.values()}
    if len(types) > 1:
        raise AnalysisError(f"host {host}: the instances whose rounds are analysed ran different instance types {sorted(map(str, types))}; shapes differ in "
                            "populated DRAM and are never pooled (design 3.1)")
    for instance in ordered:                                       # status.json is the running campaign's own view of its squares
        status = (instance.status or {}).get("paper3") if isinstance(instance.status, dict) else None
        if not isinstance(status, dict):
            continue
        derived = sorted(r for sq, owner in complete.items() if owner is instance for r in squares[sq])
        reported = sorted(status.get("confirmatory_rounds") or [])
        if derived != reported:
            message = (f"{instance.label}: status.json lists confirmatory rounds {reported} but the accepted attempts on disk give {derived}")
            if not allow_status_mismatch:
                raise AnalysisError(message + " (pass allow_status_mismatch to analyse from the disk)")
            warnings.append(message)
    return HostData(host=host, instances=ordered, square_rounds=squares, complete=complete, confirmatory=confirmatory, analysis=analysis,
                    sensitivity=sensitivity, warnings=warnings)


def load_tree(root: Path, *, allow_status_mismatch: bool = False, allow_design_mismatch: bool = False, extra_roots=()) -> Tree:
    roots = walk_for_campaigns(Path(root)) + [Path(r) for r in extra_roots]
    if not roots:
        raise AnalysisError(f"no campaign.json under {root}")
    instances, skipped = [], []
    for r in roots:
        try:
            instances.append(load_instance(r))
        except NotPaper3 as exc:
            skipped.append(str(exc))
    if not instances:
        raise AnalysisError(f"no Paper 3 campaign.json under {root}: " + "; ".join(skipped[:3]))
    by_host = {}
    for instance in instances:
        by_host.setdefault(instance.host, []).append(instance)
    hosts = {host: build_host(host, items, allow_status_mismatch=allow_status_mismatch, allow_design_mismatch=allow_design_mismatch)
             for host, items in sorted(by_host.items())}
    return Tree(root=Path(root), hosts=hosts, instances=[i for h in hosts.values() for i in h.instances],
                warnings=[f"skipped (not a Paper 3 campaign): {m}" for m in skipped] + [w for h in hosts.values() for w in h.warnings])


def blinding_problems(tree: Tree, expected=None) -> list:
    """What still blocks the unblinded analysis (design 7.3): a launch whose status.json is not complete or stopped-by-rule ("paused" is written between
    rounds, so it counts as running), and a planned launch that is not in the tree at all. A launch replaced by a relaunch (A1 item 2) is judged by its
    last instance. Each problem is a (label, text) pair; the caller may declare labels ended (lost, abandoned, never to be run)."""
    problems = []
    last = {}
    for host in tree.hosts.values():
        for instance in host.instances:
            last[(instance.host, instance.launch)] = instance
    for (host, launch), instance in sorted(last.items()):
        state = str((instance.status or {}).get("state")) if isinstance(instance.status, dict) else "no status.json"
        if state not in FINISHED_STATES:
            problems.append((instance.label, f"{instance.label}: status.json state is {state!r}, not complete or stopped by rule"))
    for host, launches in sorted((expected or {}).items()):
        for launch in launches:
            if (host, launch) not in last:
                problems.append((f"{host}-l{launch}", f"{host}-l{launch}: planned launch is not in the tree (not run yet?)"))
    return problems


def load_gate_report(attempt: Attempt) -> dict:
    report = read_json(attempt.path / "paper3-gates.json")
    if not isinstance(report, dict) or "runs" not in report:
        raise AnalysisError(f"{attempt.path}: paper3-gates.json is missing or unreadable: the eligibility flags (design 4) cannot be applied")
    if report.get("round_failed"):
        raise AnalysisError(f"{attempt.path}: accepted by gates.json but paper3-gates.json says round_failed: {report.get('round_failures')}")
    attempt.report = report
    return report
