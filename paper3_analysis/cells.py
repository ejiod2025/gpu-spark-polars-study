"""Per-cell time and energy of a campaign: one value per (host, round, suite, scale, query, engine), from the sessions' own records.

A cell's value is the median over its three trial windows of the per-run value (design 5.1): time T (seconds per run), total energy E
(joules per run: the counter difference summed over the window's runs, divided by n) and above-idle energy E - P_idle * T, per scope
series. Scope series and where they come from:

  S1-counter / S1-module   the GPU counter of the session (worker.py: energy_window.gpu_energy), the board on x86 GPU hosts, the whole
                           Grace-Hopper module on GH200; for a CPU engine on a GPU host it is the idle GPU's energy (2.5)
  S2-pinned, S2-all        RAPL on the CPU-only host, the sampler's power.csv interpolated linearly at each run's stamps with the shared
                           power_telemetry.s2_pinned_energy_j (package + DRAM of the pinned socket; every package + DRAM zone)
  S2-pinned/package, /dram the two parts of S2-pinned, shown separately (2.4); both carry the tag S2-pinned

Every value carries a scope tag (2.4), the idle windows' counter values included: a counter value without a valid tag, or with another host's tag, raises
UntaggedEnergyError, never a number. A cell that is not eligible says why; nothing is imputed and nothing is clamped (negative above-idle values are kept, 2.2).

Failures are classified with preflight.classify_error, the rules of amendment A1 (the ones the campaign's own gate uses): a coverage outcome
(resource_exhaustion, unsupported_operation) means the engine did not run that query; anything else is an integrity failure, which leaves the cell as
"ran, no value" (never a missing engine that another engine may replace). A session that did not complete keeps every query's outcome with its class.

Gates (paper3-gates.json of the accepted attempt) decide eligibility: G9 window_below_10s or per_run_below_t_min makes the cell's energy
"below measurement resolution" (time and cost are perf_counter numbers and stay); a G9 window-rule violation makes the whole cell unusable; a cell with
measured windows and no G9 verdict is an error; G10 above_idle_reliable false drops above-idle for the scopes it names; G6 energy_valid false drops the
session's energy. The report's t_min_s must equal paper3_hosts.json's and campaign.json's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import statistics

import paper3_gates as pg
import power_telemetry

from . import crosscheck
from .constants import (CPU_ENGINES, IDLE_LABELS, S1_TAG, S2_ALL, S2_PINNED, S2_PINNED_DRAM, S2_PINNED_PACKAGE, SERIES_TAG)
from .tree import AnalysisError, Instance, load_gate_report, read_json, scale_key

HOSTS_CONFIG = Path(__file__).resolve().parents[1] / "paper3_hosts.json"
COVERAGE_CLASSES = ("resource_exhaustion", "unsupported_operation")        # A1: coverage outcomes (reported, never fail a round)
_UNSET = object()


class UntaggedEnergyError(AnalysisError):
    """An energy value without a valid scope tag (design 2.4: the analysis raises, it never guesses a scope)."""


@dataclass
class EnergyCell:
    series: str                       # key: a scope tag, or S2-pinned/package, S2-pinned/dram
    tag: str                          # the scope tag the values carry
    total_j: object = None            # J per run, median over the trial windows
    above_j: object = None
    idle_w: object = None             # P_idle of the session for this series
    total_reason: object = None       # why total_j is None
    above_reason: object = None
    trial_j: tuple = ()               # J per run of each trial window (None where invalid): the within-session CV of 5.2

    def __post_init__(self):
        power_telemetry.require_scope_tag(self.tag)


@dataclass
class Cell:
    host: str
    instance: str                     # Instance.label
    launch: str
    round: int
    suite: str
    scale: str
    query: str
    engine: str
    price_instance: object = None     # the Instance, for prices
    time_s: object = None             # seconds per run, median over the trial windows
    trial_times: tuple = ()           # seconds per run of each trial window
    time_reason: object = None
    n_trials: int = 0
    coverage: object = None           # A1 coverage class of the failure (resource_exhaustion, unsupported_operation) or None
    failure_class: object = None      # the class of any failure (coverage or integrity), for the coverage matrix
    session_failed: bool = False      # the session did not complete; this query's outcome is kept with the session's class
    completed_before_failure: bool = False
    g9_reasons: tuple = ()            # the G9 verdict's reasons (window_below_10s, per_run_below_t_min, window_rule_violation)
    energy: dict = field(default_factory=dict)         # series -> EnergyCell

    @property
    def ran(self) -> bool:
        """The engine ran this query (it is not a coverage outcome): a cell that ran may still have no value, and then no other engine stands in for it."""
        return self.coverage is None

    def energy_value(self, series: str, above: bool = False):
        cell = self.energy.get(series)
        if cell is None:
            return None
        return cell.above_j if above else cell.total_j


@dataclass
class SessionInfo:
    host: str
    instance: str
    round: int
    suite: str
    scale: str
    engine: str
    status: object
    returncode: object
    synthetic_smoke: bool
    n_queries: int
    query_errors: list
    gates: dict                       # gate id -> outcome
    g10_unreliable: tuple
    failure_class: object = None


@dataclass
class CellStore:
    cells: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    latencies: dict = field(default_factory=dict)      # (host, engine) -> {"read_latency_ms": [...], "overhang_ms": [...]}
    idle: dict = field(default_factory=dict)           # (host, engine, series) -> [(round, instance label, P_idle W)] one per session
    crosscheck: dict = field(default_factory=dict)     # host -> {"field": name, "sessions": [(round, instance, engine, [relative differences])]}
    warnings: list = field(default_factory=list)
    index: dict = field(default_factory=dict)          # (host, instance label, round) -> {(suite, scale): {query: {engine: Cell}}}

    def add(self, cell: Cell):
        self.cells.append(cell)
        self.index.setdefault((cell.host, cell.instance, cell.round), {}).setdefault((cell.suite, cell.scale), {}) \
            .setdefault(cell.query, {})[cell.engine] = cell

    def at(self, host: str, instance: str, round_: int, suite: str, scale: str) -> dict:
        """{query: {engine: Cell}} of one accepted round of one instance."""
        return self.index.get((host, instance, round_), {}).get((suite, scale), {})


# ---- gate views ------------------------------------------------------------------------------------------------------------------

def gate_views(report: dict):
    """({(suite, scale, engine): session gate entry}, {(suite, scale, engine, query): G9 cell entry}) of a paper3-gates.json report."""
    sessions, cells = {}, {}
    for run in report.get("runs") or []:
        for session in run.get("sessions") or []:
            sessions[(session["suite"], str(session["scale"]), session["engine"])] = session
        for cell in run.get("cells") or []:
            cells[(cell["suite"], str(cell["scale"]), cell["engine"], cell["query"])] = cell
    return sessions, cells


def config_t_min(host: str):
    """t_min_s of the host in paper3_hosts.json, or _UNSET when the file or the entry is not there."""
    config = read_json(HOSTS_CONFIG)
    entry = ((config or {}).get("hosts") or {}).get(host) if isinstance(config, dict) else None
    return entry.get("t_min_s", _UNSET) if isinstance(entry, dict) else _UNSET


def check_t_min(instance: Instance, report: dict, attempt) -> None:
    """The resolution limit the G9 verdicts were made with is the host's pre-registered one (paper3_hosts.json, design 2.1): a report made with
    another t_min_s, or a campaign.json that records another, is refused."""
    expected = config_t_min(instance.host)
    if expected is _UNSET:
        return
    for where, value in ((f"{attempt.path.name}/paper3-gates.json", report.get("t_min_s")), ("campaign.json design.paper3", instance.paper3.get("t_min_s"))):
        if value != expected:
            raise AnalysisError(f"{instance.label}: {where} says t_min_s {value!r} but paper3_hosts.json has {expected!r} for {instance.host}: the G9 verdicts "
                                "were not made with the pre-registered resolution limit")


# ---- failures (amendment A1) -----------------------------------------------------------------------------------------------------

def classify_error(engine: str, type_name, message) -> dict:
    """preflight.classify_error: {"rule", "outcome": coverage | integrity, "class"}. The campaign's own gate and this analysis share it."""
    import preflight
    return preflight.classify_error(engine, type_name, message)


