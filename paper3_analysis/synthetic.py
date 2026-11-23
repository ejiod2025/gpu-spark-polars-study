"""Synthetic Paper 3 campaign TREES with injected, known values (part of the analysis code: the recovery run of design 5.4 and the tests use them): every launch of every host, laid out as the campaign tooling leaves them.

  <tree>/<host>/launch<N>/launch-record.json                         the operator's record (price, instance id, launch / terminate times)
  <tree>/<host>/launch<N>/rerun/paper3-<host>-l<N>/campaign.json     resolved design, cost block, provenance
                                                      status.json    the live status the campaign ended with (squares, accepted rounds)
                                                      round-<k>[-repeat-<n>]/gates.json, paper3-gates.json, <suite>/{schedule.json,
                                                      power.csv, power_sources.json, sf<scale>/<engine>/results.json}

The records are the harness's own shapes: windows are built with power_telemetry.aggregate_energy_window / gpu_energy_window /
tag_gpu_energy, the C-GCP power.csv has the sampler's columns. paper3-gates.json is written by `gate_report` in the shape of
paper3_gates.evaluate_round (tests check that shape against the real gates on a RunBuilder run).

Values are injected, not simulated: a Truth gives the median per-run time and energy of every cell, the builder writes three trial windows
whose median is exactly that (trial factors 1.05, 0.97, 1.00 on time and energy alike, so the median of the above-idle value is exact too),
counters and RAPL samples that integrate to it (the series have a sample at every stamp, so the linear interpolation is exact), and idle
windows of known power. A test then computes the expected estimate from the Truth with its own arithmetic and compares.
"""
from __future__ import annotations

import csv
from bisect import bisect_right
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import paper3_campaign as pc  # noqa: E402
import paper3_gates as pg  # noqa: E402
import power_telemetry as pt  # noqa: E402

COUNTER_SOURCE = "nvml-ctypes:nvmlDeviceGetTotalEnergyConsumption"
ENGINES = ("polars", "polars_gpu", "spark_cpu", "spark_gpu")
CPU_ENGINES = ("polars", "spark_cpu")
GPU_ENGINES = ("polars_gpu", "spark_gpu")
GRID = {"baseline": ("1", "10", "30", "100", "300"), "complex": ("30", "100", "300")}
QUERIES = {"baseline": ("q01", "q03"), "complex": ("cx_q09", "cx_q13")}
TRIAL_FACTORS = (1.05, 0.97, 1.00)                 # trials 1, 2, 3: the median is the unit factor
T_UTC0 = 1_795_000_000.0                           # 2026-11-24 or so
MONO0 = 9_000_000_000_000
WINDOW_TARGET = 10.0
PRICE = {"h100": 4.89, "a100": 1.99, "gh200": 2.59, "b200": 7.99, "c-gcp": 12.701568, "c-vm": 2.910776}
INSTANCE_TYPE = {"h100": "gpu_1x_h100_sxm5", "a100": "gpu_1x_a100_sxm4", "gh200": "gpu_1x_gh200", "b200": "gpu_1x_b200_sxm6",
                 "c-gcp": "c3-highmem-192-metal", "c-vm": "c3-highmem-44"}
HOST_KIND = {"h100": "gpu", "a100": "gpu", "gh200": "gpu", "b200": "gpu", "c-gcp": "cpu-metal", "c-vm": "cpu-vm"}
HOST_SCOPE = {"h100": "board", "a100": "board", "gh200": "module", "b200": "board", "c-gcp": "none", "c-vm": "none"}
S1_TAG = {"board": "S1-counter", "module": "S1-module"}
ZONE_ROWS = (("intel-rapl:0", "package-0", "S2-pinned/package"), ("intel-rapl:0:0", "package-0/dram", "S2-pinned/dram"),
             ("intel-rapl:1", "package-1", "S2-other"), ("intel-rapl:1:0", "package-1/dram", "S2-other"))
OTHER_SHARE = {"intel-rapl:1": 0.8, "intel-rapl:1:0": 0.2}
T_MIN = {host: entry["t_min_s"] for host, entry in json.loads((REPO / "paper3_hosts.json").read_text())["hosts"].items()}      # the pre-registered resolution limits


@dataclass
class Truth:
    """What the tree must contain, as functions of (host, round, suite, scale, query, engine); the tests' expected values come from these."""
    time: object                                   # -> median seconds per run
    energy: object = None                          # -> {series: median joules per run} or None; C-GCP series: S2-pinned/package, S2-pinned/dram, S2-other
    idle: object = None                            # (host, engine) -> {series: watts}: P_idle of the session (the two windows are 0.98 and 1.02 of it)
    failure: object = None                         # -> (error type, message) when the query fails in that session, else None


