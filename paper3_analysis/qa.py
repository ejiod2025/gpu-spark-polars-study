"""QA tables (design 4: G7-G11 flag counts, G8 per host and engine as amendment A4 requires, G9 below-resolution cells, idle stability, read
latency and overhang) and the coverage matrix (5.4).

Two entry points:

  qa_from_gates(instances)   reads ONLY status.json, round-*/gates.json, round-*/paper3-gates.json and round-*/host-exclusivity-*.json, the
                             files tools/paper3_campaign/p3_qa_view.py allows while the campaign runs (design 4, 7.3). guarded_read() refuses
                             every other name, so this mode can be run mid-campaign: it holds gate outcomes, counts and onset lags and no
                             engine-versus-engine or host-versus-host energy, power or cost aggregate.
  coverage_matrix / latency_summary / idle_summary   from the cells of a finished campaign (the full analysis only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import statistics

from .tree import ROUND_DIR, SKIP_DIRS, AnalysisError, scale_sort

ALLOWED = re.compile(r"^(status\.json|round-\d+(-repeat-\d+)?/(gates|paper3-gates|host-exclusivity-(start|end))\.json)$")
GATES = ("G6", "G7", "G8", "G9", "G10", "G11", "G12")
OUTCOMES = ("pass", "fail", "not_evaluable", "not_applicable")


class ForbiddenRead(AnalysisError):
    """A file the QA mode must not open (design 7.3)."""


def guarded_read(root: Path, relative: str):
    if not ALLOWED.match(relative):
        raise ForbiddenRead(f"{relative}: not a QA file (design 7.3 allows gate outcomes, counts and coverage only)")
    try:
        return json.loads((Path(root) / relative).read_text())
    except (OSError, ValueError):
        return None


@dataclass
class QAAttempt:
    name: str
    round: int
    index: int
    verdict: object
    report: object


@dataclass
class QAInstance:
    host: str
    launch: str
    root: Path
    status: dict
    attempts: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.host}-l{self.launch}"

    def accepted(self) -> dict:
        """{round: attempt}: the last passing attempt of each round (run_campaign.py's own rule)."""
        out = {}
        for attempt in self.attempts:
            if attempt.verdict == "pass":
                out[attempt.round] = attempt
        return out


def qa_instance_at(root: Path, host=None, launch=None):
    """One campaign root read through guarded_read only; None for a status.json of another kind of campaign (when host is not given)."""
    root = Path(root)
    status = guarded_read(root, "status.json")
    block = status.get("paper3") if isinstance(status, dict) else None
    if not isinstance(block, dict):
        if host is None:
            return None
        block = {"host": host, "launch": launch}
    instance = QAInstance(str(block.get("host")), str(block.get("launch")), root, status if isinstance(status, dict) else {})
    for name in sorted(p.name for p in root.iterdir() if p.is_dir() and ROUND_DIR.match(p.name)):
        match = ROUND_DIR.match(name)
        gates = guarded_read(root, f"{name}/gates.json")
        report = guarded_read(root, f"{name}/paper3-gates.json")
        instance.attempts.append(QAAttempt(name, int(match.group(1)), int(match.group(2) or 0), (gates or {}).get("verdict"), report))
    instance.attempts.sort(key=lambda a: (a.round, a.index))
    return instance


def qa_instances(tree_root: Path, extra_roots=()) -> list:
    """The campaign roots under a tree, found by their status.json (an allowed file) and read through guarded_read only."""
    found = []

    def visit(directory: Path, depth: int):
        if (directory / "status.json").is_file():
            found.append(directory)
            return
        if depth >= 8:
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if entry.is_dir(follow_symlinks=False) and entry.name not in SKIP_DIRS:
                visit(Path(entry.path), depth + 1)

    visit(Path(tree_root), 0)
    instances = [i for i in (qa_instance_at(r) for r in found + [Path(r) for r in extra_roots]) if i is not None]
    if not instances:
        raise AnalysisError(f"no campaign root with a Paper 3 status.json under {tree_root}")
    return sorted(instances, key=lambda i: (i.host, i.launch, str(i.root)))


def quantiles(values) -> dict:
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0, "min": None, "median": None, "p90": None, "p99": None, "max": None}

    def q(p):
        position = (len(values) - 1) * p
        low, high = math.floor(position), math.ceil(position)
        return values[low] + (values[high] - values[low]) * (position - low)
    return {"n": len(values), "min": values[0], "median": q(0.5), "p90": q(0.9), "p99": q(0.99), "max": values[-1]}


def session_entries(report):
    for run in (report or {}).get("runs") or []:
        for session in run.get("sessions") or []:
            yield session


