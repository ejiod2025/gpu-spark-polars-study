#!/usr/bin/env python3
"""Checklist of the Paper 3 H100 smoke test: what must hold in the collected output, checked from the files alone.

  check_smoke.py publish BUNDLE_DIR EVIDENCE_DIR [--mask TEXT ...]    redact a fetched bundle into the evidence directory
  check_smoke.py check   EVIDENCE_DIR [--price-per-hour 4.89] [--expect-enforced-w 700] [--json]
  check_smoke.py sums    EVIDENCE_DIR                                 SHA256SUMS.txt of every file (but itself)

EVIDENCE_DIR is docs/paper3-evidence/smoke/h100-<stamp>/ as run_h100_smoke.sh leaves it: run/ (the run_experiment.py output root),
gates.json (paper3_gates.py), host-exclusivity-{start,end}.json, data-manifest.json, stages/*-timing.json, driver-timeline.jsonl.
`check` writes checklist.json and checklist.txt there and exits 1 when any item FAILs. Statuses: PASS, WARN (look at it; the harness
may still be ready), INFO (a measurement, no expectation) and FAIL (a property the campaign needs does not hold).

The items (what the lead asked the smoke test to settle):
  C01 provenance      synthetic workload, the campaign's flags (scope board, window 10 s, idle 30/60, pulses), an H100 host
  C02 sessions        four engines, each complete, exit 0, no query error
  C03 fields          the nvidia-smi fields listed and queried (power.draw.average/instant, throttle reasons), the final field list
  C04 gpu.csv         real header names, [N/A] and non-numeric cells per column, the throttle column, row health
  C05 power limits    `-q -d POWER` captures and the --query-gpu limits: enforced.power.limit 700 W
  C06 performance     `-q -d PERFORMANCE` captures (clock event reasons) at every session's start and end
  C07 pulses          gpu_pulse.py ok:true, stamps plausible against the runner's and the worker's, onset lag in gpu.csv
  C08 idle windows    both windows per session with S1-counter energy; which engine held a CUDA context in the first one (memory, clocks)
  C09 windows         adaptive windows: stop reasons, n, gaps; both regimes (the 50-run cap and a target-reached window) seen
  C10 scope tags      every energy value tagged S1-counter
  C11 chrony          offset logged at every session's start and end
  C12 gates           paper3_gates.py report: no FAIL (G9 below-resolution cells of the synthetic light query are expected, reported)
  C13 poller          gpu.csv cadence and coverage of sessions and windows, the final field list
  C14 outputs         every engine returns the polars rows (the G2 tolerance)
  C15 exclusivity     no foreign GPU process at the host captures, the idle gate and the session ends
  C16 CPU power       what the power sampler found on this host (information)
  C17 redaction       no UUID, serial number, bus id or IP address in the evidence
  C18 time and cost   duration of every stage (setup_h100.sh included), total wall time, billed estimate
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import paper3_gates as pg  # noqa: E402
import power_telemetry as pt  # noqa: E402
import smoke_workloads  # noqa: E402

ENGINES = ("polars", "polars_gpu", "spark_cpu", "spark_gpu")
GPU_ENGINES = ("polars_gpu", "spark_gpu")
SCALE = "1"
TAG = "S1-counter"
EXPECT = {"gpu_energy_scope": "board", "energy_window_s": 10.0, "session_idle_seconds": 30.0, "session_idle_settle_seconds": 60.0,
          "g8_pulse_seconds": 3.0, "g8_pulse_engines": "all"}
BASE_QUERY_FIELDS = ("timestamp", "index", "utilization.gpu", "utilization.memory", "memory.used", "power.draw", "temperature.gpu",
                     "clocks.sm", "clocks.mem")
HEADER_ALIASES = {"clocks.sm": ("clocks.sm", "clocks.current.sm"), "clocks.mem": ("clocks.mem", "clocks.current.memory")}
CONTEXT_MIB = 100.0                 # memory.used above the resting level by more than this = a CUDA context is held
THROTTLE_BITS = {0x1: "GpuIdle", 0x2: "ApplicationsClocksSetting", 0x4: "SwPowerCap", 0x8: "HwSlowdown", 0x10: "SyncBoost",
                 0x20: "SwThermalSlowdown", 0x40: "HwThermalSlowdown", 0x80: "HwPowerBrakeSlowdown", 0x100: "DisplayClockSetting"}
# Text that must not be in published evidence (checked after redaction; publish removes what it can)
UUID = re.compile(r"\b(?:GPU|MIG)-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")
SERIAL = re.compile(r"^(?P<lead>[ \t]*(?:Serial Number|Chassis Serial Number|GPU PDI|Board ID)[ \t]*:[ \t]*)(?P<value>(?!<redacted>|N/A|0x0\b)\S.*?)[ \t]*$", re.M)
BUS_ID = re.compile(r"^(?P<lead>[ \t]*Bus Id[ \t]*:[ \t]*)(?P<value>(?!<redacted>|N/A)\S.*?)[ \t]*$", re.M)
IPV4 = re.compile(r"(?<![\w.=-])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w.])")
DASHED_IP = re.compile(r"\bip-(?:\d{1,3}-){3}\d{1,3}\b")
IP_ALLOWED = ("127.", "0.0.0.0", "169.254.169.123")
TEXT_SUFFIXES = {".json", ".jsonl", ".csv", ".txt", ".log", ".sh", ".md", ""}


# ---- small helpers ------------------------------------------------------------------------------------------------------

def num(value):
    return value if pg.is_number(value) else None


def fmt(value, digits=3):
    return "n/a" if value is None else (f"{value:.{digits}f}" if isinstance(value, float) else str(value))


def read_jsonl(path: Path) -> list:
    try:
        return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def percentile(values, q):
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[min(len(ordered) - 1, int(math.ceil(q * len(ordered))) - 1)]


def walk(node, path=()):
    """(path, dict) of every dict inside a JSON value."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from walk(value, path + (str(key),))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, path + (str(index),))


class Report:
    def __init__(self):
        self.items = []

    def add(self, ident, title, problems=(), warnings=(), info=(), data=None):
        status = "FAIL" if problems else "WARN" if warnings else "PASS"
        self.items.append({"id": ident, "title": title, "status": status, "problems": list(problems), "warnings": list(warnings),
                           "info": list(info), "data": data if data is not None else {}})

    def inform(self, ident, title, info=(), data=None, warnings=()):
        self.items.append({"id": ident, "title": title, "status": "WARN" if warnings else "INFO", "problems": [], "warnings": list(warnings),
                           "info": list(info), "data": data if data is not None else {}})

    @property
    def failed(self):
        return any(i["status"] == "FAIL" for i in self.items)

    def counts(self):
        return {s: sum(1 for i in self.items if i["status"] == s) for s in ("PASS", "WARN", "INFO", "FAIL")}

    def text(self, header=()):
        lines = list(header)
        for item in self.items:
            lines.append(f"[{item['status']:<4}] {item['id']} {item['title']}")
            for label, key in (("FAIL", "problems"), ("warn", "warnings"), ("info", "info")):
                for line in item[key]:
                    lines.append(f"         {label}: {line}")
        counts = self.counts()
        lines.append(f"summary: {counts['PASS']} PASS, {counts['WARN']} WARN, {counts['INFO']} INFO, {counts['FAIL']} FAIL"
                     f" -> {'NOT READY' if self.failed else 'no FAIL'}")
        return "\n".join(lines) + "\n"


