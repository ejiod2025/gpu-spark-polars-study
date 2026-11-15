#!/usr/bin/env python3
"""Paper 3 pre-lock CPU-side probes for the bare-metal host (P5 and the NUMA/RAPL checks of P3's follow-up). Synthetic
load only (spin loops, an allocating process); no study query. Standard library only; power_telemetry.py must sit next
to this file.

  p3_cpu_probes.py numa-bind --out FILE     the runner's binding (resolve_pinning, plan_membind, apply_membind, apply_pinning,
                                            in that order, as run_experiment.py does) in a child, then an allocating
                                            grandchild; its pages per NUMA node from /proc/<pid>/numa_maps. Scenarios: the 26-vCPU
                                            pin, --socket 0, a run on socket 1 bound to socket 0 (binding must win over first
                                            touch), and the same without binding (control).
  p3_cpu_probes.py rapl-perf --out FILE     powercap per package/dram zone against `perf stat --per-socket` (and `-a`), idle and
                                            with spin loops on socket 0 only. Root.
  p3_cpu_probes.py power-csv CSV [CSV ...]  watts per series of power.csv files written by power_telemetry.py (mean, 1 s spread,
                                            relative difference between files)
  p3_cpu_probes.py burn --cpus LIST --seconds N   one pinned spin loop per listed CPU
  p3_cpu_probes.py p6-cpu --out DIR         the RAPL analogue of P6, for t_min on the bare-metal host: the campaign's own 200 ms
                                            sampler (power.csv) against direct energy_uj reads, on synthetic pulses of known
                                            length pinned to the campaign's 26 vCPUs. Root, or setup_h100.sh --enable-rapl-read.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[2]))    # run from the repo tree: power_telemetry.py is at its root
import power_telemetry as pt  # noqa: E402
import p3_gpu_probes as gpu_probes  # noqa: E402  (shared analysis helpers: quantile, error_summary, t_min_candidates, pulse lengths)

POWERCAP = Path("/sys/class/powercap")


def run(command, timeout=300, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, **kwargs)


# ---- NUMA binding ----------------------------------------------------------------------------------------

def numa_maps_pages(pid, path=None) -> dict:
    """Pages per NUMA node of a process from /proc/<pid>/numa_maps: {"nodes": {node: pages}, "mib": {node: MiB}, "policies": [...]}.

    Each mapping line lists N<node>=<pages>; kernelpagesize_kB gives the unit (THP and hugetlb pages are counted in it)."""
    pages, mib, policies = {}, {}, set()
    try:
        text = Path(path or f"/proc/{pid}/numa_maps").read_text()
    except OSError as exc:                                       # a kernel without NUMA support has no numa_maps
        return {"error": f"{type(exc).__name__}: {exc}", "pages": {}, "mib": {}, "policies": []}
    for line in text.splitlines():
        words = line.split()
        if len(words) < 2:
            continue
        policies.add(words[1])
        size = next((int(w.split("=")[1]) for w in words if w.startswith("kernelpagesize_kB=")), 4)
        for word in words[2:]:
            match = re.fullmatch(r"N(\d+)=(\d+)", word)
            if match:
                node, count = int(match.group(1)), int(match.group(2))
                pages[node] = pages.get(node, 0) + count
                mib[node] = mib.get(node, 0.0) + count * size / 1024.0
    return {"pages": {str(k): v for k, v in sorted(pages.items())}, "mib": {str(k): round(v, 1) for k, v in sorted(mib.items())},
            "policies": sorted(policies)}


def allocator(mib: int, hold: float) -> int:
    """Allocate and touch `mib` MiB three ways (anonymous mmap, bytearray, ctypes buffer), report on stdout, hold, exit."""
    import ctypes
    import mmap
    third = max(1, mib // 3)
    region = mmap.mmap(-1, third * 1048576)
    for offset in range(0, len(region), 4096):
        region[offset] = 1
    array = bytearray(third * 1048576)
    for offset in range(0, len(array), 4096):
        array[offset] = 1
    buffer = ctypes.create_string_buffer(third * 1048576)
    ctypes.memset(buffer, 1, third * 1048576)
    pid = os.getpid()
    status = Path(f"/proc/{pid}/status").read_text()
    allowed = re.search(r"Cpus_allowed_list:\s*(\S+)", status).group(1)
    print(json.dumps({"pid": pid, "cpus_allowed_list": allowed, "numa_maps": numa_maps_pages(pid),
                      "running_cpu": int(Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[36])}), flush=True)
    time.sleep(hold)
    return 0


def bind_child(args) -> int:
    """The runner's sequence, then an allocating grandchild through an intermediate process (workers and the Spark JVM are
    grandchildren of the runner). Prints one JSON object."""
    topology = pt.cpu_topology()
    sockets = {s["socket"]: s for s in topology["sockets"]}
    record = {"scenario": args.scenario}
    if args.cpu_list:
        pinning = pt.resolve_pinning(None, args.cpu_list)
    else:
        pinning = pt.resolve_pinning(args.socket, None)
    record["pinning"] = {k: pinning[k] for k in ("cpus", "cpu_count", "sockets", "numa_nodes")}
    membind = pt.plan_membind(pinning, args.no_membind)
    record["plan"] = {k: membind[k] for k in ("enabled", "policy", "nodes")}
    if args.bind_nodes is not None:                              # run on one socket, bind to another's nodes
        membind = {"enabled": True, "policy": "MPOL_BIND", "nodes": pt.parse_cpu_list(args.bind_nodes)}
        record["plan"] = {k: membind[k] for k in ("enabled", "policy", "nodes")}
    if membind["enabled"]:
        record["membind"] = pt.apply_membind(membind["nodes"])
    record["applied_affinity"] = pt.apply_pinning(pinning["cpus"])
    record["policy_after"] = pt.read_numa_maps_policy()
    code = ("import subprocess,sys;"
            f"sys.exit(subprocess.run([sys.executable,{str(Path(__file__).resolve())!r},'alloc','--mib',{str(args.mib)!r},'--hold','0']).returncode)")
    done = run([sys.executable, "-c", code])
    record["allocator_exit"] = done.returncode
    record["allocator"] = json.loads(done.stdout.strip().splitlines()[-1]) if done.stdout.strip() else {"stderr": done.stderr[-300:]}
    record["socket_nodes"] = {str(k): v["numa_nodes"] for k, v in sockets.items()}
    print(json.dumps(record))
    return 0


def first_cores(socket: int, physical: int, topology) -> str:
    """The `physical` lowest-numbered physical cores of `socket` with their SMT siblings, as a cpulist (13 -> 26 vCPUs)."""
    members = pt.parse_cpu_list(next(s["cpus"] for s in topology["sockets"] if s["socket"] == socket))
    groups, seen = [], set()
    for cpu in members:
        if cpu in seen:
            continue
        siblings = pt.parse_cpu_list(pt.read_text(Path(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")) or str(cpu))
        seen.update(siblings)
        groups.append(siblings)
    return pt.format_cpu_list([c for group in groups[:physical] for c in group])


def numa_bind(args) -> int:
    topology = pt.cpu_topology()
    sockets = {s["socket"]: s for s in topology["sockets"]}
    if 0 not in sockets or 1 not in sockets:
        print("needs a host with sockets 0 and 1", file=sys.stderr)
        return 2
    pinned = first_cores(0, 13, topology)
    other = first_cores(1, 13, topology)
    nodes0, nodes1 = ",".join(map(str, sockets[0]["numa_nodes"])), ",".join(map(str, sockets[1]["numa_nodes"]))
    scenarios = [
        ("pin26-bound", ["--cpu-list", pinned]),
        ("socket0-bound", ["--socket", "0"]),
        ("socket1-cpus-bound-to-socket0-nodes", ["--cpu-list", other, "--bind-nodes", nodes0]),
        ("socket1-cpus-unbound-control", ["--cpu-list", other, "--no-membind"]),
    ]
    results = []
    for name, extra in scenarios:
        done = run([sys.executable, str(Path(__file__).resolve()), "bind-child", "--scenario", name, "--mib", str(args.mib), *extra])
        try:
            results.append(json.loads(done.stdout.strip().splitlines()[-1]))
        except (ValueError, IndexError):
            results.append({"scenario": name, "error": (done.stderr or done.stdout)[-500:], "exit": done.returncode})
    probe = {}
    for label, nodes in (("socket0_nodes", nodes0), ("pinned_cpu_nodes", ",".join(map(str, pt.resolve_pinning(None, pinned)["numa_nodes"])))):
        report = pt.membind_probe_report(pt.parse_cpu_list(nodes))
        probe[label] = {"nodes": nodes, "ok": report["ok"], "mechanism": report["mechanism"], "readback": report.get("readback"),
                        "reasons": report["reasons"]}
    verdicts = {}
    for r in results:
        pages = r.get("allocator", {}).get("numa_maps", {}).get("mib", {})
        total = sum(pages.values())
        verdicts[r["scenario"]] = {"mib_by_node": pages, "total_mib": round(total, 1),
                                   "node_share": {k: round(v / total, 4) for k, v in pages.items()} if total else None}
    out = {"topology": topology, "pinned_26_cpus": pinned, "socket1_probe_cpus": other, "membind_probe": probe,
           "scenarios": results, "verdicts": verdicts, "mib_requested": args.mib}
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"verdicts": verdicts, "membind_probe": probe}, indent=2))
    return 0


# ---- RAPL versus perf ------------------------------------------------------------------------------------

def powercap_zones() -> dict:
    """{zone path name: {"name", "max", "uj"}} for every readable intel-rapl zone (package, dram, ...)."""
    zones = {}
    for energy in sorted(POWERCAP.glob("intel-rapl*/energy_uj")) + sorted(POWERCAP.glob("intel-rapl*/intel-rapl*/energy_uj")):
        zone = energy.parent
        try:
            zones[zone.name] = {"name": (zone / "name").read_text().strip(), "max": int((zone / "max_energy_range_uj").read_text()),
                                "uj": int(energy.read_text()), "t": time.monotonic()}
        except OSError as exc:
            zones[zone.name] = {"name": (zone / "name").read_text().strip() if (zone / "name").exists() else "?", "error": str(exc)}
    return zones


def zone_delta_j(before, after) -> float:
    delta = after["uj"] - before["uj"]
    return (delta + before["max"] if delta < 0 else delta) / 1e6


def parse_perf_csv(text: str) -> list:
    """Rows of `perf stat -x,` output for the power events: [{"socket": "S0"|None, "event", "joules", "runtime_ns"}]."""
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        event = next((i for i, c in enumerate(cells) if c.startswith("power/energy-")), None)
        if event is None or event < 2 or cells[event - 1].lower() not in ("joules", "j"):
            continue
        try:
            value = float(cells[event - 2])
        except ValueError:
            value = None
        socket = next((c for c in cells[:event - 2] if re.fullmatch(r"S\d+", c)), None)
        runtime = cells[event + 1] if len(cells) > event + 1 and cells[event + 1].isdigit() else None
        rows.append({"socket": socket, "event": cells[event], "joules": value, "runtime_ns": int(runtime) if runtime else None})
    return rows


def add_perf_watts(row: dict, seconds: float) -> dict:
    """Mean power of one perf row over the wall-clock window (`perf stat ... sleep seconds`).

    Not joules / runtime: without --per-socket, `perf stat -a` reports a runtime summed over the sockets (40 s for a 20 s
    window on a 2-socket host), so dividing by it halves the power (2026-11-11 GCP probe; corrected 2026-11-15). The
    Joules themselves are whole-host and match the powercap package sum."""
    if row.get("joules") is not None and seconds:
        row["watts"] = row["joules"] / seconds
        row["watts_basis"] = "joules / wall-clock window seconds"
    return row


def rapl_perf_window(seconds: int, perf_args, cpus: str | None) -> dict:
    """One window: powercap before, `perf stat ... sleep N` (optionally with spin loops pinned to `cpus`), powercap after."""
    burners = []
    if cpus:
        burners = [subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "burn", "--cpus", cpus, "--seconds", str(seconds + 4)])]
        time.sleep(1.5)
    before = powercap_zones()
    done = run(["perf", "stat", *perf_args, "-x,", "-e", "power/energy-pkg/,power/energy-ram/", "--", "sleep", str(seconds)], timeout=seconds + 60)
    after = powercap_zones()
    for process in burners:
        process.wait()
    window_s = max(a["t"] for a in after.values() if "t" in a) - min(b["t"] for b in before.values() if "t" in b)
    zones = {name: {"name": before[name]["name"], "joules": zone_delta_j(before[name], after[name]),
                    "watts": zone_delta_j(before[name], after[name]) / (after[name]["t"] - before[name]["t"])}
             for name in before if "uj" in before[name] and "uj" in after.get(name, {})}
    perf = parse_perf_csv(done.stderr + "\n" + done.stdout)
    for row in perf:
        add_perf_watts(row, seconds)
    return {"seconds": seconds, "perf_args": perf_args, "burn_cpus": cpus, "powercap_window_s": window_s, "powercap": zones,
            "perf": perf, "perf_exit": done.returncode, "perf_raw": (done.stderr + done.stdout).strip().splitlines()[-12:]}


def rapl_perf(args) -> int:
    if os.geteuid() != 0:
        print("rapl-perf needs root (powercap energy_uj 0400, perf_event_paranoid)", file=sys.stderr)
        return 2
    topology = pt.cpu_topology()
    busy = first_cores(0, 13, topology)
    out = {"topology": topology, "perf_version": run(["perf", "--version"]).stdout.strip(), "burn_cpus": busy,
           "perf_list_power": [l.strip() for l in run(["perf", "list", "pmu"]).stdout.splitlines() if "power/" in l]}
    plan = [("idle-per-socket", ["--per-socket", "-a"], None), ("idle-all", ["-a"], None),
            ("socket0-busy-per-socket", ["--per-socket", "-a"], busy), ("socket0-busy-all", ["-a"], busy)]
    out["windows"] = {}
    for name, perf_args, cpus in plan:
        out["windows"][name] = rapl_perf_window(args.seconds, perf_args, cpus)
    # per-package comparison: perf --per-socket watts against the powercap package/dram zone of the same socket
    compare = {}
    for name in ("idle-per-socket", "socket0-busy-per-socket"):
        window = out["windows"][name]
        packages = {z: v for z, v in window["powercap"].items() if v["name"].startswith("package-")}
        drams = {z: v for z, v in window["powercap"].items() if v["name"] == "dram"}
        rows = []
        for row in window["perf"]:
            if row["socket"] is None or "watts" not in row:
                continue
            socket = int(row["socket"][1:])
            if row["event"] == "power/energy-pkg/":
                reference = next((v for v in packages.values() if v["name"] == f"package-{socket}"), None)
            else:                                    # dram zones are sub-zones: intel-rapl:<socket>:<n>
                reference = next((v for z, v in drams.items() if z.startswith(f"intel-rapl:{socket}:")), None)
            if reference:
                rows.append({"socket": row["socket"], "event": row["event"], "perf_w": row["watts"], "powercap_w": reference["watts"],
                             "relative_difference": (row["watts"] - reference["watts"]) / reference["watts"]})
        compare[name] = rows
    allcheck = {}
    for name in ("idle-all", "socket0-busy-all"):
        window = out["windows"][name]
        pkg_total = sum(v["watts"] for v in window["powercap"].values() if v["name"].startswith("package-"))
        perf_pkg = next((r["watts"] for r in window["perf"] if r["event"] == "power/energy-pkg/" and "watts" in r), None)
        allcheck[name] = {"perf_a_pkg_w": perf_pkg, "powercap_sum_of_packages_w": pkg_total,
                          "perf_over_sum": perf_pkg / pkg_total if perf_pkg and pkg_total else None}
    out["per_socket_comparison"], out["perf_a_vs_sum_of_packages"] = compare, allcheck
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"per_socket_comparison": compare, "perf_a_vs_sum_of_packages": allcheck}, indent=2))
    return 0


# ---- power.csv summary -----------------------------------------------------------------------------------

def series_watts(path: Path) -> dict:
    """Per (zone, domain) series of a power.csv: rows, mean W from first/last counter, 1 s means' min/max, flags seen."""
    series = {}
    with Path(path).open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["energy_uj"] == "":
                continue
            series.setdefault((row["zone"], row["domain"]), []).append((int(row["monotonic_ns"]), float(row["energy_uj"]), row["flag"]))
    out = {}
    for (zone, domain), rows in sorted(series.items()):
        if len(rows) < 2:
            continue
        seconds = (rows[-1][0] - rows[0][0]) / 1e9
        by_second, start = [], 0
        for i, (t, e, _) in enumerate(rows):
            if (t - rows[start][0]) >= 1e9:
                by_second.append((e - rows[start][1]) / 1e6 / ((t - rows[start][0]) / 1e9))
                start = i
        out[f"{zone}|{domain}"] = {
            "rows": len(rows), "seconds": round(seconds, 3), "mean_w": (rows[-1][1] - rows[0][1]) / 1e6 / seconds,
            "per_second_w": {"min": min(by_second), "max": max(by_second), "stdev": statistics.pstdev(by_second)} if by_second else None,
            "median_row_spacing_ms": statistics.median((b[0] - a[0]) / 1e6 for a, b in zip(rows, rows[1:])),
            "max_row_spacing_ms": max((b[0] - a[0]) / 1e6 for a, b in zip(rows, rows[1:])),
            "flags": sorted({f for _, _, f in rows if f})}
    return out


