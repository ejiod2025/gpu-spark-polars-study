"""Independent-block statistics and fail-closed campaign evidence contracts."""
from __future__ import annotations

import copy
import csv
import json
import math
from pathlib import Path
import shutil
import sys

import pytest
from scipy.stats import t

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import research_analysis as ra
import research_campaign as rc


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False))


def make_plan(root):
    configs = [{"id": config, "engine": engine, "python": f"/runtime/{config}/python"}
               for config, engine in rc.CONFIG_ENGINES.items()]
    blocks, sessions = rc.make_schedule(configs, 12345, root / "remote-results")
    return {"schema_version": rc.SCHEMA, "campaign_id": "synthetic-contract-only", "seed": 12345,
            "output_root": str(root / "remote-results"), "configs": configs, "blocks": blocks,
            "sessions": sessions, "settings": {"runs": 3}, "primary_contrasts": rc.primary_contrasts(),
            "source": {"files_sha256": {"docs/H100_CONFIRMATORY_PROTOCOL.md": "a" * 64}},
            "protocol_sha256": "a" * 64,
            "oracles": {f"{suite}-sf{scale}": {"path": f"/oracle/{suite}-sf{scale}.json", "sha256": "b" * 64}
                        for suite, scale in rc.CELLS},
            "semantic_fixtures": {config: {"path": f"/fixtures/{config}.json", "sha256": "c" * 64}
                                  for config in rc.CONFIG_ENGINES},
            "analysis_predeclarations": {"primary_family_size": 26, "multiplicity": "Holm",
                                         "primary_endpoint": "seconds", "familywise_alpha": 0.05}}


def example_value(session):
    queries = ra.QUERIES[session["suite"]]
    unsupported = ra.UNSUPPORTED if session["suite"] == "complex" and session["config_id"] == "polars_gpu" else set()
    factor = {"polars_cpu_144": 3, "polars_cpu_200": 2, "polars_gpu": .5,
              "spark_cpu": 4, "spark_gpu": 1}[session["config_id"]]
    if session["config_id"] == "polars_cpu_200":
        factor *= math.exp(.02 * (int(session["block_id"][-2:]) - 5.5))
    value = {"suite": session["suite"], "engine": session["engine"], "scale_factor": float(session["scale"]),
             "runs": 3, "queries": list(queries), "query_seed": session["query_seed"],
             "completed_at_utc": "2026-10-26T00:00:00+00:00", "records": [], "query_errors": []}
    audit = {"session_id": session["session_id"], "status": "PASS", "errors": [],
             "coverage": {}, "comparisons": [], "plans": []}
    for query in queries:
        if query in unsupported:
            error = {"query": query, "phase": "warmup", "trial": 0, "type": "NotImplementedError"}
            value["query_errors"].append(error)
            audit["coverage"][query] = {"status": "unsupported", "errors": [error]}
            continue
        audit["coverage"][query] = {"status": "complete"}
        for phase, trial, mult in [("warmup", 0, 999), ("measured", 1, .5),
                                   ("measured", 2, 1), ("measured", 3, 1.5)]:
            value["records"].append({"query": query, "phase": phase, "trial": trial,
                                     "seconds": factor * mult, "host_result_seconds": factor * mult + .01})
            audit["comparisons"].append({"query": query, "phase": phase, "trial": trial, "passed": True})
    return value, audit


def summaries(plan):
    return [ra.summarize_session(session, *example_value(session)) for session in plan["sessions"]]


