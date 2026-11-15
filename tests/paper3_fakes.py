"""Synthetic run directories for the Paper 3 gates: what run_experiment.py and worker.py leave behind, without a GPU.

A RunBuilder lays one run_experiment.py output root out on a virtual clock (a piecewise-constant power model drives the NVML
counter, the 200 ms gpu.csv stream and the RAPL series), using power_telemetry's own aggregation helpers for the counter
windows so that the record shapes are the harness's. Tests then change one thing and watch which gate reacts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import paper3_gates as pg  # noqa: E402
import power_telemetry as pt  # noqa: E402

T0 = datetime(2026, 11, 17, 6, 0, 0, tzinfo=timezone.utc).timestamp()
MONO0 = 7_000_000_000_000
COUNTER_SOURCE = "nvml-ctypes:nvmlDeviceGetTotalEnergyConsumption"
GPU_ENGINES = ("polars_gpu", "spark_gpu")
QUERIES = ("q01", "q03")
POWER_TEXT = """==============NVSMI LOG==============

Attached GPUs                             : 1
GPU <redacted>
    GPU Power Readings
        Average Power Draw                : 71.10 W
        Current Power Limit               : 700.00 W
        Requested Power Limit             : 700.00 W
        Default Power Limit               : 700.00 W
"""
PERFORMANCE_TEXT = """==============NVSMI LOG==============

GPU <redacted>
    Performance State                     : P0
    Clocks Event Reasons
        Idle                              : Not Active
        HW Slowdown                       : Not Active