class Ctx:
    """The evidence directory, loaded once."""

    def __init__(self, evidence: Path, expect_enforced_w=700.0, price=4.89):
        self.evidence = Path(evidence)
        self.run = self.evidence / "run"
        self.expect_enforced_w = expect_enforced_w
        self.price = price
        self.schedule = pg.read_json(self.run / "schedule.json")
        self.schedule = self.schedule if isinstance(self.schedule, list) else []
        self.env = pg.read_json(self.run / "environment.json") or {}
        self.gates = pg.read_json(self.evidence / "gates.json")
        self.manifest = pg.read_json(self.evidence / "data-manifest.json")
        self.items = {i.get("engine"): i for i in self.schedule if isinstance(i, dict)}
        self.results = {e: pg.read_json(self.run / f"sf{i.get('scale', SCALE)}" / e / "results.json") for e, i in self.items.items()}
        self.gpu = pg.read_gpu_csv(self.run / "gpu.csv") if (self.run / "gpu.csv").is_file() else None
        self.timeline = read_jsonl(self.evidence / "driver-timeline.jsonl")
        self.timings = {p.name[: -len("-timing.json")]: pg.read_json(p) for p in sorted((self.evidence / "stages").glob("*-timing.json"))}

    def column(self, *names):
        return next((n for n in names if self.gpu and n in self.gpu["columns"]), None)

    def state_dir(self, item):
        return self.run / pg.scale_dir_name(item.get("scale", SCALE))


# ---- C01 .. C02 -----------------------------------------------------------------------------------------------------------

def c01_provenance(ctx, report):
    problems, info = [], []
    args = (ctx.env.get("arguments") or {})
    if args.get("suite") != "smoke" or args.get("synthetic_smoke") is not True:
        problems.append(f"environment.json arguments: suite {args.get('suite')!r}, synthetic_smoke {args.get('synthetic_smoke')!r}; this is not the synthetic smoke workload")
    for key, want in EXPECT.items():
        have = args.get(key)
        if key in ("session_idle_seconds", "session_idle_settle_seconds") and have is None:
            have = (ctx.env.get("paper3_energy") or {}).get(key)
        if have != want:
            problems.append(f"{key} is {have!r}, the campaign's value is {want!r}")
    if sorted(args.get("engines") or []) != sorted(ENGINES):
        problems.append(f"engines {args.get('engines')!r}, expected {sorted(ENGINES)}")
    if not (isinstance(ctx.manifest, dict) and ctx.manifest.get("synthetic_smoke") is True):
        problems.append("data-manifest.json is missing or not marked synthetic_smoke")
    for engine, results in ctx.results.items():
        if not isinstance(results, dict):
            continue
        if results.get("suite") != "smoke" or results.get("synthetic_smoke") is not True:
            problems.append(f"{engine}: results.json suite {results.get('suite')!r}, synthetic_smoke {results.get('synthetic_smoke')!r}")
        extra = sorted(set(results.get("queries") or []) - set(smoke_workloads.QUERY_IDS))
        if extra:
            problems.append(f"{engine}: queries outside the smoke suite: {extra}")
    gpu_name = None
    for entry in ctx.env.get("inventory") or []:
        command = entry.get("command") if isinstance(entry, dict) else None
        if isinstance(command, list) and any(str(c).startswith("--query-gpu=name") for c in command):
            lines = [l for l in (entry.get("output") or "").splitlines() if l.strip()]
            gpu_name = lines[1] if len(lines) > 1 else None
    if not gpu_name or "H100" not in gpu_name:
        problems.append(f"GPU is {gpu_name!r}, not an H100")
    else:
        info.append(f"GPU: {gpu_name}")
    info.append(f"runs per query {args.get('runs')}, cpus {args.get('cpus')}, engine order {args.get('engine_order')}")
    report.add("C01", "provenance: synthetic workload, the campaign's flags, an H100", problems, info=info)


def c02_sessions(ctx, report):
    problems, info = [], []
    for engine in ENGINES:
        item, results = ctx.items.get(engine), ctx.results.get(engine)
        if item is None:
            problems.append(f"{engine}: no session in schedule.json")
            continue
        if item.get("status") != "complete" or item.get("returncode") != 0:
            problems.append(f"{engine}: status {item.get('status')!r}, returncode {item.get('returncode')!r}, failure {item.get('failure_reason')!r}")
        if not isinstance(results, dict):
            problems.append(f"{engine}: results.json missing")
            continue
        if results.get("query_execution_status") != "complete" or results.get("query_errors"):
            errors = [f"{e.get('query')}/{e.get('phase')}: {e.get('type')}: {str(e.get('message'))[:160]}" for e in results.get("query_errors") or []]
            problems.append(f"{engine}: query_execution_status {results.get('query_execution_status')!r}; errors {errors}")
        if results.get("failure"):
            problems.append(f"{engine}: failure {results['failure']}")
        info.append(f"{engine}: {item.get('process_wall_seconds', 0):.0f} s wall, {len([r for r in results.get('records') or [] if r.get('phase') == 'measured'])} measured windows")
    report.add("C02", "sessions: four engines, each complete with exit 0 and no query error", problems, info=info)


# ---- C03 .. C04 -----------------------------------------------------------------------------------------------------------

def c03_fields(ctx, report):
    tele = ctx.env.get("gpu_telemetry") or {}
    problems, warnings = [], []
    extra, missing = tele.get("extra_fields") or [], tele.get("missing_fields") or []
    if not tele.get("started"):
        problems.append(f"the nvidia-smi poller did not start: {tele.get('error')}")
    for field in ("power.draw.average", "power.draw.instant"):
        if field not in extra:
            problems.append(f"{field} is not in the query (missing_fields {missing})")
    throttle = [f for f in extra if f in pg.THROTTLE_COLUMNS]
    if len(throttle) != 1:
        problems.append(f"expected exactly one throttle-reasons field in the query, found {throttle}; missing_fields {missing}")
    if any(f.startswith("module.") for f in extra):
        problems.append(f"module.* fields queried on a board-scope host: {extra}")
    if missing:
        problems.append(f"fields wanted but not listed by this driver: {missing}")
    if tele.get("monitor_tz_env") != "UTC":
        problems.append(f"the poller's TZ is {tele.get('monitor_tz_env')!r}, not UTC")
    if not tele.get("driver_versions"):
        problems.append("no driver version recorded")
    listed = tele.get("fields_listed_by_driver") or []
    deprecated = [f for f in ("clocks_throttle_reasons.active",) if f in listed and f not in extra]
    info = [f"final field list (the query): {tele.get('query')}", f"throttle field used: {throttle[0] if throttle else None}"
            + (f" (the driver also lists the older {deprecated[0]})" if deprecated and throttle else ""),
            f"driver version(s): {tele.get('driver_versions')}", f"{len(listed)} fields listed by `nvidia-smi --help-query-gpu`"]
    report.add("C03", "nvidia-smi fields listed and queried; the final field list", problems, warnings, info,
               {"query": tele.get("query"), "extra_fields": extra, "missing_fields": missing, "driver_versions": tele.get("driver_versions")})


