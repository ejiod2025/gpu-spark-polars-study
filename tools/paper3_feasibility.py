#!/usr/bin/env python3
"""Blinded structural feasibility audit of the archived campaign telemetry (Paper 3 planning).

    tools/paper3_feasibility.py [--hosts-root rerun/hosts] [--manifests results/campaign-20261030/raw-manifests]
                                [--hosts h100 gh200 a100 b200] [--verify-hashes all|spot|none] [--json OUT.json]

Answers STRUCTURAL questions only, so the Paper 3 (energy and cost per query) analysis plan can be locked before any
outcome is looked at: can gpu.csv be mapped to UTC, do the telemetry streams cover every engine session, can per-query
windows be rebuilt and how tightly, is the archive complete, and is every file read unmodified.

Blinding. The only telemetry columns read are: gpu.csv timestamp, index and (for a null/unparseable COUNT only)
power.draw; host.csv timestamp_utc, scale, engine, phase. No watts, joules, utilisation, temperature, clock, memory or
cost value is ever computed or emitted. results.json `seconds` is read only to take differences and ratios against
timestamps (window overhead, unexplained time); no per-engine duration, sum or rank is emitted, and unexplained time is
reported pooled or per scale, never per engine. `assert_blinded` rejects a report that carries a forbidden key.

Layout read (per host H under <hosts-root>/H/final/):
    campaign-H/campaign.json, campaign-H/round-<r>/{baseline,complex}/{gpu.csv,host.csv,schedule.json,environment.json,
    sf<scale>/<engine>/results.json}, campaign-H/side-study/round-<r>/<arm>/<suite>/... and FINAL-H.SHA256SUMS.

Clock domain. nvidia-smi writes LOCAL wall time with no zone. Because the runner starts gpu.csv before its first session and
stops it after its last, every suite's GPU stream must contain every session window of that suite (schedule.json
start_utc, process_wall_seconds, both UTC). That brackets the local-minus-UTC offset into [gpu_first - first_start,
gpu_last - last_end]; the intersection over all suites of a host is the evidence, and the mapping is unambiguous when
exactly one multiple of 15 minutes lies inside it. Two independent checks are reported: the `nvidia-smi -q` Timestamp in
environment.json against its started_at_utc, and gpu.csv against host.csv (timestamp_utc) at both ends.

Only the standard library and polars are used.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys

import polars as pl

VERSION = 1
DEFAULT_HOSTS = ("h100", "gh200", "a100", "b200")
REPO = Path(__file__).resolve().parent.parent
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Explicit schemas: every CSV column is read as text and parsed by an expression whose failures are counted.
GPU_HEADER = ["timestamp", "index", "utilization.gpu [%]", "utilization.memory [%]", "memory.used [MiB]", "power.draw [W]",
              "temperature.gpu", "clocks.current.sm [MHz]", "clocks.current.memory [MHz]"]
GPU_COLUMNS = ["timestamp", "index", "c2", "c3", "c4", "draw", "c6", "c7", "c8"]  # positional; c2..c8 are never inspected
GPU_SCHEMA = {name: pl.String for name in GPU_COLUMNS}
GPU_TIMESTAMP_FORMAT = "%Y/%m/%d %H:%M:%S%.f"
HOST_HEADER = ["timestamp_utc", "scale", "engine", "phase", "cpu_percent", "available_bytes", "cached_bytes",
               "swap_used_bytes", "disk_free_bytes"]
HOST_SCHEMA = {name: pl.String for name in ["timestamp_utc", "scale", "engine", "phase", "h4", "h5", "h6", "h7", "h8"]}
HOST_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S%.f%:z"  # %.f also accepts isoformat()'s microsecond-less form
DRAW_PATTERN = r"^\d+(\.\d+)? W$"
DRAW_NOT_AVAILABLE_PATTERN = r"^\[?(N/A|Not Supported|Unknown Error)\]?$"

GPU_GAP_SECONDS = 1.0       # nominal interval is 0.2 s (nvidia-smi -lms 200)
HOST_GAP_SECONDS = 2.0      # nominal interval is 1 s (host_stop.wait(1))
PHASE_TOLERANCE_SECONDS = 1.5
END_SLACK_SECONDS = 1.0     # nvidia-smi is SIGTERMed right after the last session: its last sample can precede that by one gap
OFFSET_QUANTUM_SECONDS = 900  # real time zones differ from UTC by multiples of 15 minutes
SAMPLE_COUNT_THRESHOLDS = (5, 25, 100)
RESOLUTIONS_SECONDS = (1.0,)  # temporal resolution assumed for the power channel, in addition to 2x the sampling interval
UNCERTAINTY_FRACTIONS = (0.05, 0.10, 0.25)
TIME_FIELDS = ("nvsmi_q_minus_env_start_s", "gpu_first_minus_env_start_s", "gpu_first_minus_host_first_s", "gpu_last_minus_host_last_s",
               "gpu_first_minus_first_session_start_s", "gpu_last_minus_last_session_end_s")
SCALE_ORDER = {"1": 1, "10": 10, "30": 30, "100": 100, "300": 300}

# Any key that names an outcome is refused in the report (the draw_* counters are null/unparseable counts, not values).
FORBIDDEN_KEY = re.compile(r"(power|energy|watt|joule|util|temperat|clock|memory|cost|speedup|rank|mean_seconds|query_seconds)", re.I)
ALLOWED_KEYS = set()  # the draw_* counters carry no forbidden word: they count null/unparseable rows, not values


class BlindingError(AssertionError):
    pass


def assert_blinded(node, path="report"):
    """Raise if any dict key in the report names an outcome quantity."""
    if isinstance(node, dict):
        for key, value in node.items():
            if FORBIDDEN_KEY.search(str(key)) and key not in ALLOWED_KEYS:
                raise BlindingError(f"forbidden key {key!r} at {path}")
            assert_blinded(value, f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            assert_blinded(value, f"{path}[{index}]")


# ---------------------------------------------------------------------------------------------------------------- basics
def to_us(value: datetime) -> int:
    return (value - EPOCH) // timedelta(microseconds=1)


def parse_utc(text):
    """datetime.isoformat() output (with or without microseconds) as epoch microseconds; None when absent or malformed."""
    if not isinstance(text, str):
        return None
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return to_us(value)


def quantile(sorted_values, q):
    """Linear-interpolation quantile (type 7) of an already sorted sequence; None when empty."""
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def distribution(values, digits=3):
    """n/min/p50/p90/p99/max of a list of numbers (None entries dropped); rounded for a stable, readable report."""
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return {"n": 0}
    out = {"n": len(clean), "min": clean[0]}
    for label, q in (("p50", 0.5), ("p90", 0.9), ("p99", 0.99)):
        out[label] = quantile(clean, q)
    out["max"] = clean[-1]
    return {k: (round(v, digits) if isinstance(v, float) else v) for k, v in out.items()}


def load_json(path: Path):
    """(parsed, error): never raises, so one corrupt file is reported instead of aborting the audit."""
    try:
        return json.loads(path.read_text()), None
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:120]}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(path: Path):
    """{relative path: sha256} from a `sha256sum` file; {} when the manifest is missing."""
    entries = {}
    if not path.is_file():
        return entries
    for line in path.read_text().splitlines():
        if len(line) > 66 and line[64:66] == "  ":
            entries[line[66:]] = line[:64]
    return entries


# ------------------------------------------------------------------------------------------------------ telemetry streams
class Stream:
    """One telemetry CSV reduced to what the audit needs: UTC microsecond stamps and structural counters (no values)."""

    def __init__(self, kind, path):
        self.kind = kind
        self.path = path
        self.present = path.is_file()
        self.header_ok = False
        self.ends_with_newline = False
        self.rows = 0
        self.bad_timestamp_rows = 0
        self.incomplete_rows = 0
        self.draw_ok_rows = 0
        self.draw_null_rows = 0
        self.draw_na_rows = 0
        self.draw_unparseable_rows = 0
        self.indices = ()
        self.t_us = []          # file order, bad stamps dropped
        self.sorted_t_us = []
        self.backward_steps = 0
        self.duplicate_stamps = 0
        self.labels = None      # host only: DataFrame(t_us, scale, engine, phase)
        self.stamps_without_fraction = 0  # host only: isoformat() drops ".ffffff" when the microsecond is exactly 0
        self.error = None

    @property
    def span(self):
        return (self.sorted_t_us[0], self.sorted_t_us[-1]) if self.sorted_t_us else None

    def shift(self, offset_us):
        """Subtract a local-minus-UTC offset from every stamp (gpu.csv stamps are local wall time)."""
        if offset_us:
            self.t_us = [t - offset_us for t in self.t_us]
            self.sorted_t_us = [t - offset_us for t in self.sorted_t_us]


def first_line(path: Path):
    with path.open("rb") as handle:
        return handle.readline().decode("utf-8", "replace").rstrip("\r\n")


def ends_with_newline(path: Path) -> bool:
    size = path.stat().st_size
    if size == 0:
        return False
    with path.open("rb") as handle:
        handle.seek(size - 1)
        return handle.read(1) == b"\n"


def _finish_stamps(stream: Stream, stamps):
    stream.t_us = stamps
    stream.sorted_t_us = sorted(stamps)
    steps = [b - a for a, b in zip(stamps, stamps[1:])]
    stream.backward_steps = sum(1 for s in steps if s < 0)
    stream.duplicate_stamps = sum(1 for s in steps if s == 0)


def read_gpu_csv(path: Path) -> Stream:
    """gpu.csv -> Stream with the stamps as written (local wall time). power.draw is only counted, never kept."""
    stream = Stream("gpu", path)
    if not stream.present:
        return stream
    try:
        header = [c.strip() for c in first_line(path).split(",")]
        stream.header_ok = header == GPU_HEADER
        stream.ends_with_newline = ends_with_newline(path)
        frame = (pl.scan_csv(path, has_header=False, skip_rows=1, schema=GPU_SCHEMA, separator=",", quote_char=None)
                 .select(
                     pl.col("timestamp").str.strip_chars().str.strptime(pl.Datetime("us"), GPU_TIMESTAMP_FORMAT, strict=False)
                     .dt.epoch("us").alias("t_us"),
                     pl.col("index").str.strip_chars().alias("index"),
                     pl.any_horizontal(*[pl.col(c).is_null() | (pl.col(c).str.strip_chars() == "") for c in GPU_COLUMNS])
                     .alias("incomplete"),
                     (pl.col("draw").is_null() | (pl.col("draw").str.strip_chars() == "")).alias("draw_null"),
                     pl.col("draw").str.strip_chars().str.contains(DRAW_NOT_AVAILABLE_PATTERN).fill_null(False).alias("draw_na"),
                     pl.col("draw").str.strip_chars().str.contains(DRAW_PATTERN).fill_null(False).alias("draw_ok"))
                 .collect())
    except Exception as exc:  # noqa: BLE001 - reported, not fatal
        stream.error = f"{type(exc).__name__}: {str(exc)[:160]}"
        return stream
    stream.rows = frame.height
    stream.bad_timestamp_rows = frame["t_us"].null_count()
    stream.incomplete_rows = int(frame["incomplete"].sum())
    stream.draw_ok_rows = int(frame["draw_ok"].sum())
    stream.draw_null_rows = int(frame["draw_null"].sum())
    stream.draw_na_rows = int(frame["draw_na"].sum())
    stream.draw_unparseable_rows = frame.height - stream.draw_ok_rows - stream.draw_null_rows - stream.draw_na_rows
    stream.indices = tuple(sorted(frame["index"].drop_nulls().unique().to_list()))
    _finish_stamps(stream, frame["t_us"].drop_nulls().to_list())
    return stream


def read_host_csv(path: Path) -> Stream:
    stream = Stream("host", path)
    if not stream.present:
        return stream
    try:
        stream.header_ok = [c.strip() for c in first_line(path).split(",")] == HOST_HEADER
        stream.ends_with_newline = ends_with_newline(path)
        frame = (pl.scan_csv(path, has_header=False, skip_rows=1, schema=HOST_SCHEMA, separator=",", quote_char=None)
                 .select(
                     pl.col("timestamp_utc").str.to_datetime(format=HOST_TIMESTAMP_FORMAT, time_unit="us", strict=False)
                     .dt.epoch("us").alias("t_us"),
                     pl.col("scale").fill_null(""), pl.col("engine").fill_null(""), pl.col("phase").fill_null(""),
                     pl.col("timestamp_utc").str.contains(r"T\d\d:\d\d:\d\d[+-]").fill_null(False).alias("no_fraction"),
                     pl.any_horizontal(*[pl.col(c).is_null() for c in HOST_SCHEMA if c not in ("scale", "engine")]).alias("incomplete"))
                 .collect())
    except Exception as exc:  # noqa: BLE001
        stream.error = f"{type(exc).__name__}: {str(exc)[:160]}"
        return stream
    stream.rows = frame.height
    stream.bad_timestamp_rows = frame["t_us"].null_count()
    stream.incomplete_rows = int(frame["incomplete"].sum())
    stream.stamps_without_fraction = int(frame["no_fraction"].sum())
    good = frame.filter(pl.col("t_us").is_not_null())
    stream.labels = good.select("t_us", "scale", "engine", "phase")
    _finish_stamps(stream, good["t_us"].to_list())
    return stream


def interval_stats(sorted_t_us, gap_seconds):
    """Spacing of consecutive samples: median / p99 / max (s) and the number of gaps above `gap_seconds`."""
    gaps = [(b - a) / 1e6 for a, b in zip(sorted_t_us, sorted_t_us[1:])]
    if not gaps:
        return {"n_intervals": 0}
    ordered = sorted(gaps)
    return {"n_intervals": len(gaps), "median_s": round(quantile(ordered, 0.5), 4), "p99_s": round(quantile(ordered, 0.99), 4),
            "max_s": round(ordered[-1], 4), "gaps_over_threshold": sum(1 for g in gaps if g > gap_seconds)}


def window_stats(sorted_t_us, lo_us, hi_us, gap_seconds):
    """Samples inside [lo, hi]: count, spacing distribution and the fraction of the window not inside a gap above the threshold.

    Gaps include the leading edge (window start to first sample) and the trailing edge (last sample to window end).
    """
    if lo_us is None or hi_us is None or hi_us <= lo_us:
        return None
    first = bisect_left(sorted_t_us, lo_us)
    last = bisect_right(sorted_t_us, hi_us)
    inside = sorted_t_us[first:last]
    window = (hi_us - lo_us) / 1e6
    if not inside:
        # A window shorter than the sampling period can legitimately hold no sample: no gap above the threshold, coverage undefined.
        long = window > gap_seconds
        return {"n": 0, "window_s": window, "median_s": None, "p99_s": None, "covered_fraction": 0.0 if long else None,
                "max_gap_s": window, "gaps_over_threshold": int(long)}
    edges = [(inside[0] - lo_us) / 1e6, (hi_us - inside[-1]) / 1e6]
    spacing = [(b - a) / 1e6 for a, b in zip(inside, inside[1:])]
    holes = [g for g in spacing + edges if g > gap_seconds]
    ordered = sorted(spacing)
    return {"n": len(inside), "window_s": window,
            "median_s": quantile(ordered, 0.5) if ordered else None, "p99_s": quantile(ordered, 0.99) if ordered else None,
            "max_gap_s": max(spacing + edges), "gaps_over_threshold": len(holes),
            "covered_fraction": max(0.0, 1.0 - sum(holes) / window)}


def count_in_window(sorted_t_us, lo_us, hi_us):
    return bisect_right(sorted_t_us, hi_us) - bisect_left(sorted_t_us, lo_us)


def resolve_offset(lo_s, hi_s):
    """Multiples of 15 minutes inside the bracket [lo, hi] (seconds): (candidates, unambiguous?)."""
    if lo_s is None or hi_s is None or lo_s > hi_s:
        return [], False
    low = math.ceil(lo_s / OFFSET_QUANTUM_SECONDS)
    high = math.floor(hi_s / OFFSET_QUANTUM_SECONDS)
    candidates = [k * OFFSET_QUANTUM_SECONDS for k in range(low, high + 1)]
    return candidates, len(candidates) == 1


# --------------------------------------------------------------------------------------------------------- expected layout
class SuiteDir:
    """One run_experiment.py output directory: a gpu.csv/host.csv pair that spans several engine sessions."""

    def __init__(self, host, kind, round_no, arm, suite, path):
        self.host, self.kind, self.round, self.arm, self.suite, self.path = host, kind, round_no, arm, suite, path

    @property
    def label(self):
        return "/".join(str(x) for x in (self.kind, f"r{self.round}", self.arm, self.suite) if x)


def expected_layout(campaign):
    """(suite dirs, expected sessions) implied by campaign.json's design: main rounds then the side-study arms."""
    design = campaign.get("design", {})
    engines = design.get("engines", [])
    suites = design.get("suites", {})
    suite_dirs, sessions = [], []
    for rnd in design.get("main_rounds", []):
        for suite in rnd.get("suite_order", list(suites)):
            key = (("main", rnd["round"], None, suite))
            suite_dirs.append(key)
            for scale in suites.get(suite, {}).get("scales", []):
                for engine in engines:
                    sessions.append((*key, scale, engine))
    side = design.get("side_study") or {}
    for rnd in side.get("rounds", []):
        for arm in rnd.get("arm_order", []):
            for suite in rnd.get("suite_order", list(suites)):
                key = ("side", rnd["round"], arm, suite)
                suite_dirs.append(key)
                for scale in suites.get(suite, {}).get("scales", []):
                    sessions.append((*key, scale, "polars"))
    return suite_dirs, sessions


