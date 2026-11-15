#!/usr/bin/env python3
"""Paper 3 integrity and telemetry gates G6-G12 (docs/PAPER3_ENERGY_DESIGN.md section 4), on finished run directories.

A run directory is one run_experiment.py output root (one suite of one round: schedule.json, environment.json, gpu.csv,
power.csv, power_sources.json and sf<scale>/<engine>/results.json per session). The gates read those files and nothing else;
they never compare engines or hosts (section 4 title, 7.3) and the report holds no energy, power or cost magnitude: only
outcomes, counts, ratios of sample coverage, onset lags and read latencies, the QA views section 4 allows before the campaign ends.

  gate  rule (section 4)                                              on failure
  G6    counters match the probe-declared sources, none missing or    session energy invalid; the ROUND fails
        non-monotone; every sampler series advancing; launch,         (repeated once, then stop), no substitution
        completed and process-exit stamps with counter readings
  G7    trapezoid windows >= 80% of the expected 200 ms samples,      cross-check flagged; counter value unaffected
        no gap > 1 s
  G8    activity-onset lag within +-0.4 s (median), first-third vs    gpu.csv values disabled for the session
        last-third drift <= 0.2 s; chronyc tracking logged            (also when not evaluable: no two onsets)
  G9    window rule and the host's t_min met                          the cell is "below resolution"
  G10   idle pre/post mean power within 10% per scope                 above-idle flagged unreliable; total unaffected
  G11   throttle reasons, power limit, temperature logged             flagged, not excluded
  G12   no foreign GPU compute process; nothing else scheduled on     integrity failure: the ROUND fails
        C-GCP

Interpretations the design leaves open are named where they are made (INTERPRETATIONS) and listed in the run report; none
is a threshold of the design. t_min comes from an explicit per-host config (paper3_hosts.json); a host whose t_min or
probe-declared sources are unset is refused, not evaluated.

  python paper3_gates.py --host h100 --config paper3_hosts.json --run-dir R/baseline --run-dir R/complex --output R/paper3-gates.json
Exit status: 0 no round-failing gate failed, 1 G6 or G12 failed, 2 usage error or an unset/invalid host config.

  python paper3_gates.py --capture-exclusivity [--gpu]      # what is scheduled on this host now: the items to review for the
                                                             # allow-list of a CPU-only host (exclusive_use.allowed_scheduled)
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import sys

import power_telemetry

FORMAT = 1
GATES = ("G6", "G7", "G8", "G9", "G10", "G11", "G12")
ROUND_FAILING = ("G6", "G12")
FLAG = {"G6": "energy_valid", "G7": "crosscheck_ok", "G8": "gpu_csv_enabled", "G9": "resolution_ok",
        "G10": "above_idle_reliable", "G11": "power_state_logged", "G12": "exclusive_use"}
CONSEQUENCE = {"G6": "session energy invalid; round fails (repeated once, then stop); no substitution",
               "G7": "cross-check flagged; counter value unaffected",
               "G8": "gpu.csv values disabled for the session",
               "G9": "cell is below measurement resolution",
               "G10": "above-idle flagged unreliable; total unaffected",
               "G11": "flagged, not excluded",
               "G12": "integrity failure; round fails"}

# ---- the thresholds of section 4 and 2.1 (design numbers, not tuned) ---------------------------------------------
WINDOW_TARGET_S = 10.0           # section 2.1: a window runs until its summed timed time reaches this ...
WINDOW_MAX_RUNS = 50             # ... or this many runs have been made
G7_MIN_WINDOW_S = 10.0           # "on windows >= 10 s only" (section 2.3)
G7_MIN_COVERAGE = 0.80
G7_MAX_GAP_S = 1.0
G8_MIN_WINDOW_S = 5.0
G8_UTILIZATION_PERCENT = 10.0
G8_MEDIAN_LAG_S = 0.4
G8_DRIFT_S = 0.2
G8_QUIET_BEFORE_S = 1.0          # the onset is searched from this long before the stamped start (the probe tool's window) ...
G8_SEARCH_AFTER_S = 3.0          # ... to this long after it
G10_MAX_REL_DIFF = 0.10
SAMPLER_GAP_S = 30.0             # section 2.4: a window containing a sample more than 30 s after the previous one is invalid
SAMPLER_STALL_S = 5.0            # power_telemetry.STALL_SECONDS: an energy counter flat for longer than this is stalled
DEFAULT_INTERVAL_MS = 200.0
SCOPE_TAG = {"board": "S1-counter", "module": "S1-module", "none": "none"}
SCOPES = tuple(SCOPE_TAG)
IDLE_LABELS = ("before_warmups", "after_last_trial")
THROTTLE_COLUMNS = ("clocks_event_reasons.active", "clocks_throttle_reasons.active")
TEMPERATURE_COLUMN = "temperature.gpu"
GPU_CSV_TIME_FORMAT = "%Y/%m/%d %H:%M:%S.%f"
POWER_LIMIT_LINE = re.compile(r"^\s*Current Power Limit\s*:\s*(?P<watts>\d+(?:\.\d+)?)\s*W\s*$", re.M)

# Where the gates look in the records (the harness's layout: run_experiment.py schedule items, worker.py results.json).
COUNTER_BLOCK = "gpu_energy_counter"
IDLE_BLOCK = "idle_windows"
SESSION_COUNTERS = "session_counters"             # results.json: engine_ready and completed, each with utc, monotonic_ns, gpu_energy_read
SESSION_COUNTER_LABELS = ("engine_ready", "completed")
RUNNER_STAMPS = ("launch", "process_exit")        # schedule item: {"utc", "monotonic_ns"}
POWER_STATE_ITEM_KEY = "gpu_power_state"
PERFORMANCE_STATE_ITEM_KEY = "gpu_performance_state"
CLOCK_SYNC_KEY = "clock_sync"                     # schedule item: {"start": chronyc tracking record, "end": ...}
G8_PULSES_KEY = "gpu_pulses"                      # schedule item: {"start": pulse, "end": pulse}, a pulse = {"ok", "launch_utc", "done_utc", ...} (gpu_pulse.py)
COMPUTE_APPS_END_KEY = "gpu_compute_apps_end"

INTERPRETATIONS = {
    "G6.liveness": "liveness is judged per session over the runner's launch and process-exit stamps with power_telemetry.energy_between_uj "
                   "(the analysis's own function: too few samples, a stamp outside the sampled range or a sample flagged decrease or "
                   "gap = no session energy), plus no backwards step, no gap over 30 s, the counter advancing and never flat over 5 s; "
                   "the sampler's invocation-wide verdicts are information only",
    "G6.incomplete_session": "a session whose schedule status is not complete (a coverage outcome or a failure) is judged by G1/A1, "
                             "not G6: G6 is not_applicable for it",
    "G7.scope": "applied to the gpu.csv trapezoid cross-check (measured windows and idle windows >= 10 s); the RAPL series are "
                "judged by G6 (flags, liveness), not G7",
    "G7.gap": "a gap is the spacing of consecutive samples whose interval overlaps the window, boundary intervals included",
    "G8.clean_onset": "the onset is the first utilization.gpu >= 10% sample in [start - 1 s, start + 3 s] (the probe tool's search, "
                      "signed lag); a window whose search range already starts active follows earlier activity and gives no onset "
                      "(counted, not used); with fewer than two usable window onsets a session falls back to its load pulses",
    "G8.drift": "first-third versus last-third = median lag of the first max(1, n // 3) usable onsets minus that of the last max(1, "
                "n // 3); needs n >= 2 (the probe tool reports no drift below six onsets and passes; the gate evaluates it)",
    "G8.chrony": "the design says the offset is logged, with no threshold: a missing log fails G8, a logged one never does",
    "G8.not_evaluable": "a session with fewer than two onsets from its windows and its pulses together is not_evaluable: counted in the QA "
                        "table, its gpu.csv values are disabled like a failed session's, and it is never a pass",
    "G9.median_trial": "the median trial of a query is the one with the median per-run time of its trials",
    "G9.window_rule": "a window that does not follow the stop rule (n < 50 needs sum of the first n-1 runs < target <= sum of all n) "
                      "is a rule violation, reported as its own reason; the cell is not usable",
    "G10.difference": "relative difference = |post - pre| / mean(pre, post), the pair difference of the P5 probe",
    "G10.scopes": "the GPU counter, and on a RAPL host S2-all (the package and DRAM zones of every socket in the sidecar, else every "
                  "series) and S2-pinned (power_telemetry.pinned_energy_zones) where attribution is possible, each summed by "
                  "power_telemetry.s2_pinned_energy_j; a scope that exists on the host but has no valid energy in both idle windows "
                  "(for instance a window that touches a flagged sample) makes the session's above-idle unreliable",
    "G11.logged": "G11 passes when the throttle reasons, the temperature and the power-limit text are logged for the session; "
                  "throttling actually observed is reported as a count, not judged",
    "G12.scheduled": "'nothing else scheduled' is judged against an explicit allow-list in the host config (cron, at, systemd timers "
                     "captured at the start and end of the round)",
}


class GateConfigError(ValueError):
    """The host config is invalid or leaves a value the gates need unset: the host is refused, not evaluated."""


# ---- explicit value helpers (no silent type inference) -------------------------------------------------------------

def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def parse_utc(text):
    """Aware UTC datetime of an ISO-8601 stamp with an explicit offset or Z, else None (a naive stamp is not UTC)."""
    if not isinstance(text, str) or not text:
        return None
    candidate = text[:-1] + "+00:00" if text[-1:] in ("Z", "z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def epoch(text):
    parsed = parse_utc(text)
    return parsed.timestamp() if parsed is not None else None


def median(values):
    return statistics.median(values) if values else None


# ---- host config -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class HostGates:
    """One host's entry of the explicit config: what the gates compare a session with."""
    host: str
    kind: str                         # gpu | cpu-metal | cpu-vm
    energy_measured: bool
    t_min_s: object                   # positive number, None (unset) or "not_applicable"
    gpu_source: object                # str, "none" or None (unset)
    gpu_scope: object                 # board | module | none, or None (unset)
    cpu_source: object                # str, "none" or None (unset)
    cpu_series: object                # int or None (unset); required when cpu_source is a source
    allowed_scheduled: object         # list of item names or None (unset); required on cpu-metal
    idle_windows: bool                # the host's sessions hold the two engine-ready idle windows

    def unset(self) -> list:
        """Names of the values the gates need on this host that the config leaves unset."""
        missing = []
        if self.energy_measured and not (is_number(self.t_min_s) and self.t_min_s > 0):
            missing.append("t_min_s")
        if self.energy_measured:
            for name in ("gpu_source", "gpu_scope", "cpu_source"):
                if getattr(self, name) is None:
                    missing.append(name)
            if self.cpu_source not in (None, "none") and not (is_int(self.cpu_series) and self.cpu_series > 0):
                missing.append("cpu_series")
            if self.kind == "cpu-metal" and self.allowed_scheduled is None:
                missing.append("allowed_scheduled")
        return missing

    def require_ready(self) -> "HostGates":
        missing = self.unset()
        if missing:
            raise GateConfigError(f"host {self.host!r}: unset in the host config: {', '.join(missing)}; the gates refuse to evaluate "
                                  "a host whose resolution limit or probe-declared sources are not set (section 2.1, 4)")
        return self


