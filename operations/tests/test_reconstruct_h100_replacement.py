"""FABRICATED offline transport fixtures, never measured research evidence.

Only the identity registry is redirected to a temporary mock registry. All real
backup, canonical verification and Git-byte checks run against fabricated
snapshots containing unchanged frozen source. No engine, query or network runs.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

from operations import reconstruct_h100_replacement as reconstruct

backup = reconstruct.backup
REAL_ARCHIVE = reconstruct.REPOSITORY / "evidence-research/20261026/h100-confirmatory-v1"
NOTICE = "FABRICATED TEST ONLY; no timings, queries, actual results or study evidence"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(backup.payload(value) if isinstance(value, (dict, list)) else value)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def time_at(seconds):
    return (datetime(2026, 10, 27, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()


def pull_fixture(raw, phase):
    """Use real verification, with no transport/network mocking."""
    control = reconstruct.read_json(raw / f"{phase}.json")
    backup.verify_archive(raw / f"{phase}.tar", control, raw / phase)
    write(raw / f"{phase}.verified.json", {**control, "status": "OFFHOST_SHA256_VERIFIED",
        "verified_at_utc": time_at(2000), "local_directory": str(raw / phase),
        "local_archive": str(raw / f"{phase}.tar")})


def receipt_fixture(evidence, raw, phase, commit=None, prestart=None):
    control = reconstruct.read_json(raw / f"{phase}.json")
    receipt = {**control, "status": "OFFHOST_SHA256_VERIFIED", "verified_at_utc": time_at(10 if phase == "prestart" else 1800),
               "git_commit": commit, "local_directory": str(raw / phase), "fabricated_notice": NOTICE}
    if phase == "prestart":
        receipt["prestart_manifest_sha256"] = backup.sha(prestart / "PRESTART_MANIFEST.json")
    write(evidence / "backup-receipts" / f"{phase}.json", receipt)


@pytest.fixture
def fabricated(tmp_path, monkeypatch):
    evidence, raw = tmp_path / "FABRICATED-remote-evidence", tmp_path / "FABRICATED-raw-backups"
    repo = tmp_path / "FABRICATED-git-repository"
    archive = repo / "evidence-research/20261027/h100-confirmatory-replacement-v1"
    registry = repo / "research_reporting/campaign_identities.json"
    evidence.mkdir()
    source_plan = reconstruct.read_json(REAL_ARCHIVE / "campaign.json")
    # Copy only genuine immutable source/oracle/readiness bytes. The
    # session files created below are openly fabricated mechanism fixtures.
    for name in ("campaign.json.source", "oracles"):
        if (REAL_ARCHIVE / name).is_dir():
            shutil.copytree(REAL_ARCHIVE / name, evidence / name)
    write(evidence / "readiness/READINESS.json", (REAL_ARCHIVE / "readiness/READINESS.json").read_bytes())
    plan = copy.deepcopy(source_plan)
    plan.update(campaign_id=reconstruct.CAMPAIGN_ID, output_root=str(evidence / "campaign-results"), fabricated_notice=NOTICE)
    for scale, dataset in plan["datasets"].items():
        manifest = evidence / "manifests" / f"sf{scale}.json"
        write(manifest, {"fabricated_notice": NOTICE, "tables": {"fabricated": {"files": [
            {"path": "NO-DATA.parquet", "bytes": 1, "sha256": "0" * 64}]}}})
        dataset["manifest_sha256"] = backup.sha(manifest)
    for session in plan["sessions"]:
        session["output_root"] = str(evidence / "campaign-results" / session["session_id"])
    write(evidence / "campaign.json", plan)
    campaign = backup.sha(evidence / "campaign.json")
    write(evidence / "campaign.json.sha256", (campaign + "\n").encode())
    write(evidence / "confirm-driver.log", (NOTICE + "\nprestart\n").encode())
    write(evidence / "confirm-driver.pid", b"999999999\n")
    write(evidence / "confirm-driver.started-utc", (time_at(0) + "\n").encode())
    backup.snapshot(evidence, raw, "prestart")
    pull_fixture(raw, "prestart")
    shutil.copytree(raw / "prestart", archive)
    backup.seal_prestart(archive, reconstruct.read_json(raw / "prestart.json"))
    mock_registry = reconstruct.read_json(reconstruct.campaign_identity.REGISTRY_PATH)
    mock_registry["campaigns"][campaign] = {"label": NOTICE, "prestart_sha256": backup.sha(archive / "PRESTART_MANIFEST.json"),
        "source_commit": backup.SOURCE_COMMIT, "source_tree_sha256": backup.SOURCE_TREE}
    write(registry, mock_registry)
    monkeypatch.setattr(reconstruct.campaign_identity, "REGISTRY_PATH", registry)
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Fabricated Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", NOTICE)
    commit = git(repo, "rev-parse", "HEAD")
    receipt_fixture(evidence, raw, "prestart", commit, archive)
    file_counts = {}
    for scale in plan["datasets"]:
        candidates = [evidence / name / f"sf{scale}" / "manifest.json" for name in ("manifests", "dataset-manifests")]
        candidates += [evidence / name / f"sf{scale}.json" for name in ("manifests", "dataset-manifests")]
        manifest = reconstruct.read_json(next(path for path in candidates if path.is_file()))
        file_counts[scale] = sum(len(table["files"]) for table in manifest["tables"].values())
    def byte_gate(seconds):
        return {"completed_at_utc": time_at(seconds), "checks": [
            {"scale": scale, "status": "pass", "files": count} for scale, count in file_counts.items()], "fabricated_notice": NOTICE}
    journal = {"schema_version": "research-campaign-journal-v1", "campaign_sha256": campaign,
        "sessions": {session["session_id"]: {"status": "planned", "attempts": []} for session in plan["sessions"]},
        "input_integrity_gates": [byte_gate(50)], "fabricated_notice": NOTICE}
    for index, session in enumerate(plan["sessions"]):
        attempt_root = Path(session["output_root"]) / "attempt-001"
        run = attempt_root / "run"
        gate = attempt_root / "SCIENTIFIC_VALIDATION.json"
        result = run / f"sf{session['scale']}" / session["engine"] / "results.json"
        for path in (gate, result, run / "schedule.json", run / "environment.json", attempt_root / "session.json"):
            write(path, {"fabricated_notice": NOTICE, "test_session": session["session_id"]})
        write(attempt_root / "orchestrator.log", (NOTICE + "\n").encode())
        write(run / "events.jsonl", b'{"fabricated_test_event":true}\n')
        write(run / "telemetry.jsonl", b'{"fabricated_test_telemetry":true}\n')
        attempt = {"status": "succeeded", "returncode": 0, "attempt_root": str(attempt_root), "run_root": str(run),
            "started_at_utc": time_at(100 + index * 3), "ended_at_utc": time_at(101 + index * 3),
            "artifacts": {"results_path": str(result), "results_sha256": backup.sha(result),
                          "schedule_sha256": backup.sha(run / "schedule.json")}, "fabricated_notice": NOTICE}
        journal["sessions"][session["session_id"]] = {"status": "succeeded", "attempts": [attempt],
            "scientific_validation": {"status": "PASS", "path": str(gate), "sha256": backup.sha(gate)}}
        if (index + 1) % 40 == 0:
            write(evidence / "campaign-results/journal.json", journal)
            phase = f"block-{(index + 1)//40:02d}"
            backup.snapshot(evidence, raw, phase)
            pull_fixture(raw, phase)
            receipt_fixture(evidence, raw, phase)
    journal["input_integrity_gates"].append(byte_gate(1400))
    journal["completed_at_utc"] = time_at(1450)
    write(evidence / "campaign-results/journal.json", journal)
    write(evidence / "ANALYSIS_READY", (time_at(1550) + "\n").encode())
    write(evidence / "confirm-driver.log", (NOTICE + "\nprestart\nfinal\n").encode())
    write(evidence / "confirm-driver.phase", b"final-input-integrity\n")
    write(evidence / "validation.json", {"fabricated_notice": NOTICE})
    write(evidence / "analysis/analysis.json", {"fabricated_notice": NOTICE})
    backup.snapshot(evidence, raw, "final")
    pull_fixture(raw, "final")
    receipt_fixture(evidence, raw, "final")
    write(evidence / "confirm-driver.exit", b"0\n")
    write(evidence / "confirm-driver.ended-utc", (time_at(1900) + "\n").encode())
    write(evidence / "confirm-driver.last-phase", b"complete-ready-for-reporting\n")
    write(evidence / "confirm-driver.log", (NOTICE + "\nprestart\nfinal\ncompletion\n").encode())
    backup.snapshot(evidence, raw, "completion")
    pull_fixture(raw, "completion")
    return {"archive": archive, "raw": raw, "evidence": evidence, "plan": plan, "campaign": campaign,
            "registry": registry, "output": tmp_path / "FABRICATED-reconstruction"}


def run(fixture):
    return reconstruct.reconstruct(fixture["archive"], fixture["raw"], fixture["output"], "replacement")


def test_default_recovery_never_relabels_historical_transport(fabricated):
    with pytest.raises(ValueError, match="exact registered 400-session"):
        reconstruct.reconstruct(fabricated["archive"], fabricated["raw"], fabricated["output"])
    assert not fabricated["output"].exists()


def test_cohort_selection_is_bounded(fabricated):
    with pytest.raises(ValueError, match="Unknown reconstruction cohort"):
        reconstruct.reconstruct(fabricated["archive"], fabricated["raw"], fabricated["output"], "arbitrary-retry")
    assert reconstruct.session_prefixes(fabricated["plan"], "replacement")
    recovery = copy.deepcopy(fabricated["plan"])
    recovery["campaign_id"] = reconstruct.COHORTS["gclocker100"]
    assert reconstruct.session_prefixes(recovery)
    with pytest.raises(ValueError, match="exact registered 400-session"):
        reconstruct.session_prefixes(recovery, "replacement")


def repack_phase(fixture, phase, relative, body):
    """Re-sign fabricated transport to test semantic gates past transport SHA."""
    raw = fixture["raw"]
    tree = raw / phase
    write(tree / relative, body)
    manifest = reconstruct.read_json(tree / "BACKUP_MANIFEST.json")
    manifest["files"][relative] = reconstruct.identity(tree / relative)
    write(tree / "BACKUP_MANIFEST.json", manifest)
    tar_path = raw / f"{phase}.tar"
    with tarfile.open(tar_path, "w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(tree).as_posix(), recursive=False)
    control = reconstruct.read_json(raw / f"{phase}.json")
    control.update(tar_sha256=backup.sha(tar_path), tar_bytes=tar_path.stat().st_size,
                   manifest_sha256=backup.sha(tree / "BACKUP_MANIFEST.json"), files=len(manifest["files"]))
    write(raw / f"{phase}.json", control)
    report = reconstruct.read_json(raw / f"{phase}.verified.json")
    write(raw / f"{phase}.verified.json", {**report, **control})


def test_fabricated_reconstruction_preserves_inputs_and_all_payload_bytes(fabricated):
    before = {phase: reconstruct.inventory(fabricated["raw"] / phase) for phase in reconstruct.PHASES}
    manifest = run(fabricated)
    output = fabricated["output"]
    assert manifest["status"] == "RECONSTRUCTED_TRANSPORT_VERIFIED"
    assert manifest["registered_campaign_identity"]["label"] == NOTICE
    assert manifest["sessions"] == 400
    assert manifest["harvest_files"] == reconstruct.inventory(output / "harvest")
    for name in reconstruct.TRANSPORT_NAMES:
        assert not (output / "harvest" / name).exists()
    assert (output / "lineage/canonical-prestart/PRESTART_MANIFEST.json").read_bytes() == (fabricated["archive"] / "PRESTART_MANIFEST.json").read_bytes()
    advances = [row for row in manifest["overlap_decisions"] if row["decision"] == "permitted_operational_advance"]
    assert [row["path"] for row in advances] == ["confirm-driver.log", "confirm-driver.log"]
    assert advances[0]["old"]["sha256"] == before["prestart"]["confirm-driver.log"]["sha256"]
    assert set(manifest["inputs"]) == set(reconstruct.PHASES)
    assert len(manifest["block_comparisons"]) == 10
    assert "PRESTART_MANIFEST.json" not in manifest["harvest_files"]
    assert {phase: reconstruct.inventory(fabricated["raw"] / phase) for phase in reconstruct.PHASES} == before
    assert reconstruct.read_json(output / "RECONSTRUCTION_MANIFEST.json") == manifest
    for name in manifest["harvest_files"]:
        phase = manifest["payload_provenance"][name]["selected_phase"]
        original = fabricated["archive"] if phase == "prestart" else fabricated["raw"] / phase
        assert (output / "harvest" / name).read_bytes() == (original / name).read_bytes()
    with pytest.raises(FileExistsError, match="reuse"):
        run(fabricated)


@pytest.mark.parametrize("bad", ["missing_completion", "partial_tar", "corrupt_tar", "corrupt_control", "corrupt_report", "corrupt_extracted", "unregistered"])
def test_rejects_missing_partial_tampered_unknown_inputs(fabricated, bad):
    raw = fabricated["raw"]
    if bad == "missing_completion":
        (raw / "completion.json").unlink()
    elif bad == "partial_tar":
        (raw / "block-03.tar").rename(raw / "block-03.tar.partial")
    elif bad == "corrupt_tar":
        with (raw / "block-02.tar").open("ab") as stream:
            stream.write(b"FABRICATED corruption")
    elif bad == "corrupt_control":
        control = reconstruct.read_json(raw / "final.json")
        control["sessions"] = 399
        write(raw / "final.json", control)
    elif bad == "corrupt_report":
        write(raw / "final.verified.json", {})
    elif bad == "corrupt_extracted":
        write(raw / "final/analysis/analysis.json", b"FABRICATED corruption\n")
    elif bad == "unregistered":
        registry = reconstruct.read_json(fabricated["registry"])
        del registry["campaigns"][fabricated["campaign"]]
        write(fabricated["registry"], registry)
    with pytest.raises((ValueError, FileNotFoundError)):
        run(fabricated)
    assert not fabricated["output"].exists()


@pytest.mark.parametrize("bad", ["exit_nonzero", "changed_journal", "immutable_analysis", "truncated_log", "changed_block"])
def test_rejects_resigned_fabricated_semantic_contradictions(fabricated, bad):
    phase = "completion"
    if bad == "exit_nonzero":
        relative, body = "confirm-driver.exit", b"1\n"
    elif bad == "changed_journal":
        relative = "campaign-results/journal.json"
        body = reconstruct.read_json(fabricated["raw"] / phase / relative)
        body["completed_at_utc"] = time_at(1451)
    elif bad == "immutable_analysis":
        relative, body = "analysis/analysis.json", {"fabricated_notice": NOTICE, "changed": True}
    elif bad == "truncated_log":
        relative, body = "confirm-driver.log", b"completion without original log prefix\n"
    else:
        phase = "block-01"
        relative = "campaign-results/" + fabricated["plan"]["sessions"][0]["session_id"] + "/attempt-001/orchestrator.log"
        body = b"FABRICATED changed immutable block bytes\n"
    repack_phase(fabricated, phase, relative, body)
    with pytest.raises(ValueError):
        run(fabricated)
    assert not fabricated["output"].exists()


def test_rejects_missing_true_postfinal_gate_without_writing_output(fabricated):
    journal_path = fabricated["raw"] / "final/campaign-results/journal.json"
    journal = reconstruct.read_json(journal_path)
    journal["input_integrity_gates"] = journal["input_integrity_gates"][:1]
    repack_phase(fabricated, "final", "campaign-results/journal.json", journal)
    repack_phase(fabricated, "completion", "campaign-results/journal.json", journal)
    with pytest.raises(ValueError, match="true post-final"):
        run(fabricated)
    assert not fabricated["output"].exists()


def test_second_precision_terminal_marker_allows_same_second_fractional_ack(fabricated):
    raw = fabricated["raw"]
    relative = "backup-receipts/final.json"
    receipt = reconstruct.read_json(raw / "completion" / relative)
    receipt["verified_at_utc"] = time_at(1900.8)
    repack_phase(fabricated, "completion", relative, receipt)
    # Actual shell date records no fractional seconds. Its precision interval
    # includes the final ACK at .8 seconds before the real .9-second exit.
    repack_phase(fabricated, "completion", "confirm-driver.ended-utc", (time_at(1900).replace("+00:00", "Z") + "\n").encode())
    assert run(fabricated)["status"] == "RECONSTRUCTED_TRANSPORT_VERIFIED"


def test_synthetic_archive_is_never_opted_in(tmp_path):
    archive, raw = tmp_path / "SYNTHETIC-archive", tmp_path / "raw"
    write(archive / "PRESTART_MANIFEST.json", {"synthetic": True})
    write(archive / "campaign.json", {"synthetic": True, "campaign_id": "SYNTHETIC-TEST"})
    raw.mkdir()
    with pytest.raises(ValueError, match="Synthetic replay requires explicit"):
        reconstruct.reconstruct(archive, raw, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_actual_tar_is_inspected_even_when_extracted_tree_is_unchanged(fabricated):
    raw, phase = fabricated["raw"], "block-04"
    with tarfile.open(raw / f"{phase}.tar", "a") as archive:
        archive.add(raw / phase / "campaign.json", arcname="campaign.json", recursive=False)
    control = reconstruct.read_json(raw / f"{phase}.json")
    control.update(tar_sha256=backup.sha(raw / f"{phase}.tar"), tar_bytes=(raw / f"{phase}.tar").stat().st_size)
    write(raw / f"{phase}.json", control)
    report = reconstruct.read_json(raw / f"{phase}.verified.json")
    write(raw / f"{phase}.verified.json", {**report, **control})
    with pytest.raises(ValueError, match="Duplicate tar paths"):
        run(fabricated)
    assert not fabricated["output"].exists()


def test_acknowledged_git_registry_requires_the_complete_identity_entry(fabricated):
    registry = fabricated["registry"]
    original = registry.read_bytes()
    malformed = reconstruct.read_json(registry)
    del malformed["campaigns"][fabricated["campaign"]]["source_commit"]
    write(registry, malformed)
    repo = registry.parents[1]
    git(repo, "add", "research_reporting/campaign_identities.json")
    git(repo, "-c", "user.name=Fabricated Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", NOTICE)
    bad_commit = git(repo, "rev-parse", "HEAD")
    registry.write_bytes(original)  # Current mock registry is valid; acknowledged commit is not.
    relative = "backup-receipts/prestart.json"
    receipt = reconstruct.read_json(fabricated["raw"] / "completion" / relative)
    receipt["git_commit"] = bad_commit
    repack_phase(fabricated, "completion", relative, receipt)
    with pytest.raises(ValueError, match="not registered in the acknowledged prestart commit"):
        run(fabricated)
    assert not fabricated["output"].exists()