def suite_path(campaign_dir: Path, kind, round_no, arm, suite):
    if kind == "main":
        return campaign_dir / f"round-{round_no}" / suite
    return campaign_dir / "side-study" / f"round-{round_no}" / arm / suite


def scale_dir(scale: str) -> str:
    return "sf" + str(scale).replace(".", "p")


# ------------------------------------------------------------------------------------------------------------- sessions
def account_session(res, sched_row):
    """Timestamp ledger of one engine session (all numbers are differences of clock readings or ratios of such).

    Explained intervals are tiled along the worker's timeline: table/session setup, each record's [started_at_utc,
    ended_at_utc] window, each cleanup window. What is left between them is the residual. Returns None without a worker window.
    """
    start, end = parse_utc(res.get("started_at_utc")), parse_utc(res.get("completed_at_utc"))
    if start is None or end is None:
        return None
    setup = res.get("session_and_table_setup_seconds")
    ready = res.get("launch_to_engine_ready_seconds")
    records = [r for r in res.get("records") or [] if isinstance(r, dict)]
    out = {"worker_span_s": (end - start) / 1e6, "n_records": len(records), "n_errors": len(res.get("query_errors") or []),
           "records_with_window": 0, "negative_overhead": 0, "overlaps": 0}
    intervals = []  # (start_us, end_us, kind)
    if isinstance(setup, (int, float)):
        intervals.append((start, start + int(setup * 1e6), "setup"))
    windows = []
    for record in records:
        a, b = parse_utc(record.get("started_at_utc")), parse_utc(record.get("ended_at_utc"))
        seconds = record.get("seconds")
        if a is None or b is None:
            continue
        out["records_with_window"] += 1
        intervals.append((a, b, "record"))
        if isinstance(seconds, (int, float)) and seconds > 0:
            overhead = (b - a) / 1e6 - seconds
            if overhead < -1e-3:
                out["negative_overhead"] += 1
            windows.append({"phase": record.get("phase"), "start": a, "end": b, "overhead_s": overhead,
                            "overhead_fraction": overhead / seconds, "seconds": seconds})
    out["windows"] = windows
    cleanup_total = 0.0
    for key in ("cleanup_records", "failed_query_cleanup_records"):
        for rec in res.get(key) or []:
            a, b = parse_utc(rec.get("started_at_utc")), parse_utc(rec.get("ended_at_utc"))
            if a is not None and b is not None:
                intervals.append((a, b, "cleanup"))
                cleanup_total += (b - a) / 1e6
    intervals.sort()
    # What would be unexplained had only the timed `seconds` of each record been available (no per-record timestamps).
    timed_total = sum(r["seconds"] for r in records if isinstance(r.get("seconds"), (int, float)))
    out["unexplained_timed_only_s"] = out["worker_span_s"] - (setup if isinstance(setup, (int, float)) else 0.0) - timed_total - cleanup_total
    out["unexplained_timed_only_fraction"] = out["unexplained_timed_only_s"] / out["worker_span_s"] if end > start else None
    out["overlaps"] = sum(1 for p, q in zip(intervals, intervals[1:]) if q[0] < p[1] - 1000)  # > 1 ms of overlap
    explained = sum(max(0, b - a) for a, b, _ in intervals)
    out["unexplained_s"] = (end - start - explained) / 1e6
    out["unexplained_fraction_of_session"] = out["unexplained_s"] / out["worker_span_s"] if end > start else None
    # Residual gaps: before the first interval after setup (should be ~0), between intervals, after the last one.
    gaps = [(q[0] - p[1]) / 1e6 for p, q in zip(intervals, intervals[1:])]
    out["gap_s"] = gaps
    out["tail_s"] = (end - intervals[-1][1]) / 1e6 if intervals else None
    # Process-level pieces from the runner's schedule.json row.
    sched_start = parse_utc(sched_row.get("start_utc")) if sched_row else None
    wall = sched_row.get("process_wall_seconds") if sched_row else None
    if sched_start is not None and isinstance(wall, (int, float)):
        out["launch_to_worker_s"] = (start - sched_start) / 1e6
        out["teardown_s"] = (sched_start + int(wall * 1e6) - end) / 1e6
        out["worker_outside_process_window"] = int(out["launch_to_worker_s"] < 0 or out["teardown_s"] < 0)
        if isinstance(setup, (int, float)) and isinstance(ready, (int, float)):
            # launch_to_engine_ready = (launch -> worker start) + setup + the little code between: closure should be ~0
            out["ready_closure_s"] = ready - setup - out["launch_to_worker_s"]
    return out