def utc(t: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="microseconds")


def stamp_utc(ns: int) -> str:
    return utc(T_UTC0 + (ns - MONO0) / 1e9)


def window_runs(per_run: float) -> int:
    """n of the window rule (2.1) for runs of equal length: stop when the summed time reaches 10 s or at 50 runs."""
    n, seconds = 1, [per_run]
    while math.fsum(seconds) < WINDOW_TARGET and n < 50:
        n += 1
        seconds.append(per_run)
    return n


class SuiteClock:
    """CLOCK_MONOTONIC in integer nanoseconds across the sessions of one suite run, and the RAPL segments laid on it."""

    def __init__(self, base: int):
        self.t = base
        self.segments = []                          # (start ns, end ns, {zone: watts})
        self.gpu_segments = []                      # (start ns, end ns, watts): what a GPU-board stream would show

    def advance(self, seconds: float) -> int:
        self.t += int(round(seconds * 1e9))
        return self.t


def gate_report(host: str, runs: dict, queries: dict, hook=None, round_: int = 1) -> dict:
    """paper3-gates.json in the shape of paper3_gates.evaluate_round. runs: {suite: [(scale, engine), ...]}; hook(kind, key, default) may replace a
    session gate result (kind 'session', key (suite, scale, engine, gate)) or a G9 cell (kind 'cell', key (suite, scale, engine, query))."""
    kind, energy = HOST_KIND[host], HOST_KIND[host] != "cpu-vm"
    hook = hook or (lambda k, key, default: default)
    out_runs, totals = [], {g: {"pass": 0, "fail": 0, "not_evaluable": 0, "not_applicable": 0} for g in pg.GATES}
    ineligible = {}
    for suite, sessions in runs.items():
        entries, cells = [], []
        for scale, engine in sessions:
            gates = {}
            for gate in ("G6", "G7", "G8", "G10", "G11", "G12"):
                result = {"outcome": "not_applicable", "reasons": ["host has none"]}
                if gate == "G6" and energy:
                    result = {"outcome": "pass", "problems": []}
                elif kind == "gpu" and gate == "G7":
                    result = {"outcome": "pass", "windows": 4, "flagged_windows": []}
                elif kind == "gpu" and gate == "G8":
                    method = "windows" if engine in GPU_ENGINES else "pulses"
                    result = {"outcome": "pass", "problems": [], "usable_onsets": 4, "median_lag_s": 0.25, "drift_s": 0.04, "method": method,
                              "windows_considered": 6, "windows_skipped": {"busy_before": 0}, "pulses": 2, "pulses_skipped": {"busy_before": 0}}
                elif gate == "G10" and energy:
                    tags = [S1_TAG[HOST_SCOPE[host]]] if kind == "gpu" else ["S2-all", "S2-pinned"]
                    result = {"outcome": "pass", "problems": [], "relative_difference": {t: 0.04 for t in tags}, "unreliable_scopes": []}
                elif kind == "gpu" and gate == "G11":
                    result = {"outcome": "pass", "problems": [], "throttle_samples_with_reasons": 0, "power_limit_text_logged": ["end", "start"]}
                elif kind == "gpu" and gate == "G12":
                    result = {"outcome": "pass", "problems": []}
                gates[gate] = hook("session", (suite, scale, engine, gate), result)
            key = f"{suite}/sf{scale}/{engine}"
            flags = {pg.FLAG[g]: gates[g]["outcome"] in ("pass", "not_applicable") for g in gates}
            entries.append({"suite": suite, "scale": scale, "engine": engine, "key": key, "gates": gates, "flags": flags})
            for g, r in gates.items():
                totals[g][r["outcome"]] += 1
                if r["outcome"] in ("fail", "not_evaluable"):
                    ineligible.setdefault(key, []).append(pg.FLAG[g])
            if energy:
                for query in queries[suite]:
                    default = {"suite": suite, "scale": scale, "engine": engine, "query": query, "outcome": "pass", "reasons": [], "trials": 3,
                               "median_trial": {"n": 5, "stop_reason": "target_reached"}}
                    cell = hook("cell", (suite, scale, engine, query), default)
                    cells.append(cell)
                    totals["G9"][cell["outcome"]] += 1
                    if cell["outcome"] == "fail":
                        ineligible.setdefault(f"{key}/{query}", []).append(pg.FLAG["G9"])
        out_runs.append({"run_dir": f"round-{round_}/{suite}", "suite": suite, "sessions": entries, "cells": cells})
    return {"format": 1, "host": host, "kind": kind, "t_min_s": T_MIN[host], "thresholds": {}, "interpretations": {},
            "gates": {g: {**totals[g], "consequence": pg.CONSEQUENCE[g]} for g in pg.GATES}, "round_failures": [], "round_failed": False,
            "ineligible": {k: sorted(set(v)) for k, v in sorted(ineligible.items())}, "round_exclusivity": {"outcome": "pass", "problems": []}, "runs": out_runs}