def power_csv(args) -> int:
    summaries = {str(p): series_watts(Path(p)) for p in args.csv}
    result = {"files": summaries}
    if len(args.csv) >= 2:
        first, second = summaries[str(args.csv[0])], summaries[str(args.csv[1])]
        result["relative_difference_first_two"] = {k: abs(first[k]["mean_w"] - second[k]["mean_w"]) / ((first[k]["mean_w"] + second[k]["mean_w"]) / 2)
                                                   for k in first if k in second}
        total_a, total_b = sum(v["mean_w"] for v in first.values()), sum(v["mean_w"] for v in second.values())
        result["sum_of_series_w"] = [total_a, total_b]
        result["sum_relative_difference"] = abs(total_a - total_b) / ((total_a + total_b) / 2)
    print(json.dumps(result, indent=2))
    return 0


def burn(args) -> int:
    cpus = pt.parse_cpu_list(args.cpus)
    children = []
    for cpu in cpus:
        pid = os.fork()
        if pid == 0:
            os.sched_setaffinity(0, {cpu})
            end = time.monotonic() + args.seconds
            while time.monotonic() < end:
                for _ in range(20000):
                    pass
            os._exit(0)
        children.append(pid)
    for pid in children:
        os.waitpid(pid, 0)
    return 0


# ---- P6-CPU: RAPL resolution on synthetic pulses ---------------------------------------------------------

