#!/usr/bin/env python3
"""Paper 3 pre-lock GPU power probes P5 (idle floor, overhead), P6 (counter versus sampled power), P7 (power.draw
semantics, GH200 scope) and the G8 sync pulse. Synthetic load only: a spin kernel loaded through the CUDA driver API
(no toolkit, no study query, no data). Standard library only; power_telemetry.py must sit next to this file.

  python3 p3_gpu_probes.py semantics --out DIR   power.draw / .average / .instant (+ module.*) at 50 ms under pulses;
                                                 counter versus each field's integral (GPU-only or module scope)
  python3 p3_gpu_probes.py p6 --out DIR          NVML counter delta versus the 20 ms power.draw.instant integral, on
                                                 isolated pulses and on back-to-back windows of known length
  python3 p3_gpu_probes.py g8 --out DIR          3 s pulses: onset in gpu.csv-style 200 ms nvidia-smi rows (TZ=UTC)
                                                 versus the UTC start
  python3 p3_gpu_probes.py p5-gpu --out DIR      idle floor of the counter over 2 x 60 s with and without the nvidia-smi
                                                 poller, poller CPU overhead idle and under CPU load
  python3 p3_gpu_probes.py all --out DIR         semantics, p6 and g8 in this order (p5-gpu runs on its own, before any
                                                 CUDA context exists, and takes a CUDA context itself for its last windows)

Each step writes <step>.json (and the raw nvidia-smi streams as <step>-*.csv) into DIR.
"""
from __future__ import annotations

import argparse
import bisect
import calendar
import ctypes
import json
import math
import os
import random
import re
import statistics
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import power_telemetry  # noqa: E402

# gpu.csv as the runner writes it (run_experiment.GPU_TELEMETRY_QUERY, 200 ms); redefined here so the probe runs from a bare directory
GPU_CSV_QUERY = ("timestamp,index,utilization.gpu,utilization.memory,memory.used,power.draw,"
                 "temperature.gpu,clocks.sm,clocks.mem")
PULSE_LENGTHS_S = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0)
WINDOW_LENGTHS_S = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0)           # 10 s needs no window: n = 1 is the isolated pulse
WINDOW_TARGET_S, WINDOW_MAX_RUNS = 10.0, 50                      # design 2.1: n = clamp(ceil(10 s / t_hat), 1, 50)
SMI_TIMESTAMP = "%Y/%m/%d %H:%M:%S.%f"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---- spin kernel through the CUDA driver API ----------------------------------------------------------

def hex_float(value: float) -> str:
    """PTX single-precision literal, e.g. 1.0 -> 0f3F800000."""
    return "0f%08X" % struct.unpack("<I", struct.pack("<f", value))[0]


def ptx_source(unroll: int = 8, chains: int = 8) -> str:
    """A kernel that iterates the logistic map x <- r x (1 - x) (r = 3.9, chaotic) on `chains` independent accumulators per thread,
    as fused multiply-adds, until %globaltimer passes a deadline.

    The deadline is start + duration_ns, so the pulse length does not depend on the clock rate. A converging recurrence
    (x <- 0.999 x + 0.001) draws little power because its operands stop changing; the chaotic map keeps the data toggling.
    Accumulators start from distinct thread-dependent values in (0, 1) so no two chains are the same expression; the sum is
    stored only if it exceeds 8.5e37, which never happens, to keep the work live."""
    x = lambda k: f"%f{k}"                           # accumulators f1..f8
    u = lambda k: f"%f{12 + k}"                      # -r x, per chain
    nu = lambda k: f"%f{24 + k}"                     # r x, per chain
    body = []
    for _ in range(unroll):
        for k in range(1, chains + 1):
            body += [f"    mul.f32 {u(k)}, {x(k)}, %f10;", f"    neg.f32 {nu(k)}, {u(k)};",
                     f"    fma.rn.f32 {x(k)}, {u(k)}, {x(k)}, {nu(k)};"]
    start = []
    for k in range(1, chains + 1):
        start += [f"    fma.rn.f32 {u(k)}, %f9, %f11, {hex_float(0.1234 * k + 0.01)};", f"    cvt.rmi.f32.f32 {nu(k)}, {u(k)};",
                  f"    sub.f32 {x(k)}, {u(k)}, {nu(k)};"]
    total = [f"    add.f32 %f1, %f1, {x(k)};" for k in range(2, chains + 1)]
    return "\n".join([
        ".version 7.0", ".target sm_70", ".address_size 64", "",
        ".visible .entry spin(", "    .param .u64 spin_duration_ns,", "    .param .u64 spin_sink", ")", "{",
        "    .reg .pred %p<3>;", "    .reg .b64 %rd<8>;", "    .reg .u32 %r<3>;", "    .reg .f32 %f<40>;", "",
        "    ld.param.u64 %rd1, [spin_duration_ns];", "    ld.param.u64 %rd2, [spin_sink];",
        "    mov.u64 %rd3, %globaltimer;", "    add.u64 %rd4, %rd3, %rd1;",
        "    mov.u32 %r1, %tid.x;", "    cvt.rn.f32.u32 %f9, %r1;",
        f"    mov.f32 %f10, {hex_float(-3.9)};", f"    mov.f32 %f11, {hex_float(0.6180339)};", *start,
        "SPIN_LOOP:", *body,
        "    mov.u64 %rd5, %globaltimer;", "    setp.lt.u64 %p1, %rd5, %rd4;", "    @%p1 bra SPIN_LOOP;", *total,
        f"    setp.gt.f32 %p2, %f1, {hex_float(8.5e37)};", "    @%p2 st.global.f32 [%rd2], %f1;", "    ret;", "}", ""])


