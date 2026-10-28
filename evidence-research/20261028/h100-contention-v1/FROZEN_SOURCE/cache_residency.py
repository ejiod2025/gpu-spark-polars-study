#!/usr/bin/env python3
"""Read Linux file page-cache residency without reading or faulting file contents.

mincore is an instantaneous observation, not a cache-control mechanism. Other
processes and reclaim can change residency immediately after it is observed.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
import mmap
import os
from pathlib import Path
import platform
import time


def residency(path: Path) -> dict:
    if platform.system() != "Linux":
        raise OSError("mincore file residency requires Linux")
    size = path.stat().st_size
    pages = (size + mmap.PAGESIZE - 1) // mmap.PAGESIZE
    if not pages:
        return {"bytes": size, "pages": 0, "resident_pages": 0}
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mincore.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_ubyte))
    libc.mincore.restype = ctypes.c_int
    with path.open("rb") as handle:
        # ACCESS_COPY permits from_buffer to expose the address; no writes are made.
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_COPY) as mapped:
            view = (ctypes.c_char * 1).from_buffer(mapped)
            address = ctypes.addressof(view)
            vector = (ctypes.c_ubyte * pages)()
            try:
                if libc.mincore(address, size, vector) != 0:
                    error = ctypes.get_errno()
                    raise OSError(error, os.strerror(error), str(path))
                resident = sum(value & 1 for value in vector)
            finally:
                del view
    return {"bytes": size, "pages": pages, "resident_pages": resident}


def snapshot(data: Path) -> dict:
    started = time.perf_counter()
    out = {"observed_at_utc": datetime.now(timezone.utc).isoformat(),
           "method": "Linux mincore; private mappings; no data reads or writes",
           "scope": "all Parquet files in this scale; instant snapshot, not guaranteed residency",
           "files": {}, "errors": []}
    for path in sorted(data.glob("*/*.parquet")):
        try:
            out["files"][str(path.relative_to(data))] = residency(path)
        except OSError as exc:
            out["errors"].append({"path": str(path.relative_to(data)), "error": str(exc)})
    out["bytes"] = sum(row["bytes"] for row in out["files"].values())
    out["pages"] = sum(row["pages"] for row in out["files"].values())
    out["resident_pages"] = sum(row["resident_pages"] for row in out["files"].values())
    out["resident_fraction"] = out["resident_pages"] / out["pages"] if out["pages"] else None
    out["seconds"] = time.perf_counter() - started
    return out


def host_snapshot() -> dict:
    result = {"observed_at_utc": datetime.now(timezone.utc).isoformat(),
              "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None}
    for name in ("meminfo", "vmstat", "diskstats", "loadavg", "uptime"):
        path = Path("/proc") / name
        result[name] = path.read_text() if path.exists() else None
    root = Path("/sys/devices/system/cpu")
    result["cpu_frequency_policy"] = {
        str(path.relative_to(root)): path.read_text().strip()
        for pattern in ("cpu*/cpufreq/scaling_governor", "cpu*/cpufreq/scaling_cur_freq",
                        "cpu*/cpufreq/scaling_min_freq", "cpu*/cpufreq/scaling_max_freq")
        for path in sorted(root.glob(pattern))
    }
    result["transparent_hugepage"] = None
    thp = Path("/sys/kernel/mm/transparent_hugepage/enabled")
    if thp.exists():
        result["transparent_hugepage"] = thp.read_text().strip()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    args = parser.parse_args()
    print(json.dumps({"cache": snapshot(args.data), "host": host_snapshot()}, indent=2))
