#!/usr/bin/env python3
"""A short synthetic GPU load pulse in its own process, for the clock-alignment gate G8 (docs/PAPER3_ENERGY_DESIGN.md section 4).

run_experiment.py runs it as a child before a session's worker is launched and after it has exited, for sessions that have no
GPU-engine window of their own (CPU engines on a GPU host). The pulse puts a known step into utilization.gpu, so the first
200 ms nvidia-smi sample at or above 10% can be compared with the UTC and CLOCK_MONOTONIC stamps printed here.

The spin kernel is the one of tools/paper3_probes/p3_gpu_probes.py (CudaPulse: PTX through the CUDA driver API, no toolkit),
validated on H100, A100 and GH200; it is imported, not copied. No study query, no data: the load is arithmetic on registers.

  python3 gpu_pulse.py --seconds 3 --device 0 --label start      # prints one JSON object; exit 0 when the pulse ran, 1 otherwise
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
PROBES = HERE / "tools" / "paper3_probes" / "p3_gpu_probes.py"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def load_cuda_pulse():
    """The CudaPulse class of the probe tool, imported from its file (the tool lives in a directory that is not a package)."""
    spec = importlib.util.spec_from_file_location("p3_gpu_probes", PROBES)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CudaPulse


def utc_from_ns(nanoseconds: int) -> str:
    """ISO-8601 UTC with microseconds from a time.time_ns() value (the format of every other stamp)."""
    return (EPOCH + timedelta(microseconds=nanoseconds // 1000)).isoformat(timespec="microseconds")


def run_pulse(seconds: float, device: int = 0, label: str = "", factory=None) -> dict:
    """Create the CUDA context, run the kernel for `seconds` of GPU time and describe it; never raises.

    factory(device) returns an object whose pulse(seconds) gives the probe tool's dict (nominal_s, launch_utc_ns, launch_mono_ns,
    done_utc_ns, done_mono_ns, host_s); it is CudaPulse unless a test passes a fake. init_seconds is the context creation and
    PTX JIT before the pulse, which is not part of it."""
    record = {"ok": False, "label": label, "device": device, "requested_seconds": seconds,
              "process_start_utc": utc_from_ns(time.time_ns()), "process_start_monotonic_ns": time.monotonic_ns()}
    try:
        started = time.monotonic_ns()
        pulse = (factory or load_cuda_pulse())(device)
        record["init_seconds"] = (time.monotonic_ns() - started) / 1e9
        stamps = pulse.pulse(seconds)
        record.update(ok=True, nominal_seconds=stamps["nominal_s"], host_seconds=stamps["host_s"],
                      launch_utc=utc_from_ns(stamps["launch_utc_ns"]), launch_monotonic_ns=stamps["launch_mono_ns"],
                      done_utc=utc_from_ns(stamps["done_utc_ns"]), done_monotonic_ns=stamps["done_mono_ns"])
    except Exception as exc:   # no libcuda, no device, a driver error: reported to the runner, which records it
        record["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return record


def main(argv=None, factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=3.0, help="GPU time of the pulse (default 3, the design's G8 pulse)")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--label", default="", help="start or end: copied into the printed record")
    args = parser.parse_args(argv)
    if not 0 < args.seconds <= 60:
        parser.error("--seconds must be above 0 and at most 60")
    record = run_pulse(args.seconds, args.device, args.label, factory)
    print(json.dumps(record))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