class TreeBuilder:
    """Writes a tree. add_launch() once per (host, launch)."""

    def __init__(self, root, truth: Truth, *, queries=None, grid=None, formal=False, gpu_stream=False, stream_scale=1.0):
        self.root = Path(root)
        self.truth = truth
        self.queries = queries or QUERIES
        self.grid = grid or GRID
        self.formal = formal
        self.gate_hooks = {}                     # (host, round) -> hook(kind, key, default); set before add_launch
        self.gpu_stream = gpu_stream             # write gpu.csv (200 ms, UTC) for GPU hosts: constant power in each run, scaled by stream_scale against the counter
        self.stream_scale = stream_scale

    # -- layout
    def launch_dir(self, host, launch, name=None) -> Path:
        return self.root / host / (name or f"launch{launch}")

    def design_schedule(self, host) -> list:
        """The host's full design as paper3_campaign.full_schedule gives it for the lock date 20261116 (seed, letters, rows, engine orders)."""
        return pc.full_schedule(pc.build_plan(host, "20261116"))

    def suites_of(self, host) -> dict:
        if host == "c-vm":
            return {"baseline": ["10", "30", "300"]}
        return {suite: list(scales) for suite, scales in self.grid.items()}

    def engines_of(self, host) -> tuple:
        return ENGINES if HOST_KIND[host] == "gpu" else CPU_ENGINES

    def add_launch(self, host, launch, rounds, *, attempts=None, created="2026-11-24T00:00:00+00:00", state="complete", launch_name=None, price=None,
                   instance_type=None, formal=None, status_override=None, write_record=True, poison=1000.0, common=None, record_extra=None, cost=True,
                   write_status=True):
        """One campaign root with the accepted rounds `rounds` (each one passing attempt) and, via `attempts`, extra attempts:
        [{"round": 2, "index": 0, "verdict": "fail"}, {"round": 2, "index": 1, "verdict": "pass"}] replaces the default for round 2. A failed
        attempt is written with every time and energy multiplied by `poison`, so any leak of it into an estimate is visible."""
        ldir = self.launch_dir(host, launch, launch_name)
        root = ldir / "rerun" / f"paper3-{host}-l{launch}"
        root.mkdir(parents=True, exist_ok=True)
        plan = attempts or [{"round": r, "index": 0, "verdict": "pass"} for r in rounds]
        schedule = self.design_schedule(host)
        squares = {}
        for entry in schedule:
            squares.setdefault(entry["square"], []).append(entry["round"])
        accepted = sorted({a["round"] for a in plan if a["verdict"] == "pass"})
        for a in plan:
            self.write_attempt(host, root, a["round"], a["index"], a["verdict"], poison if a["verdict"] != "pass" else 1.0)
        launch_rounds = sorted({a["round"] for a in plan})
        launch_squares = sorted({e["square"] for e in schedule if e["round"] in launch_rounds})
        itype = instance_type or INSTANCE_TYPE[host]
        billed = PRICE[host] if price is None else price
        common_hash = common or hashlib.sha256(f"{host}-design".encode()).hexdigest()
        paper3 = {"host": host, "kind": HOST_KIND[host], "instance_types": [itype], "energy_measured": HOST_KIND[host] != "cpu-vm", "launch": str(launch),
                  "launch_rounds": launch_rounds, "squares": launch_squares, "schedule": schedule, "t_min_s": T_MIN[host], "common_sha256": common_hash,
                  "telemetry": {"gpu_energy_scope": HOST_SCOPE[host]}}
        campaign = {"design": {"format": 1, "engines": list(self.engines_of(host)), "suites": {s: {"scales": sc} for s, sc in self.suites_of(host).items()},
                               "flags": {"runs": 3}, "formal": self.formal if formal is None else formal, "energy": {"gpu_energy_scope": HOST_SCOPE[host]},
                               "paper3": paper3},
                    "provenance": {"created_utc": created, "host": {"instance_type": itype, "cpu_model": "synthetic cpu", "gpu": "synthetic gpu"}},
                    "synthetic": True}
        if cost:
            campaign["cost"] = {"instance_type": itype, "provider": "lambda" if HOST_KIND[host] == "gpu" else "gcp", "hourly_price_usd": billed,
                                "instance_id": f"i-{host}-{launch}", "launch_utc": "2026-11-24T01:00:00+00:00", "credit_funded": "yes"}
        (root / "campaign.json").write_text(json.dumps(campaign, indent=1) + "\n")
        done = [r for r in accepted]
        status = {"format": 1, "state": state, "paper3": {
            "host": host, "launch": str(launch), "squares_complete": [sq for sq in launch_squares if all(r in done for r in squares[sq])],
            "confirmatory_rounds": sorted(r for sq in launch_squares if all(r in done for r in squares[sq]) for r in squares[sq]),
            "sensitivity_only_rounds": sorted(r for sq in launch_squares if not all(r in done for r in squares[sq]) for r in squares[sq] if r in done)}}
        if status_override is not None:
            status["paper3"].update(status_override)
        if write_status:
            (root / "status.json").write_text(json.dumps(status, indent=1) + "\n")
        if write_record:
            record = {"format": 1, "host": host, "launch": launch, "instance_type": itype, "instance_id": f"i-{host}-{launch}", "price_usd_h": billed,
                      "launch_utc": "2026-11-24T01:00:00+00:00", "terminate_confirmed_utc": "2026-11-25T01:00:00+00:00", "credit_funded": "yes",
                      **(record_extra or {})}
            (ldir / "launch-record.json").write_text(json.dumps(record, indent=1) + "\n")
        return root

    # -- one attempt
    def write_attempt(self, host, root, round_, index, verdict, scale_factor):
        name = f"round-{round_}" if index == 0 else f"round-{round_}-repeat-{index}"
        attempt = root / name
        attempt.mkdir(parents=True, exist_ok=True)
        runs, hook = {}, self.gate_hooks.get((host, round_))
        for suite, scales in self.suites_of(host).items():
            sessions = [(scale, engine) for scale in scales for engine in self.engines_of(host)]
            runs[suite] = sessions
            self.write_suite(host, attempt / suite, round_, suite, scales, scale_factor)
        report = gate_report(host, runs, self.queries, hook, round_)
        (attempt / "paper3-gates.json").write_text(json.dumps(report, indent=1) + "\n")
        (attempt / "gates.json").write_text(json.dumps({"unit": f"main-r{round_}", "round": round_, "attempt": index, "verdict": verdict}, indent=1) + "\n")

    def write_suite(self, host, directory, round_, suite, scales, factor):
        directory.mkdir(parents=True, exist_ok=True)
        clock = SuiteClock(MONO0 + round_ * 10**14)
        schedule = []
        for scale in scales:
            for engine in self.engines_of(host):
                item, results = self.session(host, round_, suite, scale, engine, clock, factor)
                schedule.append(item)
                target = directory / f"sf{scale}" / engine
                target.mkdir(parents=True, exist_ok=True)
                (target / "results.json").write_text(json.dumps(results) + "\n")
        (directory / "schedule.json").write_text(json.dumps(schedule, indent=1) + "\n")
        if HOST_KIND[host] == "cpu-metal":
            self.write_rapl(directory, clock)
        if HOST_KIND[host] == "gpu" and self.gpu_stream:
            self.write_gpu_stream_files(directory, clock)

    # -- one session
    def session(self, host, round_, suite, scale, engine, clock, factor):
        kind, scope = HOST_KIND[host], HOST_SCOPE[host]
        truth = self.truth
        gpu_counter = kind == "gpu"
        launch_ns = clock.advance(1.0)
        ready_ns = clock.advance(3.0)
        mj = [1_000_000_000]                                           # the session's cumulative counter, mJ
        results = {"engine": engine, "suite": suite, "queries": list(self.queries[suite]), "started_at_utc": stamp_utc(launch_ns),
                   "gpu_energy_counter": {"source": COUNTER_SOURCE if gpu_counter else "disabled", "scope": scope, "scope_tag": S1_TAG.get(scope, "none"),
                                          "unit": "mJ", "reasons": []},
                   "records": [], "query_errors": [], "idle_windows": [], "query_execution_status": "complete"}
        idle = truth.idle(host, engine) if truth.idle and kind != "cpu-vm" else {}
        idle_scale = {"before_warmups": 0.98, "after_last_trial": 1.02}
        if kind != "cpu-vm":
            results["idle_windows"].append(self.idle_window(clock, "before_warmups", idle, idle_scale["before_warmups"], gpu_counter, scope, mj))
        failed = set()
        for query in self.queries[suite]:                              # warm-up (one cold run), failures are decided here as in the worker
            failure = truth.failure(host, round_, suite, scale, query, engine) if truth.failure else None
            seconds = truth.time(host, round_, suite, scale, query, engine) * factor
            if failure:
                failed.add(query)
                results["query_errors"].append({"query": query, "phase": "warmup", "trial": 0, "type": failure[0], "message": failure[1],
                                                "failed_at_utc": stamp_utc(clock.t)})
                continue
            start = clock.t
            end = clock.advance(seconds * 1.4)
            results["records"].append({"query": query, "phase": "warmup", "trial": 0, "seconds": seconds * 1.4, "timed_start_monotonic_ns": start,
                                       "timed_end_monotonic_ns": end})
            clock.advance(1.5)
        for trial in (1, 2, 3):
            for query in self.queries[suite]:
                if query in failed:
                    continue
                base_t = truth.time(host, round_, suite, scale, query, engine) * factor
                base_e = {k: v * factor for k, v in (truth.energy(host, round_, suite, scale, query, engine) or {}).items()} if truth.energy else {}
                f = TRIAL_FACTORS[trial - 1]
                results["records"].append(self.window(clock, host, query, trial, base_t * f, {k: v * f for k, v in base_e.items()}, gpu_counter, scope, mj))
                clock.advance(1.5)
        if kind != "cpu-vm":
            results["idle_windows"].append(self.idle_window(clock, "after_last_trial", idle, idle_scale["after_last_trial"], gpu_counter, scope, mj))
        completed = clock.advance(0.2)
        results["completed_at_utc"] = stamp_utc(completed)
        exit_ns = clock.advance(1.0)
        clock.advance(8.0)
        item = {"scale": scale, "engine": engine, "start_utc": stamp_utc(launch_ns), "status": "complete", "returncode": 0,
                "launch": {"utc": stamp_utc(launch_ns), "monotonic_ns": launch_ns}, "process_exit": {"utc": stamp_utc(exit_ns), "monotonic_ns": exit_ns},
                "process_wall_seconds": (exit_ns - launch_ns) / 1e9}
        return item, results

    def counter_read(self, mj_now, at_ns):
        return {"mj": mj_now, "monotonic_ns": [at_ns - 5000, at_ns - 1000]}

    def window(self, clock, host, query, trial, seconds, energy, gpu_counter, scope, mj) -> dict:
        n = window_runs(seconds)
        runs = []
        for index in range(1, n + 1):
            start = clock.t
            end = clock.advance(seconds)
            run = {"run": index, "seconds": seconds, "timed_start_utc": stamp_utc(start), "timed_end_utc": stamp_utc(end),
                   "timed_start_monotonic_ns": start, "timed_end_monotonic_ns": end}
            if gpu_counter:
                before = {"mj": mj[0], "monotonic_ns": [start - 5000, start - 1000]}
                mj[0] += int(round(energy[S1_TAG[scope]] * 1000))
                after = {"mj": mj[0], "monotonic_ns": [end + 1000, end + 5000]}
                clock.gpu_segments.append((start, end, energy[S1_TAG[scope]] / ((end - start) / 1e9)))
                run["gpu_energy"] = pt.tag_gpu_energy(pt.gpu_energy_window(COUNTER_SOURCE, 0, before, after, start, end), scope)
            elif energy:
                zones = {zone: energy.get(series, 0.0) * OTHER_SHARE.get(zone, 1.0) for zone, _, series in ZONE_ROWS}
                span = (end - start) / 1e9
                clock.segments.append((start, end, {zone: joules / span for zone, joules in zones.items()}))
            runs.append(run)
            clock.advance(0.02)
        block = pt.aggregate_energy_window(runs, scope=scope if gpu_counter else "none", source=COUNTER_SOURCE if gpu_counter else "unavailable", device=0,
                                           target_seconds=WINDOW_TARGET, t_hat_seconds=seconds * 1.4)
        return {"query": query, "phase": "measured", "trial": trial, "seconds": block["per_run_seconds"], "timed_start_utc": block["span"]["start_utc"],
                "timed_end_utc": block["span"]["end_utc"], "timed_start_monotonic_ns": block["span"]["start_monotonic_ns"],
                "timed_end_monotonic_ns": block["span"]["end_monotonic_ns"], "energy_window": block}

    def idle_window(self, clock, label, idle, factor, gpu_counter, scope, mj) -> dict:
        settle_start = clock.t
        start = clock.advance(0.5)
        end = clock.advance(30.0)
        if gpu_counter:
            watts = idle[S1_TAG[scope]] * factor
            before = {"mj": mj[0], "monotonic_ns": [start - 1000, start + 1000]}
            mj[0] += int(round(watts * 30.0 * 1000))
            after = {"mj": mj[0], "monotonic_ns": [end - 1000, end + 1000]}
            clock.gpu_segments.append((start, end, watts))
            energy = pt.tag_gpu_energy(pt.gpu_energy_window(COUNTER_SOURCE, 0, before, after, start, end), scope)
        else:
            energy = {"source": "disabled", "scope_tag": "none"}
            if idle:
                clock.segments.append((start, end, {zone: idle.get(series, 0.0) * factor * OTHER_SHARE.get(zone, 1.0) for zone, _, series in ZONE_ROWS}))
        interval, mean_w = pt.idle_window_power_w(energy)
        clock.advance(0.3)
        return {"label": label, "requested_seconds": 30.0, "settle": {"requested_seconds": 0.5, "start_monotonic_ns": settle_start, "end_monotonic_ns": start},
                "start_utc": stamp_utc(start), "end_utc": stamp_utc(end), "start_monotonic_ns": start, "end_monotonic_ns": end, "actual_seconds": 30.0,
                "gpu_energy": energy, "energy_interval_seconds": interval, "mean_power_w": mean_w}

    # -- a 200 ms nvidia-smi stream: the counter's power in every run, a plateau elsewhere (samples sit exactly on every segment edge)
    def write_gpu_stream_files(self, directory, clock):
        segments = sorted(clock.gpu_segments)
        starts = [seg[0] for seg in segments]
        lo, hi = segments[0][0] - 2 * 10**9, segments[-1][1] + 2 * 10**9
        points = sorted({*(range(lo, hi, 200_000_000)), *starts, *(seg[1] for seg in segments)})

        def watts_at(t):
            i = bisect_right(starts, t) - 1
            return segments[i][2] * self.stream_scale if i >= 0 and t <= segments[i][1] else 100.0
        from datetime import datetime, timezone
        rows = ["timestamp, index, utilization.gpu [%], power.draw [W], power.draw.instant [W]"]
        for t in points:
            stamp = datetime.fromtimestamp(T_UTC0 + (t - MONO0) / 1e9, timezone.utc).strftime("%Y/%m/%d %H:%M:%S.%f")[:-3]
            w = watts_at(t)
            rows.append(f"{stamp}, 0, 0 %, {w:.3f} W, {w:.3f} W")
        (directory / "gpu.csv").write_text("\n".join(rows) + "\n")
        (directory / "environment.json").write_text(json.dumps({"gpu_telemetry": {"started": True, "interval_ms": 200, "monitor_tz_env": "UTC"}}))

    # -- the CPU-only host's sampler files
    def write_rapl(self, directory, clock):
        idle = self.truth.idle("c-gcp", "polars") if self.truth.idle else {}
        background = {zone: idle.get(series, 0.0) * OTHER_SHARE.get(zone, 1.0) for zone, _, series in ZONE_ROWS}
        points = sorted({clock.t + 10**9, MONO0} | {s for s, _, _ in clock.segments} | {e for _, e, _ in clock.segments})
        by_start = {s: (e, w) for s, e, w in clock.segments}
        totals = {zone: 5_000_000_000.0 for zone, _, _ in ZONE_ROWS}
        rows = []
        for i, point in enumerate(points):
            if i:
                previous = points[i - 1]
                end, watts = by_start.get(previous, (None, background))
                span = (point - previous) / 1e9
                for zone in totals:
                    totals[zone] += (watts if end == point else background)[zone] * span * 1e6
            for zone, domain, _ in ZONE_ROWS:
                rows.append({"timestamp_utc": stamp_utc(point), "monotonic_ns": point, "source": "powercap-rapl", "zone": zone, "domain": domain,
                             "energy_uj": repr(totals[zone]), "power_w": "", "raw_counter_uj": repr(totals[zone]), "flag": "", "scale": "", "engine": "", "phase": ""})
        with (directory / "power.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(pt.CSV_COLUMNS))
            writer.writeheader()
            writer.writerows(rows)
        zones = {"0": {"package": ["intel-rapl:0"], "dram": ["intel-rapl:0:0"]}, "1": {"package": ["intel-rapl:1"], "dram": ["intel-rapl:1:0"]}}
        sources = {"status": "available", "cpu_energy_source": "powercap-rapl", "node_power_source": None,
                   "sources": [{"source": "powercap-rapl", "status": "active", "kind": "energy_counter", "unit": "uJ", "reasons": [],
                                "domains": [{"zone": z, "domain": d, "socket": int(z[-1] if z.count(":") == 1 else z.split(":")[1]), "kind": "package" if d.count("/") == 0 else "dram",
                                             "readable": True, "advancing": True, "sampled": True} for z, d, _ in ZONE_ROWS]}],
                   "sockets": {"per_socket_counters": True, "zones": zones},
                   "pinning": {"sockets": [0], "attribution": {"possible": True, "pinned_sockets": [0], "pinned_zones": {"0": zones["0"]},
                                                               "other_zones": {"1": zones["1"]}}}}
        (directory / "power_sources.json").write_text(json.dumps(sources, indent=1) + "\n")


# ---- a log-linear world with closed-form ratios -----------------------------------------------------------------------------------

QUERY_FACTOR = {"q01": 1.0, "q03": 1.7, "cx_q09": 1.2, "cx_q13": 2.3}
HOST_TIME_FACTOR = {"h100": {"cpu": 1.0, "gpu": 1.0}, "a100": {"cpu": 1.3, "gpu": 1.6}, "gh200": {"cpu": 1.8, "gpu": 0.9}, "b200": {"cpu": 1.0, "gpu": 0.7},
                    "c-gcp": {"cpu": 1.1, "gpu": 1.0}, "c-vm": {"cpu": 1.15, "gpu": 1.0}}
TIME_BASE = {"polars": 2.0, "polars_gpu": 6.0, "spark_cpu": 4.0, "spark_gpu": 8.0}                 # seconds at SF1 for q01
TIME_EXPONENT = {"polars": 0.9, "polars_gpu": 0.5, "spark_cpu": 0.85, "spark_gpu": 0.45}
GPU_POWER_W = {"h100": 330.0, "a100": 240.0, "gh200": 420.0, "b200": 520.0}                        # board (module) power of a GPU engine run
IDLE_GPU_W = {"h100": 76.0, "a100": 70.0, "gh200": 162.0, "b200": 143.0}                           # a CPU engine's idle GPU
CONTEXT_W = {"h100": 142.0, "a100": 75.0, "gh200": 209.0, "b200": 192.0}                           # a GPU engine's session idle (context open)
CGCP_PACKAGE_W, CGCP_DRAM_W, CGCP_OTHER_W = 330.0, 40.0, 214.0
CGCP_IDLE = {"S2-pinned/package": 180.0, "S2-pinned/dram": 36.0, "S2-other": 214.0}


def engine_kind(engine):
    return "gpu" if engine in GPU_ENGINES else "cpu"


def round_factor(host, round_, engine):
    """A deterministic 1-3% wobble per host, round and engine (so the intervals are real, small and reproducible)."""
    phase = sum(map(ord, host)) * 0.37 + ENGINES.index(engine) * 0.9
    return math.exp(0.02 * math.sin(round_ * 1.7 + phase))


class World:
    """The default truth: time = base x scale^exp x query factor x host factor x round factor; energy = power x time (counter or RAPL)."""

    def __init__(self, time_overrides=None, energy_overrides=None, failures=None):
        self.time_overrides = time_overrides or {}          # key (host, round, suite, scale, query, engine) -> seconds
        self.energy_overrides = energy_overrides or {}      # same key -> {series: J}
        self.failures = failures or {}                      # same key -> (type, message)

    def law_time(self, host, suite, scale, query, engine):
        """The noise-free time: what every round's value scatters around."""
        return TIME_BASE[engine] * float(scale) ** TIME_EXPONENT[engine] * QUERY_FACTOR[query] * HOST_TIME_FACTOR[host][engine_kind(engine)]

    def power(self, host, engine):
        """{series: watts} of an engine's run on the host: the counter on a GPU host, package / DRAM / the idle socket on the CPU-only host."""
        if HOST_KIND[host] == "gpu":
            return {S1_TAG[HOST_SCOPE[host]]: GPU_POWER_W[host] if engine in GPU_ENGINES else IDLE_GPU_W[host]}
        if host == "c-gcp":
            return {"S2-pinned/package": CGCP_PACKAGE_W, "S2-pinned/dram": CGCP_DRAM_W, "S2-other": CGCP_OTHER_W}
        return None

    def time(self, host, round_, suite, scale, query, engine):
        key = (host, round_, suite, scale, query, engine)
        if key in self.time_overrides:
            return self.time_overrides[key]
        return self.law_time(host, suite, scale, query, engine) * round_factor(host, round_, engine)

    def energy(self, host, round_, suite, scale, query, engine):
        key = (host, round_, suite, scale, query, engine)
        if key in self.energy_overrides:
            return self.energy_overrides[key]
        watts = self.power(host, engine)
        if watts is None:
            return None
        seconds = self.time(host, round_, suite, scale, query, engine)
        return {series: w * seconds for series, w in watts.items()}

    def idle(self, host, engine):
        if HOST_KIND[host] == "gpu":
            return {S1_TAG[HOST_SCOPE[host]]: CONTEXT_W[host] if engine in GPU_ENGINES else IDLE_GPU_W[host]}
        return dict(CGCP_IDLE)

    def failure(self, host, round_, suite, scale, query, engine):
        key = (host, round_, suite, scale, query, engine)
        if key in self.failures:
            return self.failures[key]
        if engine == "polars_gpu" and query == "cx_q13":
            return ("NotImplementedError", "cudf-polars: this operation is not supported on the GPU")
        return None

    def truth(self) -> Truth:
        return Truth(time=self.time, energy=self.energy, idle=self.idle, failure=self.failure)


# ---- the standard four-host tree (also: python tests/paper3_tree_fakes.py --tree DIR, for a synthetic stats.json to draft the paper with) ----------

PRICE_BILLED = {"h100": 4.95, "a100": 2.10, "c-gcp": 12.9, "c-vm": 3.05}


def g9_h100_sf1_below_resolution(kind, key, default):
    """Baseline SF1 of the H100: its energy is below measurement resolution in every round (G9); time is not affected."""
    if kind == "cell" and key[0] == "baseline" and key[1] == "1":
        return {**default, "outcome": "fail", "reasons": ["window_below_10s"]}
    return default


def standard_tree(root, world=None) -> TreeBuilder:
    """H100 (two launches, a failed attempt of round 2 repeated), A100 (one launch: one complete square), C-GCP (two launches), C-VM (one launch);
    the H100's baseline SF1 energy is below resolution; polars_gpu cannot run cx_q13; billed prices differ from the list prices."""
    builder = TreeBuilder(root, (world or World()).truth())
    for r in range(1, 9):
        builder.gate_hooks[("h100", r)] = g9_h100_sf1_below_resolution
    builder.add_launch("h100", 1, [1, 2, 3, 4], price=PRICE_BILLED["h100"], attempts=[
        {"round": 1, "index": 0, "verdict": "pass"}, {"round": 2, "index": 0, "verdict": "fail"}, {"round": 2, "index": 1, "verdict": "pass"},
        {"round": 3, "index": 0, "verdict": "pass"}, {"round": 4, "index": 0, "verdict": "pass"}])
    builder.add_launch("h100", 2, [5, 6, 7, 8], price=PRICE_BILLED["h100"])
    builder.add_launch("a100", 1, [1, 2, 3, 4], price=PRICE_BILLED["a100"])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4], price=PRICE_BILLED["c-gcp"])
    builder.add_launch("c-gcp", 2, [5, 6, 7, 8], price=PRICE_BILLED["c-gcp"])
    builder.add_launch("c-vm", 1, [1, 2], price=PRICE_BILLED["c-vm"])
    return builder


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Write the standard synthetic four-host campaign tree (injected values, NOT results) for trying analyze_paper3.py.")
    parser.add_argument("--tree", type=Path, required=True, help="directory to create (must not exist)")
    arguments = parser.parse_args()
    if arguments.tree.exists():
        parser.error(f"{arguments.tree} exists")
    standard_tree(arguments.tree)
    print(f"wrote {arguments.tree}; analyse with: python analyze_paper3.py --tree {arguments.tree} --output OUT --unblind --allow-synthetic")


if __name__ == "__main__":
    main()
