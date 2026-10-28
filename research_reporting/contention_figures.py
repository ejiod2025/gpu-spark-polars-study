#!/usr/bin/env python3
"""Local, hash-bound figures for the separate descriptive contention experiment.

Final export requires a freshly reproduced terminal audit. --draft also accepts
an integrity-PASS PARTIAL audit and visibly marks every figure and text artifact.
No engine, remote host, API, source Parquet or isolated-study analysis is used.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import io
import json
import math
from pathlib import Path
import platform
import tempfile
import textwrap
from types import SimpleNamespace

try:
    from . import contention_replay as audit
except ImportError:  # Direct script invocation.
    import contention_replay as audit


PAIRS = {"A": ("spark_gpu", "polars_cpu_200"), "B": ("spark_cpu", "polars_gpu")}
LABELS = {"spark_gpu": "Spark GPU", "polars_cpu_200": "CPU Polars 2.0.0",
          "spark_cpu": "CPU Spark", "polars_gpu": "Polars GPU"}
QUERIES = {"baseline": ("q01", "q03", "q06", "q10", "q12", "q14"),
           "complex": ("cx_q09", "cx_q13", "cx_semi_anti", "cx_window", "cx_distinct", "cx_self_join")}
QUERY_LABELS = {**{q: "Q" + str(int(q[1:])) for q in QUERIES["baseline"]},
                "cx_q09": "Q9", "cx_q13": "Q13", "cx_semi_anti": "Semi / anti",
                "cx_window": "Window", "cx_distinct": "Exact distinct", "cx_self_join": "Self join"}
CPU, GPU = "#2677A5", "#C35C17"
STATUS_DRAFT = "DRAFT · provenance audit PARTIAL · not for publication"
OUTPUTS = {"AUDITOR_SOURCE.py", "PATH_ADAPTER.json", "QUERY_ACTIONS_DESCRIPTIVE.csv", "COMMON_QUERY_OVERLAP.csv"}
STYLE = {"font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 10,
         "axes.labelsize": 9, "axes.spines.top": False, "axes.spines.right": False,
         "svg.hashsalt": "h100-contention-figures-v1", "svg.fonttype": "none",
         "pdf.fonttype": 42, "figure.facecolor": "white", "savefig.facecolor": "white"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def disposition_gate(report, draft=False):
    """Lifecycle boundary only; never sufficient without fresh_replay below."""
    require(report.get("schema_version") == "h100-contention-replay-v1" and
            report.get("synthetic") is False, "A genuine contention replay is required")
    require(report.get("experiment_id") == audit.EXPERIMENT_ID and
            report.get("plan_sha256") == audit.PLAN_SHA and report.get("prestart_sha256") == audit.PRESTART_SHA,
            "Unknown contention plan/prestart identity")
    require(report.get("integrity_status") == "PASS" and report.get("findings") == [],
            "Audit integrity must PASS without findings")
    status = report.get("status")
    terminal = status in ("COMPLETE", "STOPPED_WITH_FAILURE")
    if terminal:
        require(report.get("publication_ready") is True, "Terminal audit is not publication ready")
        handoff = report.get("handoff", {})
        require(handoff.get("status") == "PASS" and handoff.get("no_action_overlap") is True and
                handoff.get("same_scheduler_resumed") is True and handoff.get("original_payload_verified") is True,
                "Terminal audit lacks actual original-study separation proof")
        require(report.get("terminal_driver", {}).get("terminal") is True, "Terminal driver proof is required")
    else:
        require(draft and status == "PARTIAL" and report.get("publication_ready") is False,
                "Final rendering requires a terminal publication-ready audit; PARTIAL requires --draft")
    return terminal and not draft


def fresh_replay(report):
    """Reproduce the receipt using the current auditor and the actual local bytes.

    Comparing every field except wall-clock audit timestamps prevents a forged
    status/coverage/CSV receipt from passing merely because its own hashes agree.
    The temporary replay is deleted; raw evidence and supplied receipt stay intact.
    """
    roots = {Path(v["archive_path"]).parent for v in report.get("snapshots", {}).values()}
    require(len(roots) == 1, "One genuine raw-backup directory is required")
    terminal, handoff = report.get("terminal_driver", {}), report.get("handoff", {})
    with tempfile.TemporaryDirectory(prefix="h100-contention-figure-replay-") as temporary:
        args = SimpleNamespace(archive=Path(report["canonical_directory"]), raw_backups=roots.pop(),
            final_metadata=Path(terminal["path"]).parent if terminal.get("path") else None,
            handoff=Path(handoff["directory"]) if handoff.get("directory") else None,
            held_original=Path(handoff["held_journal_path"]).parent if handoff.get("held_journal_path") else None,
            original_attempt=Path(handoff["original_attempt_directory"]) if handoff.get("original_attempt_directory") else None,
            output=Path(temporary) / "replay")
        reproduced = audit.replay(args)
    ignored = {"started_at_utc", "completed_at_utc", "analysis_runtime"}
    require({k: v for k, v in report.items() if k not in ignored} ==
            {k: v for k, v in reproduced.items() if k not in ignored},
            "Receipt differs from freshly reproduced current-source offline audit")


def load_audit(directory, draft=False):
    directory = Path(directory).absolute()
    inputs = audit.Inputs()
    report = inputs.json(directory / "CONTENTION_REPLAY.json")
    final = disposition_gate(report, draft)
    source = Path(audit.__file__).absolute()
    require(report.get("audit_source_sha256") == inputs.file(source) == inputs.file(directory / "AUDITOR_SOURCE.py"),
            "Auditor source is stale or does not match the current source")
    hashes = report.get("output_files_sha256", {})
    require(set(hashes) == OUTPUTS and {p.name for p in directory.glob("*.csv")} ==
            {"QUERY_ACTIONS_DESCRIPTIVE.csv", "COMMON_QUERY_OVERLAP.csv"}, "Unexpected audit output/CSV inventory")
    for name, digest in hashes.items():
        require(inputs.file(directory / name) == digest, "Audit output hash differs: " + name)
    for name, expected in (("canonical_plan", audit.PLAN_SHA), ("canonical_prestart", audit.PRESTART_SHA)):
        ref = report.get(name, {})
        require(ref.get("sha256") == expected and inputs.file(ref["path"]) == expected, "Canonical input hash differs")
    fresh_replay(report)
    # Re-read and retain the exact bytes after the independent replay.
    actions = list(csv.DictReader(io.StringIO((directory / "QUERY_ACTIONS_DESCRIPTIVE.csv").read_text())))
    overlaps = list(csv.DictReader(io.StringIO((directory / "COMMON_QUERY_OVERLAP.csv").read_text())))
    plan = inputs.json(report["canonical_plan"]["path"])
    inputs.recheck()
    return {"report": report, "actions": actions, "overlaps": overlaps, "plan": plan,
            "inputs": inputs, "final": final, "draft": draft}


def phase_rows(data, phase, paired_only=False):
    return [r for r in data["actions"] if r["phase"] == "measured" and r["wave_id"].startswith(phase + "-")
            and (not paired_only or r["paired_condition"] == "True")]


def color(config):
    return GPU if config in ("spark_gpu", "polars_gpu") else CPU


def badge(data):
    if data["draft"]:
        return STATUS_DRAFT if data["report"]["status"] == "PARTIAL" else "DRAFT · not for publication"
    return "Separate descriptive extension · " + data["report"]["status"]


def decorate(fig, data, title, note):
    fig.suptitle(title, x=.045, ha="left", y=.985, fontsize=14, weight="bold")
    fig.text(.045, .945, badge(data), color="#9E3024" if data["draft"] else "#333333", fontsize=10, weight="bold")
    fig.text(.045, .025, textwrap.fill(note, 157), va="bottom", fontsize=8, linespacing=1.5)
    if data["draft"]:
        fig.text(.52, .51, "DRAFT", fontsize=90, color="#A33324", alpha=.08,
                 ha="center", va="center", rotation=22, zorder=20)


def duration_figure(data, phase, pair):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    participants = PAIRS[pair]
    rows = [r for r in phase_rows(data, phase, True) if r["config_id"] in participants]
    scales = ("30", "100") if phase == "main" else ("30",)
    fig, axes = plt.subplots(2, len(scales), figsize=(11.6, 8.4), squeeze=False)
    for i, suite in enumerate(("baseline", "complex")):
        for j, scale in enumerate(scales):
            ax = axes[i, j]
            queries = [q for q in QUERIES[suite] if not (pair == "B" and suite == "complex" and q in audit.UNSUPPORTED)]
            for r in rows:
                if r["suite"] != suite or r["scale"] != scale:
                    continue
                index = participants.index(r["config_id"])
                offset = (-.14, .14)[index] + (-.035 if r["repetition"] == "1" else .035)
                ax.scatter(float(r["seconds"]), queries.index(r["query"]) + offset,
                    marker="o" if r["repetition"] == "1" else "s", s=32,
                    facecolors=color(r["config_id"]) if r["repetition"] == "1" else "white",
                    edgecolors=color(r["config_id"]), linewidths=1.1, zorder=3)
            ax.set_xscale("log")
            ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
            ax.xaxis.set_minor_formatter(NullFormatter())
            ax.set_yticks(range(len(queries)), [QUERY_LABELS[q] for q in queries])
            ax.set_ylim(len(queries) - .5, -.5)
            ax.set_title(f"{suite.capitalize()} · SF{scale}", loc="left", pad=9)
            ax.set_xlabel("Measured action duration (s; logarithmic scale)")
            ax.grid(axis="x", alpha=.2, which="both", linewidth=.6)
            if not any(r["suite"] == suite and r["scale"] == scale for r in rows):
                ax.text(.5, .5, "No measured common actions observed", transform=ax.transAxes, ha="center")
    handles = [Line2D([], [], marker="o", linestyle="none", color=color(c), label=LABELS[c]) for c in participants]
    handles += [Line2D([], [], marker=m, linestyle="none", markerfacecolor=f, color="#444444", label=f"Repetition {n}")
                for n, m, f in ((1, "o", "#444444"), (2, "s", "white")) if phase == "main" or n == 1]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(.04, .923), ncol=len(handles), frameon=False)
    note = ("Each point is one measured action from a fresh concurrent wave; no averaging, confidence intervals or tests. "
            + ("Expansion has two repetitions per cell; pilot is shown separately. " if phase == "main" else "Pilot has one repetition at SF30 and is not pooled with expansion. ")
            + "Common queries only. Pair B complex excludes Q13 and window: its CPU-only measurements remain unpaired diagnostics. "
            "Pairs have different competitors and do not rank the two GPU engines. Action timers exclude setup and rendezvous waits.")
    decorate(fig, data, f"{'Expansion' if phase == 'main' else 'Pilot'} · pair {pair}: " + " + ".join(LABELS[c] for c in participants), note)
    fig.subplots_adjust(left=.15, right=.975, bottom=.17, top=.845, hspace=.5, wspace=.4)
    return fig, note


def timeline_segments(data, wave):
    """Exact measured UTC intervals, including CPU-only diagnostic actions."""
    rows = [r for r in phase_rows(data, wave.split("-")[0]) if r["wave_id"] == wave]
    if not rows:
        return []
    origin = min(datetime.fromisoformat(r["action_started_at_utc"]).timestamp() for r in rows)
    return [{"config": r["config_id"], "query": r["query"], "paired": r["paired_condition"] == "True",
             "start": datetime.fromisoformat(r["action_started_at_utc"]).timestamp() - origin,
             "width": datetime.fromisoformat(r["action_ended_at_utc"]).timestamp() -
                      datetime.fromisoformat(r["action_started_at_utc"]).timestamp()} for r in rows]


def timeline_figure(data, phase):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    attempted = {w["wave_id"] for w in data["report"]["waves"]}
    waves = [w for w in data["plan"][phase] if w["wave_id"] in attempted]
    # Every attempted wave appears, including an honest failed wave with no action.
    cols = 4 if phase == "main" else 2
    nrows = max(1, math.ceil(len(waves) / cols))
    fig, axes = plt.subplots(nrows, cols, figsize=(15.8, 3.0 * nrows + 1.8), squeeze=False)
    segments = {w["wave_id"]: timeline_segments(data, w["wave_id"]) for w in waves}
    maximum = max((s["start"] + s["width"] for ss in segments.values() for s in ss), default=1)
    for ax, wave in zip(axes.flat, waves):
        pair = "A" if wave["pair"] == "spark_gpu__polars_cpu_200" else "B"
        ss = segments[wave["wave_id"]]
        for s in ss:
            lane = 1 if s["config"] in ("spark_gpu", "polars_gpu") else 0
            # No minimum width, edge stroke or endpoint marker widens fast actions.
            ax.broken_barh([(s["start"], s["width"])], (lane - .21, .42),
                facecolors=color(s["config"]), edgecolors="none", linewidth=0,
                hatch="////" if not s["paired"] else None)
            label = s["query"].removeprefix("cx_").replace("self_join", "self").replace("semi_anti", "semi")
            if s["width"] > maximum * (len(label) * .014 + .02):
                ax.text(s["start"] + s["width"] / 2, lane, label, ha="center", va="center", fontsize=6.5, color="white", clip_on=True)
        ax.set_yticks([0, 1], ["CPU", "GPU"])
        ax.set_ylim(1.5, -.6)
        ax.set_xlim(-maximum * .015, maximum * 1.025)
        ax.set_xlabel("Seconds from first measured action")
        ax.set_title(f"{wave['wave_id'].split('-')[1]} · {pair} · {wave['suite']} · SF{wave['scale']} · r{wave['repetition']}", loc="left", fontsize=9)
        ax.grid(axis="x", alpha=.2, linewidth=.5)
        if not ss:
            ax.text(.5, .5, "No measured action observed", transform=ax.transAxes, ha="center", fontsize=8)
    for ax in list(axes.flat)[len(waves):]:
        ax.set_visible(False)
    fig.legend(handles=[Patch(color=CPU, label="CPU participant"), Patch(color=GPU, label="GPU participant"),
                        Patch(facecolor="#999999", hatch="////", label="Unpaired CPU diagnostic")],
               loc="upper left", bbox_to_anchor=(.04, .923), ncol=3, frameon=False)
    note = ("Every attempted " + ("expansion" if phase == "main" else "pilot") + " wave appears in planned execution order. "
            "Bars retain actual measured UTC start/end intervals and gaps, with a shared linear scale; fast bars are never widened. "
            "Zero is each wave's first measured action, not process startup. CPU/GPU lanes show concurrent participants; warmups are omitted. "
            "A: Spark GPU + CPU Polars 2.0.0. B: CPU Spark + Polars GPU. "
            "Hatched pair-B Q13/window CPU actions are unpaired diagnostics. Labels appear only where space permits. "
            "Both-clock common-query overlap is independently checked in the source audit; the timeline is not a process-lifetime proxy.")
    decorate(fig, data, ("Expansion" if phase == "main" else "Pilot") + " · actual measured action intervals", note)
    fig.subplots_adjust(left=.065, right=.98, bottom=.16 if phase == "pilot" else .115,
                        top=.825 if phase == "pilot" else .87, hspace=.85, wspace=.4)
    return fig, note


def render(directory, output, draft=False, formats=("svg", "png", "pdf")):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output = Path(output).resolve()
    require(not output.exists(), "Figure output must be a new directory")
    require(formats and set(formats) <= {"svg", "png", "pdf"}, "Unsupported export format")
    data = load_audit(directory, draft)
    renderer_digest = data["inputs"].file(__file__)
    protected = [Path(directory).absolute(), Path(data["report"]["canonical_directory"])]
    protected += [Path(v["directory"]) for v in data["report"]["snapshots"].values()]
    protected += [Path(v["archive_path"]).parent for v in data["report"]["snapshots"].values()]
    handoff = data["report"].get("handoff", {})
    protected += [Path(handoff[k]) for k in ("directory", "original_attempt_directory") if handoff.get(k)]
    if handoff.get("held_journal_path"):
        protected.append(Path(handoff["held_journal_path"]).parent)
    if data["report"].get("terminal_driver", {}).get("path"):
        protected.append(Path(data["report"]["terminal_driver"]["path"]).parent)
    require(not any(output.is_relative_to(p) for p in protected), "Output cannot be inside immutable evidence inputs")
    require(matplotlib.__version__ == "3.10.8", "Use the pinned Matplotlib 3.10.8 analysis environment")
    output.mkdir(parents=True)
    captions = ["# " + badge(data), "", "Separate H100 contention experiment. Descriptive observations only; no primary-400 inference.", ""]
    with plt.rc_context(STYLE):
        for phase in ("pilot", "main"):
            if not any(w["wave_id"].startswith(phase + "-") for w in data["report"]["waves"]):
                continue
            figures = [(f"{phase}_pair_{pair}_common_actions", duration_figure(data, phase, pair)) for pair in PAIRS]
            figures.append((f"{phase}_actual_action_timeline", timeline_figure(data, phase)))
            for stem, (fig, caption) in figures:
                captions.extend(["## " + stem, "", badge(data) + ". " + caption, ""])
                for extension in formats:
                    metadata = {"Date": None} if extension == "svg" else (
                        {"CreationDate": None, "ModDate": None} if extension == "pdf" else {})
                    fig.savefig(output / f"{stem}.{extension}", dpi=190, metadata=metadata)
                plt.close(fig)
    (output / "CAPTIONS.md").write_text("\n".join(captions))
    data["inputs"].recheck()
    report = data["report"]
    manifest = {"schema_version": "h100-contention-figures-v1", "status_label": badge(data),
        "draft": draft, "final": data["final"], "publication_ready": data["final"], "synthetic": False,
        "audit_status": report["status"], "audit_publication_ready": report["publication_ready"],
        "plan_sha256": audit.PLAN_SHA, "prestart_sha256": audit.PRESTART_SHA,
        "auditor_source_sha256": report["audit_source_sha256"], "renderer_source_sha256": renderer_digest,
        "input_files_sha256": data["inputs"].hashes,
        "reproduced_audit_input_files_sha256": report["input_files_sha256"],
        "audit_counts": report["counts"], "pending_reasons": report.get("handoff", {}).get("pending_reasons", []),
        "phase_measured_rows": {p: dict(Counter(r["paired_condition"] for r in phase_rows(data, p))) for p in ("pilot", "main")},
        "scope": "Separate descriptive extension. Pilot and expansion remain separate; no averaged repetitions, CIs, p-values or GPU-engine ranking.",
        "timeline_endpoint": "Actual measured UTC action intervals; no artificial minimum bar width",
        "runtime": {"python": platform.python_version(), "matplotlib": matplotlib.__version__},
        "output_files_sha256": {p.name: audit.sha(p) for p in sorted(output.iterdir()) if p.is_file()}}
    audit.write_json(output / "FIGURE_MANIFEST.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args(argv)
    manifest = render(args.audit, args.output, args.draft)
    print(json.dumps({"output": str(args.output.absolute()), "final": manifest["final"],
                      "audit_status": manifest["audit_status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
