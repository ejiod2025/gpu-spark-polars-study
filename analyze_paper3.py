#!/usr/bin/env python3
"""Paper 3 analysis: energy and cost per query of CPU and GPU dataframe engines (implements docs/PAPER3_ENERGY_DESIGN.md sections 2, 4, 5).

    analyze_paper3.py --tree CAMPAIGN_TREE --output OUT --unblind        # after the campaign: every estimand, table and macro
    analyze_paper3.py --tree CAMPAIGN_TREE --output OUT --qa-only        # any time: gate outcomes only (design 4, 7.3)

CAMPAIGN_TREE holds every launch of every host: any directory below it with a campaign.json is a launch (run_campaign.py's root:
campaign.json, status.json, round-<k>[-repeat-<n>]/{gates.json, paper3-gates.json, <suite>/ ...}); the operator's launch-record.json beside
the copied root, if present, gives P-BILLED its launch and terminate times. See docs/PAPER3_ANALYSIS.md for what each output is and which
design section it implements.

Blinding (design 7.3). The full analysis computes engine-versus-engine and host-versus-host energy, power and cost aggregates, which no one may
look at until the campaign has ended: it refuses without --unblind, and refuses a tree in which any planned launch is not complete or stopped by rule
("paused" and "running" count as not finished; a planned launch missing from the tree blocks; --unblind does not override this, --declare-ended LABEL names a lost or
abandoned launch).
--qa-only reads only the files tools/paper3_campaign/p3_qa_view.py allows and holds no such aggregate. Run it on synthetic trees only until then.

Outputs under OUT: stats.json (nested, stable keys), stats_flat.json (dotted keys), stats.tex (\\defstat lines for paper/macros.tex), tables/*.csv|md|tex.
Exit status: 0 done, 2 the tree cannot be analysed (the message says why; nothing is computed from a tree that breaks a design rule).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from paper3_analysis import cells as cells_mod                                   # noqa: E402
from paper3_analysis import contrasts as contrasts_mod                           # noqa: E402
from paper3_analysis import descriptive, outputs, qa, tree as tree_mod            # noqa: E402
from paper3_analysis.constants import (CONTRASTS, DESIGN_DOC, EXPECTED_HOSTS, FORMAT, N1_NOTE, P_NEW, P_OLD, WATERMARK)  # noqa: E402
from paper3_analysis.tree import PLANNED_LAUNCHES, AnalysisError                  # noqa: E402

BLINDING = ("design 7.3: no engine-versus-engine or host-versus-host energy, power or cost aggregate may be computed or viewed until all rounds "
            "have completed or been stopped by the rules. Pass --unblind to say the campaign is over, or use --qa-only for gate outcomes.")
INPUT_FILES = ("campaign.json", "gates.json", "paper3-gates.json", "schedule.json", "results.json", "power.csv", "power_sources.json")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def code_sha256() -> str:
    """The analysis code itself (this file and the package), for the record that says which code produced the numbers."""
    digest = hashlib.sha256()
    for path in sorted([HERE / "analyze_paper3.py", *sorted((HERE / "paper3_analysis").glob("*.py"))]):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


CODE_FILES = ["analyze_paper3.py", *[f"paper3_analysis/{p.name}" for p in sorted((HERE / "paper3_analysis").glob("*.py"))]]
# what the analysis imports but does not own: shared with the campaign (and hashed in every campaign.json's source_sha256 for the first four) or the host config
DEPENDENCIES = ["power_telemetry.py", "paper3_gates.py", "paper3_campaign.py", "preflight.py", "analyze_campaign.py", "analyze_results.py", "paper3_hosts.json"]


def code_manifest() -> dict:
    """The exact files that constitute the analysis code, each with its sha256, and the combined hash the stats.json carries; plus what it imports."""
    files = {name: tree_mod.sha256_file(HERE / name) for name in CODE_FILES}
    deps = {name: tree_mod.sha256_file(HERE / name) for name in DEPENDENCIES if (HERE / name).is_file()}
    return {"analysis_code_files": files, "analysis_code_sha256": code_sha256(), "combined": "sha256 over, for each file in the order listed, its name, NUL, its bytes, NUL",
            "imported_not_owned": deps}


def inputs_digest(tree) -> dict:
    """sha256 of every input file the analysis reads (campaign.json, the accepted rounds' gate reports, schedules, results, power series)."""
    per_instance = {}
    for instance in tree.instances:
        lines = []
        roots = [instance.root] + [a.path for a in instance.accepted.values()]
        for root in roots:
            for base, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d not in ("plans",) and not (root == instance.root and tree_mod.ROUND_DIR.match(d)))
                for name in sorted(files):
                    if name in INPUT_FILES:
                        path = Path(base) / name
                        lines.append(f"{tree_mod.sha256_file(path)}  {path.relative_to(instance.root)}")
        per_instance[instance.label] = sha256_text("\n".join(sorted(set(lines))))
    return {"per_instance": per_instance, "all": sha256_text(json.dumps(per_instance, sort_keys=True))}