def session_ident(key):
    kind, rnd, arm, suite, scale, engine = key
    return "/".join(str(x) for x in (kind, f"r{rnd}", arm, suite, f"sf{scale}", engine) if x)


class SessionRow:
    def __init__(self, key):
        self.key = key  # (kind, round, arm, suite, scale, engine)
        self.sched = None
        self.res = None
        self.res_path = None
        self.res_error = None
        self.log_present = False
        self.plans = 0
        self.account = None
        self.worker_window = None   # (lo, hi) us
        self.process_window = None
        self.gpu_worker = self.gpu_process = self.host_worker = self.host_process = None
        self.phase = None
        self.anomalies = []

    @property
    def scale(self):
        return self.key[4]

    @property
    def engine(self):
        return self.key[5]

    @property
    def ident(self):
        return session_ident(self.key)


def nvsmi_q_provenance(env):
    """Driver version and whether the power-draw fields of `nvidia-smi -q` are numeric or N/A (never their values)."""
    smi = next((i.get("output", "") for i in (env or {}).get("inventory", []) if i.get("command") == ["nvidia-smi", "-q"]), "")
    out = {}
    match = re.search(r"^Driver Version\s*:\s*(\S+)", smi, re.M)
    out["driver_version"] = match.group(1) if match else None
    for label, key in (("Product Name", "product_name"), ("Product Architecture", "architecture")):
        match = re.search(rf"^\s*{label}\s*:\s*(.+?)\s*$", smi, re.M)
        out[key] = match.group(1) if match else None
    for label in ("Average Power Draw", "Instantaneous Power Draw"):
        match = re.search(rf"^\s*{label}\s*:\s*(\S+)", smi, re.M)
        out[label] = "absent" if match is None else ("N/A" if match.group(1).upper().startswith("N/A") else "numeric")
    return out


def phase_alignment(row: SessionRow, host_stream: Stream):
    """Host.csv rows labelled execute for this session against the runner's process window (both UTC)."""
    if host_stream.labels is None or row.process_window is None:
        return None
    lo, hi = row.process_window
    mine = host_stream.labels.filter((pl.col("scale") == str(row.scale)) & (pl.col("engine") == row.engine)
                                     & (pl.col("phase") == "execute"))
    if mine.height == 0:
        return {"rows": 0}
    stamps = mine["t_us"]
    first, last = stamps.min(), stamps.max()
    tol = int(PHASE_TOLERANCE_SECONDS * 1e6)
    return {"rows": mine.height, "start_delta_s": (first - lo) / 1e6, "end_delta_s": (last - hi) / 1e6,
            "rows_outside_window": int(((stamps < lo - tol) | (stamps > hi + tol)).sum())}