def c04_gpu_csv(ctx, report):
    path = ctx.run / "gpu.csv"
    if ctx.gpu is None:
        report.add("C04", "gpu.csv: real header names, [N/A] handling, the throttle column", ["gpu.csv missing or unreadable"])
        return
    import csv
    with path.open(newline="") as handle:
        rows = list(csv.reader(handle))
    header = [cell.strip() for cell in rows[0]]
    names = [re.sub(r"\s*\[[^\]]*\]\s*$", "", cell) for cell in header]
    query = (ctx.env.get("gpu_telemetry") or {}).get("query") or ""
    wanted = query.split(",") if query else []
    problems, warnings, info = [], [], []
    if len(names) != len(wanted):
        problems.append(f"{len(names)} columns in gpu.csv, {len(wanted)} fields in the query")
    else:
        for field, name in zip(wanted, names):
            accepted = HEADER_ALIASES.get(field, (field,))
            if name not in accepted:
                (problems if field not in BASE_QUERY_FIELDS else warnings).append(f"column named {name!r} for query field {field!r}")
        if wanted[:len(BASE_QUERY_FIELDS)] != list(BASE_QUERY_FIELDS):
            problems.append(f"the first {len(BASE_QUERY_FIELDS)} query fields are not Paper 1's: {wanted[:len(BASE_QUERY_FIELDS)]}")
    cells = {name: [] for name in names}
    bad = 0
    for raw in rows[1:]:
        if len(raw) != len(names):
            bad += 1
            continue
        for name, cell in zip(names, raw):
            cells[name].append(cell.strip())
    total = len(rows) - 1 - bad
    per_column = {}
    for name in names:
        if name == "timestamp":
            continue
        values = cells[name]
        none = sum(1 for v in values if v in pg._SMI_NONE)
        non_numeric = sum(1 for v in values if v not in pg._SMI_NONE and pg.parse_smi_value(v) is None)
        kinds = sorted({v for v in values if v in pg._SMI_NONE})
        per_column[name] = {"cells": len(values), "n_a": none, "non_numeric": non_numeric, "none_spellings": kinds}
        if values and none == len(values):
            problems.append(f"column {name!r} is [N/A] in every row ({kinds})")
        elif none or non_numeric:
            warnings.append(f"column {name!r}: {none} N/A-style cell(s) {kinds} and {non_numeric} non-numeric cell(s) of {len(values)}")
    if bad > 1:
        problems.append(f"{bad} rows with the wrong number of cells (one truncated last row is tolerated)")
    elif bad == 1:
        warnings.append("1 row with the wrong number of cells (the poller was stopped mid-write; tolerated)")
    if ctx.gpu["unordered_rows"]:
        problems.append(f"{ctx.gpu['unordered_rows']} rows with a timestamp earlier than the row before")
    throttle = ctx.column(*pg.THROTTLE_COLUMNS)
    masks = {}
    if throttle is None:
        problems.append("no throttle-reasons column in gpu.csv")
    else:
        for value in ctx.gpu["columns"][throttle]:
            if value is not None:
                masks[value] = masks.get(value, 0) + 1
        info.append("throttle reasons seen (bit mask: rows, names): " + "; ".join(
            f"0x{m:x}: {n} ({'+'.join(name for bit, name in THROTTLE_BITS.items() if m & bit) or 'none'})" for m, n in sorted(masks.items())))
        if not masks:
            problems.append(f"throttle column {throttle!r} holds no parsable mask")
        elif (per_column.get(throttle) or {}).get("n_a"):
            warnings.append(f"throttle column has N/A cells: {per_column[throttle]['n_a']}")
    info.insert(0, f"header as written: {', '.join(header)}")
    info.append(f"{total} rows parsed by paper3_gates.read_gpu_csv ({len(ctx.gpu['t'])} stamped), {bad} malformed")
    report.add("C04", "gpu.csv: real header names, [N/A] handling, the throttle column", problems, warnings, info,
               {"header": header, "columns": per_column, "throttle_masks": {f"0x{m:x}": n for m, n in masks.items()}, "rows": total})


# ---- C05 .. C06 -----------------------------------------------------------------------------------------------------------

def state_texts(ctx, item, key):
    out = {}
    for label in ("start", "end"):
        record = ((item.get(key) or {}).get(label)) if isinstance(item.get(key), dict) else None
        out[label] = (record, None)
        if isinstance(record, dict):
            try:
                out[label] = (record, (ctx.state_dir(item) / str(record.get("file"))).read_text())
            except OSError:
                pass
    return out


def c05_power_limits(ctx, report):
    problems, warnings, info, data = [], [], [], {}
    for engine in ENGINES:
        item = ctx.items.get(engine)
        if item is None:
            continue
        for label, (record, text) in state_texts(ctx, item, pg.POWER_STATE_ITEM_KEY).items():
            where = f"{engine}/{label}"
            if not isinstance(record, dict) or record.get("returncode") != 0 or not (record.get("bytes") or 0) > 0:
                problems.append(f"{where}: `-q -d POWER` not captured ({record and record.get('error')})")
                continue
            if text is None:
                problems.append(f"{where}: {record.get('file')} missing")
                continue
            if hashlib.sha256(text.encode()).hexdigest() != record.get("sha256"):
                problems.append(f"{where}: sha256 differs from the record")
            current = ((record.get("power_limits_w") or {}).get("gpu") or {}).get("current_w")
            query = record.get("power_limits_query")
            enforced = ((query or {}).get("values_w") or {}).get("enforced.power.limit") if isinstance(query, dict) else None
            if current != ctx.expect_enforced_w:
                problems.append(f"{where}: Current Power Limit in -q text is {current}, expected {ctx.expect_enforced_w:g} W")
            if not isinstance(query, dict) or query.get("returncode") != 0 or query.get("error"):
                problems.append(f"{where}: power_limits_query missing or failed: {query and (query.get('error') or query.get('returncode'))}")
            elif enforced != ctx.expect_enforced_w:
                problems.append(f"{where}: enforced.power.limit is {enforced}, expected {ctx.expect_enforced_w:g} W (query values {query.get('values_w')})")
            average = re.search(r"Average Power Draw\s*:\s*(\S.*)", text)
            if average and not re.match(r"\d", average.group(1)):
                warnings.append(f"{where}: Average Power Draw reads {average.group(1).strip()!r} in `-q -d POWER`")
            if label == "start":
                data[engine] = {"current_w": current, "query_values_w": (query or {}).get("values_w") if isinstance(query, dict) else None,
                                "query_fields": (query or {}).get("fields") if isinstance(query, dict) else None,
                                "average_power_draw": average.group(1).strip() if average else None}
    first = next(iter(data.values()), {})
    info.append(f"power limits at the first session's start: -q Current {first.get('current_w')} W; --query-gpu {first.get('query_values_w')}")
    info.append(f"`-q -d POWER` Average Power Draw line at session starts: {sorted({d['average_power_draw'] for d in data.values() if d['average_power_draw']})}")
    report.add("C05", f"POWER captures and power_limits_query: enforced {ctx.expect_enforced_w:g} W at every session start and end", problems, warnings, info, data)


def c06_performance(ctx, report):
    problems, info, data = [], [], {}
    for engine in ENGINES:
        item = ctx.items.get(engine)
        if item is None:
            continue
        for label, (record, text) in state_texts(ctx, item, pg.PERFORMANCE_STATE_ITEM_KEY).items():
            where = f"{engine}/{label}"
            if not isinstance(record, dict) or record.get("returncode") != 0 or not (record.get("bytes") or 0) > 0:
                problems.append(f"{where}: `-q -d PERFORMANCE` not captured")
                continue
            if text is None:
                problems.append(f"{where}: {record.get('file')} missing")
                continue
            if hashlib.sha256(text.encode()).hexdigest() != record.get("sha256"):
                problems.append(f"{where}: sha256 differs from the record")
            if not re.search(r"Clocks (Event|Throttle) Reasons", text):
                problems.append(f"{where}: no 'Clocks Event Reasons' section in the text")
                continue
            active = re.findall(r"^\s+([A-Za-z][A-Za-z0-9 /_-]*?)\s*:\s*Active\s*$", text, re.M)
            data.setdefault(engine, {})[label] = active
    for engine, labels in data.items():
        info.append(f"{engine}: reasons Active at start {labels.get('start')}, at end {labels.get('end')}")
    report.add("C06", "PERFORMANCE captures (clock event reasons) at every session's start and end", problems, info=info, data=data)


