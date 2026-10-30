#!/usr/bin/env python3
"""Compare the same workload measured on two hosts (for example A100 and H100).

    compare_hardware.py --a ROOT [ROOT ...] --a-label A100 --b ROOT [ROOT ...] --b-label H100 --output DIR

ROOT is a run root as written by run_experiment.py (sf*/ scale directories with
manifest.json and <engine>/results.json, plus schedule.json and environment.json).
Medians are joined per (suite, scale, engine, query). Before any scale is compared,
the two copied input manifests must agree on scale factor, table row counts, table
schemas, the order-independent DuckDB fingerprints (chunked SF100/SF300 manifests)
and, when no fingerprint is available on both sides, every per-file sha256. A
mismatching scale is refused unless --allow-input-mismatch is given; the report says
which. Host shape comes from environment.json; CPU engines are labelled
non-comparable when vCPU count, RAM or the CPU allocation differ, and engine or
configuration differences (polars, cudf-polars, Spark, plugin jar, GPU profile) are
flagged. Ratios are time(A) / time(B): above 1 means B is faster.

This tool compares timings only. Run the analyzers first: it neither re-validates
query outputs nor GPU plans.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import re
import statistics
import sys

from analyze_results import ENGINE_ORDER, host_shape, order_engines, read_environment, read_json, scale_value

CPU_ENGINES = ("polars", "spark_cpu")
FINGERPRINT_FIELDS = ("row_hash_sum", "row_hash_xor", "key_hash_sum", "key_hash_xor")
FAILED_STATUSES = {"failed", "timeout", "timed_out", "cancelled", "killed", "running", "started", "incomplete"}
RAM_TOLERANCE_GIB = 1.0


def good_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def read_schedule(root):
    try:
        value = read_json(root / "schedule.json")
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    except (OSError, ValueError, TypeError):
        return []


def schedule_item(schedule, scale_name, engine):
    wanted = scale_value(scale_name)
    matches = [item for item in schedule if item.get("engine") == engine and scale_value(item.get("scale")) == wanted]
    return matches[-1] if matches else {}


def engine_cells(session, item):
    """Per-query cells for one engine session: status plus measured-trial statistics when usable."""
    state, reason = "ok", ""
    if item and (item.get("returncode") not in (None, 0) or str(item.get("status", "")).lower() in FAILED_STATUSES):
        state, reason = "failed", f"orchestrator status={item.get('status')}, returncode={item.get('returncode')}"
    elif not session.get("completed_at_utc"):
        state, reason = "incomplete", "session has no completed_at_utc"
    elif session.get("failure") or session.get("failed_at_utc"):
        state, reason = "failed", "session recorded a global failure"
    errors = {}
    for key in ("query_errors", "query_failures"):
        for error in session.get(key) or []:
            if isinstance(error, dict) and error.get("query"):
                errors.setdefault(error["query"], f"{error.get('type', 'error')}: {error.get('message', '')}".strip())
    cells = {}
    for query in session.get("queries") or []:
        times = [r["seconds"] for r in session.get("records") or []
                 if isinstance(r, dict) and r.get("query") == query and r.get("phase") == "measured" and good_number(r.get("seconds"))]
        if state != "ok":
            cells[query] = {"status": state, "reason": reason}
        elif query in errors:
            cells[query] = {"status": "failed", "reason": errors[query]}
        elif len(times) != session.get("runs"):
            cells[query] = {"status": "incomplete", "reason": f"{len(times)} of {session.get('runs')} measured trials"}
        else:
            cells[query] = {"status": "ok", "median": statistics.median(times), "min": min(times), "max": max(times),
                            "trials": len(times)}
    return cells


def load_side(roots, label):
    side = {"label": label, "roots": [str(r) for r in roots], "hosts": [], "groups": {}, "notes": []}
    for root in roots:
        environment = read_environment(root)
        side["hosts"].append({"root": str(root), "host": host_shape(environment), "environment_found": environment is not None})
        schedule = read_schedule(root)
        for scale_dir in sorted(p for p in root.glob("sf*") if p.is_dir()):
            number = scale_value(scale_dir.name)
            if number is None:
                continue
            manifest, manifest_note = None, None
            try:
                manifest = read_json(scale_dir / "manifest.json")
            except (OSError, ValueError) as exc:
                manifest_note = f"manifest.json unreadable ({type(exc).__name__})"
            by_suite = {}
            for engine_dir in sorted(p for p in scale_dir.iterdir() if p.is_dir() and (p / "results.json").is_file()):
                try:
                    session = read_json(engine_dir / "results.json")
                    if not isinstance(session, dict) or session.get("engine") != engine_dir.name:
                        raise ValueError("engine metadata does not match directory")
                except (OSError, ValueError) as exc:
                    side["notes"].append(f"{scale_dir.name}/{engine_dir.name}: unreadable results.json ({exc})")
                    continue
                by_suite.setdefault(session.get("suite", "baseline"), {})[engine_dir.name] = session
            for suite, sessions in by_suite.items():
                key = (suite, number)
                if key in side["groups"]:
                    side["notes"].append(f"{suite} {scale_dir.name}: root {root} replaces {side['groups'][key]['root']} (last root wins)")
                side["groups"][key] = {
                    "suite": suite, "scale": scale_dir.name, "root": str(root), "manifest": manifest,
                    "manifest_note": manifest_note, "sessions": sessions,
                    "cells": {engine: engine_cells(session, schedule_item(schedule, scale_dir.name, engine))
                              for engine, session in sessions.items()}}
    return side


def manifest_equivalence(a, b):
    """Return {'ok': bool, 'mismatches': [...], 'notes': [...], 'method': str} for two copied manifests."""
    result = {"ok": False, "mismatches": [], "notes": [], "method": []}
    for name, manifest in (("A", a), ("B", b)):
        if not isinstance(manifest, dict) or not isinstance(manifest.get("tables"), dict):
            result["mismatches"].append(f"manifest {name} is missing or has no tables")
    if result["mismatches"]:
        return result
    if a.get("scale_factor") != b.get("scale_factor"):
        result["mismatches"].append(f"scale_factor {a.get('scale_factor')!r} vs {b.get('scale_factor')!r}")
    ta, tb = a["tables"], b["tables"]
    if set(ta) != set(tb):
        result["mismatches"].append(f"table sets differ: only A {sorted(set(ta) - set(tb))}, only B {sorted(set(tb) - set(ta))}")
    common = sorted(set(ta) & set(tb))
    for table in common:
        if ta[table].get("rows") != tb[table].get("rows"):
            result["mismatches"].append(f"{table}: row count {ta[table].get('rows')} vs {tb[table].get('rows')}")
        if ta[table].get("schema") != tb[table].get("schema"):
            result["mismatches"].append(f"{table}: schema differs")
    result["method"].append("row counts and schemas")
    fingerprinted = (a.get("fingerprint_algorithm") is not None and b.get("fingerprint_algorithm") is not None
                     and all(all(f in ta[t] and f in tb[t] for f in FINGERPRINT_FIELDS) for t in common) and bool(common))
    if fingerprinted:
        if a["fingerprint_algorithm"] != b["fingerprint_algorithm"]:
            result["mismatches"].append(f"fingerprint algorithms differ: {a['fingerprint_algorithm']!r} vs {b['fingerprint_algorithm']!r}")
        else:
            for table in common:
                differing = [f for f in FINGERPRINT_FIELDS if ta[table][f] != tb[table][f]]
                if differing:
                    result["mismatches"].append(f"{table}: fingerprint fields differ: {', '.join(differing)}")
            result["method"].append("order-independent fingerprints (" + str(a["fingerprint_algorithm"]) + ")")
    elif any(f in ta[t] or f in tb[t] for t in common for f in FINGERPRINT_FIELDS):
        result["notes"].append("fingerprints exist on one side only or use no common algorithm; falling back to file hashes")
    files_a = {f["path"]: f.get("sha256") for t in common for f in ta[t].get("files") or [] if isinstance(f, dict) and "path" in f}
    files_b = {f["path"]: f.get("sha256") for t in common for f in tb[t].get("files") or [] if isinstance(f, dict) and "path" in f}
    hashed = bool(files_a) and bool(files_b) and all(files_a.values()) and all(files_b.values())
    if hashed:
        differing = sorted(p for p in files_a.keys() & files_b.keys() if files_a[p] != files_b[p])
        layout = files_a.keys() ^ files_b.keys()
        if differing or layout:
            text = f"{len(differing)} file sha256 differ, {len(layout)} paths present on one side only"
            if fingerprinted and not result["mismatches"]:
                result["notes"].append(text + " (physical layout differs; logical equality rests on the matching fingerprints)")
            else:
                result["mismatches"].append(text)
        else:
            result["method"].append(f"per-file sha256 ({len(files_a)} files)")
    elif not fingerprinted:
        result["mismatches"].append("neither matching fingerprints nor per-file sha256 hashes are available on both sides")
    for field in ("duckdb_version",):
        if a.get(field) != b.get(field):
            result["notes"].append(f"{field}: {a.get(field)!r} vs {b.get(field)!r}")
    if a.get("parquet") != b.get("parquet"):
        result["notes"].append(f"parquet export parameters differ: {a.get('parquet')!r} vs {b.get('parquet')!r}")
    result["ok"] = not result["mismatches"]
    return result


def host_comparability(host_a, host_b):
    """CPU-engine comparability: reasons the two host shapes differ (empty list = comparable)."""
    reasons = []
    for name, key in (("vCPU count", "vcpus"), ("CPU allocation (--cpus)", "cpus_arg")):
        a, b = host_a.get(key), host_b.get(key)
        if a is None or b is None:
            reasons.append(f"{name} unknown on one side")
        elif a != b:
            reasons.append(f"{name} {a} vs {b}")
    a, b = host_a.get("host_ram_gib"), host_b.get("host_ram_gib")
    if a is None or b is None:
        reasons.append("host RAM unknown on one side")
    elif abs(a - b) > RAM_TOLERANCE_GIB:
        reasons.append(f"host RAM {a:g} vs {b:g} GiB")
    return reasons


def merged_host(side):
    """One host description per side; several roots must agree or the disagreement is reported."""
    hosts = [h["host"] for h in side["hosts"]]
    merged = {}
    conflicts = []
    for key in ("gpu", "gpu_count", "gpu_memory_mib", "vcpus", "host_ram_gib", "cpu_model", "driver_version", "cuda_version",
                "cpus_arg", "driver_memory_arg"):
        values = [h.get(key) for h in hosts if h.get(key) is not None]
        merged[key] = values[0] if values else None
        if len(set(map(str, values))) > 1:
            conflicts.append(f"{key}: {sorted(set(map(str, values)))}")
    merged["packages"] = {k: v for h in hosts for k, v in h.get("packages", {}).items()}
    return merged, conflicts


def engine_facts(side):
    """Versions and configuration of each engine, from its sessions (first session per engine)."""
    facts = {}
    for group in sorted(side["groups"].values(), key=lambda g: (g["suite"], scale_value(g["scale"]))):
        for engine, session in group["sessions"].items():
            entry = facts.setdefault(engine, {"version": set(), "cudf_polars": set(), "plugin": set(), "profile": set(),
                                              "executor": set(), "gpu_conf": {}, "cpu_threads": set()})
            entry["version"].add(str(session.get("version")))
            entry["cpu_threads"].add(str(session.get("cpu_threads")))
            meta = session.get("polars_gpu") if isinstance(session.get("polars_gpu"), dict) else {}
            if meta:
                entry["cudf_polars"].add(str(meta.get("cudf_polars_version")))
                entry["executor"].add(f"{meta.get('executor')} {json.dumps(meta.get('executor_options'), sort_keys=True)}")
            conf = session.get("spark_conf") if isinstance(session.get("spark_conf"), dict) else {}
            for jar in str(conf.get("spark.jars", "")).split(","):
                if "rapids-4-spark_" in jar:
                    entry["plugin"].add(jar.rsplit("/", 1)[-1])
            if session.get("spark_gpu_profile"):
                entry["profile"].add(str(session["spark_gpu_profile"]))
            if engine == "spark_gpu":
                entry["gpu_conf"] = {k: v for k, v in conf.items() if k.startswith("spark.rapids.") or k in (
                    "spark.plugins", "spark.sql.shuffle.partitions", "spark.driver.memory")}
    return facts


def facts_text(values):
    return ", ".join(sorted(values)) if values else "—"


def difference_lines(label_a, label_b, facts_a, facts_b):
    rows, flags = [], []
    empty = {"version": set(), "cudf_polars": set(), "plugin": set(), "profile": set(), "executor": set(), "gpu_conf": {}}
    for engine in order_engines(set(facts_a) | set(facts_b)):
        a, b = facts_a.get(engine), facts_b.get(engine)
        shown_a, shown_b = a or empty, b or empty
        rows.append(f"| {engine} | {facts_text(shown_a['version']) if a else 'not run'} | {facts_text(shown_b['version']) if b else 'not run'} | "
                    f"{facts_text(shown_a['cudf_polars'])} / {facts_text(shown_b['cudf_polars'])} | "
                    f"{facts_text(shown_a['plugin'])} / {facts_text(shown_b['plugin'])} | "
                    f"{facts_text(shown_a['profile'])} / {facts_text(shown_b['profile'])} | "
                    f"{facts_text(shown_a['executor'])} / {facts_text(shown_b['executor'])} |")
        if a is None or b is None:
            flags.append(f"{engine} was run on only one side ({label_a if a else label_b}).")
            continue
        for field, name in (("version", "engine version"), ("cudf_polars", "cudf-polars version"), ("plugin", "RAPIDS plugin jar"),
                            ("profile", "Spark GPU profile"), ("executor", "Polars GPU executor")):
            if a[field] != b[field]:
                flags.append(f"{engine}: {name} differs ({facts_text(a[field])} on {label_a} vs {facts_text(b[field])} on {label_b}).")
        if engine == "spark_gpu" and a["gpu_conf"] != b["gpu_conf"]:
            keys = sorted(k for k in set(a["gpu_conf"]) | set(b["gpu_conf"]) if a["gpu_conf"].get(k) != b["gpu_conf"].get(k))
            flags.append("spark_gpu: recorded Spark/RAPIDS configuration differs on " + ", ".join(keys) + ".")
    return rows, flags


def cell(group, engine, query):
    return group["cells"].get(engine, {}).get(query)


def join_rows(label_a, label_b, group_a, group_b, cpu_reasons, equivalence_status):
    rows = []
    engines = order_engines(set(group_a["cells"]) | set(group_b["cells"]))
    for engine in engines:
        comparability = ("not comparable: " + "; ".join(cpu_reasons)) if engine in CPU_ENGINES and cpu_reasons else (
            "CPU engine, same host shape" if engine in CPU_ENGINES else "GPU engine across hardware (host, driver and software also differ)")
        queries = list(dict.fromkeys([*(group_a["cells"].get(engine) or {}), *(group_b["cells"].get(engine) or {})]))
        usable = []
        for query in queries:
            a, b = cell(group_a, engine, query), cell(group_b, engine, query)
            row = {"suite": group_a["suite"], "scale": group_a["scale"], "engine": engine, "query": query,
                   "comparability": comparability, "input_equivalence": equivalence_status}
            for name, c in ((label_a, a), (label_b, b)):
                row[f"{name}_status"] = c["status"] if c else "not run"
                row[f"{name}_median_seconds"] = c.get("median") if c else None
                row[f"{name}_min_seconds"] = c.get("min") if c else None
                row[f"{name}_max_seconds"] = c.get("max") if c else None
                row[f"{name}_note"] = (c.get("reason", "") if c else "no session for this engine/query")
            both = bool(a and b and a["status"] == "ok" and b["status"] == "ok")
            row[f"speedup_{label_b}_over_{label_a}"] = a["median"] / b["median"] if both else None
            rows.append(row)
            if both:
                usable.append((a["median"], b["median"]))
        total = {"suite": group_a["suite"], "scale": group_a["scale"], "engine": engine, "query": "SUM",
                 "comparability": comparability, "input_equivalence": equivalence_status}
        top, bottom = sum(a for a, _ in usable), sum(b for _, b in usable)
        total[f"{label_a}_status"] = f"ok ({len(usable)}/{len(queries)} queries)" if usable else "no common timed queries"
        total[f"{label_b}_status"] = total[f"{label_a}_status"]
        total[f"{label_a}_median_seconds"], total[f"{label_b}_median_seconds"] = (top, bottom) if usable else (None, None)
        for name in (label_a, label_b):
            total[f"{name}_min_seconds"] = total[f"{name}_max_seconds"] = None
            total[f"{name}_note"] = "sum of per-query medians over queries timed on both sides"
        total[f"speedup_{label_b}_over_{label_a}"] = top / bottom if usable else None
        rows.append(total)
    return rows


def fmt(value, digits=3):
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "—"


def cell_text(row, label):
    if row[f"{label}_median_seconds"] is not None:
        return fmt(row[f"{label}_median_seconds"])
    return row[f"{label}_status"].upper()


def report(args, sides, hosts, host_conflicts, facts, comparisons, refused, notes):
    label_a, label_b = args.a_label, args.b_label
    speedup = f"speedup_{label_b}_over_{label_a}"
    host_a, host_b = hosts
    cpu_reasons = host_comparability(host_a, host_b)
    lines = [f"# Hardware comparison: {label_a} vs {label_b}", "",
             f"Timings are joined per suite, scale, engine and query. Speedup = {label_a} median / {label_b} median: above 1 means {label_b} is faster. "
             "Totals are sums of per-query medians over the queries timed on both sides. Only scales whose copied input manifests are equivalent are compared"
             + (" (**--allow-input-mismatch was given: mismatching scales are included and marked**)." if args.allow_input_mismatch else "; other scales are refused and listed below."),
             "", "This report compares timings only. Output agreement and GPU-plan validation come from the analyzers; run them on each root first.", "",
             "## Host shape and caveats", "",
             "| Side | GPU | GPU memory | vCPU | Host RAM (GiB) | CPU | --cpus | Driver / CUDA | Roots |", "|---|---|---:|---:|---:|---|---:|---|---|"]
    for label, host, side in ((label_a, host_a, sides[0]), (label_b, host_b, sides[1])):
        gpu = f"{host['gpu_count'] or 1} x {host['gpu']}" if host.get("gpu") else "not recorded"
        memory = f"{host['gpu_memory_mib'] / 1024:.0f} GiB" if host.get("gpu_memory_mib") else "not recorded"
        lines.append(f"| {label} | {gpu} | {memory} | {host.get('vcpus') or 'not recorded'} | {host.get('host_ram_gib') or 'not recorded'} | "
                     f"{host.get('cpu_model') or 'not recorded'} | {host.get('cpus_arg') or 'not recorded'} | "
                     f"{host.get('driver_version') or '?'} / {host.get('cuda_version') or '?'} | {'; '.join('`' + r + '`' for r in side['roots'])} |")
    lines += ["", "**Caveats**", ""]
    if cpu_reasons:
        lines.append(f"- CPU engines (polars, spark_cpu) are **not comparable** across these hosts: {'; '.join(cpu_reasons)}. Their rows are listed for reference only and must not be read as a hardware speedup.")
    else:
        lines.append("- Host shape (vCPU, RAM, CPU allocation) matches on both sides, so CPU engines are comparable subject to the version notes below.")
    lines.append("- GPU-engine rows compare different GPU models on different hosts: the CPU, RAM, driver and engine versions also differ. A speedup mixes all of these and is not attributable to the GPU alone.")
    if host_a.get("gpu") == host_b.get("gpu") and host_a.get("gpu"):
        lines.append("- Both sides report the same GPU model.")
    for conflict_label, conflict in zip((label_a, label_b), host_conflicts):
        if conflict:
            lines.append(f"- {conflict_label}: roots disagree on the host description ({'; '.join(conflict)}); the first recorded value is shown.")
    for side, label in zip(sides, (label_a, label_b)):
        for entry in side["hosts"]:
            if not entry["environment_found"]:
                lines.append(f"- {label}: `{entry['root']}` has no readable environment.json; its host shape is unknown.")
    version_rows, flags = difference_lines(label_a, label_b, facts[0], facts[1])
    lines += ["", "### Engine versions and configuration", "",
              f"| Engine | Version on {label_a} | Version on {label_b} | cudf-polars ({label_a} / {label_b}) | Plugin jar ({label_a} / {label_b}) | Spark GPU profile ({label_a} / {label_b}) | Polars GPU executor ({label_a} / {label_b}) |",
              "|---|---|---|---|---|---|---|", *version_rows, ""]
    lines += ["Differences:", ""] + (["- " + flag for flag in flags] if flags else ["- None detected in the recorded versions and configuration."])
    lines += ["", "## Input equivalence", "",
              "| Suite | Scale | Result | Checked by | Mismatches / notes |", "|---|---|---|---|---|"]
    for item in comparisons + refused:
        eq = item["equivalence"]
        status = "equivalent" if eq["ok"] else ("MISMATCH (compared: --allow-input-mismatch)" if item.get("allowed") else "MISMATCH (refused)")
        detail = "; ".join(eq["mismatches"] + eq["notes"]) or "none"
        lines.append(f"| {item['suite']} | {item['scale']} | {status} | {', '.join(eq['method']) or '—'} | {detail.replace('|', chr(92) + '|')} |")
    if not (comparisons or refused):
        lines.append("| — | — | no scale present on both sides | — | — |")
    suites = list(dict.fromkeys(item["suite"] for item in comparisons))
    for suite in suites:
        group = [item for item in comparisons if item["suite"] == suite]
        lines += ["", f"## Results: {suite} suite", ""]
        for kind, title in (("gpu", "GPU engines (cross-hardware; see caveats)"), ("cpu", "CPU engines" + (" — NOT COMPARABLE, reference only" if cpu_reasons else ""))):
            selected = [row for item in group for row in item["rows"] if (row["engine"] in CPU_ENGINES) == (kind == "cpu")]
            if not selected:
                continue
            lines += [f"### {title}", "",
                      f"| Scale | Engine | Query | {label_a} (s) | {label_b} (s) | {label_a} / {label_b} | Input equivalence |", "|---|---|---|---:|---:|---:|---|"]
            for row in sorted(selected, key=lambda r: (scale_value(r["scale"]), ENGINE_ORDER.index(r["engine"]) if r["engine"] in ENGINE_ORDER else 99,
                                                       r["query"] != "SUM", r["query"])):
                ratio = row[speedup]
                lines.append(f"| {row['scale']} | {row['engine']} | {row['query']} | {cell_text(row, label_a)} | {cell_text(row, label_b)} | "
                             f"{fmt(ratio, 2) + 'x' if ratio is not None else '—'} | {'verified' if row['input_equivalence'] == 'verified' else '**MISMATCH (allowed)**'} |")
            lines.append("")
    lines += ["## Refused scales", ""]
    if refused:
        for item in refused:
            lines.append(f"- {item['suite']} {item['scale']}: refused. " + "; ".join(item["equivalence"]["mismatches"]))
    else:
        lines.append("None.")
    only = notes.get("only_one_side", [])
    if only:
        lines += ["", "Scales present on one side only (no comparison possible): " + ", ".join(only) + "."]
    for note in notes.get("loading", []):
        lines.append(f"- {note}")
    lines += ["", "## Limits", "",
              "- Single runs on single hosts: differences are descriptive and carry no significance estimate.",
              "- A failed, incomplete or missing cell is shown as such and is excluded from totals and ratios.",
              "- Equal manifests establish equal logical inputs; physical Parquet layout, compression and file counts may still differ for chunked scales.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a", type=Path, nargs="+", required=True, help="run root(s) for side A")
    parser.add_argument("--b", type=Path, nargs="+", required=True, help="run root(s) for side B")
    parser.add_argument("--a-label", default="A100")
    parser.add_argument("--b-label", default="H100")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-input-mismatch", action="store_true",
                        help="compare scales whose input manifests differ (marked in the report and CSV)")
    args = parser.parse_args()
    for label in (args.a_label, args.b_label):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", label):
            parser.error(f"label {label!r} must be alphanumeric (it names CSV columns)")
    if args.a_label == args.b_label:
        parser.error("--a-label and --b-label must differ")
    for root in [*args.a, *args.b]:
        if not root.expanduser().is_dir():
            parser.error(f"Result root is not a directory: {root}")
    sides = [load_side([r.expanduser().resolve() for r in roots], label)
             for roots, label in ((args.a, args.a_label), (args.b, args.b_label))]
    merged = [merged_host(side) for side in sides]
    hosts = (merged[0][0], merged[1][0])
    cpu_reasons = host_comparability(*hosts)
    facts = (engine_facts(sides[0]), engine_facts(sides[1]))
    comparisons, refused, only_one = [], [], []
    for key in sorted(set(sides[0]["groups"]) | set(sides[1]["groups"]), key=lambda k: (k[0], k[1])):
        group_a, group_b = sides[0]["groups"].get(key), sides[1]["groups"].get(key)
        if not (group_a and group_b):
            only_one.append(f"{key[0]} {(group_a or group_b)['scale']} ({args.a_label if group_a else args.b_label} only)")
            continue
        equivalence = manifest_equivalence(group_a["manifest"], group_b["manifest"])
        item = {"suite": key[0], "scale": group_a["scale"], "equivalence": equivalence, "allowed": False}
        if not equivalence["ok"]:
            if not args.allow_input_mismatch:
                refused.append(item)
                continue
            item["allowed"] = True
        item["rows"] = join_rows(args.a_label, args.b_label, group_a, group_b, cpu_reasons,
                                 "verified" if equivalence["ok"] else "mismatch-allowed")
        comparisons.append(item)
    notes = {"only_one_side": only_one, "loading": [f"{side['label']}: {n}" for side in sides for n in side["notes"]]}
    args.output.mkdir(parents=True, exist_ok=True)
    rows = [row for item in comparisons for row in item["rows"]]
    fields = ["suite", "scale", "engine", "query", "comparability", "input_equivalence"]
    for label in (args.a_label, args.b_label):
        fields += [f"{label}_{suffix}" for suffix in ("status", "median_seconds", "min_seconds", "max_seconds", "note")]
    fields.append(f"speedup_{args.b_label}_over_{args.a_label}")
    with (args.output / "hardware_comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (args.output / "HARDWARE_COMPARISON.md").write_text(report(args, sides, hosts, [m[1] for m in merged], facts, comparisons, refused, notes))
    (args.output / "input_equivalence.json").write_text(json.dumps({
        "labels": [args.a_label, args.b_label], "allow_input_mismatch": args.allow_input_mismatch,
        "hosts": {args.a_label: hosts[0], args.b_label: hosts[1]}, "cpu_engines_comparable": not cpu_reasons,
        "cpu_noncomparable_reasons": cpu_reasons,
        "scales": [{"suite": i["suite"], "scale": i["scale"], "compared": True, "allowed_mismatch": i["allowed"], **i["equivalence"]} for i in comparisons] +
                  [{"suite": i["suite"], "scale": i["scale"], "compared": False, **i["equivalence"]} for i in refused],
        "scales_on_one_side_only": only_one}, indent=2, allow_nan=False) + "\n")
    print(f"Compared {len(comparisons)} scale(s); refused {len(refused)}; report: {args.output.resolve() / 'HARDWARE_COMPARISON.md'}")
    for item in refused:
        print(f"Refused {item['suite']} {item['scale']}: " + "; ".join(item["equivalence"]["mismatches"]), file=sys.stderr)
    return 0 if comparisons else 1


if __name__ == "__main__":
    raise SystemExit(main())