# -------------------------------------------------------------------------------------------------------------- host audit
def audit_host(host, final_dir: Path, manifest_path: Path, verify="all"):
    """Audit one host. Returns (host_report, pooled) where `pooled` carries per-record / per-session ledgers for aggregation."""
    campaign_dir = final_dir / f"campaign-{host}"
    report = {"host": host, "campaign_dir_present": campaign_dir.is_dir()}
    pooled = {"windows": [], "accounts": [], "intervals": [], "presence": []}
    if not campaign_dir.is_dir():
        return report, pooled
    campaign, err = load_json(campaign_dir / "campaign.json")
    if campaign is None:
        report["campaign_error"] = err
        return report, pooled
    manifest = read_manifest(manifest_path)
    read_files = [campaign_dir / "campaign.json"]
    suite_keys, session_keys = expected_layout(campaign)
    design = campaign.get("design", {})
    unsupported = set(design.get("expect_gpu_unsupported") or [])

    # ---- pass 1: read every suite dir with a zero offset, to bracket the local-minus-UTC offset
    raw = {}
    for key in suite_keys:
        sdir = SuiteDir(host, *key, suite_path(campaign_dir, *key))
        item = {"sdir": sdir, "env": None, "sched": None, "gpu": None, "host": None}
        for name in ("environment.json", "schedule.json"):
            path = sdir.path / name
            data, error = load_json(path) if path.is_file() else (None, "missing")
            item["env" if name.startswith("env") else "sched"] = data
            item[name + "_error"] = None if data is not None else error
            if path.is_file():
                read_files.append(path)
        item["gpu"] = read_gpu_csv(sdir.path / "gpu.csv")
        item["host"] = read_host_csv(sdir.path / "host.csv")
        read_files += [p for p in (sdir.path / "gpu.csv", sdir.path / "host.csv") if p.is_file()]
        raw[key] = item

    def process_bounds(sched):
        starts, ends = [], []
        for entry in sched or []:
            s = parse_utc(entry.get("start_utc"))
            wall = entry.get("process_wall_seconds")
            if s is not None and isinstance(wall, (int, float)):
                starts.append(s)
                ends.append(s + int(wall * 1e6))
        return (min(starts), max(ends)) if starts else (None, None)

    brackets = []
    clock_rows = []
    for key, item in raw.items():
        gpu, hst, env = item["gpu"], item["host"], item["env"]
        first_start, last_end = process_bounds(item["sched"])
        row = {"suite_dir": item["sdir"].label}
        if gpu.span and first_start is not None:
            lo = (gpu.span[0] - first_start) / 1e6                      # offset >= gpu_first - first_start
            hi = (gpu.span[1] - last_end) / 1e6 + END_SLACK_SECONDS     # offset <= gpu_last - last_end + one gap
            row["gpu_first_minus_first_session_start_s"] = round(lo, 3)
            row["gpu_last_minus_last_session_end_s"] = round(hi - END_SLACK_SECONDS, 3)
            row["bracket_s"] = [round(lo, 3), round(hi, 3)]
            brackets.append((lo, hi))
        if gpu.span and hst.span:
            row["gpu_first_minus_host_first_s"] = round((gpu.span[0] - hst.span[0]) / 1e6, 3)
            row["gpu_last_minus_host_last_s"] = round((gpu.span[1] - hst.span[1]) / 1e6, 3)
        if env:
            started = parse_utc(env.get("started_at_utc"))
            smi = next((i.get("output", "") for i in env.get("inventory", []) if i.get("command") == ["nvidia-smi", "-q"]), "")
            match = re.search(r"^Timestamp\s*:\s*(.+)$", smi, re.M)
            if match and started is not None:
                try:
                    stamp = datetime.strptime(match.group(1).strip(), "%a %b %d %H:%M:%S %Y")
                    row["nvsmi_q_minus_env_start_s"] = round(to_us(stamp.replace(tzinfo=timezone.utc)) / 1e6 - started / 1e6, 3)
                except ValueError:
                    row["nvsmi_q_timestamp_unparsed"] = True
            if gpu.span and started is not None:
                row["gpu_first_minus_env_start_s"] = round((gpu.span[0] - started) / 1e6, 3)
        row["gpu_backward_steps"] = gpu.backward_steps
        clock_rows.append(row)
    lo_all = max((b[0] for b in brackets), default=None)
    hi_all = min((b[1] for b in brackets), default=None)
    candidates, unambiguous = resolve_offset(lo_all, hi_all)
    offset_s = candidates[0] if unambiguous else 0
    clock = {"suite_dirs_bracketed": len(brackets), "bracket_intersection_s": [round(lo_all, 3), round(hi_all, 3)] if brackets else None,
             "bracket_empty": bool(brackets) and lo_all > hi_all, "candidate_offsets_s": candidates,
             "offset_unambiguous": unambiguous, "offset_applied_s": offset_s,
             "bracket_width_s": round(hi_all - lo_all, 3) if brackets else None}
    for field in TIME_FIELDS:
        values = [r[field] for r in clock_rows if field in r]
        clock[field] = {"n": len(values), "min": min(values), "max": max(values)} if values else {"n": 0}
    clock["gpu_backward_steps_total"] = sum(r["gpu_backward_steps"] for r in clock_rows)
    # Independent evidence: the `nvidia-smi -q` Timestamp (whole seconds, local) against started_at_utc, rounded to the nearest 15 minutes.
    smi_values = [r["nvsmi_q_minus_env_start_s"] for r in clock_rows if "nvsmi_q_minus_env_start_s" in r]
    clock["independent_check_offset_s"] = (round(statistics.median(smi_values) / OFFSET_QUANTUM_SECONDS) * OFFSET_QUANTUM_SECONDS
                                           if smi_values else None)
    clock["independent_check_agrees"] = (clock["independent_check_offset_s"] == offset_s) if smi_values else None
    report["source_provenance"] = {
        name: dict(Counter(((item["env"] or {}).get("source_sha256") or {}).get(name) for item in raw.values()))
        for name in ("worker.py", "run_experiment.py")}
    provenance = [nvsmi_q_provenance(item["env"]) for item in raw.values()]
    report["nvidia_smi_q"] = {"driver_versions": dict(Counter(p["driver_version"] for p in provenance)),
                              "product_names": dict(Counter(p["product_name"] for p in provenance)),
                              "architectures": dict(Counter(p["architecture"] for p in provenance)),
                              "draw_field_average": dict(Counter(p["Average Power Draw"] for p in provenance)),
                              "draw_field_instantaneous": dict(Counter(p["Instantaneous Power Draw"] for p in provenance))}
    report["time_domain"] = clock
    offset_us = int(offset_s * 1e6)

    # ---- pass 2: UTC-mapped GPU streams, session rows, windows
    stream_rows, sessions = [], {}
    expected = set(session_keys)
    for key, item in raw.items():
        sdir, hst = item["sdir"], item["host"]
        gpu = item["gpu"]
        gpu.shift(offset_us)
        gpu_spacing = interval_stats(gpu.sorted_t_us, GPU_GAP_SECONDS)
        host_spacing = interval_stats(hst.sorted_t_us, HOST_GAP_SECONDS)
        first_start, last_end = process_bounds(item["sched"])
        slack = int(END_SLACK_SECONDS * 1e6)
        telemetry_covers = bool(gpu.span and first_start is not None and gpu.span[0] <= first_start and gpu.span[1] >= last_end - slack)
        host_covers = bool(hst.span and first_start is not None and hst.span[0] <= first_start + 2_000_000 and hst.span[1] >= last_end - 2_000_000)
        schedule = {(str(e.get("scale")), e.get("engine")): e for e in item["sched"] or []}
        duplicates = len(item["sched"] or []) - len(schedule)
        proc_windows = sorted((parse_utc(e.get("start_utc")), parse_utc(e.get("start_utc")) + int(e["process_wall_seconds"] * 1e6),
                               session_ident((*key, str(e.get("scale")), e.get("engine"))))
                              for e in item["sched"] or [] if parse_utc(e.get("start_utc")) is not None
                              and isinstance(e.get("process_wall_seconds"), (int, float)))
        overlapping = sum(1 for a, b in zip(proc_windows, proc_windows[1:]) if b[0] < a[1])
        gap_events = []
        for stream, threshold in ((gpu, GPU_GAP_SECONDS), (hst, HOST_GAP_SECONDS)):
            for a, b in zip(stream.sorted_t_us, stream.sorted_t_us[1:]):
                if (b - a) / 1e6 > threshold:
                    owner = next((ident for lo, hi, ident in proc_windows if lo <= a <= hi), None)
                    gap_events.append({"stream": stream.kind, "start_utc": datetime.fromtimestamp(a / 1e6, timezone.utc).isoformat(),
                                       "gap_s": round((b - a) / 1e6, 3), "during_session": owner or "between sessions"})
        stream_rows.append({
            "suite_dir": sdir.label, "gpu_present": gpu.present, "host_present": hst.present, "gpu_error": gpu.error, "host_error": hst.error,
            "gpu_header_ok": gpu.header_ok, "host_header_ok": hst.header_ok,
            "gpu_ends_with_newline": gpu.ends_with_newline, "host_ends_with_newline": hst.ends_with_newline,
            "gpu_rows": gpu.rows, "host_rows": hst.rows, "host_stamps_without_fraction": hst.stamps_without_fraction,
            "gpu_bad_timestamp_rows": gpu.bad_timestamp_rows, "host_bad_timestamp_rows": hst.bad_timestamp_rows,
            "gpu_incomplete_rows": gpu.incomplete_rows, "host_incomplete_rows": hst.incomplete_rows,
            "draw_ok_rows": gpu.draw_ok_rows, "draw_null_rows": gpu.draw_null_rows, "draw_na_rows": gpu.draw_na_rows,
            "draw_unparseable_rows": gpu.draw_unparseable_rows,
            "gpu_indices": list(gpu.indices), "gpu_backward_steps": gpu.backward_steps, "gpu_duplicate_stamps": gpu.duplicate_stamps,
            "host_backward_steps": hst.backward_steps, "host_duplicate_stamps": hst.duplicate_stamps,
            "gpu_spacing": gpu_spacing, "host_spacing": host_spacing,
            "gpu_stream_covers_all_sessions": telemetry_covers, "host_stream_covers_all_sessions": host_covers,
            "gpu_gaps_over_1s": [g for g in gap_events if g["stream"] == "gpu"],
            "host_gaps_over_2s": [g for g in gap_events if g["stream"] == "host"],
            "schedule_duplicate_entries": duplicates, "process_windows_overlapping": overlapping,
            "environment_json_error": item.get("environment.json_error"), "schedule_json_error": item.get("schedule.json_error"),
            "gpu_telemetry_started_flag": (item["env"] or {}).get("gpu_telemetry", {}).get("started") if item["env"] else None,
        })
        for (scale, engine), entry in schedule.items():
            skey = (*key, scale, engine)
            row = SessionRow(skey)
            row.sched = entry
            res_path = sdir.path / scale_dir(scale) / engine / "results.json"
            row.res_path = res_path
            if res_path.is_file():
                read_files.append(res_path)
                row.res, row.res_error = load_json(res_path)
            else:
                row.res_error = "missing"
            row.log_present = (sdir.path / scale_dir(scale) / f"{engine}.log").is_file()
            plans = res_path.parent / "plans"
            row.plans = sum(1 for _ in plans.iterdir()) if plans.is_dir() else 0
            s = parse_utc(entry.get("start_utc"))
            wall = entry.get("process_wall_seconds")
            if s is not None and isinstance(wall, (int, float)):
                row.process_window = (s, s + int(wall * 1e6))
            if row.res:
                row.account = account_session(row.res, entry)
                a, b = parse_utc(row.res.get("started_at_utc")), parse_utc(row.res.get("completed_at_utc"))
                if a is not None and b is not None:
                    row.worker_window = (a, b)
            for attr, window, stream, gap in (("gpu_worker", row.worker_window, gpu, GPU_GAP_SECONDS),
                                              ("gpu_process", row.process_window, gpu, GPU_GAP_SECONDS),
                                              ("host_worker", row.worker_window, hst, HOST_GAP_SECONDS),
                                              ("host_process", row.process_window, hst, HOST_GAP_SECONDS)):
                if window:
                    setattr(row, attr, window_stats(stream.sorted_t_us, window[0], window[1], gap))
            row.phase = phase_alignment(row, hst)
            sessions[skey] = row
            if row.res is not None:
                recs = [r for r in row.res.get("records") or [] if isinstance(r, dict)]
                pooled["presence"].append({
                    "host": host, "suite": key[3], "scale": scale, "records": len(recs),
                    "with_window": sum(1 for r in recs if parse_utc(r.get("started_at_utc")) is not None
                                       and parse_utc(r.get("ended_at_utc")) is not None),
                    "with_timed_stamps": sum(1 for r in recs if parse_utc(r.get("timed_start_utc")) is not None
                                             and parse_utc(r.get("timed_end_utc")) is not None),
                    "with_monotonic": sum(1 for r in recs if isinstance(r.get("timed_start_monotonic_ns"), int)
                                          and isinstance(r.get("timed_end_monotonic_ns"), int))})
            if row.account:
                gm = gpu_spacing.get("median_s")
                for w in row.account["windows"]:
                    w["gpu_samples"] = count_in_window(gpu.sorted_t_us, w["start"], w["end"])
                    w["gpu_median_interval_s"] = gm
                    w["scale"] = scale
                    w["host"] = host
                    w["suite"] = key[3]
                    pooled["windows"].append(w)
                pooled["accounts"].append({"host": host, "scale": scale, "suite": key[3], **{k: v for k, v in row.account.items() if k != "windows"}})
    report["streams"] = stream_rows

    # ---- completeness against the design
    found = set(sessions)
    missing = sorted(expected - found)
    extra = sorted(found - expected)
    status = Counter()
    anomalies = []
    for skey, row in sorted(sessions.items(), key=lambda kv: str(kv[0])):
        sched = row.sched or {}
        problems = []
        if row.res is None:
            problems.append(f"results.json {row.res_error}")
        if sched.get("status") != "complete" or sched.get("returncode") != 0:
            problems.append("schedule status/returncode not complete/0")
        if row.res is not None:
            if not row.res.get("completed_at_utc"):
                problems.append("no completed_at_utc")
            if row.res.get("query_execution_status") not in ("complete", "partial"):
                problems.append(f"query_execution_status={row.res.get('query_execution_status')}")
            queries = row.res.get("queries") or []
            errors = row.res.get("query_errors") or []
            failed = {e.get("query") for e in errors}
            # a query whose warmup fails is dropped from the measured trials (worker failed_queries): one error, no records
            expected_records = (len(queries) - len(failed)) * (1 + (row.res.get("runs") or 0))
            unexpected_errors = [e for e in errors if e.get("query") not in unsupported]
            if len(row.res.get("records") or []) != expected_records or len(errors) != len(failed):
                problems.append("records/errors do not match (queries - failed) * (1 + runs) and one error per failed query")
            if unexpected_errors:
                problems.append(f"{len(unexpected_errors)} query error(s) outside expect_gpu_unsupported")
            if row.res.get("records") and any("started_at_utc" not in r or "ended_at_utc" not in r for r in row.res["records"]):
                problems.append("record without started_at_utc/ended_at_utc")
            if row.plans < len(row.res.get("records") or []):
                problems.append("fewer plan files than records")
        if not row.log_present:
            problems.append("engine log missing")
        if row.gpu_worker is None and row.res is not None:
            problems.append("no GPU window (worker span)")
        if row.account:
            if row.account["negative_overhead"]:
                problems.append("record window shorter than its timed seconds")
            if row.account["overlaps"]:
                problems.append("overlapping explained intervals")
            if row.account.get("worker_outside_process_window"):
                problems.append("worker start/completion outside the runner's process window")
        if row.phase and row.phase.get("rows_outside_window"):
            problems.append("host.csv execute rows outside the process window")
        if row.phase is not None and row.phase.get("rows") == 0:
            problems.append("no host.csv execute rows")
        status["with_problems" if problems else "clean"] += 1
        if problems:
            anomalies.append({"session": row.ident, "problems": problems})
    stream_problems = []
    for s in stream_rows:
        for field in ("gpu_present", "host_present", "gpu_header_ok", "host_header_ok", "gpu_ends_with_newline", "host_ends_with_newline",
                      "gpu_stream_covers_all_sessions", "host_stream_covers_all_sessions"):
            if not s[field]:
                stream_problems.append({"suite_dir": s["suite_dir"], "problem": f"{field} is false"})
        for field in ("gpu_error", "host_error", "environment_json_error", "schedule_json_error"):
            if s[field]:
                stream_problems.append({"suite_dir": s["suite_dir"], "problem": f"{field}: {s[field]}"})
        for field in ("gpu_bad_timestamp_rows", "host_bad_timestamp_rows", "gpu_incomplete_rows", "host_incomplete_rows",
                      "draw_null_rows", "draw_na_rows", "draw_unparseable_rows", "gpu_backward_steps", "host_backward_steps", "gpu_duplicate_stamps",
                      "schedule_duplicate_entries", "process_windows_overlapping"):
            if s[field]:
                stream_problems.append({"suite_dir": s["suite_dir"], "problem": f"{field}={s[field]}"})
        if s["gpu_telemetry_started_flag"] is not True:
            stream_problems.append({"suite_dir": s["suite_dir"], "problem": "environment.json gpu_telemetry.started is not true"})
        if s["gpu_indices"] != ["0"]:
            stream_problems.append({"suite_dir": s["suite_dir"], "problem": f"gpu indices {s['gpu_indices']}"})
    # Files present in the tree but unknown to the design (stray suite dirs, extra sessions)
    on_disk_suites = sorted(
        {str(p.parent.relative_to(campaign_dir)) for p in campaign_dir.rglob("gpu.csv")})
    expected_suites = sorted({str(suite_path(campaign_dir, *k).relative_to(campaign_dir)) for k in suite_keys})
    report["completeness"] = {
        "suite_dirs_expected": len(suite_keys), "suite_dirs_with_gpu_csv": len(on_disk_suites),
        "suite_dirs_unexpected": sorted(set(on_disk_suites) - set(expected_suites)),
        "sessions_expected": len(expected), "sessions_found": len(found), "sessions_missing": [".".join(map(str, m)) for m in missing],
        "sessions_unexpected": [".".join(map(str, e)) for e in extra],
        "sessions_clean": status["clean"], "sessions_with_problems": status["with_problems"],
        "session_problems": anomalies, "stream_problems": stream_problems,
        "queries_without_record_window": sum(1 for row in sessions.values() for r in ((row.res or {}).get("records") or [])
                                              if parse_utc(r.get("started_at_utc")) is None or parse_utc(r.get("ended_at_utc")) is None),
        "records_total": sum(len((row.res or {}).get("records") or []) for row in sessions.values()),
        "query_errors_total": sum(len((row.res or {}).get("query_errors") or []) for row in sessions.values()),
        "query_errors_expected_gpu_unsupported": sum(1 for row in sessions.values() for e in ((row.res or {}).get("query_errors") or [])
                                                      if e.get("query") in unsupported),
        "round_files": round_file_presence(campaign_dir, design),
    }

    # ---- coverage summaries (no engine split)
    report["coverage"] = coverage_summary(sessions.values())
    report["phase_alignment"] = phase_summary(sessions.values())
    report["integrity"] = integrity(host, final_dir, manifest, read_files, verify)
    pooled["intervals"] = [(host, s["suite_dir"], s["gpu_spacing"], s["host_spacing"]) for s in stream_rows]
    return report, pooled