# ---- C07 ------------------------------------------------------------------------------------------------------------------

def c07_pulses(ctx, report):
    problems, warnings, info, data = [], [], [], {}
    gpu_ok = ctx.gpu is not None
    for engine in ENGINES:
        item = ctx.items.get(engine)
        if item is None:
            continue
        pulses = item.get(pg.G8_PULSES_KEY)
        launch, exit_ = pg.epoch((item.get("launch") or {}).get("utc")), pg.epoch((item.get("process_exit") or {}).get("utc"))
        for label in ("start", "end"):
            p = pulses.get(label) if isinstance(pulses, dict) else None
            where = f"{engine}/{label}"
            if not isinstance(p, dict):
                problems.append(f"{where}: no pulse record")
                continue
            if p.get("ok") is not True:
                problems.append(f"{where}: ok is {p.get('ok')!r}: {p.get('error')}")
                continue
            requested = num(p.get("requested_seconds"))
            l_utc, d_utc = pg.epoch(p.get("launch_utc")), pg.epoch(p.get("done_utc"))
            l_mono, d_mono = p.get("launch_monotonic_ns"), p.get("done_monotonic_ns")
            if None in (l_utc, d_utc) or not (pg.is_int(l_mono) and pg.is_int(d_mono)):
                problems.append(f"{where}: launch/done stamps missing")
                continue
            dur, dur_utc = (d_mono - l_mono) / 1e9, d_utc - l_utc
            if requested and not (0.8 * requested <= dur <= requested + 5.0):
                problems.append(f"{where}: kernel span {dur:.3f} s for a {requested:g} s pulse")
            if abs(dur - dur_utc) > 0.1:
                problems.append(f"{where}: UTC span {dur_utc:.3f} s and monotonic span {dur:.3f} s disagree")
            r_start, r_end = pg.epoch(p.get("runner_start_utc")), pg.epoch(p.get("runner_end_utc"))
            if r_start is not None and r_end is not None and not (r_start - 0.01 <= l_utc <= d_utc <= r_end + 0.01):
                problems.append(f"{where}: the child's stamps lie outside the runner's stamps around it")
            if label == "start" and launch is not None and d_utc > launch + 0.01:
                problems.append(f"{where}: the start pulse ends {d_utc - launch:.2f} s after the worker was launched")
            if label == "end" and exit_ is not None and l_utc < exit_ - 0.01:
                problems.append(f"{where}: the end pulse starts {exit_ - l_utc:.2f} s before the worker exited")
            lag, status = pg.onset_lag(ctx.gpu, l_utc) if gpu_ok else (None, "no_gpu_csv")
            if status != "ok" or lag is None or abs(lag) > pg.G8_MEDIAN_LAG_S:
                warnings.append(f"{where}: onset in gpu.csv: {status}, lag {fmt(lag)} s (G8's median limit is {pg.G8_MEDIAN_LAG_S} s)")
            data.setdefault(engine, {})[label] = {"ok": True, "kernel_seconds": round(dur, 4), "init_seconds": p.get("init_seconds"),
                                                  "onset_lag_s": None if lag is None else round(lag, 4), "onset_status": status,
                                                  "launch_utc": p.get("launch_utc"), "gap_to_worker_s": None}
    lags = [v["onset_lag_s"] for d in data.values() for v in d.values() if v["onset_lag_s"] is not None]
    info.append(f"{sum(len(d) for d in data.values())} pulses ok; onset lags in gpu.csv (s): median {fmt(statistics.median(lags) if lags else None)}, "
                f"min {fmt(min(lags) if lags else None)}, max {fmt(max(lags) if lags else None)}")
    report.add("C07", "G8 load pulses: gpu_pulse.py ok:true, plausible stamps, onset visible in gpu.csv", problems, warnings, info, data)


# ---- C08 ------------------------------------------------------------------------------------------------------------------

def window_rows(ctx, start, end):
    """Rows of gpu.csv inside [start, end], per the columns the context test needs."""
    memory, clocks = ctx.column("memory.used"), ctx.column("clocks.current.sm", "clocks.sm")
    power, throttle = ctx.column("power.draw"), ctx.column(*pg.THROTTLE_COLUMNS)
    index = [i for i, t in enumerate(ctx.gpu["t"]) if start <= t <= end]

    def values(column):
        return [ctx.gpu["columns"][column][i] for i in index if column and ctx.gpu["columns"][column][i] is not None]
    idle_bits = [1 for v in values(throttle) if int(v) & 0x1]
    return {"samples": len(index), "memory_mib": values(memory), "clocks_mhz": values(clocks), "power_w": values(power),
            "idle_flag_share": len(idle_bits) / len(values(throttle)) if values(throttle) else None}