"""
CHRONY = "Reference ID    : C6B28AC3 (time)\nStratum         : 3\nSystem time     : 0.000114318 seconds slow of NTP time\nLast offset     : +0.000040054 seconds\nLeap status     : Normal\n"

HOST_ENTRIES = {
    "h100": {"kind": "gpu", "energy_measured": True, "idle_windows": True, "t_min_s": 1.0,
             "gpu_counter": {"source": COUNTER_SOURCE, "scope": "board"}, "cpu_energy": {"source": "none", "series": None},
             "exclusive_use": {"allowed_scheduled": None}},
    "a100": {"kind": "gpu", "energy_measured": True, "idle_windows": True, "t_min_s": 2.0,
             "gpu_counter": {"source": COUNTER_SOURCE, "scope": "board"}, "cpu_energy": {"source": "none", "series": None},
             "exclusive_use": {"allowed_scheduled": None}},
    "gh200": {"kind": "gpu", "energy_measured": True, "idle_windows": True, "t_min_s": 1.5,
              "gpu_counter": {"source": COUNTER_SOURCE, "scope": "module"}, "cpu_energy": {"source": "none", "series": None},
              "exclusive_use": {"allowed_scheduled": None}},
    "c-gcp": {"kind": "cpu-metal", "energy_measured": True, "idle_windows": True, "t_min_s": 1.0,
              "gpu_counter": {"source": "none", "scope": "none"}, "cpu_energy": {"source": "powercap-rapl", "series": 4},
              "exclusive_use": {"allowed_scheduled": ["timer:fstrim.timer"]}},
    "c-vm": {"kind": "cpu-vm", "energy_measured": False, "idle_windows": False, "t_min_s": "not_applicable",
             "gpu_counter": {"source": "none", "scope": "none"}, "cpu_energy": {"source": "none", "series": None},
             "exclusive_use": {"allowed_scheduled": None}},
}
ZONES = (("intel-rapl:0", "package-0", 0, "package"), ("intel-rapl:0:0", "package-0/dram", 0, "dram"),
         ("intel-rapl:1", "package-1", 1, "package"), ("intel-rapl:1:0", "package-1/dram", 1, "dram"))


def host_gates(host="h100", **changes):
    entry = json.loads(json.dumps(HOST_ENTRIES[host]))
    for key, value in changes.items():
        if key in ("source", "scope"):
            entry["gpu_counter"][key] = value
        elif key in ("cpu_source", "cpu_series"):
            entry["cpu_energy"][key.split("_")[1]] = value
        elif key == "allowed_scheduled":
            entry["exclusive_use"]["allowed_scheduled"] = value
        else:
            entry[key] = value
    return pg.parse_host_entry(host, entry)


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="microseconds")


def mono(t):
    return int(MONO0 + (t - T0) * 1e9)


class RunBuilder:
    """One suite of one round: `engines` in order, at `scales`, on a virtual clock. build() writes the directory."""

    def __init__(self, root, host="h100", suite="baseline", engines=("polars", "polars_gpu"), scales=("1",), per_run=None,
                 skew=0.15, grid_phase=0.04, window_gap=1.5, idle_gap=1.0, queries=QUERIES, extra_pulses=True, window_target=10.0,
                 post_idle_scale=1.0):
        self.root, self.host, self.suite = Path(root), host, suite
        self.engines, self.scales, self.queries = list(engines), list(scales), list(queries)
        self.per_run = per_run or {}                    # (engine, query) -> seconds per run; default by engine
        self.skew, self.grid_phase, self.window_gap, self.idle_gap = skew, grid_phase, window_gap, idle_gap
        self.post_idle_scale = post_idle_scale          # the second idle window draws this much more (or less) than the first
        self.window_target, self.extra_pulses = window_target, extra_pulses
        self.entry = HOST_ENTRIES[host]
        self.gpu = self.entry["kind"] == "gpu"
        self.rapl = self.entry["cpu_energy"]["source"] == "powercap-rapl"
        self.segments = []                              # (start, end, watts); between segments: idle power
        self.busy = []                                  # (start, end): GPU utilization 100%
        self.rapl_busy = []                             # (start, end): CPU engine running on the CPU-only host
        self.rapl_extra = []                            # (start, end, watts) added to the pinned socket's package
        self.idle_watts = {"cpu_engine": 75.0, "gpu_engine": 140.0, "cpu_package": 180.0, "cpu_dram": 36.0}
        self.clock = T0 + 5.0
        self.sessions = []
        self.schedule = []
        self.context_open_ranges = []                   # (start, end) a GPU engine holds a CUDA context

    # -- model
    def counter_mj(self, t):
        """Cumulative GPU counter (mJ) at virtual time t: 75 W with no context, 140 W with one, 300 W under load."""
        total, cursor = 1_000_000.0, T0 - 1000.0
        edges = [x for seg in self.segments for x in seg[:2]] + [x for rng in self.context_open_ranges for x in rng]
        points = sorted({cursor, t} | {x for x in edges if cursor < x < t})
        for a, b in zip(points, points[1:]):
            total += self.watts_at((a + b) / 2) * (b - a) * 1000.0
        return int(total)

    def watts_at(self, t):
        for start, end, watts in self.segments:
            if start <= t < end:
                return watts
        return self.idle_watts["gpu_engine"] if any(a <= t < b for a, b in self.context_open_ranges) else 75.0

    def rapl_uj(self, zone_index, t):
        kind = ZONES[zone_index][3]
        watts = self.idle_watts["cpu_package" if kind == "package" else "cpu_dram"]
        loaded = sum(max(0.0, min(b, t) - a) for a, b in self.rapl_busy if a < t) if zone_index < 2 else 0.0
        extra = sum(w * max(0.0, min(b, t) - a) for a, b, w in self.rapl_extra if a < t) if zone_index == 0 else 0.0
        return int(5_000_000_000 + (watts * (t - T0 + 1000.0) + 120.0 * loaded + extra) * 1e6)

    def util_at(self, t):
        return 100 if any(a <= t < b for a, b in self.busy) else 0

    # -- reads
    def read(self, t):
        return {"mj": self.counter_mj(t), "monotonic_ns": [mono(t) - 8_000, mono(t) + 8_000]}

    def counter_window(self, a, b, start_t, end_t):
        window = pt.gpu_energy_window(COUNTER_SOURCE, 0, self.read(a), self.read(b), mono(start_t), mono(end_t))
        return pt.tag_gpu_energy(window, self.entry["gpu_counter"]["scope"])

    # -- a session on the timeline
    def per_run_seconds(self, engine, query):
        return self.per_run.get((engine, query), 1.7 if engine not in GPU_ENGINES else 1.2)

    def lay_out(self, scale, engine):
        gpu_engine = engine in GPU_ENGINES
        pulses = self.gpu and self.extra_pulses        # the runner pulses every session on a GPU host before the launch and after the exit
        pulse_start = self.clock + 0.5 if pulses else None
        launch = pulse_start + 4.0 if pulses else self.clock
        ready = launch + 3.0
        if gpu_engine:
            self.context_open_ranges.append((ready - 0.5, ready + 2000.0))      # trimmed below when the session ends
        item = {"scale": scale, "engine": engine, "start_utc": utc(launch - 0.5), "launch": {"utc": utc(launch), "monotonic_ns": mono(launch)}}
        results = {"engine": engine, "started_at_utc": utc(launch + 0.5), "gpu_energy_counter": {
            "source": COUNTER_SOURCE if self.gpu else "disabled", "scope": self.entry["gpu_counter"]["scope"] if self.gpu else "none",
            "scope_tag": pg.SCOPE_TAG[self.entry["gpu_counter"]["scope"]] if self.gpu else "none", "unit": "mJ", "reasons": []},
            "records": [], "idle_windows": []}
        counters = {"engine_ready": {"utc": utc(ready), "monotonic_ns": mono(ready), "gpu_energy_read": self.session_read(ready)}}
        t = ready + 0.2
        if self.entry["idle_windows"]:
            t = self.idle_window(results, "before_warmups", t)
        for query in self.queries:                                              # warmups: one cold run each
            seconds = self.per_run_seconds(engine, query) * 1.4
            start, end = t, t + seconds
            self.mark_busy(engine, start, end)
            results["records"].append({"query": query, "phase": "warmup", "trial": 0, "seconds": seconds, "timed_start_utc": utc(start),
                                       "timed_end_utc": utc(end), "timed_start_monotonic_ns": mono(start), "timed_end_monotonic_ns": mono(end),
                                       "gpu_energy": self.counter_window(start - 0.001, end + 0.001, start, end) if self.gpu else {"source": "disabled", "scope_tag": "none"}})
            t = end + self.window_gap
        for trial in (1, 2, 3):
            for query in self.queries:
                t = self.window(results, engine, query, trial, t)
                t += self.window_gap
        if self.entry["idle_windows"]:
            t = self.idle_window(results, "after_last_trial", t)
        completed = t + 0.2
        counters["completed"] = {"utc": utc(completed), "monotonic_ns": mono(completed), "gpu_energy_read": self.session_read(completed)}
        results["session_counters"] = counters
        results["completed_at_utc"] = utc(completed + 0.01)
        exit_t = completed + 0.8
        if gpu_engine:
            self.context_open_ranges[-1] = (self.context_open_ranges[-1][0], exit_t)
        item["process_exit"] = {"utc": utc(exit_t), "monotonic_ns": mono(exit_t)}
        item.update(process_wall_seconds=exit_t - launch, returncode=0, status="complete")
        self.clock = exit_t + 1.0
        self.decorate(item, launch, exit_t, engine, pulse_start)
        if pulses:
            self.clock = exit_t + 5.0
        return item, results

    def session_read(self, t):
        if not self.gpu:
            return {"source": "disabled", "scope_tag": "none"}
        return {**self.read(t), "source": COUNTER_SOURCE, "scope_tag": pg.SCOPE_TAG[self.entry["gpu_counter"]["scope"]]}

    def mark_busy(self, engine, start, end):
        if engine in GPU_ENGINES:
            self.busy.append((start, end))
            self.segments.append((start, end, 300.0))
        elif self.rapl:
            self.rapl_busy.append((start, end))

    def idle_window(self, results, label, t):
        settle_start, start = t, t + 0.5
        end = start + 30.0
        if label == "after_last_trial" and self.post_idle_scale != 1.0:
            if self.gpu:
                self.segments.append((start - 0.5, end + 0.5, self.watts_at(start) * self.post_idle_scale))
            if self.rapl:
                self.rapl_extra.append((start - 0.5, end + 0.5, self.idle_watts["cpu_package"] * (self.post_idle_scale - 1.0)))
        energy = self.counter_window(start - 0.001, end + 0.001, start, end) if self.gpu else {"source": "disabled", "scope_tag": "none"}
        interval, watts = pt.idle_window_power_w(energy)
        results["idle_windows"].append({
            "label": label, "requested_seconds": 30.0,
            "settle": {"requested_seconds": 0.5, "start_utc": utc(settle_start), "end_utc": utc(start), "start_monotonic_ns": mono(settle_start),
                       "end_monotonic_ns": mono(start)},
            "start_utc": utc(start), "end_utc": utc(end), "start_monotonic_ns": mono(start), "end_monotonic_ns": mono(end),
            "actual_seconds": 30.0, "gpu_energy": energy, "energy_interval_seconds": interval, "mean_power_w": watts})
        return end + 0.3

    def window(self, results, engine, query, trial, t):
        per_run = self.per_run_seconds(engine, query)
        runs, seconds, total, first_start = [], [], 0.0, t
        while True:
            start, end = t, t + per_run
            self.mark_busy(engine, start, end)
            energy = self.counter_window(start - 0.001, end + 0.001, start, end) if self.gpu else {"source": "disabled", "scope_tag": "none"}
            runs.append({"run": len(runs) + 1, "seconds": per_run, "timed_start_utc": utc(start), "timed_end_utc": utc(end),
                         "timed_start_monotonic_ns": mono(start), "timed_end_monotonic_ns": mono(end), "gpu_energy": energy})
            seconds.append(per_run)
            total += per_run
            t = end + 0.02
            if pt.energy_window_complete(seconds, self.window_target):
                break
        block = pt.aggregate_energy_window(runs, scope=self.entry["gpu_counter"]["scope"] if self.gpu else "none",
                                           source=COUNTER_SOURCE if self.gpu else "unavailable", device=0,
                                           target_seconds=self.window_target, t_hat_seconds=per_run * 1.4)
        results["records"].append({"query": query, "phase": "measured", "trial": trial, "seconds": block["per_run_seconds"],
                                   "timed_start_utc": block["span"]["start_utc"], "timed_end_utc": block["span"]["end_utc"],
                                   "timed_start_monotonic_ns": block["span"]["start_monotonic_ns"],
                                   "timed_end_monotonic_ns": block["span"]["end_monotonic_ns"], "energy_window": block})
        return t

    def decorate(self, item, launch, exit_t, engine, pulse_start):
        item["clock_sync"] = {"start": self.chrony(launch), "end": self.chrony(exit_t)}
        if self.gpu:
            item["gpu_idle_gate"] = {"idle": True, "waited_seconds": 0.0, "polls": 1, "timeout_seconds": 60, "compute_apps": [], "error": None,
                                     "checked_at_utc": utc(launch - 0.2)}
            item["gpu_compute_apps_end"] = {"compute_apps": [], "error": None, "checked_at_utc": utc(exit_t + 0.1)}
            for key, text, name in ((pg.POWER_STATE_ITEM_KEY, POWER_TEXT, "power"), (pg.PERFORMANCE_STATE_ITEM_KEY, PERFORMANCE_TEXT, "performance")):
                item[key] = {label: self.state_file(item, engine, name, label, text) for label in ("start", "end")}
            if pulse_start is not None:
                item[pg.G8_PULSES_KEY] = {}
                for label, at in (("start", pulse_start), ("end", exit_t + 0.5)):
                    self.busy.append((at + 0.02, at + 3.02))          # the kernel starts a few ms after the launch stamp
                    self.segments.append((at + 0.02, at + 3.02, 250.0))
                    item[pg.G8_PULSES_KEY][label] = {"ok": True, "label": label, "device": 0, "requested_seconds": 3.0, "launch_utc": utc(at),
                                                     "launch_monotonic_ns": mono(at), "done_utc": utc(at + 3.0), "done_monotonic_ns": mono(at + 3.0)}

    def state_file(self, item, engine, name, label, text):
        path = self.root / f"sf{item['scale']}" / f"{engine}-gpu-{name}-{label}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return {"file": path.name, "captured_utc": utc(self.clock), "returncode": 0, "bytes": len(text.encode()),
                "sha256": hashlib.sha256(text.encode()).hexdigest(), "command": ["nvidia-smi", "-q", "-d", name.upper()]}

    def chrony(self, t):
        return {"command": ["chronyc", "tracking"], "returncode": 0, "captured_utc": utc(t), "monotonic_ns": mono(t), "output": CHRONY,
                **pt.parse_chrony_tracking(CHRONY)}

    # -- files
    def build(self):
        self.root.mkdir(parents=True, exist_ok=True)
        for scale in self.scales:
            for engine in self.engines:
                item, results = self.lay_out(scale, engine)
                directory = self.root / f"sf{scale}" / engine
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "results.json").write_text(json.dumps(results, indent=1) + "\n")
                self.schedule.append(item)
                self.sessions.append((scale, engine))
        (self.root / "schedule.json").write_text(json.dumps(self.schedule, indent=1) + "\n")
        self.write_environment()
        if self.gpu:
            self.write_gpu_csv()
        if self.rapl:
            self.write_rapl()
        return self

    def write_environment(self):
        environment = {"arguments": {}, "gpu_telemetry": {"started": True, "interval_ms": 200, "monitor_tz_env": "UTC"} if self.gpu else {"started": False}}
        (self.root / "environment.json").write_text(json.dumps(environment, indent=1) + "\n")

    def write_gpu_csv(self, rows=None, drop=()):
        """gpu.csv as nvidia-smi writes it (names with units), every 200 ms on the builder's grid. rows(t) False drops a sample;
        drop names columns to leave out."""
        columns = [("timestamp", "timestamp"), ("index", "index"), ("utilization.gpu", "utilization.gpu [%]"),
                   ("utilization.memory", "utilization.memory [%]"), ("memory.used", "memory.used [MiB]"), ("power.draw", "power.draw [W]"),
                   ("temperature.gpu", "temperature.gpu"), ("clocks.sm", "clocks.current.sm [MHz]"),
                   ("clocks.mem", "clocks.current.memory [MHz]"), ("power.draw.average", "power.draw.average [W]"),
                   ("power.draw.instant", "power.draw.instant [W]"), ("clocks_event_reasons.active", "clocks_event_reasons.active")]
        kept = [c for c in columns if c[0] not in drop]
        lines = [", ".join(header for _, header in kept)]
        t = T0 + self.grid_phase
        end = self.clock + 2.0
        while t < end:
            if rows is None or rows(t):
                skew = self.skew(t) if callable(self.skew) else self.skew
                watts = self.watts_at(t)
                util = self.util_at(t)
                values = {"timestamp": datetime.fromtimestamp(t + skew, timezone.utc).strftime("%Y/%m/%d %H:%M:%S.%f")[:-3], "index": "0",
                          "utilization.gpu": f"{util} %", "utilization.memory": "0 %", "memory.used": "527 MiB", "power.draw": f"{watts:.2f} W",
                          "temperature.gpu": "61", "clocks.sm": "1980 MHz", "clocks.mem": "2619 MHz", "power.draw.average": f"{watts:.2f} W",
                          "power.draw.instant": f"{watts:.2f} W",
                          "clocks_event_reasons.active": "0x0000000000000001" if util == 0 else "0x0000000000000000"}
                lines.append(", ".join(values[name] for name, _ in kept))
            t += 0.2
        (self.root / "gpu.csv").write_text("\n".join(lines) + "\n")

    def write_rapl(self):
        rows, end = [], self.clock + 2.0
        header = ",".join(pt.CSV_COLUMNS)
        t = T0 - 1.0
        while t < end:
            for index, (zone, domain, _, _) in enumerate(ZONES):
                rows.append(f"{utc(t)},{mono(t)},powercap-rapl,{zone},{domain},{self.rapl_uj(index, t)},,{self.rapl_uj(index, t)},,,,")
            t += 0.2
        (self.root / "power.csv").write_text(header + "\n" + "\n".join(rows) + "\n")
        sources = {"status": "available", "cpu_energy_source": "powercap-rapl", "node_power_source": None,
                   "sources": [{"source": "powercap-rapl", "status": "active", "kind": "energy_counter", "unit": "uJ", "reasons": [],
                                "domains": [{"zone": z, "domain": d, "socket": s, "kind": k, "readable": True, "advancing": True, "sampled": True}
                                            for z, d, s, k in ZONES]}],
                   "sockets": {"per_socket_counters": True, "zones": {"0": {"package": ["intel-rapl:0"], "dram": ["intel-rapl:0:0"]},
                                                                      "1": {"package": ["intel-rapl:1"], "dram": ["intel-rapl:1:0"]}}},
                   "liveness": {f"powercap-rapl/{z}": {"kind": "energy", "verdict": "advancing", "samples": 1000, "changes": 999, "longest_flat_seconds": 0.2}
                                for z, _, _, _ in ZONES},
                   "pinning": {"sockets": [0], "attribution": {"possible": True, "pinned_sockets": [0],
                                                               "pinned_zones": {"0": {"package": ["intel-rapl:0"], "dram": ["intel-rapl:0:0"]}},
                                                               "other_zones": {"1": {"package": ["intel-rapl:1"], "dram": ["intel-rapl:1:0"]}}}}}
        (self.root / "power_sources.json").write_text(json.dumps(sources, indent=1) + "\n")


# ---- edit helpers -------------------------------------------------------------------------------------------------------

def edit_json(path, change):
    path = Path(path)
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=1) + "\n")


def edit_results(root, scale, engine, change):
    edit_json(Path(root) / f"sf{scale}" / engine / "results.json", change)


def edit_schedule_item(root, scale, engine, change):
    def apply(schedule):
        for item in schedule:
            if item["scale"] == scale and item["engine"] == engine:
                change(item)
    edit_json(Path(root) / "schedule.json", apply)