def host_blocks(tree) -> dict:
    out = {}
    for host, hd in sorted(tree.hosts.items()):
        instances = []
        for instance in hd.instances:
            verdicts = {}
            for attempts in instance.attempts.values():
                for a in attempts:
                    verdicts[str(a.verdict)] = verdicts.get(str(a.verdict), 0) + 1
            instances.append({"label": instance.label, "launch": instance.launch, "ordinal": instance.ordinal, "instance_type": instance.instance_type,
                              "p_billed_usd_per_hour": instance.billed_price, "accepted_rounds": sorted(instance.accepted),
                              "attempts_by_verdict": dict(sorted(verdicts.items())), "state": (instance.status or {}).get("state"),
                              "root": str(instance.root.relative_to(tree.root)) if tree.root in instance.root.parents or instance.root == tree.root else str(instance.root),
                              "paper3_launch_rounds": instance.paper3.get("launch_rounds"), "t_min_s": instance.paper3.get("t_min_s"),
                              "scope": instance.scope, "synthetic": instance.synthetic,
                              "cpu_model": ((instance.campaign.get("provenance") or {}).get("host") or {}).get("cpu_model"),
                              "gpu": ((instance.campaign.get("provenance") or {}).get("host") or {}).get("gpu")})
        out[host] = {"kind": hd.kind, "scope": hd.scope, "basis": hd.basis, "confirmatory_rounds": hd.confirmatory_rounds,
                     "analysis_rounds": hd.analysis_rounds, "sensitivity_rounds": hd.sensitivity_rounds,
                     "sensitivity_by_instance": {label: sorted(rounds) for label, rounds in sorted(hd.sensitivity.items())},
                     "squares": {str(sq): {"rounds": rounds, "complete": sq in hd.complete, "instance": hd.complete[sq].label if sq in hd.complete else None}
                                 for sq, rounds in hd.square_rounds.items()},
                     "instances": instances, "warnings": hd.warnings,
                     "status": "measured" if hd.has_complete_square else ("descriptive_no_complete_square" if hd.analysis else "no_accepted_round")}
    return out


def scope_matrix(tree) -> list:
    """Design 2.5 for the hosts of this tree: which scope each engine's energy carries and what it may be compared with."""
    rows = []
    for host, hd in sorted(tree.hosts.items()):
        instance = hd.instances[0]
        for engine in instance.engines:
            if hd.kind == "gpu":
                tag = "S1-module" if hd.scope == "module" else "S1-counter"
                what = "GPU engine: the GPU board" if hd.scope == "board" and engine.endswith("_gpu") else "CPU engine: the idle GPU's energy (the GPU paid for while idle)"
                if hd.scope == "module":
                    what = "whole Grace-Hopper module (Grace CPU + GPU + memory), never 'GPU energy' or 'node energy'"
            elif hd.kind == "cpu-metal":
                tag, what = "S2-pinned", "package + DRAM of the pinned socket (S2-all: every package and DRAM zone)"
            else:
                tag, what = "none", "time only: no energy measured on the right-sized VM"
            rows.append({"host": host, "engine": engine, "scope_tag": tag, "what": what})
    return rows