def c08_idle(ctx, report):
    problems, warnings, info, data = [], [], [], {}
    baseline = None
    memory = ctx.column("memory.used")
    if ctx.gpu is not None and memory:
        series = [v for v in ctx.gpu["columns"][memory] if v is not None]
        baseline = percentile(series, 0.05)
        if baseline is not None and baseline > CONTEXT_MIB:
            warnings.append(f"memory.used never fell below {baseline:.0f} MiB (5th percentile): the GPU was never seen without a context, "
                            "so the context test by memory is not decidable")
            baseline = None
    for engine in ENGINES:
        results, item = ctx.results.get(engine), ctx.items.get(engine)
        if not isinstance(results, dict):
            continue
        windows = {w.get("label"): w for w in results.get("idle_windows") or [] if isinstance(w, dict)}
        if sorted(windows) != sorted(pg.IDLE_LABELS):
            problems.append(f"{engine}: idle windows {sorted(windows)}, expected {sorted(pg.IDLE_LABELS)}")
        data[engine] = {}
        for label in pg.IDLE_LABELS:
            w = windows.get(label)
            if w is None:
                continue
            where = f"{engine}/{label}"
            energy = w.get("gpu_energy") or {}
            power = w.get("mean_power_w")
            want, want_settle = EXPECT["session_idle_seconds"], EXPECT["session_idle_settle_seconds"]
            if w.get("requested_seconds") != want or not want - 1.0 <= (w.get("actual_seconds") or 0) <= want + 2.0:
                problems.append(f"{where}: window {w.get('actual_seconds')} s for a requested {w.get('requested_seconds')} s (campaign: {want:g} s)")
            settle = w.get("settle") or {}
            settle_s = None
            if pg.epoch(settle.get("start_utc")) is not None and pg.epoch(settle.get("end_utc")) is not None:
                settle_s = pg.epoch(settle["end_utc"]) - pg.epoch(settle["start_utc"])
            if settle.get("requested_seconds") != want_settle or settle_s is None or not want_settle - 1.0 <= settle_s <= want_settle + 3.0:
                problems.append(f"{where}: settle {fmt(settle_s)} s for a requested {settle.get('requested_seconds')} s (campaign: {want_settle:g} s)")
            if energy.get("scope_tag") != TAG or not pg.is_int(energy.get("delta_mj")) or energy["delta_mj"] < 0 or energy.get("error"):
                problems.append(f"{where}: gpu_energy scope_tag {energy.get('scope_tag')!r}, delta_mj {energy.get('delta_mj')!r}, error {energy.get('error')}")
            if power is None:
                problems.append(f"{where}: no mean_power_w")
            elif not 20.0 <= power <= 700.0:
                warnings.append(f"{where}: mean power {power:.1f} W is outside 20-700 W")
            entry = {"mean_power_w": power, "delta_mj": energy.get("delta_mj"), "actual_seconds": w.get("actual_seconds"),
                     "energy_interval_seconds": w.get("energy_interval_seconds")}
            if ctx.gpu is not None:
                rows = window_rows(ctx, pg.epoch(w.get("start_utc")), pg.epoch(w.get("end_utc")))
                median_mem = statistics.median(rows["memory_mib"]) if rows["memory_mib"] else None
                entry.update(gpu_csv_samples=rows["samples"], memory_used_mib_median=median_mem, clocks_sm_mhz_median=(
                    statistics.median(rows["clocks_mhz"]) if rows["clocks_mhz"] else None), board_power_w_median=(
                    statistics.median(rows["power_w"]) if rows["power_w"] else None), idle_flag_share=rows["idle_flag_share"],
                    context_held=None if median_mem is None or baseline is None else median_mem - baseline >= CONTEXT_MIB)
            data[engine][label] = entry
        first, last = data[engine].get("before_warmups"), data[engine].get("after_last_trial")
        if first and last and first["mean_power_w"] and last["mean_power_w"]:
            diff = abs(last["mean_power_w"] - first["mean_power_w"]) / ((last["mean_power_w"] + first["mean_power_w"]) / 2)
            data[engine]["pair_relative_difference"] = round(diff, 4)
            if diff > pg.G10_MAX_REL_DIFF:
                warnings.append(f"{engine}: the two idle windows differ by {diff:.1%} (G10 flags above {pg.G10_MAX_REL_DIFF:.0%})")
    for engine in ENGINES:
        entry = (data.get(engine) or {}).get("before_warmups")
        if not entry:
            continue
        expected = engine in GPU_ENGINES
        held = entry.get("context_held")
        if held is not None and held != expected:
            warnings.append(f"{engine}: {'holds' if held else 'does not hold'} a CUDA context in the first idle window by memory.used "
                            f"({fmt(entry['memory_used_mib_median'], 0)} MiB against a resting {fmt(baseline, 0)} MiB); a {'GPU' if expected else 'CPU'} engine was expected to {'hold' if expected else 'not hold'} one")
        info.append(f"{engine} first window: {fmt(entry['mean_power_w'], 1)} W counter, board {fmt(entry.get('board_power_w_median'), 1)} W, "
                    f"{fmt(entry.get('memory_used_mib_median'), 0)} MiB, SM clock {fmt(entry.get('clocks_sm_mhz_median'), 0)} MHz, idle flag in "
                    f"{fmt(entry.get('idle_flag_share'))} of rows -> CUDA context held: {held}")
        last = (data.get(engine) or {}).get("after_last_trial")
        if last:
            info.append(f"{engine} last window: {fmt(last['mean_power_w'], 1)} W, {fmt(last.get('memory_used_mib_median'), 0)} MiB, SM clock "
                        f"{fmt(last.get('clocks_sm_mhz_median'), 0)} MHz")
    info.append(f"resting memory.used (5th percentile of the whole run): {fmt(baseline, 0)} MiB; a context is called held above +{CONTEXT_MIB:g} MiB")
    report.add("C08", "idle windows: both per session with S1-counter energy; CUDA context in the first (clocks, memory)", problems, warnings, info, data)


# ---- C09 ------------------------------------------------------------------------------------------------------------------

def c09_windows(ctx, report):
    problems, warnings, info, table = [], [], [], []
    for engine in ENGINES:
        results = ctx.results.get(engine)
        if not isinstance(results, dict):
            continue
        for record in results.get("records") or []:
            if record.get("phase") != "measured":
                continue
            block = record.get("energy_window")
            where = f"{engine}/{record.get('query')}#{record.get('trial')}"
            if not isinstance(block, dict):
                problems.append(f"{where}: no energy_window block")
                continue
            runs = block.get("runs") or []
            violation = pg.window_rule_violation(runs) or pg.window_inconsistency(block)
            if violation:
                problems.append(f"{where}: {violation}")
            energy = block.get("gpu_energy") or {}
            if energy.get("scope_tag") != TAG or not pg.is_int(energy.get("window_delta_mj")) or energy.get("valid_runs") != block.get("n"):
                problems.append(f"{where}: window energy scope_tag {energy.get('scope_tag')!r}, window_delta_mj {energy.get('window_delta_mj')!r}, "
                                f"valid_runs {energy.get('valid_runs')!r} of {block.get('n')}")
            bad_outputs = [r.get("run") for r in runs if isinstance(r.get("output_check"), dict) and r["output_check"].get("agrees") is False]
            if bad_outputs:
                problems.append(f"{where}: runs {bad_outputs} returned different rows than run 1")
            span = block.get("span") or {}
            gap = span.get("gap_seconds")
            if gap is None or gap < -0.001:
                problems.append(f"{where}: gap_seconds {gap!r}")
            row = {"engine": engine, "query": record.get("query"), "trial": record.get("trial"), "n": block.get("n"),
                   "stop_reason": block.get("stop_reason"), "window_seconds": block.get("window_seconds"),
                   "per_run_seconds": block.get("per_run_seconds"), "span_seconds": span.get("seconds"), "gap_seconds": gap,
                   "gap_ms_per_run": None if gap is None or not block.get("n") else gap / block["n"] * 1000,
                   "t_hat_seconds": block.get("t_hat_seconds"), "window_delta_mj": energy.get("window_delta_mj")}
            table.append(row)
            if gap is not None and (span.get("seconds") or 0) > 0 and gap / span["seconds"] > 0.5 and (block.get("window_seconds") or 0) >= 1.0:
                warnings.append(f"{where}: gaps are {gap / span['seconds']:.0%} of the window span ({gap:.2f} s of {span['seconds']:.2f} s)")
    reasons = {}
    for row in table:
        reasons.setdefault((row["engine"], row["query"]), []).append(f"n={row['n']}:{row['stop_reason']}")
    for (engine, query), cells in sorted(reasons.items()):
        per = [r["per_run_seconds"] for r in table if r["engine"] == engine and r["query"] == query]
        grouped = {cell: cells.count(cell) for cell in dict.fromkeys(cells)}
        info.append(f"{engine} {query}: per run {fmt(min(per), 4)}-{fmt(max(per), 4)} s; windows "
                    + ", ".join(f"{cell}" + (f" x{count}" if count > 1 else "") for cell, count in grouped.items()))
    capped = [r for r in table if r["stop_reason"] == "max_runs"]
    reached = [r for r in table if r["stop_reason"] == "target_reached" and (r["n"] or 0) > 1]
    single = [r for r in table if r["stop_reason"] == "target_reached" and r["n"] == 1]
    if not capped:
        warnings.append("no window stopped at the 50-run cap: the light query was not fast enough on any engine to exercise it")
    if not reached:
        warnings.append("no window stopped by reaching the 10 s target with n > 1: no engine ran a query for 0.2-10 s per run (enlarge --facts-rows)")
    info.append(f"windows: {len(table)}; stopped at the cap {len(capped)}; target reached with n>1 {len(reached)}; n=1 {len(single)}")
    if table:
        gaps = [r["gap_ms_per_run"] for r in table if r["gap_ms_per_run"] is not None]
        info.append(f"gap between runs: median {fmt(statistics.median(gaps), 2)} ms per run, max {fmt(max(gaps), 2)} ms per run")
    report.add("C09", "adaptive windows: stop reasons, n, gaps; the cap and a target-reached window both seen", problems, warnings, info,
               {"windows": table})