class CudaPulse:
    """Launches the spin kernel on device 0 and waits for it. The context stays alive between pulses, as in an engine."""
    CU_DEVICE_ATTRIBUTE_MULTIPROCESSOR_COUNT = 16

    def __init__(self, device: int = 0, threads: int = 512, blocks_per_sm: int = 4):
        self.lib = ctypes.CDLL("libcuda.so.1")
        lib = self.lib
        for name, argtypes in (("cuInit", [ctypes.c_uint]), ("cuDeviceGet", [ctypes.POINTER(ctypes.c_int), ctypes.c_int]),
                               ("cuDeviceGetAttribute", [ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_int]),
                               ("cuDevicePrimaryCtxRetain", [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]),
                               ("cuCtxSetCurrent", [ctypes.c_void_p]),
                               ("cuModuleLoadData", [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]),
                               ("cuModuleGetFunction", [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_char_p]),
                               ("cuMemAlloc_v2", [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t]),
                               ("cuLaunchKernel", [ctypes.c_void_p] + [ctypes.c_uint] * 6 + [ctypes.c_uint, ctypes.c_void_p,
                                                                                            ctypes.c_void_p, ctypes.c_void_p]),
                               ("cuCtxSynchronize", [])):
            function = getattr(lib, name)
            function.argtypes, function.restype = argtypes, ctypes.c_int
        self.check(lib.cuInit(0), "cuInit")
        dev = ctypes.c_int()
        self.check(lib.cuDeviceGet(ctypes.byref(dev), device), "cuDeviceGet")
        sms = ctypes.c_int()
        self.check(lib.cuDeviceGetAttribute(ctypes.byref(sms), self.CU_DEVICE_ATTRIBUTE_MULTIPROCESSOR_COUNT, dev.value),
                   "cuDeviceGetAttribute(SM count)")
        context = ctypes.c_void_p()
        self.check(lib.cuDevicePrimaryCtxRetain(ctypes.byref(context), dev.value), "cuDevicePrimaryCtxRetain")
        self.check(lib.cuCtxSetCurrent(context), "cuCtxSetCurrent")
        module, self.function = ctypes.c_void_p(), ctypes.c_void_p()
        self.check(lib.cuModuleLoadData(ctypes.byref(module), ptx_source().encode() + b"\0"), "cuModuleLoadData (PTX JIT)")
        self.check(lib.cuModuleGetFunction(ctypes.byref(self.function), module, b"spin"), "cuModuleGetFunction")
        self.sink = ctypes.c_uint64()
        self.check(lib.cuMemAlloc_v2(ctypes.byref(self.sink), 64), "cuMemAlloc")
        self.duration = ctypes.c_uint64()
        self.params = (ctypes.c_void_p * 2)(ctypes.addressof(self.duration), ctypes.addressof(self.sink))
        self.sms, self.threads, self.blocks = sms.value, threads, sms.value * blocks_per_sm

    def check(self, code: int, call: str) -> None:
        if code != 0:
            raise RuntimeError(f"{call} returned CUDA error {code}")

    def pulse(self, seconds: float) -> dict:
        """Run the kernel for `seconds` of GPU time; returns the host-side stamps around launch and completion."""
        self.duration.value = int(seconds * 1e9)
        utc_before, mono_before = time.time_ns(), time.monotonic_ns()
        self.check(self.lib.cuLaunchKernel(self.function, self.blocks, 1, 1, self.threads, 1, 1, 0, None, self.params, None),
                   "cuLaunchKernel")
        self.check(self.lib.cuCtxSynchronize(), "cuCtxSynchronize")
        return {"nominal_s": seconds, "launch_utc_ns": utc_before, "launch_mono_ns": mono_before,
                "done_utc_ns": time.time_ns(), "done_mono_ns": time.monotonic_ns(),
                "host_s": (time.monotonic_ns() - mono_before) / 1e9}


# ---- nvidia-smi streams --------------------------------------------------------------------------------

def smi_fields() -> list:
    """Every `--query-gpu` field this driver lists (empty when nvidia-smi cannot say)."""
    done = subprocess.run(["nvidia-smi", "--help-query-gpu"], capture_output=True, text=True, timeout=30)
    return re.findall(r'^"([a-z0-9_.]+)"', done.stdout, flags=re.MULTILINE)


