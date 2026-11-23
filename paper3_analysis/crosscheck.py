"""The S1-sampled cross-check of design 2.3: the trapezoid integral of the 200 ms nvidia-smi stream (gpu.csv) against the counter, per measured window.

Diagnostic, not a gate and never an energy value of the study: on windows whose summed run time is 10 s or more only, in sessions whose G7 (sample coverage)
and G8 (clock alignment) flags are clean (a session that fails G8 has its gpu.csv values disabled). The integral is taken over each run's own timed span
(the counter does not count the gaps between runs) with the stream interpolated linearly at both ends, summed over the window, and compared with the window's
counter difference: relative difference = (trapezoid - counter) / counter. Field: the instantaneous power of the counter's scope (module.power.draw.instant on
the GH200, power.draw.instant on the board hosts), else power.draw, whichever the stream has values for.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right

import paper3_gates as pg

WINDOW_MIN_S = 10.0


def power_field(gpu: dict, scope: str):
    """The stream column to integrate, or None: the counter's scope field first, then the plain power.draw."""
    order = (["module.power.draw.instant"] if scope == "module" else []) + ["power.draw.instant", "power.draw"]
    for name in order:
        column = gpu["columns"].get(name)
        if column and any(v is not None for v in column):
            return name
    return None


def trapezoid_j(times: list, watts: list, start: float, end: float):
    """Joules between two epoch instants from a sampled power series (watts may hold None), linear at the ends; None if the stream does not cover them."""
    if not times or end <= start or start < times[0] or end > times[-1]:
        return None
    low, high = max(bisect_right(times, start) - 1, 0), min(bisect_left(times, end) + 1, len(times) - 1)
    points = [(times[i], watts[i]) for i in range(low, high + 1)]
    if any(w is None for _, w in points):
        return None

    def at(instant):
        for (t0, w0), (t1, w1) in zip(points, points[1:]):
            if t0 <= instant <= t1:
                return w0 if t1 == t0 else w0 + (w1 - w0) * (instant - t0) / (t1 - t0)
        return None
    inner = [(start, at(start))] + [(t, w) for t, w in points if start < t < end] + [(end, at(end))]
    if any(w is None for _, w in inner):
        return None
    return sum((t1 - t0) * (w0 + w1) / 2.0 for (t0, w0), (t1, w1) in zip(inner, inner[1:]))


def session_windows(results: dict, gpu: dict, scope: str):
    """({relative differences per eligible window}, field used): one value per measured window of >= 10 s with a valid counter difference and full stream coverage."""
    field = power_field(gpu, scope)
    if field is None:
        return [], None
    times, watts = gpu["t"], gpu["columns"][field]
    out = []
    for record in results.get("records") or []:
        block = record.get("energy_window") if isinstance(record, dict) and record.get("phase") == "measured" else None
        if not isinstance(block, dict) or not (block.get("window_seconds") or 0) >= WINDOW_MIN_S:
            continue
        counter_mj = (block.get("gpu_energy") or {}).get("window_delta_mj")
        if not isinstance(counter_mj, (int, float)) or counter_mj <= 0:
            continue
        parts = []
        for run in block.get("runs") or []:
            start, end = pg.epoch(run.get("timed_start_utc")), pg.epoch(run.get("timed_end_utc"))
            parts.append(None if start is None or end is None else trapezoid_j(times, watts, start, end))
        if parts and all(p is not None for p in parts):
            out.append((sum(parts) - counter_mj / 1000.0) / (counter_mj / 1000.0))
    return out, field