# ---- C10 ------------------------------------------------------------------------------------------------------------------

def c10_scope_tags(ctx, report):
    problems, info, tags, counted = [], [], {}, 0
    for engine in ENGINES:
        results = ctx.results.get(engine)
        if not isinstance(results, dict):
            continue
        counter = results.get("gpu_energy_counter") or {}
        if counter.get("scope_tag") != TAG:
            problems.append(f"{engine}: gpu_energy_counter.scope_tag {counter.get('scope_tag')!r}")
        for path, node in walk(results):
            if "scope_tag" in node:
                tags[node["scope_tag"]] = tags.get(node["scope_tag"], 0) + 1
            if "delta_mj" in node or "window_delta_mj" in node or ("mj" in node and "monotonic_ns" in node):    # an energy value
                counted += 1
                if node.get("scope_tag") != TAG:
                    problems.append(f"{engine}: {'.'.join(path)} carries scope_tag {node.get('scope_tag')!r}")
    env_counter = ctx.env.get("gpu_energy_counter") or {}
    if env_counter.get("scope_tag") != TAG:
        problems.append(f"environment.json gpu_energy_counter.scope_tag {env_counter.get('scope_tag')!r}")
    paper3 = ctx.env.get("paper3_energy") or {}
    if paper3.get("gpu_counter_scope_tag") != TAG:
        problems.append(f"environment.json paper3_energy.gpu_counter_scope_tag {paper3.get('gpu_counter_scope_tag')!r}")
    info.append(f"{counted} energy values checked; scope_tag values seen: {tags}")
    report.add("C10", f"scope tags: every GPU energy value is {TAG}", problems[:40], info=info, data={"tags": tags, "values": counted})


# ---- C11 ------------------------------------------------------------------------------------------------------------------

def c11_chrony(ctx, report):
    problems, warnings, info, data = [], [], [], {}
    for engine in ENGINES:
        item = ctx.items.get(engine)
        if item is None:
            continue
        problems += [f"{engine}: {p}" for p in pg.chrony_problems(item)]
        sync = item.get(pg.CLOCK_SYNC_KEY) or {}
        for label in ("start", "end"):
            record = sync.get(label) or {}
            offset = record.get("system_time_offset_seconds")
            data.setdefault(engine, {})[label] = {"offset_s": offset, "last_offset_s": record.get("last_offset_seconds"),
                                                  "stratum": record.get("stratum"), "leap_status": record.get("leap_status")}
            if offset is not None and abs(offset) > 0.05:
                warnings.append(f"{engine}/{label}: system clock {offset:+.4f} s from NTP time")
            if record.get("leap_status") not in (None, "Normal"):
                warnings.append(f"{engine}/{label}: leap status {record.get('leap_status')!r}")
    offsets = [v["offset_s"] for d in data.values() for v in d.values() if v["offset_s"] is not None]
    if offsets:
        info.append(f"offsets of the system clock from NTP time (s): min {min(offsets):+.6f}, max {max(offsets):+.6f}, "
                    f"stratum {sorted({v['stratum'] for d in data.values() for v in d.values()})}")
    report.add("C11", "chrony: the clock offset is logged at every session's start and end", problems, warnings, info, data)


# ---- C12 ------------------------------------------------------------------------------------------------------------------

def c12_gates(ctx, report):
    gates, timing = ctx.gates, ctx.timings.get("gates") or {}
    if not isinstance(gates, dict):
        report.add("C12", "paper3_gates.py report: no FAIL", [f"gates.json missing (gates stage rc {timing.get('rc')})"])
        return
    problems, warnings, info = [], [], []
    for gate, totals in (gates.get("gates") or {}).items():
        if totals.get("fail") or totals.get("not_evaluable"):
            info.append(f"{gate}: pass {totals.get('pass')}, fail {totals.get('fail')}, not_evaluable {totals.get('not_evaluable')}, "
                        f"not_applicable {totals.get('not_applicable')}")
        else:
            info.append(f"{gate}: pass {totals.get('pass')}, not_applicable {totals.get('not_applicable')}")
    for run in gates.get("runs") or []:
        for session in run.get("sessions") or []:
            for gate, result in (session.get("gates") or {}).items():
                detail = "; ".join(result.get("problems") or result.get("reasons") or [])[:300]
                if result.get("outcome") == "fail":
                    problems.append(f"{gate} {session.get('key')}: {detail}")
                elif result.get("outcome") == "not_evaluable":
                    warnings.append(f"{gate} {session.get('key')} not evaluable: {detail}")
        for cell in run.get("cells") or []:
            if cell.get("outcome") != "fail":
                continue
            reasons = set(cell.get("reasons") or [])
            label = f"G9 {cell.get('engine')}/{cell.get('query')}: {sorted(reasons)}"
            if reasons <= {"window_below_10s", "per_run_below_t_min"} and cell.get("query") == "sm_light":
                info.append(label + " (expected: the light query is meant to hit the cap, below the resolution)")
            elif reasons <= {"window_below_10s", "per_run_below_t_min"}:
                warnings.append(label + " (this query was meant to run 1-3 s per run: enlarge --facts-rows)")
            else:
                problems.append(label + f" {cell.get('violations')}")
    for failure in gates.get("round_failures") or []:
        problems.append(f"round failure: {failure}")
    if gates.get("round_failed") and not any(p.startswith("round failure") for p in problems):
        problems.append("round_failed is true")
    info.append(f"gates stage exit {timing.get('rc')} (0 = clean, 1 = a round-failing gate, 2 = host config error); host {gates.get('host')}, t_min {gates.get('t_min_s')} s")
    report.add("C12", "paper3_gates.py with the h100 host config: no FAIL for a clean run", problems, warnings, info,
               {"gates": gates.get("gates"), "round_failed": gates.get("round_failed"), "ineligible": gates.get("ineligible")})


# ---- C13 ------------------------------------------------------------------------------------------------------------------