CAMPAIGN_CPUS = "0-12,96-108"          # the campaign's pin on c3-highmem-192-metal: 13 physical cores + SMT siblings on socket 0 (design 3.1)
P6_CPU_INTERVAL_S = 0.2                # the campaign sampler's period (run_experiment --power-interval-ms default)


class CpuPulse:
    """A CPU load pulse of known length: one forked spin loop per listed CPU, each pinned to its CPU, until a common deadline.

    The deadline is fixed before the first fork, so the load lasts `seconds` whatever the fork and wait cost. The memory policy
    and affinity of the parent (the campaign's pin and node binding) are inherited."""

    def __init__(self, cpus):
        self.cpus = list(cpus)

    def pulse(self, seconds: float) -> dict:
        deadline = time.monotonic() + seconds
        children = []
        for cpu in self.cpus:
            pid = os.fork()
            if pid == 0:
                try:
                    os.sched_setaffinity(0, {cpu})
                    while time.monotonic() < deadline:
                        for _ in range(20000):
                            pass
                    os._exit(0)
                except BaseException:
                    os._exit(1)
            children.append(pid)
        failed = [pid for pid in children if os.waitpid(pid, 0)[1] != 0]
        if failed:
            raise RuntimeError(f"{len(failed)} of {len(children)} spin processes failed")
        return {"nominal_s": seconds, "cpus": len(self.cpus)}


