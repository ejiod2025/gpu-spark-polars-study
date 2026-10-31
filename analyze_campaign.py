#!/usr/bin/env python3
"""Campaign statistics for the H100 four-engine study (implements docs/H100_EXPERIMENT_DESIGN.md section 9).

    analyze_campaign.py --root CAMPAIGN_ROOT --output OUT [--label H100] [--a100 ROOT ...]
    analyze_campaign.py --host H100=ROOT --host B200=ROOT [--host A100=ROOT --host GH200=ROOT] --output OUT
    analyze_campaign.py --synthetic --seed 7 --output OUT        # fake trees (four hosts) with injected ratios

CAMPAIGN_ROOT holds campaign.json, round-<k>/gates.json, round-<k>/<suite>/ (a run_experiment output
root: sf*/<engine>/results.json, schedule.json, gpu.csv, environment.json) and, optionally,
side-study/round-<k>/{polars-1.44.2,polars-2.0.0}/<suite>/. Rounds whose gates did not pass are excluded
and listed. Only the Python standard library is used.

Estimators (all pre-registered):
  per-query ratio       geometric mean over rounds of t_ref / t_engine; 95% CI is the t-interval on the
                        mean log-ratio with df = rounds - 1, back-transformed; min-max over rounds
  per-(suite, scale)    per round, the geometric mean of per-query ratios over queries available in
                        every round for both engines; then the same t-interval across rounds
  legacy totals         sum of per-query medians, reported separately and never combined with geomeans
Outputs: tables/*.csv|md|tex, stats.json (nested), stats_flat.json (dotted keys), STATS_SCHEMA.md,
STATS_REPORT.md, integrity.json + INTEGRITY.md (completeness counts only: sessions, queries by outcome, trials, gates). With --synthetic every output carries "SYNTHETIC - NOT RESULTS".

Hosts (amendments A2/A3): every host runs its own complete campaign in its own root and is analyzed on its own
(--root; stats.json carries a "host" identity block). The cross-host mode (--host LABEL=PATH, repeatable; PATH is a
campaign root or a stats.json written by a per-host run) never pools data: per (suite, scale, query, engine) it
compares the host's geometric-mean round medians with the reference host's (default H100), time ratio host /
reference, with a Welch-type interval on the log scale (unpaired rounds, Welch-Satterthwaite df). It writes
cross_host.json (+ flat copy, xhost_<HOST>.json per host, tables/x*.csv|md|tex, CROSS_HOST_REPORT.md) with the host
identity table and the caveats (CPU model/architecture, vCPU, RAM, driver). Descriptive only; no tests.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import shutil
import statistics
import subprocess
import sys

from analyze_results import (ENGINE_ORDER, IntegrityError, check_polars_gpu_metadata, gpu_short_name,
                             host_label, host_shape, inventory_output, read_environment, read_json, scale_value)

SCHEMA_VERSION = "1.0"
# names used in tables, reports and figures alike (analyze_results.ENGINE_LABELS says "CPU Spark" / "GPU Spark")
ENGINE_NAMES = {"polars": "Polars CPU", "polars_gpu": "Polars GPU", "spark_cpu": "Spark CPU", "spark_gpu": "Spark GPU"}
WATERMARK = "SYNTHETIC — NOT RESULTS"
WATERMARK_ASCII = "SYNTHETIC --- NOT RESULTS"
LEVEL = 0.95
SUITES = ("baseline", "complex")
BASELINE_QUERIES = ("q01", "q03", "q06", "q10", "q12", "q14")
COMPLEX_QUERIES = ("cx_q09", "cx_q13", "cx_semi_anti", "cx_window", "cx_distinct", "cx_self_join")
DEFAULT_QUERIES = {"baseline": BASELINE_QUERIES, "complex": COMPLEX_QUERIES}
CPU_ENGINES = ("polars", "spark_cpu")
GPU_ENGINES = ("polars_gpu", "spark_gpu")
# Section 8 of the design: rejected by cudf-polars under raise_on_fail=True (preflight 2026-10-26).
PREDECLARED_UNSUPPORTED = {("polars_gpu", "cx_q13"), ("polars_gpu", "cx_window")}
# Latin square of section 5 (seed 20261029): A = spark_cpu, B = polars_gpu, C = polars, D = spark_gpu.
DESIGN_LETTERS = {"A": "spark_cpu", "B": "polars_gpu", "C": "polars", "D": "spark_gpu"}
DESIGN_SEQUENCES = {1: "ABDC", 2: "BCAD", 3: "CDBA", 4: "DACB"}
POSITION_NONE = "none"  # predecessor label for the first session of a (suite, scale) block
COVERAGE_CLASSES = ("unsupported_operation", "out_of_memory")   # amendment A1: failures that are coverage outcomes, not integrity failures
UNSTABLE_NOTE = "excluded by A1 (unstable coverage); not an estimate"
# contrast id -> (numerator engine or None, denominator engine or None, label, research question)
CONTRASTS = {
    "c1": {"numerator": "polars", "denominator": "polars_gpu", "label": "Polars CPU / Polars GPU", "rq": "RQ1",
           "subject": "polars_gpu"},
    "c2": {"numerator": "spark_cpu", "denominator": "spark_gpu", "label": "Spark CPU / Spark GPU", "rq": "RQ1",
           "subject": "spark_gpu"},
    "c3": {"numerator": "spark_gpu", "denominator": "polars_gpu", "label": "Spark GPU / Polars GPU", "rq": "RQ2",
           "subject": "polars_gpu"},
    "c4": {"numerator": "polars", "denominator": "best_gpu", "label": "Polars CPU / best GPU engine", "rq": "RQ2/RQ3",
           "subject": None},
}
# Sensitivity variant of C4: only queries (and rounds) where both GPU engines produced a timing.
C4_STRICT = {"numerator": "polars", "denominator": "best_gpu_both", "label": "Polars CPU / best GPU (both GPU engines supported)",
             "rq": "RQ3 sensitivity", "subject": None}
ALL_CONTRASTS = {**CONTRASTS, "c4_strict": C4_STRICT}


# ------------------------------------------------------------------ statistics (stdlib only)

def _betacf(a, b, x):
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 500):
        m2 = 2 * m
        step = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + step * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + step / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        step = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + step * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + step / c
        c = c if abs(c) > tiny else tiny
        change = d * c
        h *= change
        if abs(change - 1.0) < 3e-16:
            break
    return h


def betainc(a, b, x):
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t, df):
    tail = 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


def t_quantile(p, df):
    """Student-t quantile by bisection on t_cdf; accurate to ~1e-12 (no scipy in the venv)."""
    if not 0.5 < p < 1.0 or df < 1:
        raise ValueError("t_quantile supports 0.5 < p < 1 and df >= 1")
    low, high = 0.0, 1.0
    while t_cdf(high, df) < p:
        high *= 2.0
        if high > 1e12:
            break
    for _ in range(200):
        mid = 0.5 * (low + high)
        if t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def t_critical(df, level=LEVEL):
    return t_quantile(0.5 + level / 2.0, df)


def mean_sd(values):
    values = list(values)
    n = len(values)
    if n == 0:
        return None, None
    mean = math.fsum(values) / n
    if n < 2:
        return mean, None
    return mean, math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (n - 1))


def ratio_summary(ratios, rounds):
    """Pre-registered estimate: geometric mean of per-round ratios, t-interval on the mean log-ratio.

    ratios: positive per-round ratios (aligned with rounds). With a single round there is an estimate but
    no interval; with none there is nothing.
    """
    ratios = [float(r) for r in ratios]
    n = len(ratios)
    base = {"estimate": None, "ci_low": None, "ci_high": None, "min": None, "max": None, "n_rounds": n,
            "df": max(n - 1, 0) if n else None, "log_mean": None, "log_sd": None, "rounds": list(rounds),
            "per_round": ratios}
    if n == 0:
        return base
    logs = [math.log(r) for r in ratios]
    mean, sd = mean_sd(logs)
    base.update(estimate=math.exp(mean), min=min(ratios), max=max(ratios), log_mean=mean, log_sd=sd)
    if n >= 2:
        half = t_critical(n - 1) * sd / math.sqrt(n)
        base.update(ci_low=math.exp(mean - half), ci_high=math.exp(mean + half))
    return base


def geomean(values):
    values = [v for v in values if v is not None]
    return math.exp(math.fsum(math.log(v) for v in values) / len(values)) if values else None


def cv(values):
    """Coefficient of variation (sample sd / mean); None unless at least two positive values."""
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    mean, sd = mean_sd(values)
    return sd / mean if mean else None


def quantile(values, q):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    position = (len(values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def distribution(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0, "median": None, "mean": None, "p90": None, "max": None}
    return {"n": len(values), "median": statistics.median(values), "mean": math.fsum(values) / len(values),
            "p90": quantile(values, 0.9), "max": max(values)}


# ------------------------------------------------------------------ loading

def good_number(value, positive=True):
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def classify_failure(kind, message):
    text = f"{kind} {message}".lower()
    if re.search(r"out of memory|\boom\b|bad_alloc|memoryerror|memory allocation|cudaerrormemoryallocation|"
                 r"cannot allocate|insufficient (gpu|device) memory|rmm.*alloc", text):
        return "out_of_memory"
    if re.search(r"time ?out|timed out", text):
        return "timeout"
    if re.search(r"notimplemented|not implemented|not supported|unsupported|does not support|cannot be executed on the gpu", text):
        return "unsupported_operation"
    return "error"


def read_schedule(directory):
    try:
        value = read_json(Path(directory) / "schedule.json")
    except (OSError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def parse_utc(text):
    try:
        value = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def scale_name(value):
    """Directory-style scale name for a schedule scale ('30', 30, 'sf30', '1.5')."""
    text = str(value)
    if text.startswith("sf"):
        return text
    return "sf" + text.replace(".", "p")


def scale_sort_key(name):
    value = scale_value(name)
    return float(value) if value is not None else math.inf


def parse_byte_count(value):
    """Bytes from a Spark accumulator value: 12345, '12345' or '53.31MB (55903584 bytes)'; None when it cannot be read."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\((\d+) bytes\)", str(value)) or re.fullmatch(r"\s*(\d+)\s*", str(value))
    return int(match[1]) if match else None


def read_spark_task_memory(events_dir, accumulator="gpuMaxDeviceMemoryBytes"):
    """([(task launch time in ms, bytes)], notes) from a Spark event-log directory (plain or .gz JSON lines).

    The RAPIDS Accelerator reports each task's largest device allocation in the task accumulator `gpuMaxDeviceMemoryBytes`. Only
    SparkListenerTaskEnd lines that carry it are decoded. Compressed logs other than .gz (zstd, lz4, snappy) are not read and are noted.
    """
    import gzip
    tasks, notes = [], []
    directory = Path(events_dir)
    if not directory.is_dir():
        return tasks, notes
    for path in sorted(p for p in directory.iterdir() if p.is_file()):
        if path.suffix in (".zstd", ".lz4", ".snappy", ".lzf"):
            notes.append(f"{path.name}: compressed event log ({path.suffix}) not read")
            continue
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", errors="replace") as handle:
                for line in handle:
                    if "SparkListenerTaskEnd" not in line or accumulator not in line:
                        continue
                    try:
                        info = json.loads(line)["Task Info"]
                    except (ValueError, KeyError):
                        continue
                    for item in info.get("Accumulables") or []:
                        if item.get("Name") == accumulator:
                            value = parse_byte_count(item.get("Update"))
                            if value is not None and isinstance(info.get("Launch Time"), (int, float)):
                                tasks.append((float(info["Launch Time"]), value))
        except OSError as exc:
            notes.append(f"{path.name}: unreadable ({type(exc).__name__})")
    return tasks, notes


def action_task_memory(records, tasks):
    """{(query, phase, trial): largest task-reported device memory of the tasks launched inside that action's time window, or None}."""
    out = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        start, end = parse_utc(record.get("started_at_utc")), parse_utc(record.get("ended_at_utc"))
        if start is None or end is None:
            continue
        low, high = start.timestamp() * 1000.0, end.timestamp() * 1000.0
        values = [value for launched, value in tasks if low <= launched <= high]
        out[(record.get("query"), record.get("phase"), record.get("trial"))] = max(values) if values else None
    return out


# ------------------------------------------------------------------ Spark plans (AQE final plan per trial)

PLAN_ENGINES = ("spark_cpu", "spark_gpu")
PLAN_VOLATILE = (                      # ids that change from one execution to the next without changing the plan
    (re.compile(r",?\s*\[loreId=\d+\]"), ""), (re.compile(r",?\s*loreId=\d+"), ""), (re.compile(r"#\d+L?"), "#"),
    (re.compile(r"plan_id=\d+"), "plan_id=N"), (re.compile(r"((?:Shuffle|Broadcast|TableCache)QueryStage) \d+"), r"\1 N"),
    (re.compile(r"@[0-9a-f]{6,}"), "@H"))
PLAN_TREE_PREFIX = re.compile(r"^[\s:+|\-]*")
PLAN_NOTE = ("Descriptive. The final adaptive plan of every Spark trial (plans/<query>-<phase>-<trial>.txt) is normalized (expression, plan, stage and "
             "lore ids removed) and hashed; a cell is listed when its trials do not all end in the same plan. Medians, estimates and intervals are "
             "unchanged by this section: every measured trial still enters its cell's median as designed.")


def split_plan(text):
    """(final plan text, initial plan text or None) of a saved Spark plan file."""
    marker = "== Initial Plan =="
    index = text.find(marker)
    if index < 0:
        return text, None
    return text[:index].rstrip().removesuffix("+-").rstrip(), text[index + len(marker):]


def normalize_plan(text):
    for pattern, replacement in PLAN_VOLATILE:
        text = pattern.sub(replacement, text)
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def plan_signature(text):
    """12 hex digits identifying a normalized plan; equal plans give equal signatures on every host."""
    return hashlib.sha256(normalize_plan(text).encode()).hexdigest()[:12]


def plan_lines(text):
    """Operator lines of a plan without the tree drawing: 'GpuBroadcastHashJoin [l_partkey#], [p_partkey#], Inner, GpuBuildRight, false'."""
    lines = [PLAN_TREE_PREFIX.sub("", line) for line in normalize_plan(text).splitlines()]
    return [line for line in lines if line and not line.startswith("==")]


def exchange_label(line):
    match = re.search(r"(\w*partitioning)\((.*)\)", line)
    if not match:
        return line.split(" ", 1)[1].split(",")[0].strip() if " " in line else line
    args = match.group(2).replace("#", "")
    count = re.search(r"(\d+)(?:,\s*\w+Mode)?\s*$", args)
    keys = args[:count.start()].rstrip(", ") if count else args
    return f"{match.group(1)}({keys}, {count.group(1)})" if count else f"{match.group(1)}({keys})"


def plan_features(text):
    """Join strategy, exchanges (kind, keys, partition count), shuffle readers (coalesced, local, skewed) and operator counts of a plan."""
    lines = plan_lines(text)
    operators = Counter(line.split(" ", 1)[0] for line in lines)
    joins = {name: n for name, n in operators.items() if "Join" in name or name.endswith("CartesianProduct")}
    exchanges = Counter(exchange_label(line) for line in lines if re.match(r"(?:Gpu)?(?:Columnar)?Exchange\b", line))
    readers = Counter((line.split(" ", 1) + ["plain"])[1].strip() or "plain"
                      for line in lines if re.match(r"(?:Gpu)?(?:CustomShuffleReader|AQEShuffleRead)\b", line))
    return {"operators": dict(sorted(operators.items())), "joins": dict(sorted(joins.items())), "exchanges": dict(sorted(exchanges.items())),
            "shuffle_readers": dict(sorted(readers.items())), "n_operator_lines": len(lines)}


def plan_changes(reference, other, reference_text, other_text, limit=24):
    """What differs in `other` against `reference`: feature -> {name: [reference count, other count]} for counts that differ, plus the operator
    lines present in only one of them (order and indentation ignored)."""
    out = {}
    for feature in ("operators", "joins", "exchanges", "shuffle_readers"):
        names = set(reference[feature]) | set(other[feature])
        diff = {name: [reference[feature].get(name, 0), other[feature].get(name, 0)] for name in sorted(names)
                if reference[feature].get(name, 0) != other[feature].get(name, 0)}
        if diff:
            out[feature] = diff
    ref_lines, other_lines = Counter(plan_lines(reference_text)), Counter(plan_lines(other_text))
    out["lines_only_in_this_plan"] = [line.replace("#", "")[:220] for line in sorted((other_lines - ref_lines).elements())][:limit]
    out["lines_only_in_reference_plan"] = [line.replace("#", "")[:220] for line in sorted((ref_lines - other_lines).elements())][:limit]
    return out


def read_spark_stages(events_dir):
    """([(submission ms, completion ms, tasks, shuffle bytes written)], notes) of the completed stages in a Spark event-log directory (plain or .gz)."""
    import gzip
    stages, notes = [], []
    directory = Path(events_dir)
    if not directory.is_dir():
        return stages, notes
    for path in sorted(p for p in directory.iterdir() if p.is_file()):
        if path.suffix in (".zstd", ".lz4", ".snappy", ".lzf"):
            notes.append(f"{path.name}: compressed event log ({path.suffix}) not read")
            continue
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", errors="replace") as handle:
                for line in handle:
                    if "SparkListenerStageCompleted" not in line:
                        continue
                    try:
                        info = json.loads(line)["Stage Info"]
                    except (ValueError, KeyError):
                        continue
                    submitted, completed = info.get("Submission Time"), info.get("Completion Time")
                    if info.get("Failure Reason") or not isinstance(submitted, (int, float)) or not isinstance(completed, (int, float)):
                        continue
                    written = 0
                    for item in info.get("Accumulables") or []:
                        if item.get("Name") == "internal.metrics.shuffle.write.bytesWritten" and isinstance(item.get("Value"), (int, float)):
                            written += int(item["Value"])
                    stages.append((float(submitted), float(completed), int(info.get("Number of Tasks") or 0), written))
        except OSError as exc:
            notes.append(f"{path.name}: unreadable ({type(exc).__name__})")
    return stages, notes


def action_stages(trials, stages):
    """{(query, phase, trial): {stages, tasks, shuffle_write_bytes}} for the stages submitted inside each trial's time window."""
    out = {}
    for item in trials:
        start, end = parse_utc(item.get("started")), parse_utc(item.get("ended"))
        if start is None or end is None:
            continue
        low, high = start.timestamp() * 1000.0, end.timestamp() * 1000.0
        inside = [s for s in stages if low <= s[0] <= high]
        out[(item["query"], item["phase"], item["trial"])] = {
            "stages": len(inside), "tasks": sum(s[2] for s in inside), "shuffle_write_bytes": sum(s[3] for s in inside)}
    return out


def read_session_plans(session_dir, records):
    """([{query, phase, trial, seconds, started, ended, signature, initial_signature}], {signature: features}, {signature: final plan text}, missing).

    The saved plan file of a record is named in its 'plan' field (relative to the session directory)."""
    session_dir = Path(session_dir)
    trials, catalog, texts, missing = [], {}, {}, 0
    for record in records:
        if not isinstance(record, dict) or not record.get("plan") or record.get("phase") not in ("warmup", "measured"):
            continue
        path = Path(str(record["plan"]))
        path = path if path.is_absolute() else session_dir / path
        try:
            final, initial = split_plan(path.read_text(errors="replace"))
        except OSError:
            missing += 1
            continue
        signature = plan_signature(final)
        if signature not in catalog:
            catalog[signature] = plan_features(final)
            texts[signature] = final
        trials.append({"query": record.get("query"), "phase": record["phase"], "trial": record.get("trial"),
                       "seconds": record.get("seconds") if good_number(record.get("seconds")) else None,
                       "started": record.get("started_at_utc"), "ended": record.get("ended_at_utc"),
                       "signature": signature, "initial_signature": plan_signature(initial) if initial else None})
    return trials, catalog, texts, missing


def constant_series(values, tolerance=0.05):
    """True if a sampled series stays within 5% of its minimum (a reserved pool, not memory in use); False for empty or zero-valued series."""
    values = [v for v in values if v is not None]
    return bool(values) and min(values) > 0 and max(values) <= min(values) * (1.0 + tolerance)


def nondecreasing(values, relative=0.01, absolute=64 * 1024 * 1024):
    """True if a series never falls by more than 1% (+64 MiB of sampling jitter) below its running maximum."""
    best = None
    for value in values:
        if best is not None and value < best - max(relative * best, absolute):
            return False
        best = value if best is None else max(best, value)
    return True


def largest_fall(values):
    """Largest drop of a series below its running maximum (0 for a series that never falls)."""
    best, drop = None, 0
    for value in values:
        best = value if best is None else max(best, value)
        drop = max(drop, best - value)
    return drop


def session_cells(session, queries, schedule_item=None, engine=None, task_memory=None):
    """Per-query cells for one engine session: {query: cell}.

    cell = {status: ok|failed|incomplete|session_failed|invalid, reason_class, reason, trials, median,
            peak_gpu_bytes, task_device_bytes}. task_device_bytes: per measured trial, the largest task-reported device memory
            (Spark GPU event-log accumulator gpuMaxDeviceMemoryBytes) of the tasks launched inside the action; [] when not available.
            Only complete sessions count: a session without completed_at_utc, with a recorded
    global failure, or with a non-zero orchestrator return code makes every query a session_failed cell.
    """
    cells = {}
    session_problem, session_class = None, "session_failed"
    if not isinstance(session, dict):
        session_problem = "no readable results.json"
    elif schedule_item and (schedule_item.get("returncode") not in (None, 0) or
                            str(schedule_item.get("status", "")).lower() in {"failed", "timeout", "timed_out", "killed"}):
        session_problem = f"orchestrator status={schedule_item.get('status')} returncode={schedule_item.get('returncode')}"
    elif session.get("failure") or session.get("failed_at_utc"):
        failure = session.get("failure") or {}
        session_problem = f"session failure: {failure.get('type', 'error')}: {str(failure.get('message', ''))[:200]}"
    elif not session.get("completed_at_utc"):
        session_problem = "session has no completed_at_utc"
    elif engine == "polars_gpu":
        try:
            check_polars_gpu_metadata(session)
        except IntegrityError as exc:
            session_problem, session_class = str(exc), "invalid"
    if session_problem:
        for query in queries:
            cells[query] = {"status": "session_failed" if session_class != "invalid" else "invalid",
                            "reason_class": session_class, "reason": session_problem[:300],
                            "trials": [], "median": None, "peak_gpu_bytes": []}
        return cells
    runs = session.get("runs")
    errors = {}
    for key in ("query_errors", "query_failures"):
        for item in session.get(key) or []:
            if isinstance(item, dict) and item.get("query"):
                errors.setdefault(item["query"], item)
    records = [r for r in session.get("records") or [] if isinstance(r, dict) and r.get("phase") == "measured"]
    for query in queries:
        measured = sorted((r for r in records if r.get("query") == query), key=lambda r: r.get("trial") or 0)
        times = [r["seconds"] for r in measured if good_number(r.get("seconds"))]
        memory = [r["peak_gpu_memory_bytes"] for r in measured if good_number(r.get("peak_gpu_memory_bytes"), positive=False)]
        if query in errors:
            error = errors[query]
            kind, message = str(error.get("type", "error")), str(error.get("message", ""))
            cells[query] = {"status": "failed", "reason_class": classify_failure(kind, message),
                            "reason": f"{kind}: {message}".strip()[:300], "trials": [], "median": None, "peak_gpu_bytes": []}
        elif query not in (session.get("queries") or queries):
            cells[query] = {"status": "absent", "reason_class": None, "reason": "query not declared by session",
                            "trials": [], "median": None, "peak_gpu_bytes": []}
        elif not times or (isinstance(runs, int) and len(times) != runs) or len(times) != len(measured):
            cells[query] = {"status": "incomplete", "reason_class": "error",
                            "reason": f"{len(times)} usable of {runs} measured trials", "trials": times,
                            "median": None, "peak_gpu_bytes": []}
        else:
            tasks = [task_memory.get((query, "measured", r.get("trial"))) for r in measured] if task_memory else []
            cells[query] = {"status": "ok", "reason_class": None, "reason": "", "trials": times,
                            "median": statistics.median(times), "peak_gpu_bytes": memory,
                            "task_device_bytes": [v for v in tasks if v is not None]}
    return cells


def block_orders(schedule):
    """{scale_name: [engine, ...]} in execution order (start_utc when complete, else file order)."""
    by_scale = {}
    for index, item in enumerate(schedule):
        if item.get("engine") is None or item.get("scale") is None:
            continue
        by_scale.setdefault(scale_name(item["scale"]), []).append((parse_utc(item.get("start_utc")), index, item))
    orders = {}
    for name, entries in by_scale.items():
        if all(start is not None for start, _, _ in entries):
            entries.sort(key=lambda e: (e[0], e[1]))
        last = {}
        for _, index, item in entries:  # a re-run of the same engine replaces the earlier entry
            last[item["engine"]] = index
        orders[name] = [item["engine"] for _, index, item in entries if last[item["engine"]] == index]
    return orders


REL_TOL, ABS_TOL = 1e-8, 1e-6   # gate G2 (design section 7): the rule preflight.py applies


def compare_rows(expected, actual):
    """Ordered comparison with preflight.py's rule (integers exact, an int on either side exact, floats rel 1e-8 / abs 1e-6).

    Returns (passed, largest relative difference over the numeric pairs or None).
    """
    mismatches, largest = 0, None

    def visit(left, right):
        nonlocal mismatches, largest
        if isinstance(left, bool) or isinstance(right, bool):
            mismatches += type(left) is not type(right) or left != right
        elif isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if not (math.isfinite(left) and math.isfinite(right)):
                mismatches += 1
                return
            relative = abs(left - right) / max(abs(left), abs(right)) if (left or right) else 0.0
            largest = relative if largest is None else max(largest, relative)
            if isinstance(left, int) or isinstance(right, int):
                mismatches += left != right
            else:
                mismatches += not math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)
        elif isinstance(left, dict) and isinstance(right, dict):
            mismatches += left.keys() != right.keys()
            for key in left.keys() & right.keys():
                visit(left[key], right[key])
        elif isinstance(left, list) and isinstance(right, list):
            mismatches += len(left) != len(right)
            for a, b in zip(left, right):
                visit(a, b)
        else:
            mismatches += type(left) is not type(right) or left != right

    visit(expected, actual)
    return mismatches == 0, largest


def output_agreement(sessions, reference="polars"):
    """Gate G2 recomputed from the saved outputs: every record of every other engine against the reference engine's warmup rows.

    sessions: {engine: results.json dict}. Counts compared records, records that disagree (0 in an accepted round), the largest
    relative difference among numeric values, and records that could not be compared (no rows / no reference rows).
    """
    ref = sessions.get(reference)
    if not isinstance(ref, dict):
        return None
    warm = {}
    for record in ref.get("records") or []:
        if isinstance(record, dict) and record.get("phase") == "warmup":
            warm.setdefault(record.get("query"), []).append(record.get("rows"))
    reference_rows = {q: rows[0] for q, rows in warm.items() if len(rows) == 1 and isinstance(rows[0], list) and rows[0]}
    out = {"compared": 0, "failed": 0, "max_rel_diff": None, "without_rows": 0, "without_reference": 0, "failed_by_engine": {}, "by_engine": {}}
    for engine, session in sessions.items():
        if engine == reference or not isinstance(session, dict):
            continue
        for record in session.get("records") or []:
            if not isinstance(record, dict) or record.get("query") is None:
                continue
            if not isinstance(record.get("rows"), list):
                out["without_rows"] += 1
            elif record["query"] not in reference_rows:
                out["without_reference"] += 1
            else:
                passed, largest = compare_rows(reference_rows[record["query"]], record["rows"])
                out["compared"] += 1
                out["failed"] += not passed
                mine = out["by_engine"].setdefault(engine, {"compared": 0, "failed": 0, "max_rel_diff": None, "queries": {}})
                mine["compared"] += 1
                mine["failed"] += not passed
                if largest is not None:
                    mine["max_rel_diff"] = largest if mine["max_rel_diff"] is None else max(mine["max_rel_diff"], largest)
                    previous = mine["queries"].get(record["query"])
                    mine["queries"][record["query"]] = largest if previous is None else max(previous, largest)
                if not passed:
                    out["failed_by_engine"][engine] = out["failed_by_engine"].get(engine, 0) + 1
                if largest is not None:
                    out["max_rel_diff"] = largest if out["max_rel_diff"] is None else max(out["max_rel_diff"], largest)
    return out


def session_exited_normally(session, schedule_item):
    """True for a results.json that finished (completed_at_utc), recorded no session failure, and whose process exited 0."""
    if not isinstance(session, dict) or session.get("failure") or session.get("failed_at_utc") or not session.get("completed_at_utc"):
        return False
    item = schedule_item or {}
    return item.get("returncode") in (None, 0) and str(item.get("status", "")).lower() not in {"failed", "timeout", "timed_out", "killed"}


def load_run_dir(run_dir, suite=None, expect_engine=None):
    """One run_experiment output root -> {cells, sessions, blocks, schedule, environment, notes}.

    cells: {(suite, scale, query, engine): cell}; sessions: {(suite, scale, engine): summary dict}.
    suite overrides the suite recorded in results.json (the directory name is authoritative in a campaign).
    """
    run_dir = Path(run_dir)
    schedule = read_schedule(run_dir)
    run = {"dir": str(run_dir), "schedule": schedule, "environment": read_environment(run_dir), "cells": {},
           "sessions": {}, "blocks": {}, "notes": [], "suites": set(), "agreement": {}}
    scale_dirs = sorted((p for p in run_dir.glob("sf*") if p.is_dir() and scale_value(p.name) is not None),
                        key=lambda p: scale_sort_key(p.name))
    expected = {(scale_name(i["scale"]), i["engine"]): i for i in schedule if i.get("engine") and i.get("scale") is not None}
    orders = block_orders(schedule)
    seen = set()
    for scale_dir in scale_dirs:
        raw_sessions, block_suite = {}, None
        engines = {p.name for p in scale_dir.iterdir() if p.is_dir() and (p / "results.json").is_file()}
        engines |= {e for (s, e) in expected if s == scale_dir.name}
        for engine in sorted(engines, key=lambda e: (ENGINE_ORDER.index(e) if e in ENGINE_ORDER else 99, e)):
            if expect_engine and engine != expect_engine:
                continue
            item = expected.get((scale_dir.name, engine))
            path = scale_dir / engine / "results.json"
            try:
                session = read_json(path)
                if not isinstance(session, dict):
                    raise ValueError("not an object")
                if session.get("engine") not in (None, engine):
                    raise ValueError(f"engine field {session.get('engine')!r} differs from directory")
            except (OSError, ValueError) as exc:
                session = None
                run["notes"].append(f"{scale_dir.name}/{engine}: results.json unreadable ({type(exc).__name__})")
            this_suite = suite or (session or {}).get("suite") or "baseline"
            raw_sessions[engine], block_suite = session, this_suite
            if suite and session and session.get("suite") not in (None, suite):
                run["notes"].append(f"{scale_dir.name}/{engine}: results.json says suite {session.get('suite')!r}, directory says {suite!r}")
            run["suites"].add(this_suite)
            declared = list((session or {}).get("queries") or [])
            queries = declared or list(DEFAULT_QUERIES.get(this_suite, ()))
            task_memory, event_notes, event_tasks = None, [], 0
            if engine == "spark_gpu" and session and (scale_dir / f"{engine}-events").is_dir():
                tasks, event_notes = read_spark_task_memory(scale_dir / f"{engine}-events")
                event_tasks = len(tasks)
                task_memory = action_task_memory(session.get("records") or [], tasks) if tasks else None
                run["notes"] += [f"{scale_dir.name}/{engine}: {note}" for note in event_notes]
            cells = session_cells(session, queries, item, engine, task_memory)
            for query, cell in cells.items():
                run["cells"][(this_suite, scale_dir.name, query, engine)] = cell
            plan_trials, plan_catalog, plan_texts, plans_missing = (
                read_session_plans(scale_dir / engine, (session or {}).get("records") or []) if engine in PLAN_ENGINES and session else ([], {}, {}, 0))
            wall = item.get("process_wall_seconds") if item else None
            records = (session or {}).get("records") or []
            run["sessions"][(this_suite, scale_dir.name, engine)] = {
                "process_wall_seconds": wall if good_number(wall) else None,
                "start_utc": (item or {}).get("start_utc"),
                "launch_to_engine_ready_seconds": (session or {}).get("launch_to_engine_ready_seconds"),
                "session_and_table_setup_seconds": (session or {}).get("session_and_table_setup_seconds"),
                "warmup_seconds": math.fsum(r["seconds"] for r in records if r.get("phase") == "warmup" and good_number(r.get("seconds"))),
                "measured_seconds": math.fsum(r["seconds"] for r in records if r.get("phase") == "measured" and good_number(r.get("seconds"))),
                "version": (session or {}).get("version"),
                "runs": (session or {}).get("runs"),
                "cleanup_policy": (session or {}).get("query_cleanup_policy"),
                "cleanup_records": [r for r in (session or {}).get("cleanup_records") or [] if isinstance(r, dict)],
                "failed_cleanup_records": [r for r in (session or {}).get("failed_query_cleanup_records") or [] if isinstance(r, dict)],
                "gpu_inventory": (session or {}).get("gpu_inventory"),
                "spark_jars": sorted(Path(j).name for j in str(((session or {}).get("spark_conf") or {}).get("spark.jars", "")).split(",") if j),
                "cudf_polars_version": ((session or {}).get("polars_gpu") or {}).get("cudf_polars_version"),
                "spark_gpu_profile": (session or {}).get("spark_gpu_profile"),
                "returncode": (item or {}).get("returncode"),
                "readable": session is not None,
                "completed": session_exited_normally(session, item),
                "spark_events": {"tasks_with_device_memory": event_tasks,
                                 "actions_mapped": sum(1 for v in (task_memory or {}).values() if v is not None), "actions": len(task_memory or {})}
                                if engine == "spark_gpu" else None,
                "gpu_peak_series": [r.get("peak_gpu_memory_bytes") for r in sorted(
                    (x for x in records if isinstance(x, dict) and good_number(x.get("peak_gpu_memory_bytes"), positive=False) and x.get("started_at_utc")),
                    key=lambda x: x["started_at_utc"])] if engine in GPU_ENGINES else None,
                "completed_at_utc": (session or {}).get("completed_at_utc"),
                "measured_trials": sum(1 for r in records if isinstance(r, dict) and r.get("phase") == "measured" and good_number(r.get("seconds"))),
                "records": len(records),
                "plan_records": sum(1 for r in records if isinstance(r, dict) and r.get("plan")),
                "measured_actions": [{"query": r.get("query"), "trial": r.get("trial"), "start": parse_utc(r.get("started_at_utc")).timestamp(),
                                      "end": parse_utc(r.get("ended_at_utc")).timestamp()}
                                     for r in records if engine in GPU_ENGINES and isinstance(r, dict) and r.get("phase") == "measured"
                                     and parse_utc(r.get("started_at_utc")) and parse_utc(r.get("ended_at_utc"))],
                "protocol": {k: (session or {}).get(k) for k in ("cpu_threads", "runs", "cache_policy", "timing_scope", "query_cleanup_policy",
                                                                 "failure_policy", "suite_version", "polars_execution_engine", "started_at_utc")},
                "spark_conf": ((session or {}).get("spark_conf") if engine == "spark_gpu" and isinstance((session or {}).get("spark_conf"), dict) else None),
                "plan_trials": plan_trials, "plan_catalog": plan_catalog, "plan_texts": plan_texts, "plans_missing": plans_missing,
                "events_dir": str(scale_dir / f"{engine}-events") if engine in PLAN_ENGINES and (scale_dir / f"{engine}-events").is_dir() else None,
            }
            seen.add((this_suite, scale_dir.name))
        if block_suite is not None:
            run["agreement"][(block_suite, scale_dir.name)] = output_agreement(raw_sessions)
    for suite_name, scale in seen:
        order = orders.get(scale)
        if order:
            run["blocks"][(suite_name, scale)] = order
    return run


ROUND_DIR = re.compile(r"^round-(\d+)(?:[-_]?(repeat-\d+|[A-Za-z0-9]+))?$")   # round-2, round-2b, round-2-repeat-1 (run_campaign.py)
PASS_WORDS = {"pass", "passed", "ok", "success"}


def read_gates(directory):
    """(verdict, reasons) from gates.json; verdict None when the file is missing or unreadable."""
    try:
        value = read_json(Path(directory) / "gates.json")
    except (OSError, ValueError):
        return None, ["gates.json missing or unreadable"]
    if not isinstance(value, dict):
        return None, ["gates.json is not an object"]
    verdict = str(value.get("verdict", "")).strip().lower() or None
    reasons = []
    for key in ("reasons", "failed_gates", "failures"):
        item = value.get(key)
        if isinstance(item, list):
            reasons += [str(x) if not isinstance(x, dict) else json.dumps(x, sort_keys=True) for x in item]
        elif isinstance(item, dict):
            reasons += [f"{k}: {v}" for k, v in item.items()]
    gates = value.get("gates")
    if isinstance(gates, dict):
        reasons += [f"{k}: {v if not isinstance(v, dict) else v.get('verdict', v)}" for k, v in gates.items()
                    if str(v.get("verdict") if isinstance(v, dict) else v).lower() not in PASS_WORDS | {"true"}]
    return verdict, reasons


def discover_rounds(root, allow_ungated=False):
    """Round directories -> list of {id, dir, label, verdict, reasons, included, exclusion}; later repeats supersede earlier ones."""
    found = []
    for path in sorted(Path(root).iterdir()) if Path(root).is_dir() else []:
        match = ROUND_DIR.match(path.name)
        if not path.is_dir() or not match:
            continue
        verdict, reasons = read_gates(path)
        if verdict is None and allow_ungated and (path / "gates.json").is_file() is False:
            passed, reason = True, "gates.json missing; accepted because --allow-ungated was given"
        elif verdict is None:
            passed, reason = False, "no usable gates.json verdict; round is not accepted"
        elif verdict in PASS_WORDS:
            passed, reason = True, ""
        else:
            passed, reason = False, "gates verdict " + repr(verdict) + (": " + "; ".join(reasons) if reasons else "")
        found.append({"id": int(match.group(1)), "dir": path, "label": path.name, "verdict": verdict, "reasons": reasons,
                      "included": passed, "exclusion": "" if passed else reason,
                      "note": reason if passed and reason else ""})
    by_id = {}
    for entry in found:
        by_id.setdefault(entry["id"], []).append(entry)
    rounds = []
    for round_id in sorted(by_id):
        entries = sorted(by_id[round_id], key=lambda e: (int((re.search(r"repeat-(\d+)", e["label"]) or [0, 0])[1]), e["label"]))
        passing = [e for e in entries if e["included"]]
        chosen = passing[-1] if passing else None
        for entry in entries:
            if entry["included"] and entry is not chosen:
                entry["included"], entry["exclusion"] = False, f"superseded by repeat {chosen['label']}"
        rounds += entries
    return rounds


def read_preflight(directory):
    """round-<k>/preflight/preflight-report.json (preflight.py's gate report) or None; the analysis reads counts out of it, never verdicts it did not see."""
    try:
        value = read_json(Path(directory) / "preflight" / "preflight-report.json")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("checks"), list) else None


def load_campaign(root, allow_ungated=False):
    root = Path(root)
    campaign = {"root": str(root), "metadata": None, "rounds": [], "warnings": []}
    try:
        meta = read_json(root / "campaign.json")
        campaign["metadata"] = meta if isinstance(meta, dict) else None
    except (OSError, ValueError):
        campaign["warnings"].append("campaign.json missing or unreadable")
    campaign["rounds"] = discover_rounds(root, allow_ungated)
    for entry in campaign["rounds"]:
        entry["runs"] = {}
        entry["preflight"] = read_preflight(entry["dir"]) if entry["included"] else None
        if not entry["included"]:
            continue
        for suite in SUITES:
            suite_dir = entry["dir"] / suite
            if suite_dir.is_dir():
                entry["runs"][suite] = load_run_dir(suite_dir, suite=suite)
                campaign["warnings"] += [f"{entry['label']}/{suite}: {n}" for n in entry["runs"][suite]["notes"]]
    return campaign


# ------------------------------------------------------------------ estimation

class Data:
    """Included-round view of a campaign: valid medians, statuses and block orders."""

    def __init__(self, campaign, engines=ENGINE_ORDER):
        self.engines = tuple(engines)
        self.rounds = [e["id"] for e in campaign["rounds"] if e["included"]]
        self.cells = {}      # (round, suite, scale, query, engine) -> cell
        self.sessions = {}   # (round, suite, scale, engine) -> session summary
        self.blocks = {}     # (round, suite, scale) -> [engines in order]
        self.queries = {s: list(DEFAULT_QUERIES[s]) for s in SUITES}
        self.scales = {s: set() for s in SUITES}
        for entry in campaign["rounds"]:
            if not entry["included"]:
                continue
            for suite, run in entry["runs"].items():
                for (s, scale, query, engine), cell in run["cells"].items():
                    self.cells[(entry["id"], s, scale, query, engine)] = cell
                    self.scales[s].add(scale)
                    if query not in self.queries.setdefault(s, []):
                        self.queries[s].append(query)
                for (s, scale, engine), summary in run["sessions"].items():
                    self.sessions[(entry["id"], s, scale, engine)] = summary
                for (s, scale), order in run["blocks"].items():
                    self.blocks[(entry["id"], s, scale)] = order
        self.scales = {s: sorted(v, key=scale_sort_key) for s, v in self.scales.items() if v}
        self.suites = [s for s in SUITES if s in self.scales]
        # Amendment A1: a coverage outcome (unsupported operation, out of memory) seen in some rounds of a cell but not in all of them is
        # "unstable coverage"; the cell is excluded from every contrast in ALL rounds, so its timings never enter `valid`.
        self.unstable = {}   # (suite, scale, query, engine) -> {coverage_rounds, other_rounds, outcomes, per_round_seconds}
        by_cell = {}
        for (rnd, suite, scale, query, engine), cell in self.cells.items():
            by_cell.setdefault((suite, scale, query, engine), {})[rnd] = cell
        for key, by_round in by_cell.items():
            coverage = sorted(r for r, c in by_round.items() if c["status"] == "failed" and c["reason_class"] in COVERAGE_CLASSES)
            if coverage and len(coverage) < len(by_round):
                self.unstable[key] = {
                    "coverage_rounds": coverage, "other_rounds": sorted(r for r in by_round if r not in coverage),
                    "outcomes": {r: (by_round[r]["reason_class"] if r in coverage else by_round[r]["status"]) for r in sorted(by_round)},
                    "per_round_seconds": {r: by_round[r]["median"] for r in sorted(by_round)}}
        self.valid = {}      # (suite, scale, query, engine) -> {round: median}
        for (rnd, suite, scale, query, engine), cell in self.cells.items():
            if cell["status"] == "ok" and (suite, scale, query, engine) not in self.unstable:
                self.valid.setdefault((suite, scale, query, engine), {})[rnd] = cell["median"]

    def block_rounds(self, suite, scale):
        return sorted({r for (r, s, sc, _, _) in self.cells if s == suite and sc == scale})

    def present_engines(self):
        return tuple(e for e in self.engines if any(k[4] == e for k in self.cells)) or self.engines

    def times(self, suite, scale, query, engine):
        return self.valid.get((suite, scale, query, engine), {})

    def failure(self, suite, scale, query, engine):
        """Most informative non-ok cell of an engine/query (reason_class, reason), or None; unstable coverage is named as such."""
        info = self.unstable.get((suite, scale, query, engine))
        if info:
            classes = sorted({info["outcomes"][r] for r in info["coverage_rounds"]})
            return ("unstable_coverage", f"coverage outcome ({', '.join(classes)}) in rounds {info['coverage_rounds']} but not in rounds {info['other_rounds']}")
        found = [c for (r, s, sc, q, e), c in sorted(self.cells.items())
                 if (s, sc, q, e) == (suite, scale, query, engine) and c["status"] not in ("ok", "absent")]
        return (found[0]["reason_class"], found[0]["reason"]) if found else None


def per_round_ratios(data, suite, scale, query, numerator, denominator):
    """({round: ratio}, best-engine labels per round or None) for a contrast cell."""
    num = data.times(suite, scale, query, numerator)
    ratios, best = {}, {}
    for rnd, t_num in num.items():
        if denominator in ("best_gpu", "best_gpu_both"):
            candidates = {e: data.times(suite, scale, query, e).get(rnd) for e in GPU_ENGINES}
            candidates = {e: t for e, t in candidates.items() if t is not None}
            need = 2 if denominator == "best_gpu_both" else 1
            if len(candidates) < need:
                continue
            winner = min(candidates, key=candidates.get)
            ratios[rnd], best[rnd] = t_num / candidates[winner], winner
        else:
            t_den = data.times(suite, scale, query, denominator).get(rnd)
            if t_den is not None:
                ratios[rnd] = t_num / t_den
    return ratios, (best if denominator.startswith("best_gpu") else None)


def contrast_query(data, suite, scale, query, spec):
    numerator, denominator = spec["numerator"], spec["denominator"]
    ratios, best = per_round_ratios(data, suite, scale, query, numerator, denominator)
    rounds = sorted(ratios)
    block = data.block_rounds(suite, scale)
    summary = ratio_summary([ratios[r] for r in rounds], rounds)
    best_gpu = denominator.startswith("best_gpu")
    if rounds:
        summary["status"] = "ok" if len(rounds) == len(block) else "partial"
        summary["blocked_by"] = []
    else:
        sides = [numerator] + (list(GPU_ENGINES) if best_gpu else [denominator])
        lacking = [e for e in sides if not data.times(suite, scale, query, e)]
        if denominator == "best_gpu" and any(e not in lacking for e in GPU_ENGINES):
            lacking = [e for e in lacking if e not in GPU_ENGINES]  # one GPU engine ran: best-of is defined
        blockers = []
        for engine in lacking:
            failure = data.failure(suite, scale, query, engine)
            blockers.append({"engine": engine, "reason_class": failure[0] if failure else "not_run",
                             "reason": failure[1] if failure else "no cell recorded"})
        ran = any((r, suite, scale, query, e) in data.cells for r in block for e in sides)
        unstable = any(b["reason_class"] == "unstable_coverage" for b in blockers)
        summary["status"] = "not_run" if not ran else ("unstable" if unstable else ("unsupported" if blockers else "no_overlap"))
        summary["blocked_by"] = blockers
    if best:
        counts = {}
        for engine in best.values():
            counts[engine] = counts.get(engine, 0) + 1
        summary["best_engine_rounds"] = counts
    involved = [numerator] + (list(GPU_ENGINES) if best_gpu else [denominator])
    summary["unstable_engines"] = sorted(e for e in involved if (suite, scale, query, e) in data.unstable)   # engines left out of this estimate by A1
    return summary


def contrast_suite_scale(data, suite, scale, spec, per_query):
    """Suite-level estimate over queries timed in every block round for both sides."""
    block = data.block_rounds(suite, scale)
    used = [q for q, s in per_query.items() if s["status"] == "ok" and s["n_rounds"] == len(block) and block]
    excluded = {q: s["status"] if s["status"] not in ("unsupported", "unstable") else
                f"{s['status']}: " + ", ".join(f"{b['engine']} ({b['reason_class']})" for b in s["blocked_by"])
                for q, s in per_query.items() if q not in used}
    rounds, geo = [], []
    for rnd in block:
        logs = [math.log(per_query[q]["per_round"][per_query[q]["rounds"].index(rnd)]) for q in used]
        if logs:
            rounds.append(rnd)
            geo.append(math.exp(math.fsum(logs) / len(logs)))
    summary = ratio_summary(geo, rounds)
    summary.update(n_queries=len(used), queries_used=used, queries_excluded=excluded)
    return summary


def estimate_contrasts(data):
    """{contrast: {suite: {scale: suite-level record with 'q' per-query records}}}."""
    out = {}
    for name, spec in ALL_CONTRASTS.items():
        out[name] = {}
        for suite in data.suites:
            out[name][suite] = {}
            for scale in data.scales[suite]:
                per_query = {q: contrast_query(data, suite, scale, q, spec) for q in data.queries[suite]}
                record = contrast_suite_scale(data, suite, scale, spec, per_query)
                record["q"] = per_query
                out[name][suite][scale] = record
    return out


def estimate_medians(data):
    """Geometric-mean time over rounds per (suite, scale, query, engine), with the per-round medians."""
    out = {}
    for suite in data.suites:
        out[suite] = {}
        for scale in data.scales[suite]:
            block = data.block_rounds(suite, scale)
            out[suite][scale] = {}
            for query in data.queries[suite]:
                out[suite][scale][query] = {}
                for engine in data.present_engines():
                    times = data.times(suite, scale, query, engine)
                    per_round = [times.get(r) for r in block]
                    present = [t for t in per_round if t is not None]
                    failure = None if present else data.failure(suite, scale, query, engine)
                    if (suite, scale, query, engine) in data.unstable:      # A1: no estimate, not even a time; raw values live in unstable_coverage
                        out[suite][scale][query][engine] = {
                            "status": "unstable", "geomean": None, "min": None, "max": None, "n_rounds": 0, "rounds": list(block),
                            "per_round": [None] * len(block), "cv_between_rounds": None, "reason_class": "unstable_coverage"}
                        continue
                    out[suite][scale][query][engine] = {
                        "status": "ok" if len(present) == len(block) and block else ("partial" if present else
                                  ("failed" if failure else "not_run")),
                        "geomean": geomean(present), "min": min(present) if present else None,
                        "max": max(present) if present else None, "n_rounds": len(present),
                        "rounds": list(block), "per_round": per_round,
                        "cv_between_rounds": cv(present), "reason_class": failure[0] if failure else None,
                    }
    return out


def estimate_fastest(data, medians):
    """RQ2: per (suite, scale, query) the engine with the lowest geometric-mean time, with a ratio interval to the runner-up.

    The interval is the usual t-interval on the per-round log ratio of the two selected engines; the winner is
    chosen from the same data, so it is descriptive and not a test.
    """
    out = {}
    for suite in data.suites:
        out[suite] = {}
        for scale in data.scales[suite]:
            out[suite][scale] = {}
            for query in data.queries[suite]:
                cells = {e: c for e, c in medians[suite][scale][query].items() if c["status"] == "ok"}
                if not cells:
                    out[suite][scale][query] = {"winner": None}
                    continue
                ranked = sorted(cells, key=lambda e: cells[e]["geomean"])
                record = {"winner": ranked[0], "ranking": ranked, "geomean_seconds": cells[ranked[0]]["geomean"],
                          "runner_up": None}
                if len(ranked) > 1:
                    runner = ranked[1]
                    rounds = [r for r in data.block_rounds(suite, scale)]
                    a, b = data.times(suite, scale, query, runner), data.times(suite, scale, query, ranked[0])
                    shared = [r for r in rounds if r in a and r in b]
                    summary = ratio_summary([a[r] / b[r] for r in shared], shared)
                    record.update(runner_up=runner, runner_up_over_winner=summary["estimate"],
                                  ci_low=summary["ci_low"], ci_high=summary["ci_high"])
                out[suite][scale][query] = record
    return out


def cpu_reference_check(data, medians):
    """C4 uses Polars CPU as the CPU reference (section 9); record where Spark CPU is faster so the claim can be qualified."""
    out = {}
    for suite in data.suites:
        out[suite] = {}
        for scale in data.scales[suite]:
            slower = [q for q in data.queries[suite]
                      if medians[suite][scale][q].get("polars", {}).get("geomean") is not None
                      and medians[suite][scale][q].get("spark_cpu", {}).get("geomean") is not None
                      and medians[suite][scale][q]["spark_cpu"]["geomean"] < medians[suite][scale][q]["polars"]["geomean"]]
            compared = sum(1 for q in data.queries[suite] if medians[suite][scale][q].get("polars", {}).get("geomean") is not None
                           and medians[suite][scale][q].get("spark_cpu", {}).get("geomean") is not None)
            out[suite][scale] = {"n_queries": compared, "queries_where_spark_cpu_faster": slower}
    return out


def coverage_matrix(data):
    """RQ4: per suite/query/scale/engine support across included rounds."""
    out, discrepancies = {}, []
    for suite in data.suites:
        out[suite] = {}
        for query in data.queries[suite]:
            out[suite][query] = {}
            for scale in data.scales[suite]:
                out[suite][query][scale] = {}
                block = data.block_rounds(suite, scale)
                for engine in data.present_engines():
                    cells = [data.cells.get((r, suite, scale, query, engine)) for r in block]
                    ok = sum(1 for c in cells if c and c["status"] == "ok")
                    bad = [c for c in cells if c and c["status"] not in ("ok", "absent")]
                    if ok == len(block) and block:
                        status, reason_class, reason = "supported", None, ""
                    elif ok:
                        status, reason_class, reason = "partial", bad[0]["reason_class"] if bad else None, bad[0]["reason"] if bad else ""
                    elif bad:
                        status, reason_class, reason = "failed", bad[0]["reason_class"], bad[0]["reason"]
                    else:
                        status, reason_class, reason = "not_run", None, ""
                    predeclared = (engine, query) in PREDECLARED_UNSUPPORTED
                    record = {"status": status, "rounds_ok": ok, "rounds_total": len(block), "reason_class": reason_class,
                              "reason": reason[:200], "predeclared_unsupported": predeclared,
                              "unstable": (suite, scale, query, engine) in data.unstable}
                    out[suite][query][scale][engine] = record
                    if predeclared and status in ("supported", "partial"):
                        discrepancies.append(f"{suite}/{scale}/{query}/{engine}: pre-declared unsupported but produced a timing in {ok} round(s)")
                    elif not predeclared and status in ("failed", "partial") and engine in GPU_ENGINES:
                        discrepancies.append(f"{suite}/{scale}/{query}/{engine}: {status} ({reason_class}) but not pre-declared unsupported")
    return out, discrepancies


def plan_variability_section(campaign):
    """Descriptive plan-variability note (stats["plan_variability"]): the Spark cells whose trials do not all end in the same final adaptive plan.

    Per listed cell and plan: the trials (measured and warm-up) that ended in it, their times, the join / exchange / shuffle-reader structure, what
    differs from the plan with the lowest median measured time, and, where the Spark event log is present, the stages, tasks and shuffle bytes
    written inside those trials' actions. Nothing here feeds an estimate."""
    sessions, per_cell, missing = {}, {}, 0
    for entry in campaign["rounds"]:
        if not entry["included"]:
            continue
        for suite, run in entry["runs"].items():
            for (session_suite, scale, engine), summary in run["sessions"].items():
                if engine not in PLAN_ENGINES:
                    continue
                missing += summary.get("plans_missing") or 0
                if not summary.get("plan_trials"):
                    continue
                sessions[(entry["id"], session_suite, scale, engine)] = summary
                for item in summary["plan_trials"]:
                    per_cell.setdefault((session_suite, scale, item["query"], engine), []).append({**item, "round": entry["id"]})
    stage_cache = {}

    def execution(round_id, suite, scale, engine, query):
        session_key = (round_id, suite, scale, engine)
        summary = sessions[session_key]
        if session_key not in stage_cache:
            stage_cache[session_key] = read_spark_stages(summary["events_dir"])[0] if summary.get("events_dir") else []
        if not stage_cache[session_key]:
            return {}
        return action_stages([t for t in summary["plan_trials"] if t["query"] == query], stage_cache[session_key])

    def summary_of(values):
        return {"min": min(values), "median": statistics.median(values), "max": max(values)} if values else None

    cells = []
    order = lambda k: (SUITES.index(k[0]) if k[0] in SUITES else 99, scale_sort_key(k[1]), k[2], ENGINE_ORDER.index(k[3]) if k[3] in ENGINE_ORDER else 99)
    for key in sorted(per_cell, key=order):
        suite, scale, query, engine = key
        trials = per_cell[key]
        if len({t["signature"] for t in trials}) < 2:
            continue
        groups = {}
        for t in sorted(trials, key=lambda t: (t["round"], t["phase"] != "warmup", t["trial"] or 0)):
            groups.setdefault(t["signature"], []).append(t)
        found = {}
        for (round_id, s_suite, s_scale, s_engine), summary in sessions.items():
            if (s_suite, s_scale, s_engine) == (suite, scale, engine):
                for signature in groups:
                    if signature in summary["plan_catalog"] and signature not in found:
                        found[signature] = (summary["plan_catalog"][signature], summary["plan_texts"][signature])
        plans = []
        for signature, group in groups.items():
            measured = [t for t in group if t["phase"] == "measured" and t["seconds"] is not None]
            warmup = [t for t in group if t["phase"] == "warmup" and t["seconds"] is not None]
            facts = [f for f in (execution(t["round"], suite, scale, engine, query).get((query, t["phase"], t["trial"])) for t in measured) if f]
            plans.append({
                "signature": signature, "n_measured": len(measured), "n_warmup": len(warmup),
                "measured_per_round": {str(r): sum(1 for t in measured if t["round"] == r) for r in sorted({t["round"] for t in measured})},
                "warmup_per_round": {str(r): sum(1 for t in warmup if t["round"] == r) for r in sorted({t["round"] for t in warmup})},
                "measured_seconds": summary_of([t["seconds"] for t in measured]),
                "warmup_seconds": [t["seconds"] for t in warmup],
                "execution": ({"trials_with_event_log": len(facts), "stages": summary_of([f["stages"] for f in facts]),
                               "tasks": summary_of([f["tasks"] for f in facts]),
                               "shuffle_write_bytes": summary_of([f["shuffle_write_bytes"] for f in facts])} if facts else None),
                "features": found[signature][0], "changes_vs_reference": None,
                "trials": [{"round": t["round"], "phase": t["phase"], "trial": t["trial"], "seconds": t["seconds"]} for t in group]})
        plans.sort(key=lambda p: ((p["measured_seconds"] or {}).get("median", math.inf), -p["n_measured"], p["signature"]))
        reference = plans[0]
        for plan in plans[1:]:
            plan["changes_vs_reference"] = plan_changes(reference["features"], plan["features"], found[reference["signature"]][1], found[plan["signature"]][1])
        timed = [p for p in plans if p["measured_seconds"]]
        overlap = None
        if len(timed) >= 2:
            overlap = any(max(a["measured_seconds"]["min"], b["measured_seconds"]["min"]) <= min(a["measured_seconds"]["max"], b["measured_seconds"]["max"])
                          for i, a in enumerate(timed) for b in timed[i + 1:])
        initial = {t["initial_signature"] for t in trials if t["initial_signature"]}
        cells.append({
            "suite": suite, "scale": scale, "query": query, "engine": engine, "rounds": sorted({t["round"] for t in trials}),
            "n_trials": len(trials), "n_measured": sum(p["n_measured"] for p in plans), "n_warmup": sum(p["n_warmup"] for p in plans),
            "n_plans": len(plans), "reference_signature": reference["signature"],
            "initial_plan_identical": (len(initial) == 1) if initial else None,
            "measured_time_ranges_overlap": overlap,
            "median_seconds_slowest_over_fastest": (timed[-1]["measured_seconds"]["median"] / timed[0]["measured_seconds"]["median"]
                                                    if len(timed) >= 2 and timed[0]["measured_seconds"]["median"] else None),
            "plans": plans})
    return {"engines": list(PLAN_ENGINES), "note": PLAN_NOTE, "sessions_checked": len(sessions), "cells_checked": len(per_cell),
            "trials_with_plan": sum(len(v) for v in per_cell.values()), "plan_files_missing": missing, "cells_variable": len(cells), "cells": cells}


def unstable_coverage_section(data):
    """Raw per-round outcomes of the cells A1 excludes from every contrast (unstable coverage): supplementary, never an estimate."""
    cells = []
    for (suite, scale, query, engine), info in sorted(data.unstable.items(), key=lambda kv: (kv[0][0], scale_sort_key(kv[0][1]), kv[0][2], ENGINE_ORDER.index(kv[0][3]))):
        cells.append({"suite": suite, "scale": scale, "query": query, "engine": engine, "rounds": sorted(info["outcomes"]),
                      "outcome_per_round": [info["outcomes"][r] for r in sorted(info["outcomes"])],
                      "seconds_per_round": [info["per_round_seconds"][r] for r in sorted(info["outcomes"])],
                      "coverage_rounds": info["coverage_rounds"], "label": UNSTABLE_NOTE})
    return {"note": UNSTABLE_NOTE + ". Amendment A1: a coverage outcome observed in some rounds but not others for the same (engine, suite, scale, query) "
                    "is excluded from every contrast, per-query and suite-level, in all rounds; the raw per-round values are listed here for transparency only.",
            "cells": cells}


def legacy_totals(data):
    """Sum of per-query medians (A100-study comparability); separate from every geometric-mean quantity."""
    totals, ratios = {}, {}
    for suite in data.suites:
        totals[suite], ratios[suite] = {}, {}
        for scale in data.scales[suite]:
            block = data.block_rounds(suite, scale)
            totals[suite][scale], ratios[suite][scale] = {}, {}
            per_engine = {}
            for engine in data.present_engines():
                by_round, missing = [], []
                for r in block:
                    values = [data.times(suite, scale, q, engine).get(r) for q in data.queries[suite]]
                    by_round.append(math.fsum(values) if all(v is not None for v in values) else None)
                    missing += [q for q, v in zip(data.queries[suite], values) if v is None]
                complete = bool(block) and all(t is not None for t in by_round)
                mean = math.fsum(by_round) / len(by_round) if complete else None
                per_engine[engine] = by_round if complete else None
                totals[suite][scale][engine] = {
                    "complete": complete, "per_round": by_round, "rounds": list(block), "mean_total": mean,
                    "min_total": min(by_round) if complete else None, "max_total": max(by_round) if complete else None,
                    "queries_missing": sorted(set(missing)),
                }
            for name, spec in ALL_CONTRASTS.items():
                num = per_engine.get(spec["numerator"])
                if spec["denominator"].startswith("best_gpu"):
                    options = [per_engine.get(e) for e in GPU_ENGINES]
                    options = [o for o in options if o is not None]
                    if spec["denominator"] == "best_gpu_both" and len(options) < 2:
                        options = []
                    den = [min(vals) for vals in zip(*options)] if options else None
                else:
                    den = per_engine.get(spec["denominator"])
                if num is None or den is None:
                    ratios[suite][scale][name] = {"ratio_of_mean_totals": None, "per_round": None}
                else:
                    ratios[suite][scale][name] = {"ratio_of_mean_totals": (math.fsum(num) / len(num)) / (math.fsum(den) / len(den)),
                                                  "per_round": [a / b for a, b in zip(num, den)]}
    return totals, ratios


# ------------------------------------------------------------------ variability, order, crossover

def variability(data):
    """Within-session (trial) and between-round CV of every cell, summarized per engine, suite and scale."""
    cells = []
    for (suite, scale, query, engine), times in sorted(data.valid.items()):
        rounds = sorted(times)
        within = [cv(data.cells[(r, suite, scale, query, engine)]["trials"]) for r in rounds]
        cells.append({"engine": engine, "suite": suite, "scale": scale, "query": query, "rounds": rounds,
                      "cv_within": within, "cv_between": cv([times[r] for r in rounds])})

    def summarize(selected):
        return {"within_session": distribution([v for c in selected for v in c["cv_within"]]),
                "between_rounds": distribution([c["cv_between"] for c in selected])}

    engines = data.present_engines()
    out = {"cells": cells, "by_engine": {}, "by_engine_suite": {}, "by_engine_scale": {}}
    for engine in engines:
        mine = [c for c in cells if c["engine"] == engine]
        out["by_engine"][engine] = summarize(mine)
        out["by_engine_suite"][engine] = {s: summarize([c for c in mine if c["suite"] == s]) for s in data.suites}
        out["by_engine_scale"][engine] = {}
        for suite in data.suites:
            out["by_engine_scale"][engine][suite] = {
                scale: summarize([c for c in mine if c["suite"] == suite and c["scale"] == scale])
                for scale in data.scales[suite]}
    return out


def order_design_balance(data):
    """Check the observed engine order against the Williams property of section 5 (positions and adjacent pairs)."""
    sequences = {}
    for (rnd, suite, scale), order in data.blocks.items():
        sequences.setdefault(rnd, set()).add(tuple(order))
    consistent = all(len(v) == 1 for v in sequences.values())
    result = {"rounds_with_order": sorted(sequences), "orders_consistent_within_round": consistent,
              "observed": {str(r): list(next(iter(v))) for r, v in sorted(sequences.items()) if len(v) == 1}}
    if not consistent or len(sequences) < 2:
        result.update(positions_balanced=None, pairs_balanced=None)
        return result
    orders = [next(iter(v)) for _, v in sorted(sequences.items())]
    engines = sorted(set().union(*orders))
    n = len(engines)
    position_counts = {e: [sum(1 for o in orders if len(o) > p and o[p] == e) for p in range(n)] for e in engines}
    pair_counts = {}
    for o in orders:
        for a, b in zip(o, o[1:]):
            pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1
    result["positions_balanced"] = len(orders) == n and all(c == [1] * n for c in position_counts.values())
    result["pairs_balanced"] = len(orders) == n and all(pair_counts.get((a, b), 0) == 1 for a in engines for b in engines if a != b)
    return result


def order_effects(data):
    """Mean log-time deviation by position (1..4) and by predecessor engine; descriptive only (section 9).

    Deviation = ln t_round - mean over rounds of ln t, per (suite, scale, query, engine) cell that has a valid
    median in every round of its block. Interval: t-interval (df = rounds - 1) over the per-round means.
    """
    rows = []
    for (suite, scale, query, engine), times in data.valid.items():
        block = data.block_rounds(suite, scale)
        if len(times) != len(block) or len(times) < 2:
            continue
        logs = {r: math.log(t) for r, t in times.items()}
        mean = math.fsum(logs.values()) / len(logs)
        for r in block:
            order = data.blocks.get((r, suite, scale))
            if not order or engine not in order:
                continue
            position = order.index(engine) + 1
            rows.append({"round": r, "suite": suite, "scale": scale, "engine": engine, "position": position,
                         "predecessor": order[position - 2] if position > 1 else POSITION_NONE, "deviation": logs[r] - mean})

    def summarize(selected):
        if not selected:
            return {"n_cells": 0, "n_rounds": 0, "mean_pct": None, "ci_low_pct": None, "ci_high_pct": None, "sd_log": None}
        by_round = {}
        for row in selected:
            by_round.setdefault(row["round"], []).append(row["deviation"])
        round_means = [math.fsum(v) / len(v) for _, v in sorted(by_round.items())]
        mean, sd = mean_sd(round_means)
        record = {"n_cells": len(selected), "n_rounds": len(round_means), "mean_pct": math.expm1(mean) * 100.0,
                  "ci_low_pct": None, "ci_high_pct": None, "sd_log": mean_sd([r["deviation"] for r in selected])[1]}
        if sd is not None and len(round_means) >= 2:
            half = t_critical(len(round_means) - 1) * sd / math.sqrt(len(round_means))
            record.update(ci_low_pct=math.expm1(mean - half) * 100.0, ci_high_pct=math.expm1(mean + half) * 100.0)
        return record

    engines = data.present_engines()
    positions = sorted({r["position"] for r in rows})
    predecessors = [p for p in (POSITION_NONE, *engines) if any(r["predecessor"] == p for r in rows)]
    out = {"n_observations": len(rows), "design_balance": order_design_balance(data), "position": {}, "predecessor": {},
           "predecessor_by_engine": {}, "position_by_engine": {}}
    for p in positions:
        sel = [r for r in rows if r["position"] == p]
        out["position"][str(p)] = summarize(sel)
        out["position_by_engine"][str(p)] = {e: summarize([r for r in sel if r["engine"] == e]) for e in engines
                                             if any(r["engine"] == e for r in sel)}
    for pred in predecessors:
        sel = [r for r in rows if r["predecessor"] == pred]
        out["predecessor"][pred] = summarize(sel)
        out["predecessor_by_engine"][pred] = {e: summarize([r for r in sel if r["engine"] == e]) for e in engines
                                              if any(r["engine"] == e for r in sel)}
    return out


def verdict(record):
    if not record or record.get("ci_low") is None:
        return "n/a"
    if record["ci_low"] > 1.0:
        return "gpu_faster"
    if record["ci_high"] < 1.0:
        return "cpu_faster"
    return "inconclusive"


def crossover_for(data, per_scale):
    """RQ3: scales (ascending) where the C4 interval is entirely above 1 (GPU faster) or below 1 (CPU faster)."""
    def scan(records_by_scale, scales):
        verdicts = {s: verdict(records_by_scale.get(s)) for s in scales}
        first = next((s for s in scales if verdicts[s] == "gpu_faster"), None)
        stable = None
        for i, s in enumerate(scales):
            if all(verdicts[t] == "gpu_faster" for t in scales[i:]):
                stable = s
                break
        point_first = next((s for s in scales if (records_by_scale.get(s) or {}).get("estimate") is not None
                            and records_by_scale[s]["estimate"] > 1.0), None)
        previous = scales[scales.index(first) - 1] if first and scales.index(first) > 0 else None
        return {"verdicts": verdicts, "estimates": {s: (records_by_scale.get(s) or {}).get("estimate") for s in scales},
                "crossover_found": first is not None, "first_gpu_faster_scale": first,
                "stable_from_scale": stable, "bracket": [previous, first] if first else None,
                "first_point_estimate_above_1": point_first}
    out = {}
    for suite in data.suites:
        scales = data.scales[suite]
        record = scan(per_scale[suite], scales)
        record["queries"] = {q: scan({s: per_scale[suite][s]["q"].get(q) for s in scales}, scales) for q in data.queries[suite]}
        out[suite] = record
    return out


# ------------------------------------------------------------------ secondary metrics

GPU_MEMORY_MEASURES = {
    "polars_gpu": ("process_high_water_mark",
                   "nvidia-smi device memory of the worker process, sampled during the timed action. cudf-polars allocates through an RMM async pool that keeps "
                   "freed blocks (release threshold 90% of the device), so the value is the pool's high-water mark in that session so far, not the working set of the "
                   "query: it never falls within a session (see high_water_check) and an upper bound on what the query needs"),
    "spark_gpu": ("task_reported_max_device_memory",
                  "the largest device allocation a single task reported (RAPIDS task accumulator gpuMaxDeviceMemoryBytes, Spark event log) among the tasks launched inside "
                  "the action; not a simultaneous total over concurrent tasks. The nvidia-smi samples (max_bytes, ...) are the RAPIDS pool reservation made at plugin "
                  "start, not memory in use"),
}


def gpu_memory(data):
    cells, summary, capacity = {}, {}, None
    for (rnd, suite, scale, engine), session in data.sessions.items():
        inventory = session.get("gpu_inventory") or {}
        total = inventory.get("memory_total_bytes")
        if good_number(total):
            capacity = max(capacity or 0, int(total))

    def cell_stats(per_round):
        present = [v for v in per_round if v is not None]
        return {"recorded": bool(present), "per_round_max_bytes": per_round,
                "max_bytes": max(present) if present else None, "median_bytes": statistics.median(present) if present else None}

    def summarize(values):
        return {"n_queries": len(values), "max_bytes": max(values), "min_bytes": min(values), "median_bytes": statistics.median(values),
                "fraction_of_capacity_max": max(values) / capacity if capacity else None}

    for suite in data.suites:
        cells[suite], summary[suite] = {}, {}
        for scale in data.scales[suite]:
            cells[suite][scale], summary[suite][scale] = {}, {}
            block = data.block_rounds(suite, scale)
            for query in data.queries[suite]:
                cells[suite][scale][query] = {}
                for engine in GPU_ENGINES:
                    per_round, per_round_task = [], []
                    for r in block:
                        cell = data.cells.get((r, suite, scale, query, engine))
                        values = cell["peak_gpu_bytes"] if cell and cell["status"] == "ok" else []
                        per_round.append(max(values) if values else None)
                        tasks = cell.get("task_device_bytes") if cell and cell["status"] == "ok" else []
                        per_round_task.append(max(tasks) if tasks else None)
                    if not any(k == (suite, scale, query, engine) for k in data.valid):
                        continue
                    record = {**cell_stats(per_round), "rounds": list(block)}
                    if engine == "spark_gpu":
                        record["task_reported"] = {**cell_stats(per_round_task), "rounds": list(block)}
                    cells[suite][scale][query][engine] = record
            for engine in GPU_ENGINES:
                peaks = [cells[suite][scale][q][engine]["max_bytes"] for q in data.queries[suite]
                         if engine in cells[suite][scale][q] and cells[suite][scale][q][engine]["max_bytes"] is not None]
                if peaks:
                    summary[suite][scale][engine] = summarize(peaks)
                    if engine == "spark_gpu":
                        tasks = [cells[suite][scale][q][engine]["task_reported"]["max_bytes"] for q in data.queries[suite]
                                 if engine in cells[suite][scale][q] and cells[suite][scale][q][engine]["task_reported"]["max_bytes"] is not None]
                        summary[suite][scale][engine]["task_reported"] = summarize(tasks) if tasks else None
    # which quantity each engine's memory number is, from what the event logs / samples actually contain
    measure, high_water, flat_check = {}, {}, {}
    spark_cells = [c for by_scale in cells.values() for by_query in by_scale.values() for by_engine in by_query.values() for e, c in by_engine.items() if e == "spark_gpu"]
    task_recorded = sum(1 for c in spark_cells if c["task_reported"]["recorded"])
    measure["spark_gpu"] = GPU_MEMORY_MEASURES["spark_gpu"][0] if task_recorded else "pool_reservation_only"
    measure["polars_gpu"] = GPU_MEMORY_MEASURES["polars_gpu"][0]
    measure_note = {"polars_gpu": GPU_MEMORY_MEASURES["polars_gpu"][1],
                    "spark_gpu": GPU_MEMORY_MEASURES["spark_gpu"][1] if task_recorded else
                    "no task-level device-memory accumulator (gpuMaxDeviceMemoryBytes) was found in the Spark event logs, so only the nvidia-smi samples exist; they show the "
                    "RAPIDS pool reservation made at plugin start, not memory in use, and Spark GPU is left out of the memory figure"}
    for engine in GPU_ENGINES:
        keyed = [((r, su, sc), s["gpu_peak_series"]) for (r, su, sc, e), s in sorted(data.sessions.items()) if e == engine and s.get("gpu_peak_series")]
        sessions = [(k, x) for k, x in keyed if len(x) >= 3]
        falling = [(k, x) for k, x in sessions if not nondecreasing(x)]
        high_water[engine] = {"sessions": len(sessions), "nondecreasing": len(sessions) - len(falling),
                              "largest_fall_bytes": max((largest_fall(x) for _, x in sessions), default=0),
                              "falling_sessions": [{"round": r, "suite": su, "scale": sc, "largest_fall_bytes": largest_fall(x), "max_bytes": max(x)} for (r, su, sc), x in falling]}
        flat_check[engine] = {"sessions": len(sessions), "constant": sum(1 for _, x in sessions if constant_series(x)),
                              "first_sample_median_bytes": statistics.median(x[0] for _, x in sessions) if sessions else None,
                              "later_minimum_median_bytes": statistics.median(min(x[1:]) for _, x in sessions) if sessions else None}
    spark_flat = flat_check["spark_gpu"]
    if task_recorded and spark_flat["sessions"] and spark_flat["constant"] < spark_flat["sessions"]:
        measure_note["spark_gpu"] = (GPU_MEMORY_MEASURES["spark_gpu"][1].split(" The nvidia-smi samples")[0]
                                     + f" The nvidia-smi samples (max_bytes, ...) are the worker process tree's device memory when the action was sampled; they are constant within a "
                                       f"session in only {spark_flat['constant']} of {spark_flat['sessions']} sessions, so on this host they are not a fixed pool reservation (the first "
                                       f"sampled action of a session reads {spark_flat['first_sample_median_bytes'] / 2 ** 30:.1f} GiB and the minimum over the later actions "
                                       f"{spark_flat['later_minimum_median_bytes'] / 2 ** 30:.1f} GiB, medians over sessions) and they are not task-level usage")
    check = high_water["polars_gpu"]
    if check["sessions"] and check["falling_sessions"]:
        top = max(f["max_bytes"] for f in check["falling_sessions"])
        measure_note["polars_gpu"] = (GPU_MEMORY_MEASURES["polars_gpu"][1].split(": it never falls")[0]
                                      + f": it did not fall in {check['nondecreasing']} of {check['sessions']} sessions (see high_water_check) but fell in the other "
                                        f"{len(check['falling_sessions'])} (by up to {max(f['largest_fall_bytes'] for f in check['falling_sessions']) / 2 ** 30:.1f} GiB; their samples "
                                        f"reach {100 * top / capacity if capacity else float('nan'):.0f}% of the device), so there it is the pool size at the time of the action, and an upper bound on what the "
                                        "query needs only while the pool keeps its blocks")
    return {"capacity_bytes": capacity, "cells": cells, "summary": summary, "measure": measure, "measure_note": measure_note,
            "task_reported_cells": {"spark_gpu": {"with_value": task_recorded, "cells": len(spark_cells)}}, "high_water_check": high_water,
            "constant_check": flat_check,
            "note": "max_bytes / median_bytes / min_bytes are the nvidia-smi samples of the worker process tree during the timed action (worker peak_gpu_memory_bytes); "
                    "maximum over the 3 measured trials, then over rounds; a sampled peak can miss short transients. What the sample means differs by engine: "
                    "see measure / measure_note, and the task_reported blocks for Spark GPU"}


def mean_or_none(values):
    values = [v for v in values if v is not None]
    return math.fsum(values) / len(values) if values else None


def process_times(data):
    out = {}
    for suite in data.suites:
        out[suite] = {}
        for scale in data.scales[suite]:
            out[suite][scale] = {}
            for engine in data.present_engines():
                sessions = [data.sessions.get((r, suite, scale, engine)) for r in data.block_rounds(suite, scale)]
                sessions = [s for s in sessions if s]
                if not sessions:
                    continue
                out[suite][scale][engine] = {
                    "n_sessions": len(sessions),
                    "process_wall_seconds": mean_or_none([s["process_wall_seconds"] for s in sessions]),
                    "launch_to_engine_ready_seconds": mean_or_none([s["launch_to_engine_ready_seconds"] for s in sessions]),
                    "session_and_table_setup_seconds": mean_or_none([s["session_and_table_setup_seconds"] for s in sessions]),
                    "warmup_seconds": mean_or_none([s["warmup_seconds"] for s in sessions]),
                    "measured_seconds": mean_or_none([s["measured_seconds"] for s in sessions]),
                    "per_round_process_wall_seconds": [s["process_wall_seconds"] for s in sessions]}
    return out


def cleanup_costs(data):
    """verified_gc cost per session (complex suite): seconds spent in cleanup barriers, outside the query timers."""
    out = {}
    for suite in data.suites:
        for scale in data.scales[suite]:
            for engine in data.present_engines():
                per_round = []
                for r in data.block_rounds(suite, scale):
                    session = data.sessions.get((r, suite, scale, engine))
                    records = (session or {}).get("cleanup_records") or []
                    seconds = [x["seconds"] for x in records if good_number(x.get("seconds"), positive=False)]
                    if seconds:
                        wall = session.get("process_wall_seconds")
                        per_round.append({"n": len(seconds), "total_seconds": math.fsum(seconds),
                                          "mean_seconds": math.fsum(seconds) / len(seconds), "max_seconds": max(seconds),
                                          "share_of_process_wall": math.fsum(seconds) / wall if good_number(wall) else None})
                if per_round:
                    out.setdefault(suite, {}).setdefault(scale, {})[engine] = {
                        "n_sessions": len(per_round), "cleanups_per_session": per_round[0]["n"],
                        "mean_seconds_per_cleanup": mean_or_none([p["mean_seconds"] for p in per_round]),
                        "max_seconds": max(p["max_seconds"] for p in per_round),
                        "total_seconds_per_session": mean_or_none([p["total_seconds"] for p in per_round]),
                        "share_of_process_wall": mean_or_none([p["share_of_process_wall"] for p in per_round])}
    return out


def read_gpu_power(path, gpu_index=0):
    """(epoch seconds treating nvidia-smi's local stamp as UTC, watts) for one GPU, parsed from a gpu.csv stream."""
    times, watts = [], []
    with Path(path).open(newline="", errors="replace") as handle:
        header = [c.strip().lower() for c in handle.readline().split(",")]
        try:
            ti = next(i for i, c in enumerate(header) if c.startswith("timestamp"))
            pi = next(i for i, c in enumerate(header) if c.startswith("power.draw"))
        except StopIteration:
            return times, watts
        gi = next((i for i, c in enumerate(header) if c == "index"), None)
        for line in handle:
            parts = line.split(",")
            if len(parts) <= max(ti, pi) or (gi is not None and parts[gi].strip() != str(gpu_index)):
                continue
            try:
                stamp = datetime.strptime(parts[ti].strip(), "%Y/%m/%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
                power = float(parts[pi].strip().split()[0])
            except (ValueError, IndexError):
                continue
            if math.isfinite(power):
                times.append(stamp.timestamp())
                watts.append(power)
    return times, watts


def attach_energy(run, gpu_index=0):
    """Add mean power and the energy proxy (mean power.draw x process wall time) to each session of a run.

    nvidia-smi stamps are host-local; the offset to UTC is estimated from the last sample versus the end of the
    last session (the monitor is stopped right after it) and rounded to 15 minutes.
    """
    path = Path(run["dir"]) / "gpu.csv"
    info = {"gpu_csv": False, "tz_offset_seconds": None}
    run["energy_info"] = info
    if not path.is_file():
        return
    import bisect
    times, watts = read_gpu_power(path, gpu_index)
    info["gpu_csv"], info["n_samples"] = True, len(times)
    windows = {}
    for key, session in run["sessions"].items():
        start, wall = parse_utc(session.get("start_utc")), session.get("process_wall_seconds")
        if start is not None and good_number(wall):
            windows[key] = (start.timestamp(), start.timestamp() + wall, wall)
    if not times or not windows:
        return
    last_end = max(end for _, end, _ in windows.values())
    offset = round((times[-1] - last_end) / 900.0) * 900
    info["tz_offset_seconds"] = offset
    for key, (begin, end, wall) in windows.items():
        lo, hi = bisect.bisect_left(times, begin + offset), bisect.bisect_right(times, end + offset)
        if hi > lo:
            mean = math.fsum(watts[lo:hi]) / (hi - lo)
            run["sessions"][key].update(mean_power_w=mean, energy_wh=mean * wall / 3600.0, power_samples=hi - lo)


# ------------------------------------------------------------------ descriptive GPU telemetry (nvidia-smi samples inside measured actions)

TELEMETRY_LABEL = "descriptive telemetry; not a pre-registered contrast; no causal claim"
TELEMETRY_IDLE_PCT = 10.0         # a sample with utilization.gpu below this counts as GPU-idle
TELEMETRY_MIN_ACTION_SECONDS = 1.0    # shorter actions hold too few samples (nvidia-smi is sampled every ~200 ms) and are left out of every statistic here


def smi_number(text):
    """'85 %', '235.12 W', '1410 MHz', '512 MiB' -> float; None for N/A or anything unparsable."""
    try:
        value = float(str(text).strip().split()[0])
    except (ValueError, IndexError):
        return None
    return value if math.isfinite(value) else None


def read_gpu_samples(path, gpu_index=0):
    """{times (epoch s, local stamp read as UTC), util, mem_util, mem_mib, power, sm_clock} for one GPU from a gpu.csv; columns missing from the file stay all-None."""
    out = {k: [] for k in ("times", "util", "mem_util", "mem_mib", "power", "sm_clock")}
    wanted = {"util": "utilization.gpu", "mem_util": "utilization.memory", "mem_mib": "memory.used", "power": "power.draw", "sm_clock": "clocks.current.sm"}
    with Path(path).open(newline="", errors="replace") as handle:
        header = [c.strip().lower() for c in handle.readline().split(",")]
        try:
            ti = next(i for i, c in enumerate(header) if c.startswith("timestamp"))
        except StopIteration:
            return out
        gi = next((i for i, c in enumerate(header) if c == "index"), None)
        columns = {key: next((i for i, c in enumerate(header) if c.startswith(prefix)), None) for key, prefix in wanted.items()}
        for line in handle:
            parts = line.split(",")
            if len(parts) <= ti or (gi is not None and (len(parts) <= gi or parts[gi].strip() != str(gpu_index))):
                continue
            try:
                stamp = datetime.strptime(parts[ti].strip(), "%Y/%m/%d %H:%M:%S.%f").replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
            out["times"].append(stamp)
            for key, index in columns.items():
                out[key].append(smi_number(parts[index]) if index is not None and index < len(parts) else None)
    return out


def attach_telemetry(run, gpu_index=0):
    """Per GPU-engine session: the gpu.csv samples inside each measured action of at least TELEMETRY_MIN_ACTION_SECONDS.

    The offset between the nvidia-smi stamps and UTC is the one attach_energy estimated (0 when it could not)."""
    import bisect
    path = Path(run["dir"]) / "gpu.csv"
    info = {"gpu_csv": False, "sample_interval_ms": None, "n_samples": 0}
    run["telemetry_info"] = info
    if not path.is_file():
        return
    samples = read_gpu_samples(path, gpu_index)
    times = samples["times"]
    if not times:
        return
    offset = (run.get("energy_info") or {}).get("tz_offset_seconds") or 0
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    info.update(gpu_csv=True, n_samples=len(times), sample_interval_ms=1000.0 * statistics.median(gaps) if gaps else None, tz_offset_seconds=offset)
    for key, summary in run["sessions"].items():
        if key[2] not in GPU_ENGINES:
            continue
        actions, short, short_seconds, empty = [], 0, 0.0, 0
        for action in summary.get("measured_actions") or []:
            duration = action["end"] - action["start"]
            if duration < TELEMETRY_MIN_ACTION_SECONDS:
                short, short_seconds = short + 1, short_seconds + max(duration, 0.0)
                continue
            lo, hi = bisect.bisect_left(times, action["start"] + offset), bisect.bisect_right(times, action["end"] + offset)
            if hi <= lo:
                empty += 1
                continue
            actions.append({"query": action["query"], "trial": action["trial"], "seconds": duration, "n": hi - lo,
                            **{k: samples[k][lo:hi] for k in ("util", "mem_util", "power", "sm_clock")}})
        summary["telemetry"] = {"actions": actions, "short_actions": short, "short_seconds": short_seconds, "actions_without_samples": empty}


def gpu_limits(environment):
    """(current power limit in W, maximum SM clock in MHz) of the first GPU from the environment's nvidia-smi -q output; None when absent."""
    text = inventory_output(environment, "nvidia-smi") if environment else ""
    power = re.search(r"Current Power Limit\s*:\s*([\d.]+) W", text)
    clock = re.search(r"Max Clocks\s*\n\s*Graphics\s*:\s*\d+ MHz\s*\n\s*SM\s*:\s*(\d+) MHz", text)
    return (float(power[1]) if power else None), (float(clock[1]) if clock else None)


def telemetry_stats(actions, power_limit, max_clock):
    util = [u for a in actions for u in a["util"] if u is not None]
    power = [p for a in actions for p in a["power"] if p is not None]
    clock = [c for a in actions for c in a["sm_clock"] if c is not None]
    mem = [m for a in actions for m in a["mem_util"] if m is not None]
    power_median = statistics.median(power) if power else None
    clock_median = statistics.median(clock) if clock else None
    return {"n_actions": len(actions), "action_seconds": math.fsum(a["seconds"] for a in actions), "n_samples": len(util),
            "util_median_pct": statistics.median(util) if util else None, "util_p90_pct": quantile(util, 0.9),
            "idle_fraction": (sum(1 for u in util if u < TELEMETRY_IDLE_PCT) / len(util)) if util else None,
            "power_median_w": power_median, "power_fraction_of_limit": power_median / power_limit if power_median is not None and power_limit else None,
            "sm_clock_median_mhz": clock_median, "sm_clock_fraction_of_max": clock_median / max_clock if clock_median is not None and max_clock else None,
            "mem_util_median_pct": statistics.median(mem) if mem else None}


def gpu_telemetry_section(campaign, data):
    """secondary.gpu_telemetry: per (suite, scale, GPU engine), statistics of the nvidia-smi samples that fall inside measured actions of at least 1 s.

    Descriptive only: no contrast, no test, no causal reading. Utilization is nvidia-smi's utilization.gpu (share of the sample period in which at
    least one kernel was executing), not SM occupancy."""
    infos = [r.get("telemetry_info") or {} for e in campaign["rounds"] if e["included"] for r in e["runs"].values()]
    with_csv = [i for i in infos if i.get("gpu_csv")]
    power_limit, max_clock = gpu_limits(first_environment(campaign))
    intervals = [i["sample_interval_ms"] for i in with_csv if i.get("sample_interval_ms")]
    interval = statistics.median(intervals) if intervals else None
    meta = {"label": TELEMETRY_LABEL, "available": bool(with_csv), "reason": None if with_csv else "no gpu.csv found (or --no-energy)",
            "definition": ("For each GPU engine, suite and scale: the nvidia-smi samples (utilization.gpu, utilization.memory, power.draw, clocks.current.sm) that fall inside the "
                           "measured actions of the accepted rounds, pooled over queries, trials and rounds. idle_fraction is the share of samples with utilization.gpu below "
                           f"{TELEMETRY_IDLE_PCT:g}% (equal to the share of action wall time at uniform sampling). utilization.gpu is nvidia-smi's share of the sample period "
                           "in which at least one kernel was executing, not SM occupancy. Descriptive; not a pre-registered contrast; no causal claim."),
            "idle_threshold_pct": TELEMETRY_IDLE_PCT, "min_action_seconds": TELEMETRY_MIN_ACTION_SECONDS,
            "sample_interval_ms": interval, "gpu_csv_runs": len(with_csv),
            "power_limit_w": power_limit, "power_limit_source": "nvidia-smi -q, Current Power Limit of the first GPU" if power_limit else None,
            "max_sm_clock_mhz": max_clock,
            "resolution_note": (f"nvidia-smi is sampled about every {interval:.0f} ms, so an action of 1 s holds about {1000.0 / interval:.0f} samples and the first and last sample of an "
                                "action are partly outside it; actions shorter than 1 s (most SF1 actions) are left out of every statistic and counted in n_actions_excluded_short"
                                if interval else "no sampling interval (no gpu.csv)")}
    cells = {}
    if with_csv:
        for suite in data.suites:
            cells[suite] = {}
            for scale in data.scales[suite]:
                cells[suite][scale] = {}
                for engine in GPU_ENGINES:
                    sessions = [(r, data.sessions.get((r, suite, scale, engine))) for r in data.block_rounds(suite, scale)]
                    sessions = [(r, s) for r, s in sessions if s and s.get("telemetry") is not None]
                    if not sessions:
                        continue
                    actions = [(r, a) for r, s in sessions for a in s["telemetry"]["actions"]]
                    record = telemetry_stats([a for _, a in actions], power_limit, max_clock)
                    by_round = [telemetry_stats([a for rr, a in actions if rr == r], power_limit, max_clock)["idle_fraction"] for r, _ in sessions]
                    record.update(
                        rounds=[r for r, _ in sessions], idle_fraction_by_round=by_round, n_sessions=len(sessions),
                        n_actions_excluded_short=sum(s["telemetry"]["short_actions"] for _, s in sessions),
                        seconds_excluded_short=math.fsum(s["telemetry"]["short_seconds"] for _, s in sessions),
                        n_actions_without_samples=sum(s["telemetry"]["actions_without_samples"] for _, s in sessions),
                        q={q: telemetry_stats([a for _, a in actions if a["query"] == q], power_limit, max_clock)
                           for q in data.queries[suite] if any(a["query"] == q for _, a in actions)})
                    for q_record in record["q"].values():
                        for key in ("n_samples", "action_seconds", "power_median_w", "power_fraction_of_limit", "sm_clock_median_mhz", "sm_clock_fraction_of_max", "mem_util_median_pct"):
                            q_record.pop(key, None)
                    cells[suite][scale][engine] = record
    return {"meta": meta, "cells": cells}


def energy_proxy(data):
    out = {}
    for suite in data.suites:
        out[suite] = {}
        for scale in data.scales[suite]:
            out[suite][scale] = {}
            for engine in data.present_engines():
                sessions = [data.sessions.get((r, suite, scale, engine)) for r in data.block_rounds(suite, scale)]
                sessions = [s for s in sessions if s and s.get("mean_power_w") is not None]
                if sessions:
                    out[suite][scale][engine] = {
                        "n_sessions": len(sessions), "mean_power_w": mean_or_none([s["mean_power_w"] for s in sessions]),
                        "energy_wh": mean_or_none([s["energy_wh"] for s in sessions]),
                        "per_round_energy_wh": [s["energy_wh"] for s in sessions],
                        "process_wall_seconds": mean_or_none([s["process_wall_seconds"] for s in sessions])}
    return {"by_cell": out, "definition": "GPU board energy proxy = mean nvidia-smi power.draw over the session window x "
                                         "process wall time; CPU and host power are not included, and CPU-only engines show "
                                         "the idle GPU draw"}


# ------------------------------------------------------------------ side study and A100 comparison

def version_tag(label):
    return re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")


def version_key(label):
    match = re.search(r"(\d+(?:\.\d+)*)$", label)
    return tuple(int(x) for x in match.group(1).split(".")) if match else (0,)


def load_side_study(root, allow_ungated=False, energy=False):
    """Polars version bridge (RQ5): rounds of {polars-<old>, polars-<new>}/<suite>/ -> a Data-compatible campaign."""
    base = Path(root) / "side-study"
    info = {"available": False, "rounds": [], "versions": [], "warnings": []}
    if not base.is_dir():
        return None, info
    rounds = discover_rounds(base, allow_ungated=True)  # side-study gates are optional; verdicts, when present, still exclude
    versions = sorted({p.name for r in rounds for p in r["dir"].iterdir() if p.is_dir() and p.name.startswith("polars-")}, key=version_key)
    if len(versions) != 2:
        info["warnings"].append(f"expected two polars-<version> directories, found {versions}")
        return None, info
    info["versions"] = versions
    campaign = {"rounds": [], "warnings": []}
    for entry in rounds:
        if entry["included"] and not all((entry["dir"] / v).is_dir() for v in versions):
            entry["included"], entry["exclusion"] = False, "missing a version directory"
        entry["runs"] = {}
        if entry["included"]:
            for suite in SUITES:
                merged = {"cells": {}, "sessions": {}, "blocks": {}, "notes": []}
                for version in versions:
                    suite_dir = entry["dir"] / version / suite
                    if not suite_dir.is_dir():
                        continue
                    run = load_run_dir(suite_dir, suite=suite, expect_engine="polars")
                    for (s, scale, query, engine), cell in run["cells"].items():
                        merged["cells"][(s, scale, query, version_tag(version))] = cell
                        session = run["sessions"].get((s, scale, engine))
                        if session and session.get("version") and not version.endswith(str(session["version"])):
                            info["warnings"].append(f"{entry['label']}/{version}/{suite}/{scale}: session reports polars {session['version']}")
                    for (s, scale, engine), summary in run["sessions"].items():
                        merged["sessions"][(s, scale, version_tag(version))] = summary
                if merged["cells"]:
                    entry["runs"][suite] = merged
        campaign["rounds"].append(entry)
    info["available"] = any(e["included"] and e["runs"] for e in campaign["rounds"])
    info["rounds"] = [{"round": e["id"], "label": e["label"], "included": e["included"], "reason": e["exclusion"]}
                      for e in campaign["rounds"]]
    return campaign, info


def estimate_side_study(campaign, info):
    if campaign is None or not info["available"]:
        return {"meta": {**info, "available": False}, "ratio": {}}          # no "medians" key: analyses written before the key existed stay reproducible value for value
    old, new = (version_tag(v) for v in info["versions"])
    tags = (old, new)
    data = Data(campaign, engines=tags)
    spec = {"numerator": new, "denominator": old}
    ratio = {}
    medians = {}      # per-round medians of each Polars version, so that other analyses (paper 2 protocol check) need no raw files
    for suite in data.suites:
        ratio[suite] = {}
        for scale in data.scales[suite]:
            per_query = {q: contrast_query(data, suite, scale, q, spec) for q in data.queries[suite]}
            record = contrast_suite_scale(data, suite, scale, spec, per_query)
            record["q"] = per_query
            record["geomean_seconds"] = {tag: {q: geomean(list(data.times(suite, scale, q, tag).values())) for q in data.queries[suite]}
                                         for tag in tags}
            ratio[suite][scale] = record
            medians.setdefault(suite, {})[scale] = {
                q: {tag: {"rounds": sorted(data.times(suite, scale, q, tag)), "per_round": [data.times(suite, scale, q, tag)[r] for r in sorted(data.times(suite, scale, q, tag))]}
                    for tag in tags if data.times(suite, scale, q, tag)} for q in data.queries[suite]}
    meta = {**info, "numerator": new, "denominator": old, "rounds_included": data.rounds,
            "definition": f"time({info['versions'][1]}) / time({info['versions'][0]}) on the same host; above 1 means the newer Polars is slower",
            "engine": "polars (CPU streaming)"}
    return {"meta": meta, "ratio": ratio, "medians": medians}


# ------------------------------------------------------------------ exploratory (post-hoc) analyses

EXPLORATORY_LABEL = "exploratory (post-hoc; not in the locked design §9)"
EXPLORATORY = {       # id -> numerator (a side-study Polars version), denominator (main-campaign GPU side), counterpart contrast
    "e1": {"denominator": "polars_gpu", "counterpart": "c1", "label": "Polars {v} CPU / Polars GPU"},
    "e4": {"denominator": "best_gpu", "counterpart": "c4", "label": "Polars {v} CPU / best GPU engine"},
}
EXPLORATORY_INTERVAL = ("Welch-type 95% interval on the log scale: the CPU times come from the side-study sessions and the GPU times from the main rounds, "
                        "different sessions in different rounds, so the two sets of per-round values are unpaired (Welch-Satterthwaite df); no interval with "
                        "fewer than two rounds on either side")


def exploratory_record(cpu, gpu, side_block, main_block):
    """Welch-type ratio of two unpaired sets of per-round values: cpu = {side round: seconds}, gpu = {main round: seconds}."""
    cpu_rounds, gpu_rounds = sorted(cpu), sorted(gpu)
    w = welch_summary([cpu[r] for r in cpu_rounds], [gpu[r] for r in gpu_rounds])
    return {"estimate": w["estimate"], "ci_low": w["ci_low"], "ci_high": w["ci_high"], "df": w["df"], "log_se": w["log_se"],
            "n_cpu_rounds": w["n_host"], "n_gpu_rounds": w["n_ref"], "cpu_seconds": w["host_seconds"], "gpu_seconds": w["ref_seconds"],
            "cpu_per_round": w["host_per_round"], "gpu_per_round": w["ref_per_round"], "cpu_rounds": cpu_rounds, "gpu_rounds": gpu_rounds}


def exploratory_query(data, side, suite, scale, query, spec, tag):
    cpu = dict(side.times(suite, scale, query, tag))
    if spec["denominator"] == "best_gpu":
        by_round = {}
        for engine in GPU_ENGINES:
            for rnd, t in data.times(suite, scale, query, engine).items():
                by_round.setdefault(rnd, {})[engine] = t
        gpu = {rnd: min(c.values()) for rnd, c in by_round.items()}
        best = Counter(min(c, key=c.get) for c in by_round.values())
        sides = list(GPU_ENGINES)
    else:
        gpu = dict(data.times(suite, scale, query, spec["denominator"]))
        best = None
        sides = [spec["denominator"]]
    side_block, main_block = side.block_rounds(suite, scale), data.block_rounds(suite, scale)
    record = exploratory_record(cpu, gpu, side_block, main_block)
    blockers = []
    if not cpu:
        failure = side.failure(suite, scale, query, tag)
        blockers.append({"engine": tag, "reason_class": failure[0] if failure else "not_run", "reason": failure[1] if failure else "no cell recorded"})
    if not gpu:
        for engine in sides:
            failure = data.failure(suite, scale, query, engine)
            blockers.append({"engine": engine, "reason_class": failure[0] if failure else "not_run", "reason": failure[1] if failure else "no cell recorded"})
    if cpu and gpu:
        record["status"] = "ok" if len(cpu) == len(side_block) and len(gpu) == len(main_block) else "partial"
    else:
        ran = any((r, suite, scale, query, e) in data.cells for r in main_block for e in sides) or any(
            (r, suite, scale, query, tag) in side.cells for r in side_block)
        record["status"] = ("not_run" if not ran else "unstable" if any(b["reason_class"] == "unstable_coverage" for b in blockers)
                            else "unsupported" if blockers else "no_overlap")
    record["blocked_by"] = blockers
    if best:
        record["best_engine_rounds"] = dict(best)
    record["unstable_engines"] = sorted(e for e in sides if (suite, scale, query, e) in data.unstable)
    return record


def exploratory_suite(data, side, suite, scale, per_query):
    side_block, main_block = side.block_rounds(suite, scale), data.block_rounds(suite, scale)
    used = [q for q, r in per_query.items() if r["status"] == "ok"]
    excluded = {q: r["status"] if r["status"] not in ("unsupported", "unstable") else
                f"{r['status']}: " + ", ".join(f"{b['engine']} ({b['reason_class']})" for b in r["blocked_by"])
                for q, r in per_query.items() if q not in used}

    def per_round(key, count):
        return [math.exp(math.fsum(math.log(per_query[q][key][i]) for q in used) / len(used)) for i in range(count)] if used else []

    cpu = dict(zip(side_block, per_round("cpu_per_round", len(side_block))))
    gpu = dict(zip(main_block, per_round("gpu_per_round", len(main_block))))
    record = exploratory_record(cpu, gpu, side_block, main_block)
    record.update(status="ok" if used else "no_common_query", n_queries=len(used), queries_used=used, queries_excluded=excluded)
    return record


def estimate_exploratory(data, side_campaign, side_info):
    """Post-hoc secondary analyses (never part of C1-C4 or the crossover): E1 = Polars <new> CPU (side-study sessions) / Polars GPU (main rounds),
    E4 = Polars <new> CPU / best GPU engine. Same orientation as C1 and C4 (above 1: the GPU side is faster)."""
    available = bool(side_campaign is not None and side_info.get("available") and len(side_info.get("versions") or []) == 2)
    version = side_info["versions"][-1].split("-", 1)[-1] if available else None
    out = {}
    for name, spec in EXPLORATORY.items():
        out[name] = {"meta": {
            "label": EXPLORATORY_LABEL, "available": available, "contrast": spec["label"].format(v=version or "<new>"), "counterpart": spec["counterpart"],
            "numerator": f"polars {version} CPU (streaming), side-study sessions" if available else None,
            "denominator": ("polars_gpu" if spec["denominator"] == "polars_gpu" else "best GPU engine per query and main round (polars_gpu or spark_gpu)"),
            "cpu_version": version, "side_study_rounds": [], "main_rounds": list(data.rounds), "interval": EXPLORATORY_INTERVAL,
            "definition": ("time of the Polars CPU side-study sessions / time of the GPU side of the main rounds, per (suite, scale, query); above 1 means the GPU side "
                           "was faster. The suite value is the same estimator over the per-round geometric mean of the queries timed in every round on both sides. "
                           "This is post-hoc: not pre-registered, not part of the locked design (section 9), never mixed into C1-C4 or the crossover verdict."),
            "reason": None if available else "no side study with two Polars versions"}}
    if not available:
        return out
    tag = version_tag(side_info["versions"][-1])
    side = Data(side_campaign, engines=tuple(version_tag(v) for v in side_info["versions"]))
    for name, spec in EXPLORATORY.items():
        out[name]["meta"]["side_study_rounds"] = list(side.rounds)
        for suite in data.suites:
            if suite not in side.scales:
                continue
            for scale in data.scales[suite]:
                if scale not in side.scales[suite]:
                    continue
                per_query = {q: exploratory_query(data, side, suite, scale, q, spec, tag) for q in data.queries[suite]}
                record = exploratory_suite(data, side, suite, scale, per_query)
                record["q"] = per_query
                out[name].setdefault(suite, {})[scale] = record
    return out


# ------------------------------------------------------------------ paper 2: baseline-confound analyses (exploratory, post-hoc)

PAPER2_LABEL = EXPLORATORY_LABEL
PAPER2_FIELDS = ("estimate", "ci_low", "ci_high", "df")


def paper2_pick(record, extra=()):
    return {**{k: record.get(k) for k in PAPER2_FIELDS}, **{k: record.get(k) for k in extra}}


def paper2_series(stats, suite, scale, queries, engine):
    """{round: suite geometric-mean time over `queries`} for one engine from stats['medians']; None unless every query has a full, valid set of rounds."""
    cells = stats["medians"].get(suite, {}).get(scale, {})
    per_query = []
    for query in queries:
        cell = (cells.get(query) or {}).get(engine)
        if not cell or cell.get("status") != "ok" or not cell.get("rounds") or any(v is None for v in cell["per_round"]):
            return None
        per_query.append(dict(zip(cell["rounds"], cell["per_round"])))
    if not per_query:
        return None
    rounds = sorted(set.intersection(*[set(d) for d in per_query]))
    return {r: math.exp(math.fsum(math.log(d[r]) for d in per_query) / len(per_query)) for r in rounds} if rounds else None


def paper2_protocol_check(stats):
    """K1: side-study Polars 1.44.2 time / main-round polars time on the same host, per suite and scale (suite geomean over the queries timed in every round on both sides)."""
    side = stats.get("side_study") or {}
    meta = side.get("meta") or {}
    if not meta.get("available") or not side.get("medians"):
        return {}
    old = meta["denominator"]                       # the older Polars version of the side study (1.44.2)
    out = {}
    for suite, by_scale in side["medians"].items():
        for scale, by_query in by_scale.items():
            side_rounds = set(meta.get("rounds_included") or [])
            used = [q for q, tags in by_query.items() if old in tags and set(tags[old]["rounds"]) == side_rounds
                    and paper2_series(stats, suite, scale, [q], "polars") is not None]
            if not used:
                continue
            side_series = {r: math.exp(math.fsum(math.log(dict(zip(by_query[q][old]["rounds"], by_query[q][old]["per_round"]))[r]) for q in used) / len(used))
                           for r in sorted(side_rounds)}
            main_series = paper2_series(stats, suite, scale, used, "polars")
            record = welch_summary(list(side_series.values()), list(main_series.values()))
            out.setdefault(suite, {})[scale] = {**paper2_pick(record), "n_queries": len(used), "queries_used": used, "n_side_rounds": record["n_host"],
                                                "n_main_rounds": record["n_ref"], "side_seconds": record["host_seconds"], "main_seconds": record["ref_seconds"]}
    return out


def paper2_decomposition(stats, reference_stats):
    """K2: for c1 and c2, C_host / C_reference split into the CPU engine's and the GPU engine's host / reference time ratios over ONE matched query set.

    log(speedup_ratio) = log(cpu_side) - log(gpu_side) holds exactly for the point estimates (checked to 1e-9); every interval is Welch-type on the log scale
    (rounds of two campaigns are unpaired)."""
    out = {}
    for c in ("c1", "c2"):
        spec = CONTRASTS[c]
        cpu_engine, gpu_engine = spec["numerator"], spec["denominator"]
        for suite, by_scale in (stats.get(c) or {}).items():
            for scale, mine in by_scale.items():
                theirs = ((reference_stats.get(c) or {}).get(suite) or {}).get(scale)
                if not theirs or mine.get("estimate") is None or theirs.get("estimate") is None:
                    continue
                queries = [q for q in mine["queries_used"] if q in set(theirs["queries_used"])]
                series = {(who, engine): paper2_series(s, suite, scale, queries, engine) for who, s in (("host", stats), ("ref", reference_stats))
                          for engine in (cpu_engine, gpu_engine)}
                if not queries or any(v is None for v in series.values()):
                    continue
                speed = {who: [series[(who, cpu_engine)][r] / series[(who, gpu_engine)][r] for r in sorted(set(series[(who, cpu_engine)]) & set(series[(who, gpu_engine)]))]
                         for who in ("host", "ref")}
                ratio = welch_summary(speed["host"], speed["ref"])
                cpu = welch_summary(list(series[("host", cpu_engine)].values()), list(series[("ref", cpu_engine)].values()))
                gpu = welch_summary(list(series[("host", gpu_engine)].values()), list(series[("ref", gpu_engine)].values()))
                gap = abs(math.log(ratio["estimate"]) - (math.log(cpu["estimate"]) - math.log(gpu["estimate"])))
                if gap > 1e-9:
                    raise AssertionError(f"decomposition identity broken for {c}/{suite}/{scale}: {gap}")
                out.setdefault(c, {}).setdefault(suite, {})[scale] = {
                    "speedup_ratio": paper2_pick(ratio), "cpu_side": paper2_pick(cpu), "gpu_side": paper2_pick(gpu), "n_queries": len(queries),
                    "queries_used": queries, "queries_dropped_for_the_pair": sorted(set(mine["queries_used"]) ^ set(theirs["queries_used"])),
                    "cpu_engine": cpu_engine, "gpu_engine": gpu_engine, "identity_gap": gap,
                    "speedup_host": ratio["host_seconds"], "speedup_reference": ratio["ref_seconds"],
                    "n_rounds_host": ratio["n_host"], "n_rounds_reference": ratio["n_ref"]}
    return out


def paper2_verdict(ci_low, ci_high):
    """gpu_faster (interval entirely above 1), cpu_faster (entirely below), inconclusive (contains 1); None without an interval."""
    if ci_low is None or ci_high is None:
        return None
    return "gpu_faster" if ci_low > 1 else "cpu_faster" if ci_high < 1 else "inconclusive"


def paper2_suite_series(per_query, queries):
    """{round: geometric mean over `queries` of that round's value}; the rounds every query has."""
    rounds = sorted(set.intersection(*[set(per_query[q]) for q in queries]))
    return {r: math.exp(math.fsum(math.log(per_query[q][r]) for q in queries) / len(queries)) for r in rounds}


def paper2_adjusted_record(old, new, pol, gpu, queries):
    """E1 / K1 for one query set as B_matched x C: B = Polars new / old in the side-study rounds (paired within a round), C = main-round Polars CPU / GPU (paired within a round).

    old, new: side-study {query: {round: seconds}} of the two Polars versions; pol, gpu: main-round {query: {round: seconds}} of Polars CPU 1.44.2 and the GPU side.
    estimate = exp(mean log B + mean log C); se^2 = s_B^2/n_B + s_C^2/n_C with Welch-Satterthwaite df (the two factors come from different sessions). The point estimate is
    asserted equal to E1 / K1 computed over the same queries, to 1e-9."""
    s_old, s_new, m_pol, m_gpu = (paper2_suite_series(d, queries) for d in (old, new, pol, gpu))
    b = [s_new[r] / s_old[r] for r in sorted(s_new) if r in s_old]
    c = [m_pol[r] / m_gpu[r] for r in sorted(m_pol) if r in m_gpu]
    lb, lc = [math.log(x) for x in b], [math.log(x) for x in c]
    estimate = math.exp(statistics.fmean(lb) + statistics.fmean(lc))
    record = {"estimate": estimate, "ci_low": None, "ci_high": None, "df": None, "n_queries": len(queries), "n_side_rounds": len(b), "n_main_rounds": len(c)}
    if len(b) >= 2 and len(c) >= 2:
        vb, vc = statistics.variance(lb) / len(lb), statistics.variance(lc) / len(lc)
        variance = vb + vc
        if variance == 0.0:
            record.update(df=float(len(b) + len(c) - 2), ci_low=estimate, ci_high=estimate)
        else:
            df = variance ** 2 / ((vb ** 2 / (len(b) - 1) if vb else 0.0) + (vc ** 2 / (len(c) - 1) if vc else 0.0))
            half = t_quantile(0.5 + LEVEL / 2.0, df) * math.sqrt(variance)
            record.update(df=df, ci_low=math.exp(math.log(estimate) - half), ci_high=math.exp(math.log(estimate) + half))
    e = welch_summary(list(s_new.values()), list(m_gpu.values()))
    k = welch_summary(list(s_old.values()), list(m_pol.values()))
    gap = abs(math.log(estimate) - (math.log(e["estimate"]) - math.log(k["estimate"])))
    if gap > 1e-9:
        raise AssertionError(f"E1 / K1 identity broken over {queries}: {gap}")
    record.update(identity_gap=gap, unadjusted_over_session_check=e["estimate"] / k["estimate"], unadjusted=paper2_pick(e), session_check=paper2_pick(k),
                  b_matched={"estimate": math.exp(statistics.fmean(lb)), **dict(zip(("ci_low", "ci_high", "df"), paper2_one_sample(lb)))},
                  c_baseline={"estimate": math.exp(statistics.fmean(lc)), **dict(zip(("ci_low", "ci_high", "df"), paper2_one_sample(lc)))},
                  verdict_unadjusted=paper2_verdict(e["ci_low"], e["ci_high"]), verdict_adjusted=paper2_verdict(record["ci_low"], record["ci_high"]))
    record["verdict_changes"] = bool(record["verdict_unadjusted"] and record["verdict_adjusted"] and record["verdict_unadjusted"] != record["verdict_adjusted"])
    return record


def paper2_one_sample(logs):
    """(ci_low, ci_high, df) of exp(mean of `logs`): the t-interval across rounds; (None, None, None) with fewer than two."""
    if len(logs) < 2:
        return None, None, None
    mean, sd = statistics.fmean(logs), statistics.stdev(logs)
    half = t_quantile(0.5 + LEVEL / 2.0, len(logs) - 1) * sd / math.sqrt(len(logs))
    return math.exp(mean - half), math.exp(mean + half), float(len(logs) - 1)


def paper2_adjusted(stats, which):
    """exploratory_adj.<which>.<suite>.<scale>: E1 / K1 (which='e1', GPU side Polars GPU) or E4 / K4 (which='e4', GPU side the better GPU engine per query and round)."""
    side = stats.get("side_study") or {}
    meta = side.get("meta") or {}
    if not meta.get("available") or not side.get("medians"):
        return {}
    old_tag, new_tag = meta["denominator"], meta["numerator"]
    side_rounds, main_rounds = sorted(meta.get("rounds_included") or []), sorted(stats["campaign"]["rounds_included"])
    out = {}
    for suite, by_scale in side["medians"].items():
        for scale, by_query in by_scale.items():
            cells = stats["medians"].get(suite, {}).get(scale) or {}

            def main_series(query, engine):
                cell = (cells.get(query) or {}).get(engine)
                if not cell or cell.get("status") != "ok" or sorted(cell.get("rounds") or []) != main_rounds or any(v is None for v in cell["per_round"]):
                    return None
                return dict(zip(cell["rounds"], cell["per_round"]))

            def gpu_series(query):
                if which == "e1":
                    return main_series(query, "polars_gpu")
                found = [s for s in (main_series(query, e) for e in GPU_ENGINES) if s]
                return {r: min(s[r] for s in found) for r in main_rounds} if found else None

            def side_series(query, tag):
                d = by_query.get(query, {}).get(tag)
                return dict(zip(d["rounds"], d["per_round"])) if d and sorted(d["rounds"]) == side_rounds else None

            usable = {q: (side_series(q, old_tag), side_series(q, new_tag), main_series(q, "polars"), gpu_series(q)) for q in by_query}
            queries = [q for q, parts in usable.items() if all(parts)]
            if not queries:
                continue
            pick = lambda i: {q: usable[q][i] for q in queries}
            cell_out = paper2_adjusted_record(pick(0), pick(1), pick(2), pick(3), queries)
            cell_out["queries_used"] = queries
            cell_out["q"] = {q: paper2_adjusted_record({q: usable[q][0]}, {q: usable[q][1]}, {q: usable[q][2]}, {q: usable[q][3]}, [q]) for q in queries}
            for rec in cell_out["q"].values():
                for k in ("unadjusted", "session_check", "b_matched", "c_baseline", "unadjusted_over_session_check", "n_side_rounds", "n_main_rounds"):
                    rec.pop(k, None)
            out.setdefault(suite, {})[scale] = cell_out
    return out


def paper2_gap_keys(protocol, sensitivity):
    """Add e1_c1_gap, k1_limit and gap_exceeds_k1_limits to every protocol_check cell that has both C1 and E1."""
    for suite, by_scale in protocol.items():
        for scale, record in by_scale.items():
            both = (sensitivity.get(suite, {}).get(scale) or {})
            c1, e1 = both.get("c1"), both.get("e1")
            if c1 and e1 and c1["estimate"] and e1["estimate"] is not None and record["ci_low"] is not None:
                gap = 1.0 - e1["estimate"] / c1["estimate"]
                limit = max(abs(record["ci_low"] - 1.0), abs(record["ci_high"] - 1.0))
                record.update(e1_c1_gap=gap, k1_limit=limit, gap_exceeds_k1_limits=bool(gap > limit))
    return protocol


def paper2_sensitivity(stats):
    """C1 (locked design) and E1 (exploratory) per suite and scale, as drawn in f20."""
    out = {}
    e1 = (stats.get("exploratory") or {}).get("e1") or {}
    for suite, by_scale in (stats.get("c1") or {}).items():
        for scale, c1 in by_scale.items():
            entry = {"c1": paper2_pick(c1, ("n_queries",))}
            record = (e1.get(suite) or {}).get(scale) if (e1.get("meta") or {}).get("available") else None
            entry["e1"] = paper2_pick(record, ("n_queries", "n_cpu_rounds", "n_gpu_rounds")) if record else None
            out.setdefault(suite, {})[scale] = entry
    return out


def build_paper2(host_stats, reference=None):
    """The paper-2 file from per-host stats dicts {label: stats}: hosts, protocol_check (K1), decomp (K2), sensitivity (f20 data). Lower-case host keys."""
    reference = reference or (DEFAULT_REFERENCE if DEFAULT_REFERENCE in host_stats else sorted(host_stats)[0])
    if reference not in host_stats:
        raise ValueError(f"reference host {reference!r} is not among {sorted(host_stats)}")
    labels = ladder_sorted(host_stats)
    key = lambda label: label.lower()
    hosts = {}
    for label in labels:
        h = host_stats[label].get("host") or {}
        exploratory = (host_stats[label].get("exploratory") or {}).get("e1", {}).get("meta") or {}
        hosts[key(label)] = {
            "label": label, "gpu": h.get("gpu"), "arch": h.get("arch"), "cpu_model": h.get("cpu_model"), "vcpus": h.get("vcpus"), "cpus_arg": h.get("cpus_arg"),
            "smt_threads_per_core": h.get("smt_threads_per_core"), "physical_cores": h.get("physical_cores"),
            "cpus_arg_cores_min": h.get("cpus_arg_cores_min"), "cpus_arg_cores_max": h.get("cpus_arg_cores_max"),
            "bridge_available": bool(exploratory.get("available")), "side_study_rounds": exploratory.get("side_study_rounds") or [],
            "main_rounds": host_stats[label]["campaign"]["rounds_included"], "role": "reference" if label == reference else "comparison",
            "cores_text": paper2_cores_text(h)}
    ref = host_stats[reference]
    return {
        "meta": {"label": PAPER2_LABEL, "reference": key(reference), "hosts": [key(x) for x in labels], "schema_version": "1.0",
                 "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "synthetic": any(s.get("synthetic") for s in host_stats.values()),
                 "interval": EXPLORATORY_INTERVAL,
                 "definition": ("Post-hoc analyses for the baseline-confound paper; none is part of the locked design (section 9). protocol_check: side-study Polars 1.44.2 time / main-round polars "
                                "time on the same host. decomp: for c1 and c2, the speedup of a host divided by the speedup of the reference host, split into the CPU engine's and the GPU "
                                "engine's host / reference time ratios over one matched query set. sensitivity: C1 (locked design, t-interval over rounds) next to E1 (Polars 2.0.0 CPU / Polars "
                                "GPU, Welch-type). Every interval except C1's is Welch-type on the log scale (unpaired rounds).")},
        "hosts": hosts,
        "protocol_check": {key(x): paper2_gap_keys(paper2_protocol_check(host_stats[x]), paper2_sensitivity(host_stats[x])) for x in labels},
        "decomp": {key(x): paper2_decomposition(host_stats[x], ref) for x in labels if x != reference},
        "sensitivity": {key(x): paper2_sensitivity(host_stats[x]) for x in labels},
        "exploratory_adj": {"meta": {"label": PAPER2_LABEL + ", sensitivity", "definition": (
                                "E1 / K1 and E4 / K4 per host, suite and scale: the exploratory Polars 2.0.0 CPU / GPU ratio divided by the protocol check (side-study Polars 1.44.2 / main-round "
                                "polars), computed over the same queries. Equals B_matched x C: B = Polars 2.0.0 / 1.44.2 in the side-study rounds, C = main-round Polars CPU 1.44.2 / GPU (C1, or "
                                "C4 for E4), each paired within its rounds. Interval: se^2 = s_B^2/n_B + s_C^2/n_C with Welch-Satterthwaite df. The point estimate is asserted equal to E1 / K1 "
                                "(identity_gap, 1e-9). verdict_*: gpu_faster / cpu_faster / inconclusive from the interval against 1."),
                                "interval": EXPLORATORY_INTERVAL},
                            "e1": {key(x): paper2_adjusted(host_stats[x], "e1") for x in labels},
                            "e4": {key(x): paper2_adjusted(host_stats[x], "e4") for x in labels}}}


def paper2_cores_text(host):
    """'26 thr on 13 cores (SMT2)': the --cpus threads and the physical cores they can occupy, as lscpu reports the machine."""
    lo, hi, smt, threads = host.get("cpus_arg_cores_min"), host.get("cpus_arg_cores_max"), host.get("smt_threads_per_core"), host.get("cpus_arg")
    if threads is None or lo is None or hi is None:
        return None
    return f"{threads} thr on {lo if lo == hi else f'{lo} to {hi}'} cores (SMT{smt})" if smt else f"{threads} thr on {lo if lo == hi else f'{lo} to {hi}'} cores"


PAPER2_SCHEMA = [
    ("meta.label", "Always 'exploratory (post-hoc; not in the locked design §9)': nothing in this file is part of the locked design."),
    ("meta.reference", "Reference host (h100); decomp ratios are host / reference."),
    ("meta.hosts", "Host keys in GPU-generation order (a100 h100 gh200 b200 as present)."),
    ("meta.interval / meta.definition", "How the intervals are built and what each block is."),
    ("hosts.{host}.{field}", "Identity and topology of the host: label, gpu, arch, cpu_model, vcpus, cpus_arg, smt_threads_per_core, physical_cores, cpus_arg_cores_min, cpus_arg_cores_max, cores_text ('26 thr on 13 cores (SMT2)'), bridge_available (side study with two Polars versions present), side_study_rounds, main_rounds, role."),
    ("protocol_check.{host}.{suite}.{scale}.{estimate,ci_low,ci_high,df,n_queries}", "K1: time of Polars 1.44.2 in the side-study sessions / time of the main-round polars sessions on the same host: suite geometric mean over the queries timed in every round on both sides; Welch-type 95% interval on the log scale. Also n_side_rounds, n_main_rounds, side_seconds, main_seconds, queries_used. Hosts without a side study have an empty object."),
    ("decomp.{host}.{c}.{suite}.{scale}.speedup_ratio.{estimate,ci_low,ci_high,df}", "K2: speedup of the host (C_host, CPU engine time / GPU engine time of contrast c in c1 or c2, per-round suite geomean) divided by the reference host's, Welch-type interval across the two hosts' rounds. Hosts: every host except the reference."),
    ("decomp.{host}.{c}.{suite}.{scale}.cpu_side.{estimate,ci_low,ci_high,df}", "Host / reference time ratio of the contrast's CPU engine (polars for c1, spark_cpu for c2) over the SAME matched query set as speedup_ratio (for c1 complex: the queries Polars GPU supports)."),
    ("decomp.{host}.{c}.{suite}.{scale}.gpu_side.{estimate,ci_low,ci_high,df}", "The same for the GPU engine (polars_gpu for c1, spark_gpu for c2). log(speedup_ratio.estimate) = log(cpu_side.estimate) - log(gpu_side.estimate) exactly (identity_gap, asserted <= 1e-9)."),
    ("decomp.{host}.{c}.{suite}.{scale}.{n_queries,queries_used,queries_dropped_for_the_pair,cpu_engine,gpu_engine,identity_gap,speedup_host,speedup_reference,n_rounds_host,n_rounds_reference}", "Query set and bookkeeping of the decomposition."),
    ("protocol_check.{host}.{suite}.{scale}.{e1_c1_gap,k1_limit,gap_exceeds_k1_limits}", "Where the host has C1 and E1: e1_c1_gap = 1 - E1 / C1 (point estimates), k1_limit = max(|ci_low - 1|, |ci_high - 1|) of this cell's K1, gap_exceeds_k1_limits = e1_c1_gap > k1_limit."),
    ("exploratory_adj.meta.{label,definition,interval}", "Label (exploratory sensitivity), definition and interval of the session-adjusted ratios."),
    ("exploratory_adj.{e1,e4}.{host}.{suite}.{scale}.{estimate,ci_low,ci_high,df,n_queries}", "K4: E1 / K1 (e1; GPU side Polars GPU) or E4 / K4 (e4; GPU side the better GPU engine per query and round), over the same queries, for hosts with a bridge. Equals B_matched x C: B = Polars 2.0.0 / 1.44.2 in the side-study rounds, C = main-round Polars CPU 1.44.2 / GPU side (C1 or C4), each paired within its rounds; se^2 = s_B^2/n_B + s_C^2/n_C, Welch-Satterthwaite df."),
    ("exploratory_adj.{e1,e4}.{host}.{suite}.{scale}.{identity_gap,unadjusted_over_session_check,b_matched,c_baseline,unadjusted,session_check,verdict_unadjusted,verdict_adjusted,verdict_changes,queries_used,n_side_rounds,n_main_rounds}", "identity_gap: |log(estimate) - log(unadjusted / session_check)| (asserted <= 1e-9); unadjusted and session_check: E1 (or E4) and K1 (or K4) over the same queries; b_matched and c_baseline: the two factors with one-sample t-intervals across rounds; verdict_*: gpu_faster / cpu_faster / inconclusive from the interval against 1; verdict_changes: the verdict differs after the adjustment."),
    ("exploratory_adj.{e1,e4}.{host}.{suite}.{scale}.q.{query}.{estimate,ci_low,ci_high,df,n_queries,identity_gap,verdict_unadjusted,verdict_adjusted,verdict_changes}", "The same adjustment for one query (every query with a session check). Hosts without a bridge have an empty object."),
    ("sensitivity.{host}.{suite}.{scale}.c1.{estimate,ci_low,ci_high,df,n_queries}", "C1 of the host's own campaign (Polars CPU 1.44.2 / Polars GPU; t-interval over rounds)."),
    ("sensitivity.{host}.{suite}.{scale}.e1.{estimate,ci_low,ci_high,df,n_queries,n_cpu_rounds,n_gpu_rounds}", "E1 of the host: Polars 2.0.0 CPU (side-study sessions) / Polars GPU (main rounds), Welch-type; null when the host has no side study."),
]


def paper2_schema_markdown():
    lines = ["# paper2.json schema", "",
             f"**{PAPER2_LABEL}.** Written by `analyze_campaign.py --paper2 --host LABEL=stats.json ... --output DIR` from the hosts' stats.json files. Host keys are lower case. "
             "Ratios are slower-is-numerator time ratios as elsewhere; `decomp` ratios are host / reference time (below 1: the host is faster).", "",
             "| Key pattern | Meaning |", "|---|---|"]
    lines += [f"| `{pattern}` | {text} |" for pattern, text in PAPER2_SCHEMA]
    lines += ["", f"Interval: {EXPLORATORY_INTERVAL}.", ""]
    return "\n".join(lines)


def write_paper2(paper2, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "paper2.json").write_text(json.dumps(paper2, indent=2, allow_nan=False) + "\n")
    (output / "PAPER2_SCHEMA.md").write_text(paper2_schema_markdown())
    return output / "paper2.json"


def load_a100(roots):
    """Merge A100 run roots (production, large-sf100, large-sf300, complex-formal-v2) into one cell map."""
    merged = {"cells": {}, "hosts": [], "roots": [], "notes": [], "facts": {}, "sessions": [], "orders": {}, "arguments": [], "campaign_roots": []}
    for raw in roots or ():
        root = Path(raw)
        if not root.is_dir():
            merged["notes"].append(f"{root}: not a directory")
            continue
        run = load_run_dir(root)
        merged["roots"].append(str(root))
        if (root / "campaign.json").is_file():
            merged["campaign_roots"].append(str(root))
        merged["arguments"].append({"root": str(root), **{k: v for k, v in ((run["environment"] or {}).get("arguments") or {}).items()
                                                          if k in ("cpus", "runs", "driver_memory", "engine_timeout_seconds", "keep_going", "query_cleanup", "cleanup_timeout_seconds")}})
        for (suite, scale), order in run["blocks"].items():
            merged["orders"][f"{suite}/{scale}"] = order
        for (suite, scale, engine), summary in run["sessions"].items():
            merged["sessions"].append({"suite": suite, "scale": scale, "engine": engine, "version": summary.get("version"),
                                       "spark_jars": summary.get("spark_jars"), "cudf_polars_version": summary.get("cudf_polars_version"),
                                       "spark_gpu_profile": summary.get("spark_gpu_profile"), "spark_conf": summary.get("spark_conf"),
                                       "plan_trials": summary.get("plan_trials") or [], "plan_catalog": summary.get("plan_catalog") or {},
                                       "plan_texts": summary.get("plan_texts") or {}, "plans_missing": summary.get("plans_missing") or 0,
                                       "events_dir": summary.get("events_dir"), **(summary.get("protocol") or {})})
        shape = host_shape(run["environment"])
        merged["hosts"].append({"root": str(root), "host": shape, "label": host_label(shape)})
        for key, cell in run["cells"].items():
            if key in merged["cells"]:
                merged["notes"].append(f"{key[0]}/{key[1]}/{key[3]}/{key[2]}: root {root} replaces an earlier root")
            merged["cells"][key] = cell
        for (suite, scale, engine), session in run["sessions"].items():
            if engine in GPU_ENGINES:
                merged["facts"][engine] = {"version": session.get("version"), "spark_jars": session.get("spark_jars"),
                                           "cudf_polars_version": session.get("cudf_polars_version"),
                                           "spark_gpu_profile": session.get("spark_gpu_profile")}
    return merged


RUN_SPECIFIC_CONF = re.compile(r"^spark\.(app\.|driver\.(host|port)|executor\.id|eventLog|jars|repl|submit|plugins\.internal)")
STUDY_SESSION_FIELDS = (            # (field, label); library version means polars for the Polars engines and Spark for the Spark engines
    ("version", "library version"), ("cpu_threads", "threads"), ("runs", "trials per cell"), ("cache_policy", "cache policy"),
    ("timing_scope", "timing scope"), ("query_cleanup_policy", "query cleanup policy"), ("failure_policy", "failure policy"),
    ("suite_version", "suite version"), ("polars_execution_engine", "Polars execution engine"), ("spark_gpu_profile", "Spark GPU profile"),
    ("cudf_polars_version", "cudf-polars version"), ("spark_jars", "Spark jars"))
STUDY_FIELD_LABELS = {**dict(STUDY_SESSION_FIELDS), "gpu": "GPU", "vcpus": "vCPUs", "host_ram_gib": "host RAM (GiB)", "sessions_per_cell": "sessions per cell",
                      "engine_order": "engine order"}
STUDY_SUMMARY_ORDER = ("library version", "threads", "sessions per cell", "engine_order", "query cleanup policy", "failure policy", "suite version",
                       "Spark GPU profile", "spark_conf", "Spark jars", "GPU", "vCPUs", "host RAM (GiB)")


def study_values(values):
    out = set()
    for value in values:
        out.add((", ".join(map(str, value)) or None) if isinstance(value, (list, tuple)) else value)
    return sorted(out, key=lambda v: (v is None, str(v)))


def a100_study_block(data, a100, host, ratio_engines):
    """Who the 'A100' is: the previous study's saved evidence (never the A100 campaign), and everything recorded that differs from the H100 campaign.

    Every entry is read from the two sides' result files; nothing is filled by hand. A field the earlier study did not record is None on its side."""
    blank = {"label": None, "previous_study": None, "roots": [], "first_session_utc": None, "last_session_utc": None, "engines_present": [],
             "engines_compared": [], "engines_only_in_h100_campaign": [], "gpu": None, "vcpus": None, "host_ram_gib": None, "polars_version": None,
             "polars_version_h100": None, "spark_version": None, "spark_version_h100": None, "cpu_threads": None, "cpu_threads_h100": None,
             "sessions_per_cell": None, "rounds_h100": None, "arguments": [], "differences": [], "same": [], "spark_conf_differences": {},
             "spark_conf_keys_recorded": {"a100_study": None, "h100_campaign": None}, "difference_summary": None, "difference_headline": None,
             "plan_variability": plan_variability_section({"rounds": []})}
    if not a100 or not a100["cells"]:
        return blank
    sessions = a100["sessions"]
    starts = sorted(s["started_at_utc"] for s in sessions if s.get("started_at_utc"))
    days = sorted({parse_utc(t).date().isoformat() for t in starts if parse_utc(t)})
    label = (f"{days[0]} A100 study" if len(days) == 1 else f"{days[0]} to {days[-1]} A100 study") if days else "previous A100 study"
    h100 = [(key, summary) for key, summary in sorted(data.sessions.items())]

    def h100_value(summary, field):
        return summary.get(field) if field in ("version", "spark_jars", "cudf_polars_version", "spark_gpu_profile") else (summary.get("protocol") or {}).get(field)

    a100_engines = sorted({s["engine"] for s in sessions}, key=lambda e: ENGINE_ORDER.index(e) if e in ENGINE_ORDER else 99)
    h100_engines = sorted({key[3] for key, _ in h100}, key=lambda e: ENGINE_ORDER.index(e) if e in ENGINE_ORDER else 99)
    shared = [e for e in a100_engines if e in h100_engines]
    grouped = {}
    same = []
    for engine in shared:
        for suite in SUITES:
            mine = [s for s in sessions if s["engine"] == engine and s["suite"] == suite]
            theirs = [summary for key, summary in h100 if key[3] == engine and key[1] == suite]
            if not mine or not theirs:
                continue
            for field, _ in STUDY_SESSION_FIELDS:
                a_values, h_values = study_values(s.get(field) for s in mine), study_values(h100_value(summary, field) for summary in theirs)
                if a_values == h_values:
                    if a_values != [None]:
                        same.append({"field": field, "engine": engine, "suite": suite, "value": a_values[0] if len(a_values) == 1 else a_values})
                    continue
                entry = grouped.setdefault((field, tuple(map(str, a_values)), tuple(map(str, h_values))),
                                           {"field": field, "engines": [], "suites": [], "a100_study": a_values, "h100_campaign": h_values})
                entry["engines"] = sorted(set(entry["engines"]) | {engine}, key=lambda e: ENGINE_ORDER.index(e))
                entry["suites"] = sorted(set(entry["suites"]) | {suite}, key=SUITES.index)
    differences = list(grouped.values())
    # one session per cell against rounds; engine order
    per_cell = Counter((s["suite"], s["scale"], s["engine"]) for s in sessions)
    h100_rounds = len(data.rounds)
    if set(per_cell.values()) != {h100_rounds}:
        differences.append({"field": "sessions_per_cell", "engines": [], "suites": [], "a100_study": sorted(set(per_cell.values())), "h100_campaign": [h100_rounds]})
    a_orders = study_values(" > ".join(order) for order in a100["orders"].values())
    h_orders = study_values(" > ".join(order) for order in data.blocks.values())
    if a_orders != h_orders:
        differences.append({"field": "engine_order", "engines": [], "suites": [], "a100_study": a_orders, "h100_campaign": h_orders})
    # host
    a_host = (a100["hosts"][0]["host"] if a100["hosts"] else {}) or {}
    for field, theirs in (("gpu", (host or {}).get("gpu")), ("vcpus", (host or {}).get("vcpus")), ("host_ram_gib", (host or {}).get("host_ram_gib"))):
        if a_host.get(field) != theirs:
            differences.append({"field": field, "engines": [], "suites": [], "a100_study": [a_host.get(field)], "h100_campaign": [theirs]})
    # Spark GPU configuration: keys recorded on both sides whose values differ (the earlier study recorded only the keys it set)
    a_conf, h_conf = {}, {}
    for s in sessions:
        for k, v in (s.get("spark_conf") or {}).items():
            a_conf.setdefault(k, set()).add(str(v))
    for key, summary in h100:
        for k, v in (summary.get("spark_conf") or {}).items():
            h_conf.setdefault(k, set()).add(str(v))
    conf = {}
    for k in sorted(set(a_conf) & set(h_conf)):
        if RUN_SPECIFIC_CONF.match(k) or a_conf[k] & h_conf[k]:
            continue
        entry = {"a100_study": sorted(a_conf[k]), "h100_campaign": sorted(h_conf[k]), "relative_difference": None}
        try:
            mine, theirs = statistics.median(float(x) for x in a_conf[k]), statistics.median(float(x) for x in h_conf[k])
            entry["relative_difference"] = abs(mine - theirs) / abs(theirs) if theirs else None
        except ValueError:
            pass
        conf[k] = entry
    if conf:
        differences.append({"field": "spark_conf", "engines": ["spark_gpu"], "suites": [], "a100_study": sorted(conf), "h100_campaign": sorted(conf)})
    for d in differences:       # does the difference touch an engine whose ratio is reported? Host and protocol entries (no engine) apply to all.
        d["in_compared_ratio"] = (not d["engines"]) or bool(set(d["engines"]) & set(ratio_engines))
    order = {name: i for i, name in enumerate(STUDY_SUMMARY_ORDER)}
    label_of = {**dict(STUDY_SESSION_FIELDS), "gpu": "GPU", "vcpus": "vCPUs", "host_ram_gib": "host RAM (GiB)", "sessions_per_cell": "sessions per cell"}
    differences.sort(key=lambda d: (order.get(label_of.get(d["field"], d["field"]), 99), d["field"], d["engines"]))

    def show(values):
        return "/".join("not recorded" if v is None else str(v) for v in values)

    phrases = []
    for d in differences:
        name = label_of.get(d["field"], d["field"])
        if d["field"] == "spark_conf":
            parts = []
            for k, v in conf.items():
                parts.append(f"{k.replace('spark.', '')} ({show(v['a100_study']) if len(v['a100_study']) == 1 else 'median'} "
                             + (f"against {show(v['h100_campaign'])}" if len(v['h100_campaign']) == 1 and v["relative_difference"] is None
                                else f"{100 * v['relative_difference']:.1f}% apart" if v["relative_difference"] is not None else "against another value") + ")")
            phrases.append("Spark GPU configuration differs in " + ", ".join(parts))
            continue
        scope = (f" ({', '.join(ENGINE_NAMES.get(e, e) for e in d['engines'])}" + (f", {'/'.join(d['suites'])} suite" if len(d["suites"]) == 1 else "") + ")") if d["engines"] else ""
        if d["field"] == "sessions_per_cell":
            phrases.append(f"{show(d['a100_study'])} session per cell against {show(d['h100_campaign'])} rounds")
        elif d["field"] == "engine_order":
            phrases.append(f"engine order: {len(d['a100_study'])} fixed rotation(s) against {len(d['h100_campaign'])} balanced orders")
        else:
            phrases.append(f"{name}{scope}: {show(d['a100_study'])} against {show(d['h100_campaign'])}" + ("" if d["in_compared_ratio"] else " (engine not in the ratio)"))
    versions = lambda engines, values: study_values(v for e, v in values if e in engines)
    polars_a = versions(("polars", "polars_gpu"), [(s["engine"], s.get("version")) for s in sessions])
    polars_h = versions(("polars", "polars_gpu"), [(key[3], summary.get("version")) for key, summary in h100])
    spark_a = versions(("spark_cpu", "spark_gpu"), [(s["engine"], s.get("version")) for s in sessions])
    spark_h = versions(("spark_cpu", "spark_gpu"), [(key[3], summary.get("version")) for key, summary in h100])
    one = lambda values: values[0] if len(values) == 1 else None
    by_field = {}
    for d in differences:
        by_field.setdefault(d["field"], []).append(d)
    headline = []
    if "version" in by_field:
        headline.append("Polars " + show(by_field["version"][0]["a100_study"]) + " against " + show(by_field["version"][0]["h100_campaign"])
                        + ("" if any(d["in_compared_ratio"] for d in by_field["version"]) else " (CPU engine; not in the ratio)"))
    if "cpu_threads" in by_field:
        headline.append(show(by_field["cpu_threads"][0]["a100_study"]) + " against " + show(by_field["cpu_threads"][0]["h100_campaign"]) + " threads")
    protocol = [name for name in ("sessions_per_cell", "engine_order", "query_cleanup_policy", "failure_policy", "suite_version", "spark_gpu_profile") if name in by_field]
    if protocol:
        recorded_later = [label_of[n] for n in protocol if n in label_of and n not in ("sessions_per_cell", "engine_order")
                          and all(v is None for d in by_field[n] for v in d["a100_study"])]
        headline.append("protocol: " + "; ".join(
            ([f"{show(by_field['sessions_per_cell'][0]['a100_study'])} session per cell against {show(by_field['sessions_per_cell'][0]['h100_campaign'])} rounds"] if "sessions_per_cell" in by_field else [])
            + (["fixed engine rotation per scale against rotated, balanced orders"] if "engine_order" in by_field else [])
            + ([f"{', '.join(dict.fromkeys(recorded_later))} not recorded in the earlier study"] if recorded_later else [])))
    threads_a, threads_h = study_values(s.get("cpu_threads") for s in sessions), study_values(summary.get("protocol", {}).get("cpu_threads") for _, summary in h100)
    runs = {}
    for session in sessions:
        runs.setdefault(session["suite"], {"sessions": {}})["sessions"][(session["suite"], session["scale"], session["engine"])] = session
    earlier_plans = plan_variability_section({"rounds": [{"id": 1, "included": True, "runs": runs}]})
    return {
        "label": label, "previous_study": not a100["campaign_roots"], "roots": a100["roots"],
        "first_session_utc": starts[0] if starts else None, "last_session_utc": starts[-1] if starts else None,
        "engines_present": a100_engines, "engines_compared": sorted(ratio_engines),
        "engines_only_in_h100_campaign": [e for e in h100_engines if e not in a100_engines],
        "gpu": a_host.get("gpu"), "vcpus": a_host.get("vcpus"), "host_ram_gib": a_host.get("host_ram_gib"),
        "polars_version": one(polars_a), "polars_version_h100": one(polars_h), "spark_version": one(spark_a), "spark_version_h100": one(spark_h),
        "cpu_threads": one(threads_a), "cpu_threads_h100": one(threads_h),
        "sessions_per_cell": max(per_cell.values()) if per_cell else None, "rounds_h100": h100_rounds, "arguments": a100["arguments"],
        "differences": differences, "same": same, "spark_conf_differences": conf,
        "spark_conf_keys_recorded": {"a100_study": len(a_conf), "h100_campaign": len(h_conf)},
        "difference_summary": ("; ".join(phrases) + ".") if phrases else None,
        "difference_headline": ("; ".join(headline) + ".") if headline else None, "plan_variability": earlier_plans}


def estimate_a100(data, a100, host=None):
    """GPU-engine A100 -> H100 ratios. CPU engines are deliberately absent: vCPU counts differ (30 vs 26)."""
    meta = {"available": bool(a100 and a100["cells"]), "equivalent_hosts": False, "cpu_engines_compared": False,
            "definition": "time on A100 / time on H100 for the same GPU engine, query and scale; above 1 means the H100 host was faster. "
                          "The A100 study is a single session per cell (no rounds), so the interval reflects the H100 round-to-round "
                          "variation only. Hosts differ in GPU, vCPU count, RAM and software versions: not an equivalent comparison. The A100 side is the previous study's saved "
                          "evidence (see study.label and study.differences), not the A100 campaign.",
            "a100_hosts": (a100 or {}).get("hosts", []), "a100_notes": (a100 or {}).get("notes", [])}
    if not meta["available"]:
        return {"meta": meta, "study": a100_study_block(data, None, host, ()), "ratio": {}}
    view = Data.__new__(Data)
    view.__dict__.update(data.__dict__)
    view.valid = dict(data.valid)
    pairs = []
    for engine in GPU_ENGINES:
        tag = "a100_" + engine
        pairs.append((engine, tag))
        for (suite, scale, query, eng), cell in a100["cells"].items():
            if eng == engine and cell["status"] == "ok" and suite in data.scales and scale in data.scales[suite]:
                view.valid[(suite, scale, query, tag)] = {r: cell["median"] for r in data.block_rounds(suite, scale)}
    ratio = {}
    for engine, tag in pairs:
        spec = {"numerator": tag, "denominator": engine}
        for suite in data.suites:
            for scale in data.scales[suite]:
                if not any((suite, scale, q, tag) in view.valid for q in data.queries[suite]):
                    continue
                per_query = {q: contrast_query(view, suite, scale, q, spec) for q in data.queries[suite]}
                record = contrast_suite_scale(view, suite, scale, spec, per_query)
                record["q"] = per_query
                ratio.setdefault(engine, {}).setdefault(suite, {})[scale] = record
    meta["engines"] = sorted(ratio)
    h100_facts = {}
    for (rnd, suite, scale, engine), session in sorted(data.sessions.items()):
        if engine in GPU_ENGINES:
            h100_facts[engine] = {"version": session.get("version"), "spark_jars": session.get("spark_jars"),
                                  "cudf_polars_version": session.get("cudf_polars_version"),
                                  "spark_gpu_profile": session.get("spark_gpu_profile")}
    meta["engine_facts"] = {"a100": a100["facts"], "h100": h100_facts}
    return {"meta": meta, "study": a100_study_block(data, a100, host, meta["engines"]), "ratio": ratio}


# ------------------------------------------------------------------ host identity (amendments A2/A3)

LADDER = ("A100", "H100", "GH200", "B200")   # GPU-generation order: Ampere, Hopper, Grace-Hopper, Blackwell
DEFAULT_REFERENCE = "H100"
ARM_WORDS = re.compile(r"aarch64|arm64|neoverse|grace|\barm\b", re.I)


def host_architecture(lscpu_text, platform_text, cpu_model):
    """x86_64 / aarch64 from lscpu 'Architecture:', else the platform string, else the CPU model name; None if unknown."""
    match = re.search(r"^Architecture:\s*(\S+)", lscpu_text or "", re.M)
    if match:
        arch = match[1].lower()
    else:
        text = f"{platform_text or ''} {cpu_model or ''}"
        arch = "aarch64" if ARM_WORDS.search(text) else ("x86_64" if re.search(r"x86_64|x86-64|amd64|intel|xeon|epyc", text, re.I) else None)
    return {"arm64": "aarch64", "amd64": "x86_64"}.get(arch, arch)


def first_environment(campaign):
    for entry in campaign["rounds"]:
        if entry["included"]:
            for run in entry["runs"].values():
                if run.get("environment"):
                    return run["environment"]
    return None


def cuda_runtime_text(value):
    """12090 -> '12.9.0' (the integer encoding of cudaRuntimeGetVersion); None when not an integer."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return f"{value // 1000}.{(value % 1000) // 10}.{value % 10}"


def cpu_topology(lscpu, vcpus, cpus_arg):
    """CPU topology as lscpu reports it (inside a VM this is the guest's view, not the physical machine's), and what --cpus threads can occupy.

    smt_threads_per_core, cores_per_socket, sockets, physical_cores (cores_per_socket x sockets), numa_nodes, numa_nodes_with_cpus, hypervisor,
    topology_consistent (threads x cores == CPU(s)), cpus_arg_cores_min (threads packed onto sibling hardware threads: ceil(threads / SMT)),
    cpus_arg_cores_max (threads spread over distinct cores: min(threads, physical cores)), cpus_arg_share_of_vcpus. None for anything unreported.
    """
    def number(pattern):
        match = re.search(pattern, lscpu or "", re.M)
        return int(match[1]) if match else None

    smt, cores, sockets = (number(r"^Thread\(s\) per core:\s+(\d+)"), number(r"^Core\(s\) per socket:\s+(\d+)"), number(r"^Socket\(s\):\s+(\d+)"))
    numa = number(r"^NUMA node\(s\):\s+(\d+)")
    with_cpus = sum(1 for m in re.finditer(r"^NUMA node\d+ CPU\(s\):[ \t]*(\S*)", lscpu or "", re.M) if m[1]) if numa is not None else None
    hypervisor = re.search(r"^Hypervisor vendor:\s+(.+?)\s*$", lscpu or "", re.M)
    physical = cores * sockets if cores and sockets else None
    threads = min(cpus_arg, vcpus) if good_number(cpus_arg) and good_number(vcpus) else (cpus_arg if good_number(cpus_arg) else None)
    return {
        "hypervisor": hypervisor[1] if hypervisor else None,
        "smt_threads_per_core": smt, "cores_per_socket": cores, "sockets": sockets, "physical_cores": physical,
        "numa_nodes": numa, "numa_nodes_with_cpus": with_cpus,
        "topology_consistent": (smt * physical == vcpus) if smt and physical and good_number(vcpus) else None,
        "cpus_arg_cores_min": -(-int(threads) // smt) if threads and smt else None,
        "cpus_arg_cores_max": min(int(threads), physical) if threads and physical else None,
        "cpus_arg_share_of_vcpus": round(cpus_arg / vcpus, 3) if good_number(cpus_arg) and good_number(vcpus) else None,
    }


def host_identity(campaign, data, label=None):
    """Who ran this campaign: GPU, CPU model and architecture, vCPUs, RAM, driver; None for anything not recorded.

    Sources, in order: the environment.json inventory (lscpu, free, nvidia-smi, pip freeze; see
    analyze_results.host_shape), the resource_allocation block, campaign.json provenance.host (run_campaign.py),
    and the sessions' gpu_inventory. The label defaults to the GPU's short name (H100, A100, GH200, B200), then the instance type.
    """
    environment = first_environment(campaign)
    shape = host_shape(environment)
    meta = campaign.get("metadata") or {}
    recorded = ((meta.get("provenance") or {}).get("host")) if isinstance(meta.get("provenance"), dict) else None
    recorded = recorded if isinstance(recorded, dict) else {}
    design = meta.get("design") if isinstance(meta.get("design"), dict) else {}
    # run_campaign.py stores the nvidia-smi query line "name, driver_version, memory.total"
    parts = [p.strip() for p in str(recorded.get("gpu") or "").split(",")]
    smi_name, smi_driver, smi_memory = ((parts + [None, None, None])[:3])
    smi_name, smi_driver, smi_memory = smi_name or None, smi_driver or None, smi_memory or None
    memory_mib = shape["gpu_memory_mib"]
    if memory_mib is None and smi_memory:
        match = re.match(r"(\d+)", smi_memory)
        memory_mib = int(match[1]) if match else None
    if memory_mib is None:
        for session in data.sessions.values():
            total = (session.get("gpu_inventory") or {}).get("memory_total_bytes")
            if good_number(total):
                memory_mib = round(total / 1024 ** 2)
                break
    cpu_model = shape["cpu_model"] or recorded.get("cpu_model")
    lscpu = inventory_output(environment, "lscpu") if environment else ""
    ram = shape["host_ram_gib"]
    if ram is None and good_number(recorded.get("memory_total_bytes")):
        ram = round(recorded["memory_total_bytes"] / 1024 ** 3, 1)
    gpu = shape["gpu"] or smi_name
    instance = recorded.get("instance_type")
    if not gpu:
        for session in data.sessions.values():
            gpu = (session.get("gpu_inventory") or {}).get("name") or gpu
    synthetic = bool((meta or {}).get("synthetic"))
    short = gpu_short_name(gpu) if gpu else None
    flags = design.get("flags") if isinstance(design.get("flags"), dict) else {}
    inputs = (meta.get("provenance") or {}).get("inputs") if isinstance(meta.get("provenance"), dict) else None
    inputs = inputs if isinstance(inputs, dict) else {}
    jar = inputs.get("rapids_jar")
    identity = {
        "label": label or short or instance or "host",
        "instance_type": instance,
        "gpu": gpu, "gpu_short": short, "gpu_count": shape["gpu_count"],
        "gpu_memory_gib": round(memory_mib / 1024, 1) if memory_mib else None,
        "cpu_model": cpu_model,
        "arch": host_architecture(lscpu, recorded.get("platform"), cpu_model),
        "vcpus": shape["vcpus"] if shape["vcpus"] is not None else recorded.get("cpus"),
        "cpus_arg": shape["cpus_arg"] if shape["cpus_arg"] is not None else flags.get("cpus"),
        **cpu_topology(lscpu, shape["vcpus"] if shape["vcpus"] is not None else recorded.get("cpus"),
                       shape["cpus_arg"] if shape["cpus_arg"] is not None else flags.get("cpus")),
        "host_ram_gib": ram,
        "driver_version": shape["driver_version"] or smi_driver,
        "cuda_version": shape["cuda_version"],
        "cuda_runtime_version": cuda_runtime_text(shape.get("cuda_runtime_version")),
        "platform": recorded.get("platform"),
        "packages": shape["packages"],
        "rapids_jar": Path(jar).name if jar else None, "rapids_jar_sha256": inputs.get("rapids_jar_sha256"),
        "preflight": preflight_provenance(campaign)[0],
        "gpu_policy": design.get("gpu_policy"),
        "formal": design.get("formal"),
        "synthetic": synthetic,
        "source": shape["source"] + (["campaign.json:provenance.host"] if recorded else []),
    }
    return identity


def preflight_arg(args, key):
    """preflight-report.json stores its arguments as strings ('250', 'None', 'True'); back to None / bool / number / text."""
    value = (args or {}).get(key)
    if value in (None, "None", ""):
        return None
    if value in ("True", "False", True, False):
        return value in ("True", True)
    try:
        number = float(value)
        return int(number) if number == int(number) else number
    except (TypeError, ValueError):
        return value


def preflight_provenance(campaign):
    """Operational record of the preflight runs around a campaign: ([entries], [notes]).

    Entries: the round preflights (round-<k>/preflight/preflight-report.json) and any host rehearsal preflight kept beside the campaign root
    (<root>/../preflight*/preflight-report.json). Thresholds such as --min-free-gib are operational settings of the host, not design parameters;
    every non-default one is reported in the notes so it cannot go unnoticed.
    """
    root = Path(campaign["root"])
    reports = [(f"{e['label']}/preflight", e["preflight"], f"round-{e['id']}") for e in campaign["rounds"] if e["included"] and e.get("preflight")]
    for directory in sorted(p for p in root.parent.glob("preflight*") if p.is_dir() and p != root):
        try:
            report = read_json(directory / "preflight-report.json")
        except (OSError, ValueError):
            continue
        if isinstance(report, dict):
            reports.append((f"{directory.name}", report, "host rehearsal"))
    entries, notes = [], []
    for name, report, scope in reports:
        args = report.get("args") or {}
        entry = {"scope": scope, "path": name, "verdict": report.get("verdict"), "counts": report.get("counts"), "formal": preflight_arg(args, "formal"),
                 "min_free_gib": preflight_arg(args, "min_free_gib"), "allow_any_gpu": preflight_arg(args, "allow_any_gpu"), "runs": preflight_arg(args, "runs"),
                 "started_at_utc": report.get("started_at_utc"), "completed_at_utc": report.get("completed_at_utc")}
        entries.append(entry)
        if entry["min_free_gib"] is not None:
            notes.append(f"preflight {name} ({scope}) ran with --min-free-gib {entry['min_free_gib']:g}: a recorded operational threshold for the host's free-disk check, "
                         "not a design parameter")
    return entries, notes


def campaign_provenance(campaign):
    """Run provenance recorded by run_campaign.py (opaque block): when, which commit, which design document."""
    meta = campaign.get("metadata") or {}
    prov = meta.get("provenance") if isinstance(meta.get("provenance"), dict) else {}
    git = prov.get("git") if isinstance(prov.get("git"), dict) else {}
    return {"created_utc": prov.get("created_utc"), "design_doc_sha256": prov.get("design_doc_sha256"),
            "design_sha256": meta.get("design_sha256"), "git_commit": git.get("commit"), "git_branch": git.get("branch"),
            "git_dirty_files": len(git.get("dirty_files") or []) if git else None}


def output_agreement_section(campaign):
    """Size of the output disagreement with Polars per (suite, scale, engine): the largest relative difference among the saved numeric values.

    Recomputed from the saved result rows of the accepted rounds. Values are per round and overall, with the query that attains the maximum;
    all of them are inside gate G2 (rel 1e-8) in an accepted round. Integrity evidence, not an estimate.
    """
    cells = {}
    for entry in campaign["rounds"]:
        if not entry["included"]:
            continue
        for run in entry["runs"].values():
            for (suite, scale), agreement in run["agreement"].items():
                for engine, info in ((agreement or {}).get("by_engine") or {}).items():
                    cell = cells.setdefault(suite, {}).setdefault(scale, {}).setdefault(engine, {"rounds": [], "per_round": [], "queries": {}, "compared": 0, "failed": 0})
                    cell["rounds"].append(entry["id"])
                    cell["per_round"].append(info["max_rel_diff"])
                    cell["compared"] += info["compared"]
                    cell["failed"] += info["failed"]
                    for query, value in info["queries"].items():
                        cell["queries"][query] = value if query not in cell["queries"] else max(cell["queries"][query], value)
    out = {}
    for suite, by_scale in cells.items():
        out[suite] = {}
        for scale in sorted(by_scale, key=scale_sort_key):
            out[suite][scale] = {}
            for engine in ENGINE_ORDER:
                cell = by_scale[scale].get(engine)
                if not cell:
                    continue
                present = [v for v in cell["per_round"] if v is not None]
                worst = max(cell["queries"], key=cell["queries"].get) if cell["queries"] else None
                out[suite][scale][engine] = {
                    "compared": cell["compared"], "failed": cell["failed"], "max_rel_diff": max(present) if present else None,
                    "max_rel_diff_query": worst, "rounds": cell["rounds"], "max_rel_diff_per_round": cell["per_round"],
                    "queries": dict(sorted(cell["queries"].items()))}
    return out


# ------------------------------------------------------------------ integrity counts (no timings, no ratios)

def integrity_report(campaign, data, expected_engines):
    """Counts that say whether the data are complete and sound, with no performance content.

    Per round and (suite, scale, engine) session: readable results.json, orchestrator return code, queries by outcome
    (ok / failed by class / incomplete / session-level), trials per ok cell, cleanup barriers. Totals per round and engine.
    """
    meta = campaign.get("metadata") or {}
    design = meta.get("design") if isinstance(meta.get("design"), dict) else {}
    design_suites = design.get("suites") if isinstance(design.get("suites"), dict) else {}
    if design_suites and all(isinstance(v, dict) and v.get("scales") for v in design_suites.values()):
        scales_expected = {s: [scale_name(x) for x in v["scales"]] for s, v in design_suites.items()}     # what the campaign was told to run
    elif isinstance(meta.get("scales"), dict):
        scales_expected = {s: [scale_name(x) for x in v] for s, v in meta["scales"].items()}
    else:
        scales_expected = {s: list(data.scales.get(s, [])) for s in data.suites}
    engines_expected = list(design.get("engines") or meta.get("engines") or expected_engines)
    expected_sessions = {(s, sc, e) for s, scs in scales_expected.items() for sc in scs for e in engines_expected}
    per_round_expected = len(expected_sessions)
    rounds, sessions = [], []
    for entry in campaign["rounds"]:
        record = {"round": entry["id"], "label": entry["label"], "included": entry["included"], "verdict": entry["verdict"],
                  "exclusion": entry["exclusion"], "sessions_expected": per_round_expected, "sessions_found": 0, "sessions_readable": 0,
                  "sessions_nonzero_returncode": 0, "cells": 0, "ok": 0, "failed": {}, "incomplete": 0, "session_failed": 0, "invalid": 0,
                  "sessions_missing": []}
        if entry["included"]:
            for suite, run in entry["runs"].items():
                for (s, scale, engine), summary in sorted(run["sessions"].items(), key=lambda kv: (kv[0][0], scale_sort_key(kv[0][1]), kv[0][2])):
                    cells = [c for (cs, csc, _, ce), c in run["cells"].items() if (cs, csc, ce) == (s, scale, engine)]
                    row = {"round": entry["id"], "suite": s, "scale": scale, "engine": engine, "readable": summary["readable"],
                           "returncode": summary["returncode"], "queries": len(cells), "ok": sum(c["status"] == "ok" for c in cells),
                           "failed": {}, "incomplete": sum(c["status"] == "incomplete" for c in cells),
                           "session_failed": sum(c["status"] == "session_failed" for c in cells), "invalid": sum(c["status"] == "invalid" for c in cells),
                           "trials_per_ok_cell": sorted({len(c["trials"]) for c in cells if c["status"] == "ok"}),
                           "cleanup_records": len(summary["cleanup_records"]),
                           "cleanup_not_passed": sum(1 for r in summary["cleanup_records"] if str(r.get("status", "")).lower() not in PASS_WORDS)}
                    for c in cells:
                        if c["status"] == "failed":
                            row["failed"][c["reason_class"]] = row["failed"].get(c["reason_class"], 0) + 1
                    sessions.append(row)
                    record["sessions_found"] += 1
                    record["sessions_readable"] += bool(row["readable"])
                    record["sessions_nonzero_returncode"] += row["returncode"] not in (None, 0)
                    record["cells"] += row["queries"]
                    record["ok"] += row["ok"]
                    record["incomplete"] += row["incomplete"]
                    record["session_failed"] += row["session_failed"]
                    record["invalid"] += row["invalid"]
                    for k, v in row["failed"].items():
                        record["failed"][k] = record["failed"].get(k, 0) + v
            found = {(row["suite"], row["scale"], row["engine"]) for row in sessions if row["round"] == entry["id"]}
            record["sessions_missing"] = [f"{s}/{sc}/{e}" for s, sc, e in sorted(expected_sessions - found, key=lambda k: (k[0], scale_sort_key(k[1]), k[2]))]
        rounds.append(record)
    by_engine = {}
    for row in sessions:
        agg = by_engine.setdefault(row["engine"], {"sessions": 0, "queries": 0, "ok": 0, "failed": {}, "incomplete": 0, "session_failed": 0, "invalid": 0})
        agg["sessions"] += 1
        for key in ("queries", "ok", "incomplete", "session_failed", "invalid"):
            agg[key] += row[key]
        for k, v in row["failed"].items():
            agg["failed"][k] = agg["failed"].get(k, 0) + v
    cells = integrity_cells(campaign)
    return {"rounds": rounds, "sessions": sessions, "by_engine": by_engine, "expected_engines": engines_expected,
            "expected_scales": scales_expected, "integrity_failures_in_accepted_rounds": cells,
            "trials_per_ok_cell": sorted({n for row in sessions for n in row["trials_per_ok_cell"]}),
            "cleanup_not_passed": sum(row["cleanup_not_passed"] for row in sessions),
            "note": "integrity counts only: no timings, no ratios; a failed query is a coverage outcome (A1) unless its class is error/timeout/session"}


def integrity_markdown(integrity, synthetic=False):
    lines = ["# Integrity counts", ""]
    if synthetic:
        lines += [f"**{WATERMARK}**", ""]
    cells = integrity["integrity_failures_in_accepted_rounds"]
    if cells:
        lines += [f"> **INTEGRITY DISAGREEMENT: {len(cells)} cell(s) of ACCEPTED rounds are integrity failures under amendment A1** "
                  "(unclassified error, timeout, failed or invalid session, incomplete trials, outputs that differ from Polars). A round with such a cell should have failed its gate "
                  "(design section 7) and been repeated or excluded, so the gate and this analysis disagree. Resolve before using any estimate.", ""]
        lines += ["| Round | Suite | Scale | Engine | Query | Kind | Reason |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {c['round']} | {c['suite']} | {c['scale']} | {ENGINE_NAMES.get(c['engine'], c['engine'])} | {c['query']} | {c['kind']} | "
                  f"{c['reason'].replace('|', ' ')} |" for c in cells[:40]]
        lines += ["" if len(cells) <= 40 else f"... and {len(cells) - 40} more (integrity.json).", ""]
    lines += [integrity["note"] + ".", "",
              "| Round | Included | Gate verdict | Sessions found / expected | Readable | Non-zero return | Queries | OK | Failed (class: n) | Incomplete | Session-level | Invalid |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in integrity["rounds"]:
        failed = ", ".join(f"{k}: {v}" for k, v in sorted(r["failed"].items())) or DASH
        lines.append(f"| {r['label']} | {'yes' if r['included'] else 'no: ' + r['exclusion']} | {r['verdict'] or DASH} | {r['sessions_found']} / {r['sessions_expected']} | "
                     f"{r['sessions_readable']} | {r['sessions_nonzero_returncode']} | {r['cells']} | {r['ok']} | {failed} | {r['incomplete']} | {r['session_failed']} | {r['invalid']} |")
    missing = [f"{r['label']}: {', '.join(r['sessions_missing'])}" for r in integrity["rounds"] if r["sessions_missing"]]
    if missing:
        lines += ["", "Expected sessions with no results: " + "; ".join(missing) + "."]
    lines += ["", "| Engine | Sessions | Queries | OK | Failed (class: n) | Incomplete | Session-level | Invalid |", "|---|---|---|---|---|---|---|---|"]
    for engine in ENGINE_ORDER:
        a = integrity["by_engine"].get(engine)
        if a:
            failed = ", ".join(f"{k}: {v}" for k, v in sorted(a["failed"].items())) or DASH
            lines.append(f"| {ENGINE_NAMES[engine]} | {a['sessions']} | {a['queries']} | {a['ok']} | {failed} | {a['incomplete']} | {a['session_failed']} | {a['invalid']} |")
    lines += ["", f"Measured trials per OK cell: {integrity['trials_per_ok_cell']}. Cleanup barriers not passed: {integrity['cleanup_not_passed']}.", ""]
    if integrity.get("operational_notes"):
        lines += ["## Operational parameters recorded by preflight (not design parameters)", ""] + [f"- {note}" for note in integrity["operational_notes"]] + [""]
    if integrity.get("meta_notes"):
        lines += ["## Sources, gaps and disagreements of the meta values", ""] + [f"- {note}" for note in integrity["meta_notes"]] + [""]
    return "\n".join(lines)


def write_integrity(integrity, output, synthetic=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "integrity.json").write_text(json.dumps(integrity, indent=1, allow_nan=False) + "\n")
    (output / "INTEGRITY.md").write_text(integrity_markdown(integrity, synthetic))


# ------------------------------------------------------------------ meta.* integrity facts (paper contract)

DESIGN_DOC = Path(__file__).resolve().parent / "docs" / "H100_EXPERIMENT_DESIGN.md"
INTEGRITY_REASONS = {"error", "timeout"}          # A1: an unclassified error or an engine timeout fails the round


def sha256_of(path):
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def format_utc(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if value else None


def input_gate_verdict(campaign, data, root, inputs_reports=()):
    """(verdict, source): 'RESULT: OK' | 'RESULT: FAILED' | 'RESULT: INCOMPLETE' from verify_generated_inputs reports, else None.

    The inputs may have been verified in several runs (the H100 campaign used one report for SF1-30 and one for SF100-300), so the verdict is
    taken over all reports together: OK needs every report complete and OK and the union of their verified scales to cover every scale of
    the campaign; any complete report that failed gives FAILED; anything else INCOMPLETE.
    Reports are searched in this order: --inputs-report files, the paths recorded in campaign.json (as-is, then by file name inside the
    campaign root or next to it), <root>/inputs-verification.json. A recorded sha256 that does not match the file found makes the report
    unusable. Without any report the preflight 'inputs_report' check of every included round is used; with neither the verdict is None.
    """
    root = Path(root)
    prov = ((campaign.get("metadata") or {}).get("provenance") or {})
    recorded = (prov.get("inputs") or {}).get("inputs_reports_sha256") or {}
    candidates = [(Path(p), None) for p in inputs_reports]
    for name, digest in recorded.items():
        for path in (Path(name), root / Path(name).name, root.parent / Path(name).name):
            if path.is_file():
                candidates.append((path, digest))
                break
    default = root / "inputs-verification.json"
    if not candidates and default.is_file():
        candidates.append((default, None))
    needed = {float(scale_value(sc)) for sc in {sc for scs in data.scales.values() for sc in scs} if scale_value(sc) is not None}
    reports, used = [], []
    for path, digest in candidates:
        try:
            if digest and sha256_of(path) != digest:
                campaign["warnings"].append(f"{path}: sha256 differs from campaign.json; report ignored")
                continue
            report = read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(report, dict) and report.get("tool") == "verify_generated_inputs":
            reports.append(report)
            used.append(str(path))
    if reports:
        covered = {float(x) for report in reports for x in report.get("scales_verified") or []}
        if any(r.get("result") == "FAILED" for r in reports):
            verdict = "RESULT: FAILED"
        elif all(r.get("result") == "OK" and r.get("ok") is True and r.get("complete") is True for r in reports) and needed <= covered:
            verdict = "RESULT: OK"
        else:
            verdict = "RESULT: INCOMPLETE"
        missing_files = [Path(n).name for n in recorded if not any(Path(u).name == Path(n).name for u in used)]
        if missing_files:
            campaign["warnings"].append(f"meta.input_gate: recorded verification report(s) not found next to the campaign: {', '.join(missing_files)}")
        return verdict, "; ".join(used)
    checked = [next((c for c in (entry["preflight"] or {}).get("checks", []) if c.get("id") == "inputs_report"), None)
               for entry in campaign["rounds"] if entry["included"]]
    if checked and all(c is not None and str(c.get("status")).upper() == "PASS" for c in checked):
        if recorded:
            campaign["warnings"].append("meta.input_gate: the verification report(s) recorded in campaign.json (" + ", ".join(Path(n).name for n in recorded)
                                        + ") were not found; the verdict comes from the preflight 'inputs_report' check, which read them on the host. "
                                          "Copy them next to the campaign root (or pass --inputs-report) to verify them here.")
        return "RESULT: OK", "preflight inputs_report check of every included round"
    return None, None


def amendment_ids(text):
    """Amendment ids ('A1', 'A2', ...) in the order of the '### A<n>' headings of section 11 (Amendments); None without that section."""
    match = re.search(r"^## 11\.[^\n]*\n(.*?)(?=^## \d|\Z)", text, re.M | re.S)
    return re.findall(r"^### (A\d+)\b", match[1], re.M) if match else None


def git_output(args, cwd, timeout=30):
    """stdout (bytes) of a read-only git command, or None when git is missing, the command fails or the directory is not a repository."""
    try:
        result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def committed_doc_versions(doc_path):
    """[(commit, sha256, text)] of the design document across its git history, newest first ([] if git or the history is unavailable)."""
    doc = Path(doc_path).resolve()
    top = git_output(["rev-parse", "--show-toplevel"], doc.parent if doc.parent.is_dir() else Path.cwd())
    if not top:
        return []
    top = Path(top.decode().strip()).resolve()
    try:
        relative = doc.relative_to(top).as_posix()
    except ValueError:
        return []
    log = git_output(["log", "--format=%H", "--", relative], top)
    versions = []
    for commit in (log or b"").decode().split():
        blob = git_output(["show", f"{commit}:{relative}"], top)
        if blob is not None:
            import hashlib
            versions.append((commit, hashlib.sha256(blob).hexdigest(), blob.decode("utf-8", errors="replace")))
    return versions


def design_version(recorded_sha256, doc_path):
    """The version of the design document a campaign started under, identified by the sha256 recorded in its campaign.json.

    The sha is looked up among the committed versions of the document (git log / git show, read-only); the oldest commit holding that exact
    content is the design commit. Returns {commit, amendments, ids, later, matches, notes}: amendments counts section 11 headings of THAT version,
    `later` lists the amendment ids present in the current document but not in that version, `matches` is True when the sha resolves to a
    committed version. Only a sha that matches no commit produces a warning in `notes`.
    """
    doc = Path(doc_path)
    out = {"commit": None, "amendments": None, "ids": None, "later": None, "matches": None, "notes": []}
    current = doc.read_text() if doc.is_file() else None
    current_ids = amendment_ids(current) if current is not None else None
    if not recorded_sha256:
        out["notes"].append("meta.design_doc_matches_campaign is null: campaign.json recorded no design_doc_sha256, so the version the campaign started under is unknown")
        if current_ids is not None:
            out.update(amendments=len(current_ids), ids=current_ids, later=[])
        return out
    versions = committed_doc_versions(doc) if doc.is_file() else []
    hits = [v for v in versions if v[1] == recorded_sha256]
    if hits:
        commit, _, text = hits[-1]                       # oldest commit with this content
        ids = amendment_ids(text)
        out.update(commit=commit, ids=ids, amendments=len(ids) if ids is not None else None, matches=True,
                   later=[i for i in current_ids if i not in ids] if (current_ids is not None and ids is not None) else None)
        return out
    working = current is not None and sha256_of(doc) == recorded_sha256
    if working and not versions:                         # no git history to consult, but the document is byte-identical to the one hashed
        out.update(ids=current_ids, amendments=len(current_ids) if current_ids is not None else None, later=[], matches=True)
        out["notes"].append("meta.design_commit is null: no git history of the design document is available; the version was identified by the working-tree file's sha256")
        return out
    out["matches"] = False
    out["notes"].append(f"meta.design_doc_matches_campaign is false: the design document sha256 {recorded_sha256[:12]} recorded in campaign.json matches no commit of {doc}"
                        + (" (it equals the uncommitted working-tree file)" if working else ""))
    if working:
        out.update(ids=current_ids, amendments=len(current_ids) if current_ids is not None else None, later=[])
    return out


def preflight_counts(entry):
    """What the round's preflight report says it checked: saved outputs compared with Polars (G2) and verified_gc barriers (G5).

    Read out of the check details ('all N records match the reference engine', 'N verified_gc barriers passed'); None unless the
    report exists and its verdict is pass, so a count is never taken from a gate that did not pass.
    """
    report = entry.get("preflight")
    if not report or str(report.get("verdict")).lower() not in PASS_WORDS:
        return None
    outputs = barriers = 0
    for check in report.get("checks", []):
        if str(check.get("status")).upper() != "PASS":
            continue
        cid, detail = str(check.get("id", "")), str(check.get("detail", ""))
        if cid.endswith(":agreement") and (m := re.match(r"all (\d+) records match", detail)):
            outputs += int(m[1])
        if cid.endswith(":contract") and (m := re.search(r"(\d+) verified_gc barriers passed", detail)):
            barriers += int(m[1])
    return {"outputs_compared": outputs, "cleanup_barriers": barriers}


def status_times(root):
    """(started, updated) UTC datetimes from run_campaign.py's status.json, when the campaign is complete; else (None, None)."""
    try:
        status = read_json(Path(root) / "status.json")
    except (OSError, ValueError):
        return None, None
    if not isinstance(status, dict) or status.get("state") != "complete":
        return None, None
    return parse_utc(status.get("started_utc")), parse_utc(status.get("updated_utc"))


def integrity_cells(campaign):
    """Cells of accepted rounds that A1 classifies as integrity failures (they fail a round, so none should be here)."""
    out = []
    for entry in campaign["rounds"]:
        if not entry["included"]:
            continue
        for run in entry["runs"].values():
            for (suite, scale, query, engine), cell in sorted(run["cells"].items()):
                kind = cell["reason_class"] if cell["status"] == "failed" and cell["reason_class"] in INTEGRITY_REASONS else (
                    cell["status"] if cell["status"] in ("session_failed", "invalid", "incomplete") else None)
                if kind:
                    out.append({"round": entry["id"], "suite": suite, "scale": scale, "engine": engine, "query": query, "kind": kind,
                                "reason": cell["reason"][:160]})
            for (suite, scale), agreement in sorted(run["agreement"].items()):
                for engine, count in sorted(((agreement or {}).get("failed_by_engine") or {}).items()):
                    out.append({"round": entry["id"], "suite": suite, "scale": scale, "engine": engine, "query": f"{count} record(s)",
                                "kind": "output_disagreement", "reason": f"saved outputs differ from polars beyond rel {REL_TOL:g} / abs {ABS_TOL:g} (gate 2)"})
    return out


def campaign_meta(campaign, data, host, root, inputs_reports=(), design_doc=None, notes=None):
    """The meta.* facts the paper prints, each taken from campaign artifacts (never typed in); None only where they cannot be determined.

    Gate-reported counts (outputs compared, verified_gc barriers, spark_gpu plan checks) come from round-<k>/preflight/preflight-report.json
    of accepted rounds; the same quantities are recomputed from results.json and a disagreement is logged. Times come from schedule.json /
    results.json (fallback: status.json, campaign.json), the input gate from the verify_generated_inputs report, the amendment count from
    section 11 of the design document. Every null, fallback and disagreement is appended to `notes` (and so ends up in the warnings).
    """
    notes = notes if notes is not None else []
    included = [e for e in campaign["rounds"] if e["included"]]
    prov = campaign_provenance(campaign)
    sessions = [(e, key, summary) for e in included for run in e["runs"].values() for key, summary in run["sessions"].items()]
    attempts = {}
    for entry in campaign["rounds"]:
        attempts.setdefault(entry["id"], []).append(entry)

    def unknown(key, why):
        notes.append(f"meta.{key} is null: {why}")

    if not included:
        why = "no round passed its gates"
        for key in ("sessions_completed", "measured_trials", "outputs_compared", "outputs_failed", "max_rel_diff", "cleanup_barriers_passed",
                    "campaign_start_utc", "campaign_end_utc", "spark_gpu_plans_checked"):
            unknown(key, why)
    # saved outputs against Polars (gate G2): gate count first, results.json recomputation as evidence and fallback
    agreements = [a for e in included for run in e["runs"].values() for a in run["agreement"].values() if a]
    recomputed = sum(a["compared"] for a in agreements)
    gate_counts = [preflight_counts(e) for e in included]
    gated = bool(included) and all(g is not None for g in gate_counts)
    outputs_compared = sum(g["outputs_compared"] for g in gate_counts) if gated else (recomputed or None)
    if included and not gated:
        notes.append("meta.outputs_compared / cleanup_barriers_passed: not every accepted round has a passing preflight report; "
                     "values are recomputed from results.json" + ("" if recomputed else " (and no saved outputs carry rows, so outputs_compared is null)"))
    if gated and recomputed and recomputed != outputs_compared:
        notes.append(f"DISAGREEMENT meta.outputs_compared: preflight reports {outputs_compared}, results.json rows give {recomputed}")
    if included and outputs_compared is None:
        unknown("outputs_compared", "neither a passing preflight report nor saved output rows are available")
    failed_rows = sum(a["failed"] for a in agreements)
    g2_pass = bool(included) and all(str(((e["preflight"] or {}).get("gates") or {}).get("G2_output_agreement", {}).get("verdict", "")).lower() in PASS_WORDS
                                     for e in included)
    outputs_failed = failed_rows if recomputed else (0 if g2_pass else None)
    if included and outputs_failed is None:
        unknown("outputs_failed", "no saved output rows and no passing G2 verdict in the preflight reports")
    max_rel = max((a["max_rel_diff"] for a in agreements if a["max_rel_diff"] is not None), default=None)
    if included and max_rel is None:
        unknown("max_rel_diff", "no saved output rows to compare (preflight reports only the pass/fail of the tolerance)")
    # verified_gc barriers (gate G5)
    from_results = sum(1 for _, _, s in sessions for r in s["cleanup_records"] + s["failed_cleanup_records"] if str(r.get("status", "")).lower() in PASS_WORDS)
    barriers = sum(g["cleanup_barriers"] for g in gate_counts) if gated else (from_results if sessions else None)
    if gated and from_results != barriers:
        notes.append(f"DISAGREEMENT meta.cleanup_barriers_passed: preflight reports {barriers}, results.json cleanup records give {from_results}")
    # spark_gpu plan rule (gate G4)
    spark = [(e, key, summary) for e, key, summary in sessions if key[2] == "spark_gpu"]
    plans = 0
    for entry, (suite, scale, _), summary in spark:
        check = next((c for c in (entry["preflight"] or {}).get("checks", []) if c.get("id") == f"{suite}/{scale}:spark_gpu:plans"), None)
        if check is None or str(check.get("status")).upper() != "PASS":
            plans = None
            unknown("spark_gpu_plans_checked", f"no passing preflight plan check for spark_gpu {suite}/{scale} in {entry['label']}")
            break
        plans += summary["plan_records"]
    # campaign start / end
    starts, ends = [], []
    for _, _, summary in sessions:
        start, done = parse_utc(summary.get("start_utc")), parse_utc(summary.get("completed_at_utc"))
        if start:
            starts.append(start)
            ends.append(start + timedelta(seconds=summary["process_wall_seconds"]) if summary.get("process_wall_seconds") else (done or start))
        elif done:
            ends.append(done)
    started, updated = status_times(root)
    if included and not starts:
        starts = [started or parse_utc(prov["created_utc"])]
        notes.append("meta.campaign_start_utc: no session carries start_utc; taken from status.json / campaign.json")
        starts = [t for t in starts if t]
    if included and not ends and updated:
        ends = [updated]
        notes.append("meta.campaign_end_utc: no session end time; taken from status.json")
    if included and not starts:
        unknown("campaign_start_utc", "no session start_utc, status.json or campaign.json time")
    if included and not ends:
        unknown("campaign_end_utc", "no session end time or status.json")
    version = design_version(prov["design_doc_sha256"], design_doc or DESIGN_DOC)
    notes.extend(version["notes"])
    amendments, doc_matches = version["amendments"], version["matches"]
    if amendments is None:
        unknown("amendments", f"the design version the campaign started under could not be determined ({design_doc or DESIGN_DOC})")
    if version["commit"] is None and not any(n.startswith("meta.design_commit is null") for n in version["notes"]):
        unknown("design_commit", "the recorded design document sha256 resolves to no commit")
    gate, gate_source = input_gate_verdict(campaign, data, root, inputs_reports)
    if gate is None:
        unknown("input_gate", "no verify_generated_inputs report found (--inputs-report, campaign.json paths, <root>/inputs-verification.json) "
                              "and no passing preflight inputs_report check in every accepted round")
    for key, value in (("gpu_name", host["gpu"]), ("cpu_model", host["cpu_model"]), ("driver_version", host["driver_version"])):
        if included and value is None:
            unknown(key, "not recorded in environment.json or campaign.json")
    if prov["git_commit"] is None and included:
        unknown("run_commit", "run_campaign.py recorded no git commit (no git checkout on the host); the design commit was resolved from the design document's sha256")
    failures = integrity_cells(campaign)
    if failures:
        notes.append(f"{len(failures)} cell(s) of accepted rounds are integrity failures under A1 (see INTEGRITY.md): the gate and this analysis disagree")
    if version["later"] is None and amendments is not None:
        unknown("later_amendments", "the current design document could not be read or has no section 11")
    elif version["later"] is None:
        unknown("later_amendments", "the version the campaign started under is unknown")
    return {
        "gpu_name": host["gpu"], "cpu_model": host["cpu_model"], "driver_version": host["driver_version"],
        "rounds_completed": len(included), "rounds_repeated": sum(1 for v in attempts.values() if len(v) > 1),
        "rounds_gated": sum(1 for e in included if e["verdict"] in PASS_WORDS),
        "input_gate": gate, "input_gate_source": gate_source,
        "amendments": amendments, "later_amendments": version["later"], "design_doc_matches_campaign": doc_matches,
        "design_commit": version["commit"], "design_commit_short": version["commit"][:7] if version["commit"] else None,
        "design_doc_sha256": prov["design_doc_sha256"], "design_sha256": prov["design_sha256"],
        "run_commit": prov["git_commit"], "run_commit_dirty_files": prov["git_dirty_files"],
        "sessions_completed": sum(1 for _, _, s in sessions if s["completed"]) if sessions else None,
        "measured_trials": sum(s["measured_trials"] for _, _, s in sessions) if sessions else None,
        "outputs_compared": outputs_compared if included else None,
        "outputs_failed": outputs_failed if included else None,
        "max_rel_diff": max_rel,
        "spark_gpu_plans_checked": plans if included else None,
        "cleanup_barriers_passed": barriers if included else None,
        "campaign_start_utc": format_utc(min(starts)) if starts else None,
        "campaign_end_utc": format_utc(max(ends)) if ends else None,
        "integrity_failures_in_accepted_rounds": len(failures),
    }


# ------------------------------------------------------------------ assembly

def design_metadata(campaign):
    """Selected campaign.json fields: the legacy top-level ones and the run_campaign.py 'design' block."""
    meta = campaign.get("metadata") or {}
    out = {key: meta.get(key) for key in ("seed", "design_seed", "engines", "scales", "suites", "sequences", "synthetic", "host", "started_at_utc")
           if key in meta}
    design = meta.get("design") if isinstance(meta.get("design"), dict) else {}
    for key in ("seed", "letters", "sequences", "engines", "flags", "gpu_policy", "formal", "expect_gpu_unsupported"):
        if key in design:
            out.setdefault(key, design[key])
    return out


def analyze(root, a100_roots=(), allow_ungated=False, synthetic=False, energy=True, gpu_index=0, label=None, extras=None, inputs_reports=(),
            design_doc=None, side_study=True):
    """Full pre-registered analysis of one campaign tree -> result dict (the content of stats.json).

    One call analyzes one host's campaign; label names the host in cross-host comparisons (default: the recorded
    instance type or the GPU's short name). When extras is a dict it receives {"integrity": integrity_report(...)}:
    counts only, kept out of stats.json so that file's schema stays fixed.
    """
    campaign = load_campaign(root, allow_ungated)
    synthetic = bool(synthetic or (campaign["metadata"] or {}).get("synthetic"))
    if energy:
        for entry in campaign["rounds"]:
            for run in entry["runs"].values():
                attach_energy(run, gpu_index)
                attach_telemetry(run, gpu_index)
    data = Data(campaign)
    contrasts = estimate_contrasts(data)
    medians = estimate_medians(data)
    coverage, discrepancies = coverage_matrix(data)
    totals, legacy_ratios = legacy_totals(data)
    side_campaign, side_info = load_side_study(root, allow_ungated) if side_study else (None, {
        "available": False, "rounds": [], "versions": [], "warnings": ["the side study was ignored (--no-side-study)"]})
    side = estimate_side_study(side_campaign, side_info)
    exploratory = estimate_exploratory(data, side_campaign, side_info)
    host = host_identity(campaign, data, label)
    a100 = estimate_a100(data, load_a100(a100_roots), host)
    n_rounds = len(data.rounds)
    included = [e for e in campaign["rounds"] if e["included"]]
    excluded = [e for e in campaign["rounds"] if not e["included"]]
    integrity = integrity_report(campaign, data, data.present_engines())
    if extras is not None:
        extras["integrity"] = integrity
    meta_notes = []
    meta = campaign_meta(campaign, data, host, root, inputs_reports, design_doc, meta_notes)
    operational_notes = preflight_provenance(campaign)[1]
    campaign["warnings"] += meta_notes + operational_notes
    integrity["meta_notes"] = meta_notes
    integrity["operational_notes"] = operational_notes
    result = {
        "schema_version": SCHEMA_VERSION,
        "synthetic": synthetic,
        "watermark": WATERMARK if synthetic else None,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": host,
        "meta": meta,
        "campaign": {
            "root": str(Path(root)), "design": design_metadata(campaign), "provenance": campaign_provenance(campaign),
            "rounds_found": [e["label"] for e in campaign["rounds"]],
            "rounds_included": data.rounds,
            "rounds_included_labels": [e["label"] for e in included],
            "rounds_excluded": [{"round": e["id"], "label": e["label"], "reason": e["exclusion"], "gate_reasons": e["reasons"]} for e in excluded],
            "n_rounds": n_rounds, "df": max(n_rounds - 1, 0), "level": LEVEL,
            "t_critical": t_critical(n_rounds - 1) if n_rounds >= 2 else None,
            "engines": list(data.present_engines()), "suites": list(data.suites),
            "scales": {s: list(v) for s, v in data.scales.items()}, "queries": {s: list(data.queries[s]) for s in data.suites},
            "trials_per_cell": sorted({s["runs"] for s in data.sessions.values() if s.get("runs") is not None}),
            "energy": {"gpu_csv_runs": sum(1 for e in included for r in e["runs"].values() if (r.get("energy_info") or {}).get("gpu_csv")),
                       "tz_offset_seconds": sorted({(r.get("energy_info") or {}).get("tz_offset_seconds") for e in included
                                                    for r in e["runs"].values()} - {None})},
            "warnings": campaign["warnings"],
        },
        "contrasts": {name: {**{k: v for k, v in spec.items()},
                             "definition": f"{spec['numerator']} / {spec['denominator']} (slower side in the numerator for a speedup above 1)"}
                      for name, spec in ALL_CONTRASTS.items()},
        **contrasts,
        "medians": medians,
        "fastest": estimate_fastest(data, medians),
        "cpu_reference": cpu_reference_check(data, medians),
        "legacy": {"totals": totals, "ratios": legacy_ratios,
                   "note": "sum of the per-query medians (A100-study comparability); arithmetic, never combined with geometric means"},
        "variability": variability(data),
        "order": order_effects(data),
        "crossover": {"c4": crossover_for(data, contrasts["c4"]), "c4_strict": crossover_for(data, contrasts["c4_strict"])},
        "unstable_coverage": unstable_coverage_section(data),
        "output_agreement": output_agreement_section(campaign),
        "plan_variability": plan_variability_section(campaign),
        "coverage": {"matrix": coverage, "discrepancies": discrepancies,
                     "predeclared_unsupported": sorted(f"{e}:{q}" for e, q in PREDECLARED_UNSUPPORTED)},
        "secondary": {"gpu_memory": gpu_memory(data), "process": process_times(data), "cleanup": cleanup_costs(data),
                      "energy": energy_proxy(data), "gpu_telemetry": gpu_telemetry_section(campaign, data)},
        "side_study": side,
        "exploratory": exploratory,
        "a100_vs_h100": a100,
    }
    return result


# ------------------------------------------------------------------ flat keys

def flatten(value, prefix=""):
    """Dotted-key view of a nested dict; lists and scalars are leaves."""
    flat = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            flat.update(flatten(item, path) if isinstance(item, dict) and item else {path: item})
    else:
        flat[prefix] = value
    return flat


def lookup(stats, dotted):
    """Resolve a dotted key against the nested stats dict (raises KeyError)."""
    node = stats
    for part in dotted.split("."):
        node = node[part]
    return node


# ------------------------------------------------------------------ synthetic campaign (for drafting and tests)

SYNTH_SCALES = {"baseline": ("1", "10", "30", "100", "300"), "complex": ("30", "100", "300")}
SYNTH_WEIGHT = {"q01": 1.0, "q03": 1.6, "q06": 0.35, "q10": 1.9, "q12": 0.8, "q14": 0.5,
                "cx_q09": 3.0, "cx_q13": 1.4, "cx_semi_anti": 2.0, "cx_window": 2.6, "cx_distinct": 2.2, "cx_self_join": 3.4}
SYNTH_GPU_AFFINITY = {"q01": 1.3, "q03": 0.9, "q06": 1.1, "q10": 0.8, "q12": 1.0, "q14": 0.7,
                      "cx_q09": 1.2, "cx_q13": 1.0, "cx_semi_anti": 0.8, "cx_window": 1.0, "cx_distinct": 1.4, "cx_self_join": 0.6}
SYNTH_SPARK_AFFINITY = {"q01": 1.2, "q03": 1.0, "q06": 0.8, "q10": 1.1, "q12": 0.9, "q14": 1.0,
                        "cx_q09": 1.3, "cx_q13": 1.0, "cx_semi_anti": 0.9, "cx_window": 1.1, "cx_distinct": 1.0, "cx_self_join": 0.8}
SYNTH_VERSION_RATIO = {"baseline": 0.92, "complex": 1.08}
SYNTH_A100_SLOWDOWN = 1.7
SYNTH_H100_BYTES = 81559 * 1024 * 1024
SYNTH_GIB = 1024 ** 3


# Synthetic host profiles (amendments A2/A3). All numbers are invented for the generator; time factors multiply the
# H100 truth per engine (GPU engines follow the GPU generation, CPU engines the CPU), and "oom" lists the cells whose
# working set exceeds the device (a coverage outcome, A1). CPU model names are labelled SYNTHETIC on purpose.
SYNTH_HOSTS = {
    "H100": {"instance": "gpu_1x_h100_sxm5", "gpu": "NVIDIA H100 80GB HBM3", "gpu_mib": 81559, "vcpus": 26, "ram_gib": 225.0,
             "cpu": "SYNTHETIC x86_64 CPU (H100 host)", "arch": "x86_64", "driver": "580.0.1", "cuda": "13.0",
             "power_limit_w": 700, "max_sm_mhz": 1980,
             "topology": (2, 13, 1, 1, "KVM"),
             "gpu_time": {"polars_gpu": 1.0, "spark_gpu": 1.0}, "cpu_time": 1.0,
             "oom": {("complex", "300", "cx_self_join", "polars_gpu")}},
    "B200": {"instance": "gpu_1x_b200_sxm6", "gpu": "NVIDIA B200", "gpu_mib": 183359, "vcpus": 26, "ram_gib": 360.0,
             "cpu": "SYNTHETIC x86_64 CPU (B200 host)", "arch": "x86_64", "driver": "580.0.1", "cuda": "13.0",
             "power_limit_w": 1000, "max_sm_mhz": 1965,
             "topology": (2, 13, 1, 1, "KVM"),
             "gpu_time": {"polars_gpu": 0.55, "spark_gpu": 0.70}, "cpu_time": 0.95, "oom": set()},
    "A100": {"instance": "gpu_1x_a100_sxm4", "gpu": "NVIDIA A100-SXM4-40GB", "gpu_mib": 40960, "vcpus": 30, "ram_gib": 200.0,
             "cpu": "SYNTHETIC x86_64 CPU (A100 host)", "arch": "x86_64", "driver": "570.0.1", "cuda": "12.8",
             "power_limit_w": 400, "max_sm_mhz": 1410,
             "topology": (1, 1, 30, 1, "KVM"),
             "gpu_time": {"polars_gpu": 1.60, "spark_gpu": 1.45}, "cpu_time": 1.10,
             "oom": {("baseline", "300", "q03", "polars_gpu"), ("baseline", "300", "q10", "polars_gpu"),
                     ("complex", "100", "cx_self_join", "polars_gpu"), ("complex", "300", "cx_self_join", "polars_gpu"),
                     ("complex", "300", "cx_q09", "polars_gpu")}},
    "GH200": {"instance": "gpu_1x_gh200", "gpu": "NVIDIA GH200 480GB", "gpu_mib": 97871, "vcpus": 64, "ram_gib": 432.0,
              "cpu": "SYNTHETIC aarch64 CPU (GH200 host)", "arch": "aarch64", "driver": "580.0.1", "cuda": "13.0",
              "power_limit_w": 700, "max_sm_mhz": 1980,
             "topology": (1, 64, 1, 9, None),
              "gpu_time": {"polars_gpu": 0.85, "spark_gpu": 0.88}, "cpu_time": 1.30, "oom": set()},
}


# Added by the --synthetic command line only (never by make_synthetic_campaign's defaults): a timeout in one round (partial, an integrity
# failure), an out-of-memory in one round only (unstable coverage, A1) and a cell that failed for an unclassified reason in every round,
# so every coverage class shows up in F4.
SYNTH_SHOWCASE_FAILURES = (
    (2, "baseline", "30", "q10", "spark_gpu", ("TimeoutError", "action exceeded the timeout")),
    (3, "baseline", "100", "q06", "polars_gpu", ("MemoryError", "std::bad_alloc: out_of_memory: RMM failure at allocation of 41 GiB")),   # unstable coverage
    *((r, "complex", "300", "cx_distinct", "spark_gpu", ("RuntimeError", "CUDA error: illegal memory access (synthetic)")) for r in (1, 2, 3, 4)),
)


SYNTH_SHOWCASE_PLANS = (("complex", "30", "cx_q09"),)   # command line only: Spark GPU trials of this cell end in one of two adaptive plans


def synthetic_lscpu(profile):
    """lscpu text of a synthetic host: threads x cores x sockets must equal the vCPU count; extra NUMA nodes (GH200) hold no CPUs."""
    threads, cores, sockets, numa, hypervisor = profile["topology"]
    lines = [f"Architecture:  {profile['arch']}", f"CPU(s):  {profile['vcpus']}", f"Model name:  {profile['cpu']}", f"Thread(s) per core:  {threads}",
             f"Core(s) per socket:  {cores}", f"Socket(s):  {sockets}"] + ([f"Hypervisor vendor:  {hypervisor}"] if hypervisor else []) + [
             f"NUMA node(s):  {numa}", f"NUMA node0 CPU(s):  0-{profile['vcpus'] - 1}"] + [f"NUMA node{k} CPU(s):  " for k in range(1, numa)]
    return "\n".join(lines) + "\n"


def synthetic_plan_text(query, variant, serial):
    """A Spark GPU adaptive plan file: final plan, then initial plan. Ids change with `serial` (as between real executions); variant 1 has
    one more shuffle (a hash exchange on the join key read through a local shuffle reader), the same join operators and 120 partitions."""
    i = 1000 + 37 * serial
    left = [f"      :- GpuFileGpuScan parquet [k#{i + 4}L,v#{i + 2}] Format: Parquet, Location: InMemoryFileIndex[file:/data/{query}]"]
    if variant:
        left = [f"      :- GpuShuffleCoalesce 1073741824, [loreId={i + 10}]",
                f"      :  +- GpuCustomShuffleReader local",
                f"      :     +- ShuffleQueryStage {serial % 9 + 2}",
                f"      :        +- GpuColumnarExchange gpuhashpartitioning(k#{i + 4}L, 120, Murmur3Mode), ENSURE_REQUIREMENTS, [plan_id={i + 11}], [loreId={i + 12}]",
                f"      :           +- GpuFileGpuScan parquet [k#{i + 4}L,v#{i + 2}] Format: Parquet, Location: InMemoryFileIndex[file:/data/{query}]"]
    lines = ["AdaptiveSparkPlan isFinalPlan=true", "+- == Final Plan ==", f"   GpuColumnarToRow false, [loreId={i}]",
             f"   +- GpuBroadcastHashJoin [k#{i + 4}L], [d#{i + 8}L], Inner, GpuBuildRight, false, [loreId={i + 9}]", *left,
             f"      +- BroadcastQueryStage {serial % 3}", f"         +- GpuBroadcastExchange HashedRelationBroadcastMode(List(input[0, bigint, false]),false), [plan_id={i + 13}], [loreId={i + 14}]",
             "+- == Initial Plan ==", f"HashAggregate(keys=[k#{i + 1}], functions=[sum(v#{i + 2})])",
             f"+- SortMergeJoin [k#{i + 4}L], [d#{i + 8}L], Inner"]
    return "\n".join(lines) + "\n"


def synth_host_factor(host, engine, query):
    """Injected time factor of an engine on a host relative to H100, with a deterministic per-query wobble (+-7%)."""
    profile = SYNTH_HOSTS[host]
    if host == DEFAULT_REFERENCE:
        return 1.0
    base = profile["gpu_time"].get(engine, profile["cpu_time"])
    wobble = 0.93 + 0.14 * (sum(ord(c) * (i + 1) for i, c in enumerate(f"{host}/{engine}/{query}")) % 101) / 100.0
    return base * wobble


def synth_truth(suite, scale, query, host=DEFAULT_REFERENCE):
    """Known per-engine times (seconds) and the injected ratios for one cell; None where the engine cannot run it."""
    f = float(scale)
    polars = 0.04 + SYNTH_WEIGHT[query] * 0.08 * f ** 0.9
    c1 = SYNTH_GPU_AFFINITY[query] * (0.30 if suite == "baseline" else 0.22) * f ** 0.5
    spark_cpu = polars * (1.6 + 4.0 / f ** 0.35)
    c2 = (0.45 if suite == "baseline" else 0.40) * f ** 0.42 * SYNTH_SPARK_AFFINITY[query]
    times = {"polars": polars, "polars_gpu": polars / c1, "spark_cpu": spark_cpu, "spark_gpu": spark_cpu / c2}
    times = {engine: t * synth_host_factor(host, engine, query) for engine, t in times.items()}
    if ("polars_gpu", query) in PREDECLARED_UNSUPPORTED:
        times["polars_gpu"] = None
    for engine in ENGINE_ORDER:
        if (suite, scale, query, engine) in SYNTH_HOSTS[host]["oom"]:
            times[engine] = None  # out of memory on this host's device
    return times


def synth_ratios(times):
    """C1..C4 (and C4 strict) implied by a truth time dict."""
    t = times
    gpu = [x for x in (t["polars_gpu"], t["spark_gpu"]) if x is not None]
    return {"c1": t["polars"] / t["polars_gpu"] if t["polars_gpu"] else None,
            "c2": t["spark_cpu"] / t["spark_gpu"],
            "c3": t["spark_gpu"] / t["polars_gpu"] if t["polars_gpu"] else None,
            "c4": t["polars"] / min(gpu),
            "c4_strict": t["polars"] / min(gpu) if t["polars_gpu"] else None}


def _synth_stamp(epoch, tz_hours):
    return (datetime.fromtimestamp(epoch + tz_hours * 3600, tz=timezone.utc)).strftime("%Y/%m/%d %H:%M:%S.%f")[:-3]


def make_synthetic_campaign(root, seed=1, *, rounds=4, scales=None, round_sigma=0.04, query_sigma=0.03, trial_sigma=0.02,
                            position_effect=0.01, failed_gate_rounds=(), gpu_interval=5.0, tz_hours=0,
                            side_study=True, a100=True, time_scale=1.0, extra_failures=(), host=DEFAULT_REFERENCE, spark_events=True, plan_variants=()):
    """Write a realistic fake campaign tree under root with KNOWN injected ratios; returns the truth dict.

    Everything is marked synthetic (campaign.json, synthetic_truth.json); nothing resembles a measurement. The
    layout follows the runner contract: round-<k>/gates.json, round-<k>/<suite>/{sf*/<engine>/results.json,
    schedule.json, environment.json, gpu.csv}, side-study/round-<k>/{polars-1.44.2,polars-2.0.0}/<suite>/ and
    a100/<root>/ run roots. Sigma 0 and position_effect 0 give noise-free data whose estimators recover the truth
    exactly. failed_gate_rounds get verdict "fail" and corrupted timings (x10) to prove they are excluded.
    host selects a SYNTH_HOSTS profile (identity block, device memory, per-engine time factors versus H100 and its
    out-of-memory cells); every host is its own campaign with its own noise stream. Every Spark GPU trial gets a plan file; trials of the
    (suite, scale, query) cells in plan_variants alternate between two final plans (and two event-log stage profiles) without changing a time.
    """
    if host not in SYNTH_HOSTS:
        raise ValueError(f"unknown synthetic host {host!r}; choose from {sorted(SYNTH_HOSTS)}")
    profile = SYNTH_HOSTS[host]
    root = Path(root)
    if root.exists():
        if (root / "synthetic_truth.json").is_file():
            shutil.rmtree(root)
        elif any(root.iterdir()):
            raise FileExistsError(f"{root} exists and is not a synthetic campaign; refusing to overwrite")
    root.mkdir(parents=True)
    rng = random.Random(seed if host == DEFAULT_REFERENCE else seed * 1009 + sum(ord(c) for c in host))
    scales = scales or SYNTH_SCALES
    engines = list(ENGINE_ORDER)
    base_epoch = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc).timestamp()
    trial_sd = {"polars": trial_sigma, "polars_gpu": trial_sigma * 2, "spark_cpu": trial_sigma * 1.5, "spark_gpu": trial_sigma * 2.5}
    gpu_bytes = profile["gpu_mib"] * 1024 * 1024
    host_env = {"arguments": {"cpus": 26, "driver_memory": "96g"},
                "resource_allocation": {"gpu": profile["gpu"], "gpu_count": 1, "vcpus": profile["vcpus"],
                                        "host_ram_gib_approx": profile["ram_gib"]},
                "started_at_utc": datetime.fromtimestamp(base_epoch, tz=timezone.utc).isoformat(),
                "inventory": [
                    {"command": ["lscpu"], "output": synthetic_lscpu(profile)},
                    {"command": ["free", "-b"], "output": "              total        used\nMem:  "
                                                         f"{int(profile['ram_gib'] * 1024 ** 3)}  1000\n"},
                    {"command": ["python", "-c", "probe"], "output": json.dumps({"distributions": {}, "imports": {}, "cuda_runtime_version": 12090,
                                                                               "cuda_driver_version": 13000 if profile["cuda"].startswith("13") else 12080})},
                    {"command": ["nvidia-smi", "-q"], "output": f"Driver Version  : {profile['driver']}\nCUDA Version  : {profile['cuda']}\n"
                                                               f"Attached GPUs  : 1\n    Product Name  : {profile['gpu']}\n    FB Memory Usage\n"
                                                               f"        Total  : {profile['gpu_mib']} MiB\n"
                                                               f"    GPU Power Readings\n        Current Power Limit  : {profile['power_limit_w']:.2f} W\n"
                                                               f"    Max Clocks\n        Graphics  : {profile['max_sm_mhz']} MHz\n        SM  : {profile['max_sm_mhz']} MHz\n"}]}
    device_cap = 76.0 * profile["gpu_mib"] / 81559.0   # GiB a process can actually hold: the H100 figure scaled by device size
    a100_env = {"arguments": {"cpus": 30, "driver_memory": "96g"},
                "resource_allocation": {"gpu": "NVIDIA A100-SXM4-40GB", "gpu_count": 1, "vcpus": 30, "host_ram_gib_approx": 216}}

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n")

    def noise(sigma):
        return math.exp(rng.gauss(0.0, sigma)) if sigma else 1.0

    def result_rows(suite, scale, query, engine):
        """Saved query output of one record: identical across engines up to a relative 3e-10 on the floats (inside gate G2's 1e-8)."""
        seed = sum(ord(c) for c in f"{suite}/{scale}/{query}")
        base = 1000.0 + seed % 977 + float(scale)
        eps = {"polars": 0.0, "polars_gpu": 2e-10, "spark_cpu": 1e-10, "spark_gpu": 3e-10}[engine]
        return [[query, base * (1.0 + eps), 100 + seed % 13], [query + "_b", base * 2.0 * (1.0 + eps), 7]]

    def session_json(engine, suite, scale, times, start_epoch, wall_parts, corrupted, version=None, a100_host=False,
                     failures=None, position=1):
        """results.json for one session; times: {query: true seconds or None}. Returns (session, duration seconds)."""
        queries = list(times)
        failures = failures or {}
        session_noise = noise(round_sigma)
        pool_mark = 0.0
        records, errors, cleanups, cursor = [], [], [], start_epoch + wall_parts["ready"]
        order_rng = random.Random(rng.random())
        measured_order = []
        for trial in range(1, 4):
            order = list(queries)
            order_rng.shuffle(order)
            measured_order.append(order)
        schedule = [(q, "warmup", 0) for q in queries] + [(q, "measured", t) for t in range(1, 4) for q in measured_order[t - 1]]
        failed = set()
        for query, phase, trial in schedule:
            if query in failed:
                continue
            true = times[query]
            failure = failures.get(query)
            if true is None or failure:
                if query not in failed:
                    kind, message = failure or (("NotImplementedError", "inline regex flag (?s) is not supported by cudf-polars")
                                                if query == "cx_q13" else
                                                ("NotImplementedError", "cum_count over a window is not supported on the GPU")
                                                if query == "cx_window" else
                                                ("MemoryError", "std::bad_alloc: out_of_memory: RMM failure at allocation of 41 GiB"))
                    errors.append({"query": query, "phase": phase, "trial": trial, "type": kind, "message": message,
                                   "failed_at_utc": datetime.fromtimestamp(cursor, tz=timezone.utc).isoformat()})
                    failed.add(query)
                continue
            seconds = true * session_noise * noise(query_sigma) * (1.0 + position_effect * (position - 1))
            if phase == "warmup":
                seconds *= 1.6
            else:
                seconds *= noise(trial_sd[engine])
            if corrupted:
                seconds *= 10.0
            seconds *= time_scale
            record = {"query": query, "phase": phase, "trial": trial, "seconds": seconds,
                      "started_at_utc": datetime.fromtimestamp(cursor, tz=timezone.utc).isoformat(),
                      "ended_at_utc": datetime.fromtimestamp(cursor + seconds, tz=timezone.utc).isoformat(),
                      "peak_process_tree_rss_bytes": int(2e9 + 3e7 * float(scale) * SYNTH_WEIGHT[query]),
                      "gpu_execution_class": {"polars": "cpu_engine", "spark_cpu": "cpu_engine", "polars_gpu": "gpu_engine_strict",
                                              "spark_gpu": "gpu_operators_present"}[engine]}
            record["rows"] = result_rows(suite, scale, query, engine)
            if engine == "spark_gpu":
                record.update(plan=f"plans/{query}-{phase}{trial}.txt", gpu_plan_observed=True)
            if engine in GPU_ENGINES:
                if engine == "polars_gpu":
                    # cudf-polars' RMM async pool retains freed blocks, so the sampled value is the session's high-water mark so far
                    pool_mark = max(pool_mark, min(0.6 + 0.24 * float(scale) * SYNTH_WEIGHT[query], device_cap))
                    peak = pool_mark * SYNTH_GIB
                else:
                    # the RAPIDS pool is reserved at plugin start: one size per session, the same in every action (the draw below keeps the random stream unchanged)
                    spare = rng.random() if float(scale) > 10 else 0.0
                    peak = (40.0 if float(scale) <= 10 else 62.0 + 10.0 * random.Random(f"{host}/{suite}/{scale}").random()) * device_cap / 76.0 * SYNTH_GIB
                record["peak_gpu_memory_bytes"] = int(peak * (1.0 + (0.01 if engine == "polars_gpu" else 0.002) * rng.random()))
            records.append(record)
            cursor += seconds
            if suite == "complex":
                clean = (0.4 + 0.8 * rng.random()) if engine.startswith("spark") else 0.04 + 0.03 * rng.random()
                cleanups.append({"query": query, "phase": phase, "trial": trial, "policy": "verified_gc", "status": "passed",
                                 "seconds": clean, "scratch_before": {"files": 12, "bytes": 1 << 20},
                                 "scratch_after": {"files": 0, "bytes": 0}})
                cursor += clean
        duration = cursor - start_epoch + wall_parts["shutdown"]
        session = {
            "engine": engine, "suite": suite, "suite_version": "complex-v2" if suite == "complex" else "baseline-v1",
            "queries": queries, "scale_factor": float(scale), "runs": 3, "manifest_sha256": "0" * 64,
            "started_at_utc": datetime.fromtimestamp(start_epoch, tz=timezone.utc).isoformat(),
            "completed_at_utc": datetime.fromtimestamp(cursor, tz=timezone.utc).isoformat(),
            "cpu_threads": 30 if a100_host else 26, "version": version or {"polars": "1.44.2", "polars_gpu": "1.44.2",
                                                                          "spark_cpu": "3.5.9", "spark_gpu": "3.5.9"}[engine],
            "session_and_table_setup_seconds": wall_parts["setup"], "launch_to_engine_ready_seconds": wall_parts["ready"],
            "polars_execution_engine": {"polars": "streaming", "polars_gpu": "gpu"}.get(engine),
            "records": records, "query_errors": errors,
            "query_cleanup_policy": "verified_gc" if suite == "complex" else "none", "cleanup_records": cleanups,
            "query_execution_status": "partial" if errors else "complete",
        }
        if engine == "polars_gpu":
            session["polars_gpu"] = {"executor": "streaming", "raise_on_fail": True, "device": 0, "cudf_polars_version": "26.10.2"}
        if engine == "spark_gpu":
            session["spark_gpu_profile"] = "a100-parity"
            session["spark_conf"] = {"spark.jars": "/opt/jars/rapids-4-spark_2.12-26.08.1.jar"}
        if engine in GPU_ENGINES:
            session["gpu_inventory"] = {"name": "NVIDIA A100-SXM4-40GB" if a100_host else profile["gpu"],
                                        "memory_total_bytes": 40 * 1024 ** 3 if a100_host else gpu_bytes}
        return session, duration

    def plan_variant(record, suite, scale, round_index):
        """1 for the trials of a plan_variants cell that end in the longer plan (measured trials, alternating by round and trial), else 0."""
        return int(record.get("phase") == "measured" and (suite, str(scale), record["query"]) in {tuple(map(str, v)) for v in plan_variants}
                   and (round_index + int(record.get("trial") or 0)) % 2 == 0)

    def write_spark_plans(directory, session, suite, scale, round_index):
        for serial, record in enumerate(session["records"]):
            if record.get("plan"):
                target = directory / record["plan"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(synthetic_plan_text(record["query"], plan_variant(record, suite, scale, round_index), serial + 13 * round_index))

    def write_spark_events(path, session, scale, variants=None):
        """A Spark event log with RAPIDS task accumulators: each action gets tasks launched inside its window; the largest reported device memory
        depends on the query (and a little on the trial); a setup task outside every window and tasks without the accumulator are included on purpose."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps({"Event": "SparkListenerLogStart", "Spark Version": "3.5.9"})]
        task_id = 0

        def task(launch_ms, accumulables):
            nonlocal task_id
            task_id += 1
            return json.dumps({"Event": "SparkListenerTaskEnd", "Stage ID": task_id % 7, "Task Info": {
                "Task ID": task_id, "Launch Time": int(launch_ms), "Finish Time": int(launch_ms) + 40, "Accumulables": accumulables}})

        def accumulator(name, value):
            return {"ID": task_id, "Name": name, "Update": value, "Value": value, "Internal": False, "Count Failed Values": True}

        first = datetime.fromisoformat(session["records"][0]["started_at_utc"]).timestamp() * 1000 if session["records"] else 0
        lines.append(task(first - 5000, [accumulator("gpuMaxDeviceMemoryBytes", "1.00MB (1048576 bytes)")]))        # session setup, outside every action
        for record in session["records"]:
            start = datetime.fromisoformat(record["started_at_utc"]).timestamp() * 1000
            end = datetime.fromisoformat(record["ended_at_utc"]).timestamp() * 1000
            base = (0.15 + 0.03 * float(scale) * SYNTH_WEIGHT[record["query"]]) * SYNTH_GIB * (1.0 + 0.02 * (record["trial"] or 0))
            for k, fraction in enumerate((0.5, 1.0, 0.8, 0.3)):
                value = int(base * fraction)
                lines.append(task(start + (end - start) * (0.1 + 0.2 * k), [accumulator("gpuTime", "12 ms"), accumulator(
                    "gpuMaxDeviceMemoryBytes", f"{value / 2 ** 20:.2f}MB ({value} bytes)")]))
            lines.append(task(start + (end - start) * 0.95, [accumulator("gpuTime", "3 ms")]))                      # a task without the device-memory accumulator
            extra = (variants or {}).get(id(record), 0)
            for k in range(3 + extra):                                                                                # the plan with the extra shuffle has one more stage
                lines.append(json.dumps({"Event": "SparkListenerStageCompleted", "Stage Info": {
                    "Stage ID": 1000 * len(lines) + k, "Stage Name": "collect at worker.py:515", "Number of Tasks": 30 + 88 * (k == 2 and extra),
                    "Submission Time": int(start + (end - start) * (0.05 + 0.2 * k)), "Completion Time": int(start + (end - start) * (0.2 + 0.2 * k)),
                    "Accumulables": [{"ID": k, "Name": "internal.metrics.shuffle.write.bytesWritten", "Value": 1_000_000 + 5_000_000_000 * (k == 1 and extra), "Internal": True}]}}))
        path.write_text("\n".join(lines) + "\n")

    def write_run(run_dir, order_by_scale, suite, truth_for, *, start_epoch, round_index, corrupted=False, version=None,
                  a100_host=False, engines_only=None, with_gpu_csv=True, scales_override=None):
        """One run_experiment-style output root; returns (end epoch, per-session windows)."""
        schedule, windows, cursor = [], [], start_epoch
        action_windows = []                 # (start, end, GPU engine) of every measured action: the synthetic gpu.csv shows the GPU busy for 70% of each
        wall = {}
        for scale in (scales_override or scales[suite]):
            order = order_by_scale(scale)
            for position, engine in enumerate(order, start=1):
                if engines_only and engine not in engines_only:
                    continue
                parts = {"ready": {"polars": 2.0, "polars_gpu": 9.0, "spark_cpu": 11.0, "spark_gpu": 19.0}[engine] * time_scale,
                         "setup": {"polars": 0.4, "polars_gpu": 5.0, "spark_cpu": 9.0, "spark_gpu": 16.0}[engine] * time_scale,
                         "shutdown": 1.0}
                failures = {q: f for (r, su, sc, q, e), f in ((tuple(k[:5]), k[5]) for k in extra_failures)
                            if (r, su, sc, e) == (round_index, suite, scale, engine)}
                session, duration = session_json(engine, suite, scale, truth_for(scale, engine), cursor, parts, corrupted,
                                                 version=version, a100_host=a100_host, failures=failures, position=position)
                write(run_dir / f"sf{scale}" / engine / "results.json", session)
                if engine in GPU_ENGINES:
                    action_windows.extend((datetime.fromisoformat(r["started_at_utc"]).timestamp(), datetime.fromisoformat(r["ended_at_utc"]).timestamp(), engine)
                                          for r in session["records"] if r["phase"] == "measured")
                if engine == "spark_gpu":
                    write_spark_plans(run_dir / f"sf{scale}" / engine, session, suite, scale, round_index)
                if engine == "spark_gpu" and spark_events and not a100_host:
                    write_spark_events(run_dir / f"sf{scale}" / f"{engine}-events" / f"local-{int(cursor * 1000)}", session, scale,
                                       {id(r): plan_variant(r, suite, scale, round_index) for r in session["records"]})
                write(run_dir / f"sf{scale}" / "manifest.json", {"scale_factor": float(scale), "tables": {}})
                schedule.append({"scale": scale, "engine": engine, "command": ["synthetic"], "preheat": {"bytes": 0, "seconds": 0.1},
                                 "start_utc": datetime.fromtimestamp(cursor, tz=timezone.utc).isoformat(),
                                 "process_wall_seconds": duration, "returncode": 0, "status": "complete"})
                windows.append((cursor, cursor + duration, engine))
                cursor += duration + 15.0 * time_scale
        write(run_dir / "schedule.json", schedule)
        write(run_dir / "environment.json", a100_env if a100_host else host_env)
        if with_gpu_csv and windows:
            lines = ["timestamp, index, utilization.gpu [%], utilization.memory [%], memory.used [MiB], power.draw [W], temperature.gpu, "
                     "clocks.current.sm [MHz], clocks.current.memory [MHz]"]
            t, end = windows[0][0] - 5.0, windows[-1][1] + 2.0
            starts = sorted(action_windows)
            import bisect
            while t <= end:
                engine = next((e for b, f, e in windows if b <= t <= f), None)
                watts = {None: 74.0, "polars": 78.0, "spark_cpu": 79.0, "polars_gpu": 235.0, "spark_gpu": 185.0}[engine]
                watts += rng.gauss(0.0, 3.0)
                k = bisect.bisect_right(starts, (t, float("inf"), "")) - 1
                active = None
                if k >= 0 and starts[k][0] <= t <= starts[k][1]:
                    begin, finish, which = starts[k]
                    busy = (t - begin) < 0.7 * (finish - begin)          # the GPU is busy for the first 70% of an action, idle in the rest
                    active = (85 if which == "polars_gpu" else 55) if busy else 3
                lines.append(f"{_synth_stamp(t, tz_hours)}, 0, {0 if active is None else active} %, {0 if active is None else active // 5} %, 512 MiB, {watts:.2f} W, 40, "
                             f"{345 if not active or active < 10 else 1700} MHz, 2619 MHz")
                t += gpu_interval
            (run_dir / "gpu.csv").write_text("\n".join(lines) + "\n")
        return cursor, windows

    truth = {"synthetic": True, "seed": seed, "params": {"rounds": rounds, "round_sigma": round_sigma, "query_sigma": query_sigma,
                                                         "trial_sigma": trial_sigma, "position_effect": position_effect,
                                                         "failed_gate_rounds": list(failed_gate_rounds)},
             "times": {}, "ratios": {}, "suite_ratios": {}, "side_study": {}, "a100": {}}
    for suite in SUITES:
        truth["times"][suite], truth["ratios"][suite], truth["suite_ratios"][suite] = {}, {}, {}
        for scale in scales[suite]:
            key = "sf" + scale
            truth["times"][suite][key], truth["ratios"][suite][key] = {}, {}
            per_contrast = {c: [] for c in ("c1", "c2", "c3", "c4", "c4_strict")}
            for query in DEFAULT_QUERIES[suite]:
                times = synth_truth(suite, scale, query, host)
                truth["times"][suite][key][query] = times
                ratios = synth_ratios(times)
                truth["ratios"][suite][key][query] = ratios
                for c, v in ratios.items():
                    if v is not None:
                        per_contrast[c].append(v)
            # suite-level truth: geometric mean over the queries available for that contrast
            truth["suite_ratios"][suite][key] = {c: geomean(v) for c, v in per_contrast.items()}

    def truth_for(suite):
        return lambda scale, engine: {q: truth["times"][suite]["sf" + scale][q][engine] for q in DEFAULT_QUERIES[suite]}

    letters = {k: DESIGN_SEQUENCES[((k - 1) % 4) + 1] for k in range(1, rounds + 1)}
    cursor = base_epoch
    for k in range(1, rounds + 1):
        round_dir = root / f"round-{k}"
        order = [DESIGN_LETTERS[c] for c in letters[k]]
        suites = list(SUITES) if k % 2 == 1 else list(reversed(SUITES))
        for suite in suites:
            cursor, _ = write_run(round_dir / suite, lambda scale, order=order: order, suite, truth_for(suite), start_epoch=cursor,
                                  round_index=k, corrupted=k in failed_gate_rounds)
            cursor += 600.0 * time_scale
        failed = k in failed_gate_rounds
        write(round_dir / "gates.json", {"round": k, "verdict": "fail" if failed else "pass", "synthetic": True,
                                         "reasons": ["gate 2: output agreement failed (synthetic)"] if failed else [],
                                         "preflight": {"report": str(round_dir / "preflight" / "preflight-report.json"),
                                                       "verdict": "fail" if failed else "pass"}})
        checks = [{"id": "inputs_report", "status": "PASS", "detail": f"{sum(len(v) for v in scales.values())} verified dataset manifests from 1 report(s)",
                   "gates": ["other"]}]
        for suite in SUITES:
            for scale in scales[suite]:
                for engine in engines:
                    tag = f"{suite}/sf{scale}:{engine}"
                    session = json.loads((round_dir / suite / f"sf{scale}" / engine / "results.json").read_text())
                    checks.append({"id": f"{tag}:plans", "status": "FAIL" if failed and engine == "spark_gpu" else "PASS",
                                   "detail": "every plan has GPU scan and aggregate/join operators" if engine == "spark_gpu" else "plan files present",
                                   "gates": ["G4_spark_gpu_plans" if engine == "spark_gpu" else "other"]})
                    if engine != "polars":
                        compared = sum(1 for r in session["records"] if "rows" in r)
                        checks.append({"id": f"{tag}:agreement", "status": "FAIL" if failed else "PASS",
                                       "detail": f"all {compared} records match the reference engine within rel 1e-08 / abs 1e-06 (integers exact)",
                                       "gates": ["G2_output_agreement"]})
                    if suite == "complex":
                        checks.append({"id": f"{tag}:contract", "status": "PASS", "gates": ["G5_verified_gc"],
                                       "detail": f"{len(session['cleanup_records'])} verified_gc barriers passed"})
        write(round_dir / "preflight" / "preflight-report.json", {
            "tool": "preflight", "format": 2, "verdict": "fail" if failed else "pass", "ok": not failed, "synthetic": True, "checks": checks,
            "counts": {"PASS": len(checks), "FAIL": int(failed), "WARN": 0},
            "args": {"formal": "False", "runs": "3", "min_free_gib": "None", "allow_any_gpu": str(host != DEFAULT_REFERENCE)},
            "gates": {g: {"verdict": "fail" if failed else "pass"} for g in ("G1_sessions_complete", "G2_output_agreement", "G4_spark_gpu_plans", "G5_verified_gc")}})
    if side_study:
        pair_orders = {1: ("polars-1.44.2", "polars-2.0.0"), 2: ("polars-2.0.0", "polars-1.44.2"),
                       3: ("polars-2.0.0", "polars-1.44.2"), 4: ("polars-1.44.2", "polars-2.0.0")}
        for k in range(1, rounds + 1):
            sdir = root / "side-study" / f"round-{k}"
            for label in pair_orders[((k - 1) % 4) + 1]:
                for suite in SUITES:
                    def side_truth(scale, engine, suite=suite, label=label):
                        factor = 1.0 if label == "polars-1.44.2" else SYNTH_VERSION_RATIO[suite]
                        return {q: truth["times"][suite]["sf" + scale][q]["polars"] * factor for q in DEFAULT_QUERIES[suite]}
                    cursor, _ = write_run(sdir / label / suite, lambda scale: ["polars"], suite, side_truth, start_epoch=cursor,
                                          round_index=k, version=label.split("-", 1)[1], engines_only=["polars"], with_gpu_csv=False)
            write(sdir / "gates.json", {"round": k, "verdict": "pass", "synthetic": True})
        for suite in SUITES:
            truth["side_study"][suite] = {"sf" + scale: SYNTH_VERSION_RATIO[suite] for scale in scales[suite]}
    if a100:
        a100_roots = {"production-20261025": ("baseline", ("1", "10", "30")), "large-sf100-20261025": ("baseline", ("100",)),
                      "large-sf300-20261025": ("baseline", ("300",)), "complex-formal-v2-20261025": ("complex", scales["complex"])}
        cursor_a = base_epoch - 86400 * 30
        for name, (suite, a_scales) in a100_roots.items():
            def a_truth(scale, engine, suite=suite):
                times = truth["times"][suite]["sf" + scale]
                return {q: (times[q][engine] * SYNTH_A100_SLOWDOWN if engine in ("spark_gpu",) else times[q][engine] * 1.15)
                        for q in DEFAULT_QUERIES[suite]}
            legacy_order = ["spark_cpu", "polars", "spark_gpu"]
            cursor_a, _ = write_run(root / "a100" / name, lambda scale: legacy_order, suite, a_truth, start_epoch=cursor_a,
                                    round_index=0, a100_host=True, engines_only=legacy_order, with_gpu_csv=False,
                                    scales_override=a_scales)
        truth["a100"] = {"spark_gpu_slowdown": SYNTH_A100_SLOWDOWN}
    # injected host / H100 time ratios (the cross-host truth): per query, and per suite as a geometric mean over the
    # queries both hosts can run
    truth["host"] = host
    truth["vs_reference"] = {}
    for suite in SUITES:
        truth["vs_reference"][suite] = {}
        for scale in scales[suite]:
            key = "sf" + scale
            per_engine = {e: {} for e in engines}
            for query in DEFAULT_QUERIES[suite]:
                reference = synth_truth(suite, scale, query, DEFAULT_REFERENCE)
                for e in engines:
                    own = truth["times"][suite][key][query][e]
                    per_engine[e][query] = own / reference[e] if own is not None and reference[e] is not None else None
            truth["vs_reference"][suite][key] = {
                "query": per_engine,
                "suite": {e: geomean([v for v in per_engine[e].values() if v is not None]) for e in engines}}
    write(root / "status.json", {"format": 1, "state": "complete", "message": "all rounds accepted", "synthetic": True,
                                 "started_utc": datetime.fromtimestamp(base_epoch, tz=timezone.utc).isoformat(),
                                 "updated_utc": datetime.fromtimestamp(cursor, tz=timezone.utc).isoformat()})
    verified = sorted({float(sc) for v in scales.values() for sc in v})
    write(root / "inputs-verification.json", {"tool": "verify_generated_inputs", "format": 2, "complete": True, "ok": True, "result": "OK", "synthetic": True,
                                              "scales_requested": verified, "scales_verified": verified,
                                              "datasets": [{"scale_factor": sc, "ok": True, "checks": [{"id": "new_manifest", "sha256": "0" * 64}]} for sc in verified]})
    write(root / "campaign.json", {
        "synthetic": True, "watermark": WATERMARK, "seed": 20261029, "synthetic_seed": seed, "engines": engines,
        "sequences": {str(k): [DESIGN_LETTERS[c] for c in v] for k, v in letters.items()},
        "scales": {s: list(v) for s, v in scales.items()}, "suites": list(SUITES),
        "host": f"synthetic {host} / {profile['vcpus']} vCPU",
        # the layout run_campaign.py writes: design block and provenance (host identity per amendment A2)
        "design": {"seed": 20261029, "letters": dict(DESIGN_LETTERS), "sequences": [DESIGN_SEQUENCES[k] for k in sorted(DESIGN_SEQUENCES)],
                   "engines": engines, "flags": {"cpus": 26, "driver_memory": "96g", "runs": 3},
                   "gpu_policy": "h100_required" if host == DEFAULT_REFERENCE else "any_gpu (amendment A2 hosts)", "formal": False,
                   "expect_gpu_unsupported": sorted({q for _, q in PREDECLARED_UNSUPPORTED})},
        "design_sha256": "0" * 64,
        "provenance": {"created_utc": datetime.fromtimestamp(base_epoch, tz=timezone.utc).isoformat(),
                       "design_doc_sha256": (committed_doc_versions(DESIGN_DOC) or [(None, sha256_of(DESIGN_DOC) if DESIGN_DOC.is_file() else None, "")])[0][1],
                       "git": {"commit": "0" * 40, "branch": "synthetic", "dirty_files": []},
                       "inputs": {"manifest_sha256": {}, "rapids_jar": f"/opt/jars/rapids-4-spark_2.12-26.08.1{'-cuda12-arm64' if profile['arch'] == 'aarch64' else ''}.jar",
                                  "rapids_jar_sha256": ("1" if profile["arch"] == "aarch64" else "0") * 64, "inputs_reports_sha256": {str(root / "inputs-verification.json"): sha256_of(root / "inputs-verification.json")}},
                       "host": {"instance_type": profile["instance"], "hostname": f"synthetic-{host.lower()}",
                                "platform": f"Linux-synthetic-{profile['arch']}", "cpus": profile["vcpus"], "cpu_model": profile["cpu"],
                                "memory_total_bytes": int(profile["ram_gib"] * 1024 ** 3),
                                "gpu": f"{profile['gpu']}, {profile['driver']}, {profile['gpu_mib']} MiB"}}})
    write(root / "synthetic_truth.json", truth)
    return truth


# ------------------------------------------------------------------ cross-host comparison (amendments A2 / A3)

CROSS_SCHEMA_VERSION = "1.0"
IDENTITY_FIELDS = ("gpu", "gpu_memory_gib", "cpu_model", "arch", "vcpus", "cpus_arg", "smt_threads_per_core", "physical_cores", "numa_nodes", "hypervisor", "host_ram_gib", "driver_version", "cuda_version", "cuda_runtime_version", "rapids_jar")
CROSS_DEFINITION = (
    "ratio = time on the host / time on the reference host (below 1: the host is faster). Per (suite, scale, query, engine) the "
    "estimate is the ratio of the hosts' geometric-mean round medians. The interval is a Welch-type interval on the log scale: "
    "rounds are independent runs on different machines, so the two sets of per-round log times are treated as unpaired samples "
    "(Welch-Satterthwaite df, two-sided t critical value). The suite-level ratio is the same estimator applied to the per-round "
    "geometric mean over the queries that have a valid median in every round on both hosts. Descriptive only: no hypothesis tests, "
    "no pooling across hosts, and every per-host estimate keeps its own campaign's rounds.")
CROSS_CAVEATS = (
    "Hosts are separate campaigns (amendments A2/A3); data are never pooled and the reference host remains the primary analysis.",
    "A GPU-engine ratio confounds GPU generation, CPU, host memory, driver and software build; it does not isolate the GPU.",
    "CPU-engine ratios reflect the CPU model, architecture, core and SMT layout and memory system (all hosts ran with the same --cpus, which occupies a different number of physical cores where SMT differs); the GPU is idle.",
    "Rounds are not paired across hosts, so the Welch interval ignores any common drift; with 4 rounds per host the df is at most 6.",
    "A cell that failed on one host (coverage outcome, A1) is excluded from that host's ratios and listed under coverage_differences.",
)


def ladder_sorted(labels):
    """Labels in GPU-generation order (A100, H100, GH200, B200); unknown labels keep their given order after the known ones."""
    labels = list(labels)
    return [x for x in LADDER if x in labels] + [x for x in labels if x not in LADDER]


def welch_summary(host_values, reference_values, level=LEVEL):
    """Ratio of geometric means host / reference with a Welch-type interval on the log scale.

    Both arguments are positive per-round values of independent samples. With fewer than two values on either side there
    is an estimate but no interval; when both samples have zero spread the interval collapses to the estimate.
    """
    a, b = [float(x) for x in host_values], [float(x) for x in reference_values]
    out = {"estimate": None, "ci_low": None, "ci_high": None, "df": None, "log_se": None, "n_host": len(a), "n_ref": len(b),
           "host_seconds": geomean(a), "ref_seconds": geomean(b), "host_per_round": a, "ref_per_round": b}
    if not a or not b:
        return out
    ma, sa = mean_sd([math.log(x) for x in a])
    mb, sb = mean_sd([math.log(x) for x in b])
    out["estimate"] = math.exp(ma - mb)
    if len(a) < 2 or len(b) < 2:
        return out
    va, vb = sa ** 2 / len(a), sb ** 2 / len(b)
    variance = va + vb
    out["log_se"] = math.sqrt(variance)
    if variance == 0.0:
        out.update(df=float(len(a) + len(b) - 2), ci_low=out["estimate"], ci_high=out["estimate"])
        return out
    df = variance ** 2 / ((va ** 2 / (len(a) - 1) if va else 0.0) + (vb ** 2 / (len(b) - 1) if vb else 0.0))
    half = t_quantile(0.5 + level / 2.0, df) * out["log_se"]
    out.update(df=df, ci_low=math.exp(ma - mb - half), ci_high=math.exp(ma - mb + half))
    return out


def cross_query_record(host_cell, ref_cell, host, reference):
    """One (suite, scale, query, engine) comparison from the two hosts' medians cells (stats['medians'] entries)."""
    h = [v for v in host_cell["per_round"] if v is not None]
    r = [v for v in ref_cell["per_round"] if v is not None]
    blocked = []
    if not h:
        blocked.append({"host": host, "reason_class": host_cell.get("reason_class") or host_cell["status"]})
    if not r:
        blocked.append({"host": reference, "reason_class": ref_cell.get("reason_class") or ref_cell["status"]})
    if h and r:
        status = "ok" if host_cell["status"] == "ok" and ref_cell["status"] == "ok" else "partial"
    else:
        status = "unavailable"
    record = welch_summary(h, r)
    record.update(status=status, blocked_by=blocked)
    return record


def cross_suite_record(records, host_cells, ref_cells):
    """Suite-level ratio over the queries timed in every round on both hosts."""
    used = [q for q, rec in records.items() if rec["status"] == "ok"]
    excluded = {q: rec["status"] + (" (" + ", ".join(f"{b['host']}: {b['reason_class']}" for b in rec["blocked_by"]) + ")" if rec["blocked_by"] else "")
                for q, rec in records.items() if q not in used}
    host_rounds = next((host_cells[q]["rounds"] for q in used), [])
    ref_rounds = next((ref_cells[q]["rounds"] for q in used), [])

    def per_round(cells, count):
        return [math.exp(math.fsum(math.log(cells[q]["per_round"][i]) for q in used) / len(used)) for i in range(count)] if used else []

    record = welch_summary(per_round(host_cells, len(host_rounds)), per_round(ref_cells, len(ref_rounds)))
    record.update(status="ok" if used else "no_common_query", n_queries=len(used), queries_used=used, queries_excluded=excluded,
                  host_rounds=list(host_rounds), ref_rounds=list(ref_rounds))
    return record


def identity_differences(info, ref_info):
    """{field: [host value, reference value]} for the identity fields that differ (or are recorded on one side only)."""
    return {f: [info.get(f), ref_info.get(f)] for f in IDENTITY_FIELDS
            if info.get(f) != ref_info.get(f) and not (info.get(f) is None and ref_info.get(f) is None)}


def host_caveats(host, reference, info, ref_info, differences):
    notes = []
    pair = lambda field: f"{differences[field][0]} vs {differences[field][1]}"
    if "arch" in differences:
        notes.append(f"CPU architecture differs ({pair('arch')}): wheels, JDK and RAPIDS jar are platform builds, so CPU-engine and "
                     f"host-side GPU-engine ratios include the architecture, not only the GPU.")
    if "cpu_model" in differences:
        notes.append(f"CPU model differs ({pair('cpu_model')}).")
    if "vcpus" in differences:
        used = info.get("cpus_arg") or ref_info.get("cpus_arg")
        notes.append(f"vCPU count differs ({pair('vcpus')}); all engines ran with --cpus {used}, so the extra vCPUs sit idle "
                     f"but cache, memory bandwidth and NUMA layout may still differ." if used else
                     f"vCPU count differs ({pair('vcpus')}); the --cpus setting is not recorded for one host.")
    if "cpus_arg" in differences:
        notes.append(f"--cpus differs ({pair('cpus_arg')}): the engines' thread counts are not identical.")
    used = info.get("cpus_arg") or ref_info.get("cpus_arg")
    occupied = lambda i: (None if i.get("cpus_arg_cores_min") is None or i.get("cpus_arg_cores_max") is None else
                          str(i["cpus_arg_cores_min"]) if i["cpus_arg_cores_min"] == i["cpus_arg_cores_max"] else f"{i['cpus_arg_cores_min']} to {i['cpus_arg_cores_max']}")
    mine, theirs = occupied(info), occupied(ref_info)
    if "smt_threads_per_core" in differences:
        notes.append(f"SMT differs ({host}: {info.get('smt_threads_per_core')} thread per core, {reference}: {ref_info.get('smt_threads_per_core')}): "
                     + (f"{used} threads occupy {mine} physical core(s) on {host} ({info.get('physical_cores')} in total) and {theirs} on {reference} "
                        f"({ref_info.get('physical_cores')} in total); " if mine and theirs else "")
                     + "CPU-engine ratios compare different numbers of physical cores (hardware threads sharing a core versus whole cores), not only different CPUs, "
                       "and are not per-core ratios. The topology is what lscpu reports inside the machine; under a hypervisor the mapping of vCPUs to physical "
                       "cores and threads is the hypervisor's.")
    elif "physical_cores" in differences:
        notes.append(f"Physical core count differs ({pair('physical_cores')}); at --cpus {used} the threads occupy {mine or 'an unrecorded number of'} physical core(s) on "
                     f"{host} and {theirs or 'an unrecorded number of'} on {reference}.")
    if "numa_nodes" in differences:
        notes.append(f"NUMA node count differs ({pair('numa_nodes')}; nodes that hold CPUs: {info.get('numa_nodes_with_cpus')} vs {ref_info.get('numa_nodes_with_cpus')}).")
    if "hypervisor" in differences:
        notes.append(f"Hypervisor line in lscpu differs ({differences['hypervisor'][0] or 'none reported'} vs {differences['hypervisor'][1] or 'none reported'}); "
                     "an absent line does not establish bare metal.")
    if "host_ram_gib" in differences:
        notes.append(f"Host RAM differs ({pair('host_ram_gib')} GiB).")
    if "gpu_memory_gib" in differences:
        notes.append(f"Device memory differs ({pair('gpu_memory_gib')} GiB): out-of-memory boundaries differ, so some cells exist on only one host.")
    if "driver_version" in differences or "cuda_version" in differences:
        notes.append(f"Driver / CUDA differ (driver {differences.get('driver_version', ['same', 'same'])[0]} vs "
                     f"{differences.get('driver_version', ['same', 'same'])[1]}; CUDA {differences.get('cuda_version', ['same', 'same'])[0]} vs "
                     f"{differences.get('cuda_version', ['same', 'same'])[1]}).")
    if "rapids_jar" in differences and differences["rapids_jar"][0] and differences["rapids_jar"][1]:
        notes.append(f"RAPIDS Accelerator jar differs ({pair('rapids_jar')}): platform builds of the same plugin version, recorded with their sha256.")
    missing = [f for f in ("cpu_model", "arch", "vcpus", "host_ram_gib", "driver_version") if info.get(f) is None or ref_info.get(f) is None]
    if missing:
        notes.append("Host identity not recorded for: " + ", ".join(missing) + " (a difference there cannot be ruled out).")
    if info.get("n_rounds") != ref_info.get("n_rounds"):
        notes.append(f"Rounds differ ({info.get('n_rounds')} on {host} vs {ref_info.get('n_rounds')} on {reference}); the Welch df reflects both.")
    if info.get("design_doc_sha256") and ref_info.get("design_doc_sha256") and info["design_doc_sha256"] != ref_info["design_doc_sha256"]:
        def version(i):
            commit = (i.get("design_commit") or "")[:7] or f"document {i['design_doc_sha256'][:7]}"
            return f"{commit} ({i['amendments']} amendments)" if i.get("amendments") is not None else commit
        notes.append(f"The campaigns started under different design versions: {host} under {version(info)}, {reference} under {version(ref_info)}; "
                     "later amendments change the protocol only where they say so (see the design document, section 11).")
    if bool(info.get("synthetic")) != bool(ref_info.get("synthetic")):
        notes.append("One host is synthetic and the other is not: nothing here is a measurement.")
    return notes


TELEMETRY_COMPACT = ("n_actions", "n_actions_excluded_short", "n_samples", "util_median_pct", "util_p90_pct", "idle_fraction", "power_median_w", "power_fraction_of_limit",
                     "sm_clock_median_mhz", "sm_clock_fraction_of_max", "mem_util_median_pct")


def gpu_telemetry_compact(gt):
    """The per-host view kept in cross_host.json: the telemetry cells without their per-query and per-round detail."""
    if not isinstance(gt, dict) or not gt.get("meta"):
        return {"label": TELEMETRY_LABEL, "available": False, "sample_interval_ms": None, "power_limit_w": None, "max_sm_clock_mhz": None, "cells": {}}
    meta = gt["meta"]
    return {"label": meta["label"], "available": meta["available"], "sample_interval_ms": meta["sample_interval_ms"], "power_limit_w": meta["power_limit_w"],
            "max_sm_clock_mhz": meta["max_sm_clock_mhz"],
            "cells": {suite: {scale: {engine: {k: r.get(k) for k in TELEMETRY_COMPACT} for engine, r in by_engine.items()} for scale, by_engine in by_scale.items()}
                      for suite, by_scale in gt.get("cells", {}).items()}}


def plan_variability_compact(pv):
    """The per-host view kept in cross_host.json: which final plans each variable Spark cell ended in, how often and how fast."""
    if not isinstance(pv, dict):
        return {"cells_checked": None, "cells_variable": None, "cells": []}
    keep = ("suite", "scale", "query", "engine", "rounds", "n_plans", "initial_plan_identical", "measured_time_ranges_overlap",
            "median_seconds_slowest_over_fastest", "reference_signature")
    return {"cells_checked": pv.get("cells_checked"), "cells_variable": pv.get("cells_variable"),
            "cells": [{**{k: c.get(k) for k in keep},
                       "plans": [{"signature": p["signature"], "n_measured": p["n_measured"], "n_warmup": p["n_warmup"],
                                  "measured_median_seconds": (p.get("measured_seconds") or {}).get("median"),
                                  "warmup_median_seconds": statistics.median(p["warmup_seconds"]) if p.get("warmup_seconds") else None,
                                  "exchanges": sum((p.get("features") or {}).get("exchanges", {}).values()),
                                  "shuffle_write_bytes_median": ((p.get("execution") or {}).get("shuffle_write_bytes") or {}).get("median")}
                                 for p in c["plans"]]} for c in pv.get("cells") or []]}


def compare_hosts(host_stats, reference=None):
    """Cross-host secondary analysis (A2/A3) from per-host stats dicts {label: stats}; never pools data across hosts."""
    labels = ladder_sorted(host_stats)
    if not labels:
        raise ValueError("no hosts to compare")
    reference = reference or (DEFAULT_REFERENCE if DEFAULT_REFERENCE in host_stats else labels[0])
    if reference not in host_stats:
        raise ValueError(f"reference host {reference!r} is not among the hosts {labels}")
    ref = host_stats[reference]
    synthetic = any(s.get("synthetic") for s in host_stats.values())
    info = {}
    for label in labels:
        stats = host_stats[label]
        info[label] = {**stats.get("host", {}), "label": label, "role": "reference" if label == reference else "comparison",
                       **{k: (stats.get("meta") or {}).get(k) for k in ("design_commit", "design_doc_sha256", "amendments", "later_amendments")},
                       "n_rounds": stats["campaign"]["n_rounds"], "rounds_included": stats["campaign"]["rounds_included"],
                       "engines": stats["campaign"]["engines"], "root": stats["campaign"]["root"],
                       "synthetic": bool(stats.get("synthetic"))}
    differences = {label: identity_differences(info[label], info[reference]) for label in labels if label != reference}
    result = {
        "schema_version": CROSS_SCHEMA_VERSION, "synthetic": synthetic, "watermark": WATERMARK if synthetic else None,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reference": reference, "hosts": labels, "hosts_not_included": [x for x in LADDER if x not in host_stats],
        "partial": any(x not in host_stats for x in LADDER), "level": LEVEL, "definition": CROSS_DEFINITION,
        "caveats": list(CROSS_CAVEATS), "host_info": info, "identity_differences": differences,
        "caveats_by_host": {label: host_caveats(label, reference, info[label], info[reference], differences[label]) for label in differences},
        "ratio": {}, "ratio_query": {}, "speedup": {}, "coverage_differences": {}, "agreement": {}, "plan_variability": {}, "gpu_telemetry": {}, "skipped": {},
    }
    ref_medians = ref.get("medians") or {}
    for label in labels:
        if label == reference:
            continue
        medians = host_stats[label].get("medians") or {}
        result["ratio"][label], result["ratio_query"][label], skipped = {}, {}, []
        result["skipped"][label] = skipped
        engines = [e for e in ENGINE_ORDER if e in host_stats[label]["campaign"]["engines"] and e in ref["campaign"]["engines"]]
        for engine in engines:
            suite_out, query_out = {}, {}
            for suite in SUITES:
                for scale in sorted(set(medians.get(suite, {})) & set(ref_medians.get(suite, {})), key=scale_sort_key):
                    queries = [q for q in ref["campaign"]["queries"].get(suite, []) if q in medians[suite][scale] and q in ref_medians[suite][scale]]
                    host_cells = {q: medians[suite][scale][q].get(engine) for q in queries}
                    ref_cells = {q: ref_medians[suite][scale][q].get(engine) for q in queries}
                    queries = [q for q in queries if host_cells[q] is not None and ref_cells[q] is not None]
                    if not queries:
                        continue
                    records = {q: cross_query_record(host_cells[q], ref_cells[q], label, reference) for q in queries}
                    suite_record = cross_suite_record(records, host_cells, ref_cells)
                    suite_out.setdefault(suite, {})[scale] = suite_record
                    query_out.setdefault(suite, {})[scale] = records
                for scale in sorted(set(medians.get(suite, {})) ^ set(ref_medians.get(suite, {})), key=scale_sort_key):
                    skipped.append(f"{suite}/{scale}: present on only one of {label} and {reference}")
            if suite_out:
                result["ratio"][label][engine], result["ratio_query"][label][engine] = suite_out, query_out
        result["skipped"][label] = sorted(set(skipped))
    # per-host speedups (C1..C4, from each host's own campaign; no combination across hosts)
    for contrast in ("c1", "c2", "c3", "c4", "c4_strict"):
        for label in labels:
            for suite, by_scale in (host_stats[label].get(contrast) or {}).items():
                for scale, record in by_scale.items():
                    result["speedup"].setdefault(contrast, {}).setdefault(label, {}).setdefault(suite, {})[scale] = {
                        k: record.get(k) for k in ("estimate", "ci_low", "ci_high", "df", "n_rounds", "n_queries")}
    # size of the output disagreement with Polars: a host's largest relative difference against the reference host's, same (suite, scale, engine)
    ref_agreement = ref.get("output_agreement") or {}
    for label in labels:
        if label == reference:
            continue
        out = {}
        for suite, by_scale in (host_stats[label].get("output_agreement") or {}).items():
            for scale, by_engine in by_scale.items():
                for engine, cell in by_engine.items():
                    other = ref_agreement.get(suite, {}).get(scale, {}).get(engine)
                    if not other:
                        continue
                    mine, theirs = cell["max_rel_diff"], other["max_rel_diff"]
                    matched = {str(r): [a, b] for r, a in zip(cell["rounds"], cell["max_rel_diff_per_round"])
                               for r2, b in zip(other["rounds"], other["max_rel_diff_per_round"]) if r == r2}
                    out.setdefault(suite, {}).setdefault(scale, {})[engine] = {
                        "host_max_rel_diff": mine, "ref_max_rel_diff": theirs, "host_max_rel_diff_query": cell["max_rel_diff_query"],
                        "ref_max_rel_diff_query": other["max_rel_diff_query"],
                        "larger_than_reference": bool(mine is not None and (theirs is None or mine > theirs)),
                        "host_over_reference": mine / theirs if (mine is not None and theirs) else None,
                        "host_within_gate": bool(mine is None or mine <= REL_TOL), "host_failed": cell["failed"],
                        "host_rounds": cell["rounds"], "ref_rounds": other["rounds"], "matched_rounds": matched,
                        "queries_larger": sorted(q for q, v in cell["queries"].items() if v > other["queries"].get(q, 0.0))}
        result["agreement"][label] = out
    # coverage outcomes that differ between a host and the reference
    for label in labels:
        if label == reference:
            continue
        rows = []
        host_matrix, ref_matrix = host_stats[label]["coverage"]["matrix"], ref["coverage"]["matrix"]
        for suite, by_query in host_matrix.items():
            for query, by_scale in by_query.items():
                for scale, by_engine in by_scale.items():
                    for engine, cell in by_engine.items():
                        other = ref_matrix.get(suite, {}).get(query, {}).get(scale, {}).get(engine)
                        if other and (cell["status"], cell["reason_class"]) != (other["status"], other["reason_class"]):
                            rows.append({"suite": suite, "scale": scale, "query": query, "engine": engine, "host_status": cell["status"],
                                         "host_reason_class": cell["reason_class"], "ref_status": other["status"],
                                         "ref_reason_class": other["reason_class"]})
        result["coverage_differences"][label] = rows
    result["plan_variability"] = {label: plan_variability_compact(host_stats[label].get("plan_variability")) for label in labels}
    result["gpu_telemetry"] = {label: gpu_telemetry_compact((host_stats[label].get("secondary") or {}).get("gpu_telemetry")) for label in labels}
    return result


def xhost_compat(cross, host):
    """The per-host view the paper tooling expects (--stats xhost=PATH): engine -> suite -> scale -> estimate/ci_low/ci_high."""
    out = {"_meta": {"host": host, "reference": cross["reference"], "synthetic": cross["synthetic"], "watermark": cross["watermark"],
                     "definition": f"suite geometric mean of t_{host}/t_{cross['reference']}"}}
    for engine, by_suite in cross["ratio"].get(host, {}).items():
        for suite, by_scale in by_suite.items():
            for scale, record in by_scale.items():
                out.setdefault(engine, {}).setdefault(suite, {})[scale] = {
                    k: record.get(k) for k in ("estimate", "ci_low", "ci_high", "df", "n_queries")}
    return out


def load_host_stats(label, path, *, allow_ungated=False, energy=True, gpu_index=0, a100_roots=(), extras=None):
    """(stats dict, campaign root or None) for one --host argument: a stats.json (file or directory) or a campaign root."""
    path = Path(path)
    stats_file = path if path.is_file() else (path / "stats.json" if (path / "stats.json").is_file()
                                              and not any(ROUND_DIR.match(p.name) for p in path.iterdir() if p.is_dir()) else None)
    if stats_file is not None:
        stats = read_json(stats_file)
        if not isinstance(stats, dict) or "medians" not in stats or "campaign" not in stats:
            raise ValueError(f"{stats_file} is not a campaign stats.json (no 'medians'/'campaign')")
        stats = {**stats, "host": {**(stats.get("host") or {}), "label": label}}
        return stats, None
    if not path.is_dir():
        raise ValueError(f"{path} is neither a campaign root nor a stats.json")
    return analyze(path, a100_roots, allow_ungated=allow_ungated, energy=energy, gpu_index=gpu_index, label=label, extras=extras), path


def parse_host_args(values):
    """['H100=/path', 'B200=/path'] -> [(label, path)], rejecting duplicates and missing labels."""
    pairs = []
    for value in values or ():
        label, sep, path = value.partition("=")
        if not sep or not label.strip() or not path.strip() or re.search(r"[\s./\\]", label.strip()):
            raise ValueError(f"--host expects LABEL=PATH with a simple label (H100, B200, ...), got {value!r}")
        if label.strip() in dict(pairs):
            raise ValueError(f"--host {label.strip()} given twice")
        pairs.append((label.strip(), path.strip()))
    return pairs


# ------------------------------------------------------------------ stats.json schema (documented keys)

SCHEMA = [
    # (dotted pattern, description, optional). {placeholders} match one key segment. Keep in sync: tests enforce both ways.
    ("schema_version", "Schema version of this file ('1.0').", False),
    ("synthetic", "true when the tree is synthetic (injected ratios); every output then carries 'SYNTHETIC — NOT RESULTS'.", False),
    ("watermark", "'SYNTHETIC — NOT RESULTS' for synthetic data, else null.", False),
    ("generated_at_utc", "UTC time of the analysis run.", False),
    ("host.label", "Host label used in cross-host comparisons (--label; default: GPU short name such as H100, A100, GH200, B200).", False),
    ("host.instance_type", "Instance type recorded by run_campaign.py (--host-label), e.g. gpu_1x_h100_sxm5; null if not recorded.", False),
    ("host.gpu", "GPU product name (nvidia-smi).", False),
    ("host.gpu_short", "Short GPU name: H100, A100, GH200, B200.", False),
    ("host.gpu_count", "Number of GPUs.", False),
    ("host.gpu_memory_gib", "Device memory, GiB.", False),
    ("host.cpu_model", "CPU model name (lscpu).", False),
    ("host.arch", "CPU architecture: x86_64 or aarch64 (null if not recorded).", False),
    ("host.vcpus", "vCPUs of the host.", False),
    ("host.cpus_arg", "Value of --cpus used by every engine (design: 26 on all hosts).", False),
    ("host.hypervisor", "Hypervisor vendor line of lscpu (e.g. KVM); null when lscpu has none (which does not establish bare metal).", False),
    ("host.smt_threads_per_core", "lscpu 'Thread(s) per core' (2 = SMT/hyperthreading as the machine reports it; 1 = one thread per core); null if not recorded.", False),
    ("host.cores_per_socket", "lscpu 'Core(s) per socket'.", False),
    ("host.sockets", "lscpu 'Socket(s)'.", False),
    ("host.physical_cores", "cores_per_socket x sockets, as lscpu reports it (a VM can report a flat topology of many one-core sockets).", False),
    ("host.numa_nodes", "lscpu 'NUMA node(s)'.", False),
    ("host.numa_nodes_with_cpus", "NUMA nodes whose 'NUMA nodeN CPU(s)' list is not empty (e.g. 1 of 9 on GH200, where the other nodes are CPU-less).", False),
    ("host.topology_consistent", "true when smt_threads_per_core x physical_cores equals the CPU(s) count; null if a field is missing.", False),
    ("host.cpus_arg_cores_min", "Fewest physical cores that --cpus threads can occupy: ceil(min(cpus_arg, vcpus) / smt_threads_per_core) (threads packed onto sibling hardware threads).", False),
    ("host.cpus_arg_cores_max", "Most physical cores that --cpus threads can occupy: min(min(cpus_arg, vcpus), physical_cores) (threads spread over distinct cores).", False),
    ("host.cpus_arg_share_of_vcpus", "cpus_arg / vcpus (1.0 = the threads can use every vCPU).", False),
    ("host.host_ram_gib", "Host RAM, GiB.", False),
    ("host.driver_version", "NVIDIA driver version.", False),
    ("host.cuda_version", "CUDA version reported by nvidia-smi.", False),
    ("host.cuda_runtime_version", "CUDA runtime the pinned Python stack loads (cudaRuntimeGetVersion, e.g. '12.9.0'); null if the environment probe did not record it.", False),
    ("host.platform", "Platform string recorded by run_campaign.py.", False),
    ("host.packages", "Opaque object: versions of polars/cudf/rmm/pyspark packages found in the environment inventory.", False),
    ("host.rapids_jar", "File name of the RAPIDS Accelerator jar the campaign was started with (the aarch64 build carries the -cuda12-arm64 classifier); null if not recorded.", False),
    ("host.rapids_jar_sha256", "sha256 of that jar as recorded by run_campaign.py.", False),
    ("host.preflight", "List of {scope, path, verdict, counts, formal, min_free_gib, allow_any_gpu, runs, started_at_utc, completed_at_utc} for the round preflights and any host rehearsal preflight kept beside the campaign root; min_free_gib is an operational threshold, not a design parameter.", False),
    ("host.gpu_policy", "h100_required or 'any_gpu (amendment A2 hosts)' (campaign.json design).", False),
    ("host.formal", "campaign.json design flag: false when test overrides were used.", False),
    ("host.synthetic", "true for a synthetic tree.", False),
    ("host.source", "Where each identity field came from (inventory, resource_allocation, campaign.json).", False),
    ("meta.gpu_name", "GPU product name (same as host.gpu).", False),
    ("meta.cpu_model", "CPU model name (same as host.cpu_model).", False),
    ("meta.driver_version", "NVIDIA driver version (same as host.driver_version).", False),
    ("meta.rounds_completed", "Accepted rounds that enter the estimates.", False),
    ("meta.rounds_repeated", "Round numbers that have more than one attempt directory (round-<k>, round-<k>-repeat-<n>).", False),
    ("meta.rounds_gated", "Accepted rounds whose gates.json carries a pass verdict (fewer than rounds_completed under --allow-ungated).", False),
    ("meta.input_gate", "verify_generated_inputs verdict for the campaign's inputs: 'RESULT: OK' | 'RESULT: FAILED' | 'RESULT: INCOMPLETE' (every needed scale verified, complete report); null if no report was found.", False),
    ("meta.input_gate_source", "File(s) or check the verdict was read from (null if none).", False),
    ("meta.amendments", "Number of '### A<n>' headings in section 11 of the design-document version this campaign started under (the commit whose docs/H100_EXPERIMENT_DESIGN.md sha256 equals the one recorded in campaign.json); null if that version cannot be determined.", False),
    ("meta.later_amendments", "List of amendment ids in the current design document that are not in the version the campaign started under (e.g. ['A4']); [] if none, null if undetermined.", False),
    ("meta.design_doc_matches_campaign", "true when the sha256 recorded in campaign.json resolves to a committed version of the design document; false (with a warning) when it matches no commit; null if none was recorded.", False),
    ("meta.design_commit", "Oldest git commit holding the design-document version the campaign started under (full hash); null if it cannot be resolved.", False),
    ("meta.design_commit_short", "First 7 characters of meta.design_commit.", False),
    ("meta.run_commit", "Git HEAD commit recorded by run_campaign.py when the campaign was created (the code that ran), null if not recorded.", False),
    ("meta.run_commit_dirty_files", "Number of uncommitted files recorded by run_campaign.py at that time.", False),
    ("meta.design_doc_sha256", "sha256 of the design document recorded at campaign start.", False),
    ("meta.design_sha256", "sha256 of the resolved campaign design (campaign.json).", False),
    ("meta.sessions_completed", "Sessions of accepted rounds that finished with exit status 0 and no recorded failure.", False),
    ("meta.measured_trials", "Measured (timed) trial records with a finite time, over accepted rounds.", False),
    ("meta.outputs_compared", "Saved query outputs compared with Polars' (gate 2), recomputed from results.json rows over accepted rounds (null if none could be compared).", False),
    ("meta.outputs_failed", "Of those, outputs outside rel 1e-8 / abs 1e-6 (integers exact); 0 in a gated campaign.", False),
    ("meta.max_rel_diff", "Largest relative difference among the compared numeric values.", False),
    ("meta.spark_gpu_plans_checked", "spark_gpu final plans (one per record) in sessions whose preflight plan rule passed (gate 4); 0 if spark_gpu did not run; null if a spark_gpu session has no passing preflight plan check.", False),
    ("meta.cleanup_barriers_passed", "verified_gc cleanup barriers with status passed (gate 5), over accepted rounds.", False),
    ("meta.campaign_start_utc", "Start of the earliest session of the accepted rounds, 'YYYY-MM-DD HH:MM UTC'.", False),
    ("meta.campaign_end_utc", "End of the latest session of the accepted rounds, same format.", False),
    ("meta.integrity_failures_in_accepted_rounds", "Cells of accepted rounds that A1 classifies as integrity failures (error, timeout, failed or invalid session, incomplete trials); must be 0.", False),
    ("campaign.root", "Campaign root that was analyzed.", False),
    ("campaign.design", "Opaque object: selected fields of campaign.json (seed, engines, sequences, ...).", False),
    ("campaign.provenance", "Opaque object: created_utc, design_doc_sha256, design_sha256, git_commit, git_branch, git_dirty_files.", False),
    ("campaign.rounds_found", "Round directory names discovered.", False),
    ("campaign.rounds_included", "Integer round ids used in every estimate (gates passed).", False),
    ("campaign.rounds_included_labels", "Directory names of the included rounds.", False),
    ("campaign.rounds_excluded", "List of {round, label, reason, gate_reasons} for rounds left out (failed gate, superseded, no verdict).", False),
    ("campaign.n_rounds", "Number of included rounds R.", False),
    ("campaign.df", "Degrees of freedom of every interval: R - 1.", False),
    ("campaign.level", "Confidence level (0.95).", False),
    ("campaign.t_critical", "Two-sided Student-t critical value used (null if R < 2).", False),
    ("campaign.engines", "Engines present, canonical order.", False),
    ("campaign.suites", "Suites present.", False),
    ("campaign.scales.{suite}", "Scale keys (sf1, sf10, ...) of a suite, ascending.", False),
    ("campaign.queries.{suite}", "Query ids of a suite.", False),
    ("campaign.trials_per_cell", "Distinct measured-trial counts per session (expected [3]).", False),
    ("campaign.energy.gpu_csv_runs", "Number of suite runs with a gpu.csv used for the energy proxy.", False),
    ("campaign.energy.tz_offset_seconds", "Estimated nvidia-smi local-time offset to UTC, seconds (one entry per distinct value).", False),
    ("campaign.warnings", "Non-fatal loader warnings.", False),
    ("contrasts.{c}.numerator", "Reference-side engine (slower-is-numerator speedup t_ref / t_engine).", False),
    ("contrasts.{c}.denominator", "Engine whose speedup is estimated ('best_gpu' = faster of the supported GPU engines per round).", False),
    ("contrasts.{c}.label", "Human-readable contrast label.", False),
    ("contrasts.{c}.rq", "Research question the contrast answers.", False),
    ("contrasts.{c}.subject", "Engine whose colour the figures use (null for C4).", False),
    ("contrasts.{c}.definition", "One-line definition.", False),
    # per-(suite, scale) contrast estimates; {c} is c1, c2, c3, c4 or c4_strict
    ("{c}.{suite}.{scale}.estimate", "Geometric mean over rounds of the per-round suite geomean ratio (null if no common query).", False),
    ("{c}.{suite}.{scale}.ci_low", "Lower 95% limit: exp(mean log - t*se), df = rounds - 1 (null if rounds < 2).", False),
    ("{c}.{suite}.{scale}.ci_high", "Upper 95% limit.", False),
    ("{c}.{suite}.{scale}.min", "Smallest per-round suite ratio.", False),
    ("{c}.{suite}.{scale}.max", "Largest per-round suite ratio.", False),
    ("{c}.{suite}.{scale}.n_rounds", "Rounds contributing.", False),
    ("{c}.{suite}.{scale}.df", "Degrees of freedom.", False),
    ("{c}.{suite}.{scale}.log_mean", "Mean of the per-round log ratios.", False),
    ("{c}.{suite}.{scale}.log_sd", "Sample sd of the per-round log ratios.", False),
    ("{c}.{suite}.{scale}.rounds", "Round ids aligned with per_round.", False),
    ("{c}.{suite}.{scale}.per_round", "Per-round suite geomean ratios.", False),
    ("{c}.{suite}.{scale}.n_queries", "Queries in the suite geomean.", False),
    ("{c}.{suite}.{scale}.queries_used", "Query ids in the suite geomean (available for both sides in every round).", False),
    ("{c}.{suite}.{scale}.queries_excluded", "Dict query -> reason; empty when every query was used.", False),
    ("{c}.{suite}.{scale}.queries_excluded.{query}", "Why a query is outside the suite geomean (unsupported engine, partial rounds, not run).", True),
    ("{c}.{suite}.{scale}.q.{query}.estimate", "Per-query geometric mean over rounds of t_ref / t_engine (null if not computable).", False),
    ("{c}.{suite}.{scale}.q.{query}.ci_low", "Per-query lower 95% limit.", False),
    ("{c}.{suite}.{scale}.q.{query}.ci_high", "Per-query upper 95% limit.", False),
    ("{c}.{suite}.{scale}.q.{query}.min", "Smallest per-round ratio.", False),
    ("{c}.{suite}.{scale}.q.{query}.max", "Largest per-round ratio.", False),
    ("{c}.{suite}.{scale}.q.{query}.n_rounds", "Rounds with both sides valid.", False),
    ("{c}.{suite}.{scale}.q.{query}.df", "Degrees of freedom.", False),
    ("{c}.{suite}.{scale}.q.{query}.log_mean", "Mean per-round log ratio.", False),
    ("{c}.{suite}.{scale}.q.{query}.log_sd", "Sample sd of per-round log ratios.", False),
    ("{c}.{suite}.{scale}.q.{query}.rounds", "Round ids aligned with per_round.", False),
    ("{c}.{suite}.{scale}.q.{query}.per_round", "Per-round ratios.", False),
    ("{c}.{suite}.{scale}.q.{query}.status", "ok | partial (some rounds) | unsupported | unstable (A1: unstable coverage of an engine involved; no estimate) | no_overlap | not_run.", False),
    ("{c}.{suite}.{scale}.q.{query}.unstable_engines", "Engines of this contrast whose cell is unstable coverage (A1) and was therefore left out of the estimate (empty list normally).", False),
    ("{c}.{suite}.{scale}.q.{query}.blocked_by", "List of {engine, reason_class, reason} that prevented the ratio.", False),
    ("{c}.{suite}.{scale}.q.{query}.best_engine_rounds", "C4 only: dict engine -> rounds in which it was the best GPU engine.", True),
    ("{c}.{suite}.{scale}.q.{query}.best_engine_rounds.{engine}", "C4 only: rounds in which this GPU engine was the best.", True),
    ("medians.{suite}.{scale}.{query}.{engine}.status", "ok | partial | failed | not_run across rounds; 'unstable' = unstable coverage (A1), no time is reported (see unstable_coverage).", False),
    ("medians.{suite}.{scale}.{query}.{engine}.geomean", "Geometric mean over rounds of the per-round median of 3 trials, seconds.", False),
    ("medians.{suite}.{scale}.{query}.{engine}.min", "Smallest per-round median.", False),
    ("medians.{suite}.{scale}.{query}.{engine}.max", "Largest per-round median.", False),
    ("medians.{suite}.{scale}.{query}.{engine}.n_rounds", "Rounds with a valid median.", False),
    ("medians.{suite}.{scale}.{query}.{engine}.rounds", "Round ids aligned with per_round.", False),
    ("medians.{suite}.{scale}.{query}.{engine}.per_round", "Per-round medians (null where invalid).", False),
    ("medians.{suite}.{scale}.{query}.{engine}.cv_between_rounds", "Coefficient of variation of the per-round medians.", False),
    ("medians.{suite}.{scale}.{query}.{engine}.reason_class", "unsupported_operation | out_of_memory | timeout | error | session_failed | invalid | unstable_coverage | null.", False),
    ("fastest.{suite}.{scale}.{query}.winner", "Engine with the lowest geometric-mean time (null if none ran).", False),
    ("fastest.{suite}.{scale}.{query}.ranking", "Engines fastest to slowest.", True),
    ("fastest.{suite}.{scale}.{query}.geomean_seconds", "Winner's geometric-mean time.", True),
    ("fastest.{suite}.{scale}.{query}.runner_up", "Second-fastest engine.", True),
    ("fastest.{suite}.{scale}.{query}.runner_up_over_winner", "Geometric-mean ratio runner-up / winner (descriptive; winner picked from the same data).", True),
    ("fastest.{suite}.{scale}.{query}.ci_low", "t-interval lower limit of that ratio.", True),
    ("fastest.{suite}.{scale}.{query}.ci_high", "t-interval upper limit of that ratio.", True),
    ("cpu_reference.{suite}.{scale}.n_queries", "Queries with timings for both CPU engines (C4 uses Polars CPU as the CPU reference).", False),
    ("cpu_reference.{suite}.{scale}.queries_where_spark_cpu_faster", "Queries where Spark CPU's geometric-mean time beats Polars CPU's.", False),
    ("legacy.note", "Legacy totals are arithmetic sums of medians; never mixed with geometric means.", False),
    ("legacy.totals.{suite}.{scale}.{engine}.complete", "true if every query has a valid median in every round.", False),
    ("legacy.totals.{suite}.{scale}.{engine}.per_round", "Per-round sum of the per-query medians (null where a query is missing).", False),
    ("legacy.totals.{suite}.{scale}.{engine}.rounds", "Round ids aligned with per_round.", False),
    ("legacy.totals.{suite}.{scale}.{engine}.mean_total", "Mean over rounds of the sum of medians (null unless complete).", False),
    ("legacy.totals.{suite}.{scale}.{engine}.min_total", "Smallest per-round total.", False),
    ("legacy.totals.{suite}.{scale}.{engine}.max_total", "Largest per-round total.", False),
    ("legacy.totals.{suite}.{scale}.{engine}.queries_missing", "Queries without a valid median in some round.", False),
    ("legacy.ratios.{suite}.{scale}.{c}.ratio_of_mean_totals", "Legacy ratio: mean total of the reference side / mean total of the engine side.", False),
    ("legacy.ratios.{suite}.{scale}.{c}.per_round", "Per-round legacy ratios.", False),
    ("variability.cells", "List of {engine, suite, scale, query, rounds, cv_within[], cv_between} per cell.", False),
    ("variability.{group}.{engine}.within_session.{stat}", "Distribution (n, median, mean, p90, max) of per-session trial CVs.", False),
    ("variability.{group}.{engine}.between_rounds.{stat}", "Distribution of between-round CVs of the per-round medians.", False),
    ("variability.by_engine_suite.{engine}.{suite}.within_session.{stat}", "Same, per suite.", False),
    ("variability.by_engine_suite.{engine}.{suite}.between_rounds.{stat}", "Same, per suite.", False),
    ("variability.by_engine_scale.{engine}.{suite}.{scale}.within_session.{stat}", "Same, per suite and scale.", False),
    ("variability.by_engine_scale.{engine}.{suite}.{scale}.between_rounds.{stat}", "Same, per suite and scale.", False),
    ("order.n_observations", "Cell-round deviations used.", False),
    ("order.design_balance", "Opaque object: observed order per round and whether positions / adjacent pairs are balanced.", False),
    ("order.position.{p}.{stat}", "Position 1-4: n_cells, n_rounds, mean_pct, ci_low_pct, ci_high_pct, sd_log of the log-time deviation.", False),
    ("order.position_by_engine.{p}.{engine}.{stat}", "Same, per measured engine.", False),
    ("order.predecessor.{pred}.{stat}", "By predecessor engine ('none' = first session of a block).", False),
    ("order.predecessor_by_engine.{pred}.{engine}.{stat}", "Same, per measured engine.", False),
    ("crossover.{c}.{suite}.verdicts.{scale}", "gpu_faster (CI above 1) | cpu_faster (CI below 1) | inconclusive | n/a.", False),
    ("crossover.{c}.{suite}.estimates.{scale}", "C4 suite estimate at that scale.", False),
    ("crossover.{c}.{suite}.crossover_found", "true if some scale has the C4 CI entirely above 1.", False),
    ("crossover.{c}.{suite}.first_gpu_faster_scale", "Smallest such scale (null if none).", False),
    ("crossover.{c}.{suite}.stable_from_scale", "Smallest scale from which every larger scale is also gpu_faster.", False),
    ("crossover.{c}.{suite}.bracket", "[previous scale, first gpu_faster scale] or null.", False),
    ("crossover.{c}.{suite}.first_point_estimate_above_1", "Smallest scale whose point estimate exceeds 1 (ignores the interval).", False),
    ("crossover.{c}.{suite}.queries.{query}.{field}", "Per-query version of the five fields above (verdicts / estimates are dicts keyed by scale).", False),
    ("crossover.{c}.{suite}.queries.{query}.{field}.{scale}", "Per-query verdicts and estimates by scale.", False),
    ("coverage.matrix.{suite}.{query}.{scale}.{engine}.status", "supported | partial | failed | not_run across included rounds.", False),
    ("coverage.matrix.{suite}.{query}.{scale}.{engine}.rounds_ok", "Rounds with a valid timing.", False),
    ("coverage.matrix.{suite}.{query}.{scale}.{engine}.rounds_total", "Included rounds in this block.", False),
    ("coverage.matrix.{suite}.{query}.{scale}.{engine}.reason_class", "unsupported_operation | out_of_memory | timeout | error | session_failed | invalid | null.", False),
    ("coverage.matrix.{suite}.{query}.{scale}.{engine}.reason", "First recorded error text (truncated).", False),
    ("coverage.matrix.{suite}.{query}.{scale}.{engine}.predeclared_unsupported", "true for the section 8 pre-declared GPU gaps.", False),
    ("coverage.matrix.{suite}.{query}.{scale}.{engine}.unstable", "true if a coverage outcome (unsupported operation, out of memory) occurred in some rounds but not all (A1 unstable coverage): excluded from every contrast in all rounds.", False),
    ("output_agreement.{suite}.{scale}.{engine}.{field}", "Integrity evidence recomputed from the saved result rows against Polars: compared, failed, max_rel_diff (largest relative difference among numeric values, all accepted rounds), max_rel_diff_query (query attaining it), rounds, max_rel_diff_per_round (aligned with rounds; null if no numeric value).", False),
    ("output_agreement.{suite}.{scale}.{engine}.queries.{query}", "Largest relative difference of that query over the accepted rounds.", True),
    ("plan_variability.engines", "Engines whose saved final adaptive plans were compared (spark_cpu, spark_gpu).", False),
    ("plan_variability.note", "What the section is: descriptive, nothing in it feeds an estimate (medians stay as designed).", False),
    ("plan_variability.sessions_checked", "Spark sessions of the included rounds with at least one saved plan.", False),
    ("plan_variability.cells_checked", "(suite, scale, query, engine) Spark cells with at least one saved plan.", False),
    ("plan_variability.trials_with_plan", "Trials (warm-up and measured) whose plan file was read.", False),
    ("plan_variability.plan_files_missing", "Plan files named in results.json that could not be read.", False),
    ("plan_variability.cells_variable", "Cells whose trials do not all end in the same final plan (length of cells).", False),
    ("plan_variability.cells", "List with one entry per variable cell: suite, scale, query, engine, rounds, n_trials, n_measured, n_warmup, n_plans, reference_signature (plan with the lowest median measured time), initial_plan_identical (true when every trial started from the same initial plan; null if no initial plan was saved), measured_time_ranges_overlap (do the plans' measured-time ranges intersect), median_seconds_slowest_over_fastest, and plans: one entry per final plan {signature (12 hex digits of the normalized plan; equal plans have equal signatures on every host), n_measured, n_warmup, measured_per_round, warmup_per_round, measured_seconds {min, median, max}, warmup_seconds, execution (event-log stages, tasks and shuffle_write_bytes per action as {min, median, max}, with trials_with_event_log; null without a log), features {operators, joins, exchanges (kind(keys, partitions) -> count), shuffle_readers (coalesced|local|skewed|plain -> count)}, changes_vs_reference (feature -> name -> [reference count, this count], lines_only_in_this_plan, lines_only_in_reference_plan; null for the reference plan), trials [{round, phase, trial, seconds}]}.", False),
    ("unstable_coverage.note", "Why these cells are listed and why they are not estimates (amendment A1).", False),
    ("unstable_coverage.cells", "List of {suite, scale, query, engine, rounds, outcome_per_round, seconds_per_round, coverage_rounds, label}: raw per-round outcomes of unstable-coverage cells, labelled 'excluded by A1 (unstable coverage); not an estimate'.", False),
    ("coverage.discrepancies", "Cells that contradict the pre-declaration (declared but ran, or undeclared GPU failure).", False),
    ("coverage.predeclared_unsupported", "engine:query pairs pre-declared unsupported.", False),
    ("secondary.gpu_memory.capacity_bytes", "Device memory of the GPU (from gpu_inventory), bytes.", False),
    ("secondary.gpu_memory.note", "Sampling caveat and what the per-engine numbers mean.", False),
    ("secondary.gpu_memory.measure.{engine}", "What the engine's memory number is: polars_gpu 'process_high_water_mark' (nvidia-smi, RMM async pool); spark_gpu 'task_reported_max_device_memory' (event-log accumulator gpuMaxDeviceMemoryBytes) or 'pool_reservation_only' when the event logs carry no such accumulator (then Spark GPU is left out of the memory figure).", False),
    ("secondary.gpu_memory.measure_note.{engine}", "One-paragraph explanation of that measure and its limits.", False),
    ("secondary.gpu_memory.task_reported_cells.{engine}.{field}", "with_value / cells: Spark GPU (query, scale) cells with a task-reported device-memory value.", False),
    ("secondary.gpu_memory.constant_check.{engine}.{field}", "sessions (>= 3 sampled actions), constant (sessions whose sampled device memory stays within 5% of its minimum: a constant sample is a reserved pool, not memory in use), first_sample_median_bytes (median over sessions of the first sampled action, a warm-up) and later_minimum_median_bytes (median over sessions of the smallest later sample).", False),
    ("secondary.gpu_memory.high_water_check.{engine}.{field}", "sessions (>= 3 sampled actions), nondecreasing (sessions whose sampled device memory never falls, within 1% + 64 MiB, in time order: evidence that the sample is a session high-water mark rather than a per-query value), largest_fall_bytes (the largest drop below the running maximum in any session) and falling_sessions (list of {round, suite, scale, largest_fall_bytes, max_bytes} for the sessions whose sample does fall; empty when none).", False),
    ("secondary.gpu_memory.cells.{suite}.{scale}.{query}.{engine}.{field}", "recorded, per_round_max_bytes, rounds, max_bytes, median_bytes of the per-trial nvidia-smi sample (for Spark GPU: the pool reservation).", False),
    ("secondary.gpu_memory.cells.{suite}.{scale}.{query}.spark_gpu.task_reported.{field}", "Spark GPU only: recorded, per_round_max_bytes, rounds, max_bytes, median_bytes of the largest task-reported device memory of the action (not a simultaneous total).", False),
    ("secondary.gpu_memory.summary.{suite}.{scale}.{engine}.{field}", "n_queries, max_bytes, min_bytes, median_bytes, fraction_of_capacity_max over queries (nvidia-smi sample).", False),
    ("secondary.gpu_memory.summary.{suite}.{scale}.spark_gpu.task_reported.{field}", "Spark GPU only: the same summary over the task-reported device memory (null when no cell has a value).", True),
    ("secondary.process.{suite}.{scale}.{engine}.{field}", "Mean over rounds: process_wall_seconds, launch_to_engine_ready_seconds, session_and_table_setup_seconds, warmup_seconds, measured_seconds, n_sessions, per_round_process_wall_seconds.", False),
    ("secondary.cleanup.{suite}.{scale}.{engine}.{field}", "verified_gc cost: n_sessions, cleanups_per_session, mean_seconds_per_cleanup, max_seconds, total_seconds_per_session, share_of_process_wall.", True),
    ("secondary.energy.definition", "Energy-proxy definition and caveat.", False),
    ("secondary.energy.by_cell.{suite}.{scale}.{engine}.{field}", "n_sessions, mean_power_w, energy_wh, per_round_energy_wh, process_wall_seconds.", True),
    ("side_study.meta", "Opaque object: availability, versions, rounds, definition (ratio = newer / older Polars).", False),
    ("side_study.ratio.{suite}.{scale}.{field}", "Suite-level ratio newer / older Polars; same fields as {c}.{suite}.{scale}.", True),
    ("side_study.ratio.{suite}.{scale}.queries_excluded.{query}", "Excluded query reason.", True),
    ("side_study.ratio.{suite}.{scale}.q.{query}.{field}", "Per-query ratio newer / older Polars; same fields as {c}.{suite}.{scale}.q.{query}.", True),
    ("side_study.medians.{suite}.{scale}.{query}.{version}.rounds", "Side-study round ids aligned with per_round.", True),
    ("side_study.medians.{suite}.{scale}.{query}.{version}.per_round", "The version's per-round median seconds (median of three measured trials) for that query; used by the paper-2 protocol check.", True),
    ("side_study.ratio.{suite}.{scale}.geomean_seconds.{version}.{query}", "Geometric-mean time of that Polars version.", True),
    ("secondary.gpu_telemetry.meta.label", "Always 'descriptive telemetry; not a pre-registered contrast; no causal claim'.", False),
    ("secondary.gpu_telemetry.meta.available", "true when gpu.csv files were read (false with --no-energy or without gpu.csv).", False),
    ("secondary.gpu_telemetry.meta.reason", "Why it is unavailable (null when available).", False),
    ("secondary.gpu_telemetry.meta.definition", "What is pooled and what idle_fraction and utilization.gpu mean.", False),
    ("secondary.gpu_telemetry.meta.idle_threshold_pct", "A sample with utilization.gpu below this percentage counts as GPU-idle (10).", False),
    ("secondary.gpu_telemetry.meta.min_action_seconds", "Measured actions shorter than this (1 s) are left out of every statistic of the section.", False),
    ("secondary.gpu_telemetry.meta.sample_interval_ms", "Median spacing of the gpu.csv samples (about 200 ms); null without gpu.csv.", False),
    ("secondary.gpu_telemetry.meta.gpu_csv_runs", "Suite runs whose gpu.csv was read.", False),
    ("secondary.gpu_telemetry.meta.power_limit_w", "Current power limit of the first GPU from nvidia-smi -q in environment.json (null if not recorded).", False),
    ("secondary.gpu_telemetry.meta.power_limit_source", "Where power_limit_w came from.", False),
    ("secondary.gpu_telemetry.meta.max_sm_clock_mhz", "Maximum SM clock from nvidia-smi -q (null if not recorded).", False),
    ("secondary.gpu_telemetry.meta.resolution_note", "How the sampling interval limits short actions.", False),
    ("secondary.gpu_telemetry.cells.{suite}.{scale}.{engine}.{field}", "Pooled over the samples inside the measured actions (>= 1 s) of the included rounds: n_actions, n_actions_excluded_short (actions under 1 s), seconds_excluded_short, n_actions_without_samples, n_sessions, action_seconds, n_samples, util_median_pct, util_p90_pct (utilization.gpu), idle_fraction (share of samples with utilization.gpu below 10%), power_median_w, power_fraction_of_limit (median power / power_limit_w), sm_clock_median_mhz, sm_clock_fraction_of_max, mem_util_median_pct (utilization.memory); statistics are null when no action qualifies.", True),
    ("secondary.gpu_telemetry.cells.{suite}.{scale}.{engine}.rounds", "Round ids aligned with idle_fraction_by_round.", True),
    ("secondary.gpu_telemetry.cells.{suite}.{scale}.{engine}.idle_fraction_by_round", "idle_fraction per round (null for a round without a qualifying action).", True),
    ("secondary.gpu_telemetry.cells.{suite}.{scale}.{engine}.q.{query}.{field}", "Per query: n_actions, util_median_pct, util_p90_pct, idle_fraction.", True),
    ("exploratory.{e}.meta.label", "Always 'exploratory (post-hoc; not in the locked design §9)'. E1 and E4 are not part of C1-C4, the crossover verdict or any pre-registered estimate.", False),
    ("exploratory.{e}.meta.available", "true when a side study with two Polars versions exists (otherwise the suite blocks are absent).", False),
    ("exploratory.{e}.meta.contrast", "E1: 'Polars <new> CPU / Polars GPU'; E4: 'Polars <new> CPU / best GPU engine'.", False),
    ("exploratory.{e}.meta.counterpart", "The locked-design contrast it can be read next to (E1: c1, E4: c4).", False),
    ("exploratory.{e}.meta.numerator", "The CPU side: the newer Polars' side-study sessions (null if unavailable).", False),
    ("exploratory.{e}.meta.denominator", "The GPU side taken from the main rounds.", False),
    ("exploratory.{e}.meta.cpu_version", "Polars version of the CPU side, e.g. '2.0.0'.", False),
    ("exploratory.{e}.meta.side_study_rounds", "Round ids of the side study that enter the CPU side.", False),
    ("exploratory.{e}.meta.main_rounds", "Round ids of the main campaign that enter the GPU side.", False),
    ("exploratory.{e}.meta.interval", "How the interval is built: Welch-type on the log scale, unpaired sessions from different rounds.", False),
    ("exploratory.{e}.meta.definition", "What the ratio is and the orientation (above 1: the GPU side was faster, as for C1 and C4).", False),
    ("exploratory.{e}.meta.reason", "Why the analysis is unavailable (null when available).", False),
    ("exploratory.{e}.{suite}.{scale}.estimate", "Geometric mean over side-study rounds of the CPU suite geomean divided by the geometric mean over main rounds of the GPU suite geomean (queries timed in every round on both sides).", True),
    ("exploratory.{e}.{suite}.{scale}.ci_low", "Lower Welch-type 95% limit on the log scale (null with fewer than 2 rounds on either side).", True),
    ("exploratory.{e}.{suite}.{scale}.ci_high", "Upper limit.", True),
    ("exploratory.{e}.{suite}.{scale}.df", "Welch-Satterthwaite degrees of freedom (non-integer).", True),
    ("exploratory.{e}.{suite}.{scale}.log_se", "Standard error of the difference of mean log times.", True),
    ("exploratory.{e}.{suite}.{scale}.n_cpu_rounds", "Side-study rounds on the CPU side.", True),
    ("exploratory.{e}.{suite}.{scale}.n_gpu_rounds", "Main rounds on the GPU side.", True),
    ("exploratory.{e}.{suite}.{scale}.cpu_seconds", "Geometric mean over side-study rounds of the CPU suite geomean (seconds).", True),
    ("exploratory.{e}.{suite}.{scale}.gpu_seconds", "Same for the GPU side (seconds).", True),
    ("exploratory.{e}.{suite}.{scale}.cpu_per_round", "Per-round CPU suite geomean times (seconds).", True),
    ("exploratory.{e}.{suite}.{scale}.gpu_per_round", "Per-round GPU suite geomean times (seconds).", True),
    ("exploratory.{e}.{suite}.{scale}.cpu_rounds", "Round ids aligned with cpu_per_round.", True),
    ("exploratory.{e}.{suite}.{scale}.gpu_rounds", "Round ids aligned with gpu_per_round.", True),
    ("exploratory.{e}.{suite}.{scale}.status", "ok | no_common_query.", True),
    ("exploratory.{e}.{suite}.{scale}.n_queries", "Queries in the suite estimate.", True),
    ("exploratory.{e}.{suite}.{scale}.queries_used", "Query ids in the suite estimate.", True),
    ("exploratory.{e}.{suite}.{scale}.queries_excluded", "Dict query -> reason; empty when all queries are used.", True),
    ("exploratory.{e}.{suite}.{scale}.queries_excluded.{query}", "Why a query is outside the suite estimate.", True),
    ("exploratory.{e}.{suite}.{scale}.q.{query}.{field}", "Per-query version of the suite fields (estimate, ci_low, ci_high, df, log_se, n_cpu_rounds, n_gpu_rounds, cpu_seconds, gpu_seconds, cpu_per_round, gpu_per_round, cpu_rounds, gpu_rounds), plus status (ok|partial|unsupported|unstable|no_overlap|not_run).", True),
    ("exploratory.{e}.{suite}.{scale}.q.{query}.blocked_by", "List of {engine, reason_class, reason} that prevented the ratio.", True),
    ("exploratory.{e}.{suite}.{scale}.q.{query}.unstable_engines", "GPU engines left out by A1 (unstable coverage).", True),
    ("exploratory.{e}.{suite}.{scale}.q.{query}.best_engine_rounds", "E4 only: dict GPU engine -> main rounds in which it was the best.", True),
    ("exploratory.{e}.{suite}.{scale}.q.{query}.best_engine_rounds.{engine}", "E4 only: rounds in which this GPU engine was the best.", True),
    ("a100_vs_h100.meta", "Opaque object: hosts, non-equivalence flags, definition, engine facts.", False),
    ("a100_vs_h100.study.label", "Who the A100 side is, e.g. '2026-10-25 A100 study': the previous study's saved evidence (dated from its sessions), null when no A100 roots were given.", False),
    ("a100_vs_h100.study.previous_study", "true when the A100 roots are run_experiment output roots of an earlier study (no campaign.json), false for an A100 campaign root; null without roots.", False),
    ("a100_vs_h100.study.roots", "The A100 evidence roots that were read.", False),
    ("a100_vs_h100.study.first_session_utc", "Start of the earliest A100 session (UTC).", False),
    ("a100_vs_h100.study.last_session_utc", "Start of the latest A100 session (UTC).", False),
    ("a100_vs_h100.study.engines_present", "Engines with sessions in the earlier study.", False),
    ("a100_vs_h100.study.engines_compared", "Engines whose A100 / H100 ratio is reported (GPU engines present in both).", False),
    ("a100_vs_h100.study.engines_only_in_h100_campaign", "Engines of the H100 campaign that the earlier study did not run (e.g. polars_gpu).", False),
    ("a100_vs_h100.study.gpu", "A100 GPU name recorded by the earlier study.", False),
    ("a100_vs_h100.study.vcpus", "vCPUs allocated in the earlier study.", False),
    ("a100_vs_h100.study.host_ram_gib", "Approximate host RAM of the earlier study, GiB.", False),
    ("a100_vs_h100.study.polars_version", "Polars version of the earlier study's Polars sessions (null if several).", False),
    ("a100_vs_h100.study.polars_version_h100", "Polars version of the H100 campaign's Polars sessions (null if several).", False),
    ("a100_vs_h100.study.spark_version", "Spark version of the earlier study.", False),
    ("a100_vs_h100.study.spark_version_h100", "Spark version of the H100 campaign.", False),
    ("a100_vs_h100.study.cpu_threads", "Threads (--cpus) of the earlier study's sessions (null if several).", False),
    ("a100_vs_h100.study.cpu_threads_h100", "Threads (--cpus) of the H100 campaign's sessions.", False),
    ("a100_vs_h100.study.sessions_per_cell", "Sessions per (suite, scale, engine) in the earlier study (one).", False),
    ("a100_vs_h100.study.rounds_h100", "Rounds of the H100 campaign that enter the comparison.", False),
    ("a100_vs_h100.study.arguments", "List of {root, cpus, runs, driver_memory, engine_timeout_seconds, keep_going, query_cleanup, cleanup_timeout_seconds} recorded in each A100 root's environment.json.", False),
    ("a100_vs_h100.study.differences", "List of {field, engines, suites, a100_study, h100_campaign, in_compared_ratio}: every recorded field that differs between the earlier study and the H100 campaign (library version, threads, trials, policies, Spark GPU profile, jars, sessions_per_cell, engine_order, GPU, vCPUs, host RAM, spark_conf). A field the earlier study did not record has null on its side. in_compared_ratio: the entry applies to an engine whose ratio is reported (host and protocol entries apply to all).", False),
    ("a100_vs_h100.study.same", "List of {field, engine, suite, value} for recorded fields that are identical on both sides.", False),
    ("a100_vs_h100.study.spark_conf_differences", "Opaque object: Spark GPU configuration keys recorded by both sides whose values differ ({a100_study, h100_campaign, relative_difference}); run-specific keys are left out.", False),
    ("a100_vs_h100.study.spark_conf_keys_recorded.a100_study", "Number of Spark GPU configuration keys the earlier study recorded (it saved only the keys it set).", False),
    ("a100_vs_h100.study.spark_conf_keys_recorded.h100_campaign", "Number of Spark GPU configuration keys the H100 campaign recorded.", False),
    ("a100_vs_h100.study.plan_variability", "Opaque object with the structure of the top-level plan_variability, computed over the earlier study's Spark sessions (one session per cell): cells whose trials, warm-up included, ended in more than one final plan. Plan signatures of the two studies are not comparable (the earlier study's scan locations are sanitized); compare features.", False),
    ("a100_vs_h100.study.difference_headline", "One sentence: Polars version, threads and protocol differences (used in the F9 footnote and caption).", False),
    ("a100_vs_h100.study.difference_summary", "All differences in one sentence each (the full list behind the headline).", False),
    ("a100_vs_h100.ratio.{engine}.{suite}.{scale}.{field}", "A100 time / H100 time for a GPU engine (non-equivalent hosts); suite-level fields as above.", True),
    ("a100_vs_h100.ratio.{engine}.{suite}.{scale}.queries_excluded.{query}", "Excluded query reason.", True),
    ("a100_vs_h100.ratio.{engine}.{suite}.{scale}.q.{query}.{field}", "Per-query A100 / H100 ratio.", True),
]

# cross_host.json (cross-host mode): documented the same way; {host} is a host label (A100, H100, GH200, B200, ...)
CROSS_SCHEMA = [
    ("schema_version", "Schema version of cross_host.json ('1.0').", False),
    ("synthetic", "true if any host tree is synthetic; every output then carries 'SYNTHETIC — NOT RESULTS'.", False),
    ("watermark", "'SYNTHETIC — NOT RESULTS' or null.", False),
    ("generated_at_utc", "UTC time of the comparison.", False),
    ("reference", "Reference host label (default H100); every ratio is host / reference.", False),
    ("hosts", "All host labels in GPU-generation order (A100, H100, GH200, B200; unknown labels last).", False),
    ("hosts_not_included", "Ladder hosts (A100, H100, GH200, B200) that have no entry in this comparison (empty list when all four are present).", True),
    ("partial", "true when hosts_not_included is not empty: the comparison covers only part of the GPU-generation ladder; the report and F11 say so.", False),
    ("level", "Confidence level of the intervals (0.95).", False),
    ("definition", "How the ratio, the Welch-type interval and the suite-level estimate are defined.", False),
    ("caveats", "List of general caveats (separate campaigns, confounding, unpaired rounds, coverage exclusions).", False),
    ("caveats_by_host.{host}", "List of host-specific caveats derived from the identity differences versus the reference.", False),
    ("identity_differences.{host}", "Dict field -> [host value, reference value] for identity fields that differ (empty dict when identical).", True),
    ("identity_differences.{host}.{field}", "[host value, reference value] for gpu, gpu_memory_gib, cpu_model, arch, vcpus, cpus_arg, smt_threads_per_core, physical_cores, numa_nodes, hypervisor, host_ram_gib, driver_version, cuda_version, cuda_runtime_version, rapids_jar.", True),
    ("host_info.{host}.{field}", "Identity of a host (the 'host' block of its stats.json) plus role (reference|comparison), the design version it started under (design_commit, design_doc_sha256, amendments, later_amendments), n_rounds, rounds_included, engines, root, synthetic.", False),
    ("host_info.{host}.packages", "Opaque object: package versions found in the host's environment inventory.", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.estimate", "Suite-level time ratio host / reference: geometric-mean round medians over the queries valid in every round on both hosts (below 1: host faster).", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.ci_low", "Lower 95% limit, Welch-type interval on the log scale (null with fewer than 2 rounds on either host).", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.ci_high", "Upper 95% limit.", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.df", "Welch-Satterthwaite degrees of freedom (non-integer).", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.log_se", "Standard error of the difference of mean log times.", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.n_host", "Rounds of the host.", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.n_ref", "Rounds of the reference.", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.host_seconds", "Geometric mean over rounds of the host's per-round suite geomean time (seconds).", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.ref_seconds", "Same for the reference host (seconds).", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.host_per_round", "Host's per-round suite geomean times (seconds).", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.ref_per_round", "Reference's per-round suite geomean times (seconds).", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.host_rounds", "Round ids aligned with host_per_round.", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.ref_rounds", "Round ids aligned with ref_per_round.", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.status", "ok | no_common_query.", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.n_queries", "Queries in the suite-level estimate.", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.queries_used", "Query ids in the estimate.", False),
    ("ratio.{host}.{engine}.{suite}.{scale}.queries_excluded", "Dict query -> reason (partial rounds, unavailable on one host with the failure class); empty when all queries are used.", True),
    ("ratio.{host}.{engine}.{suite}.{scale}.queries_excluded.{query}", "Why a query is outside the suite estimate.", True),
    ("ratio_query.{host}.{engine}.{suite}.{scale}.{query}.{field}", "Per-query version: estimate, ci_low, ci_high, df, log_se, n_host, n_ref, host_seconds, ref_seconds, host_per_round, ref_per_round, status (ok|partial|unavailable), blocked_by.", False),
    ("ratio_query.{host}.{engine}.{suite}.{scale}.{query}.blocked_by", "List of {host, reason_class} that prevented the ratio.", False),
    ("speedup.{c}.{host}.{suite}.{scale}.{field}", "The host's own suite-level speedup of contrast c (estimate, ci_low, ci_high, df, n_rounds, n_queries), copied from that host's stats.json (not combined across hosts); the reference host is included.", False),
    ("coverage_differences.{host}", "List of {suite, scale, query, engine, host_status, host_reason_class, ref_status, ref_reason_class} for cells whose coverage outcome differs from the reference.", False),
    ("skipped.{host}", "Notes on (suite, scale) blocks present on only one of the two hosts.", False),
    ("agreement.{host}.{suite}.{scale}.{engine}.{field}", "Integrity comparison of the output disagreement with Polars: host_max_rel_diff and ref_max_rel_diff (largest relative difference, all accepted rounds), their worst queries, larger_than_reference, host_over_reference (their ratio; null if the reference is 0), host_within_gate (<= 1e-8), host_failed, host_rounds, ref_rounds, matched_rounds (round -> [host, reference]), queries_larger (queries whose difference exceeds the reference's).", False),
    ("plan_variability.{host}.cells_checked", "Spark cells with a saved plan on that host (null when the host's stats.json has no plan_variability section).", False),
    ("plan_variability.{host}.cells_variable", "Of those, cells whose trials ended in more than one final adaptive plan.", False),
    ("plan_variability.{host}.cells", "List with one entry per variable cell: suite, scale, query, engine, rounds, n_plans, initial_plan_identical, measured_time_ranges_overlap, median_seconds_slowest_over_fastest, reference_signature, and plans [{signature (equal plans have equal signatures on every host), n_measured, n_warmup, measured_median_seconds, warmup_median_seconds, exchanges, shuffle_write_bytes_median}]. Descriptive; no estimate depends on it.", False),
    ("gpu_telemetry.{host}.label", "'descriptive telemetry; not a pre-registered contrast; no causal claim'.", False),
    ("gpu_telemetry.{host}.available", "true when the host's stats.json has GPU telemetry from gpu.csv.", False),
    ("gpu_telemetry.{host}.sample_interval_ms", "Median spacing of the host's gpu.csv samples (about 200 ms).", False),
    ("gpu_telemetry.{host}.power_limit_w", "The card's current power limit (nvidia-smi -q), null if not recorded.", False),
    ("gpu_telemetry.{host}.max_sm_clock_mhz", "The card's maximum SM clock (nvidia-smi -q), null if not recorded.", False),
    ("gpu_telemetry.{host}.cells.{suite}.{scale}.{engine}.{field}", "n_actions, n_actions_excluded_short, n_samples, util_median_pct, util_p90_pct, idle_fraction, power_median_w, power_fraction_of_limit, sm_clock_median_mhz, sm_clock_fraction_of_max, mem_util_median_pct: as in the host's secondary.gpu_telemetry.cells (actions under 1 s excluded).", True),
    ("agreement.{host}.{suite}.{scale}.{engine}.matched_rounds.{round}", "[host value, reference value] of the largest relative difference in a round both hosts completed.", True),
]
CROSS_OPAQUE_PATTERNS = (re.compile(r"host_info\.[^.]+\.packages\Z"),)

SCHEMA_CONSTANTS = {  # values the free placeholders may take (documentation + test matcher)
    "group": ("by_engine",),
    "stat": ("n", "median", "mean", "p90", "max", "n_cells", "n_rounds", "mean_pct", "ci_low_pct", "ci_high_pct", "sd_log"),
}
OPAQUE_PREFIXES = ("campaign.design", "campaign.provenance", "host.packages", "order.design_balance", "side_study.meta", "a100_vs_h100.meta",
                   "a100_vs_h100.study.spark_conf_differences", "a100_vs_h100.study.plan_variability")


def schema_regex(pattern):
    parts = re.split(r"(\{[a-z_]+\})", pattern)
    token = {"{c}": "(?:c1|c2|c3|c4|c4_strict)", "{p}": "[1-4]"}
    return re.compile("".join(token.get(p, "[^.]+") if p.startswith("{") else re.escape(p) for p in parts) + r"\Z")


def schema_matches(key, schema=None):
    return [p for p, _, _ in (SCHEMA if schema is None else schema) if schema_regex(p).match(key)]


def flatten_stats(stats, opaque=OPAQUE_PREFIXES, opaque_patterns=()):
    """stats_flat.json content: dotted keys, with the opaque subtrees kept whole."""
    flat = {}

    def visit(value, path):
        if path in opaque or any(pattern.match(path) for pattern in opaque_patterns) or not isinstance(value, dict) or not value:
            flat[path] = value
            return
        for key, item in value.items():
            visit(item, f"{path}.{key}" if path else str(key))

    visit(stats, "")
    return flat


def schema_markdown():
    lines = ["# stats.json schema", "",
             "`stats.json` is nested; `stats_flat.json` holds the same leaves under dotted keys (`c1.baseline.sf300.estimate`). "
             "Scales are `sf1 sf10 sf30 sf100 sf300`, suites `baseline` / `complex`, engines `polars polars_gpu spark_cpu spark_gpu`, "
             "queries `q01 ...` / `cx_...`. `{c}` is one of `c1 c2 c3 c4 c4_strict`. Missing values are `null`, never absent. "
             "Keys listed here are stable; new keys may be added, none removed.", "",
             "Ratios are slower-is-numerator speedups `t_ref / t_engine`: above 1 the denominator engine is faster. "
             "`estimate` is a geometric mean over rounds; `ci_low`/`ci_high` are the 95% t-interval on the mean log-ratio "
             "(df = rounds - 1) back-transformed; `min`/`max` are the extreme per-round ratios. No p-values are computed.", "",
             "Contrasts: C1 `polars / polars_gpu`, C2 `spark_cpu / spark_gpu`, C3 `spark_gpu / polars_gpu`, "
             "C4 `polars / best GPU engine per query and round` (the faster of the GPU engines that produced a timing), "
             "`c4_strict` the same restricted to queries both GPU engines ran (sensitivity). Queries unsupported on polars_gpu "
             "are excluded from C1/C3/`c4_strict`; see `coverage`.", "",
             f"Exploratory block `exploratory.{{e}}` (`e1`: Polars 2.0.0 CPU / Polars GPU, `e4`: Polars 2.0.0 CPU / best GPU engine): **{EXPLORATORY_LABEL}**. "
             "The CPU side comes from the side-study sessions and the GPU side from the main rounds (unpaired), so the interval is Welch-type on the log scale; "
             "it is never used by C1-C4, the crossover or any other primary output.", "",
             "| Key pattern | Meaning |", "|---|---|"]
    for pattern, description, optional in SCHEMA:
        lines.append(f"| `{pattern}` | {description}{' (present only when applicable)' if optional else ''} |")
    lines += ["", "Placeholders: `{suite}`, `{scale}`, `{query}`, `{engine}`, `{c}`, `{e}` (`e1` `e4`), `{p}` (position 1-4), `{pred}` (predecessor engine or `none`), "
              "`{version}` (`polars_1_44_2`, `polars_2_0_0`), `{stat}`/`{field}` (the field names listed in the row), `{group}` (`by_engine`).", "",
              "## Cross-host file `cross_host.json`", "",
              "Written by `analyze_campaign.py --host LABEL=PATH ...` (and by `--synthetic`, under `cross_host/`); amendments A2/A3 of the design. "
              "`cross_host_flat.json` holds the same leaves under dotted keys (`ratio.B200.polars_gpu.baseline.sf300.estimate`). "
              "For the paper tooling, `xhost_<HOST>.json` repeats one host's suite-level ratios as `<engine>.<suite>.<scale>.{estimate,ci_low,ci_high,df,n_queries}` "
              "(pass it as `--stats xhost=PATH`). `{host}` is a host label (`A100 H100 GH200 B200`); per-host `stats.json` files keep the schema above. "
              "Ratios are host / reference time (below 1: the host is faster); intervals are Welch-type on the log scale.", "",
              "| Key pattern | Meaning |", "|---|---|"]
    for pattern, description, optional in CROSS_SCHEMA:
        lines.append(f"| `{pattern}` | {description}{' (present only when applicable)' if optional else ''} |")
    lines += ["", "Cross-host placeholders: `{host}`, `{engine}`, `{suite}`, `{scale}`, `{query}`, `{c}` (as above), `{field}` (the field names listed in the row).", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ tables (csv + md + tex)

DASH = "–"
TEX_ESCAPES = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
               "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex_escape(text):
    return "".join(TEX_ESCAPES.get(ch, ch) for ch in str(text)).replace("—", "---").replace("–", "--").replace("×", r"$\times$")


def sig3(value):
    """Three significant figures without exponents (0.306, 5.00, 12.3, 123, 1234)."""
    if value is None:
        return DASH
    if value == 0:
        return "0"
    decimals = max(0, 2 - math.floor(math.log10(abs(value))))
    return f"{value:.{decimals}f}"


def with_ci(record):
    if not record or record.get("estimate") is None:
        return DASH
    if record.get("ci_low") is None:
        return f"{sig3(record['estimate'])} (n=1)"
    return f"{sig3(record['estimate'])} [{sig3(record['ci_low'])}, {sig3(record['ci_high'])}]"


def span(record):
    if not record or record.get("min") is None:
        return DASH
    return f"{sig3(record['min'])}–{sig3(record['max'])}"


def gib(value):
    return DASH if value is None else f"{value / 1024 ** 3:.1f}"


def short_scale(name):
    return name.upper() if name else name


class Table:
    """A rectangular result: CSV carries raw values; md/tex carry the formatted text columns."""

    def __init__(self, name, title, columns, rows, text_columns=None, note="", longtable=False):
        self.name, self.title, self.columns, self.rows, self.note, self.longtable = name, title, columns, rows, note, longtable
        self.text_columns = text_columns or [(header, (lambda row, key=key: self.plain(row.get(key)))) for key, header in columns]

    @staticmethod
    def plain(value):
        if value is None:
            return DASH
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, float):
            return sig3(value)
        if isinstance(value, (list, tuple)):
            return ", ".join(Table.plain(v) for v in value) or DASH
        return str(value)

    def text_rows(self):
        return [[fn(row) for _, fn in self.text_columns] for row in self.rows]

    def markdown(self, synthetic=False, heading=True):
        headers = [h for h, _ in self.text_columns]
        lines = ([f"### {self.title}", ""] if heading else [])
        if synthetic:
            lines += [f"**{WATERMARK}**", ""]
        lines += ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
        for cells in self.text_rows():
            lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |")
        if self.note:
            lines += ["", self.note]
        return "\n".join(lines) + "\n"

    def latex(self, synthetic=False):
        headers = [h for h, _ in self.text_columns]
        body = self.text_rows()
        numeric = []
        for i in range(len(headers)):
            cells = [r[i] for r in body if r[i] not in ("", DASH)]
            numeric.append(bool(cells) and all(re.fullmatch(r"[-+]?[\d.,]+( ?\(n=1\))?( \[[-+\d.,\s]+\])?([–-][\d.]+)?", c) for c in cells))
        spec = "".join("r" if n else "l" for n in numeric)
        out = [f"% {self.name}: {self.title}", "% generated by analyze_campaign.py; requires booktabs" + (" and longtable" if self.longtable else "")]
        if synthetic:
            out.append(f"% {WATERMARK_ASCII}")
        head = " & ".join(tex_escape(h) for h in headers) + r" \\"
        banner = [rf"\multicolumn{{{len(headers)}}}{{c}}{{\textbf{{{WATERMARK_ASCII}}}}} \\"] if synthetic else []
        if self.longtable:
            out += [rf"\begin{{longtable}}{{{spec}}}", r"\toprule", *banner, head, r"\midrule", r"\endhead", r"\bottomrule", r"\endfoot"]
        else:
            out += [rf"\begin{{tabular}}{{{spec}}}", r"\toprule", *banner, head, r"\midrule"]
        for cells in body:
            out.append(" & ".join(tex_escape(c) for c in cells) + r" \\")
        out += [r"\end{longtable}", ""] if self.longtable else [r"\bottomrule", r"\end{tabular}", ""]
        return "\n".join(out)

    def write(self, directory, synthetic=False):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        keys = [k for k, _ in self.columns]
        with (directory / f"{self.name}.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["data_status", *keys])
            status = WATERMARK if synthetic else "measured"
            for row in self.rows:
                writer.writerow([status, *["" if row.get(k) is None else (json.dumps(row[k]) if isinstance(row.get(k), (list, dict)) else row.get(k)) for k in keys]])
        (directory / f"{self.name}.md").write_text(self.markdown(synthetic))
        (directory / f"{self.name}.tex").write_text(self.latex(synthetic))


def iter_estimates(stats, contrast_ids=("c1", "c2", "c3", "c4", "c4_strict")):
    for c in contrast_ids:
        for suite, by_scale in stats[c].items():
            for scale, record in by_scale.items():
                yield c, suite, scale, record


def build_tables(stats):
    tables = []
    label = {c: spec["label"] for c, spec in stats["contrasts"].items()}
    # t01: suite-level contrasts
    rows = [{"contrast": c, "suite": suite, "scale": scale, "n_queries": r["n_queries"], "n_rounds": r["n_rounds"], "estimate": r["estimate"],
             "ci_low": r["ci_low"], "ci_high": r["ci_high"], "min": r["min"], "max": r["max"], "_r": r}
            for c, suite, scale, r in iter_estimates(stats)]
    tables.append(Table("t01_suite_contrasts", "Suite-level speedup (geometric mean of per-query ratios; 95% t-interval over rounds)",
        [("contrast", "contrast"), ("suite", "suite"), ("scale", "scale"), ("n_queries", "n_queries"), ("n_rounds", "n_rounds"),
         ("estimate", "estimate"), ("ci_low", "ci_low"), ("ci_high", "ci_high"), ("min", "min"), ("max", "max")], rows,
        [("Contrast", lambda r: label[r["contrast"]] + (" (sensitivity)" if r["contrast"] == "c4_strict" else "")), ("Suite", lambda r: r["suite"]),
         ("Scale", lambda r: short_scale(r["scale"])), ("Queries", lambda r: str(r["n_queries"])),
         ("Speedup [95% CI]", lambda r: with_ci(r["_r"])), ("Round range", lambda r: span(r["_r"]))],
        note="Speedup = t_ref / t_engine; above 1 the denominator engine is faster. Queries unsupported on a side are excluded from that "
             "suite geomean (see t12_coverage). Intervals use df = rounds − 1."))
    # t02: per-query contrasts
    rows = []
    for c, suite, scale, r in iter_estimates(stats):
        for query, q in r["q"].items():
            rows.append({"contrast": c, "suite": suite, "scale": scale, "query": query, "status": q["status"], "n_rounds": q["n_rounds"],
                         "estimate": q["estimate"], "ci_low": q["ci_low"], "ci_high": q["ci_high"], "min": q["min"], "max": q["max"],
                         "blocked_by": "; ".join(f"{b['engine']}:{b['reason_class']}" for b in q["blocked_by"]), "_r": q})
    tables.append(Table("t02_query_contrasts", "Per-query speedup (geometric mean over rounds; 95% t-interval)",
        [("contrast", "contrast"), ("suite", "suite"), ("scale", "scale"), ("query", "query"), ("status", "status"), ("n_rounds", "n_rounds"),
         ("estimate", "estimate"), ("ci_low", "ci_low"), ("ci_high", "ci_high"), ("min", "min"), ("max", "max"), ("blocked_by", "blocked_by")], rows,
        [("Contrast", lambda r: r["contrast"].upper().replace("_STRICT", "s")), ("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])),
         ("Query", lambda r: r["query"]), ("Speedup [95% CI]", lambda r: with_ci(r["_r"]) if r["status"] in ("ok", "partial") else
                                            ("n/a: " + (r["blocked_by"] or r["status"]))), ("Round range", lambda r: span(r["_r"]))], longtable=True))
    # t03: median times
    rows = []
    for suite, by_scale in stats["medians"].items():
        for scale, by_query in by_scale.items():
            for query, by_engine in by_query.items():
                for engine, m in by_engine.items():
                    rows.append({"suite": suite, "scale": scale, "query": query, "engine": engine, "status": m["status"], "geomean_seconds": m["geomean"],
                                 "min_seconds": m["min"], "max_seconds": m["max"], "n_rounds": m["n_rounds"], "cv_between_rounds": m["cv_between_rounds"],
                                 "reason_class": m["reason_class"], "per_round_seconds": m["per_round"]})
    tables.append(Table("t03_median_times", "Per-query time: geometric mean over rounds of the per-round median of 3 trials (seconds)",
        [(k, k) for k in ("suite", "scale", "query", "engine", "status", "geomean_seconds", "min_seconds", "max_seconds", "n_rounds", "cv_between_rounds", "reason_class", "per_round_seconds")],
        rows, [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Query", lambda r: r["query"]),
               ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
               ("Time (s) [min–max]", lambda r: f"{sig3(r['geomean_seconds'])} [{sig3(r['min_seconds'])}–{sig3(r['max_seconds'])}]" if r["geomean_seconds"] is not None
                else (r["reason_class"] or r["status"]))], longtable=True))
    # t04: fastest engine
    rows = []
    for suite, by_scale in stats["fastest"].items():
        for scale, by_query in by_scale.items():
            for query, f in by_query.items():
                rows.append({"suite": suite, "scale": scale, "query": query, "winner": f.get("winner"), "geomean_seconds": f.get("geomean_seconds"),
                             "runner_up": f.get("runner_up"), "runner_up_over_winner": f.get("runner_up_over_winner"),
                             "ci_low": f.get("ci_low"), "ci_high": f.get("ci_high")})
    tables.append(Table("t04_fastest_engine", "Fastest engine per query and scale (descriptive, RQ2)",
        [(k, k) for k in ("suite", "scale", "query", "winner", "geomean_seconds", "runner_up", "runner_up_over_winner", "ci_low", "ci_high")], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Query", lambda r: r["query"]),
         ("Fastest", lambda r: ENGINE_NAMES.get(r["winner"], DASH)), ("Time (s)", lambda r: sig3(r["geomean_seconds"])),
         ("Runner-up / fastest [95% CI]", lambda r: with_ci({"estimate": r["runner_up_over_winner"], "ci_low": r["ci_low"], "ci_high": r["ci_high"]})
          if r["runner_up"] else DASH)], longtable=True,
        note="The winner is chosen from the same data, so the interval is descriptive."))
    # t05/t06: legacy totals and ratios
    rows = []
    for suite, by_scale in stats["legacy"]["totals"].items():
        for scale, by_engine in by_scale.items():
            for engine, t in by_engine.items():
                rows.append({"suite": suite, "scale": scale, "engine": engine, "complete": t["complete"], "mean_total_seconds": t["mean_total"],
                             "min_total_seconds": t["min_total"], "max_total_seconds": t["max_total"], "queries_missing": ", ".join(t["queries_missing"])})
    tables.append(Table("t05_legacy_totals", "Legacy sum of per-query medians (A100-study comparability; arithmetic, not a geometric mean)",
        [(k, k) for k in ("suite", "scale", "engine", "complete", "mean_total_seconds", "min_total_seconds", "max_total_seconds", "queries_missing")], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
         ("Sum of medians (s) [min–max]", lambda r: f"{sig3(r['mean_total_seconds'])} [{sig3(r['min_total_seconds'])}–{sig3(r['max_total_seconds'])}]"
          if r["complete"] else "n/a: " + (r["queries_missing"] or "no data"))],
        note="Totals exist only when every query has a valid median in every round; never combined with the geometric means of t01."))
    rows = []
    for suite, by_scale in stats["legacy"]["ratios"].items():
        for scale, by_c in by_scale.items():
            for c, r in by_c.items():
                rows.append({"suite": suite, "scale": scale, "contrast": c, "ratio_of_mean_totals": r["ratio_of_mean_totals"]})
    tables.append(Table("t06_legacy_ratios", "Legacy ratio of mean sums of medians",
        [(k, k) for k in ("suite", "scale", "contrast", "ratio_of_mean_totals")], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Contrast", lambda r: label[r["contrast"]]),
         ("Ratio of mean totals", lambda r: sig3(r["ratio_of_mean_totals"]))],
        note="Not comparable with the geometric means of t01: a sum is dominated by the longest queries."))
    # t07/t08: variability
    rows = []
    for engine, v in stats["variability"]["by_engine"].items():
        w, b = v["within_session"], v["between_rounds"]
        rows.append({"engine": engine, "n_within": w["n"], "within_median": w["median"], "within_p90": w["p90"], "within_max": w["max"],
                     "n_between": b["n"], "between_median": b["median"], "between_p90": b["p90"], "between_max": b["max"]})
    pct = lambda x: DASH if x is None else f"{100 * x:.1f}%"
    tables.append(Table("t07_variability", "Coefficient of variation per engine: within session (3 trials) and between rounds",
        [(k, k) for k in ("engine", "n_within", "within_median", "within_p90", "within_max", "n_between", "between_median", "between_p90", "between_max")], rows,
        [("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])), ("Within: median CV", lambda r: pct(r["within_median"])),
         ("Within: p90", lambda r: pct(r["within_p90"])), ("Within: max", lambda r: pct(r["within_max"])),
         ("Between: median CV", lambda r: pct(r["between_median"])), ("Between: p90", lambda r: pct(r["between_p90"])),
         ("Between: max", lambda r: pct(r["between_max"]))],
        note="Within-session CV uses the 3 measured trials of each cell and round; between-round CV uses the per-round medians of each cell."))
    rows = []
    for engine, by_suite in stats["variability"]["by_engine_scale"].items():
        for suite, by_scale in by_suite.items():
            for scale, v in by_scale.items():
                rows.append({"engine": engine, "suite": suite, "scale": scale, "within_median": v["within_session"]["median"],
                             "between_median": v["between_rounds"]["median"], "n_cells": v["between_rounds"]["n"]})
    tables.append(Table("t08_variability_by_scale", "Median CV by engine, suite and scale",
        [(k, k) for k in ("engine", "suite", "scale", "n_cells", "within_median", "between_median")], rows,
        [("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])), ("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])),
         ("Within-session median CV", lambda r: pct(r["within_median"])), ("Between-round median CV", lambda r: pct(r["between_median"]))]))
    # t09/t10: order effects
    def order_rows(group, key):
        out = []
        for k, v in stats["order"][group].items():
            out.append({key: k, "n_cells": v["n_cells"], "n_rounds": v["n_rounds"], "mean_pct": v["mean_pct"], "ci_low_pct": v["ci_low_pct"], "ci_high_pct": v["ci_high_pct"]})
        return out
    pct_ci = lambda r: DASH if r["mean_pct"] is None else (f"{r['mean_pct']:+.1f}%" + ("" if r["ci_low_pct"] is None else f" [{r['ci_low_pct']:+.1f}, {r['ci_high_pct']:+.1f}]"))
    tables.append(Table("t09_order_position", "Time deviation by session position within a block (descriptive)",
        [(k, k) for k in ("position", "n_cells", "n_rounds", "mean_pct", "ci_low_pct", "ci_high_pct")], order_rows("position", "position"),
        [("Position", lambda r: r["position"]), ("Cells", lambda r: str(r["n_cells"])), ("Mean deviation [95% CI]", pct_ci)],
        note="Deviation of each cell's time from its own mean over rounds, averaged over cells, then a t-interval over rounds. "
             "Reported descriptively; the Williams design balances positions across engines."))
    tables.append(Table("t10_order_predecessor", "Time deviation by predecessor engine (first-order carryover, descriptive)",
        [(k, k) for k in ("predecessor", "n_cells", "n_rounds", "mean_pct", "ci_low_pct", "ci_high_pct")], order_rows("predecessor", "predecessor"),
        [("Predecessor", lambda r: ENGINE_NAMES.get(r["predecessor"], "none (first in block)")), ("Cells", lambda r: str(r["n_cells"])),
         ("Mean deviation [95% CI]", pct_ci)]))
    # t11: crossover
    rows = []
    for c, by_suite in stats["crossover"].items():
        for suite, x in by_suite.items():
            rows.append({"contrast": c, "suite": suite, "crossover_found": x["crossover_found"], "first_gpu_faster_scale": x["first_gpu_faster_scale"],
                         "stable_from_scale": x["stable_from_scale"], "first_point_estimate_above_1": x["first_point_estimate_above_1"],
                         "verdicts": "; ".join(f"{s}:{v}" for s, v in x["verdicts"].items())})
    tables.append(Table("t11_crossover", "RQ3: scale at which the C4 interval lies entirely above 1 (GPU faster than the best CPU engine side)",
        [(k, k) for k in ("contrast", "suite", "crossover_found", "first_gpu_faster_scale", "stable_from_scale", "first_point_estimate_above_1", "verdicts")], rows,
        [("Contrast", lambda r: "C4" if r["contrast"] == "c4" else "C4 (both GPU engines)"), ("Suite", lambda r: r["suite"]),
         ("First scale CI > 1", lambda r: short_scale(r["first_gpu_faster_scale"]) if r["first_gpu_faster_scale"] else "none"),
         ("Stable from", lambda r: short_scale(r["stable_from_scale"]) if r["stable_from_scale"] else "none"),
         ("First point estimate > 1", lambda r: short_scale(r["first_point_estimate_above_1"]) if r["first_point_estimate_above_1"] else "none"),
         ("Verdict per scale", lambda r: r["verdicts"].replace("_", " "))],
        note="gpu faster: CI entirely above 1; cpu faster: entirely below 1; inconclusive: interval contains 1."))
    # t12: coverage
    rows = []
    for suite, by_query in stats["coverage"]["matrix"].items():
        for query, by_scale in by_query.items():
            for scale, by_engine in by_scale.items():
                for engine, cell in by_engine.items():
                    rows.append({"suite": suite, "query": query, "scale": scale, "engine": engine, "status": cell["status"], "rounds_ok": cell["rounds_ok"],
                                 "rounds_total": cell["rounds_total"], "reason_class": cell["reason_class"], "predeclared_unsupported": cell["predeclared_unsupported"],
                                 "unstable": cell["unstable"], "reason": cell["reason"]})
    tables.append(Table("t12_coverage", "RQ4: GPU coverage (support across included rounds)",
        [(k, k) for k in ("suite", "query", "scale", "engine", "status", "rounds_ok", "rounds_total", "reason_class", "predeclared_unsupported", "unstable", "reason")], rows,
        [("Suite", lambda r: r["suite"]), ("Query", lambda r: r["query"]), ("Scale", lambda r: short_scale(r["scale"])),
         ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
         ("Status", lambda r: "unstable (A1: excluded)" if r["unstable"] else (r["status"] if r["status"] != "failed" else f"failed ({(r['reason_class'] or 'error').replace('_', ' ')})")),
         ("Rounds ok", lambda r: f"{r['rounds_ok']}/{r['rounds_total']}"), ("Pre-declared", lambda r: "yes" if r["predeclared_unsupported"] else "")], longtable=True))
    # t13: GPU memory (the measure differs by engine: it is a column, not a footnote)
    memory = stats["secondary"]["gpu_memory"]
    measure_label = {"process_high_water_mark": "nvidia-smi, process high-water mark (RMM pool)", "task_reported_max_device_memory": "task-reported max (not a total)",
                     "pool_reservation_only": "nvidia-smi, pool reservation"}
    rows = []
    for suite, by_scale in memory["summary"].items():
        for scale, by_engine in by_scale.items():
            for engine, m in by_engine.items():
                if engine == "spark_gpu":
                    flat = memory["constant_check"]["spark_gpu"]
                    smi = ("nvidia-smi, pool reservation" if not flat["sessions"] or flat["constant"] == flat["sessions"]
                           else "nvidia-smi sample (not constant within sessions)")
                    rows.append({"suite": suite, "scale": scale, "engine": engine, "measure": smi, "n_queries": m["n_queries"],
                                 "max_bytes": m["max_bytes"], "median_bytes": m["median_bytes"], "min_bytes": m["min_bytes"],
                                 "fraction_of_capacity_max": m["fraction_of_capacity_max"]})
                    t = m.get("task_reported")
                    if t:
                        rows.append({"suite": suite, "scale": scale, "engine": engine, "measure": measure_label["task_reported_max_device_memory"],
                                     "n_queries": t["n_queries"], "max_bytes": t["max_bytes"], "median_bytes": t["median_bytes"], "min_bytes": t["min_bytes"],
                                     "fraction_of_capacity_max": t["fraction_of_capacity_max"]})
                else:
                    rows.append({"suite": suite, "scale": scale, "engine": engine, "measure": measure_label[memory["measure"][engine]], "n_queries": m["n_queries"],
                                 "max_bytes": m["max_bytes"], "median_bytes": m["median_bytes"], "min_bytes": m["min_bytes"],
                                 "fraction_of_capacity_max": m["fraction_of_capacity_max"]})
    tables.append(Table("t13_gpu_memory", "GPU memory per engine and scale (what is measured differs by engine: see the Measure column)",
        [(k, k) for k in ("suite", "scale", "engine", "measure", "n_queries", "min_bytes", "median_bytes", "max_bytes", "fraction_of_capacity_max")], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
         ("Measure", lambda r: r["measure"]),
         ("Min–max over queries (GiB)", lambda r: f"{gib(r['min_bytes'])}–{gib(r['max_bytes'])}"), ("Median (GiB)", lambda r: gib(r["median_bytes"])),
         ("Max / device", lambda r: pct(r["fraction_of_capacity_max"]))],
        note=memory["note"] + " Polars GPU: " + memory["measure_note"]["polars_gpu"] + ". Spark GPU: " + memory["measure_note"]["spark_gpu"] + "."))
    # t14: process time
    rows = []
    for suite, by_scale in stats["secondary"]["process"].items():
        for scale, by_engine in by_scale.items():
            for engine, p in by_engine.items():
                rows.append({"suite": suite, "scale": scale, "engine": engine, "n_sessions": p["n_sessions"], "process_wall_seconds": p["process_wall_seconds"],
                             "launch_to_engine_ready_seconds": p["launch_to_engine_ready_seconds"],
                             "session_and_table_setup_seconds": p["session_and_table_setup_seconds"], "warmup_seconds": p["warmup_seconds"],
                             "measured_seconds": p["measured_seconds"]})
    tables.append(Table("t14_process_time", "Process wall time and startup (mean over rounds, seconds; overlapping scopes, do not add)",
        [(k, k) for k in ("suite", "scale", "engine", "n_sessions", "process_wall_seconds", "launch_to_engine_ready_seconds", "session_and_table_setup_seconds",
                          "warmup_seconds", "measured_seconds")], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
         ("Process wall", lambda r: sig3(r["process_wall_seconds"])), ("Launch → ready", lambda r: sig3(r["launch_to_engine_ready_seconds"])),
         ("Setup", lambda r: sig3(r["session_and_table_setup_seconds"])), ("Warmups", lambda r: sig3(r["warmup_seconds"])),
         ("Measured", lambda r: sig3(r["measured_seconds"]))], longtable=True))
    # t15: cleanup
    rows = []
    for suite, by_scale in stats["secondary"]["cleanup"].items():
        for scale, by_engine in by_scale.items():
            for engine, c in by_engine.items():
                rows.append({"suite": suite, "scale": scale, "engine": engine, "cleanups_per_session": c["cleanups_per_session"],
                             "mean_seconds_per_cleanup": c["mean_seconds_per_cleanup"], "max_seconds": c["max_seconds"],
                             "total_seconds_per_session": c["total_seconds_per_session"], "share_of_process_wall": c["share_of_process_wall"]})
    tables.append(Table("t15_cleanup_cost", "verified_gc cleanup cost (outside the query timers)",
        [(k, k) for k in ("suite", "scale", "engine", "cleanups_per_session", "mean_seconds_per_cleanup", "max_seconds", "total_seconds_per_session", "share_of_process_wall")], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
         ("Per cleanup (s)", lambda r: sig3(r["mean_seconds_per_cleanup"])), ("Max (s)", lambda r: sig3(r["max_seconds"])),
         ("Per session (s)", lambda r: sig3(r["total_seconds_per_session"])), ("Share of wall", lambda r: pct(r["share_of_process_wall"]))]))
    # t16: energy
    rows = []
    for suite, by_scale in stats["secondary"]["energy"]["by_cell"].items():
        for scale, by_engine in by_scale.items():
            for engine, e in by_engine.items():
                rows.append({"suite": suite, "scale": scale, "engine": engine, "n_sessions": e["n_sessions"], "mean_power_w": e["mean_power_w"],
                             "energy_wh": e["energy_wh"], "process_wall_seconds": e["process_wall_seconds"]})
    tables.append(Table("t16_energy_proxy", "GPU board energy proxy per session (mean power.draw × process wall time)",
        [(k, k) for k in ("suite", "scale", "engine", "n_sessions", "mean_power_w", "process_wall_seconds", "energy_wh")], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
         ("Mean power (W)", lambda r: sig3(r["mean_power_w"])), ("Wall (s)", lambda r: sig3(r["process_wall_seconds"])), ("Energy (Wh)", lambda r: sig3(r["energy_wh"]))],
        note=stats["secondary"]["energy"]["definition"], longtable=True))
    # t17: side study
    rows = []
    for suite, by_scale in stats["side_study"]["ratio"].items():
        for scale, r in by_scale.items():
            rows.append({"suite": suite, "scale": scale, "n_queries": r["n_queries"], "n_rounds": r["n_rounds"], "estimate": r["estimate"], "ci_low": r["ci_low"],
                         "ci_high": r["ci_high"], "min": r["min"], "max": r["max"], "_r": r})
    tables.append(Table("t17_polars_version_bridge", "RQ5: Polars 2.0.0 / 1.44.2 time ratio (CPU, same host); above 1 means 2.0.0 is slower",
        [(k, k) for k in ("suite", "scale", "n_queries", "n_rounds", "estimate", "ci_low", "ci_high", "min", "max")], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Queries", lambda r: str(r["n_queries"])),
         ("Ratio 2.0.0 / 1.44.2 [95% CI]", lambda r: with_ci(r["_r"])), ("Round range", lambda r: span(r["_r"]))]))
    # t18: A100 vs H100
    rows = []
    for engine, by_suite in stats["a100_vs_h100"]["ratio"].items():
        for suite, by_scale in by_suite.items():
            for scale, r in by_scale.items():
                rows.append({"engine": engine, "suite": suite, "scale": scale, "n_queries": r["n_queries"], "n_rounds": r["n_rounds"], "estimate": r["estimate"],
                             "ci_low": r["ci_low"], "ci_high": r["ci_high"], "min": r["min"], "max": r["max"], "_r": r})
    tables.append(Table("t18_a100_vs_h100", "A100 → H100 time ratio for GPU engines (NON-EQUIVALENT hosts; CPU engines not compared)",
        [(k, k) for k in ("engine", "suite", "scale", "n_queries", "n_rounds", "estimate", "ci_low", "ci_high", "min", "max")], rows,
        [("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])), ("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])),
         ("Queries", lambda r: str(r["n_queries"])), ("A100 / H100 time [95% CI over H100 rounds]", lambda r: with_ci(r["_r"]))],
        note=stats["a100_vs_h100"]["meta"]["definition"] + a100_study_note(stats)))
    # t22: what differs between the earlier A100 study and the H100 campaign
    study = stats["a100_vs_h100"]["study"]
    shown = lambda values: " / ".join("not recorded" if v is None else str(v) for v in values)
    rows = [{"field": d["field"], "engines": d["engines"], "suites": d["suites"], "a100_study": shown(d["a100_study"]), "h100_campaign": shown(d["h100_campaign"]),
             "in_compared_ratio": d["in_compared_ratio"]} for d in study["differences"] if d["field"] != "spark_conf"]
    rows += [{"field": k, "engines": ["spark_gpu"], "suites": [], "a100_study": shown(v["a100_study"] if len(v["a100_study"]) < 3 else [f"{len(v['a100_study'])} values"]),
              "h100_campaign": shown(v["h100_campaign"] if len(v["h100_campaign"]) < 3 else [f"{len(v['h100_campaign'])} values"]), "in_compared_ratio": True}
             for k, v in study["spark_conf_differences"].items()]
    tables.append(Table("t22_a100_study_differences", f"What differs between the {study['label'] or 'earlier A100 study'} and the H100 campaign (read from the result files)",
        [(k, k) for k in ("field", "engines", "suites", "a100_study", "h100_campaign", "in_compared_ratio")], rows,
        [("Field", lambda r: STUDY_FIELD_LABELS.get(r["field"], r["field"])), ("Engines", lambda r: ", ".join(ENGINE_NAMES.get(e, e) for e in r["engines"]) or "all"),
         ("Suites", lambda r: ", ".join(r["suites"]) or "all"), ("A100 study", lambda r: r["a100_study"]), ("H100 campaign", lambda r: r["h100_campaign"]),
         ("Engine in the ratio", lambda r: "yes" if r["in_compared_ratio"] else "no")],
        note="'not recorded': the earlier study's result files carry no such field. CPU engines are not compared across the two.", longtable=True))
    # t23: exploratory E1 / E4 next to their locked-design counterparts
    rows = []
    for name in ("e1", "e4"):
        block = stats["exploratory"][name]
        for suite in SUITES:
            for scale, r in block.get(suite, {}).items():
                c = stats[block["meta"]["counterpart"]][suite][scale]
                rows.append({"contrast": name.upper(), "suite": suite, "scale": scale, "n_queries": r["n_queries"], "n_cpu_rounds": r["n_cpu_rounds"], "n_gpu_rounds": r["n_gpu_rounds"],
                             "estimate": r["estimate"], "ci_low": r["ci_low"], "ci_high": r["ci_high"], "df": r["df"],
                             "counterpart": block["meta"]["counterpart"].upper(), "counterpart_n_queries": c["n_queries"], "counterpart_estimate": c["estimate"],
                             "counterpart_ci_low": c["ci_low"], "counterpart_ci_high": c["ci_high"], "_r": r, "_c": c})
    ci_text = lambda r: DASH if r["estimate"] is None else (f"{sig3(r['estimate'])} [{sig3(r['ci_low'])}, {sig3(r['ci_high'])}]" if r["ci_low"] is not None else f"{sig3(r['estimate'])} (no interval)")
    tables.append(Table("t23_exploratory", f"{EXPLORATORY_LABEL}: Polars 2.0.0 CPU (side-study sessions) against the GPU engines of the main rounds, next to the locked-design contrasts",
        [(k, k) for k in ("contrast", "suite", "scale", "n_queries", "n_cpu_rounds", "n_gpu_rounds", "estimate", "ci_low", "ci_high", "df", "counterpart",
                          "counterpart_n_queries", "counterpart_estimate", "counterpart_ci_low", "counterpart_ci_high")], rows,
        [("Exploratory", lambda r: r["contrast"] + " (exploratory)"), ("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])),
         ("Queries", lambda r: str(r["n_queries"])),
         ("Exploratory ratio [Welch 95% CI, unpaired]", lambda r: ci_text(r["_r"])),
         ("Rounds (CPU side / GPU side)", lambda r: f"{r['n_cpu_rounds']} / {r['n_gpu_rounds']}"),
         ("Locked-design counterpart [t 95% CI over rounds]", lambda r: r["counterpart"] + " " + ci_text(r["_c"]) + f" ({r['counterpart_n_queries']} queries)")],
        note=EXPLORATORY_LABEL + ". Ratio = time of the Polars CPU side-study sessions / time of the GPU side of the main rounds (above 1: the GPU side was faster, as for C1 and C4). "
             + EXPLORATORY_INTERVAL + ".", longtable=True))
    # t24: descriptive GPU telemetry
    telemetry = stats["secondary"]["gpu_telemetry"]
    rows = [{"suite": suite, "scale": scale, "engine": engine, **{k: r[k] for k in (
        "n_actions", "n_actions_excluded_short", "n_samples", "util_median_pct", "util_p90_pct", "idle_fraction", "power_median_w", "power_fraction_of_limit",
        "sm_clock_median_mhz", "sm_clock_fraction_of_max", "mem_util_median_pct")}}
            for suite, by_scale in telemetry["cells"].items() for scale, by_engine in by_scale.items() for engine, r in by_engine.items()]
    num = lambda v, f="{:.0f}": DASH if v is None else f.format(v)
    tables.append(Table("t24_gpu_telemetry", f"GPU telemetry inside measured actions of at least {TELEMETRY_MIN_ACTION_SECONDS:g} s ({TELEMETRY_LABEL})",
        [(k, k) for k in ("suite", "scale", "engine", "n_actions", "n_actions_excluded_short", "n_samples", "util_median_pct", "util_p90_pct", "idle_fraction", "power_median_w",
                          "power_fraction_of_limit", "sm_clock_median_mhz", "sm_clock_fraction_of_max", "mem_util_median_pct")], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
         ("Actions used / left out (< 1 s)", lambda r: f"{r['n_actions']} / {r['n_actions_excluded_short']}"), ("Samples", lambda r: str(r["n_samples"])),
         ("GPU util median / p90 (%)", lambda r: f"{num(r['util_median_pct'])} / {num(r['util_p90_pct'])}" if r["util_median_pct"] is not None else DASH),
         (f"GPU-idle fraction (util < {TELEMETRY_IDLE_PCT:g}%)", lambda r: DASH if r["idle_fraction"] is None else f"{100 * r['idle_fraction']:.1f}%"),
         ("Power median W (fraction of limit)", lambda r: DASH if r["power_median_w"] is None else f"{r['power_median_w']:.0f} W" + (f" ({100 * r['power_fraction_of_limit']:.0f}%)" if r["power_fraction_of_limit"] is not None else "")),
         ("SM clock median MHz (fraction of max)", lambda r: DASH if r["sm_clock_median_mhz"] is None else f"{r['sm_clock_median_mhz']:.0f}" + (f" ({100 * r['sm_clock_fraction_of_max']:.0f}%)" if r["sm_clock_fraction_of_max"] is not None else "")),
         ("Memory util median (%)", lambda r: num(r["mem_util_median_pct"]))],
        note=TELEMETRY_LABEL + ". " + telemetry["meta"]["resolution_note"] + ".", longtable=True))
    # t19: rounds
    rows = [{"round": r, "label": f"round-{r}", "status": "included", "reason": ""} for r in stats["campaign"]["rounds_included"]]
    rows += [{"round": x["round"], "label": x["label"], "status": "excluded", "reason": x["reason"]} for x in stats["campaign"]["rounds_excluded"]]
    rows.sort(key=lambda r: (r["round"], r["label"]))
    tables.append(Table("t19_rounds", "Rounds included in and excluded from the estimates",
        [(k, k) for k in ("round", "label", "status", "reason")], rows,
        [("Round", lambda r: r["label"]), ("Status", lambda r: r["status"]), ("Reason", lambda r: r["reason"] or DASH)]))
    # t20: unstable coverage (supplementary; excluded from every estimate)
    rows = [{"suite": c["suite"], "scale": c["scale"], "query": c["query"], "engine": c["engine"], "rounds": c["rounds"],
             "outcome_per_round": c["outcome_per_round"], "seconds_per_round": c["seconds_per_round"], "label": c["label"]}
            for c in stats["unstable_coverage"]["cells"]]
    tables.append(Table("t20_unstable_coverage", "Unstable coverage: " + UNSTABLE_NOTE + " (raw per-round values)",
        [(k, k) for k in ("suite", "scale", "query", "engine", "rounds", "outcome_per_round", "seconds_per_round", "label")], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Query", lambda r: r["query"]),
         ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
         ("Outcome per round (time of a completed round, s)", lambda r: "; ".join(
             f"r{rd}: {o.replace('_', ' ')}" + (f" ({sig3(sec)})" if sec is not None and o == "ok" else "") for rd, o, sec in zip(r["rounds"], r["outcome_per_round"], r["seconds_per_round"]))),
         ("Status", lambda r: UNSTABLE_NOTE)],
        note=stats["unstable_coverage"]["note"], longtable=True))
    # t21: plan variability (descriptive; no estimate depends on it)
    rows = []
    for c in stats["plan_variability"]["cells"]:
        for plan in c["plans"]:
            execution, seconds = plan["execution"] or {}, plan["measured_seconds"] or {}
            rows.append({"suite": c["suite"], "scale": c["scale"], "query": c["query"], "engine": c["engine"], "plan": plan["signature"],
                         "role": "lowest median" if plan["signature"] == c["reference_signature"] else "other",
                         "n_measured": plan["n_measured"], "n_warmup": plan["n_warmup"],
                         "measured_per_round": "; ".join(f"r{r}: {n}" for r, n in plan["measured_per_round"].items()) or None,
                         "seconds_min": seconds.get("min"), "seconds_median": seconds.get("median"), "seconds_max": seconds.get("max"),
                         "exchanges": sum(plan["features"]["exchanges"].values()),
                         "shuffle_readers": ", ".join(f"{k} {v}" for k, v in plan["features"]["shuffle_readers"].items()) or None,
                         "stages": (execution.get("stages") or {}).get("median"), "tasks": (execution.get("tasks") or {}).get("median"),
                         "shuffle_write_gib": ((execution.get("shuffle_write_bytes") or {}).get("median") or 0) / 2 ** 30 if execution else None})
    tables.append(Table("t21_plan_variability", "Plan variability: Spark cells whose trials end in more than one final adaptive plan (descriptive)",
        [(k, k) for k in ("suite", "scale", "query", "engine", "plan", "role", "n_measured", "n_warmup", "measured_per_round", "seconds_min",
                          "seconds_median", "seconds_max", "exchanges", "shuffle_readers", "stages", "tasks", "shuffle_write_gib")], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Query", lambda r: r["query"]),
         ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])), ("Plan", lambda r: r["plan"] + (" *" if r["role"] != "other" else "")),
         ("Measured trials (per round)", lambda r: f"{r['n_measured']}" + (f" ({r['measured_per_round']})" if r["measured_per_round"] else "")),
         ("Warm-ups", lambda r: str(r["n_warmup"])),
         ("Measured s (min / median / max)", lambda r: "/".join(sig3(r[k]) for k in ("seconds_min", "seconds_median", "seconds_max")) if r["seconds_median"] is not None else DASH),
         ("Exchanges", lambda r: str(r["exchanges"])), ("Shuffle readers", lambda r: r["shuffle_readers"] or DASH),
         ("Stages / tasks (median per action)", lambda r: f"{sig3(r['stages'])} / {sig3(r['tasks'])}" if r["stages"] is not None else DASH),
         ("Shuffle written (GiB, median)", lambda r: sig3(r["shuffle_write_gib"]) if r["shuffle_write_gib"] is not None else DASH)],
        note="* plan with the lowest median measured time in its cell (the reference for the listed changes).", longtable=True))
    return tables


# ------------------------------------------------------------------ report and outputs

def filtered(table, predicate, drop=()):
    return Table(table.name, table.title, table.columns, [r for r in table.rows if predicate(r)],
                 [tc for tc in table.text_columns if tc[0] not in drop], "", table.longtable)


def verdict_tally(stats):
    lines = ["| Contrast | Suite | Scales with CI above 1 | Scales with CI below 1 | Scales with CI containing 1 |", "|---|---|---|---|---|"]
    for c in ("c1", "c2", "c3", "c4", "c4_strict"):
        for suite, by_scale in stats[c].items():
            groups = {"above": [], "below": [], "contains": []}
            for scale, r in by_scale.items():
                if r["ci_low"] is None:
                    continue
                groups["above" if r["ci_low"] > 1 else "below" if r["ci_high"] < 1 else "contains"].append(short_scale(scale))
            lines.append(f"| {stats['contrasts'][c]['label']} | {suite} | {', '.join(groups['above']) or DASH} | "
                         f"{', '.join(groups['below']) or DASH} | {', '.join(groups['contains']) or DASH} |")
    return "\n".join(lines) + "\n"


def winner_tally(stats):
    engines = stats["campaign"]["engines"]
    lines = ["| Suite | Scale | " + " | ".join(ENGINE_NAMES.get(e, e) for e in engines) + " |", "|---|---|" + "---|" * len(engines)]
    for suite, by_scale in stats["fastest"].items():
        for scale, by_query in by_scale.items():
            counts = {e: sum(1 for f in by_query.values() if f.get("winner") == e) for e in engines}
            lines.append(f"| {suite} | {short_scale(scale)} | " + " | ".join(f"{counts[e]}/{len(by_query)}" for e in engines) + " |")
    return "\n".join(lines) + "\n"


def report_text(stats, tables):
    t = {table.name: table for table in tables}
    synthetic = stats["synthetic"]
    campaign = stats["campaign"]
    out = ["# Campaign statistics report", ""]
    if synthetic:
        out += [f"> **{WATERMARK}.** This tree was generated with injected ratios to exercise the pipeline; no number below is a measurement.", ""]
    out += [f"Source: `{campaign['root']}`. Schema {stats['schema_version']}; keys documented in `STATS_SCHEMA.md`. "
            "Pre-registered plan: `docs/H100_EXPERIMENT_DESIGN.md` section 9.", "",
            "## 1. Rounds", "",
            f"{campaign['n_rounds']} round(s) enter every estimate (df = {campaign['df']}, two-sided t critical value "
            f"{sig3(campaign['t_critical']) if campaign['t_critical'] else 'n/a'}); trials per cell: {campaign['trials_per_cell']}. "
            "With so few rounds the intervals are wide by construction; they are reported as estimates, not as tests, and no p-value is computed.", "",
            t["t19_rounds"].markdown(synthetic, heading=False)]
    if campaign["rounds_excluded"]:
        out += ["**Excluded rounds:** " + "; ".join(f"{x['label']} ({x['reason']})" for x in campaign["rounds_excluded"]) + ".", ""]
    if campaign["warnings"]:
        out += ["Loader warnings:", ""] + [f"- {w}" for w in campaign["warnings"][:20]] + [""]
    balance = stats["order"]["design_balance"]
    out += [f"Observed engine order per round: " + "; ".join(f"round {r}: {' → '.join(o)}" for r, o in balance["observed"].items()) + ". "
            f"Each engine occupies each position once: {balance['positions_balanced']}; each ordered engine pair adjacent once: {balance['pairs_balanced']}.", ""]
    out += ["## 2. Reading the ratios", "",
            "A speedup is `t_ref / t_engine` per query and round, so above 1 the denominator engine is faster. The per-query estimate is the geometric "
            "mean over rounds with a 95% t-interval on the mean log-ratio (df = rounds − 1). The suite estimate is, per round, the geometric mean of the "
            "per-query ratios over queries available for both sides in every round, then the same interval across rounds. Round is the block.", ""]
    out += ["## 3. Primary contrasts per suite and scale", ""]
    for c in ("c1", "c2", "c3", "c4", "c4_strict"):
        spec = stats["contrasts"][c]
        out += [f"### {spec['label']} ({c.upper().replace('_STRICT', ' sensitivity')}, {spec['rq']})", "",
                filtered(t["t01_suite_contrasts"], lambda r, c=c: r["contrast"] == c, ("Contrast",)).markdown(synthetic, heading=False)]
    out += ["### Interval tally", "", verdict_tally(stats)]
    out += ["## 4. RQ3 crossover", "", t["t11_crossover"].markdown(synthetic, heading=False)]
    cpu = [f"{suite}/{short_scale(scale)}: {', '.join(v['queries_where_spark_cpu_faster'])}"
           for suite, by_scale in stats["cpu_reference"].items() for scale, v in by_scale.items() if v["queries_where_spark_cpu_faster"]]
    out += ["C4 takes Polars CPU as the CPU side (section 9). " + ("Spark CPU is faster than Polars CPU for: " + "; ".join(cpu) + "." if cpu
            else "Spark CPU is never faster than Polars CPU in this campaign, so Polars CPU is the best CPU engine at every cell."), ""]
    out += ["## 5. RQ2 fastest engine", "", "Count of queries on which each engine has the lowest geometric-mean time:", "", winner_tally(stats)]
    out += ["## 6. RQ4 GPU coverage", ""]
    gaps = [(suite, query, scale, engine, cell) for suite, bq in stats["coverage"]["matrix"].items() for query, bs in bq.items()
            for scale, be in bs.items() for engine, cell in be.items() if engine in GPU_ENGINES and cell["status"] in ("failed", "partial")]
    if gaps:
        out += ["| Suite | Query | Scale | Engine | Status | Reason class | Pre-declared |", "|---|---|---|---|---|---|---|"]
        out += [f"| {s} | {q} | {short_scale(sc)} | {ENGINE_NAMES.get(e, e)} | {c['status']} ({c['rounds_ok']}/{c['rounds_total']} rounds) | "
                f"{(c['reason_class'] or '').replace('_', ' ')} | {'yes' if c['predeclared_unsupported'] else 'no'} |" for s, q, sc, e, c in gaps] + [""]
    else:
        out += ["Every query ran on both GPU engines in every included round.", ""]
    if stats["coverage"]["discrepancies"]:
        out += ["Discrepancies with the section 8 pre-declaration:", ""] + [f"- {d}" for d in stats["coverage"]["discrepancies"]] + [""]
    out += ["### Unstable coverage (amendment A1)", ""]
    if stats["unstable_coverage"]["cells"]:
        out += [stats["unstable_coverage"]["note"], "", t["t20_unstable_coverage"].markdown(synthetic, heading=False), ""]
    else:
        out += ["No cell has a coverage outcome in some rounds and not in others.", ""]
    out += plan_variability_report(stats, t["t21_plan_variability"], synthetic)
    out += ["## 7. Variability and order effects", "", t["t07_variability"].markdown(synthetic, heading=False), "",
            t["t09_order_position"].markdown(synthetic, heading=False), "", t["t10_order_predecessor"].markdown(synthetic, heading=False), "",
            "## 8. Legacy sum-of-medians totals (kept separate from the geometric means)", "",
            t["t05_legacy_totals"].markdown(synthetic, heading=False), "", t["t06_legacy_ratios"].markdown(synthetic, heading=False), "",
            "## 9. Secondary metrics", "", t["t13_gpu_memory"].markdown(synthetic), t["t14_process_time"].markdown(synthetic),
            t["t15_cleanup_cost"].markdown(synthetic), t["t16_energy_proxy"].markdown(synthetic), *telemetry_report(stats, t["t24_gpu_telemetry"], synthetic),
            "## 10. RQ5 Polars version bridge", "", t["t17_polars_version_bridge"].markdown(synthetic, heading=False), ""]
    meta = stats["side_study"]["meta"]
    if not meta.get("available"):
        out += ["No side-study data found.", ""]
    out += exploratory_report(stats, t["t23_exploratory"], synthetic)
    study = stats["a100_vs_h100"]["study"]
    out += [f"## 11. A100 → H100 (GPU engines only; non-equivalent hosts)", ""]
    if study["label"]:
        span = study["first_session_utc"][:10] if study["first_session_utc"][:10] == study["last_session_utc"][:10] else f"{study['first_session_utc'][:10]} to {study['last_session_utc'][:10]}"
        out += [f"The A100 side is the **{study['label']}** (sessions on {span}), the saved evidence of a previous study, "
                f"not the A100 campaign. Engines in that study: {', '.join(ENGINE_NAMES.get(e, e) for e in study['engines_present'])}; compared here: "
                f"{', '.join(ENGINE_NAMES.get(e, e) for e in study['engines_compared'])} (no {', '.join(ENGINE_NAMES.get(e, e) for e in study['engines_only_in_h100_campaign'])} run in that study). "
                "CPU engines are never compared across the two.", "",
                f"Main differences: {study['difference_headline']}", ""]
    out += [t["t18_a100_vs_h100"].markdown(synthetic, heading=False), ""]
    earlier = study["plan_variability"]
    if study["label"] and earlier["cells"]:
        out += ["Cells of the earlier study whose trials ended in more than one final adaptive plan (descriptive; every trial enters that study's median; "
                "signatures differ from the campaign's because the earlier study's scan locations are sanitized, so plans are compared by structure):", ""]
        for c in earlier["cells"]:
            parts = [f"plan {p['signature'][:7]}: {p['n_measured']} measured and {p['n_warmup']} warm-up trial(s)"
                     + (f" ({sig3(p['measured_seconds']['min'])}-{sig3(p['measured_seconds']['max'])} s)" if p["measured_seconds"] else "")
                     + f", {sum(p['features']['exchanges'].values())} exchanges, shuffle readers " + (", ".join(f"{k} {v}" for k, v in p["features"]["shuffle_readers"].items()) or "none")
                     for p in c["plans"]]
            out.append(f"- {c['suite']} {short_scale(c['scale'])} {c['query']} ({ENGINE_NAMES.get(c['engine'], c['engine'])}): " + "; ".join(parts))
        out += [""]
    if study["label"]:
        out += ["### What differs from the H100 campaign (read from the result files)", "", t["t22_a100_study_differences"].markdown(synthetic, heading=False), ""]
    hosts = stats["a100_vs_h100"]["meta"].get("a100_hosts", [])
    if hosts:
        out += ["A100 host: " + "; ".join(sorted({h["label"] for h in hosts})) + ".", ""]
    out += ["## 12. Caveats", "",
            "- Four rounds give df = 3; intervals are wide and are not hypothesis tests. Multiplicity across contrasts, suites and scales is not adjusted.",
            "- Round is a block: all engines run once per round, so round-level drift cancels in each per-round ratio. Session-level effects remain inside the interval.",
            "- C4 uses, per query and round, the faster of the GPU engines that produced a timing; a query that only one GPU engine supports is included with that engine. "
            "`c4_strict` restricts to queries both GPU engines ran. C1, C3 and `c4_strict` exclude queries unsupported on Polars GPU; they stay visible in the coverage table.",
            "- Geometric means weight queries equally; the legacy sums of medians are dominated by the longest queries. They answer different questions and are never mixed.",
            "- Peak GPU memory is a sampled per-process-tree value; the energy proxy is GPU board power only (host CPU power excluded; CPU engines show idle GPU draw).",
            "- The A100 study is one session per cell and ran on different hardware, vCPU count and software: its ratios to H100 describe a host change, not an architecture effect."]
    if synthetic:
        out += ["", f"**{WATERMARK}**"]
    return "\n".join(out) + "\n"


def write_outputs(stats, output):
    output = Path(output)
    tables_dir = output / "tables"
    output.mkdir(parents=True, exist_ok=True)
    text = json.dumps(stats, indent=2, allow_nan=False)  # raises on NaN/inf: a non-finite statistic is a bug, not a value
    (output / "stats.json").write_text(text + "\n")
    stats = json.loads(text)
    (output / "stats_flat.json").write_text(json.dumps(flatten_stats(stats), indent=1, allow_nan=False) + "\n")
    tables = build_tables(stats)
    for table in tables:
        table.write(tables_dir, synthetic=stats["synthetic"])
    (output / "STATS_SCHEMA.md").write_text(schema_markdown())
    (output / "STATS_REPORT.md").write_text(report_text(stats, tables))
    marker = output / "SYNTHETIC_NOT_RESULTS.txt"
    if stats["synthetic"]:
        marker.write_text(WATERMARK + "\nEvery file in this directory derives from a generated tree with injected ratios.\n")
    else:
        marker.unlink(missing_ok=True)
    return tables


# ------------------------------------------------------------------ cross-host tables, report, outputs

def ratio_text(record):
    """'0.62 [0.58, 0.66]' for a cross-host ratio record (ratio only when there is no interval)."""
    if not record or record.get("estimate") is None:
        return DASH
    if record.get("ci_low") is None:
        return f"{sig3(record['estimate'])} (n={min(record['n_host'], record['n_ref'])})"
    return f"{sig3(record['estimate'])} [{sig3(record['ci_low'])}, {sig3(record['ci_high'])}]"


def build_cross_tables(cross):
    reference, hosts = cross["reference"], cross["hosts"]
    others = [h for h in hosts if h != reference]
    tables = []
    # x01: host identity
    fields = ("role", "instance_type", "gpu", "gpu_memory_gib", "cpu_model", "arch", "vcpus", "cpus_arg", "smt_threads_per_core", "physical_cores",
              "numa_nodes", "cpus_arg_cores_min", "cpus_arg_cores_max", "host_ram_gib", "driver_version", "cuda_version", "rapids_jar", "n_rounds", "synthetic")
    rows = [{"host": h, **{f: cross["host_info"][h].get(f) for f in fields}} for h in hosts]
    num = lambda v: DASH if v is None else (f"{v:g}" if isinstance(v, (int, float)) else str(v))
    tables.append(Table("x01_hosts", "Host identity (one complete campaign per host; reference first in comparisons)",
        [("host", "host"), *[(f, f) for f in fields]], rows,
        [("Host", lambda r: r["host"] + (" (ref.)" if r["role"] == "reference" else "")), ("GPU", lambda r: num(r["gpu"])),
         ("Device GiB", lambda r: num(r["gpu_memory_gib"])), ("CPU model", lambda r: num(r["cpu_model"])), ("Arch", lambda r: num(r["arch"])),
         ("vCPU", lambda r: num(r["vcpus"])), ("--cpus", lambda r: num(r["cpus_arg"])), ("Threads / core (SMT)", lambda r: num(r["smt_threads_per_core"])),
         ("Physical cores", lambda r: num(r["physical_cores"])), ("NUMA nodes", lambda r: num(r["numa_nodes"])),
         ("Cores occupied by --cpus threads", lambda r: DASH if r["cpus_arg_cores_min"] is None or r["cpus_arg_cores_max"] is None else (
             str(r["cpus_arg_cores_min"]) if r["cpus_arg_cores_min"] == r["cpus_arg_cores_max"] else f"{r['cpus_arg_cores_min']} to {r['cpus_arg_cores_max']}")),
         ("RAM GiB", lambda r: num(r["host_ram_gib"])),
         ("Driver", lambda r: num(r["driver_version"])), ("CUDA", lambda r: num(r["cuda_version"])), ("RAPIDS jar", lambda r: num(r["rapids_jar"])),
         ("Rounds", lambda r: num(r["n_rounds"]))],
        note="Identity is read from environment.json and campaign.json; a dash marks a field that was not recorded. Topology (threads per core, physical cores, "
             "NUMA nodes) is what lscpu reports inside the machine; the cores occupied are bounded below by packing the --cpus threads onto sibling hardware "
             "threads and above by spreading them over distinct cores."))
    # x02: suite-level ratios, one row per engine/suite/scale, one column group per comparison host
    keys = sorted({(e, s, sc) for h in others for e, by_suite in cross["ratio"].get(h, {}).items() for s, by_scale in by_suite.items() for sc in by_scale},
                  key=lambda k: (ENGINE_ORDER.index(k[0]), SUITES.index(k[1]), scale_sort_key(k[2])))
    rows = []
    for e, s, sc in keys:
        row = {"engine": e, "suite": s, "scale": sc, "_r": {}}
        for h in others:
            r = cross["ratio"].get(h, {}).get(e, {}).get(s, {}).get(sc)
            row["_r"][h] = r
            row[f"ratio_{h}"] = r and r["estimate"]
            row[f"ci_low_{h}"] = r and r["ci_low"]
            row[f"ci_high_{h}"] = r and r["ci_high"]
            row[f"df_{h}"] = r and r["df"]
            row[f"n_queries_{h}"] = r and r["n_queries"]
        rows.append(row)
    tables.append(Table("x02_suite_ratios", f"Suite-level time ratio host / {reference} (geometric mean of round medians; Welch-type 95% interval on the log scale)",
        [("engine", "engine"), ("suite", "suite"), ("scale", "scale"), *[(f"{k}_{h}", f"{k}_{h}") for h in others for k in ("ratio", "ci_low", "ci_high", "df", "n_queries")]], rows,
        [("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])), ("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])),
         *[(f"{h} / {reference} [95% CI]", lambda r, h=h: ratio_text(r["_r"].get(h))) for h in others]],
        note=f"Time ratio host / {reference}: below 1 the host is faster. The interval is Welch-type on the log scale (unpaired rounds); n=k marks a single round on one side (no interval).",
        longtable=True))
    # x03: per-query ratios (long)
    rows = []
    for h in others:
        for e, by_suite in cross["ratio_query"].get(h, {}).items():
            for s, by_scale in by_suite.items():
                for sc, by_query in by_scale.items():
                    for q, r in by_query.items():
                        rows.append({"host": h, "engine": e, "suite": s, "scale": sc, "query": q, "status": r["status"], "estimate": r["estimate"],
                                     "ci_low": r["ci_low"], "ci_high": r["ci_high"], "df": r["df"], "n_host": r["n_host"], "n_ref": r["n_ref"],
                                     "host_seconds": r["host_seconds"], "ref_seconds": r["ref_seconds"],
                                     "blocked_by": "; ".join(f"{b['host']}:{b['reason_class']}" for b in r["blocked_by"]), "_r": r})
    tables.append(Table("x03_query_ratios", f"Per-query time ratio host / {reference} (Welch-type 95% interval)",
        [(k, k) for k in ("host", "engine", "suite", "scale", "query", "status", "estimate", "ci_low", "ci_high", "df", "n_host", "n_ref", "host_seconds", "ref_seconds", "blocked_by")], rows,
        [("Host", lambda r: r["host"]), ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])), ("Suite", lambda r: r["suite"]),
         ("Scale", lambda r: short_scale(r["scale"])), ("Query", lambda r: r["query"]),
         ("Ratio [95% CI]", lambda r: ratio_text(r["_r"]) if r["status"] in ("ok", "partial") else "n/a: " + (r["blocked_by"] or r["status"]))], longtable=True))
    # x04: speedups of each host's own campaign
    rows = []
    for c in ("c1", "c2", "c3", "c4", "c4_strict"):
        for s in SUITES:
            for sc in sorted({sc for h in hosts for sc in cross["speedup"].get(c, {}).get(h, {}).get(s, {})}, key=scale_sort_key):
                row = {"contrast": c, "suite": s, "scale": sc, "_r": {}}
                for h in hosts:
                    r = cross["speedup"].get(c, {}).get(h, {}).get(s, {}).get(sc)
                    row["_r"][h] = r and {**r, "n_host": r["n_rounds"], "n_ref": r["n_rounds"]}
                    row[f"estimate_{h}"], row[f"ci_low_{h}"], row[f"ci_high_{h}"] = (r and r["estimate"]), (r and r["ci_low"]), (r and r["ci_high"])
                rows.append(row)
    tables.append(Table("x04_speedup_by_host", "Speedup of each host's own campaign (t_ref / t_engine; 95% t-interval over that host's rounds; not combined across hosts)",
        [("contrast", "contrast"), ("suite", "suite"), ("scale", "scale"), *[(f"{k}_{h}", f"{k}_{h}") for h in hosts for k in ("estimate", "ci_low", "ci_high")]], rows,
        [("Contrast", lambda r: r["contrast"].upper().replace("_STRICT", "s")), ("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])),
         *[(h, lambda r, h=h: ratio_text(r["_r"].get(h))) for h in hosts]],
        note="C1 Polars CPU / Polars GPU, C2 Spark CPU / Spark GPU, C3 Spark GPU / Polars GPU, C4 Polars CPU / best GPU engine; each host's intervals come from its own rounds.",
        longtable=True))
    # x05: coverage differences
    rows = [{"host": h, **d} for h in others for d in cross["coverage_differences"].get(h, [])]
    tables.append(Table("x05_coverage_differences", f"Cells whose coverage outcome differs from {reference}",
        [(k, k) for k in ("host", "suite", "scale", "query", "engine", "host_status", "host_reason_class", "ref_status", "ref_reason_class")], rows or [],
        [("Host", lambda r: r["host"]), ("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Query", lambda r: r["query"]),
         ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
         ("Host outcome", lambda r: r["host_status"] + (f" ({r['host_reason_class'].replace('_', ' ')})" if r["host_reason_class"] else "")),
         (f"{reference} outcome", lambda r: r["ref_status"] + (f" ({r['ref_reason_class'].replace('_', ' ')})" if r["ref_reason_class"] else ""))], longtable=True))
    # x06: output agreement (integrity evidence, no timings)
    rows = []
    for h in others:
        for suite, by_scale in cross["agreement"].get(h, {}).items():
            for scale, by_engine in by_scale.items():
                for engine, a in by_engine.items():
                    rows.append({"host": h, "suite": suite, "scale": scale, "engine": engine, **{k: a[k] for k in (
                        "host_max_rel_diff", "ref_max_rel_diff", "host_max_rel_diff_query", "ref_max_rel_diff_query", "larger_than_reference", "host_over_reference",
                        "host_within_gate", "host_failed")},
                                 "queries_larger": a["queries_larger"]})
    sci = lambda v: DASH if v is None else f"{v:.2e}"
    tables.append(Table("x06_output_agreement", f"Largest relative difference of saved outputs against Polars, host versus {reference} (gate G2: 1e-8; integrity evidence)",
        [(k, k) for k in ("host", "suite", "scale", "engine", "host_max_rel_diff", "ref_max_rel_diff", "host_max_rel_diff_query", "ref_max_rel_diff_query",
                          "larger_than_reference", "host_over_reference", "host_within_gate", "host_failed", "queries_larger")], rows,
        [("Host", lambda r: r["host"]), ("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
         ("Host max", lambda r: sci(r["host_max_rel_diff"])), (f"{reference} max", lambda r: sci(r["ref_max_rel_diff"])),
         ("Host worst query", lambda r: r["host_max_rel_diff_query"] or DASH), ("Larger than reference", lambda r: "yes" if r["larger_than_reference"] else "no"),
         ("Host / reference", lambda r: DASH if r["host_over_reference"] is None else f"{r['host_over_reference']:.3f}"),
         ("Within 1e-8", lambda r: "yes" if r["host_within_gate"] else "NO")], longtable=True,
        note="Recomputed from the saved result rows; a larger difference than the reference host's is reported even when inside the tolerance."))
    # x07: plan variability of the Spark cells, per host
    rows = [{"host": h, "suite": c["suite"], "scale": c["scale"], "query": c["query"], "engine": c["engine"], "plan": p["signature"],
             "n_measured": p["n_measured"], "n_warmup": p["n_warmup"], "measured_median_seconds": p["measured_median_seconds"],
             "warmup_median_seconds": p["warmup_median_seconds"], "exchanges": p["exchanges"],
             "shuffle_write_gib": None if p["shuffle_write_bytes_median"] is None else p["shuffle_write_bytes_median"] / 2 ** 30}
            for h in hosts for c in (cross.get("plan_variability") or {}).get(h, {}).get("cells", []) for p in c["plans"]]
    tables.append(Table("x07_plan_variability", "Spark cells that ended in more than one final adaptive plan, per host (equal plans have equal signatures on every host)",
        [(k, k) for k in ("host", "suite", "scale", "query", "engine", "plan", "n_measured", "n_warmup", "measured_median_seconds",
                          "warmup_median_seconds", "exchanges", "shuffle_write_gib")], rows,
        [("Host", lambda r: r["host"]), ("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Query", lambda r: r["query"]),
         ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])), ("Plan", lambda r: r["plan"]), ("Measured trials", lambda r: str(r["n_measured"])),
         ("Warm-ups", lambda r: str(r["n_warmup"])), ("Measured median (s)", lambda r: sig3(r["measured_median_seconds"]) if r["measured_median_seconds"] is not None else DASH),
         ("Warm-up median (s)", lambda r: sig3(r["warmup_median_seconds"]) if r["warmup_median_seconds"] is not None else DASH),
         ("Exchanges", lambda r: str(r["exchanges"])),
         ("Shuffle written (GiB, median)", lambda r: sig3(r["shuffle_write_gib"]) if r["shuffle_write_gib"] is not None else DASH)],
        note="Descriptive; a trial that ends in a different plan still enters its cell's median. Warm-up trials are not measured trials.", longtable=True))
    # x08: descriptive GPU telemetry per host (cross-host summary)
    telemetry = cross.get("gpu_telemetry") or {}
    keys = sorted({(suite, scale, engine) for h in hosts for suite, by_scale in telemetry.get(h, {}).get("cells", {}).items() for scale, by_engine in by_scale.items()
                   for engine in by_engine}, key=lambda k: (SUITES.index(k[0]), scale_sort_key(k[1]), ENGINE_ORDER.index(k[2])))
    rows = []
    for suite, scale, engine in keys:
        row = {"suite": suite, "scale": scale, "engine": engine}
        for h in hosts:
            cell = telemetry.get(h, {}).get("cells", {}).get(suite, {}).get(scale, {}).get(engine) or {}
            for k in ("n_actions", "util_median_pct", "idle_fraction", "power_median_w", "power_fraction_of_limit", "sm_clock_fraction_of_max"):
                row[f"{h}_{k}"] = cell.get(k)
        rows.append(row)

    def host_text(h):
        def f(r):
            if not r.get(f"{h}_n_actions"):
                return DASH
            part = lambda v, fmt: DASH if v is None else fmt.format(v)
            return (f"{part(None if r[f'{h}_idle_fraction'] is None else 100 * r[f'{h}_idle_fraction'], '{:.0f}%')} / {part(r[f'{h}_util_median_pct'], '{:.0f}%')} / "
                    f"{part(r[f'{h}_power_median_w'], '{:.0f} W')}" + ("" if r[f"{h}_power_fraction_of_limit"] is None else f" ({100 * r[f'{h}_power_fraction_of_limit']:.0f}%)"))
        return f

    tables.append(Table("x08_gpu_telemetry", f"GPU telemetry inside measured actions of at least {TELEMETRY_MIN_ACTION_SECONDS:g} s, per host ({TELEMETRY_LABEL})",
        [("suite", "suite"), ("scale", "scale"), ("engine", "engine"), *[(f"{h}_{k}", f"{h}_{k}") for h in hosts for k in ("n_actions", "util_median_pct", "idle_fraction", "power_median_w",
                                                                                                                 "power_fraction_of_limit", "sm_clock_fraction_of_max")]], rows,
        [("Suite", lambda r: r["suite"]), ("Scale", lambda r: short_scale(r["scale"])), ("Engine", lambda r: ENGINE_NAMES.get(r["engine"], r["engine"])),
         *[(f"{h}: GPU-idle / util median / power (of limit)", host_text(h)) for h in hosts]],
        note=TELEMETRY_LABEL + ". GPU-idle = share of nvidia-smi samples (about every 200 ms) with utilization.gpu below 10% inside measured actions of at least 1 s; a dash: no "
             "action of that cell was at least 1 s long (or no gpu.csv). Power in watts and as a share of the card's current power limit.", longtable=True))
    return tables


def cross_report_text(cross, tables):
    t = {table.name: table for table in tables}
    synthetic, reference = cross["synthetic"], cross["reference"]
    others = [h for h in cross["hosts"] if h != reference]
    out = ["# Cross-host comparison report", ""]
    if synthetic:
        out += [f"> **{WATERMARK}.** Generated trees with injected ratios; no number below is a measurement.", ""]
    if cross.get("partial"):
        out += [f"> **PARTIAL comparison.** The GPU-generation ladder has four hosts; {', '.join(cross['hosts_not_included'])} "
                f"{'is' if len(cross['hosts_not_included']) == 1 else 'are'} not included here (no analyzed campaign was supplied). "
                f"Nothing below says how {'it compares' if len(cross['hosts_not_included']) == 1 else 'they compare'}; the full comparison is a separate run.", ""]
    out += [f"Hosts (GPU-generation order): {', '.join(cross['hosts'])}. Reference: **{reference}**. Schema {cross['schema_version']}; keys in `STATS_SCHEMA.md`. "
            "Design: `docs/H100_EXPERIMENT_DESIGN.md` amendments A2 and A3.", "",
            "## 1. Host identity", "", t["x01_hosts"].markdown(synthetic, heading=False), "",
            "## 2. How the ratios are built", "", cross["definition"], ""]
    out += ["## 3. Suite-level time ratios", "", t["x02_suite_ratios"].markdown(synthetic, heading=False), ""]
    out += ["## 4. Speedup of each host's own campaign", "", t["x04_speedup_by_host"].markdown(synthetic, heading=False), ""]
    out += ["## 5. Coverage outcomes that differ from the reference", ""]
    if t["x05_coverage_differences"].rows:
        out += [t["x05_coverage_differences"].markdown(synthetic, heading=False), ""]
    else:
        out += ["None: every host has the same coverage outcome as the reference in every cell.", ""]
    skipped = [f"- {h}: {note}" for h in others for note in cross["skipped"].get(h, [])]
    if skipped:
        out += ["Blocks present on only one host:", ""] + skipped + [""]
    larger = [(h, s_, sc, e, a) for h in others for s_, by_scale in cross["agreement"].get(h, {}).items() for sc, by_engine in by_scale.items()
              for e, a in by_engine.items() if a["larger_than_reference"]]
    out += ["## 6. Output agreement with Polars (integrity evidence)", "",
            f"{len(larger)} (suite, scale, engine) cell(s) show a larger relative difference than on {reference}; all are listed in table x06, "
            f"cells outside the 1e-8 gate: {sum(1 for h in others for by_scale in cross['agreement'].get(h, {}).values() for by_engine in by_scale.values() for a in by_engine.values() if not a['host_within_gate'])}.", ""]
    out += ["## 7. Plan variability of the Spark cells (descriptive)", ""]
    pvs = cross.get("plan_variability") or {}
    if t["x07_plan_variability"].rows:
        out += ["Cells whose trials ended in more than one final adaptive plan on a host (a plan's signature is the same on every host). The trials of a variable cell "
                "still enter the cell's median as designed.", "", t["x07_plan_variability"].markdown(synthetic, heading=False), ""]
    else:
        out += ["No Spark cell ended in more than one final adaptive plan on any host.", ""]
    unavailable = [h for h in cross["hosts"] if pvs.get(h, {}).get("cells_checked") is None]
    if unavailable:
        out += ["Plan variability was not computed for: " + ", ".join(unavailable) + " (stats.json written before the section existed).", ""]
    out += ["## 8. GPU telemetry inside measured actions (descriptive)", "",
            f"{TELEMETRY_LABEL}. Per host, engine, suite and scale: the share of nvidia-smi samples with utilization.gpu below 10% (GPU-idle), the median utilization.gpu and the "
            "median power draw with its share of the card's power limit, from the samples inside measured actions of at least 1 s (shorter actions are left out; the sampling interval is "
            "about 200 ms).", "", t["x08_gpu_telemetry"].markdown(synthetic, heading=False), ""]
    out += ["## 9. Caveats", ""] + [f"- {c}" for c in cross["caveats"]] + [""]
    for h in others:
        notes = cross["caveats_by_host"].get(h) or ["No identity difference was detected between the recorded fields."]
        out += [f"**{h} versus {reference}**", ""] + [f"- {n}" for n in notes] + [""]
    if synthetic:
        out += [f"**{WATERMARK}**"]
    return "\n".join(out) + "\n"


def telemetry_report(stats, table, synthetic):
    """STATS_REPORT.md block for secondary.gpu_telemetry (a markdown string, placed inside section 9)."""
    meta = stats["secondary"]["gpu_telemetry"]["meta"]
    head = f"### GPU telemetry inside measured actions ({meta['label']})\n\n"
    if not meta["available"]:
        return [head + f"Not computed: {meta['reason']}.\n"]
    return [head + f"{meta['definition']} {meta['resolution_note'][0].upper() + meta['resolution_note'][1:]}. Power limit {meta['power_limit_w']:g} W "
            f"({meta['power_limit_source']}); maximum SM clock {meta['max_sm_clock_mhz']:g} MHz.\n\n" if meta["power_limit_w"] and meta["max_sm_clock_mhz"] else
            head + f"{meta['definition']} {meta['resolution_note'][0].upper() + meta['resolution_note'][1:]}.\n\n", table.markdown(synthetic, heading=False) + "\n"]


def exploratory_report(stats, table, synthetic):
    """STATS_REPORT.md lines for stats["exploratory"]: clearly set apart from the pre-registered results."""
    e1 = stats["exploratory"]["e1"]["meta"]
    out = [f"### EXPLORATORY ({EXPLORATORY_LABEL.split('(', 1)[1].rstrip(')')}): Polars {e1['cpu_version'] or '2.0.0'} CPU against the GPU engines", ""]
    if not e1["available"]:
        return out + [f"Not computed: {e1['reason']}.", ""]
    out += [f"**This is not part of the pre-registered analysis (design section 9) and is never used by C1-C4 or the crossover verdict.** "
            f"E1 = time of Polars {e1['cpu_version']} CPU (side-study rounds {e1['side_study_rounds']}) / time of Polars GPU (main rounds {e1['main_rounds']}); E4 = the same CPU time / "
            "the better GPU engine per query and round. Orientation as for C1 and C4: above 1 the GPU side was faster. The GPU comparisons of the locked design use Polars "
            f"1.44.2 on the CPU side (C1, C4), which is why the version bridge above matters. {EXPLORATORY_INTERVAL[0].upper() + EXPLORATORY_INTERVAL[1:]}.", "",
            table.markdown(synthetic, heading=False), ""]
    return out


def a100_study_note(stats):
    study = stats["a100_vs_h100"]["study"]
    return f" A100 = the {study['label']} (saved evidence of a previous study, not the A100 campaign). {study['difference_headline']}" if study.get("label") else ""


def plan_variability_report(stats, table, synthetic):
    """STATS_REPORT.md lines for stats["plan_variability"]."""
    pv = stats["plan_variability"]
    out = ["### Plan variability (descriptive)", "",
           f"{pv['cells_checked']} Spark cells ({pv['trials_with_plan']} trials with a saved plan, {pv['sessions_checked']} sessions) were checked; "
           f"{pv['cells_variable']} ha{'s' if pv['cells_variable'] == 1 else 've'} more than one final adaptive plan. "
           + (f"{pv['plan_files_missing']} plan file(s) were named in the results but not found. " if pv["plan_files_missing"] else "") + PLAN_NOTE, ""]
    for c in pv["cells"]:
        name = f"{c['suite']} {short_scale(c['scale'])} {c['query']} ({ENGINE_NAMES.get(c['engine'], c['engine'])})"
        initial = {True: "identical in every trial", False: "not identical", None: "not recorded"}[c["initial_plan_identical"]]
        out += [f"**{name}**: {c['n_plans']} final plans in {c['n_trials']} trials ({c['n_measured']} measured, {c['n_warmup']} warm-up); initial plan {initial}."]
        if c["measured_time_ranges_overlap"] is not None:
            out[-1] += (f" The measured times of the plans {'overlap' if c['measured_time_ranges_overlap'] else 'do not overlap'}"
                        + (f" (median of the slowest over the fastest plan: {sig3(c['median_seconds_slowest_over_fastest'])})." if c["median_seconds_slowest_over_fastest"] else "."))
        for plan in c["plans"]:
            changes = plan["changes_vs_reference"]
            if not changes:
                continue
            parts = []
            for feature, label in (("joins", "joins"), ("exchanges", "exchanges"), ("shuffle_readers", "shuffle readers")):
                parts += [f"{label} `{k}`: {a} to {b}" for k, (a, b) in changes.get(feature, {}).items()]
            extra = [f"`{line[:110]}`" for line in changes.get("lines_only_in_this_plan", [])[:4]]
            out += ["", f"- Plan {plan['signature']} against plan {c['reference_signature']}: " + ("; ".join(parts) if parts else "same joins, exchanges and shuffle readers")
                    + (f". Operator lines only in this plan: {', '.join(extra)}" if extra else "") + "."]
            facts = []
            for key, label in (("stages", "stages"), ("tasks", "tasks"), ("shuffle_write_bytes", "shuffle bytes written")):
                before = ((c["plans"][0].get("execution") or {}).get(key) or {}).get("median")
                after = ((plan.get("execution") or {}).get(key) or {}).get("median")
                if before is not None and after is not None:
                    scale_bytes = (lambda v: f"{sig3(v / 2 ** 30)} GiB") if key == "shuffle_write_bytes" else sig3
                    facts.append(f"{label} {scale_bytes(before)} to {scale_bytes(after)}")
            if facts:
                out[-1] += " Per action (Spark event log, median): " + "; ".join(facts) + "."
        out += [""]
    if pv["cells"]:
        out += [table.markdown(synthetic, heading=False), ""]
    return out


def write_cross_outputs(cross, output):
    """cross_host.json (+ flat), xhost_<HOST>.json per comparison host, tables/x*.csv|md|tex, CROSS_HOST_REPORT.md, STATS_SCHEMA.md."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    text = json.dumps(cross, indent=2, allow_nan=False)
    (output / "cross_host.json").write_text(text + "\n")
    cross = json.loads(text)
    (output / "cross_host_flat.json").write_text(json.dumps(flatten_stats(cross, (), CROSS_OPAQUE_PATTERNS), indent=1, allow_nan=False) + "\n")
    for host in cross["hosts"]:
        if host != cross["reference"]:
            (output / f"xhost_{host}.json").write_text(json.dumps(xhost_compat(cross, host), indent=1, allow_nan=False) + "\n")
    tables = build_cross_tables(cross)
    for table in tables:
        table.write(output / "tables", synthetic=cross["synthetic"])
    (output / "CROSS_HOST_REPORT.md").write_text(cross_report_text(cross, tables))
    (output / "STATS_SCHEMA.md").write_text(schema_markdown())
    marker = output / "SYNTHETIC_NOT_RESULTS.txt"
    if cross["synthetic"]:
        marker.write_text(WATERMARK + "\nEvery file in this directory derives from generated trees with injected ratios.\n")
    else:
        marker.unlink(missing_ok=True)
    return tables


def run_cross_host(host_specs, output, *, reference=None, allow_ungated=False, energy=True, gpu_index=0, a100_roots=None):
    """Analyze (or load) every host, write each host's own outputs under output/hosts/<LABEL>/, then the comparison.

    host_specs: [(label, campaign root or stats.json path)]. Returns (cross dict, per-host stats dict, error messages).
    A host without any accepted round is reported and left out; the reference must survive.
    """
    output = Path(output)
    stats, errors = {}, []
    for label, path in host_specs:
        extras = {}
        try:
            result, root = load_host_stats(label, path, allow_ungated=allow_ungated, energy=energy, gpu_index=gpu_index,
                                           a100_roots=(a100_roots or {}).get(label, ()), extras=extras)
        except (OSError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
            continue
        if not result["campaign"]["rounds_included"]:
            errors.append(f"{label}: no round passed its gates; nothing to compare")
            if root is not None:
                write_integrity(extras["integrity"], output / "hosts" / label, result["synthetic"])   # why the rounds were refused
            continue
        stats[label] = result
        if root is not None:
            write_outputs(result, output / "hosts" / label)
            write_integrity(extras["integrity"], output / "hosts" / label, result["synthetic"])
    reference = reference or (DEFAULT_REFERENCE if DEFAULT_REFERENCE in dict(host_specs) else (host_specs[0][0] if host_specs else None))
    if reference not in stats:
        errors.append(f"reference host {reference!r} has no usable data")
        return None, stats, errors
    cross = compare_hosts(stats, reference)
    write_cross_outputs(cross, output)
    return cross, stats, errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, help="campaign root (campaign.json, round-<k>/, side-study/) of ONE host")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", help="host label recorded in stats.json (default: GPU short name, e.g. H100)")
    parser.add_argument("--host", action="append", metavar="LABEL=PATH", default=[],
                        help="cross-host mode (repeatable): a campaign root or a stats.json per host, e.g. --host H100=<root> --host B200=<root>")
    parser.add_argument("--reference", help="reference host of the cross-host ratios (default H100 if given, else the first --host)")
    parser.add_argument("--a100", nargs="*", type=Path, default=[], help="A100 run roots (production, large-sf100, large-sf300, complex-formal-v2)")
    parser.add_argument("--inputs-report", nargs="*", type=Path, default=[],
                        help="verify_generated_inputs.py report(s) for meta.input_gate (default: the paths recorded in campaign.json, then <root>/inputs-verification.json)")
    parser.add_argument("--design-doc", type=Path, help="design document whose '### A<n>' headings are counted for meta.amendments (default: docs/H100_EXPERIMENT_DESIGN.md)")
    parser.add_argument("--synthetic", action="store_true",
                        help="generate fake campaigns with injected ratios (H100 under --root, default OUTPUT/synthetic-campaign, plus the other hosts) and analyze them")
    parser.add_argument("--synthetic-hosts", default=",".join(SYNTH_HOSTS), help="hosts the synthetic generator emits (H100 first; H100 alone skips the cross-host output)")
    parser.add_argument("--seed", type=int, default=1, help="seed of the synthetic generator")
    parser.add_argument("--allow-ungated", action="store_true", help="accept rounds without gates.json (development only; listed in the report)")
    parser.add_argument("--no-energy", action="store_true", help="skip gpu.csv parsing (energy proxy and GPU telemetry)")
    parser.add_argument("--paper2", action="store_true", help="with --host LABEL=stats.json ...: write paper2.json and PAPER2_SCHEMA.md (exploratory baseline-confound analyses) to --output")
    parser.add_argument("--no-side-study", action="store_true", help="ignore <root>/side-study (no version bridge, no exploratory E1/E4), e.g. while it is incomplete")
    parser.add_argument("--gpu-index", type=int, default=0)
    args = parser.parse_args(argv)
    if args.host:
        if args.synthetic or args.root:
            parser.error("--host (cross-host mode) cannot be combined with --root or --synthetic")
        try:
            specs = parse_host_args(args.host)
        except ValueError as exc:
            parser.error(str(exc))
        if args.paper2:
            loaded = {}
            for label, path in specs:
                loaded[label], _ = load_host_stats(label, path, allow_ungated=args.allow_ungated, energy=not args.no_energy, gpu_index=args.gpu_index)
            reference = args.reference or (DEFAULT_REFERENCE if DEFAULT_REFERENCE in loaded else specs[0][0])
            target = write_paper2(build_paper2(loaded, reference), args.output)
            print(f"paper 2 file for hosts {sorted(loaded)} against {reference}: {target}")
            return 0
        cross, stats, errors = run_cross_host(specs, args.output, reference=args.reference, allow_ungated=args.allow_ungated,
                                              energy=not args.no_energy, gpu_index=args.gpu_index)
        for message in errors:
            print(f"analyze_campaign.py: {message}", file=sys.stderr)
        if cross is None:
            return 1
        print(f"compared hosts {cross['hosts']} against {cross['reference']}; outputs in {args.output}")
        return 1 if errors else 0
    root = args.root
    a100_roots = list(args.a100)
    synthetic_hosts = []
    if args.synthetic:
        synthetic_hosts = [h.strip() for h in args.synthetic_hosts.split(",") if h.strip()]
        unknown = [h for h in synthetic_hosts if h not in SYNTH_HOSTS]
        if unknown or DEFAULT_REFERENCE not in synthetic_hosts:
            parser.error(f"--synthetic-hosts must include {DEFAULT_REFERENCE} and only name {sorted(SYNTH_HOSTS)}; got {synthetic_hosts}")
        root = root or args.output / "synthetic-campaign"
        make_synthetic_campaign(root, seed=args.seed, extra_failures=SYNTH_SHOWCASE_FAILURES, plan_variants=SYNTH_SHOWCASE_PLANS)
        a100_roots = a100_roots or sorted((root / "a100").iterdir())
    elif root is None:
        parser.error("--root is required unless --synthetic or --host is given")
    if not Path(root).is_dir():
        parser.error(f"--root {root} is not a directory")
    extras = {}
    stats = analyze(root, a100_roots, allow_ungated=args.allow_ungated, synthetic=args.synthetic, energy=not args.no_energy, gpu_index=args.gpu_index,
                    label=args.label, extras=extras, inputs_reports=args.inputs_report, design_doc=args.design_doc,
                    side_study=not args.no_side_study)
    write_integrity(extras["integrity"], args.output, stats["synthetic"])
    if not stats["campaign"]["rounds_included"]:
        print("analyze_campaign.py: no round passed its gates; nothing to estimate", file=sys.stderr)
        write_outputs(stats, args.output)
        return 1
    write_outputs(stats, args.output)
    print(f"analyzed rounds {stats['campaign']['rounds_included']} (excluded: {[x['label'] for x in stats['campaign']['rounds_excluded']]}); "
          f"outputs in {args.output}")
    if len(synthetic_hosts) > 1:
        specs = [(DEFAULT_REFERENCE, root)]
        for host in synthetic_hosts:
            if host != DEFAULT_REFERENCE:
                make_synthetic_campaign(args.output / "synthetic-hosts" / host, seed=args.seed, host=host, a100=False, side_study=False)
                specs.append((host, args.output / "synthetic-hosts" / host))
        cross, _, errors = run_cross_host(specs, args.output / "cross_host", reference=DEFAULT_REFERENCE, energy=not args.no_energy,
                                          gpu_index=args.gpu_index, a100_roots={DEFAULT_REFERENCE: a100_roots})
        for message in errors:
            print(f"analyze_campaign.py: {message}", file=sys.stderr)
        if cross is None:
            return 1
        print(f"compared synthetic hosts {cross['hosts']} against {cross['reference']}; outputs in {args.output / 'cross_host'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