def qa_from_gates(instances: list) -> dict:
    """Gate-flag counts per host and engine over the accepted rounds; G8 per host and engine; G9 cells; G10 stability; attempts overview."""
    flags, g8, g9, g10, attempts, exclusivity = {}, {}, {}, {}, [], {}
    for instance in instances:
        accepted = instance.accepted()
        for attempt in instance.attempts:
            counts = {g: {o: 0 for o in OUTCOMES} for g in GATES}
            for session in session_entries(attempt.report):
                for gate, result in (session.get("gates") or {}).items():
                    counts[gate][result.get("outcome", "not_applicable")] += 1
            for run in (attempt.report or {}).get("runs") or []:
                for cell in run.get("cells") or []:
                    counts["G9"][cell.get("outcome", "not_applicable")] += 1
            attempts.append({"host": instance.host, "launch": instance.launch, "attempt": attempt.name, "round": attempt.round, "verdict": attempt.verdict,
                             "accepted": accepted.get(attempt.round) is attempt, "round_failed": (attempt.report or {}).get("round_failed"),
                             "round_failures": len((attempt.report or {}).get("round_failures") or []),
                             **{f"{g}_{o}": counts[g][o] for g in GATES for o in OUTCOMES}})
        for number, attempt in sorted(accepted.items()):
            report = attempt.report or {}
            ex = report.get("round_exclusivity") or {}
            exclusivity.setdefault(instance.host, {}).setdefault(ex.get("outcome", "missing"), 0)
            exclusivity[instance.host][ex.get("outcome", "missing")] += 1
            for session in session_entries(report):
                engine = session.get("engine")
                for gate, result in (session.get("gates") or {}).items():
                    node = flags.setdefault(instance.host, {}).setdefault(engine, {}).setdefault(gate, {o: 0 for o in OUTCOMES})
                    node[result.get("outcome", "not_applicable")] += 1
                result = (session.get("gates") or {}).get("G8") or {}
                node = g8.setdefault(instance.host, {}).setdefault(engine, {"sessions": 0, "pass": 0, "fail": 0, "not_evaluable": 0, "not_applicable": 0,
                                                                          "method_windows": 0, "method_pulses": 0, "median_lags_s": [], "drifts_s": []})
                node["sessions"] += 1
                node[result.get("outcome", "not_applicable")] += 1
                if result.get("method") in ("windows", "pulses"):
                    node[f"method_{result['method']}"] += 1
                if isinstance(result.get("median_lag_s"), (int, float)):
                    node["median_lags_s"].append(result["median_lag_s"])
                if isinstance(result.get("drift_s"), (int, float)):
                    node["drifts_s"].append(result["drift_s"])
                g10result = (session.get("gates") or {}).get("G10") or {}
                for scope, rel in (g10result.get("relative_difference") or {}).items():
                    g10.setdefault(instance.host, {}).setdefault(engine, {}).setdefault(scope, []).append(rel)
            for run in report.get("runs") or []:
                for cell in run.get("cells") or []:
                    key = (instance.host, cell.get("suite"), str(cell.get("scale")), cell.get("engine"))
                    node = g9.setdefault(key, {"cells": 0, "fail": 0, "window_rule_violation": 0, "window_below_10s": 0, "per_run_below_t_min": 0})
                    node["cells"] += 1
                    node["fail"] += cell.get("outcome") == "fail"
                    for reason in cell.get("reasons") or []:
                        if reason in node:
                            node[reason] += 1
    g8_out = {}
    for host, by_engine in g8.items():
        for engine, node in by_engine.items():
            evaluated = node["pass"] + node["fail"] + node["not_evaluable"]
            g8_out.setdefault(host, {})[engine] = {**{k: v for k, v in node.items() if k not in ("median_lags_s", "drifts_s")},
                                                   "flagged": node["fail"] + node["not_evaluable"],
                                                   "flagged_share": (node["fail"] + node["not_evaluable"]) / evaluated if evaluated else None,
                                                   "median_lag_s": quantiles(node["median_lags_s"]), "drift_s": quantiles(node["drifts_s"])}
    return {"flags": flags, "g8": g8_out, "g9": {"|".join(k): v for k, v in sorted(g9.items())},
            "g10_relative_difference": {h: {e: {s: quantiles(v) for s, v in sorted(by.items())} for e, by in sorted(by_e.items())}
                                        for h, by_e in sorted(g10.items())},
            "attempts": attempts, "round_exclusivity": exclusivity,
            "note": "G7-G11 are eligibility flags: a failed or not-evaluable G8 disables the session's gpu.csv values and nothing else (design 4, A4 item 3); "
                    "no flag stops, repeats or drops a round (A4 item 4)."}


# ---- from the cells of a finished campaign -----------------------------------------------------------------------------------------

def latency_summary(store) -> dict:
    """Counter read latency and overhang per run (2.1: summarised in the QA table, not a gate), per host and engine, in milliseconds."""
    out = {}
    for (host, engine), data in sorted(store.latencies.items()):
        out.setdefault(host, {})[engine] = {"read_latency_ms": quantiles(data["read_latency_ms"]), "overhang_ms": quantiles(data["overhang_ms"])}
    return out