class ZoneReader:
    """Direct energy_uj reads of the S2-pinned zones (package and DRAM of the pinned socket): the reference of P6-CPU.

    zones is [{"zone", "path", "max"}]; each read returns the raw counters with the monotonic bracket around the whole read."""

    def __init__(self, zones):
        self.zones = list(zones)

    def read(self) -> dict:
        before = time.monotonic_ns()
        values = {z["zone"]: pt.read_int(z["path"]) for z in self.zones}              # the sampler's own reader
        return {"uj": values, "monotonic_ns": [before, time.monotonic_ns()]}

    def delta_j(self, start: dict, end: dict) -> float:
        """Joules between two reads, a counter that wrapped counted once (max_energy_range_uj)."""
        total = 0
        for z in self.zones:
            delta = end["uj"][z["zone"]] - start["uj"][z["zone"]]
            total += delta + z["max"] if delta < 0 else delta
        return total / 1e6


def pinned_zone_files(report: dict) -> list:
    """[{"zone", "path", "max"}] of the S2-pinned zones from a PowerSampler report: where to read their counters directly."""
    wanted = set(pt.pinned_energy_zones(report))
    found = []
    for source in report.get("sources", []):
        for domain in source.get("domains", []):
            if domain.get("zone") in wanted and domain.get("sampled"):
                found.append({"zone": domain["zone"], "domain": domain["domain"], "path": domain["path"],
                              "max": domain.get("max_energy_range_uj") or 0})
    return sorted(found, key=lambda z: z["zone"])


