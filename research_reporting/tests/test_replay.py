"""SYNTHETIC full-artifact tests using the genuine frozen C84 validator/analyzer."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys

import pytest

REPORTING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPORTING))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay
from synthetic_replay_fixture import create_synthetic_replay


@pytest.fixture(scope="module")
def synthetic_base(tmp_path_factory):
    return create_synthetic_replay(tmp_path_factory.mktemp("SYNTHETIC-full-replay-base"))


@pytest.fixture()
def synthetic(synthetic_base, tmp_path):
    archive, artifacts = synthetic_base
    copied_archive = tmp_path / "SYNTHETIC-archive"
    copied_artifacts = tmp_path / "relocated-harvest" / "campaign-results"
    shutil.copytree(archive, copied_archive)
    shutil.copytree(artifacts, copied_artifacts)
    return copied_archive, copied_artifacts


def test_complete_relocated_artifact_runs_frozen_code_and_watermarked_rendering(synthetic, tmp_path):
    archive, artifacts = synthetic
    before_archive, before_artifacts = replay.inventory(archive), replay.inventory(artifacts)
    output = tmp_path / "SYNTHETIC-replay-output"
    # Accept a harvest parent while resolving exactly one campaign-results root.
    manifest = replay.replay(archive, artifacts.parent, output, allow_synthetic=True)
    assert manifest["status"] == "PASS" and manifest["synthetic"] is True
    assert manifest["source_commit"] == replay.SOURCE_COMMIT
    assert manifest["campaign_sha256"] == replay.sha256(archive / "campaign.json")
    assert len(manifest["source_files_sha256"]) == 52
    assert [step["name"] for step in manifest["commands"]] == ["validation", "analysis", "complete_audit", "rendering"]
    for index, script in enumerate(("research_validate.py", "research_analysis.py")):
        assert manifest["commands"][index]["command"][2] == str(archive / "campaign.json.source" / script)
    raw = replay.read_json(output / "validation.raw.json")
    normalized = replay.read_json(output / "validation.json")
    changes = replay.read_json(output / "PATH_NORMALIZATION.json")
    assert raw["status"] == normalized["status"] == "PASS"
    assert raw["audited_sessions"] == 400 and raw["successful_outputs_compared"] == 9360
    assert len(changes["changes"]) == 1920
    # Exact structural identity after undoing the sole permitted provenance changes.
    restored = copy.deepcopy(normalized)
    restored.pop("portability_adapter")
    for change in changes["changes"]:
        _, _, session_index, _, plan_index, _ = change["json_pointer"].split("/")
        entry = restored["sessions"][int(session_index)]["plans"][int(plan_index)]
        assert entry["source"] == change["to"]
        entry["source"] = change["from"]
    assert restored == raw
    assert normalized["oracle_files"] == raw["oracle_files"]
    assert normalized["created_at_utc"] == raw["created_at_utc"]
    analysis = replay.read_json(output / "analysis" / "analysis.json")
    assert len(analysis["sessions"]) == 400 and len(analysis["primary"]) == 26
    assert analysis["primary_family_status"] == "complete"
    assert analysis["analysis_source_sha256"] == manifest["source_files_sha256"]["research_analysis.py"]
    assert replay.read_json(output / "complete-audit" / "AUDIT_COMPLETE.json")["status"] == "PASS"
    # The exact frozen analyzer output is retained. Only a separate render input is tagged.
    assert "synthetic" not in analysis and "SYNTHETIC" in analysis["campaign_id"]
    assert replay.read_json(output / "SYNTHETIC-render-input" / "analysis.json")["synthetic"] is True
    for svg in (output / "figures").glob("*.svg"):
        assert "SYNTHETIC DATA" in svg.read_text()
    assert len(list((output / "figures").glob("*.png"))) == 4
    assert len(list((output / "figures").glob("*.pdf"))) == 4
    assert replay.inventory(archive) == before_archive and replay.inventory(artifacts) == before_artifacts
    assert not list(archive.rglob("__pycache__"))


def test_synthetic_cannot_be_passed_off_as_real_archive(synthetic, tmp_path):
    archive, artifacts = synthetic
    output = tmp_path / "refused"
    with pytest.raises(replay.ReplayError, match="Synthetic replay requires"):
        replay.replay(archive, artifacts, output)
    manifest = replay.read_json(output / "REPLAY_MANIFEST.json")
    assert manifest["status"] == "FAIL" and manifest["commands"] == []
    assert (output / "FAILURE.json").is_file()


@pytest.mark.parametrize("mutation", ["source_drift", "oracle_drift", "missing_source", "unlisted_file"])
def test_prestart_checks_refuse_missing_or_changed_archive_before_execution(synthetic, tmp_path, mutation):
    archive, artifacts = synthetic
    if mutation == "source_drift":
        path = archive / "campaign.json.source" / "research_validate.py"
        path.write_text(path.read_text() + "\n# SYNTHETIC tamper\n")
    elif mutation == "oracle_drift":
        path = archive / "oracles" / "baseline-sf1.json"
        path.write_text(path.read_text() + "\n")
    elif mutation == "missing_source":
        (archive / "campaign.json.source" / "worker.py").unlink()
    else:
        (archive / "SYNTHETIC-unlisted.txt").write_text("not in the archived checksum inventory")
    output = tmp_path / "failed-archive-check"
    with pytest.raises(replay.ReplayError):
        replay.replay(archive, artifacts, output, allow_synthetic=True)
    manifest = replay.read_json(output / "REPLAY_MANIFEST.json")
    assert manifest["status"] == "FAIL" and manifest["phase"] == "archive-verification"
    assert manifest["commands"] == []


@pytest.mark.parametrize("mutation", ["incomplete", "retried", "results_drift"])
def test_frozen_validator_failure_is_preserved_and_stops_analysis(synthetic, tmp_path, mutation):
    archive, artifacts = synthetic
    journal_path = artifacts / "journal.json"
    journal = replay.read_json(journal_path)
    identity = next(iter(journal["sessions"]))
    item = journal["sessions"][identity]
    if mutation == "incomplete":
        item["status"] = "planned"
        replay.write_json(journal_path, journal)
    elif mutation == "retried":
        item["attempts"].append(copy.deepcopy(item["attempts"][0]))
        replay.write_json(journal_path, journal)
    else:
        plan = replay.read_json(archive / "campaign.json")
        path = replay.mapped_file(plan, artifacts, item["attempts"][0]["artifacts"]["results_path"])
        path.write_text(path.read_text() + "\n")
    output = tmp_path / "failed-validation"
    with pytest.raises(replay.ReplayError, match="validation exited"):
        replay.replay(archive, artifacts, output, allow_synthetic=True)
    manifest = replay.read_json(output / "REPLAY_MANIFEST.json")
    assert manifest["status"] == "FAIL" and len(manifest["commands"]) == 1
    assert (output / "validation.log").is_file() and not (output / "analysis").exists()
    if (output / "validation.raw.json").exists():
        assert replay.read_json(output / "validation.raw.json")["status"] == "FAIL"


def test_adapter_refuses_wrong_action_plan_binding_even_for_identical_plan_bytes(synthetic, tmp_path):
    archive, artifacts = synthetic
    plan = replay.read_json(archive / "campaign.json")
    journal = replay.read_json(artifacts / "journal.json")
    gpu = [session for session in plan["sessions"] if session["engine"] == "spark_gpu"]
    first, other = gpu[:2]
    first_gate = journal["sessions"][first["session_id"]]["scientific_validation"]["path"]
    audited = replay.read_json(replay.mapped_file(plan, artifacts, first_gate))
    a = journal["sessions"][first["session_id"]]["attempts"][0]
    b = journal["sessions"][other["session_id"]]["attempts"][0]
    first_plan = Path(a["artifacts"]["results_path"]).parent / "plans/SYNTHETIC-plan.txt"
    other_plan = Path(b["artifacts"]["results_path"]).parent / "plans/SYNTHETIC-plan.txt"
    assert replay.sha256(replay.mapped_file(plan, artifacts, first_plan)) == replay.sha256(replay.mapped_file(plan, artifacts, other_plan))
    for entry in audited["plans"]:
        entry["source"] = str(replay.mapped_file(plan, artifacts, first_plan))
    audited["plans"][0]["source"] = str(replay.mapped_file(plan, artifacts, other_plan))
    raw = tmp_path / "SYNTHETIC-raw-validation.json"
    replay.write_json(raw, {"status": "PASS", "sessions": [audited]})
    with pytest.raises(ValueError, match="exact recorded action"):
        replay.adapt_validation(raw, tmp_path / "not-written.json", plan, artifacts, tmp_path / "not-written-changes.json")


def test_output_overwrite_and_input_nested_output_are_refused(synthetic, tmp_path):
    archive, artifacts = synthetic
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="overwrite"):
        replay.replay(archive, artifacts, existing, allow_synthetic=True)
    assert marker.read_text() == "keep"
    with pytest.raises(ValueError, match="separate"):
        replay.replay(archive, artifacts, artifacts / "new-output", allow_synthetic=True)