def complete_evidence(root):
    """Synthetic, hash-bound artifacts test the contract; they are never study data."""
    plan = make_plan(root)
    campaign = root / "campaign.json"
    write(campaign, plan)
    digest = rc.sha256(campaign)
    campaign.with_name(campaign.name + ".sha256").write_text(digest + "\n")
    journal = {"schema_version": rc.JOURNAL_SCHEMA, "campaign_sha256": digest, "sessions": {}}
    audits = []
    for session in plan["sessions"]:
        run = Path(session["output_root"]) / "attempt-001" / "run"
        path = run / f"sf{session['scale']}" / session["engine"] / "results.json"
        value, audit = example_value(session)
        write(path, value)
        write(run / "schedule.json", [{"status": "complete"}])
        attempt = {"status": "succeeded", "run_root": str(run), "artifacts": {
            "results_path": str(path), "results_sha256": rc.sha256(path),
            "schedule_sha256": rc.sha256(run / "schedule.json")}}
        journal["sessions"][session["session_id"]] = {"status": "succeeded", "attempts": [attempt]}
        write(run.parent / "session.json", {"schema_version": "research-session-v1", "campaign_sha256": digest,
                                           "session": session, "attempt": attempt})
        audit["results_sha256"] = rc.sha256(path)
        gate_path = run.parent / "SCIENTIFIC_VALIDATION.json"
        write(gate_path, audit)
        journal["sessions"][session["session_id"]]["scientific_validation"] = {
            "status": "PASS", "path": str(gate_path), "sha256": rc.sha256(gate_path)}
        audits.append(audit)
    journal_path = Path(plan["output_root"]) / "journal.json"
    write(journal_path, journal)
    validation = root / "validation.json"
    write(validation, {"schema_version": "research-validation-v1", "status": "PASS", "campaign_sha256": digest,
                       "journal_sha256": rc.sha256(journal_path), "planned_sessions": 400, "audited_sessions": 400,
                       "oracle_files": plan["oracles"], "pending_sessions": [], "failed_sessions": [], "sessions": audits})
    return campaign, validation, plan


def test_log_ratio_t_interval_matches_hand_calculation_and_uses_ten_blocks():
    logs = [-.18, -.14, -.10, -.06, -.02, .02, .06, .10, .14, .18]
    rows = [{"block_id": block, "numerator_seconds": 2 * math.exp(delta), "denominator_seconds": 1}
            for block, delta in zip(ra.BLOCKS, logs)]
    result = ra.log_ratio_summary(rows)
    mean = math.log(2)
    sd = math.sqrt(sum(delta * delta for delta in logs) / 9)
    margin = float(t.ppf(.975, 9)) * sd / math.sqrt(10)
    assert result["geometric_mean_speedup"] == pytest.approx(2)
    assert result["ci95_pointwise"] == pytest.approx([math.exp(mean - margin), math.exp(mean + margin)])
    assert result["p_raw"] == pytest.approx(2 * t.sf(mean / (sd / math.sqrt(10)), 9))
    assert result["n_blocks"] == 10 and result["degrees_of_freedom"] == 9


def test_geometric_mean_and_holm_known_values_and_order():
    rows = [{"block_id": b, "numerator_seconds": n, "denominator_seconds": 1}
            for b, n in zip(ra.BLOCKS, [1] * 5 + [4] * 5)]
    assert ra.log_ratio_summary(rows)["geometric_mean_speedup"] == pytest.approx(2)
    assert ra.holm_adjust([.03, .01, .04]) == pytest.approx([.06, .03, .06])
    assert ra.holm_adjust([.001] + [1] * 25)[0] == pytest.approx(.026)


def test_no_missing_block_complete_case_analysis_or_duplicate_pairing():
    rows = [{"block_id": b, "numerator_seconds": 2, "denominator_seconds": 1} for b in ra.BLOCKS]
    incomplete = ra.log_ratio_summary(rows[:-1])
    assert incomplete["status"] == "incomplete" and incomplete["n_blocks"] == 9
    assert incomplete["geometric_mean_speedup"] is None and incomplete["p_raw"] == 1
    with pytest.raises(ValueError, match="Duplicate paired block"):
        ra.log_ratio_summary(rows + [rows[0]])
    rows[0]["numerator_seconds"] = float("nan")
    with pytest.raises(ValueError, match="finite and positive"):
        ra.log_ratio_summary(rows)