def measure_cpu_pulse(reader, pulse, seconds: float) -> dict:
    """Direct read, stamps, pulse, stamps, direct read: the study's per-run bracket (reads and stamps just outside the pulse)."""
    start_read = reader.read()
    open_utc, open_mono = pt.stamp_open()
    stamps = pulse.pulse(seconds)
    close_utc, close_mono = pt.stamp_close()
    end_read = reader.read()
    return {"start_read": start_read, "end_read": end_read, "open_utc": open_utc, "open_mono": open_mono,
            "close_utc": close_utc, "close_mono": close_mono, "host_s": (close_mono - open_mono) / 1e9, "pulse": stamps}


def score_cpu_run(run: dict, series: dict, zones, reader) -> dict:
    """The campaign path (S2-pinned energy interpolated from power.csv at the stamps) against the direct reads, for one run."""
    estimate = pt.s2_pinned_energy_j(series, [z["zone"] for z in zones], run["open_mono"], run["close_mono"])
    reference = reader.delta_j(run["start_read"], run["end_read"])
    return {"estimate_j": estimate["joules"], "estimate_reason": estimate["reason"], "reference_j": reference,
            "rel_error": None if estimate["joules"] is None or reference <= 0 else (estimate["joules"] - reference) / reference}


def cpu_error_summary(records) -> dict:
    """p3_gpu_probes.error_summary on estimate/reference records, with the keys named for what they are, and the invalid count."""
    adapted = [{**r, "counter_j": r["estimate_j"], "integral_j": r["reference_j"]} for r in records]
    summary = gpu_probes.error_summary(adapted)
    for length, row in summary.items():
        row["median_estimate_j"], row["median_reference_j"] = row.pop("median_counter_j"), row.pop("median_integral_j")
        mine = [r for r in records if str(r["length_s"]) == length]
        row["n_total"], row["n_invalid"] = len(mine), sum(1 for r in mine if r.get("rel_error") is None)
    return summary


