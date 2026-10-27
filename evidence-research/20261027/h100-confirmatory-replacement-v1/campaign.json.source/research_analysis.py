#!/usr/bin/env python3
"""Analyze the prospective H100 campaign without pooling its exploratory pilots.

Usage (no GPU, JVM, or input Parquet files are needed):
    python research_analysis.py --campaign campaign.json \
        --validation validation.json --output analysis
    python research_analysis.py --campaign campaign.json \
        --validation validation.json --artifacts-root ./archived-runs --output analysis

Accepted inputs are the immutable research-campaign-v1 plan and its .sha256
sidecar, the matching research-campaign-journal-v1 journal, all 400 session.json
and results.json artifacts, and a complete research-validation-v1 PASS audit.
For example, one entry in campaign.sessions is:
    {"session_id": "block-01-baseline-sf1-polars_cpu_200",
     "block_id": "block-01", "cell_id": "baseline-sf1", "suite": "baseline",
     "scale": "1", "config_id": "polars_cpu_200", "engine": "polars",
     "python": "/env/cpu200/bin/python", "query_seed": 123, "position": 1,
     "output_root": "/campaign/block-01-baseline-sf1-polars_cpu_200"}
Its journal item must be succeeded with exactly one attempt, whose artifacts
contain results_path, results_sha256, and schedule_sha256. The corresponding
session.json binds that exact frozen session and attempt to campaign_sha256.
Every supported query has warmup/0 and measured/1,2,3 action records with
positive seconds and host_result_seconds; the audit supplies coverage and
independent-oracle comparisons. CPU configurations are identified by config_id,
not the shared engine name. Missing, duplicate, failed, or changed evidence is
an error, never a complete-case exclusion. --artifacts-root changes file access
only: the archived journal and session records retain their original paths.

The independent sample is ten paired blocks. Inner actions are summarized by
their session median and never counted as additional replications. Primary
tests use the 26 contrasts frozen in the plan, with a single Holm family.
Pointwise t intervals assume independent, approximately normal block log
ratios; ten blocks provide no power guarantee. Query and host-result intervals
are descriptive, with no additional significance tests or winner selection.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import statistics

from scipy.stats import t

import complex_workloads
from research_campaign import CELLS, CONFIG_ENGINES, load_plan, primary_contrasts, sha256
import workloads

BLOCKS = tuple(f"block-{i:02d}" for i in range(1, 11))
ENDPOINTS = ("seconds", "host_result_seconds")
QUERIES = {"baseline": tuple(workloads.QUERY_IDS), "complex": tuple(complex_workloads.QUERY_IDS)}
UNSUPPORTED = frozenset({"cx_q13", "cx_window"})
PAIRS = (("polars_cpu_200", "spark_gpu"), ("spark_cpu", "spark_gpu"),
         ("polars_cpu_144", "polars_gpu"), ("polars_cpu_200", "polars_gpu"))


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key {key!r}: {path}")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError(f"Non-finite JSON value {value}: {path}")

    return json.loads(Path(path).read_text(), object_pairs_hook=pairs, parse_constant=nonfinite)


def unique_index(rows, key, label):
    result = {}
    for row in rows:
        identity = row[key]
        if identity in result:
            raise ValueError(f"Duplicate {label}: {identity}")
        result[identity] = row
    return result


def path_mapper(plan, artifacts_root=None):
    """Rebase artifact access, while comparing original metadata byte for byte."""
    frozen = Path(plan["output_root"])
    local = frozen if artifacts_root is None else Path(artifacts_root)

    def mapped(path):
        try:
            relative = Path(path).relative_to(frozen)
        except ValueError as exc:
            raise ValueError(f"Artifact path outside frozen output_root: {path}") from exc
        if ".." in relative.parts:
            raise ValueError(f"Unsafe artifact path: {path}")
        return local / relative

    return mapped


def validate_design(plan):
    if plan.get("settings", {}).get("runs") != 3:
        raise ValueError("The fixed design requires three nested measured actions")
    if tuple(sorted(b["block_id"] for b in plan["blocks"])) != BLOCKS:
        raise ValueError("All ten planned blocks are required exactly once")
    configs = unique_index(plan["configs"], "id", "configuration")
    if set(configs) != set(CONFIG_ENGINES):
        raise ValueError("The five planned configurations are required")
    sessions = unique_index(plan["sessions"], "session_id", "planned session")
    keys = [(s["block_id"], s["suite"], s["scale"], s["config_id"]) for s in sessions.values()]
    expected = {(block, suite, scale, config) for block in BLOCKS for suite, scale in CELLS
                for config in CONFIG_ENGINES}
    if len(keys) != 400 or set(keys) != expected or any(n != 1 for n in Counter(keys).values()):
        raise ValueError("Missing or duplicate planned session cells; all 400 are required")
    observed = unique_index(plan.get("primary_contrasts", []), "contrast_id", "primary contrast")
    prescribed = {c["contrast_id"]: c for c in primary_contrasts()}
    if observed != prescribed:
        raise ValueError("Primary contrast family differs from the frozen 26-test protocol")
    declarations = plan.get("analysis_predeclarations", {})
    if (declarations.get("primary_family_size") != 26 or declarations.get("multiplicity") != "Holm"
            or declarations.get("primary_endpoint") != "seconds"
            or declarations.get("familywise_alpha") != 0.05):
        raise ValueError("Analysis predeclarations differ from the protocol")


def summarize_session(session, value, audit):
    """Validate nested action cardinalities and retain every unsupported query."""
    queries = QUERIES[session["suite"]]
    scale = value.get("scale_factor")
    scale_matches = (not isinstance(scale, bool) and isinstance(scale, (int, float))
                     and math.isfinite(scale) and float(scale) == float(session["scale"]))
    if (value.get("engine") != session["engine"] or value.get("suite") != session["suite"]
            or not scale_matches or value.get("runs") != 3
            or value.get("queries") != list(queries) or value.get("query_seed") != session["query_seed"]
            or not value.get("completed_at_utc") or value.get("failure")):
        raise ValueError(f"Worker identity/completion differs: {session['session_id']}")
    coverage = audit.get("coverage", {})
    if set(coverage) != set(queries):
        raise ValueError(f"Audit coverage differs: {session['session_id']}")
    records = value.get("records", [])
    keyed = {}
    for record in records:
        key = (record["query"], record["phase"], record["trial"])
        if key in keyed:
            raise ValueError(f"Duplicate action: {session['session_id']}/{key}")
        if key[0] not in queries:
            raise ValueError(f"Unknown action query: {key[0]}")
        for endpoint in ENDPOINTS:
            number = record.get(endpoint)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
                raise ValueError(f"Invalid {endpoint}: {session['session_id']}/{key}")
        if record["host_result_seconds"] < record["seconds"]:
            raise ValueError("Host-result endpoint precedes the action endpoint")
        keyed[key] = record
    errors = value.get("query_errors", [])
    if any(error.get("query") not in queries for error in errors):
        raise ValueError("Unknown query failure")
    query_summaries = {}
    for query in queries:
        observed = {key for key in keyed if key[0] == query}
        expected = {(query, "warmup", 0), *((query, "measured", trial) for trial in (1, 2, 3))}
        failures = [e for e in errors if e["query"] == query]
        status = coverage[query].get("status")
        if status == "unsupported":
            if (session["config_id"] != "polars_gpu" or session["suite"] != "complex"
                    or query not in UNSUPPORTED or observed or len(failures) != 1
                    or (failures[0].get("phase"), failures[0].get("trial")) != ("warmup", 0)):
                raise ValueError(f"Invalid unsupported outcome: {session['session_id']}/{query}")
            query_summaries[query] = {"status": "unsupported", "endpoints": {}, "errors": failures}
        elif status == "complete" and observed == expected and not failures:
            endpoints = {}
            for endpoint in ENDPOINTS:
                values = [keyed[(query, "measured", trial)][endpoint] for trial in (1, 2, 3)]
                endpoints[endpoint] = {"median": statistics.median(values), "min": min(values),
                                       "max": max(values), "trials": values}
            query_summaries[query] = {"status": "complete", "endpoints": endpoints}
        else:
            raise ValueError(f"Incomplete or failed query: {session['session_id']}/{query}")
    complete = all(q["status"] == "complete" for q in query_summaries.values())
    # Even a later runtime accepting these expressions cannot silently redefine this protocol.
    if session["config_id"] == "polars_gpu" and session["suite"] == "complex":
        if {q for q, result in query_summaries.items() if result["status"] == "unsupported"} != UNSUPPORTED:
            raise ValueError("GPU complex coverage changed; a new protocol is required")
        complete = False
    expected_keys = {(r["query"], r["phase"], r["trial"]) for r in records}
    comparisons = audit.get("comparisons", [])
    comparison_keys = [(r["query"], r["phase"], r["trial"]) for r in comparisons]
    if (len(comparison_keys) != len(expected_keys) or set(comparison_keys) != expected_keys
            or not all(r.get("passed") is True for r in comparisons)):
        raise ValueError(f"Oracle audit does not cover every successful action: {session['session_id']}")
    return {**{k: session[k] for k in ("session_id", "block_id", "cell_id", "suite", "scale", "config_id", "position")},
            "queries": query_summaries, "coverage": f"{sum(q['status'] == 'complete' for q in query_summaries.values())}/6",
            "suite_status": "complete" if complete else "unsupported", "suite_scores": {
                endpoint: math.fsum(q["endpoints"][endpoint]["median"] for q in query_summaries.values())
                if complete else None for endpoint in ENDPOINTS}}


def load_evidence(campaign, validation, artifacts_root=None):
    read_json(campaign)  # Reject duplicate JSON keys before the campaign loader checks the design.
    plan, digest = load_plan(campaign)
    validate_design(plan)
    mapped = path_mapper(plan, artifacts_root)
    journal_path = mapped(Path(plan["output_root"]) / "journal.json")
    journal = read_json(journal_path)
    audit = read_json(validation)
    if (audit.get("schema_version") != "research-validation-v1" or audit.get("status") != "PASS"
            or audit.get("campaign_sha256") != digest or audit.get("journal_sha256") != sha256(journal_path)
            or audit.get("planned_sessions") != 400 or audit.get("audited_sessions") != 400
            or audit.get("pending_sessions") or audit.get("failed_sessions")):
        raise ValueError("A complete PASS validation tied to this campaign and journal is required")
    if (journal.get("schema_version") != "research-campaign-journal-v1"
            or journal.get("campaign_sha256") != digest):
        raise ValueError("Journal identity differs from campaign")
    if (set(audit.get("oracle_files", {})) != set(plan["oracles"])
            or any(audit["oracle_files"][cell].get("sha256") != oracle["sha256"]
                   for cell, oracle in plan["oracles"].items())):
        raise ValueError("Validation oracle hashes differ from the frozen campaign")
    planned = unique_index(plan["sessions"], "session_id", "planned session")
    audited = unique_index(audit.get("sessions", []), "session_id", "validated session")
    if set(journal.get("sessions", {})) != set(planned) or set(audited) != set(planned):
        raise ValueError("Missing/extra journal or validated sessions; no sessions may be dropped")
    summaries = []
    for session in plan["sessions"]:
        identity = session["session_id"]
        item, checked = journal["sessions"][identity], audited[identity]
        if item.get("status") != "succeeded" or checked.get("status") != "PASS":
            raise ValueError(f"Failed/incomplete session: {identity}")
        gate = item.get("scientific_validation", {})
        gate_path = mapped(gate["path"]) if gate.get("path") else None
        if (gate.get("status") != "PASS" or gate_path is None
                or sha256(gate_path) != gate.get("sha256") or read_json(gate_path) != checked
                or checked.get("errors")):
            raise ValueError(f"Missing/changed per-session scientific PASS gate: {identity}")
        attempts = item.get("attempts", [])
        if len(attempts) != 1 or attempts[0].get("status") != "succeeded":
            raise ValueError(f"Unreviewed replacement/failed attempt: {identity}")
        attempt = attempts[0]
        run_root = Path(attempt["run_root"])
        record = read_json(mapped(run_root.parent / "session.json"))
        if (record.get("schema_version") != "research-session-v1"
                or record.get("campaign_sha256") != digest or record.get("session") != session
                or record.get("attempt") != attempt):
            raise ValueError(f"Session binding differs from plan/journal: {identity}")
        expected_path = run_root / f"sf{session['scale']}" / session["engine"] / "results.json"
        artifacts = attempt["artifacts"]
        if str(expected_path) != artifacts.get("results_path"):
            raise ValueError(f"Results path differs from session: {identity}")
        path = mapped(expected_path)
        result_hash = sha256(path)
        if (result_hash != artifacts.get("results_sha256") or result_hash != checked.get("results_sha256")
                or sha256(mapped(run_root / "schedule.json")) != artifacts.get("schedule_sha256")):
            raise ValueError(f"Result/schedule hash drift after validation: {identity}")
        summary = summarize_session(session, read_json(path), checked)
        summary["results_sha256"] = result_hash
        summaries.append(summary)
    return plan, digest, audit, summaries


def log_ratio_summary(observations, expected_blocks=BLOCKS, include_test=True):
    """Use one paired observation per block; never silently reduce the sample."""
    indexed = unique_index(observations, "block_id", "paired block")
    if set(indexed) - set(expected_blocks):
        raise ValueError("Unknown paired block")
    missing = [block for block in expected_blocks if block not in indexed
               or indexed[block].get("numerator_seconds") is None
               or indexed[block].get("denominator_seconds") is None]
    if missing:
        return {"status": "incomplete", "n_blocks": len(expected_blocks) - len(missing),
                "missing_blocks": missing, "geometric_mean_speedup": None, "ci95_pointwise": None,
                **({"p_raw": 1.0} if include_test else {})}
    logs = []
    for block in expected_blocks:
        row = indexed[block]
        a, b = row["numerator_seconds"], row["denominator_seconds"]
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in (a, b)):
            raise ValueError("Paired times must be finite and positive")
        logs.append(math.log(a) - math.log(b))
    n = len(logs)
    if n < 2:
        raise ValueError("At least two independent paired blocks are needed")
    mean = math.fsum(logs) / n
    sd = statistics.stdev(logs)
    se = sd / math.sqrt(n)
    margin = float(t.ppf(0.975, n - 1)) * se
    # A zero observed variance is retained explicitly, never treated as proof of reproducibility.
    if sd == 0:
        pvalue = 1.0 if mean == 0 else 0.0
    else:
        pvalue = float(min(1.0, 2 * t.sf(abs(mean / se), n - 1)))
    output = {"status": "complete", "n_blocks": n, "missing_blocks": [],
              "geometric_mean_speedup": math.exp(mean),
              "ci95_pointwise": [math.exp(mean - margin), math.exp(mean + margin)],
              "mean_log_ratio": mean, "sd_log_ratio": sd, "se_log_ratio": se,
              "degrees_of_freedom": n - 1, "zero_observed_log_variance": sd == 0}
    if include_test:
        output["p_raw"] = pvalue
    return output


def holm_adjust(pvalues):
    """Return adjusted values in input order, keeping the entire declared family."""
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in pvalues):
        raise ValueError("Invalid primary p value")
    m = len(pvalues)
    adjusted = [None] * m
    previous = 0.0
    for rank, index in enumerate(sorted(range(m), key=lambda i: pvalues[i])):
        previous = max(previous, min(1.0, (m - rank) * pvalues[index]))
        adjusted[index] = previous
    return adjusted


def contrast_observations(index, contrast, endpoint, query=None):
    rows = []
    for block in BLOCKS:
        row = {"block_id": block}
        for side in ("numerator", "denominator"):
            session = index[(block, contrast["suite"], contrast["scale"], contrast[side])]
            row[side + "_session_id"] = session["session_id"]
            row[side + "_status"] = session["suite_status"] if query is None else session["queries"][query]["status"]
            row[side + "_seconds"] = (session["suite_scores"][endpoint] if query is None else
                session["queries"][query]["endpoints"].get(endpoint, {}).get("median"))
        a, b = row["numerator_seconds"], row["denominator_seconds"]
        row["ratio"] = a / b if a is not None and b is not None else None
        row["log_ratio"] = math.log(a) - math.log(b) if row["ratio"] is not None else None
        rows.append(row)
    return rows


def analyze(plan, summaries):
    index = {}
    for session in summaries:
        key = tuple(session[k] for k in ("block_id", "suite", "scale", "config_id"))
        if key in index:
            raise ValueError(f"Duplicate summarized session: {key}")
        index[key] = session
    expected = {(b, suite, scale, config) for b in BLOCKS for suite, scale in CELLS for config in CONFIG_ENGINES}
    if set(index) != expected:
        raise ValueError("All 400 session summaries are required; no missing-session exclusion")
    primary, secondary, queries, raw = [], [], [], []

    def estimate(contrast, endpoint, family, query=None):
        observations = contrast_observations(index, contrast, endpoint, query)
        for observation in observations:
            raw.append({"family": family, **contrast, "endpoint": endpoint,
                        "query": query or "", **observation})
        return {**contrast, "endpoint": endpoint, **({"query": query} if query else {}),
                **log_ratio_summary(observations, include_test=family == "primary_suite")}

    for contrast in plan["primary_contrasts"]:
        primary.append(estimate(contrast, "seconds", "primary_suite"))
    if len(primary) != 26:
        raise ValueError("The primary family must retain all 26 contrasts")
    for entry, adjusted in zip(primary, holm_adjust([entry["p_raw"] for entry in primary])):
        entry["p_holm"] = adjusted
        entry["holm_reject_two_sided_005"] = entry["status"] == "complete" and adjusted < 0.05
        entry["supported_gpu_advantage"] = (entry["holm_reject_two_sided_005"]
                                             and entry["geometric_mean_speedup"] > 1)
        entry["supported_reference_advantage"] = (entry["holm_reject_two_sided_005"]
                                                   and entry["geometric_mean_speedup"] < 1)
    for numerator, denominator in PAIRS:
        for suite, scale in CELLS:
            contrast = {"contrast_id": f"{numerator}_over_{denominator}-{suite}-sf{scale}",
                        "numerator": numerator, "denominator": denominator, "suite": suite, "scale": scale}
            secondary.append(estimate(contrast, "host_result_seconds", "secondary_suite"))
            for query in QUERIES[suite]:
                for endpoint in ENDPOINTS:
                    queries.append(estimate(contrast, endpoint, "exploratory_query", query))
    primary_by_id = {entry["contrast_id"]: entry for entry in primary}
    for entry in secondary:
        action = primary_by_id.get(entry["contrast_id"], {})
        a, b = action.get("geometric_mean_speedup"), entry.get("geometric_mean_speedup")
        entry["direction_changed_vs_action_point_estimate"] = (
            (a > 1) != (b > 1) if a is not None and b is not None else None)
    family_complete = all(entry["status"] == "complete" for entry in primary)
    return {"primary_family_status": "complete" if family_complete else "incomplete",
            "primary_family_size": 26, "primary": primary, "secondary_host_result": secondary,
            "secondary_direction_changes": [entry["contrast_id"] for entry in secondary
                                             if entry["direction_changed_vs_action_point_estimate"]],
            "exploratory_queries": queries, "sessions": summaries}, raw


def write_csv(path, rows):
    if not rows:
        return
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def markdown_report(report):
    lines = ["# H100 confirmatory paired-block analysis", "",
             f"Campaign `{report['campaign_id']}`; {len(report['sessions'])} sessions; ten paired blocks.", "",
             "Each suite score sums six session query medians; it is not an observed suite wall time. "
             "Ratios above one favor the GPU. Intervals are pointwise 95%, not simultaneous. "
             "The 26 two-sided primary tests form one Holm family. Inner actions do not increase N.", "",
             "| Comparison | Suite | SF | Geometric speedup | Pointwise 95% CI | Holm p | Supported GPU advantage |",
             "|---|---|---:|---:|---|---:|---|"]
    for entry in report["primary"]:
        ci = entry["ci95_pointwise"]
        estimate = f"{entry['geometric_mean_speedup']:.3f}×" if ci else "unavailable"
        interval = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "incomplete"
        lines.append(f"| {entry['numerator']} / {entry['denominator']} | {entry['suite']} | {entry['scale']} | "
                     f"{estimate} | {interval} | {entry['p_holm']:.4g} | {entry['supported_gpu_advantage']} |")
    lines += ["", "The t model assumes independent, approximately normal block log ratios. "
              "Ten blocks provide no power guarantee; temporal dependence and single-host scope remain limitations. "
              "A nonsignificant result is not equivalence. Zero observed log variance, if present, is explicitly "
              "flagged in JSON and does not establish absence of variability.", "",
              "Polars GPU complex coverage remains four of six queries: the original regex and window "
              "rejections have no substituted latency or complete-suite aggregate. "
              "Query and host-result intervals are exploratory and have no additional hypothesis tests.", "",
              f"Host-result versus action point-estimate direction changes: {len(report['secondary_direction_changes'])}. "
              "The JSON names each changed comparison; this descriptive check is not another significance test.", "",
              "See analysis.json for every session/query estimate and descriptive contrast, "
              "session_query_medians.csv for nested session observations, and block_ratios.csv "
              "for all paired block observations, including unsupported outcomes."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--artifacts-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Refusing to overwrite an analysis directory")
    try:
        plan, digest, audit, sessions = load_evidence(args.campaign, args.validation, args.artifacts_root)
        report, raw = analyze(plan, sessions)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Evidence rejected: {type(exc).__name__}: {exc}\n")
    report.update({"schema_version": "research-analysis-v1", "campaign_id": plan["campaign_id"],
                   "campaign_sha256": digest, "journal_sha256": audit["journal_sha256"],
                   "validation_sha256": sha256(args.validation), "validation_status": audit["status"],
                   "created_at_utc": datetime.now(timezone.utc).isoformat(),
                   "analysis_source_sha256": sha256(Path(__file__)),
                   "analysis_python": platform.python_version(), "analysis_platform": platform.platform(),
                   "analysis_packages": {package: importlib.metadata.version(package)
                                         for package in ("numpy", "scipy", "psutil")},
                   "inference_unit": "ten paired blocks; three actions nested inside each session",
                   "ci_scope": "pointwise 95%; not simultaneous", "primary_endpoint": "seconds",
                   "assumptions": ["independent blocks", "approximately normal block log ratios",
                                   "fixed ten blocks is not a power guarantee", "single-host conditional inference"]})
    # JSON rejects NaN/Infinity, including any accidental downstream statistical overflow.
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    args.output.mkdir(parents=True)
    (args.output / "analysis.json").write_text(payload)
    (args.output / "REPORT.md").write_text(markdown_report(report))
    write_csv(args.output / "block_ratios.csv", raw)
    session_rows = []
    for session in sessions:
        for query, summary in session["queries"].items():
            for endpoint in ENDPOINTS:
                values = summary["endpoints"].get(endpoint, {})
                session_rows.append({**{key: session[key] for key in ("session_id", "block_id", "suite", "scale", "config_id", "position")},
                                     "query": query, "endpoint": endpoint, "status": summary["status"],
                                     "median_seconds": values.get("median"), "min_seconds": values.get("min"),
                                     "max_seconds": values.get("max"), "nested_action_count": len(values.get("trials", []))})
    write_csv(args.output / "session_query_medians.csv", session_rows)
    print(json.dumps({"campaign_id": plan["campaign_id"], "sessions": len(sessions),
                      "primary_family_size": 26, "primary_family_status": report["primary_family_status"]}))
    return 0 if report["primary_family_status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