def test_session_median_excludes_warmup_and_gpu_complex_never_gets_partial_sum(tmp_path):
    plan = make_plan(tmp_path)
    cpu = next(s for s in plan["sessions"] if s["config_id"] == "polars_cpu_144")
    summary = ra.summarize_session(cpu, *example_value(cpu))
    assert summary["queries"][ra.QUERIES[cpu["suite"]][0]]["endpoints"]["seconds"]["median"] == 3
    assert summary["suite_scores"]["seconds"] == 18
    gpu = next(s for s in plan["sessions"] if s["config_id"] == "polars_gpu" and s["suite"] == "complex")
    summary = ra.summarize_session(gpu, *example_value(gpu))
    assert summary["coverage"] == "4/6"
    assert summary["suite_scores"] == {"seconds": None, "host_result_seconds": None}
    assert {q for q, v in summary["queries"].items() if v["status"] == "unsupported"} == ra.UNSUPPORTED


@pytest.mark.parametrize("scale", [True, float("nan"), float("inf"), "1.0", 1.01])
def test_invalid_or_mismatched_numeric_scale_is_rejected(tmp_path, scale):
    session = next(s for s in make_plan(tmp_path)["sessions"] if s["scale"] == "1")
    value, audit = example_value(session)
    value["scale_factor"] = scale
    with pytest.raises(ValueError, match="identity/completion"):
        ra.summarize_session(session, value, audit)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "failed", "unvalidated", "endpoint"])
def test_no_invalid_inner_action_is_silently_dropped(tmp_path, kind):
    session = make_plan(tmp_path)["sessions"][0]
    value, audit = example_value(session)
    if kind == "missing":
        value["records"].pop()
    elif kind == "duplicate":
        value["records"].append(copy.deepcopy(value["records"][0]))
    elif kind == "failed":
        value["query_errors"].append({"query": value["queries"][0], "phase": "measured", "trial": 1})
    elif kind == "unvalidated":
        audit["comparisons"].pop()
    else:
        value["records"][0]["host_result_seconds"] = 0
    with pytest.raises(ValueError):
        ra.summarize_session(session, value, audit)


def test_primary_family_all_cells_and_descriptive_queries_preserved(tmp_path):
    plan = make_plan(tmp_path)
    ra.validate_design(plan)
    report, raw = ra.analyze(plan, summaries(plan))
    assert report["primary_family_status"] == "complete"
    assert len(report["primary"]) == 26
    assert {entry["n_blocks"] for entry in report["primary"]} == {10}
    assert len([row for row in raw if row["family"] == "primary_suite"]) == 260
    assert len(report["exploratory_queries"]) == 4 * 8 * 6 * 2
    assert all("p_raw" not in row and "p_holm" not in row for row in report["exploratory_queries"])
    gpu_complex = [r for r in report["secondary_host_result"] if r["denominator"] == "polars_gpu" and r["suite"] == "complex"]
    assert len(gpu_complex) == 6 and all(r["geometric_mean_speedup"] is None for r in gpu_complex)


def test_unavailable_primary_contrast_retains_26_test_family(tmp_path):
    plan = make_plan(tmp_path)
    observed = summaries(plan)
    next(s for s in observed if s["block_id"] == "block-01" and s["suite"] == "baseline"
         and s["scale"] == "1" and s["config_id"] == "spark_gpu")["suite_scores"]["seconds"] = None
    report, _ = ra.analyze(plan, observed)
    assert report["primary_family_status"] == "incomplete"
    assert len(report["primary"]) == 26
    failed = [entry for entry in report["primary"] if entry["status"] == "incomplete"]
    assert len(failed) == 2
    assert all(entry["p_raw"] == entry["p_holm"] == 1 for entry in failed)
    assert all(entry["geometric_mean_speedup"] is None for entry in failed)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "missing_family", "duplicate_family"])