class SmiStream:
    """`nvidia-smi --query-gpu=... -lms N` with TZ=UTC, written to `path`; stop() ends it and returns the CPU seconds it used."""

    def __init__(self, fields, interval_ms: int, path: Path, header: bool = False, device: int = 0):
        self.fields, self.path, self.interval_ms = list(fields), Path(path), interval_ms
        fmt = "csv" if header else "csv,noheader,nounits"
        self.handle = self.path.open("w")
        self.started_utc, self.started_mono = time.time_ns(), time.monotonic_ns()
        self.process = subprocess.Popen(["nvidia-smi", "-i", str(device), f"--query-gpu={','.join(self.fields)}",
                                         f"--format={fmt}", "-lms", str(interval_ms)], stdout=self.handle,
                                        stderr=subprocess.STDOUT, env={**os.environ, "TZ": "UTC"})

    def cpu_seconds(self) -> float:
        """utime + stime of the nvidia-smi process so far (from /proc), or nan when unreadable."""
        try:
            fields = Path(f"/proc/{self.process.pid}/stat").read_text().rsplit(")", 1)[1].split()
            return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
        except (OSError, IndexError, ValueError):
            return float("nan")

    def stop(self) -> float:
        cpu = self.cpu_seconds()
        self.stopped_utc = time.time_ns() / 1e9
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.handle.close()
        return cpu


def parse_number(text: str):
    """'71.06', '71.06 W', '1980 MHz' -> float; '[N/A]', '' -> None."""
    match = re.match(r"\s*(-?\d+(?:\.\d+)?)", text or "")
    return float(match.group(1)) if match else None


def parse_smi_csv(text: str, fields) -> dict:
    """{"t": [epoch seconds], <field>: [value or None]} from a stream whose first column is `timestamp` (UTC).

    Header lines (a --format=csv stream starts with one), blank lines and rows that do not parse are skipped."""
    out = {"t": [], **{f: [] for f in fields if f != "timestamp"}}
    names = [f for f in fields if f != "timestamp"]
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) != len(fields) or not re.match(r"\d{4}/\d\d/\d\d ", cells[0]):
            continue
        try:
            stamp = datetime.strptime(cells[0], SMI_TIMESTAMP)
        except ValueError:
            continue
        out["t"].append(calendar.timegm(stamp.timetuple()) + stamp.microsecond / 1e6)
        for name, cell in zip(names, cells[1:]):
            out[name].append(parse_number(cell))
    return out


# ---- analysis ------------------------------------------------------------------------------------------

def integrate(times, values, t0: float, t1: float) -> dict:
    """Trapezoid integral of a sampled signal over [t0, t1] with linear interpolation at the ends.

    {"joules", "samples", "max_gap_s"}; joules is None when the samples do not cover the window or too few lie in it.
    Missing values (None) are skipped as if the sample did not exist."""
    pairs = [(t, v) for t, v in zip(times, values) if v is not None]
    if len(pairs) < 2 or t1 <= t0 or pairs[0][0] > t0 or pairs[-1][0] < t1:
        return {"joules": None, "samples": 0, "max_gap_s": None}
    ts = [t for t, _ in pairs]

    def at(t):
        i = bisect.bisect_right(ts, t)
        if i >= len(ts):
            return pairs[-1][1]
        (ta, va), (tb, vb) = pairs[i - 1], pairs[i]
        return va if tb == ta else va + (vb - va) * (t - ta) / (tb - ta)
    inside = [(t, v) for t, v in pairs if t0 < t < t1]
    path = [(t0, at(t0))] + inside + [(t1, at(t1))]
    joules = sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(path, path[1:]))
    return {"joules": joules, "samples": len(inside), "max_gap_s": max(b[0] - a[0] for a, b in zip(path, path[1:]))}


def quantile(values, q: float):
    """Linear-interpolation quantile (numpy's default); None for an empty list."""
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    position = q * (len(data) - 1)
    low = math.floor(position)
    high = min(low + 1, len(data) - 1)
    return data[low] + (data[high] - data[low]) * (position - low)


def error_summary(records) -> dict:
    """Per pulse length: median and p90 of the signed and absolute relative error (counter - integral) / integral."""
    out = {}
    for length in sorted({r["length_s"] for r in records}):
        rows = [r for r in records if r["length_s"] == length and r.get("rel_error") is not None]
        errors, absolute = [r["rel_error"] for r in rows], [abs(r["rel_error"]) for r in rows]
        out[str(length)] = {
            "n": len(rows), "runs_per_window": rows[0].get("runs") if rows else None,
            "median_rel_error": quantile(errors, 0.5), "p90_rel_error": quantile(errors, 0.9),
            "median_abs_rel_error": quantile(absolute, 0.5), "p90_abs_rel_error": quantile(absolute, 0.9),
            "min_rel_error": min(errors) if errors else None, "max_rel_error": max(errors) if errors else None,
            "median_counter_j": quantile([r["counter_j"] for r in rows], 0.5),
            "median_integral_j": quantile([r["integral_j"] for r in rows], 0.5),
            "median_window_s": quantile([r["window_s"] for r in rows], 0.5)}
    return out