_ENTRY_KEYS = {"kind", "energy_measured", "t_min_s", "t_min_basis", "gpu_counter", "cpu_energy", "exclusive_use", "idle_windows"}


def parse_host_entry(host: str, entry) -> HostGates:
    if not isinstance(entry, dict):
        raise GateConfigError(f"host {host!r}: entry must be an object")
    unknown = sorted(set(entry) - _ENTRY_KEYS)
    if unknown:
        raise GateConfigError(f"host {host!r}: unknown keys {unknown}")
    kind = entry.get("kind")
    if kind not in ("gpu", "cpu-metal", "cpu-vm"):
        raise GateConfigError(f"host {host!r}: kind {kind!r} is not gpu, cpu-metal or cpu-vm")
    for key in ("energy_measured", "idle_windows"):
        if not isinstance(entry.get(key), bool):
            raise GateConfigError(f"host {host!r}: {key} must be true or false")
    t_min = entry.get("t_min_s", "missing")
    if t_min == "missing":
        raise GateConfigError(f"host {host!r}: t_min_s is absent (use null for 'pending', 'not_applicable' for a time-only host)")
    if not (t_min is None or t_min == "not_applicable" or (is_number(t_min) and t_min > 0)):
        raise GateConfigError(f"host {host!r}: t_min_s {t_min!r} is not a positive number, null or 'not_applicable'")
    if t_min == "not_applicable" and entry["energy_measured"]:
        raise GateConfigError(f"host {host!r}: t_min_s 'not_applicable' only for a host that measures no energy")
    counter, cpu, exclusive = (entry.get(k) for k in ("gpu_counter", "cpu_energy", "exclusive_use"))
    for name, block, keys in (("gpu_counter", counter, {"source", "scope"}), ("cpu_energy", cpu, {"source", "series"}),
                              ("exclusive_use", exclusive, {"allowed_scheduled"})):
        if not isinstance(block, dict) or set(block) != keys:
            raise GateConfigError(f"host {host!r}: {name} must be an object with exactly the keys {sorted(keys)}")
    scope = counter["scope"]
    if scope is not None and scope not in SCOPES:
        raise GateConfigError(f"host {host!r}: gpu_counter.scope {scope!r} is not one of {SCOPES}")
    for name, value in (("gpu_counter.source", counter["source"]), ("cpu_energy.source", cpu["source"])):
        if value is not None and not (isinstance(value, str) and value):
            raise GateConfigError(f"host {host!r}: {name} must be a non-empty string or null")
    if cpu["series"] is not None and not (is_int(cpu["series"]) and cpu["series"] > 0):
        raise GateConfigError(f"host {host!r}: cpu_energy.series must be a positive integer or null")
    allowed = exclusive["allowed_scheduled"]
    if allowed is not None and not (isinstance(allowed, list) and all(isinstance(x, str) for x in allowed)):
        raise GateConfigError(f"host {host!r}: exclusive_use.allowed_scheduled must be a list of strings or null")
    return HostGates(host=host, kind=kind, energy_measured=entry["energy_measured"], t_min_s=t_min, gpu_source=counter["source"],
                     gpu_scope=scope, cpu_source=cpu["source"], cpu_series=cpu["series"], allowed_scheduled=allowed,
                     idle_windows=entry["idle_windows"])


def host_entry_digest(path: Path, host: str) -> str:
    """sha256 of the host's entry alone (canonical JSON), so filling in another host's pending values does not change this host's design."""
    document = read_json(path)
    entry = document.get("hosts", {}).get(host) if isinstance(document, dict) and isinstance(document.get("hosts"), dict) else None
    if entry is None:
        raise GateConfigError(f"{path}: no entry for host {host!r}")
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_host_config(path: Path, host: str) -> HostGates:
    """The host's entry of the explicit config. Raises GateConfigError for a missing file, host or malformed entry; unset
    values load as None and are refused by require_ready() when the gates need them."""
    document = read_json(path)
    if not isinstance(document, dict) or document.get("format") != 1 or not isinstance(document.get("hosts"), dict):
        raise GateConfigError(f"{path}: not a paper3 host config (expected an object with format 1 and hosts)")
    if host not in document["hosts"]:
        raise GateConfigError(f"{path}: no entry for host {host!r} (has {sorted(document['hosts'])})")
    return parse_host_entry(host, document["hosts"][host])


# ---- readers: gpu.csv, power.csv -------------------------------------------------------------------------------------

_SMI_NONE = {"", "N/A", "[N/A]", "[Not Supported]", "Not Supported", "[Unknown Error]"}
_SMI_NUMBER = re.compile(r"^(?P<value>-?\d+(?:\.\d+)?)(?:\s*(?:%|W|MiB|MHz|C))?$")