def test_design_rejects_missing_or_duplicate_sessions_and_family(tmp_path, mutation):
    plan = make_plan(tmp_path)
    if mutation == "missing":
        plan["sessions"].pop()
    elif mutation == "duplicate":
        plan["sessions"][-1] = copy.deepcopy(plan["sessions"][0])
    elif mutation == "missing_family":
        plan["primary_contrasts"].pop()
    else:
        plan["primary_contrasts"].append(copy.deepcopy(plan["primary_contrasts"][0]))
    with pytest.raises(ValueError):
        ra.validate_design(plan)
    if mutation in ("missing", "duplicate"):
        with pytest.raises(ValueError):
            ra.analyze(plan, summaries(plan))


def test_complete_hash_bound_evidence_and_archived_path_replay(tmp_path):
    campaign, validation, plan = complete_evidence(tmp_path)
    loaded, digest, audit, sessions = ra.load_evidence(campaign, validation)
    assert len(sessions) == 400 and digest == rc.sha256(campaign) and audit["status"] == "PASS"
    archive = tmp_path / "archived-results"
    shutil.move(plan["output_root"], archive)
    _, _, _, archived = ra.load_evidence(campaign, validation, archive)
    assert archived == sessions
    output = tmp_path / "analysis"
    assert ra.main(["--campaign", str(campaign), "--validation", str(validation),
                    "--artifacts-root", str(archive), "--output", str(output)]) == 0
    published = json.loads((output / "analysis.json").read_text())
    assert published["primary_family_size"] == 26 and published["validation_status"] == "PASS"
    with (output / "session_query_medians.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) == 400 * 6 * 2


@pytest.mark.parametrize("mutation", ["results_drift", "journal_drift", "duplicate_audit", "missing_audit", "partial_audit", "campaign_binding", "session_binding", "scientific_drift", "failed_session", "missing_journal_session", "oracle_binding"])
def test_evidence_gate_rejects_drift_missing_duplicate_or_partial(tmp_path, mutation):
    campaign, validation, plan = complete_evidence(tmp_path)
    session = plan["sessions"][0]
    audit = json.loads(validation.read_text())
    if mutation == "results_drift":
        path = Path(session["output_root"]) / "attempt-001" / "run" / f"sf{session['scale']}" / session["engine"] / "results.json"
        value = json.loads(path.read_text())
        value["records"][0]["seconds"] += .001
        write(path, value)
    elif mutation == "journal_drift":
        path = Path(plan["output_root"]) / "journal.json"
        path.write_text(path.read_text() + "\n")
    elif mutation == "duplicate_audit":
        audit["sessions"].append(copy.deepcopy(audit["sessions"][0]))
    elif mutation == "missing_audit":
        audit["sessions"].pop()
    elif mutation == "partial_audit":
        audit["status"] = "PARTIAL"
    elif mutation == "campaign_binding":
        audit["campaign_sha256"] = "0" * 64
    elif mutation == "oracle_binding":
        audit["oracle_files"]["baseline-sf1"]["sha256"] = "0" * 64
    elif mutation == "session_binding":
        path = Path(session["output_root"]) / "attempt-001" / "session.json"
        record = json.loads(path.read_text())
        record["session"]["config_id"] = "other"
        write(path, record)
    elif mutation == "scientific_drift":
        path = Path(session["output_root"]) / "attempt-001" / "SCIENTIFIC_VALIDATION.json"
        path.write_text(path.read_text() + "\n")
    else:
        path = Path(plan["output_root"]) / "journal.json"
        journal = json.loads(path.read_text())
        if mutation == "failed_session":
            journal["sessions"][session["session_id"]]["status"] = "failed"
        else:
            del journal["sessions"][session["session_id"]]
        write(path, journal)
        # Even a rewritten audit cannot conceal a failed or missing planned session.
        audit["journal_sha256"] = rc.sha256(path)
    write(validation, audit)
    with pytest.raises(ValueError):
        ra.load_evidence(campaign, validation)


def test_duplicate_json_keys_do_not_disappear(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"status":"FAIL","status":"PASS"}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        ra.read_json(path)