def round_file_presence(campaign_dir, design):
    out = {"attempt_json": 0, "gates_json": 0, "preflight_report": 0, "rounds_expected": 0}
    rounds = [campaign_dir / f"round-{r['round']}" for r in design.get("main_rounds", [])]
    rounds += [campaign_dir / "side-study" / f"round-{r['round']}" for r in (design.get("side_study") or {}).get("rounds", [])]
    out["rounds_expected"] = len(rounds)
    for rdir in rounds:
        out["attempt_json"] += (rdir / "attempt.json").is_file()
        out["gates_json"] += (rdir / "gates.json").is_file()
    out["preflight_report"] = sum((campaign_dir / f"round-{r['round']}" / "preflight" / "preflight-report.json").is_file()
                                  for r in design.get("main_rounds", []))
    return out


def coverage_summary(sessions):
    sessions = list(sessions)

    def summarise(attr, gap_s):
        stats = [getattr(s, attr) for s in sessions if getattr(s, attr)]
        covered = [s["covered_fraction"] for s in stats]
        return {"sessions": len(stats), "sessions_without_window": len(sessions) - len(stats),
                "sessions_with_zero_samples": sum(1 for s in stats if s["n"] == 0),
                "samples_in_windows_total": sum(s["n"] for s in stats),
                "sessions_with_a_gap_over_threshold": sum(1 for s in stats if s["gaps_over_threshold"]),
                "gap_threshold_s": gap_s,
                "covered_fraction": distribution(covered, 5),
                "median_interval_s": distribution([s["median_s"] for s in stats], 4),
                "p99_interval_s": distribution([s["p99_s"] for s in stats], 4),
                "max_gap_s": distribution([s["max_gap_s"] for s in stats], 4)}
    return {"gpu_worker_window": summarise("gpu_worker", GPU_GAP_SECONDS), "gpu_process_window": summarise("gpu_process", GPU_GAP_SECONDS),
            "host_worker_window": summarise("host_worker", HOST_GAP_SECONDS), "host_process_window": summarise("host_process", HOST_GAP_SECONDS)}