def log_tail(path: Path, limit: int = 6000) -> str:
    try:
        with Path(path).open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - limit))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def session_verdict(engine: str, results, scale_dir: Path) -> dict:
    """The A1 verdict of a session that did not complete: its recorded failure, else the tail of the engine's log (as preflight reads it)."""
    failure = (results or {}).get("failure") if isinstance(results, dict) else None
    if isinstance(failure, dict):
        return classify_error(engine, failure.get("type"), failure.get("message"))
    return classify_error(engine, "", log_tail(Path(scale_dir) / f"{engine}.log"))


# ---- trial-level extraction ------------------------------------------------------------------------------------------------------

def positive(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def trial_time(block: dict):
    """Seconds per run of a window from its own runs (the block's summary is checked against them by G9, not trusted here)."""
    seconds = [r.get("seconds") for r in block.get("runs") or []]
    if not seconds or not all(positive(s) for s in seconds):
        return None
    return math.fsum(seconds) / len(seconds)


def counter_tag(energy, instance: Instance, where: str):
    """(expected S1 tag or None, tag found): the tag of a counter value must be valid and be the one the host's declared scope gives (2.4)."""
    expected = S1_TAG.get(instance.scope)
    try:
        tag = power_telemetry.require_scope_tag(energy)
    except ValueError as exc:
        raise UntaggedEnergyError(f"{where}: {exc}") from None
    if tag != power_telemetry.SCOPE_NONE and expected is None:
        raise UntaggedEnergyError(f"{where}: GPU energy tagged {tag!r} on a host declared with scope {instance.scope!r}")
    if tag != power_telemetry.SCOPE_NONE and tag != expected:
        raise UntaggedEnergyError(f"{where}: GPU energy tagged {tag!r}, the host's declared scope gives {expected!r}")
    return expected, tag


def counter_series(block: dict, instance: Instance, where: str):
    """(series key, per-run J or None, reason) of one window's GPU counter value, or None when the host has no GPU counter in use."""
    energy = block.get("gpu_energy")
    if not isinstance(energy, dict):
        raise UntaggedEnergyError(f"{where}: energy_window has no gpu_energy block: not a Paper 3 window record")
    expected, tag = counter_tag(energy, instance, where)
    if tag == power_telemetry.SCOPE_NONE:
        if energy.get("per_run_delta_mj") is not None or energy.get("window_delta_mj") is not None:
            raise UntaggedEnergyError(f"{where}: an energy value is tagged 'none'")
        return (expected, None, "no_counter_value") if expected else None
    per_run = energy.get("per_run_delta_mj")
    if not positive(per_run) and per_run != 0:
        return tag, None, "energy_invalid"
    return tag, per_run / 1000.0, None


def idle_counter_watts(window: dict, instance: Instance, where: str):
    """Mean W of an idle window's counter difference, with its tag checked like any other energy value (an untagged or foreign-tagged idle value raises)."""
    energy = window.get("gpu_energy")
    if not isinstance(energy, dict):
        raise UntaggedEnergyError(f"{where}: idle window without a gpu_energy block")
    _, tag = counter_tag(energy, instance, where)
    if tag == power_telemetry.SCOPE_NONE:
        if energy.get("delta_mj") is not None:
            raise UntaggedEnergyError(f"{where}: an idle-window energy value is tagged 'none'")
        return None
    return power_telemetry.idle_window_power_w(energy)[1]


def rapl_zone_sets(sources: dict) -> dict:
    """{series key: [zones]} of the host's CPU energy series from power_sources.json (the shared helpers; core and uncore sub-zones are never added)."""
    zones = {}
    pinned = power_telemetry.pinned_energy_zones(sources)
    if pinned:
        zones[S2_PINNED] = pinned
        attribution = ((sources.get("pinning") or {}).get("attribution") or {}).get("pinned_zones") or {}
        zones[S2_PINNED_PACKAGE] = sorted(z for kinds in attribution.values() for z in kinds.get("package", []))
        zones[S2_PINNED_DRAM] = sorted(z for kinds in attribution.values() for z in kinds.get("dram", []))
    every = power_telemetry.all_package_dram_zones(sources)
    if every:
        zones[S2_ALL] = every
    return {k: v for k, v in zones.items() if v}


def rapl_joules(series: dict, zones, start_ns, end_ns):
    """Joules of the zones between two CLOCK_MONOTONIC stamps (power_telemetry.s2_pinned_energy_j: linear interpolation); None if invalid."""
    if not (isinstance(start_ns, int) and isinstance(end_ns, int)):
        return None
    return power_telemetry.s2_pinned_energy_j(series, zones, start_ns, end_ns)["joules"]


def window_rapl(block: dict, series: dict, zone_sets: dict) -> dict:
    """{series key: per-run J or None}: each run of the window interpolated at its own stamps, summed, divided by n."""
    runs = block.get("runs") or []
    out = {}
    for key, zones in zone_sets.items():
        parts = [rapl_joules(series, zones, r.get("timed_start_monotonic_ns"), r.get("timed_end_monotonic_ns")) for r in runs]
        out[key] = None if not parts or any(p is None for p in parts) else math.fsum(parts) / len(parts)
    return out


def session_idle(results: dict, instance: Instance, series, zone_sets: dict, where: str) -> dict:
    """{series key: P_idle W or None}: the mean of the two idle windows' mean power per scope (2.2)."""
    windows = {w.get("label"): w for w in results.get("idle_windows") or [] if isinstance(w, dict)}
    per_series = {}
    for label in IDLE_LABELS:
        window = windows.get(label)
        if window is None:
            continue
        watts = {}
        if instance.scope in S1_TAG or instance.kind == "gpu":
            value = idle_counter_watts(window, instance, f"{where} idle window {label}")
            if value is not None:
                watts[S1_TAG[instance.scope]] = value
        elif isinstance(window.get("gpu_energy"), dict):
            idle_counter_watts(window, instance, f"{where} idle window {label}")      # a host with no counter: the window must say so (tag none)
        start, end = window.get("start_monotonic_ns"), window.get("end_monotonic_ns")
        if zone_sets and series is not None and isinstance(start, int) and isinstance(end, int) and end > start:
            for key, zones in zone_sets.items():
                joules = rapl_joules(series, zones, start, end)
                if joules is not None:
                    watts[key] = joules / ((end - start) / 1e9)
        per_series[label] = watts
    keys = set().union(*[set(v) for v in per_series.values()]) if per_series else set()
    return {k: (statistics.fmean([per_series[label][k] for label in IDLE_LABELS]) if all(k in per_series.get(label, {}) for label in IDLE_LABELS) else None)
            for k in keys}


# ---- one session -----------------------------------------------------------------------------------------------------------------

def above_reported(instance: Instance, engine: str, series: str) -> bool:
    """2.2: above-idle is zero by construction for a CPU engine on an x86 GPU host (the idle GPU) and not reported; on GH200 it is."""
    return not (instance.kind == "gpu" and instance.scope == "board" and engine in CPU_ENGINES)


def failure_cells(store, instance, round_, suite, scale, item, results, queries, verdict) -> None:
    """The cells of a session that did not complete: every query keeps its outcome with the session's A1 class (completed queries are recorded as such)."""
    measured = {}
    for record in (results or {}).get("records") or []:
        if isinstance(record, dict) and record.get("phase") == "measured":
            measured[record.get("query")] = measured.get(record.get("query"), 0) + 1
    coverage = verdict["class"] if verdict["outcome"] == "coverage" else None
    for query in queries:
        cell = Cell(instance.host, instance.label, instance.launch, round_, suite, scale, query, item["engine"], price_instance=instance)
        cell.session_failed, cell.coverage, cell.failure_class = True, coverage, verdict["class"]
        cell.completed_before_failure = measured.get(query, 0) >= instance.runs
        cell.time_reason = f"session_failed:{verdict['class']}:{'completed' if cell.completed_before_failure else 'not_reached'}"
        store.add(cell)


def build_session(store: CellStore, instance: Instance, round_: int, suite: str, scale: str, item: dict, results, gates: dict, g9: dict, rapl,
                  gpu=None, scale_dir=None) -> None:
    engine = item["engine"]
    where = f"{instance.label} round {round_} {suite}/{scale_key(scale)}/{engine}"
    session_gates = (gates or {}).get("gates") or {}
    flags = (gates or {}).get("flags") or {}
    g10 = session_gates.get("G10") or {}
    unreliable = tuple(g10.get("unreliable_scopes") or ()) if g10.get("outcome") == "fail" else ()
    complete = item.get("status") == "complete" and item.get("returncode") == 0
    queries = list((results or {}).get("queries") or [])
    errors = [e for e in (results or {}).get("query_errors") or [] if isinstance(e, dict)]
    verdict = None if complete else session_verdict(engine, results, scale_dir)
    store.sessions.append(SessionInfo(instance.host, instance.label, round_, suite, scale, engine, item.get("status"), item.get("returncode"),
                                      (results or {}).get("synthetic_smoke") is True, len(queries), errors,
                                      {g: v.get("outcome") for g, v in session_gates.items()}, unreliable, verdict["class"] if verdict else None))
    if not complete:
        if results is None:
            store.warnings.append(f"{where}: session did not complete and left no results.json ({verdict['class']})")
        else:
            failure_cells(store, instance, round_, suite, scale, item, results, queries, verdict)
        return
    series_by_zone, zone_sets = (rapl or (None, {}))
    idle = session_idle(results, instance, series_by_zone, zone_sets, where)
    for key, watts in idle.items():
        store.idle.setdefault((instance.host, engine, key), []).append((round_, instance.label, watts))
    energy_valid = flags.get("energy_valid", True)
    for record in results.get("records") or []:                      # read latency and overhang per run: the QA table (2.1)
        block = record.get("energy_window") if isinstance(record, dict) else None
        if isinstance(block, dict) and record.get("phase") == "measured":
            into = store.latencies.setdefault((instance.host, engine), {"read_latency_ms": [], "overhang_ms": []})
            for run in block.get("runs") or []:
                energy = run.get("gpu_energy") if isinstance(run, dict) else None
                if isinstance(energy, dict):
                    for name in ("start_read_latency_ns", "end_read_latency_ns"):
                        if isinstance(energy.get(name), int):
                            into["read_latency_ms"].append(energy[name] / 1e6)
                    if isinstance(energy.get("overhang_ns_max"), int):
                        into["overhang_ms"].append(energy["overhang_ns_max"] / 1e6)
    if gpu is not None and energy_valid and flags.get("crosscheck_ok", False) and flags.get("gpu_csv_enabled", False):
        differences, used = crosscheck.session_windows(results, gpu, instance.scope)
        if differences:
            entry = store.crosscheck.setdefault(instance.host, {"field": used, "sessions": []})
            entry["sessions"].append((round_, instance.label, engine, differences))
    for query in queries:
        cell = Cell(instance.host, instance.label, instance.launch, round_, suite, scale, query, engine, price_instance=instance)
        store.add(cell)
        measured = [r for r in results.get("records") or [] if isinstance(r, dict) and r.get("query") == query and r.get("phase") == "measured"]
        failures = [e for e in errors if e.get("query") == query]
        cell.n_trials = len(measured)
        if failures:
            found = classify_error(engine, failures[0].get("type", ""), failures[0].get("message", ""))
            cell.failure_class = found["class"]
            if found["outcome"] == "coverage":
                cell.coverage = found["class"]
                cell.time_reason = f"coverage:{found['class']}"
            else:
                cell.time_reason = f"integrity:{found['class']}"
            continue
        if len(measured) != instance.runs:
            cell.time_reason = "incomplete_trials"
            continue
        gate_cell = g9.get((suite, scale, engine, query))
        if gate_cell is None and instance.paper3.get("energy_measured"):
            raise AnalysisError(f"{where}/{query}: paper3-gates.json holds no G9 verdict for a cell with measured windows (a missing verdict is not an OK)")
        reasons = set((gate_cell or {}).get("reasons") or [])
        cell.g9_reasons = tuple(sorted(reasons))
        if "window_rule_violation" in reasons:
            cell.time_reason = "window_rule_violation"
            continue
        blocks = []
        for record in measured:
            block = record.get("energy_window")
            if not isinstance(block, dict):
                raise AnalysisError(f"{where}: a measured record without energy_window: not a Paper 3 window-mode record")
            blocks.append((block, trial_time(block)))
        times = [t for _, t in blocks]
        if any(t is None for t in times):
            cell.time_reason = "incomplete_trials"
            continue
        cell.time_s = statistics.median(times)
        cell.trial_times = tuple(times)
        below_resolution = bool(reasons & {"window_below_10s", "per_run_below_t_min"})
        trial_energy, trial_reason = {}, {}                           # series -> [per-run J or None per trial]
        for block, _ in blocks:
            counter = counter_series(block, instance, f"{where}/{query}")
            if counter is not None:
                tag, joules, why = counter
                trial_energy.setdefault(tag, []).append(joules)
                trial_reason.setdefault(tag, why)
            if zone_sets:
                for key, joules in window_rapl(block, series_by_zone, zone_sets).items():
                    trial_energy.setdefault(key, []).append(joules)
                    if joules is None:
                        trial_reason.setdefault(key, "energy_invalid")
        for key, joules in trial_energy.items():
            tag = SERIES_TAG.get(key, key)
            entry = EnergyCell(key, tag, idle_w=idle.get(key), trial_j=tuple(joules))
            cell.energy[key] = entry
            if not energy_valid:
                entry.total_reason = entry.above_reason = "g6_session_invalid"
            elif below_resolution:
                entry.total_reason = entry.above_reason = "below_resolution"
            elif any(j is None for j in joules):
                entry.total_reason = entry.above_reason = trial_reason.get(key) or "energy_invalid"
            else:
                entry.total_j = statistics.median(joules)
                if not above_reported(instance, engine, key):
                    entry.above_reason = "not_reported_idle_gpu"
                elif "all" in unreliable or tag in unreliable:
                    entry.above_reason = "above_idle_unreliable"
                elif entry.idle_w is None:
                    entry.above_reason = "idle_window_missing"
                else:
                    entry.above_j = statistics.median([j - entry.idle_w * t for j, t in zip(joules, times)])
        if instance.paper3.get("energy_measured") and not trial_energy:
            store.warnings.append(f"{where}: no energy series on a host that measures energy")


# ---- the whole tree --------------------------------------------------------------------------------------------------------------

def read_gpu_stream(root: Path, instance: Instance):
    """gpu.csv of a suite run root with its UTC check (environment.json monitor_tz_env), or None: the cross-check is a GPU-host diagnostic only."""
    if instance.kind != "gpu" or not (root / "gpu.csv").is_file():
        return None
    environment = read_json(root / "environment.json") or {}
    if ((environment.get("gpu_telemetry") or {}).get("monitor_tz_env")) != "UTC":
        return None
    return pg.read_gpu_csv(root / "gpu.csv")


def build_cells(tree, *, allow_synthetic: bool = False) -> CellStore:
    """Every cell of every accepted round (confirmatory and sensitivity) of every instance of the tree."""
    store = CellStore()
    seen = set()
    for host in tree.hosts.values():
        for instance in host.instances:
            if instance.synthetic and not allow_synthetic:
                raise AnalysisError(f"{instance.label}: not a formal campaign (campaign.json formal is not true, or marked synthetic); "
                                    "pass allow_synthetic for a test tree")
            for number, attempt in sorted(instance.accepted.items()):
                key = (instance.label, number)
                if key in seen:
                    continue
                seen.add(key)
                report = load_gate_report(attempt)
                check_t_min(instance, report, attempt)
                session_gates, g9 = gate_views(report)
                for suite in sorted(instance.suites):
                    root = attempt.path / suite
                    schedule = read_json(root / "schedule.json")
                    if not isinstance(schedule, list):
                        raise AnalysisError(f"{root}/schedule.json is missing or unreadable in an accepted round")
                    sources = read_json(root / "power_sources.json")
                    rapl = None
                    if instance.kind == "cpu-metal":
                        if not isinstance(sources, dict) or not (root / "power.csv").is_file():
                            raise AnalysisError(f"{root}: power_sources.json or power.csv is missing on a host that measures CPU energy")
                        zone_sets = rapl_zone_sets(sources)
                        rapl = (power_telemetry.read_power_csv_series(root / "power.csv"), zone_sets)
                    gpu = read_gpu_stream(root, instance)
                    for item in schedule:
                        if not isinstance(item, dict) or "scale" not in item or "engine" not in item:
                            raise AnalysisError(f"{root}/schedule.json holds an entry without scale and engine")
                        scale = str(item["scale"])
                        scale_dir = root / scale_key(scale)
                        results = read_json(scale_dir / item["engine"] / "results.json")
                        complete = item.get("status") == "complete" and item.get("returncode") == 0
                        if not isinstance(results, dict):
                            if complete:
                                raise AnalysisError(f"{scale_dir / item['engine']}/results.json is missing or unreadable in an accepted round")
                            results = None
                        if results is not None and results.get("synthetic_smoke") is True:
                            raise AnalysisError(f"{root}: results.json is marked synthetic_smoke (the harness smoke test's data never enter an analysis)")
                        build_session(store, instance, number, suite, scale, item, results, session_gates.get((suite, scale, item["engine"])), g9, rapl,
                                      gpu, scale_dir)
    return store