def idle_summary(store) -> dict:
    """P_idle per session (W) by host, engine and scope: the QA view of the idle floors; a CPU-engine session's idle GPU and a GPU-engine session's
    are different quantities (2.2) and are never merged."""
    out = {}
    for (host, engine, series), sessions in sorted(store.idle.items()):
        values = [w for _, _, w in sessions]
        usable = [v for v in values if v is not None]
        mean = statistics.fmean(usable) if usable else None
        out.setdefault(host, {}).setdefault(engine, {})[series] = {**quantiles(usable), "mean": mean,
                                                                    "cv": (statistics.stdev(usable) / mean if len(usable) >= 2 and mean else None),
                                                                    "sessions_without_idle": len(values) - len(usable)}
    return out


def coverage_matrix(tree, store, selection) -> list:
    """One row per (host, suite, scale, query, engine): over the analysis's rounds, how many have a time, a total energy, an above-idle energy,
    and why the others do not (5.4: ineligible cells go here and are excluded from the affected contrast; nothing is imputed)."""
    rows = []
    for host, hd in sorted(tree.hosts.items()):
        rounds = selection.subject.get(host, {})
        instance = hd.reference_instance
        for suite in sorted(instance.suites):
            for scale in scale_sort(instance.suites[suite]):
                keys = {}
                for r, label in rounds.items():
                    for q, by_engine in store.at(host, label, r, suite, scale).items():
                        for engine, cell in by_engine.items():
                            keys.setdefault((q, engine), []).append(cell)
                for (q, engine), cells in sorted(keys.items()):
                    row = {"host": host, "suite": suite, "scale": scale, "query": q, "engine": engine, "basis": selection.basis.get(host),
                           "rounds": len(rounds), "rounds_with_cell": len(cells), "time_ok": sum(c.time_s is not None for c in cells)}
                    reasons = {}
                    for c in cells:
                        if c.time_s is None:
                            reasons[c.time_reason or "no_time"] = reasons.get(c.time_reason or "no_time", 0) + 1
                    series_set = sorted({s for c in cells for s in c.energy})
                    primary = next((s for s in ("S2-pinned", "S1-counter", "S1-module") if s in series_set), None)
                    row["energy_series"] = primary
                    row["energy_ok"] = sum(primary in c.energy and c.energy[primary].total_j is not None for c in cells) if primary else 0
                    row["above_idle_ok"] = sum(primary in c.energy and c.energy[primary].above_j is not None for c in cells) if primary else 0
                    for c in cells:
                        if c.time_s is not None and primary is None:
                            reasons["scope_absent"] = reasons.get("scope_absent", 0) + 1
                        entry = c.energy.get(primary) if primary else None
                        if entry is not None and entry.total_j is None:
                            reasons[f"energy:{entry.total_reason}"] = reasons.get(f"energy:{entry.total_reason}", 0) + 1
                        if entry is not None and entry.total_j is not None and entry.above_j is None:
                            reasons[f"above_idle:{entry.above_reason}"] = reasons.get(f"above_idle:{entry.above_reason}", 0) + 1
                    row["reasons"] = ";".join(f"{k}={v}" for k, v in sorted(reasons.items()))
                    row["coverage"] = next((c.coverage for c in cells if c.coverage), None)
                    row["failure_class"] = next((c.failure_class for c in cells if c.failure_class), None)
                    row["completed_before_session_failure"] = sum(c.completed_before_failure for c in cells)
                    rows.append(row)
    return rows


def crosscheck_summary(store, rounds_by_host: dict) -> dict:
    """The S1-sampled cross-check (2.3) per host: the median and spread of the relative difference (trapezoid of the 200 ms stream against the counter) over
    the eligible windows (>= 10 s, in sessions whose G7 and G8 flags are clean), and how many sessions and windows that is. A diagnostic only."""
    out = {}
    for host, data in sorted(store.crosscheck.items()):
        rounds = rounds_by_host.get(host, {})
        sessions = [(r, label, engine, diffs) for r, label, engine, diffs in data["sessions"] if rounds.get(r) == label]
        values = [d for _, _, _, diffs in sessions for d in diffs]
        summary = {"field": data["field"], "n_sessions": len(sessions), "n_windows": len(values)}
        q = quantiles(values)
        summary.update(median=q["median"], p10=None, p90=q["p90"], min=q["min"], max=q["max"],
                       median_abs=quantiles([abs(v) for v in values])["median"], spread_iqr=None)
        if values:
            ordered = sorted(values)

            def at(p):
                position = (len(ordered) - 1) * p
                low, high = math.floor(position), math.ceil(position)
                return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
            summary.update(p10=at(0.1), spread_iqr=at(0.75) - at(0.25))
        out[host] = summary
    return out