def parse_smi_value(text: str):
    """A value of one gpu.csv cell: a float, an int for a 0x... bit mask, or None when the driver printed nothing numeric."""
    text = text.strip()
    if text in _SMI_NONE:
        return None
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", text):
        return int(text, 16)
    match = _SMI_NUMBER.match(text)
    return float(match["value"]) if match else None


def read_gpu_csv(path: Path):
    """gpu.csv as {"t": [epoch s], "names": [...], "columns": {name: [value]}, "bad_rows": n, "unordered_rows": n}, or None.

    Timestamps are read as UTC: Paper 3 mode starts the poller with TZ=UTC (the caller checks environment.json)."""
    try:
        with Path(path).open(newline="") as handle:
            rows = list(csv.reader(handle))
    except OSError:
        return None
    if not rows:
        return None
    names = [re.sub(r"\s*\[[^\]]*\]\s*$", "", cell.strip()) for cell in rows[0]]
    if "timestamp" not in names:
        return None
    out = {"t": [], "names": names, "columns": {n: [] for n in names if n != "timestamp"}, "bad_rows": 0, "unordered_rows": 0}
    for raw in rows[1:]:
        if len(raw) != len(names):
            out["bad_rows"] += 1
            continue
        cells = dict(zip(names, raw))
        try:
            stamp = datetime.strptime(cells["timestamp"].strip(), GPU_CSV_TIME_FORMAT).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            out["bad_rows"] += 1
            continue
        if out["t"] and stamp < out["t"][-1]:
            out["unordered_rows"] += 1
        out["t"].append(stamp)
        for name in out["columns"]:
            out["columns"][name].append(parse_smi_value(cells[name]))
    return out


def read_power_series(path: Path):
    """power.csv as power_telemetry.read_power_csv_series() reads it (the one implementation the P6-CPU probe, the gates and the
    analysis share): {zone: {"domain", "mono", "uj", "flags"}}; None when the file cannot be read."""
    try:
        return power_telemetry.read_power_csv_series(path)
    except OSError:
        return None


# ---- sessions ----------------------------------------------------------------------------------------------------------

def scale_dir_name(scale) -> str:
    return "sf" + str(scale).replace(".", "p")


@dataclass
class Session:
    suite: str
    scale: str
    engine: str
    item: dict
    directory: Path
    results: object                   # results.json or None

    @property
    def key(self) -> str:
        return f"{self.suite}/{scale_dir_name(self.scale)}/{self.engine}"

    @property
    def complete(self) -> bool:
        return self.item.get("status") == "complete" and self.item.get("returncode") == 0

    def stamp_span_ns(self):
        """(launch, process_exit) CLOCK_MONOTONIC ns from the runner's stamps of the schedule item: the session as the sampler saw it."""
        launch, exit_ = (self.item.get(name) for name in RUNNER_STAMPS)
        values = [stamp.get("monotonic_ns") if isinstance(stamp, dict) else None for stamp in (launch, exit_)]
        return (values[0], values[1]) if all(is_int(v) for v in values) and values[1] >= values[0] else None

    def span(self):
        """(start epoch, end epoch) of the session from results.json, or None."""
        results = self.results if isinstance(self.results, dict) else {}
        start, end = epoch(results.get("started_at_utc")), epoch(results.get("completed_at_utc"))
        return (start, end) if start is not None and end is not None and end >= start else None


@dataclass
class RunData:
    root: Path
    suite: str
    host: HostGates
    schedule: list
    environment: dict
    sources: object                   # power_sources.json or None
    gpu_csv: object                   # read_gpu_csv() or None
    power_series: object              # read_power_series() or None


def load_run(root: Path, suite: str, host: HostGates):
    root = Path(root)
    schedule = read_json(root / "schedule.json")
    if not isinstance(schedule, list):
        raise GateConfigError(f"{root}: schedule.json missing or unreadable; there is no run to evaluate")
    environment = read_json(root / "environment.json")
    data = RunData(root=root, suite=suite, host=host, schedule=schedule,
                   environment=environment if isinstance(environment, dict) else {},
                   sources=read_json(root / "power_sources.json"),
                   gpu_csv=read_gpu_csv(root / "gpu.csv") if (root / "gpu.csv").is_file() else None,
                   power_series=read_power_series(root / "power.csv") if (root / "power.csv").is_file() else None)
    sessions = []
    for item in schedule:
        if not isinstance(item, dict) or "scale" not in item or "engine" not in item:
            raise GateConfigError(f"{root}/schedule.json: an entry without scale and engine")
        directory = root / scale_dir_name(item["scale"]) / item["engine"]
        results = read_json(directory / "results.json")
        sessions.append(Session(suite, str(item["scale"]), item["engine"], item, directory,
                                results if isinstance(results, dict) else None))
    return data, sessions