def t_min_candidates(summary: dict, limit: float = 0.02) -> dict:
    """Shortest pulse length whose median absolute error is <= `limit` and stays so at every longer length."""
    lengths = sorted(summary, key=float)
    ok = [summary[x]["median_abs_rel_error"] is not None and summary[x]["median_abs_rel_error"] <= limit for x in lengths]
    chosen = None
    for i, length in enumerate(lengths):
        if all(ok[i:]):
            chosen = float(length)
            break
    return {"limit": limit, "rule": "smallest length whose median |error| <= limit and every longer length also is",
            "t_min_s": chosen, "per_length_ok": dict(zip(lengths, ok))}


def correlation(a, b):
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx, sy = math.sqrt(sum((x - mx) ** 2 for x in xs)), math.sqrt(sum((y - my) ** 2 for y in ys))
    return None if sx == 0 or sy == 0 else sum((x - mx) * (y - my) for x, y in pairs) / (sx * sy)


def equality_fraction(a, b, tolerance: float = 0.006):
    """Share of rows where both values exist and agree within the 0.01 W print resolution (None if no row has both)."""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    return sum(abs(x - y) <= tolerance for x, y in pairs) / len(pairs) if pairs else None


def classify_draw(fraction_average, fraction_instant, threshold: float = 0.95) -> str:
    if fraction_average is not None and fraction_average >= threshold:
        return "power.draw == power.draw.average (trailing average)"
    if fraction_instant is not None and fraction_instant >= threshold:
        return "power.draw == power.draw.instant (instantaneous)"
    return "power.draw matches neither field in >= 95% of rows"


def onset_lags(times, util, starts, threshold: float = 10.0, before: float = 1.0, after: float = 3.0):
    """For each pulse start (epoch s): the first sample with utilization >= threshold in [start - before, start + after]
    minus the start (None if there is none). A negative lag would mean a row stamped before the pulse began."""
    lags = []
    for start in starts:
        hit = next((t for t, u in zip(times, util) if u is not None and start - before <= t <= start + after and u >= threshold), None)
        lags.append(None if hit is None else hit - start)
    return lags


def thirds_drift(lags):
    """Median lag of the first third minus that of the last third (None when there are fewer than 6 usable lags)."""
    usable = [x for x in lags if x is not None]
    if len(usable) < 6:
        return None
    third = len(usable) // 3
    return statistics.median(usable[:third]) - statistics.median(usable[-third:])


# ---- shared measurement helpers --------------------------------------------------------------------------

def read_counter(reader) -> dict:
    """One counter read with the UTC clock around it (the NVML bracket is monotonic; the integral needs UTC)."""
    utc_before = time.time_ns()
    sample = reader.read()
    utc_after = time.time_ns()
    return {"mj": sample["mj"], "utc_s": (utc_before + utc_after) / 2e9, "latency_ms": (utc_after - utc_before) / 1e6,
            "error": sample.get("error")}