def strip_detail(contrasts: dict) -> dict:
    """The suite-level records of a contrast tree without their per-query and total detail (the sensitivities)."""
    def strip(node):
        if isinstance(node, dict) and "estimate" in node and "per_round" in node:
            return {k: v for k, v in node.items() if k not in ("q", "totals", "per_round", "queries")}
        return {k: strip(v) for k, v in node.items()} if isinstance(node, dict) else node
    return strip(contrasts)


def qa_for_tree(tree) -> dict:
    return qa.qa_from_gates([qa.qa_instance_at(i.root, i.host, i.launch) for i in tree.instances])


def guard_output(tree_root, output) -> None:
    """The outputs never go inside the tree they were computed from (a re-run would read its own tables as inputs)."""
    tree, out = Path(tree_root).resolve(), Path(output).resolve()
    if out == tree or tree in out.parents:
        raise AnalysisError(f"--output {output} is inside --tree {tree_root}: write the analysis elsewhere")


def host_counts(tree, store, main) -> dict:
    """Scalars per host for the paper: launches, rounds, attempts, sessions, G9 below-resolution cells, coverage outcomes (cells of the analysis's rounds)."""
    out = {}
    for host, hd in sorted(tree.hosts.items()):
        used = {(label, r) for r, label in main.subject.get(host, {}).items()}
        attempts = [a for inst in hd.instances for items in inst.attempts.values() for a in items]
        launches = {i.launch for i in hd.instances}
        cells = [c for c in store.cells if c.host == host and (c.instance, c.round) in used]
        sessions = [x for x in store.sessions if x.host == host]
        coverage = {"resource_exhaustion": 0, "unsupported_operation": 0, "integrity_failure": 0, "other_without_time": 0}
        for c in cells:
            if c.time_s is not None:
                continue
            if c.coverage in coverage:
                coverage[c.coverage] += 1
            elif c.failure_class and c.failure_class not in ("resource_exhaustion", "unsupported_operation"):
                coverage["integrity_failure"] += 1
            else:
                coverage["other_without_time"] += 1
        g9_reasons = {"window_below_10s": 0, "per_run_below_t_min": 0, "window_rule_violation": 0}
        by_engine = {}
        for c in cells:
            if c.g9_reasons:
                for reason in c.g9_reasons:
                    if reason in g9_reasons:
                        g9_reasons[reason] += 1
            node = by_engine.setdefault(c.engine, {"cells": 0, "below_resolution": 0})
            node["cells"] += 1
            node["below_resolution"] += bool(set(c.g9_reasons) & {"window_below_10s", "per_run_below_t_min", "window_rule_violation"})
        out[host] = {
            "launches_planned": len(PLANNED_LAUNCHES.get(host, ())), "launches_found": len(launches), "instances": len(hd.instances),
            "launches_lost_replaced": len(hd.instances) - len(launches),
            "rounds_planned": sum(len(r) for r in hd.square_rounds.values()), "rounds_accepted": sum(len(i.accepted) for i in hd.instances),
            "rounds_analysed": len(hd.analysis), "rounds_confirmatory": len(hd.confirmatory), "rounds_sensitivity": sum(len(v) for v in hd.sensitivity.values()),
            "round_attempts": len(attempts), "round_repeats": sum(1 for a in attempts if a.index > 0), "round_attempts_failed": sum(1 for a in attempts if a.verdict == "fail"),
            "squares_planned": len(hd.square_rounds), "squares_complete": len(hd.complete),
            "sessions_accepted": len(sessions), "sessions_analysed": sum(1 for x in sessions if (x.instance, x.round) in used),
            "cells_analysed": len(cells), "cells_without_time": sum(1 for c in cells if c.time_s is None),
            "coverage_outcomes": {**coverage, "total": sum(coverage.values())},
            "g9": {"cells_below_resolution": sum(1 for c in cells if set(c.g9_reasons) & {"window_below_10s", "per_run_below_t_min", "window_rule_violation"}),
                   **g9_reasons, "by_engine": dict(sorted(by_engine.items()))}}
    return out