def counter_reads(results: dict):
    """Every GPU counter read of a session as (monotonic mid ns, mJ), and the number of reads that were needed but unusable.

    Needed: both reads of every idle window and of every run of every measured trial. Warmup reads join the monotonicity
    check when they are usable and are not counted as missing."""
    reads, unusable = [], 0

    def take(block, needed):
        nonlocal unusable
        usable = False
        if isinstance(block, dict) and not block.get("error"):
            for prefix in ("start", "end"):
                mj, bracket = block.get(f"{prefix}_mj"), block.get(f"{prefix}_read_monotonic_ns")
                if is_int(mj) and mj >= 0 and isinstance(bracket, list) and len(bracket) == 2 and all(is_int(b) for b in bracket):
                    reads.append(((bracket[0] + bracket[1]) // 2, mj))
                    usable = True
                else:
                    usable = False
                    break
        if needed and not usable:
            unusable += 1

    for label in SESSION_COUNTER_LABELS:
        entry = (results.get(SESSION_COUNTERS) or {}).get(label) if isinstance(results.get(SESSION_COUNTERS), dict) else None
        read = entry.get("gpu_energy_read") if isinstance(entry, dict) else None
        bracket = read.get("monotonic_ns") if isinstance(read, dict) else None
        if isinstance(read, dict) and not read.get("error") and is_int(read.get("mj")) and read["mj"] >= 0 \
                and isinstance(bracket, list) and len(bracket) == 2 and all(is_int(b) for b in bracket):
            reads.append(((bracket[0] + bracket[1]) // 2, read["mj"]))
    for window in results.get(IDLE_BLOCK) or []:
        take(window.get("gpu_energy") if isinstance(window, dict) else None, True)
    for record in results.get("records") or []:
        if not isinstance(record, dict):
            continue
        measured = record.get("phase") == "measured"
        runs = (record.get("energy_window") or {}).get("runs") if isinstance(record.get("energy_window"), dict) else None
        if runs:
            for run in runs:
                take(run.get("gpu_energy") if isinstance(run, dict) else None, measured)
        elif "gpu_energy" in record:
            take(record["gpu_energy"], measured)
    return reads, unusable


# ---- G6 --------------------------------------------------------------------------------------------------------------------

def check_counter(session: Session, host: HostGates) -> list:
    problems, results = [], session.results
    block = results.get(COUNTER_BLOCK)
    if not isinstance(block, dict):
        return [f"results.json has no {COUNTER_BLOCK} block"]
    if host.gpu_source == "none":
        if block.get("scope") != "none":
            problems.append(f"the host declares no GPU counter but the session ran with scope {block.get('scope')!r}")
        return problems
    if block.get("source") != host.gpu_source:
        problems.append(f"GPU counter source {block.get('source')!r}, probe-declared {host.gpu_source!r}")
    if block.get("scope") != host.gpu_scope:
        problems.append(f"GPU counter scope {block.get('scope')!r}, probe-declared {host.gpu_scope!r}")
    if block.get("scope_tag") != SCOPE_TAG.get(host.gpu_scope):
        problems.append(f"GPU counter scope tag {block.get('scope_tag')!r}, expected {SCOPE_TAG.get(host.gpu_scope)!r}")
    reads, unusable = counter_reads(results)
    if unusable:
        problems.append(f"{unusable} counter read(s) needed for an idle window or a measured run are missing, errored or negative")
    ordered = sorted(reads)
    drops = [(a, b) for a, b in zip(ordered, ordered[1:]) if b[1] < a[1]]
    if drops:
        problems.append(f"GPU counter not monotone: {len(drops)} read(s) lower than an earlier one")
    if not reads:
        problems.append("no GPU counter read in the session")
    return problems


def invocation_liveness(run: RunData) -> dict:
    """The sampler's own verdicts for the whole run_experiment.py invocation: information only (the session's own samples decide)."""
    liveness = run.sources.get("liveness") if isinstance(run.sources, dict) else None
    return {k: v.get("verdict") for k, v in sorted((liveness or {}).items()) if isinstance(v, dict)}


def check_cpu_series(session: Session, run: RunData) -> list:
    """The declared CPU energy source is the one sampled, with the declared number of series, each alive over this session.

    The window is the runner's launch and process-exit stamps and the energy is power_telemetry.energy_between_uj's, the function
    the analysis uses: a series whose session energy is None there (fewer than two samples, a stamp outside the sampled range, a
    sample flagged decrease or gap in the intervals the window uses) fails G6 with that reason. On top of it, from the samples
    inside the window: the counter never decreases, no two samples are more than 30 s apart, the counter advances, and it is
    never flat for longer than the sampler's own stall limit of 5 s."""
    host, sources = run.host, run.sources
    if host.cpu_source == "none":
        found = sources.get("cpu_energy_source") if isinstance(sources, dict) else None
        return [f"the host declares no CPU energy source but the sampler used {found!r}"] if found else []
    if not isinstance(sources, dict):
        return ["power_sources.json missing: no CPU energy source to compare with the declaration"]
    problems = []
    if sources.get("cpu_energy_source") != host.cpu_source:
        problems.append(f"CPU energy source {sources.get('cpu_energy_source')!r}, probe-declared {host.cpu_source!r}")
    sampled = sum(1 for src in sources.get("sources") or [] if isinstance(src, dict) and src.get("source") == host.cpu_source
                  for d in src.get("domains") or [] if isinstance(d, dict) and d.get("sampled") is True)
    if sampled != host.cpu_series:
        problems.append(f"{sampled} sampled series, probe-declared {host.cpu_series}")
    if run.power_series is None:
        problems.append("power.csv missing or unreadable")
        return problems
    if len(run.power_series) != host.cpu_series:
        problems.append(f"power.csv holds {len(run.power_series)} counter series, probe-declared {host.cpu_series}")
    window = session.stamp_span_ns()
    if window is None:
        problems.append("no launch and process-exit stamps to cut the CPU series by")
        return problems
    for zone, series in sorted(run.power_series.items()):
        energy = power_telemetry.energy_between_uj(series, *window)
        if energy["uj"] is None:
            problems.append(f"series {zone}: session energy not computable: {energy['reason']}")
            continue
        inside = [i for i, t in enumerate(series["mono"]) if window[0] <= t <= window[1]]
        values, times = [series["uj"][i] for i in inside], [series["mono"][i] for i in inside]
        if any(b < a for a, b in zip(values, values[1:])):
            problems.append(f"series {zone}: counter not monotone inside the session")
        if energy["max_gap_seconds"] is not None and energy["max_gap_seconds"] > SAMPLER_GAP_S:
            problems.append(f"series {zone}: {energy['max_gap_seconds']:.1f} s between two samples inside the session (limit {SAMPLER_GAP_S:g} s)")
        if len(values) >= 2 and values[-1] <= values[0]:
            problems.append(f"series {zone}: the counter did not advance inside the session")
        elif len(values) >= 2:
            flat, start = 0.0, 0
            for k in range(1, len(values) + 1):
                if k == len(values) or values[k] != values[start]:
                    flat = max(flat, (times[k - 1] - times[start]) / 1e9)
                    start = k
            if flat > SAMPLER_STALL_S:
                problems.append(f"series {zone}: the counter was flat for {flat:.1f} s inside the session (limit {SAMPLER_STALL_S:g} s)")
    return problems


def stamp_of(entry):
    """(utc epoch, monotonic ns) of a {"utc", "monotonic_ns"} stamp, or None."""
    if isinstance(entry, dict) and parse_utc(entry.get("utc")) is not None and is_int(entry.get("monotonic_ns")):
        return epoch(entry["utc"]), entry["monotonic_ns"]
    return None


def check_stamps(session: Session, run: RunData) -> list:
    """Launch and process-exit stamps (runner), engine-ready and completed stamps with their counter readings (worker), in order.

    The runner has no GPU context, so the GPU counter is read by the worker at engine_ready and completed; the CPU series are the
    sampler's, cut by the launch and exit stamps in check_cpu_series()."""
    host, results, problems = run.host, session.results, []
    marks = {}
    for name in RUNNER_STAMPS:
        marks[name] = stamp_of(session.item.get(name))
        if marks[name] is None:
            problems.append(f"{name} stamp missing or malformed in schedule.json")
    counters = results.get(SESSION_COUNTERS)
    for label in SESSION_COUNTER_LABELS:
        entry = counters.get(label) if isinstance(counters, dict) else None
        marks[label] = stamp_of(entry)
        if marks[label] is None:
            problems.append(f"session_counters.{label} stamp missing or malformed")
            continue
        if host.gpu_source != "none":
            read = entry.get("gpu_energy_read")
            bracket = read.get("monotonic_ns") if isinstance(read, dict) else None
            if not (isinstance(read, dict) and is_int(read.get("mj")) and read["mj"] >= 0 and not read.get("error")
                    and isinstance(bracket, list) and len(bracket) == 2 and all(is_int(b) for b in bracket)):
                problems.append(f"session_counters.{label} holds no usable GPU counter reading")
            elif read.get("source") != host.gpu_source or read.get("scope_tag") != SCOPE_TAG.get(host.gpu_scope):
                problems.append(f"session_counters.{label}: counter {read.get('source')!r}/{read.get('scope_tag')!r}, "
                                f"probe-declared {host.gpu_source!r}/{SCOPE_TAG.get(host.gpu_scope)!r}")
    if not parse_utc(results.get("completed_at_utc")):
        problems.append("completed_at_utc missing or not UTC")
    order = ("launch", "engine_ready", "completed", "process_exit")
    if all(marks.get(name) for name in order):
        monotonic = [marks[name][1] for name in order]
        if any(b < a for a, b in zip(monotonic, monotonic[1:])):
            problems.append("session stamps out of order: expected launch <= engine_ready <= completed <= process_exit")
    return problems


def gate_g6(session: Session, run: RunData) -> dict:
    host = run.host
    if not host.energy_measured:
        return {"outcome": "not_applicable", "reasons": ["host measures no energy"]}
    if not session.complete:
        return {"outcome": "not_applicable", "reasons": ["session did not complete (judged by G1/A1)"]}
    if session.results is None:
        return {"outcome": "fail", "problems": ["results.json missing or unreadable"]}
    problems = check_counter(session, host) + check_cpu_series(session, run) + check_stamps(session, run)
    result = {"outcome": "fail" if problems else "pass", "problems": problems}
    if host.cpu_source not in (None, "none"):
        result["invocation_liveness"] = invocation_liveness(run)       # information: the sampler's verdicts for the whole invocation
    return result


# ---- G7 --------------------------------------------------------------------------------------------------------------------

def utc_stamped(run: RunData) -> bool:
    telemetry = run.environment.get("gpu_telemetry")
    return isinstance(telemetry, dict) and telemetry.get("monitor_tz_env") == "UTC"


def interval_s(run: RunData) -> float:
    value = (run.environment.get("gpu_telemetry") or {}).get("interval_ms")
    return (value if is_number(value) and value > 0 else DEFAULT_INTERVAL_MS) / 1000.0


def session_windows(session: Session) -> list:
    """The trapezoid windows of a session as {label, kind, start, end} (epoch s): measured windows and idle windows."""
    out, results = [], session.results or {}
    for record in results.get("records") or []:
        window = record.get("energy_window") if isinstance(record, dict) else None
        span = window.get("span") if isinstance(window, dict) else None
        if record.get("phase") != "measured" or not isinstance(span, dict):
            continue
        start, end = epoch(span.get("start_utc")), epoch(span.get("end_utc"))
        if start is not None and end is not None and end > start:
            out.append({"label": f"{record.get('query')}#{record.get('trial')}", "kind": "measured", "start": start, "end": end,
                        "window_seconds": window.get("window_seconds"), "n": window.get("n")})
    for window in results.get(IDLE_BLOCK) or []:
        if isinstance(window, dict):
            start, end = epoch(window.get("start_utc")), epoch(window.get("end_utc"))
            if start is not None and end is not None and end > start:
                out.append({"label": str(window.get("label")), "kind": "idle", "start": start, "end": end})
    return out


def window_coverage(times, start: float, end: float, interval: float) -> dict:
    """Samples inside [start, end] against the expected count, and the longest spacing of the samples around the window."""
    inside = [t for t in times if start <= t <= end]
    expected = (end - start) / interval
    spacing = [b - a for a, b in zip(times, times[1:]) if b >= start and a <= end]
    return {"samples": len(inside), "expected": expected, "coverage": len(inside) / expected if expected > 0 else None,
            "max_gap_s": max(spacing) if spacing else None}


def gate_g7(session: Session, run: RunData) -> dict:
    if run.gpu_csv is None or not run.host.energy_measured or run.host.kind != "gpu":
        return {"outcome": "not_applicable", "reasons": ["no GPU telemetry stream on this host"]}
    if session.results is None:
        return {"outcome": "not_applicable", "reasons": ["no results.json"]}
    if not utc_stamped(run):
        return {"outcome": "fail", "problems": ["gpu.csv is not stamped in UTC (environment.json gpu_telemetry.monitor_tz_env)"]}
    interval = interval_s(run)
    windows = [w for w in session_windows(session) if w["end"] - w["start"] >= G7_MIN_WINDOW_S]
    flagged, details = [], []
    for w in windows:
        cov = window_coverage(run.gpu_csv["t"], w["start"], w["end"], interval)
        bad = cov["coverage"] is None or cov["coverage"] < G7_MIN_COVERAGE or cov["max_gap_s"] is None or cov["max_gap_s"] > G7_MAX_GAP_S
        details.append({"label": w["label"], "kind": w["kind"], "seconds": round(w["end"] - w["start"], 3),
                        "coverage": None if cov["coverage"] is None else round(cov["coverage"], 4),
                        "max_gap_s": None if cov["max_gap_s"] is None else round(cov["max_gap_s"], 3)})
        if bad:
            flagged.append(details[-1])
    if not windows:
        return {"outcome": "not_applicable", "reasons": ["no window of 10 s or longer"], "windows": 0}
    return {"outcome": "fail" if flagged else "pass", "windows": len(windows), "flagged_windows": flagged}


# ---- G8 --------------------------------------------------------------------------------------------------------------------

def onset_lag(gpu: dict, start: float):
    """(lag s, status) of the first gpu.csv sample with utilization.gpu >= 10% within [start - 1 s, start + 3 s], minus start.

    The lag is signed, as in the probe tool (a negative lag is a row stamped before the window began). status: ok |
    busy_before (the GPU was already active at the first sample of the search range, so earlier activity, not this window,
    is what the stream shows: not a usable onset) | missed (no sample >= 10% in the range) | no_utilization_column."""
    util = gpu["columns"].get("utilization.gpu")
    if util is None:
        return None, "no_utilization_column"
    window = [(t, u) for t, u in zip(gpu["t"], util) if u is not None and start - G8_QUIET_BEFORE_S <= t <= start + G8_SEARCH_AFTER_S]
    if not window:
        return None, "missed"
    if window[0][1] >= G8_UTILIZATION_PERCENT:
        return None, "busy_before"
    hit = next((t for t, u in window if u >= G8_UTILIZATION_PERCENT), None)
    return (None, "missed") if hit is None else (hit - start, "ok")


def thirds_drift(lags):
    """Median lag of the first third minus that of the last third; None below two lags (INTERPRETATIONS G8.drift)."""
    if len(lags) < 2:
        return None
    third = max(1, len(lags) // 3)
    return median(lags[:third]) - median(lags[-third:])


def g8_verdict(lags, onsets_missed) -> dict:
    usable = len(lags)
    if usable < 2:
        return {"outcome": "fail", "problems": [f"{usable} usable onset(s): the median lag and the drift need at least two"
                                                + (f" ({onsets_missed} without an onset)" if onsets_missed else "")]}
    med, drift = median(lags), thirds_drift(lags)
    problems = []
    if onsets_missed:
        problems.append(f"{onsets_missed} window(s) had no GPU activity within 3 s of the stamped start")
    if abs(med) > G8_MEDIAN_LAG_S:
        problems.append(f"median onset lag {med:+.3f} s outside +-{G8_MEDIAN_LAG_S} s")
    if abs(drift) > G8_DRIFT_S:
        problems.append(f"first-third minus last-third drift {drift:+.3f} s above {G8_DRIFT_S} s")
    return {"outcome": "fail" if problems else "pass", "problems": problems, "usable_onsets": usable,
            "median_lag_s": round(med, 4), "drift_s": round(drift, 4)}


def pulse_lags(gpu: dict, item: dict):
    """Onset lags (s) of the session's 3 s load pulses against gpu.csv (start pulse, then end pulse), the pulses that left no
    onset, and the number of pulse records.

    A pulse record is gpu_pulse.py's: ok, and the child's own launch_utc stamp, which is the start the first sample >= 10% follows.
    A record that is not ok (no CUDA, no device) is a pulse without an onset."""
    pulses = item.get(G8_PULSES_KEY)
    records = [pulses.get(label) for label in ("start", "end")] if isinstance(pulses, dict) else []
    records = [r for r in records if isinstance(r, dict)]
    lags, missed = [], 0
    for record in records:
        launched = epoch(record.get("launch_utc")) if record.get("ok") is True else None
        lag, status = onset_lag(gpu, launched) if launched is not None else (None, "missed")
        if status == "ok":
            lags.append(lag)
        else:
            missed += 1
    return lags, missed, len(records)


def chrony_problems(item: dict) -> list:
    """The offset of the system clock from NTP time is logged at the session's start and end (the design sets no threshold)."""
    sync = item.get(CLOCK_SYNC_KEY)
    problems = []
    for label in ("start", "end"):
        record = sync.get(label) if isinstance(sync, dict) else None
        if not (isinstance(record, dict) and record.get("returncode") == 0 and is_number(record.get("system_time_offset_seconds"))):
            problems.append(f"chronyc tracking offset not logged at session {label}")
    return problems


def gate_g8(session: Session, run: RunData) -> dict:
    """Clock alignment from onset times only. Evidence, in this order: the session's GPU-engine windows of 5 s or longer with a
    usable onset (two or more), else its start and end load pulses (two onsets). Outcomes: pass, fail (a misaligned or drifting
    clock, or no chrony offset logged), not_evaluable (fewer than two onsets from either source: reported, never a pass)."""
    if run.gpu_csv is None or not run.host.energy_measured or run.host.kind != "gpu":
        return {"outcome": "not_applicable", "reasons": ["no GPU telemetry stream on this host"]}
    if session.results is None or not session.complete:
        return {"outcome": "not_applicable", "reasons": ["session did not complete"]}
    if not utc_stamped(run):
        return {"outcome": "fail", "problems": ["gpu.csv is not stamped in UTC (environment.json gpu_telemetry.monitor_tz_env)"]}
    skipped = {"busy_before": 0, "missed": 0, "no_utilization_column": 0}
    lags, windows = [], []
    if session.engine in ("polars_gpu", "spark_gpu"):      # a CPU engine leaves no onset in utilization.gpu: it uses the pulses
        windows = [w for w in session_windows(session) if w["kind"] == "measured" and (w.get("window_seconds") or 0) >= G8_MIN_WINDOW_S]
        for w in sorted(windows, key=lambda w: w["start"]):
            lag, status = onset_lag(run.gpu_csv, w["start"])
            if status == "ok":
                lags.append(lag)
            else:
                skipped[status] += 1
    method, missed, pulses = "windows", skipped["missed"], 0
    if len(lags) < 2:
        method = "pulses"
        lags, missed, pulses = pulse_lags(run.gpu_csv, session.item)
        if len(lags) < 2:
            return {"outcome": "not_evaluable", "method": "none", "windows_considered": len(windows), "windows_skipped": skipped,
                    "pulses": pulses, "reasons": [f"{len(windows)} window(s) of 5 s or longer without two usable onsets and "
                                                  f"{pulses} pulse record(s) with {len(lags)} onset(s): the lag and the drift need two"]}
    verdict = g8_verdict(lags, missed)
    verdict["problems"] = verdict.get("problems", []) + chrony_problems(session.item)
    verdict["outcome"] = "fail" if verdict["problems"] else "pass"
    return {**verdict, "method": method, "windows_considered": len(windows), "windows_skipped": skipped, "pulses": pulses}


# ---- G9 --------------------------------------------------------------------------------------------------------------------

def window_rule_violation(runs, target: float = WINDOW_TARGET_S, cap: int = WINDOW_MAX_RUNS):
    """None when a window's runs are what the stop rule makes: the first n-1 sum below the target and either all n reach it
    or n is the cap. Written from section 2.1, independent of power_telemetry.energy_window_stop_violation."""
    seconds = [r.get("seconds") if isinstance(r, dict) else None for r in runs]
    if not seconds or not all(is_number(s) and s > 0 for s in seconds):
        return "a run without a positive time"
    n = len(seconds)
    if n > cap:
        return f"{n} runs exceed the cap of {cap}"
    if n > 1 and math.fsum(seconds[:-1]) >= target:
        return f"the first {n - 1} runs already sum to {math.fsum(seconds[:-1]):.6g} s >= {target:g} s"
    if n < cap and math.fsum(seconds) < target:
        return f"stopped after {n} runs at {math.fsum(seconds):.6g} s < {target:g} s with fewer than {cap} runs"
    return None


def window_inconsistency(block: dict):
    """What differs between a window block's summary and its own runs (n, summed time, per-run time, stop reason), or None."""
    runs = block.get("runs") or []
    seconds = [r.get("seconds") if isinstance(r, dict) else None for r in runs]
    if not seconds or not all(is_number(x) for x in seconds):
        return "runs without times"
    total, n = math.fsum(seconds), len(seconds)
    problems = []
    if block.get("n") != n:
        problems.append(f"n {block.get('n')!r} but {n} runs")
    for name, expected in (("window_seconds", total), ("per_run_seconds", total / n)):
        value = block.get(name)
        if not is_number(value) or abs(value - expected) > 1e-9 * max(1.0, expected):
            problems.append(f"{name} {value!r} but the runs give {expected:.9g}")
    target = block.get("target_seconds")
    if is_number(target):
        reason = "target_reached" if total >= target else "max_runs"
        if block.get("stop_reason") != reason:
            problems.append(f"stop_reason {block.get('stop_reason')!r} but the runs give {reason!r}")
    return "; ".join(problems) or None


def gate_g9(session: Session, run: RunData):
    """One entry per (query) cell of a session: median trial's window and per-run time against the target and t_min."""
    host = run.host
    if not host.energy_measured or session.results is None:
        return []
    t_min = host.t_min_s
    by_query = {}
    for record in session.results.get("records") or []:
        if isinstance(record, dict) and record.get("phase") == "measured" and isinstance(record.get("energy_window"), dict):
            by_query.setdefault(record.get("query"), []).append(record["energy_window"])
    cells = []
    for query, windows in sorted(by_query.items(), key=lambda kv: str(kv[0])):
        violations = [v for v in (window_rule_violation(w.get("runs") or []) for w in windows) if v]
        violations += [v for v in (window_inconsistency(w) for w in windows) if v]
        targets = {w.get("target_seconds") for w in windows}
        if targets != {WINDOW_TARGET_S}:
            violations.append(f"window target {sorted(map(str, targets))} instead of {WINDOW_TARGET_S:g} s")
        # the decision uses times recomputed from each trial's own runs, never the block's summary (which is checked against them)
        trials = []
        for w in windows:
            seconds = [r.get("seconds") if isinstance(r, dict) else None for r in w.get("runs") or []]
            if seconds and all(is_number(x) and x > 0 for x in seconds):
                total = math.fsum(seconds)
                trials.append({"window": total, "per_run": total / len(seconds), "n": len(seconds), "stop_reason": w.get("stop_reason")})
        if not trials:
            cells.append({"suite": session.suite, "scale": session.scale, "engine": session.engine, "query": query,
                          "outcome": "fail", "reasons": ["window_rule_violation"], "trials": len(windows),
                          "violations": violations or ["no trial has usable run times"]})
            continue
        trials.sort(key=lambda t: t["per_run"])
        middle = trials[(len(trials) - 1) // 2]                   # the median trial (the lower one of an even number)
        reasons = []
        if violations:
            reasons.append("window_rule_violation")
        if middle["window"] < WINDOW_TARGET_S:
            reasons.append("window_below_10s")
        if middle["per_run"] < t_min:
            reasons.append("per_run_below_t_min")
        cells.append({"suite": session.suite, "scale": session.scale, "engine": session.engine, "query": query,
                      "outcome": "fail" if reasons else "pass", "reasons": reasons, "trials": len(windows),
                      "median_trial": {"n": middle["n"], "stop_reason": middle["stop_reason"]},
                      **({"violations": violations} if violations else {})})
    return cells


# ---- G10 -------------------------------------------------------------------------------------------------------------------

def counter_idle_power(block):
    """Mean watts of an idle window from its counter reads, recomputed here (midpoint of each read bracket)."""
    if not isinstance(block, dict) or block.get("error"):
        return None
    delta, start, end = block.get("delta_mj"), block.get("start_read_monotonic_ns"), block.get("end_read_monotonic_ns")
    try:
        seconds = ((end[0] + end[1]) / 2 - (start[0] + start[1]) / 2) / 1e9
    except (TypeError, IndexError):
        return None
    return delta / 1000.0 / seconds if is_int(delta) and delta >= 0 and seconds > 0 else None


def cpu_idle_power(run: RunData, window: dict):
    """{"S2-all": W, "S2-pinned": W} of one idle window from power.csv at the window's monotonic stamps, by the shared
    power_telemetry.s2_pinned_energy_j (summed zones, joules); a scope whose window is invalid or whose zones are missing is absent."""
    sources, series = run.sources, run.power_series
    start, end = window.get("start_monotonic_ns"), window.get("end_monotonic_ns")
    if not isinstance(sources, dict) or series is None or not (is_int(start) and is_int(end) and end > start):
        return {}
    out = {}
    # S2-all: the shared package + DRAM zones of every socket; a source that is not per socket (perf-stat, turbostat) has one summed series
    all_zones = power_telemetry.all_package_dram_zones(sources) or sorted(series)
    for scope, zones in (("S2-all", all_zones), ("S2-pinned", power_telemetry.pinned_energy_zones(sources))):
        if not zones:
            continue
        energy = power_telemetry.s2_pinned_energy_j(series, zones, start, end)    # the shared sum of zones; "pinned" is the caller's choice of zones
        if energy["joules"] is not None:                 # the shared function gives None, with a reason, for a negative window energy
            out[scope] = energy["joules"] / ((end - start) / 1e9)
    return out


def gate_g10(session: Session, run: RunData) -> dict:
    host = run.host
    if not host.energy_measured or not host.idle_windows:
        return {"outcome": "not_applicable", "reasons": ["host has no engine-ready idle windows"]}
    if session.results is None or not session.complete:
        return {"outcome": "not_applicable", "reasons": ["session did not complete"]}
    windows = {w.get("label"): w for w in session.results.get(IDLE_BLOCK) or [] if isinstance(w, dict)}
    if set(IDLE_LABELS) - set(windows):
        return {"outcome": "fail", "problems": [f"idle window(s) {sorted(set(IDLE_LABELS) - set(windows))} missing: P_idle cannot be formed"],
                "unreliable_scopes": ["all"]}
    power = {}
    for label in IDLE_LABELS:
        w = windows[label]
        values = {}
        if host.gpu_scope in ("board", "module"):
            watts = counter_idle_power(w.get("gpu_energy"))
            if watts is not None:
                values[SCOPE_TAG[host.gpu_scope]] = watts
        values.update(cpu_idle_power(run, w))
        power[label] = values
    scopes = sorted(set(power[IDLE_LABELS[0]]) & set(power[IDLE_LABELS[1]]))
    expected = [SCOPE_TAG[host.gpu_scope]] if host.gpu_scope in ("board", "module") else []
    if host.cpu_source not in (None, "none"):
        expected.append("S2-all")
        if power_telemetry.pinned_energy_zones(run.sources if isinstance(run.sources, dict) else {}):
            expected.append("S2-pinned")                    # a scope that exists on this host must have an idle power in both windows
    missing = [s for s in expected if s not in scopes]
    problems, stability, unreliable = [], {}, []
    if missing:
        problems.append(f"no idle mean power for scope(s) {missing} in both windows")
        unreliable += missing
    for scope in scopes:
        pre, post = power[IDLE_LABELS[0]][scope], power[IDLE_LABELS[1]][scope]
        mean = (pre + post) / 2
        rel = abs(post - pre) / mean if mean > 0 else None
        stability[scope] = None if rel is None else round(rel, 5)
        if rel is None or rel > G10_MAX_REL_DIFF:
            unreliable.append(scope)
            problems.append(f"scope {scope}: pre/post idle mean power differ by "
                            f"{'n/a' if rel is None else f'{rel:.1%}'} (limit {G10_MAX_REL_DIFF:.0%})")
    if not scopes and not missing:
        problems.append("no scope with an idle mean power")
        unreliable.append("all")
    return {"outcome": "fail" if problems else "pass", "problems": problems, "relative_difference": stability,
            "unreliable_scopes": sorted(unreliable)}


# ---- G11 -------------------------------------------------------------------------------------------------------------------

def parse_power_limit(text: str):
    match = POWER_LIMIT_LINE.search(text or "")
    return float(match["watts"]) if match else None


def state_text_problems(session: Session, run: RunData, key: str, what: str) -> tuple:
    """(problems, texts) of an `nvidia-smi -q -d <section>` capture at the session's start and end: recorded, readable, hash intact."""
    state, problems, texts = session.item.get(key), [], {}
    for label in ("start", "end"):
        record = state.get(label) if isinstance(state, dict) else None
        if not isinstance(record, dict) or record.get("returncode") != 0 or not is_int(record.get("bytes")) or record["bytes"] <= 0:
            problems.append(f"{what} text at session {label} not captured")
            continue
        path = run.root / scale_dir_name(session.scale) / str(record.get("file"))
        try:
            text = path.read_text()
        except OSError:
            problems.append(f"{what} text at session {label}: {record.get('file')} is missing")
            continue
        if hashlib.sha256(text.encode()).hexdigest() != record.get("sha256"):
            problems.append(f"{what} text at session {label}: sha256 differs from the record")
            continue
        texts[label] = text
    return problems, texts


def gate_g11(session: Session, run: RunData) -> dict:
    host = run.host
    if not host.energy_measured or host.kind != "gpu":
        return {"outcome": "not_applicable", "reasons": ["no GPU on this host"]}
    if session.results is None or not session.complete:
        return {"outcome": "not_applicable", "reasons": ["session did not complete"]}
    problems, span = [], session.span()
    gpu = run.gpu_csv
    throttle_events = None
    if gpu is None or span is None or not utc_stamped(run):
        problems.append("gpu.csv unusable for this session (missing, or no UTC stamps or session span)")
    else:
        inside = [i for i, t in enumerate(gpu["t"]) if span[0] <= t <= span[1]]
        column = next((c for c in THROTTLE_COLUMNS if c in gpu["columns"]), None)
        if column is None or not any(gpu["columns"][column][i] is not None for i in inside):
            problems.append(f"no throttle-reason samples in gpu.csv (columns {THROTTLE_COLUMNS})")
        else:
            # bit 0x1 is GPU idle and 0x4 the applications-clocks setting: states, not throttling; count the rest
            throttle_events = sum(1 for i in inside if int(gpu["columns"][column][i] or 0) & ~0x5)
        if not any(gpu["columns"].get(TEMPERATURE_COLUMN, [None] * len(gpu["t"]))[i] is not None for i in inside):
            problems.append(f"no {TEMPERATURE_COLUMN} samples in gpu.csv")
    power_problems, power_texts = state_text_problems(session, run, POWER_STATE_ITEM_KEY, "nvidia-smi -q -d POWER")
    problems += power_problems
    limits = {label: parse_power_limit(text) for label, text in power_texts.items()}
    problems += [f"nvidia-smi -q -d POWER text at session {label} holds no Current Power Limit" for label, v in sorted(limits.items()) if v is None]
    performance_problems, _ = state_text_problems(session, run, PERFORMANCE_STATE_ITEM_KEY, "nvidia-smi -q -d PERFORMANCE")
    problems += performance_problems
    return {"outcome": "fail" if problems else "pass", "problems": problems, "throttle_samples_with_reasons": throttle_events,
            "power_limit_text_logged": sorted(k for k, v in limits.items() if v is not None)}


# ---- G12 -------------------------------------------------------------------------------------------------------------------

def gate_g12(session: Session, run: RunData) -> dict:
    host = run.host
    if not host.energy_measured:
        return {"outcome": "not_applicable", "reasons": ["host measures no energy"]}
    if host.kind != "gpu":
        return {"outcome": "not_applicable", "reasons": ["no GPU on this host; host exclusivity is judged per round"]}
    problems = []
    gate = session.item.get("gpu_idle_gate")
    if not isinstance(gate, dict):
        problems.append("no gpu_idle_gate record: foreign GPU processes were not checked before launch")
    else:
        if gate.get("idle") is not True:
            problems.append("the GPU was not idle at launch")
        if gate.get("compute_apps"):
            problems.append(f"{len(gate['compute_apps'])} compute process(es) on the GPU at launch")
        if gate.get("error"):
            problems.append("nvidia-smi could not list the compute processes at launch")
    if session.complete:                        # after the worker exited, nothing may still compute on the GPU
        end = session.item.get(COMPUTE_APPS_END_KEY)
        if not isinstance(end, dict):
            problems.append("no compute-process listing after the worker exited")
        elif end.get("compute_apps") or end.get("error"):
            problems.append("a compute process was on the GPU after the worker exited" if end.get("compute_apps")
                            else "nvidia-smi could not list the compute processes after the worker exited")
    return {"outcome": "fail" if problems else "pass", "problems": problems}


def capture_host_exclusivity(label: str, *, gpu: bool, run=None, now=None) -> dict:
    """What is on the host that the campaign did not start: compute processes on the GPU and scheduled work, as a record.

    run(command) -> {"returncode", "stdout"} is injectable. Written by the campaign driver at the start and end of a round."""
    def default_run(command):
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=60)
            return {"returncode": done.returncode, "stdout": done.stdout}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"returncode": None, "stdout": "", "error": f"{type(exc).__name__}: {exc}"[:200]}
    run = run or default_run
    stamp = (now or (lambda: datetime.now(timezone.utc)))().isoformat()
    record = {"format": 1, "label": label, "captured_utc": stamp, "gpu_compute_apps": None, "scheduled": {}}
    if gpu:
        done = run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"])
        apps = [line.strip() for line in done["stdout"].splitlines() if line.strip()]
        record["gpu_compute_apps"] = {"returncode": done["returncode"], "apps": apps if done["returncode"] == 0 else None,
                                      **({"error": done["error"]} if done.get("error") else {})}
    timers = run(["systemctl", "list-timers", "--all", "--no-pager", "--plain", "--no-legend"])
    record["scheduled"]["systemd_timers"] = (sorted({tok for line in timers["stdout"].splitlines() for tok in line.split()
                                                     if tok.endswith(".timer")}) if timers["returncode"] == 0 else None)
    cron = run(["sh", "-c", "ls -1 /etc/cron.d /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly 2>/dev/null; true"])
    record["scheduled"]["cron_files"] = sorted({line.strip() for line in cron["stdout"].splitlines()
                                                if line.strip() and not line.startswith("/") and not line.endswith(":")
                                                and line.strip() not in (".placeholder", "README")})
    crontab = run(["crontab", "-l"])
    record["scheduled"]["crontab_entries"] = ([line for line in crontab["stdout"].splitlines() if line.strip() and not line.startswith("#")]
                                              if crontab["returncode"] == 0 else [])
    at = run(["atq"])
    record["scheduled"]["at_jobs"] = [line for line in at["stdout"].splitlines() if line.strip()] if at["returncode"] == 0 else []
    return record


def scheduled_items(capture: dict) -> list:
    sched = capture.get("scheduled") or {}
    return sorted([f"timer:{t}" for t in sched.get("systemd_timers") or []] + [f"cron:{c}" for c in sched.get("cron_files") or []]
                  + [f"crontab:{e}" for e in sched.get("crontab_entries") or []] + [f"at:{a}" for a in sched.get("at_jobs") or []])


def round_exclusivity(host: HostGates, captures: list) -> dict:
    """G12 at round level, from the captures at the round's start and end (host exclusivity)."""
    if not host.energy_measured or host.kind == "cpu-vm":
        return {"outcome": "not_applicable", "reasons": ["host measures no energy"]}
    problems = []
    labels = sorted(c.get("label") for c in captures if isinstance(c, dict))
    if labels != ["end", "start"]:
        problems.append(f"host captures present: {labels}; both start and end are needed")
    for capture in captures:
        if not isinstance(capture, dict):
            continue
        if host.kind == "gpu":
            apps = capture.get("gpu_compute_apps")
            if not isinstance(apps, dict) or apps.get("apps") is None:
                problems.append(f"{capture.get('label')}: compute processes could not be listed")
            elif apps["apps"]:
                problems.append(f"{capture.get('label')}: foreign GPU compute process(es): {len(apps['apps'])}")
        if host.kind == "cpu-metal":
            extra = [i for i in scheduled_items(capture) if i not in (host.allowed_scheduled or [])]
            if extra:
                problems.append(f"{capture.get('label')}: scheduled work not in the allow-list: {extra[:8]}")
            if any(v is None for v in (capture.get("scheduled") or {}).values()):
                problems.append(f"{capture.get('label')}: a scheduler listing failed")
    return {"outcome": "fail" if problems else "pass", "problems": problems}


# ---- evaluation ------------------------------------------------------------------------------------------------------------

SESSION_GATES = (("G6", gate_g6), ("G7", gate_g7), ("G8", gate_g8), ("G10", gate_g10), ("G11", gate_g11), ("G12", gate_g12))


def evaluate_run(root: Path, suite: str, host: HostGates) -> dict:
    """Gate outcomes of every session and cell of one run directory. GateConfigError when the host config is not ready."""
    host.require_ready()
    run, sessions = load_run(root, suite, host)
    reports, cells = [], []
    for session in sessions:
        gates = {name: function(session, run) for name, function in SESSION_GATES}
        reports.append({"suite": suite, "scale": session.scale, "engine": session.engine, "key": session.key, "gates": gates,
                        "flags": {FLAG[g]: gates[g]["outcome"] in ("pass", "not_applicable") for g in gates}})
        cells += gate_g9(session, run)
    return {"run_dir": str(root), "suite": suite, "sessions": reports, "cells": cells}


def summarize(host: HostGates, runs: list, captures: list) -> dict:
    """The round's gate table: counts per gate, the failures that fail the round, the eligibility flags and the QA table."""
    totals = {g: {"pass": 0, "fail": 0, "not_evaluable": 0, "not_applicable": 0} for g in GATES}
    failures, flags = [], {}
    for run in runs:
        for s in run["sessions"]:
            for gate, result in s["gates"].items():
                totals[gate][result["outcome"]] += 1
                if result["outcome"] in ("fail", "not_evaluable"):       # not evaluable is reported and never counted as a pass
                    detail = "; ".join(result.get("problems") or result.get("reasons") or [])[:300]
                    if gate in ROUND_FAILING and result["outcome"] == "fail":
                        failures.append(f"{gate} {s['key']}: {detail}")
                    flags.setdefault(s["key"], []).append(FLAG[gate])
        for c in run["cells"]:
            totals["G9"][c["outcome"]] += 1
            if c["outcome"] == "fail":
                flags.setdefault(f"{c['suite']}/{scale_dir_name(c['scale'])}/{c['engine']}/{c['query']}", []).append(FLAG["G9"])
    exclusivity = round_exclusivity(host, captures)
    totals["G12"][exclusivity["outcome"]] += 1
    if exclusivity["outcome"] == "fail":
        failures += [f"G12 round: {p}" for p in exclusivity["problems"]]
    return {"gates": {g: {**totals[g], "consequence": CONSEQUENCE[g]} for g in GATES}, "round_failures": failures,
            "round_failed": bool(failures), "ineligible": {k: sorted(set(v)) for k, v in sorted(flags.items())},
            "round_exclusivity": exclusivity}


def evaluate_round(runs: dict, host: HostGates, captures: list = ()) -> dict:
    """runs: {suite: run directory}. The full gate report of one round attempt (pure function of the files, no timestamps)."""
    evaluated = [evaluate_run(root, suite, host) for suite, root in runs.items()]
    return {"format": FORMAT, "host": host.host, "kind": host.kind, "t_min_s": host.t_min_s,
            "thresholds": {"window_target_s": WINDOW_TARGET_S, "window_max_runs": WINDOW_MAX_RUNS, "g7_min_coverage": G7_MIN_COVERAGE,
                           "g7_max_gap_s": G7_MAX_GAP_S, "g8_median_lag_s": G8_MEDIAN_LAG_S, "g8_drift_s": G8_DRIFT_S,
                           "g10_max_relative_difference": G10_MAX_REL_DIFF},
            "interpretations": INTERPRETATIONS, **summarize(host, evaluated, list(captures)), "runs": evaluated}


# ---- command line ----------------------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", help="host id in the config (a100, h100, gh200, b200, c-gcp, c-vm)")
    parser.add_argument("--config", type=Path, help="the explicit per-host config (paper3_hosts.json)")
    parser.add_argument("--run-dir", type=Path, action="append", metavar="DIR",
                        help="a run_experiment.py output root; repeat for the round's suites")
    parser.add_argument("--capture-exclusivity", action="store_true",
                        help="print a host-exclusivity capture of this host and the allow-list item names it contains, then exit")
    parser.add_argument("--gpu", action="store_true", help="with --capture-exclusivity: also list the GPU compute processes")
    parser.add_argument("--suite", action="append", metavar="NAME", help="suite name of each --run-dir, in order (default: the directory name)")
    parser.add_argument("--host-capture", type=Path, action="append", default=[], metavar="JSON",
                        help="host-exclusivity capture(s) of the round (start and end)")
    parser.add_argument("--output", type=Path, help="write the report here (default: print)")
    args = parser.parse_args(argv)
    if args.capture_exclusivity:
        capture = capture_host_exclusivity("start", gpu=args.gpu)
        print(json.dumps({"capture": capture, "allow_list_items": scheduled_items(capture)}, indent=2))
        return 0
    if not (args.host and args.config and args.run_dir):
        parser.error("--host, --config and at least one --run-dir are required (or --capture-exclusivity)")
    suites = args.suite or [d.name for d in args.run_dir]
    if len(suites) != len(args.run_dir):
        parser.error("--suite must be given once per --run-dir")
    try:
        host = load_host_config(args.config, args.host)
        captures = [read_json(p) for p in args.host_capture]
        report = evaluate_round(dict(zip(suites, args.run_dir)), host, captures)
    except GateConfigError as exc:
        print(f"paper3_gates.py: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")
    return 1 if report["round_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