def run_command(command, timeout=30) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env={**os.environ, "TZ": "UTC"})
        return (done.stdout + done.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{type(exc).__name__}: {exc}"


def host_meta(reader, pulse=None) -> dict:
    query = "name,driver_version,power.limit,power.default_limit,persistence_mode,pstate,temperature.gpu,clocks.sm,clocks.mem"
    return {"utc": utc_stamp(), "nvidia_smi": run_command(["nvidia-smi", f"--query-gpu={query}", "--format=csv"]),
            "counter": reader.describe(), "chrony": run_command(["chronyc", "tracking"]),
            "pulse_kernel": {"sms": pulse.sms, "blocks": pulse.blocks, "threads_per_block": pulse.threads} if pulse else None}


def write_result(out: Path, step: str, result: dict) -> None:
    (out / f"{step}.json").write_text(json.dumps(result, indent=2, default=str) + "\n")


def jitter(low: float, spread: float = 0.1) -> float:
    return low + random.random() * spread           # random phase against the counter's ~100 ms ticks


def measure_pulse(reader, pulse, seconds: float) -> dict:
    """Counter read, pulse, counter read: the study's per-run bracket (reads just outside the timed region)."""
    start = read_counter(reader)
    stamps = pulse.pulse(seconds)
    end = read_counter(reader)
    return {"start": start, "end": end, "pulse": stamps}


def score(read_pair, stream, field: str):
    """Counter delta and the integral of `field` over the same read window; (counter_j, integral dict, window_s)."""
    start, end = read_pair
    window = end["utc_s"] - start["utc_s"]
    counter = (end["mj"] - start["mj"]) / 1000.0 if None not in (start["mj"], end["mj"]) else None
    return counter, integrate(stream["t"], stream[field], start["utc_s"], end["utc_s"]), window


# ---- steps -------------------------------------------------------------------------------------------------

def p6_reference_field(choice: str, listed) -> str:
    """The p6 reference field: an explicit choice wins (nvidia-smi is not consulted); "auto" takes GPU-only
    power.draw.instant when the driver lists it, else power.draw. `listed` is a callable returning the listed fields."""
    if choice != "auto":
        return choice
    return "power.draw.instant" if "power.draw.instant" in listed() else "power.draw"


def step_p6(args, out: Path, reader, pulse) -> dict:
    """Counter versus the 20 ms integral of a sampled power field on pulses of known length.

    The reference must have the counter's scope (the semantics step measures it): GPU-only power.draw.instant by
    default, but module.power.draw.instant on Grace Hopper, whose counter covers the whole module (CPU + GPU +
    memory; 2026-11-15 GH200 probe: counter / module integral 0.998, counter / GPU-only integral 1.14-1.55)."""
    field = p6_reference_field(getattr(args, "p6_field", "auto"), smi_fields)
    stream_path = out / "p6-power-20ms.csv"
    rng = random.Random(args.seed)
    isolated, windows = [], []
    stream = SmiStream(["timestamp", field], 20, stream_path)
    time.sleep(3)
    for _ in range(2):
        pulse.pulse(0.5)                            # warm-up: PTX JIT, clock ramp
    time.sleep(5)
    began = utc_stamp()
    for repeat in range(args.repeats):
        order = list(PULSE_LENGTHS_S)
        rng.shuffle(order)
        for length in order:
            time.sleep(jitter(3.0 if length < 5 else 6.0))
            run = measure_pulse(reader, pulse, length)
            isolated.append({"repeat": repeat, "length_s": length, "runs": 1, **run})
    for repeat in range(args.window_repeats):
        order = list(WINDOW_LENGTHS_S)
        rng.shuffle(order)
        for length in order:
            runs = max(1, min(WINDOW_MAX_RUNS, math.ceil(WINDOW_TARGET_S / length)))
            time.sleep(jitter(5.0))
            pairs = []
            for _ in range(runs):
                run = measure_pulse(reader, pulse, length)
                pairs.append(run)
                time.sleep(jitter(0.2))             # the gap between runs of a window (cleanup in the study)
            windows.append({"repeat": repeat, "length_s": length, "runs": runs, "pairs": pairs})
    time.sleep(3)
    stream_cpu = stream.stop()
    parsed = parse_smi_csv(stream_path.read_text(), ["timestamp", field])
    spacing = [b - a for a, b in zip(parsed["t"], parsed["t"][1:])]
    for record in isolated:
        counter, integral, window = score((record["start"], record["end"]), parsed, field)
        record.update(counter_j=counter, integral_j=integral["joules"], integral_samples=integral["samples"],
                      integral_max_gap_s=integral["max_gap_s"], window_s=window,
                      rel_error=None if not integral["joules"] or counter is None else (counter - integral["joules"]) / integral["joules"])
    for record in windows:
        counter_sum = integral_sum = window_sum = 0.0
        valid = True
        for run in record["pairs"]:
            counter, integral, window = score((run["start"], run["end"]), parsed, field)
            if counter is None or integral["joules"] is None:
                valid = False
                break
            counter_sum, integral_sum, window_sum = counter_sum + counter, integral_sum + integral["joules"], window_sum + window
        record.update(counter_j=counter_sum if valid else None, integral_j=integral_sum if valid else None,
                      window_s=window_sum, rel_error=(counter_sum - integral_sum) / integral_sum if valid and integral_sum else None)
        record["per_run_s"] = record["length_s"]
    isolated_summary, window_summary = error_summary(isolated), error_summary(windows)
    for record in isolated + windows:               # keep the raw per-run values small enough to commit
        if "pairs" in record:
            record["pairs"] = [{"start_mj": p["start"]["mj"], "end_mj": p["end"]["mj"], "start_utc_s": p["start"]["utc_s"],
                                "end_utc_s": p["end"]["utc_s"], "host_s": p["pulse"]["host_s"]} for p in record["pairs"]]
        else:
            record["start_mj"], record["end_mj"] = record["start"]["mj"], record["end"]["mj"]
            record["start_utc_s"], record["end_utc_s"] = record["start"]["utc_s"], record["end"]["utc_s"]
            record["host_pulse_s"] = record["pulse"]["host_s"]
            record["read_latency_ms"] = [record["start"]["latency_ms"], record["end"]["latency_ms"]]
            del record["start"], record["end"], record["pulse"]
    return {"step": "p6", "reference_field": field, "started_utc": began, "ended_utc": utc_stamp(),
            "stream": {"interval_ms": 20, "rows": len(parsed["t"]), "median_spacing_ms": 1000 * statistics.median(spacing),
                       "p90_spacing_ms": 1000 * quantile(spacing, 0.9), "max_spacing_ms": 1000 * max(spacing),
                       "poller_cpu_seconds": stream_cpu, "last_row_before_stop_s": stream.stopped_utc - parsed["t"][-1]},
            "definition": "rel_error = (NVML counter delta - integral of the reference field) / integral, both over [start read, end read] "
                          "(UTC midpoints of the two counter reads); isolated: one pulse per window; window: sum over the runs of one "
                          "window (n = clamp(ceil(10 s / length), 1, 50), gaps between runs excluded, as design 2.1)",
            "isolated": {"repeats": args.repeats, "summary": isolated_summary, "t_min": t_min_candidates(isolated_summary),
                         "records": isolated},
            "windows": {"repeats": args.window_repeats, "summary": window_summary, "t_min": t_min_candidates(window_summary),
                        "records": windows}}


def step_semantics(args, out: Path, reader, pulse) -> dict:
    """power.draw versus .average versus .instant (and module.*) at 50 ms under pulses; counter scope at 20 ms under steady load."""
    listed = smi_fields()
    power_fields = [f for f in listed if "power" in f and ("draw" in f)]
    wanted = [f for f in ("power.draw", "power.draw.average", "power.draw.instant") if f in listed] + \
             [f for f in listed if f.startswith("module.power.draw") or f.startswith("memory.power.draw")]
    for _ in range(2):
        pulse.pulse(0.5)
    path = out / "semantics-50ms.csv"
    stream = SmiStream(["timestamp", *wanted], 50, path)
    time.sleep(3)
    start = read_counter(reader)
    for _ in range(args.pulses):
        pulse.pulse(0.3)
        time.sleep(0.7)
    end = read_counter(reader)
    time.sleep(1)
    stream.stop()
    parsed = parse_smi_csv(path.read_text(), ["timestamp", *wanted])
    window = [i for i, t in enumerate(parsed["t"]) if start["utc_s"] <= t <= end["utc_s"]]
    column = {f: [parsed[f][i] for i in window] for f in wanted}
    report = {"samples_in_window": len(window), "window_s": end["utc_s"] - start["utc_s"], "fields": {}}
    for f in wanted:
        values = [v for v in column[f] if v is not None]
        report["fields"][f] = {"n": len(values), "min": min(values, default=None), "max": max(values, default=None),
                               "mean": statistics.fmean(values) if values else None}
    draw = column.get("power.draw", [])
    average, instant = column.get("power.draw.average", []), column.get("power.draw.instant", [])
    report["draw_equals_average"] = equality_fraction(draw, average)
    report["draw_equals_instant"] = equality_fraction(draw, instant)
    report["corr_draw_average"], report["corr_draw_instant"] = correlation(draw, average), correlation(draw, instant)
    report["verdict"] = classify_draw(report["draw_equals_average"], report["draw_equals_instant"])
    # counter scope: steady load plus idle at 20 ms so the integral error is small; compare with every instantaneous field
    scope_fields = [f for f in wanted if f.endswith("instant")] or [f for f in wanted if f == "power.draw"]
    scope_path = out / "semantics-scope-20ms.csv"
    stream = SmiStream(["timestamp", *scope_fields], 20, scope_path)
    time.sleep(3)
    idle_start = read_counter(reader)
    time.sleep(10)
    idle_end = read_counter(reader)
    load_start = read_counter(reader)
    pulse.pulse(20.0)
    load_end = read_counter(reader)
    time.sleep(3)
    stream.stop()
    scope = parse_smi_csv(scope_path.read_text(), ["timestamp", *scope_fields])
    report["counter_scope"] = {}
    for label, (a, b) in (("idle_10s", (idle_start, idle_end)), ("load_20s", (load_start, load_end))):
        counter = (b["mj"] - a["mj"]) / 1000.0
        report["counter_scope"][label] = {"counter_j": counter, "window_s": b["utc_s"] - a["utc_s"], "counter_w": counter / (b["utc_s"] - a["utc_s"])}
        for f in scope_fields:
            joules = integrate(scope["t"], scope[f], a["utc_s"], b["utc_s"])["joules"]
            report["counter_scope"][label][f] = {"integral_j": joules, "counter_over_integral": counter / joules if joules else None}
    pulsed_counter = (end["mj"] - start["mj"]) / 1000.0
    report["pulsed_window"] = {"counter_j": pulsed_counter,
                               **{f: {"integral_j": integrate(parsed["t"], parsed[f], start["utc_s"], end["utc_s"])["joules"]} for f in wanted}}
    return {"step": "semantics", "fields_listed_with_power": power_fields, "fields_queried": wanted, "pulses": args.pulses,
            "nvidia_smi_q_power": redact_smi_q(run_command(["nvidia-smi", "-q", "-d", "POWER"])), **report}


def redact_smi_q(text: str) -> str:
    """Drop the lines that identify a board (`GPU 0000:..` header, UUID, serial, bus id)."""
    keep = [line for line in text.splitlines() if not re.search(r"^GPU\s+[0-9A-Fa-f]{4,8}:|UUID|Serial|Bus Id|PDI|Board ID", line)]
    return "\n".join(keep)


def step_g8(args, out: Path, reader, pulse) -> dict:
    """3 s pulses against a gpu.csv-style 200 ms stream: onset lag of utilization.gpu >= 10% versus the UTC launch stamp."""
    fields = GPU_CSV_QUERY.split(",")
    path = out / "g8-gpu.csv"
    chrony_before = run_command(["chronyc", "tracking"])
    for _ in range(2):
        pulse.pulse(0.5)
    stream = SmiStream(fields, 200, path, header=True)
    anchor_before = power_telemetry.clock_anchor()
    time.sleep(3)
    starts = []
    for _ in range(args.g8_pulses):
        time.sleep(jitter(3.0, 0.4))                # random phase against the 200 ms sampling
        stamps = pulse.pulse(3.0)
        starts.append({"launch_utc_s": stamps["launch_utc_ns"] / 1e9, "launch_mono_ns": stamps["launch_mono_ns"],
                       "done_utc_s": stamps["done_utc_ns"] / 1e9, "host_s": stamps["host_s"]})
    time.sleep(2)
    stream.stop()
    parsed = parse_smi_csv(path.read_text(), fields)
    lags = onset_lags(parsed["t"], parsed["utilization.gpu"], [s["launch_utc_s"] for s in starts])
    usable = [x for x in lags if x is not None]
    spacing = [b - a for a, b in zip(parsed["t"], parsed["t"][1:])]
    drift = thirds_drift(lags)
    return {"step": "g8", "command": f"TZ=UTC nvidia-smi --query-gpu={GPU_CSV_QUERY} --format=csv -lms 200", "pulses": len(starts),
            "pulse_s": 3.0, "rows": len(parsed["t"]), "median_spacing_ms": 1000 * statistics.median(spacing),
            "lags_s": lags, "median_lag_s": statistics.median(usable) if usable else None,
            "min_lag_s": min(usable, default=None), "max_lag_s": max(usable, default=None),
            "p90_lag_s": quantile(usable, 0.9), "no_onset_found": sum(x is None for x in lags),
            "drift_first_minus_last_third_s": drift,
            "g8_thresholds": {"median_lag_within_s": 0.4, "drift_max_s": 0.2},
            "pass": bool(usable) and abs(statistics.median(usable)) <= 0.4 and (drift is None or abs(drift) <= 0.2),
            "anchor_before_stream_launch": anchor_before, "anchor_after": power_telemetry.clock_anchor(),
            "chrony_before": chrony_before, "chrony_after": run_command(["chronyc", "tracking"]),
            "pulse_starts": starts}


def burn(count: int):
    code = "while True: pass"
    return [subprocess.Popen([sys.executable, "-c", code]) for _ in range(count)]


def stop_burn(processes) -> None:
    for process in processes:
        process.terminate()
    for process in processes:
        process.wait()


def step_p5_gpu(args, out: Path, reader, pulse) -> dict:
    """Idle floor of the counter: 2 x 60 s with the nvidia-smi poller (gpu.csv config) and 2 x 60 s without, interleaved; then
    60 s under CPU load. Poller CPU use from /proc. The GPU is idle throughout (no pulse), but the CUDA context is not held."""
    windows = []
    fields = GPU_CSV_QUERY.split(",")

    def window(label: str, poller: bool, load: int = 0, seconds: float = 60.0) -> dict:
        stream = SmiStream(fields, 200, out / f"p5-gpu-{label}.csv", header=True) if poller else None
        burners = burn(load) if load else []
        process_before, wall_before = time.process_time(), time.monotonic()
        cpu_before = stream.cpu_seconds() if stream else None
        start = read_counter(reader)
        time.sleep(seconds)
        end = read_counter(reader)
        cpu_after = stream.cpu_seconds() if stream else None
        stop_burn(burners)
        if stream:
            stream.stop()
        spent = (end["utc_s"] - start["utc_s"])
        record = {"label": label, "poller": poller, "cpu_burners": load, "window_s": spent,
                  "counter_j": (end["mj"] - start["mj"]) / 1000.0, "counter_w": (end["mj"] - start["mj"]) / 1000.0 / spent,
                  "poller_cpu_fraction_of_one_core": (cpu_after - cpu_before) / spent if stream else None,
                  "probe_process_cpu_fraction": (time.process_time() - process_before) / (time.monotonic() - wall_before),
                  "smi": run_command(["nvidia-smi", "--query-gpu=temperature.gpu,clocks.sm,clocks.mem,pstate,power.draw,persistence_mode,"
                                      "clocks_throttle_reasons.active", "--format=csv"])}
        if stream:
            parsed = parse_smi_csv((out / f"p5-gpu-{label}.csv").read_text(), fields)
            watts = [w for w in parsed["power.draw"] if w is not None]
            record["power_draw_w"] = {"mean": statistics.fmean(watts), "min": min(watts), "max": max(watts)} if watts else None
        return record

    time.sleep(args.settle)                          # >= 60 s without load, as the design's idle brackets
    for index, poller in enumerate((True, False, True, False)):
        windows.append(window(f"idle-{'poller' if poller else 'bare'}-{index // 2 + 1}", poller))
    context_error = None
    try:                                             # a GPU engine holds a CUDA context while it is "ready": idle floor with one
        holder = pulse or CudaPulse()
        holder.pulse(0.2)
        time.sleep(60)
        for index in range(2):
            windows.append(window(f"idle-context-poller-{index + 1}", True))
    except Exception as exc:
        context_error = f"{type(exc).__name__}: {exc}"
    cpus = os.cpu_count() or 1
    windows.append(window("cpu-load-poller", True, load=min(26, cpus)))
    windows.append(window("cpu-load-bare", False, load=min(26, cpus)))
    host_sampler = host_sampler_overhead(30.0)
    idle_poller = [w["counter_w"] for w in windows if w["label"].startswith("idle-poller")]
    idle_bare = [w["counter_w"] for w in windows if w["label"].startswith("idle-bare")]
    return {"step": "p5-gpu", "windows": windows, "host_sampler": host_sampler, "context_windows_error": context_error,
            "idle_floor_w": {"poller": idle_poller, "bare": idle_bare,
                             "poller_pair_relative_difference": abs(idle_poller[0] - idle_poller[1]) / statistics.fmean(idle_poller),
                             "bare_pair_relative_difference": abs(idle_bare[0] - idle_bare[1]) / statistics.fmean(idle_bare),
                             "poller_minus_bare_w": statistics.fmean(idle_poller) - statistics.fmean(idle_bare),
                             "g10_threshold": 0.10},
            "counter_read": {"latency_ms_median": reader_latency_ms(reader)}}


def reader_latency_ms(reader, reads: int = 200) -> float:
    return statistics.median(read_counter(reader)["latency_ms"] for _ in range(reads))


def host_sampler_overhead(seconds: float) -> dict:
    """CPU use of the runner's host.csv thread (psutil, 1 Hz), measured in this process; psutil missing is recorded."""
    try:
        import psutil
    except ImportError:
        return {"psutil": False, "note": "psutil not installed on this host: host.csv sampler overhead not measured here"}
    before_cpu, before_wall, rows = time.process_time(), time.monotonic(), 0
    while time.monotonic() - before_wall < seconds:
        memory = psutil.virtual_memory()
        psutil.cpu_percent(), psutil.swap_memory().used, getattr(memory, "cached", 0)
        rows += 1
        time.sleep(1)
    return {"psutil": psutil.__version__, "rows": rows, "cpu_fraction_of_one_core": (time.process_time() - before_cpu) / (time.monotonic() - before_wall)}


STEPS = {"semantics": step_semantics, "p6": step_p6, "g8": step_g8, "p5-gpu": step_p5_gpu}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=[*STEPS, "all"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=8, help="p6: repeats per isolated pulse length")
    parser.add_argument("--window-repeats", type=int, default=5, help="p6: repeats per window length")
    parser.add_argument("--pulses", type=int, default=20, help="semantics: 0.3 s on / 0.7 s off pulses")
    parser.add_argument("--g8-pulses", type=int, default=20)
    parser.add_argument("--settle", type=float, default=60.0, help="p5-gpu: idle seconds before the first window")
    parser.add_argument("--seed", type=int, default=20261111)
    parser.add_argument("--p6-field", default="auto", choices=["auto", "power.draw.instant", "module.power.draw.instant"],
                        help="p6: reference field; must match the counter scope found by the semantics step")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    reader = power_telemetry.GpuEnergyReader(0)
    if reader.source == power_telemetry.GPU_ENERGY_UNAVAILABLE:
        print("no GPU energy counter: " + "; ".join(reader.reasons), file=sys.stderr)
        return 3
    steps = [s for s in STEPS if s != "p5-gpu"] if args.step == "all" else [args.step]   # p5-gpu first, alone: no CUDA context
    pulse = None
    if any(s != "p5-gpu" for s in steps):
        pulse = CudaPulse()
        print(f"spin kernel loaded: {pulse.sms} SMs, grid {pulse.blocks} x {pulse.threads}", file=sys.stderr)
    failed = 0
    for step in steps:
        print(f"[{utc_stamp()}] {step} ...", file=sys.stderr, flush=True)
        try:
            result = STEPS[step](args, args.out, reader, pulse)
            result["meta"] = host_meta(reader, pulse)
        except Exception as exc:                    # one failed step must not lose the others
            failed += 1
            result = {"step": step, "error": f"{type(exc).__name__}: {exc}"}
            print(f"{step} failed: {result['error']}", file=sys.stderr)
        write_result(args.out, step, result)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