def billing_by_host(billing: list) -> dict:
    """The reconciliation of 2.6 as scalars per host: billed hours, billed USD (hours x the price at launch, P-BILLED), the invoice and the gap if given."""
    out = {}
    for row in billing:
        node = out.setdefault(row["host"], {"hours": 0.0, "billed_usd": 0.0, "instances": 0, "instances_with_hours": 0, "invoice_usd": None, "gap": None,
                                            "gap_above_2pct": None, "credit_funded": row.get("credit_funded")})
        node["instances"] += 1
        if row["hours"] is not None and row["expected_usd"] is not None:
            node["instances_with_hours"] += 1
            node["hours"] += row["hours"]
            node["billed_usd"] += row["expected_usd"]
        if row["invoice_usd"] is not None:
            node["invoice_usd"] = (node["invoice_usd"] or 0.0) + row["invoice_usd"]
    for node in out.values():
        if node["invoice_usd"] is not None and node["billed_usd"]:
            node["gap"] = (node["invoice_usd"] - node["billed_usd"]) / node["billed_usd"]
            node["gap_above_2pct"] = abs(node["gap"]) > 0.02
    return out


def expected_launches(tree, synthetic: bool, given):
    """The planned launches the unblinded analysis waits for: the design's (A5: A100, H100, C-GCP, C-VM, plus any other host in the tree) for a real
    tree, none for a synthetic one unless a test names them."""
    if given is not None:
        return given
    if synthetic:
        return {}
    return {h: PLANNED_LAUNCHES[h] for h in sorted(set(EXPECTED_HOSTS) | set(tree.hosts)) if h in PLANNED_LAUNCHES}


def check_blinding(tree, expected, declared) -> list:
    """Raise unless every launch that must exist has finished or stopped by rule (design 7.3). --unblind does not override this; a launch that was lost or
    abandoned, or never will run, is declared by label (--declare-ended LABEL) and recorded."""
    problems = tree_mod.blinding_problems(tree, expected)
    known = {label for label, _ in problems} | {i.label for i in tree.instances}
    unknown = sorted(set(declared) - known)
    if unknown:
        raise AnalysisError(f"--declare-ended names nothing in this tree: {unknown} (instances: {sorted(i.label for i in tree.instances)})")
    blocking = [text for label, text in problems if label not in set(declared)]
    if blocking:
        raise AnalysisError("the campaign is not over (design 7.3); " + "; ".join(blocking) + ". " + BLINDING
                            + " A launch that was lost or will not run is declared with --declare-ended LABEL.")
    return [text for label, text in problems if label in set(declared)]