def phase_summary(sessions):
    rows = [s.phase for s in sessions if s.phase and s.phase.get("rows")]
    return {"sessions_checked": len(rows),
            "first_execute_row_minus_process_start_s": distribution([r["start_delta_s"] for r in rows], 3),
            "last_execute_row_minus_process_end_s": distribution([r["end_delta_s"] for r in rows], 3),
            "execute_rows_outside_process_window": sum(r["rows_outside_window"] for r in rows),
            "sessions_without_execute_rows": sum(1 for s in sessions if s.phase and not s.phase.get("rows"))}


def integrity(host, final_dir, manifest, read_files, verify):
    """sha256 of the files this audit read against FINAL-<host>.SHA256SUMS, plus manifest/tree set differences."""
    out = {"manifest_entries": len(manifest), "files_read": len(read_files)}
    if not manifest:
        out["manifest"] = "missing"
        return out
    targets = read_files
    if verify == "none":
        targets = []
    elif verify == "spot":
        targets = read_files[:: max(1, len(read_files) // 40)]
    mismatched, absent = [], []
    for path in targets:
        rel = str(path.relative_to(final_dir))
        want = manifest.get(rel)
        if want is None:
            absent.append(rel)
        elif sha256_of(path) != want:
            mismatched.append(rel)
    out.update({"mode": verify, "files_hashed": len(targets), "hash_mismatches": mismatched, "read_but_not_in_manifest": absent})
    campaign_dir = final_dir / f"campaign-{host}"
    on_disk = {str(p.relative_to(final_dir)) for p in final_dir.rglob("*") if p.is_file()}
    in_manifest = set(manifest)
    on_disk.discard(f"FINAL-{host}.SHA256SUMS")  # the manifest cannot list itself
    out["manifest_entries_missing_on_disk"] = len(in_manifest - on_disk)
    out["tree_files_not_in_manifest"] = len(on_disk - in_manifest)
    out["tree_files_not_in_manifest_examples"] = sorted(on_disk - in_manifest)[:5]
    out["manifest_missing_examples"] = sorted(in_manifest - on_disk)[:5]
    return out


# ----------------------------------------------------------------------------------------------------------------- pooling
def pooled_report(pooled_by_host, resolutions=RESOLUTIONS_SECONDS):
    """Unexplained time and per-query-window uncertainty pooled over hosts and engines; split by scale only."""
    accounts = [a for p in pooled_by_host.values() for a in p["accounts"]]
    windows = [w for p in pooled_by_host.values() for w in p["windows"]]
    presence = [r for p in pooled_by_host.values() for r in p["presence"]]
    by_scale_acc, by_scale_win = defaultdict(list), defaultdict(list)
    for a in accounts:
        by_scale_acc[a["scale"]].append(a)
    for w in windows:
        by_scale_win[w["scale"]].append(w)

    def accounting(rows):
        gaps = [g for r in rows if not r["n_errors"] for g in r["gap_s"]]
        return {
            "sessions": len(rows), "sessions_with_query_errors": sum(1 for r in rows if r["n_errors"]),
            "unexplained_s_session_total": distribution([r["unexplained_s"] for r in rows if not r["n_errors"]], 4),
            "unexplained_fraction_of_session": distribution([r["unexplained_fraction_of_session"] for r in rows if not r["n_errors"]], 6),
            "unexplained_s_if_only_timed_seconds": distribution([r["unexplained_timed_only_s"] for r in rows if not r["n_errors"]], 4),
            "unexplained_fraction_if_only_timed_seconds": distribution([r["unexplained_timed_only_fraction"] for r in rows if not r["n_errors"]], 6),
            "gap_between_explained_intervals_s": distribution(gaps, 4),
            "gaps_over_0p1s": sum(1 for g in gaps if g > 0.1), "gaps_total": len(gaps),
            "tail_after_last_interval_s": distribution([r["tail_s"] for r in rows if not r["n_errors"]], 4),
            "launch_to_worker_start_s": distribution([r.get("launch_to_worker_s") for r in rows], 3),
            "teardown_after_completed_at_utc_s": distribution([r.get("teardown_s") for r in rows], 3),
            "launch_to_ready_closure_s": distribution([r.get("ready_closure_s") for r in rows], 3),
        }

    def query_windows(rows):
        measured = [w for w in rows if w["phase"] == "measured"]
        out = {"windows": len(rows), "measured_windows": len(measured), "warmup_windows": len(rows) - len(measured),
               "in_window_overhead_s": distribution([w["overhead_s"] for w in measured], 4),
               "in_window_overhead_fraction_of_query_time": distribution([w["overhead_fraction"] for w in measured], 5),
               "windows_with_fewer_gpu_samples_than": {str(n): sum(1 for w in measured if w["gpu_samples"] < n) for n in SAMPLE_COUNT_THRESHOLDS}}
        sampled = {}
        for label, resolution in (("2x_sampling_interval", None), *[(f"{r}s_resolution", r) for r in resolutions]):
            fractions = []
            for w in measured:
                step = (2 * (w["gpu_median_interval_s"] or 0.2)) if resolution is None else 2 * resolution
                fractions.append((w["overhead_s"] + step) / w["seconds"])
            sampled[label] = {"uncertainty_fraction": distribution(fractions, 5),
                              "share_within": {f"{int(f * 100)}%": round(sum(1 for x in fractions if x <= f) / len(fractions), 4) if fractions else None
                                               for f in UNCERTAINTY_FRACTIONS}}
        out["boundary_uncertainty"] = sampled
        return out

    ordered = sorted(by_scale_acc, key=lambda s: SCALE_ORDER.get(s, 1e9))

    def stamp_table():
        cells = defaultdict(lambda: Counter())
        for r in presence:
            cell = cells[(r["host"], r["suite"], r["scale"])]
            for key in ("records", "with_window", "with_timed_stamps", "with_monotonic"):
                cell[key] += r[key]
        return {f"{h}/{suite}/sf{scale}": dict(cells[(h, suite, scale)])
                for h, suite, scale in sorted(cells, key=lambda k: (k[0], k[1], SCALE_ORDER.get(k[2], 1e9)))}

    def width(rows):
        measured = [w for w in rows if w["phase"] == "measured"]
        return {"measured_windows": len(measured),
                "window_minus_timed_seconds_s": distribution([w["overhead_s"] for w in measured], 4),
                "window_minus_timed_seconds_fraction_of_query_time": distribution([w["overhead_fraction"] for w in measured], 5)}

    by_host, by_suite = defaultdict(list), defaultdict(list)
    for w in windows:
        by_host[w["host"]].append(w)
        by_suite[w["suite"]].append(w)
    return {
        "record_stamp_presence": {
            "records": sum(r["records"] for r in presence), "with_started_ended_at_utc": sum(r["with_window"] for r in presence),
            "with_timed_start_end_utc": sum(r["with_timed_stamps"] for r in presence),
            "with_timed_monotonic_ns": sum(r["with_monotonic"] for r in presence),
            "by_host_suite_scale": stamp_table()},
        "window_width_by_host": {h: width(by_host[h]) for h in sorted(by_host)},
        "window_width_by_suite": {k: width(by_suite[k]) for k in sorted(by_suite)},
        "pooling": "hosts and engines pooled; split by scale only; never by engine or host",
        "session_accounting_all_scales": accounting(accounts),
        "session_accounting_by_scale": {s: accounting(by_scale_acc[s]) for s in ordered},
        "query_windows_all_scales": query_windows(windows),
        "query_windows_by_scale": {s: query_windows(by_scale_win[s]) for s in sorted(by_scale_win, key=lambda x: SCALE_ORDER.get(x, 1e9))},
    }


# --------------------------------------------------------------------------------------------------------------- running
def run(hosts_root: Path, manifests: Path, hosts=DEFAULT_HOSTS, verify="all"):
    reports, pooled = {}, {}
    for host in hosts:
        final_dir = hosts_root / host / "final"
        reports[host], pooled[host] = audit_host(host, final_dir, manifests / f"FINAL-{host}.SHA256SUMS", verify)
    report = {"tool": "paper3_feasibility", "version": VERSION, "polars": pl.__version__, "hosts": reports,
              "pooled": pooled_report(pooled),
              "parameters": {"gpu_gap_s": GPU_GAP_SECONDS, "host_gap_s": HOST_GAP_SECONDS, "phase_tolerance_s": PHASE_TOLERANCE_SECONDS,
                             "offset_quantum_s": OFFSET_QUANTUM_SECONDS, "resolutions_s": list(RESOLUTIONS_SECONDS), "verify_hashes": verify}}
    assert_blinded(report)
    return report


def fmt(value):
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def dist_line(d):
    if not d or not d.get("n"):
        return "n=0"
    return "n={n} min={min} p50={p50} p90={p90} p99={p99} max={max}".format(**{k: fmt(v) for k, v in d.items()})


def render(report):
    lines = ["PAPER 3 BLINDED FEASIBILITY AUDIT (structure only; no outcome values read or printed)", ""]
    for host, h in report["hosts"].items():
        lines.append(f"== host {host}")
        if not h.get("campaign_dir_present") or "time_domain" not in h:
            lines += ["  campaign directory or campaign.json unavailable", ""]
            continue
        c, comp, integ = h["time_domain"], h["completeness"], h["integrity"]
        lines.append(f"  time domain: bracket intersection over {c['suite_dirs_bracketed']} suite dirs = {c['bracket_intersection_s']} s "
                     f"(width {c['bracket_width_s']} s); 15-min candidates {c['candidate_offsets_s']}; unambiguous={c['offset_unambiguous']}; "
                     f"applied offset {c['offset_applied_s']} s")
        for field in TIME_FIELDS:
            lines.append(f"    {field}: {c[field]}")
        lines.append(f"    independent check (nvidia-smi -q Timestamp vs environment started_at_utc, nearest 15 min): "
                     f"{c['independent_check_offset_s']} s, agrees with the bracket: {c['independent_check_agrees']}")
        lines.append(f"    gpu backward timestamp steps (all suite dirs): {c['gpu_backward_steps_total']}")
        q = h["nvidia_smi_q"]
        lines.append(f"    nvidia-smi -q: {q['product_names']} {q['architectures']}; drivers {q['driver_versions']}; Average Power Draw field {q['draw_field_average']}; "
                     f"Instantaneous Power Draw field {q['draw_field_instantaneous']}")
        lines.append(f"  completeness: suite dirs {comp['suite_dirs_with_gpu_csv']}/{comp['suite_dirs_expected']}, sessions "
                     f"{comp['sessions_found']}/{comp['sessions_expected']} (missing {len(comp['sessions_missing'])}, unexpected "
                     f"{len(comp['sessions_unexpected'])}), clean {comp['sessions_clean']}, with problems {comp['sessions_with_problems']}, "
                     f"stream problems {len(comp['stream_problems'])}")
        lines.append(f"    records {comp['records_total']} (without a timestamp window: {comp['queries_without_record_window']}); query errors "
                     f"{comp['query_errors_total']} (expected GPU-unsupported: {comp['query_errors_expected_gpu_unsupported']}); round files {comp['round_files']}")
        for attr, label in (("gpu_worker_window", "gpu / worker window"), ("gpu_process_window", "gpu / process window"),
                            ("host_worker_window", "host / worker window"), ("host_process_window", "host / process window")):
            cov = h["coverage"][attr]
            lines.append(f"  coverage {label}: sessions {cov['sessions']}, zero-sample {cov['sessions_with_zero_samples']}, samples {cov['samples_in_windows_total']}, "
                         f"with a gap > {cov['gap_threshold_s']} s: {cov['sessions_with_a_gap_over_threshold']}")
            lines.append(f"      covered fraction: {dist_line(cov['covered_fraction'])}")
            lines.append(f"      median interval s: {dist_line(cov['median_interval_s'])}")
            lines.append(f"      p99 interval s: {dist_line(cov['p99_interval_s'])}; max gap s: {dist_line(cov['max_gap_s'])}")
        ph = h["phase_alignment"]
        lines.append(f"  host.csv phase vs process window: sessions {ph['sessions_checked']}, rows outside {ph['execute_rows_outside_process_window']}, "
                     f"without execute rows {ph['sessions_without_execute_rows']}")
        lines.append(f"      first execute row minus start s: {dist_line(ph['first_execute_row_minus_process_start_s'])}")
        lines.append(f"      last execute row minus end s: {dist_line(ph['last_execute_row_minus_process_end_s'])}")
        draw_ok = sum(s["draw_ok_rows"] for s in h["streams"])
        draw_bad = sum(s["draw_null_rows"] + s["draw_na_rows"] + s["draw_unparseable_rows"] for s in h["streams"])
        lines.append(f"  host.csv stamps without a fractional second (parsed by the %.f format): {sum(s['host_stamps_without_fraction'] for s in h['streams'])}")
        lines.append(f"  draw column parse: ok rows {draw_ok}, null/N-A/unparseable rows {draw_bad}; gpu gaps > 1 s in "
                     f"{sum(1 for s in h['streams'] if s['gpu_gaps_over_1s'])} suite dirs ({sum(len(s['gpu_gaps_over_1s']) for s in h['streams'])} gaps); "
                     f"host gaps > 2 s in {sum(1 for s in h['streams'] if s['host_gaps_over_2s'])} suite dirs")
        lines.append(f"  integrity: hashed {integ.get('files_hashed')}/{integ['files_read']} files read, mismatches {len(integ.get('hash_mismatches', []))}, "
                     f"read-but-unlisted {len(integ.get('read_but_not_in_manifest', []))}; manifest entries missing on disk "
                     f"{integ.get('manifest_entries_missing_on_disk')}, tree files not in manifest {integ.get('tree_files_not_in_manifest')}")
        for problem in comp["session_problems"][:10]:
            lines.append(f"    ! {problem['session']}: {'; '.join(problem['problems'])}")
        for problem in comp["stream_problems"][:10]:
            lines.append(f"    ! {problem['suite_dir']}: {problem['problem']}")
        lines.append("")
    p = report["pooled"]
    pr = p["record_stamp_presence"]
    lines.append("== per-trial timestamps in the archive")
    for h, rep in report["hosts"].items():
        if "source_provenance" in rep:
            lines.append(f"  {h}: worker.py sha256 {rep['source_provenance']['worker.py']}")
    lines.append(f"  records {pr['records']}: with started_at_utc and ended_at_utc {pr['with_started_ended_at_utc']}, "
                 f"with timed_start/end_utc {pr['with_timed_start_end_utc']}, with timed monotonic ns {pr['with_timed_monotonic_ns']}")
    bad = {k: v for k, v in pr["by_host_suite_scale"].items() if v["with_window"] != v["records"]}
    lines.append(f"  host/suite/scale cells {len(pr['by_host_suite_scale'])}; cells where some record lacks the window stamps: {len(bad)}")
    for label, table in (("host", p["window_width_by_host"]), ("suite", p["window_width_by_suite"])):
        for key, w in table.items():
            lines.append(f"  window minus timed seconds by {label} {key}: n={w['measured_windows']}; s {dist_line(w['window_minus_timed_seconds_s'])}; "
                         f"fraction of query time p50/p99 {fmt(w['window_minus_timed_seconds_fraction_of_query_time'].get('p50'))}/"
                         f"{fmt(w['window_minus_timed_seconds_fraction_of_query_time'].get('p99'))}")
    lines.append("")
    lines.append("== pooled over hosts and engines (split by scale only)")
    allacc, allwin = p["session_accounting_all_scales"], p["query_windows_all_scales"]
    lines.append(f"  session accounting: sessions {allacc['sessions']} (with query errors {allacc['sessions_with_query_errors']}, excluded below)")
    for key in ("unexplained_s_session_total", "unexplained_fraction_of_session", "unexplained_s_if_only_timed_seconds",
                "unexplained_fraction_if_only_timed_seconds", "gap_between_explained_intervals_s", "tail_after_last_interval_s",
                "launch_to_worker_start_s", "teardown_after_completed_at_utc_s", "launch_to_ready_closure_s"):
        lines.append(f"    {key}: {dist_line(allacc[key])}")
    lines.append(f"    gaps between explained intervals above 0.1 s: {allacc['gaps_over_0p1s']} of {allacc['gaps_total']}")
    lines.append("  by scale: unexplained s per session / fraction of session")
    for scale, a in p["session_accounting_by_scale"].items():
        lines.append(f"    sf{scale}: sessions {a['sessions']}; {dist_line(a['unexplained_s_session_total'])}; fraction p99 "
                     f"{fmt(a['unexplained_fraction_of_session'].get('p99'))}; timed-only: {dist_line(a['unexplained_s_if_only_timed_seconds'])}; "
                     f"fraction p99 {fmt(a['unexplained_fraction_if_only_timed_seconds'].get('p99'))}")
    lines.append(f"  query windows: {allwin['windows']} ({allwin['measured_windows']} measured, {allwin['warmup_windows']} warmup)")
    lines.append(f"    in-window overhead s: {dist_line(allwin['in_window_overhead_s'])}")
    lines.append(f"    in-window overhead / query time: {dist_line(allwin['in_window_overhead_fraction_of_query_time'])}")
    lines.append("  by scale (measured windows): overhead s p50/p99; overhead fraction p50/p99; windows with < 5/25/100 GPU samples; "
                 "share of windows with boundary uncertainty <= 5%/10%/25% [2x sampling | 1 s resolution]")
    for scale, w in p["query_windows_by_scale"].items():
        o, f = w["in_window_overhead_s"], w["in_window_overhead_fraction_of_query_time"]
        u0, u1 = w["boundary_uncertainty"]["2x_sampling_interval"]["share_within"], w["boundary_uncertainty"]["1.0s_resolution"]["share_within"]
        lines.append(f"    sf{scale}: n={w['measured_windows']}; overhead s {fmt(o.get('p50'))}/{fmt(o.get('p99'))}; fraction {fmt(f.get('p50'))}/{fmt(f.get('p99'))}; "
                     f"<5/25/100 samples {list(w['windows_with_fewer_gpu_samples_than'].values())}; share [{u0['5%']},{u0['10%']},{u0['25%']}] | "
                     f"[{u1['5%']},{u1['10%']},{u1['25%']}]")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--hosts-root", type=Path, default=REPO / "rerun" / "hosts")
    parser.add_argument("--manifests", type=Path, default=REPO / "results" / "campaign-20261030" / "raw-manifests")
    parser.add_argument("--hosts", nargs="+", default=list(DEFAULT_HOSTS))
    parser.add_argument("--verify-hashes", choices=("all", "spot", "none"), default="all")
    parser.add_argument("--json", type=Path, default=None, help="also write the full structured report here")
    args = parser.parse_args(argv)
    report = run(args.hosts_root, args.manifests, tuple(args.hosts), args.verify_hashes)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    sys.stdout.write(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