def jitter(rng, low: float, spread: float = 0.1) -> float:
    return low + rng.random() * spread                  # random phase against the 200 ms sampler ticks


def run_p6_cpu(args, out: Path, sampler, reader, pulse, zones, sleep=None) -> dict:
    """Pulses of known length against the sampler's series; the analysis of both modes. sampler.start() has been called by the
    caller; sampler.stop() is called here once the last pulse and a settle are over, so the series covers every window."""
    rng = random.Random(args.seed)
    sleep = sleep or time.sleep                                     # looked up now, so a test can substitute the module's clock
    wait = lambda seconds: sleep(seconds * args.wait_scale)         # noqa: E731  (wait_scale shrinks every pause; 1 on the real host)
    isolated, windows = [], []
    sleep(args.settle)
    for _ in range(2):
        pulse.pulse(args.warmup_pulse_s)                            # warm-up: cold caches, clock ramp, first fork
    wait(3.0)
    began = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    for repeat in range(args.repeats):
        order = list(args.lengths)
        rng.shuffle(order)
        for length in order:
            wait(jitter(rng, 3.0 if length < 5 else 6.0))
            isolated.append({"repeat": repeat, "length_s": length, "runs": 1, "pairs": [measure_cpu_pulse(reader, pulse, length)]})
    for repeat in range(args.window_repeats):
        order = list(args.window_lengths)
        rng.shuffle(order)
        for length in order:
            wait(jitter(rng, 5.0))
            pairs = []
            while True:                                             # design 2.1: until the summed timed time reaches the target, or the cap
                pairs.append(measure_cpu_pulse(reader, pulse, length))
                if pt.energy_window_complete([p["host_s"] for p in pairs], args.window_target_s, args.window_max_runs):
                    break
                wait(jitter(rng, 0.2))                              # the gap between runs of a window (cleanup in the study)
            windows.append({"repeat": repeat, "length_s": length, "runs": len(pairs), "pairs": pairs})
    wait(3.0)
    report = sampler.stop()
    series = pt.read_power_csv_series(sampler.csv_path)
    for record in isolated + windows:
        scored = [score_cpu_run(run, series, zones, reader) for run in record["pairs"]]
        if any(s["estimate_j"] is None for s in scored):
            record.update(estimate_j=None, estimate_reason="; ".join(sorted({s["estimate_reason"] for s in scored if s["estimate_reason"]})))
        else:
            record.update(estimate_j=sum(s["estimate_j"] for s in scored), estimate_reason=None)
        record["reference_j"] = sum(s["reference_j"] for s in scored)
        record["window_s"] = sum(run["host_s"] for run in record["pairs"])
        record["rel_error"] = (None if record["estimate_j"] is None or record["reference_j"] <= 0
                               else (record["estimate_j"] - record["reference_j"]) / record["reference_j"])
        record["stop_reason"] = "target_reached" if record["window_s"] >= args.window_target_s else "max_runs"
        record["pairs"] = [{"start_mono_ns": run["open_mono"], "end_mono_ns": run["close_mono"], "start_utc": run["open_utc"],
                            "end_utc": run["close_utc"], "host_s": run["host_s"], "reference_j": s["reference_j"], "estimate_j": s["estimate_j"],
                            "estimate_reason": s["estimate_reason"],
                            "start_read_ns": run["start_read"]["monotonic_ns"], "end_read_ns": run["end_read"]["monotonic_ns"],
                            "start_uj": run["start_read"]["uj"], "end_uj": run["end_read"]["uj"]} for run, s in zip(record["pairs"], scored)]
        record["per_run_s"] = record["length_s"]
    for record in isolated:                                 # one run per isolated record: its pair is the record's, as in p6.json
        record["pair"] = record.pop("pairs")[0]
    isolated_summary, window_summary = cpu_error_summary(isolated), cpu_error_summary(windows)
    spacing = [b - a for z in series.values() for a, b in zip(z["mono"], z["mono"][1:])]
    return {"step": "p6-cpu", "started_utc": began, "ended_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "pinning": {"cpus": args.cpus, "zones": [{k: z[k] for k in ("zone", "domain", "path")} for z in zones]},
            "stream": {"interval_s": P6_CPU_INTERVAL_S if args.interval_ms is None else args.interval_ms / 1000, "series": len(series),
                       "rows": {zone: len(v["mono"]) for zone, v in sorted(series.items())},
                       "median_spacing_ms": statistics.median(spacing) / 1e6 if spacing else None,
                       "max_spacing_ms": max(spacing) / 1e6 if spacing else None,
                       "flags": sorted({f for v in series.values() for flag in v["flags"] for f in flag.split("+") if f}),
                       "csv": Path(sampler.csv_path).name, "sidecar": Path(sampler.sidecar_path).name,
                       "overhead": report.get("overhead")},
            "definition": "rel_error = (S2-pinned energy interpolated linearly from the sampler's power.csv at the stamps taken just "
                          "outside the pulse - direct energy_uj reads of the same zones just outside those stamps) / direct reads; "
                          "isolated: one pulse per window; windows: the sums over the runs of one window (runs continue until their "
                          f"summed timed time reaches {args.window_target_s:g} s or {args.window_max_runs} runs; gaps between runs "
                          "excluded, as design 2.1). length_s is the nominal pulse; host_s is the stamped window (pulse + fork and wait).",
            "isolated": {"repeats": args.repeats, "summary": isolated_summary, "t_min": gpu_probes.t_min_candidates(isolated_summary),
                         "records": isolated},
            "windows": {"repeats": args.window_repeats, "target_s": args.window_target_s, "max_runs": args.window_max_runs,
                        "summary": window_summary, "t_min": gpu_probes.t_min_candidates(window_summary), "records": windows}}