def run_analysis(tree_root, output, *, unblind: bool = False, allow_synthetic: bool = False, declare_ended=(), expected_launches_override=None,
                 allow_status_mismatch: bool = False, allow_design_mismatch: bool = False, invoices=None, extra_roots=(), write: bool = True) -> dict:
    if not unblind:
        raise AnalysisError(BLINDING)
    if write:
        guard_output(tree_root, output)
    tree = tree_mod.load_tree(Path(tree_root), allow_status_mismatch=allow_status_mismatch, allow_design_mismatch=allow_design_mismatch, extra_roots=extra_roots)
    synthetic = any(i.synthetic for i in tree.instances)
    declared_problems = check_blinding(tree, expected_launches(tree, synthetic, expected_launches_override), set(declare_ended))
    store = cells_mod.build_cells(tree, allow_synthetic=allow_synthetic)
    vm_check = contrasts_mod.c_vm_check(tree, store)
    analyzer = contrasts_mod.Analyzer(tree, store, vm_check=vm_check)
    main = contrasts_mod.main_selection(tree)
    contrasts = analyzer.gpu_host_contrasts(main)
    delta = analyzer.delta_star(main)
    break_even = analyzer.break_evens(contrasts)
    x4 = analyzer.x4(main)
    x5, x6 = analyzer.x5_x6(main)
    totals = analyzer.totals(main)
    unpaired = analyzer.cross_host_unpaired(main)
    sensitivity = {sel.name: strip_detail(analyzer.gpu_host_contrasts(sel)) for sel in contrasts_mod.sensitivity_selections(tree)}
    coverage = qa.coverage_matrix(tree, store, main)
    qa_stats = qa_for_tree(tree)
    qa_stats["read_latency_and_overhang"] = qa.latency_summary(store)
    qa_stats["idle_power_w"] = qa.idle_summary(store)
    qa_stats["crosscheck"] = qa.crosscheck_summary(store, main.subject, [h for h, hd in tree.hosts.items() if hd.kind == "gpu"])
    billing = contrasts_mod.billing_reconciliation(tree, invoices or {})
    hosts = host_blocks(tree)
    warnings = sorted(set(tree.warnings + store.warnings + analyzer.warnings))
    stats = {
        "_meta": {"format": FORMAT, "design": DESIGN_DOC, "analysis": "analyze_paper3.py", "synthetic": synthetic, "watermark": WATERMARK if synthetic else None,
                  "analysis_code_sha256": code_sha256(), "inputs_sha256": inputs_digest(tree),
                  "design_doc_sha256": sha256_text((HERE / DESIGN_DOC).read_text()) if (HERE / DESIGN_DOC).is_file() else None,
                  "hosts_in_tree": sorted(tree.hosts), "hosts_not_run": [h for h in EXPECTED_HOSTS if h not in tree.hosts],
                  "declared_ended": sorted(declare_ended), "declared_ended_problems": declared_problems, "warnings": warnings, "n1": N1_NOTE,
                  "price_tables": {"P-NEW": P_NEW, "P-OLD": P_OLD},
                  "p_billed_b1": {"usd_per_hour": analyzer.vm_billed, "source": "the C-VM launch's price at launch" if analyzer.vm_billed is not None
                                  else "no C-VM price: P-BILLED's B1 falls back to the P-NEW B1 price"}},
        "hosts": hosts, "counts": host_counts(tree, store, main), "scope_matrix": scope_matrix(tree), "prices": contrasts_mod.price_tables_used(tree),
        "price_ratios": contrasts_mod.price_ratios(analyzer), "billing": billing, "billing_by_host": billing_by_host(billing),
        "c_vm_check": {k: v for k, v in vm_check.items() if k != "reference_times"},
        **{cname: contrasts.get(cname, {}) for cname in CONTRASTS}, "x4": x4, "x5": x5, "x6": x6, "delta_star": delta, "break_even": break_even,
        "totals": totals, "sensitivity": sensitivity, "sensitivity_unpaired": unpaired,
        "descriptive": {"cv": descriptive.cv_summary(tree, store, main), "order_and_carryover": descriptive.order_summary(tree, store, main)},
        "coverage": {"rows": len(coverage), "cells_without_time": sum(1 for c in store.cells if c.time_s is None),
                     "cells_without_energy": sum(1 for c in store.cells if c.time_s is not None and not any(e.total_j is not None for e in c.energy.values()))},
        "qa": qa_stats,
    }
    stats = outputs.clean(stats)
    if not write:
        return stats
    output = Path(output)
    outputs.write_text(output / "stats.json", outputs.dumps(stats))
    flat = outputs.flatten(stats)
    outputs.write_text(output / "stats_flat.json", outputs.dumps(flat))
    outputs.write_text(output / "stats.tex", outputs.stats_tex(flat, synthetic))
    outputs.write_tables(output / "tables", contrasts, delta, break_even, qa_stats, tree, x4, x5, x6, totals, store, coverage, billing, synthetic)
    return stats