def c13_poller(ctx, report):
    if ctx.gpu is None or len(ctx.gpu["t"]) < 3:
        report.add("C13", "poller cadence and coverage", ["gpu.csv missing or too short"])
        return
    t = ctx.gpu["t"]
    interval = (num((ctx.env.get("gpu_telemetry") or {}).get("interval_ms")) or pg.DEFAULT_INTERVAL_MS) / 1000.0
    deltas = [b - a for a, b in zip(t, t[1:])]
    problems, warnings, info = [], [], []
    median, p99, worst = statistics.median(deltas), percentile(deltas, 0.99), max(deltas)
    if abs(median - interval) > 0.05:
        problems.append(f"median spacing {median * 1000:.0f} ms against a requested {interval * 1000:.0f} ms")
    if worst > pg.SAMPLER_GAP_S:
        problems.append(f"a gap of {worst:.1f} s (a window containing a sample more than {pg.SAMPLER_GAP_S:g} s after the previous is invalid)")
    elif worst > pg.G7_MAX_GAP_S:
        warnings.append(f"longest gap {worst:.2f} s (G7 limit inside a window: {pg.G7_MAX_GAP_S:g} s)")
    info.append(f"{len(t)} rows over {t[-1] - t[0]:.0f} s; spacing median {median * 1000:.0f} ms, p99 {p99 * 1000:.0f} ms, max {worst * 1000:.0f} ms; "
                f"query: {(ctx.env.get('gpu_telemetry') or {}).get('query')}")
    spans, windows = [], []
    for engine in ENGINES:
        item, results = ctx.items.get(engine), ctx.results.get(engine)
        if item is None:
            continue
        a, b = pg.epoch((item.get("launch") or {}).get("utc")), pg.epoch((item.get("process_exit") or {}).get("utc"))
        if a is not None and b is not None:
            spans.append((f"{engine} session", a, b))
        if isinstance(results, dict):
            for w in results.get("idle_windows") or []:
                windows.append((f"{engine}/{w.get('label')}", pg.epoch(w.get("start_utc")), pg.epoch(w.get("end_utc"))))
            for r in results.get("records") or []:
                span = ((r.get("energy_window") or {}).get("span")) if r.get("phase") == "measured" else None
                if isinstance(span, dict):
                    windows.append((f"{engine}/{r.get('query')}#{r.get('trial')}", pg.epoch(span.get("start_utc")), pg.epoch(span.get("end_utc"))))
    if spans and (t[0] > spans[0][1] or t[-1] < max(s[2] for s in spans)):
        problems.append("the poller does not cover every session (it started after the first launch or ended before the last exit)")
    coverage = []
    for label, a, b in spans + windows:
        if a is None or b is None or b <= a:
            continue
        cov = pg.window_coverage(t, a, b, interval)
        coverage.append((label, cov["coverage"], cov["max_gap_s"], b - a))
    for label, cover, gap, length in coverage:
        if length >= pg.G7_MIN_WINDOW_S and (cover is None or cover < pg.G7_MIN_COVERAGE or gap is None or gap > pg.G7_MAX_GAP_S):
            problems.append(f"{label}: coverage {fmt(cover)} and longest gap {fmt(gap)} s over {length:.1f} s (G7: >= {pg.G7_MIN_COVERAGE}, <= {pg.G7_MAX_GAP_S:g} s)")
    covers = [c for _, c, _, _ in coverage if c is not None]
    if covers:
        info.append(f"coverage of {len(coverage)} sessions and windows: min {min(covers):.3f}, median {statistics.median(covers):.3f}")
    report.add("C13", "poller cadence and coverage of sessions and windows (G7's 0.80 / 1 s rule)", problems, warnings, info,
               {"rows": len(t), "spacing_ms": {"median": median * 1000, "p99": p99 * 1000, "max": worst * 1000}})


# ---- C14 ------------------------------------------------------------------------------------------------------------------

def rows_match(reference, other):
    if len(reference) != len(other):
        return f"{len(reference)} rows against {len(other)}"
    for index, (a, b) in enumerate(zip(reference, other)):
        if set(a) != set(b):
            return f"row {index}: columns {sorted(a)} against {sorted(b)}"
        for key in a:
            x, y = a[key], b[key]
            if isinstance(x, float) or isinstance(y, float):
                if x is None or y is None or abs(x - y) > max(1e-6, 1e-8 * max(abs(x), abs(y))):
                    return f"row {index} {key}: {x!r} against {y!r}"
            elif x != y:
                return f"row {index} {key}: {x!r} against {y!r}"
    return None


def c14_outputs(ctx, report):
    problems, info = [], []

    def warmup_rows(engine, query):
        results = ctx.results.get(engine)
        for r in (results or {}).get("records") or []:
            if r.get("query") == query and r.get("phase") == "warmup" and isinstance(r.get("rows"), list):
                return r["rows"]
        return None
    for query in smoke_workloads.QUERY_IDS:
        reference = warmup_rows("polars", query)
        if reference is None:
            problems.append(f"{query}: no polars warmup rows to compare with")
            continue
        for engine in ENGINES[1:]:
            rows = warmup_rows(engine, query)
            if rows is None:
                problems.append(f"{query}: {engine} has no warmup rows")
                continue
            mismatch = rows_match(reference, rows)
            if mismatch:
                problems.append(f"{query}: {engine} differs from polars: {mismatch}")
        info.append(f"{query}: {len(reference)} result rows")
    report.add("C14", "outputs: every engine returns the polars rows (ints exact, floats rel 1e-8 / abs 1e-6)", problems, info=info)


# ---- C15 .. C16 -----------------------------------------------------------------------------------------------------------

def c15_exclusivity(ctx, report):
    problems, info = [], []
    for label in ("start", "end"):
        capture = pg.read_json(ctx.evidence / f"host-exclusivity-{label}.json")
        apps = (capture or {}).get("gpu_compute_apps") if isinstance(capture, dict) else None
        if not isinstance(apps, dict) or apps.get("apps") is None:
            problems.append(f"host capture {label}: compute processes could not be listed")
        elif apps["apps"]:
            problems.append(f"host capture {label}: foreign GPU compute process(es) {apps['apps']}")
    for engine in ENGINES:
        item = ctx.items.get(engine) or {}
        gate, end = item.get("gpu_idle_gate"), item.get(pg.COMPUTE_APPS_END_KEY)
        if not isinstance(gate, dict) or gate.get("idle") is not True or gate.get("compute_apps") or gate.get("error"):
            problems.append(f"{engine}: gpu_idle_gate {gate}")
        else:
            info.append(f"{engine}: idle gate passed after {gate.get('waited_seconds')} s ({gate.get('polls')} poll(s))")
        if not isinstance(end, dict) or end.get("compute_apps") or end.get("error"):
            problems.append(f"{engine}: compute processes at the end of the session: {end}")
    report.add("C15", "no foreign GPU process at the host captures, the idle gates and the session ends", problems, info=info)


def c16_cpu_power(ctx, report):
    tele = ctx.env.get("power_telemetry") or {}
    info = [f"power_telemetry: enabled {tele.get('enabled')}, started {tele.get('started')}, status {tele.get('status')}, cpu_energy_source "
            f"{tele.get('cpu_energy_source')}, node_power_source {tele.get('node_power_source')}, interval {tele.get('interval_seconds')}",
            f"reasons: {tele.get('reasons')}"]
    present = (ctx.run / "power.csv").is_file()
    info.append(f"power.csv present: {present}" + (f" ({(ctx.run / 'power.csv').stat().st_size} bytes)" if present else ""))
    report.inform("C16", "CPU / node power sampler on this host (the h100 config declares cpu_energy none)", info, {"power_telemetry": tele},
                  warnings=[] if tele.get("started") else [f"the power sampler did not start: {tele.get('error')}"])


def scan_redaction(evidence: Path):
    """(path, rule) of every leak in the evidence text files."""
    leaks = []
    for path in sorted(p for p in evidence.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES and p.name != "SHA256SUMS.txt"):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(evidence))
        for rule, regex in (("gpu uuid", UUID), ("serial number", SERIAL), ("bus id", BUS_ID), ("ip address", IPV4)):
            found = [m.group(0) for m in regex.finditer(text) if not (rule == "ip address" and m.group(0).startswith(IP_ALLOWED))]
            if found:
                leaks.append((rel, f"{rule} x{len(found)}"))
    return leaks


def c17_redaction(ctx, report):
    leaks = scan_redaction(ctx.evidence)
    redaction = pg.read_json(ctx.evidence / "redaction.json") or {}
    report.add("C17", "redaction: no GPU UUID, serial number, bus id or IP address in the evidence",
               [f"{path}: {rule}" for path, rule in leaks[:30]], info=[f"publish removed: {redaction.get('removed')}"], data={"leaks": len(leaks)})


# ---- C18 ------------------------------------------------------------------------------------------------------------------