def p6_cpu(args, start_background=True, on_started=None) -> int:
    """The step on the real host: the campaign's pin and node binding, then pt.PowerSampler exactly as run_experiment starts it.

    start_background=False leaves the sampler threads off and on_started(sampler) is called once it is running: a test drives
    the polls itself, on a simulated clock."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pinning = pt.resolve_pinning(None, args.cpus)
    membind = pt.plan_membind(pinning, False)
    if membind["enabled"]:                                  # memory first, as run_experiment does: a numactl re-exec starts from the original affinity
        membind.update(pt.apply_membind(membind["nodes"]))
    pinning["membind"] = membind
    pinning["applied_affinity"] = pt.apply_pinning(pinning["cpus"])
    interval = (P6_CPU_INTERVAL_S if args.interval_ms is None else args.interval_ms / 1000)
    sampler = pt.PowerSampler(out, interval=interval, labels=lambda: ("", "p6-cpu", "execute"), pinning=pinning,
                              csv_name="p6-cpu-power.csv", sidecar_name="p6-cpu-power-sources.json")
    report = sampler.start(background=start_background)
    if on_started is not None:
        on_started(sampler)
    zones = pinned_zone_files(report)
    if report.get("cpu_energy_source") != "powercap-rapl" or not zones:
        sampler.stop()
        reasons = "; ".join(report.get("reasons") or []) or "no S2-pinned zone (is the run pinned to one socket?)"
        result = {"step": "p6-cpu", "error": f"cannot measure: cpu_energy_source={report.get('cpu_energy_source')!r}, "
                                              f"pinned zones {[z['zone'] for z in zones]}: {reasons}"}
    else:
        result = run_p6_cpu(args, out, sampler, ZoneReader(zones), CpuPulse(pt.parse_cpu_list(pinning["cpus"])), zones)
        result["meta"] = {"utc": datetime.now(timezone.utc).isoformat(), "pinning": {k: pinning[k] for k in ("cpus", "cpu_count", "sockets",
                          "numa_nodes", "membind", "applied_affinity") if k in pinning},
                          "sampler_status": report.get("status"), "cpu_energy_source": report.get("cpu_energy_source"),
                          "attribution": (report.get("pinning") or {}).get("attribution"), "host": report.get("host")}
    (out / "p6-cpu.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(json.dumps({k: result.get(k) for k in ("step", "error")} | {"t_min_isolated_s": (result.get("isolated") or {}).get("t_min", {}).get("t_min_s"),
                                                                  "t_min_windows_s": (result.get("windows") or {}).get("t_min", {}).get("t_min_s")}))
    return 1 if "error" in result else 0


def p6_cpu_seconds(args) -> float:
    """What the step will take, from its own schedule: pulses at their length plus fork cost, pauses at their mean jitter."""
    pause = lambda length: (3.0 if length < 5 else 6.0) + 0.05                              # noqa: E731
    runs = lambda length: min(args.window_max_runs, max(1, int(args.window_target_s / (length + 0.1)) + 1))   # noqa: E731
    pulses = (args.repeats * sum(x + 0.1 for x in args.lengths) + args.window_repeats * sum(runs(x) * (x + 0.1) for x in args.window_lengths)
              + 2 * args.warmup_pulse_s)
    pauses = (args.repeats * sum(pause(x) for x in args.lengths) + args.window_repeats * sum(5.05 + runs(x) * 0.25 for x in args.window_lengths)
              + 3.0 + 3.0)
    return args.settle + pulses + pauses * args.wait_scale


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("numa-bind")
    p.add_argument("--out", required=True)
    p.add_argument("--mib", type=int, default=3072)
    p = sub.add_parser("bind-child")                              # internal
    p.add_argument("--scenario", required=True)
    p.add_argument("--cpu-list")
    p.add_argument("--socket", type=int)
    p.add_argument("--no-membind", action="store_true")
    p.add_argument("--bind-nodes")
    p.add_argument("--mib", type=int, default=3072)
    p = sub.add_parser("alloc")                                   # internal
    p.add_argument("--mib", type=int, default=3072)
    p.add_argument("--hold", type=float, default=0.0)
    p = sub.add_parser("rapl-perf")
    p.add_argument("--out", required=True)
    p.add_argument("--seconds", type=int, default=20)
    p = sub.add_parser("power-csv")
    p.add_argument("csv", nargs="+", type=Path)
    p = sub.add_parser("burn")
    p.add_argument("--cpus", required=True)
    p.add_argument("--seconds", type=float, required=True)
    p = sub.add_parser("p6-cpu")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--cpus", default=CAMPAIGN_CPUS, help="the pin (default: the campaign's 13 physical cores of socket 0 with SMT siblings)")
    p.add_argument("--repeats", type=int, default=8, help="repeats per isolated pulse length")
    p.add_argument("--window-repeats", type=int, default=5, help="repeats per window length")
    p.add_argument("--lengths", type=float, nargs="+", default=list(gpu_probes.PULSE_LENGTHS_S), help="isolated pulse lengths, seconds")
    p.add_argument("--window-lengths", type=float, nargs="+", default=list(gpu_probes.WINDOW_LENGTHS_S), help="window pulse lengths, seconds")
    p.add_argument("--window-target-s", type=float, default=gpu_probes.WINDOW_TARGET_S, help="window rule: stop when the summed time reaches this")
    p.add_argument("--window-max-runs", type=int, default=gpu_probes.WINDOW_MAX_RUNS, help="window rule: or after this many runs")
    p.add_argument("--settle", type=float, default=5.0, help="seconds of sampling before the first pulse")
    p.add_argument("--warmup-pulse-s", type=float, default=1.0, help="length of the two warm-up pulses before the first measured one")
    p.add_argument("--interval-ms", type=float, default=None, help=f"sampler period (default {P6_CPU_INTERVAL_S * 1000:g}, the campaign's)")
    p.add_argument("--seed", type=int, default=20261111)
    p.add_argument("--wait-scale", type=float, default=1.0, help="multiplies every pause between pulses (1 on the host; tests use less)")
    args = parser.parse_args(argv)
    if args.command == "p6-cpu":
        if min(args.lengths + args.window_lengths + [args.window_target_s]) <= 0 or args.repeats < 1 or args.window_repeats < 0 \
                or args.window_max_runs < 1 or args.settle < 0 or args.warmup_pulse_s <= 0 or args.wait_scale < 0 or (args.interval_ms is not None and args.interval_ms <= 0):
            parser.error("p6-cpu: lengths, target and interval must be positive, repeats at least 1, settle and wait scale non-negative")
        print(f"p6-cpu: expected runtime about {p6_cpu_seconds(args) / 60:.0f} min", file=sys.stderr, flush=True)
    return {"numa-bind": numa_bind, "bind-child": bind_child, "alloc": lambda a: allocator(a.mib, a.hold),
            "rapl-perf": rapl_perf, "power-csv": power_csv, "burn": burn, "p6-cpu": p6_cpu}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
