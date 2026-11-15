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
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import power_telemetry as pt  # noqa: E402

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
    args = parser.parse_args(argv)
    return {"numa-bind": numa_bind, "bind-child": bind_child, "alloc": lambda a: allocator(a.mib, a.hold),
            "rapl-perf": rapl_perf, "power-csv": power_csv, "burn": burn}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