def c18_time(ctx, report):
    info, problems, data = [], [], {}
    for name in ("setup", "data", "run", "gates"):
        t = ctx.timings.get(name)
        if isinstance(t, dict):
            data[name] = t.get("seconds")
            info.append(f"stage {name}: {t.get('seconds')} s ({t.get('seconds', 0) / 60:.1f} min), exit {t.get('rc')}, {t.get('start_utc')} -> {t.get('end_utc')}")
        else:
            info.append(f"stage {name}: no timing record")
    setup = ctx.timings.get("setup")
    if not isinstance(setup, dict):
        problems.append("setup_h100.sh was not timed (no stages/setup-timing.json)")
    events = {e.get("name"): e.get("epoch") for e in ctx.timeline if isinstance(e, dict)}
    if events.get("create_call") and events.get("ssh_ready"):
        info.append(f"launch to SSH ready: {(events['ssh_ready'] - events['create_call']) / 60:.1f} min")
    billed = None
    if events.get("create_call") and events.get("terminated"):
        billed = events["terminated"] - events["create_call"]
        data["billed_seconds_estimate"] = billed
        info.append(f"create call to confirmed termination: {billed / 60:.1f} min -> about ${billed / 3600 * ctx.price:.2f} at ${ctx.price:g}/h "
                    f"(Lambda bills per minute from boot; the boot is part of the create-to-ready time)")
    elif ctx.timeline:
        info.append("no 'terminated' event in the driver timeline: the instance was not confirmed gone")
    stage_marks = {}
    for e in ctx.timeline:
        if isinstance(e, dict) and str(e.get("name", "")).rsplit("_", 1)[-1] in ("start", "end"):
            stage_marks.setdefault(e["name"].rsplit("_", 1)[0], {})[e["name"].rsplit("_", 1)[1]] = e.get("epoch")
    seen = [f"{n} {(m['end'] - m['start']) / 60:.1f} min" for n, m in stage_marks.items() if m.get("start") and m.get("end")]
    if seen:
        info.append("driver view of the stages (start call to exit code seen, polling included): " + ", ".join(seen))
    for engine in ENGINES:
        item = ctx.items.get(engine)
        if item:
            info.append(f"{engine}: session {item.get('process_wall_seconds', 0):.0f} s (worker launch to exit)")
    first, last = (ctx.schedule[0], ctx.schedule[-1]) if ctx.schedule else (None, None)
    if first and last and pg.epoch((first.get("launch") or {}).get("utc")) and pg.epoch((last.get("process_exit") or {}).get("utc")):
        span = pg.epoch(last["process_exit"]["utc"]) - pg.epoch(first["launch"]["utc"])
        data["sessions_span_seconds"] = span
        info.append(f"first launch to last exit: {span / 60:.1f} min (four sessions, pulses and idle windows included)")
    report.inform("C18", "time and cost: every stage, the whole run", info, data, warnings=problems)


# ---- the commands -------------------------------------------------------------------------------------------------------------

CHECKS = (c01_provenance, c02_sessions, c03_fields, c04_gpu_csv, c05_power_limits, c06_performance, c07_pulses, c08_idle, c09_windows,
          c10_scope_tags, c11_chrony, c12_gates, c13_poller, c14_outputs, c15_exclusivity, c16_cpu_power, c17_redaction, c18_time)


def check(evidence: Path, expect_enforced_w=700.0, price=4.89) -> Report:
    ctx = Ctx(evidence, expect_enforced_w, price)
    report = Report()
    if not ctx.schedule:
        report.add("C00", "the run directory", [f"{ctx.run}/schedule.json missing or empty: the run stage left nothing to check"])
    for function in CHECKS:
        try:
            function(ctx, report)
        except Exception as exc:    # a malformed file must be one FAIL, not the end of the checklist
            report.add(function.__name__[:3].upper(), function.__name__, [f"the check itself failed: {type(exc).__name__}: {exc}"])
    return report


def redact_text(text: str, masks):
    """(text, counts): masks, UUIDs, serial numbers, bus ids, IP addresses and ip-a-b-c-d host names removed."""
    counts = {}

    def count(rule, n):
        if n:
            counts[rule] = counts.get(rule, 0) + n

    def replace_ip(match):
        if match.group(0).startswith(IP_ALLOWED):
            return match.group(0)
        count("ip address", 1)
        return "<ip>"
    for mask in sorted({m for m in masks if m and len(m) >= 3}, key=len, reverse=True):
        count("mask", text.count(mask))
        text = text.replace(mask, "<redacted>")
    for rule, regex, replacement in (("gpu uuid", UUID, "<redacted>"), ("serial number", SERIAL, lambda m: m.group("lead") + "<redacted>"),
                                     ("bus id", BUS_ID, lambda m: m.group("lead") + "<redacted>"), ("dashed ip host", DASHED_IP, "<host>")):
        text, n = regex.subn(replacement, text)
        count(rule, n)
    return IPV4.sub(replace_ip, text), counts


def publish(bundle: Path, evidence: Path, masks) -> dict:
    evidence.mkdir(parents=True, exist_ok=True)
    totals, files = {}, 0
    for source in sorted(p for p in bundle.rglob("*") if p.is_file()):
        target = evidence / source.relative_to(bundle)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix in TEXT_SUFFIXES:
            text, counts = redact_text(source.read_text(errors="replace"), masks)
            target.write_text(text)
            for rule, n in counts.items():
                totals[rule] = totals.get(rule, 0) + n
        else:
            target.write_bytes(source.read_bytes())
        files += 1
    (evidence / "redaction.json").write_text(json.dumps({"files": files, "removed": totals, "rules": ["explicit masks (instance address, host name)",
                                                                                                      "GPU/MIG UUIDs", "serial numbers", "bus ids", "IPv4 addresses", "dashed-IP host names"]}, indent=1) + "\n")
    return {"files": files, "removed": totals}


def sums(evidence: Path) -> int:
    lines = []
    for path in sorted(p for p in evidence.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt"):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(evidence)}  {path.stat().st_size}B")
    (evidence / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")
    return len(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    pub = sub.add_parser("publish")
    pub.add_argument("bundle", type=Path)
    pub.add_argument("evidence", type=Path)
    pub.add_argument("--mask", action="append", default=[])
    chk = sub.add_parser("check")
    chk.add_argument("evidence", type=Path)
    chk.add_argument("--price-per-hour", type=float, default=4.89)
    chk.add_argument("--expect-enforced-w", type=float, default=700.0)
    chk.add_argument("--expect-idle-settle-s", type=float, default=EXPECT["session_idle_settle_seconds"],
                     help="settle before each in-session idle window the campaign uses (60; rehearsals with shorter settles only)")
    chk.add_argument("--json", action="store_true", help="print the JSON report instead of the text")
    chk.add_argument("--no-write", action="store_true", help="do not write checklist.json / checklist.txt")
    hashes = sub.add_parser("sums")
    hashes.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
    if args.command == "publish":
        print(json.dumps(publish(args.bundle, args.evidence, args.mask)))
        return 0
    if args.command == "sums":
        print(f"{sums(args.evidence)} files hashed")
        return 0
    EXPECT["session_idle_settle_seconds"] = args.expect_idle_settle_s
    report = check(args.evidence, args.expect_enforced_w, args.price_per_hour)
    header = [f"Paper 3 H100 smoke test checklist for {args.evidence.name} (synthetic load; nothing here is a study result)"]
    text = report.text(header)
    if not args.no_write:
        (args.evidence / "checklist.json").write_text(json.dumps({"counts": report.counts(), "failed": report.failed, "items": report.items}, indent=1) + "\n")
        (args.evidence / "checklist.txt").write_text(text)
    print(json.dumps({"counts": report.counts(), "failed": report.failed, "items": report.items}, indent=1) if args.json else text, end="")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
