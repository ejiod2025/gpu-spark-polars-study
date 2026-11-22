#!/usr/bin/env python3
"""The QA view of a Paper 3 campaign root: what the design allows the operator to look at before the campaign ends (section 7.3).

  p3_qa_view.py ROOT [--json] [--detail]

Prints progress (state, session counts, the pooled ETA), and per round the gate outcomes (G1-G5 verdict, G6-G12 counts, which
sessions failed which gate). It reads only status.json, gates.json, paper3-gates.json and host-exclusivity-*.json, and refuses
every other path by construction (read() checks the name against ALLOWED): results.json, schedule.json, gpu.csv, power.csv,
host.csv and the per-session records hold timings, energy and power, and no engine-versus-engine or host-versus-host energy,
power or cost aggregate may be looked at until all rounds are done or stopped (design 7.3). The pooled ETA is a sum over all
engines; per-engine durations are never printed. --detail adds the text of the gate problems (integrity findings, no values of
the study).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ALLOWED = re.compile(r"^(status\.json|round-\d+(-repeat-\d+)?/(gates|paper3-gates|host-exclusivity-(start|end))\.json)$")


class ForbiddenRead(Exception):
    """A file the QA view must not open (design 7.3)."""


def read(root: Path, relative: str):
    if not ALLOWED.match(relative):
        raise ForbiddenRead(f"{relative}: not a QA file (design 7.3 allows gate outcomes, counts and coverage only)")
    try:
        return json.loads((root / relative).read_text())
    except (OSError, ValueError):
        return None


def attempt_dirs(root: Path) -> list:
    names = [p.name for p in root.iterdir() if p.is_dir() and re.fullmatch(r"round-\d+(-repeat-\d+)?", p.name)] if root.is_dir() else []
    return sorted(names, key=lambda n: tuple(int(x) for x in re.findall(r"\d+", n)))


def gate_summary(root: Path, name: str, detail: bool) -> dict:
    gates = read(root, f"{name}/gates.json") or {}
    paper3 = gates.get("paper3") or {}
    out = {"attempt": name, "verdict": gates.get("verdict"), "reasons": gates.get("reasons") if detail else len(gates.get("reasons") or []),
           "design_conformance_passed": (gates.get("design_conformance") or {}).get("passed"),
           "preflight_verdict": (gates.get("preflight") or {}).get("verdict"),
           "paper3": {"evaluated": paper3.get("evaluated"), "round_failed": paper3.get("round_failed"),
                      "gates": paper3.get("gates"), "ineligible_keys": paper3.get("ineligible_keys"),
                      "failing": sorted({f.split(":")[0] for f in paper3.get("round_failures") or []}),
                      **({"failure_text": paper3.get("round_failures")} if detail else {})} if paper3 else None}
    report = read(root, f"{name}/paper3-gates.json")
    if report:
        out["paper3_report_gates"] = {g: {k: v for k, v in c.items() if k != "consequence"} for g, c in (report.get("gates") or {}).items()}
    for label in ("start", "end"):
        capture = read(root, f"{name}/host-exclusivity-{label}.json")
        if capture:
            scheduled = capture.get("scheduled") or {}
            count = sum(len(v) for v in scheduled.values() if isinstance(v, list))
            out[f"exclusivity_{label}"] = {"scheduled_items": count, **({"scheduled": scheduled, "gpu_compute_apps": capture.get("gpu_compute_apps")} if detail else {})}
    return out


def view(root: Path, detail: bool = False) -> dict:
    status = read(root, "status.json") or {}
    current = status.get("current") or {}
    eta = status.get("eta") or {}
    return {"root": root.name, "state": status.get("state"), "message": status.get("message"), "updated_utc": status.get("updated_utc"),
            "sessions": status.get("sessions"),
            "current": {k: current.get(k) for k in ("phase", "round", "suite", "scale", "engine", "last_session", "last_status") if k in current},
            "pooled_eta": {"remaining_seconds": eta.get("remaining_seconds"), "eta_utc": eta.get("eta_utc")},
            "paper3": {k: (status.get("paper3") or {}).get(k) for k in ("host", "launch", "squares_complete", "confirmatory_rounds", "sensitivity_only_rounds")},
            "rounds": {uid: {k: v.get(k) for k in ("sessions", "attempts")} | {"accepted": v.get("accepted_dir") is not None}
                       for uid, v in (status.get("rounds") or {}).items()},
            "attempts": [gate_summary(root, name, detail) for name in attempt_dirs(root)]}


def text(v: dict) -> str:
    lines = [f"{v['root']}: state {v['state']} ({v['message'] or '-'}), updated {v['updated_utc']}"]
    s = v["sessions"] or {}
    lines.append(f"  sessions {s.get('completed')}/{s.get('total')}, failed among those run {s.get('failed_among_run')}, run including repeats {s.get('run_including_repeats')}")
    if v["current"]:
        lines.append("  now: " + ", ".join(f"{k} {x}" for k, x in v["current"].items()))
    eta = v["pooled_eta"]
    if eta.get("eta_utc"):
        lines.append(f"  pooled ETA (all engines together): {eta['eta_utc']}")
    p3 = v["paper3"]
    if p3.get("host"):
        lines.append(f"  squares complete {p3['squares_complete']}, confirmatory rounds {p3['confirmatory_rounds']}, sensitivity only {p3['sensitivity_only_rounds']}")
    for a in v["attempts"]:
        lines.append(f"  {a['attempt']}: gate verdict {a['verdict']}; preflight {a['preflight_verdict']}; conformance passed {a['design_conformance_passed']}")
        gates = (a.get("paper3") or {}).get("gates") or a.get("paper3_report_gates") or {}
        if gates:
            lines.append("    " + "  ".join(f"{g} p{c.get('pass', 0)}/f{c.get('fail', 0)}/n{c.get('not_evaluable', 0)}" for g, c in sorted(gates.items())))
        failing = (a.get("paper3") or {}).get("failing")
        if failing:
            lines.append(f"    round-failing findings: {', '.join(failing)}")
        for label in ("start", "end"):
            if f"exclusivity_{label}" in a:
                lines.append(f"    host exclusivity {label}: {a[f'exclusivity_{label}']['scheduled_items']} scheduled item(s)")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--detail", action="store_true")
    args = parser.parse_args(argv)
    result = view(args.root, args.detail)
    print(json.dumps(result, indent=2) if args.json else text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