def run_qa(tree_root, output, *, extra_roots=(), synthetic: bool = False) -> dict:
    guard_output(tree_root, output)
    instances = qa.qa_instances(Path(tree_root), extra_roots)
    stats = outputs.clean({"_meta": {"format": FORMAT, "design": DESIGN_DOC, "mode": "qa-only",
                                     "files_read": "status.json, round-*/gates.json, round-*/paper3-gates.json (design 7.3: gate outcomes only)",
                                     "synthetic": synthetic, "watermark": WATERMARK if synthetic else None},
                           "instances": [{"host": i.host, "launch": i.launch, "state": i.status.get("state"), "attempts": len(i.attempts),
                                          "accepted_rounds": sorted(i.accepted())} for i in instances],
                           "qa": qa.qa_from_gates(instances)})
    output = Path(output)
    outputs.write_text(output / "qa.json", outputs.dumps(stats))
    outputs.write_qa_tables(output / "tables", stats["qa"], synthetic)
    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tree", type=Path, help="directory holding every launch of every host (any depth); campaign roots are found by campaign.json")
    parser.add_argument("--root", type=Path, action="append", default=[], help="an extra campaign root outside --tree (repeatable)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--recovery", type=Path, metavar="OUT", help="design 5.4: run the analysis on synthetic campaigns with injected known effects and write OUT/recovery.json "
                                                                      "(each estimate against its true value, interval coverage, classes and break-even brackets); needs no tree")
    parser.add_argument("--replicates", type=int, default=12, help="with --recovery: seeds for the coverage replicates (0: none)")
    parser.add_argument("--seed", type=int, default=20261123, help="with --recovery: the seed of everything synthetic")
    parser.add_argument("--manifest", action="store_true", help="print the files that constitute the analysis code with their sha256 and exit")
    parser.add_argument("--unblind", action="store_true", help="the campaign has ended (design 7.3); required for the full analysis")
    parser.add_argument("--qa-only", action="store_true", help="gate outcomes only, from the files the QA view allows; may be run while the campaign runs")
    parser.add_argument("--allow-synthetic", action="store_true", help="analyse a test tree (campaign.json formal false or marked synthetic); outputs are watermarked")
    parser.add_argument("--declare-ended", action="append", default=[], metavar="LABEL",
                        help="a launch that was lost, abandoned or will not run (instance label such as h100-l2, or a planned launch missing from the tree); "
                             "without it every planned launch must be complete or stopped by rule (design 7.3). Repeatable; recorded in stats.json")
    parser.add_argument("--allow-status-mismatch", action="store_true", help="continue when status.json lists other confirmatory rounds than the disk gives")
    parser.add_argument("--allow-design-mismatch", action="store_true", help="continue when the launches of a host carry different common_sha256 (recorded as a warning)")
    parser.add_argument("--invoices", type=Path, help="JSON {instance label: invoiced USD} for the P-BILLED reconciliation (2.6)")
    args = parser.parse_args(argv)
    if args.manifest:
        print(json.dumps(code_manifest(), indent=1))
        return 0
    if args.recovery is not None:
        from paper3_analysis import recovery
        report = recovery.run_recovery(args.recovery, run_analysis=run_analysis, replicates=args.replicates, seed=args.seed, code_sha256=code_sha256())
        for item in report["checks"]:
            print(("PASS " if item["passed"] else "FAIL ") + item["name"] + ": " + item["detail"])
        print(f"wrote {args.recovery / 'recovery.json'}; all_passed = {report['all_passed']}")
        return 0 if report["all_passed"] else 1
    if args.tree is None or args.output is None:
        parser.error("--tree and --output are required (or --recovery OUT, or --manifest)")
    try:
        if args.qa_only:
            run_qa(args.tree, args.output, extra_roots=args.root, synthetic=args.allow_synthetic)
        else:
            invoices = json.loads(args.invoices.read_text()) if args.invoices else {}
            run_analysis(args.tree, args.output, unblind=args.unblind, allow_synthetic=args.allow_synthetic, declare_ended=args.declare_ended,
                         allow_status_mismatch=args.allow_status_mismatch, allow_design_mismatch=args.allow_design_mismatch, invoices=invoices, extra_roots=args.root)
    except AnalysisError as exc:
        print(f"analyze_paper3.py: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
