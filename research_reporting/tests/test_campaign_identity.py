"""Exact identity registration tests; no benchmark or remote host is accessed."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPORTING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPORTING))
import campaign_identity as identity
import replay
import audit_complete
import package_evidence

ARCHIVE = REPORTING.parent / "evidence-research/20261026/h100-confirmatory-v1"


def install_registry(monkeypatch, tmp_path, edit):
    registry = json.loads(identity.REGISTRY_PATH.read_text())
    edit(registry)
    path = tmp_path / "TEST_ONLY-identities.json"
    path.write_text(json.dumps(registry))
    monkeypatch.setattr(identity, "REGISTRY_PATH", path)
    return registry


def test_original_archive_still_requires_every_original_anchor():
    checked = replay.verify_archive(ARCHIVE)
    assert checked["campaign_sha256"] == identity.ORIGINAL_CAMPAIGN_SHA256
    assert checked["prestart_sha256"] == identity.ORIGINAL_PRESTART_SHA256
    assert checked["registered_campaign_identity"]["source_commit"] == identity.SOURCE_COMMIT
    assert len(checked["archive_files_sha256"]) == 333


def test_unknown_campaign_or_wrong_prestart_cannot_be_registered_implicitly():
    with pytest.raises(ValueError, match="not in the committed"):
        identity.registered_identity("a" * 64, "b" * 64)
    with pytest.raises(ValueError, match="Prestart checksum differs"):
        identity.registered_identity(identity.ORIGINAL_CAMPAIGN_SHA256, "b" * 64)


def recovery_entry():
    return {"label": "TEST ONLY fixed recovery identity", "prestart_sha256": "b" * 64,
            "source_commit": identity.SOURCE_COMMIT, "source_tree_sha256": identity.SOURCE_TREE_SHA256,
            "campaign_id": identity.RECOVERY_CAMPAIGN_ID, "execution_profile_sha256": "c" * 64}


def test_recovery_identity_binds_actual_registered_profile_hash(monkeypatch, tmp_path):
    install_registry(monkeypatch, tmp_path, lambda registry: registry["campaigns"].update({"a" * 64: recovery_entry()}))
    checked = identity.registered_identity("a" * 64, "b" * 64)
    assert checked["campaign_id"] == identity.RECOVERY_CAMPAIGN_ID
    assert checked["execution_profile_sha256"] == "c" * 64
    assert identity.registered_identity(identity.ORIGINAL_CAMPAIGN_SHA256)["prestart_sha256"] == identity.ORIGINAL_PRESTART_SHA256
    assert identity.registered_identity(identity.ABORTED_V1_CAMPAIGN_SHA256)["prestart_sha256"] == identity.ABORTED_V1_PRESTART_SHA256


@pytest.mark.parametrize("bad", ["arbitrary_id", "malformed_profile", "missing_profile", "duplicate_recovery", "relabel_aborted", "rebind_aborted"])
def test_recovery_registry_is_bounded_and_historical_anchors_stay_fixed(monkeypatch, tmp_path, bad):
    def edit(registry):
        entry = recovery_entry()
        registry["campaigns"]["a" * 64] = entry
        if bad == "arbitrary_id":
            entry["campaign_id"] += "-retry"
        elif bad == "malformed_profile":
            entry["execution_profile_sha256"] = "pending"
        elif bad == "missing_profile":
            del entry["execution_profile_sha256"]
        elif bad == "duplicate_recovery":
            registry["campaigns"]["d" * 64] = entry.copy()
        elif bad == "relabel_aborted":
            registry["campaigns"][identity.ABORTED_V1_CAMPAIGN_SHA256] = entry.copy()
        else:
            registry["campaigns"][identity.ABORTED_V1_CAMPAIGN_SHA256]["prestart_sha256"] = "d" * 64
    install_registry(monkeypatch, tmp_path, edit)
    with pytest.raises(ValueError):
        identity.registered_identity("a" * 64)


@pytest.mark.parametrize("mutation", ["original", "source", "malformed", "duplicate"])
def test_registry_cannot_rebind_original_or_accept_other_source(monkeypatch, tmp_path, mutation):
    def edit(registry):
        entry = registry["campaigns"][identity.ORIGINAL_CAMPAIGN_SHA256]
        if mutation == "original":
            entry["prestart_sha256"] = "b" * 64
        elif mutation == "source":
            entry["source_tree_sha256"] = "c" * 64
        elif mutation == "malformed":
            registry["campaigns"]["not-a-sha"] = entry.copy()
    install_registry(monkeypatch, tmp_path, edit)
    if mutation == "duplicate":
        identity.REGISTRY_PATH.write_text('{"schema_version":"x","schema_version":"y"}')
    with pytest.raises(ValueError):
        identity.registered_identity(identity.ORIGINAL_CAMPAIGN_SHA256)


def test_additional_exact_identity_accepts_only_its_unchanged_source_archive(monkeypatch, tmp_path):
    # TEST ONLY: exercise registering new genuine-run anchors without writing the
    # actual committed registry or claiming any replacement benchmark took place.
    archive = tmp_path / "TEST_ONLY-replacement-prestart"
    shutil.copytree(ARCHIVE, archive)
    campaign = json.loads((archive / "campaign.json").read_text())
    campaign["campaign_id"] = "TEST_ONLY replacement anchor mechanism"
    (archive / "campaign.json").write_bytes(replay.canonical_bytes(campaign))
    campaign_sha = replay.sha256(archive / "campaign.json")
    (archive / "campaign.json.sha256").write_text(campaign_sha + "\n")
    manifest = json.loads((archive / "PRESTART_MANIFEST.json").read_text())
    manifest["campaign_sha256"] = campaign_sha
    for name in ("campaign.json", "campaign.json.sha256"):
        manifest["files_sha256"][name] = replay.sha256(archive / name)
    (archive / "PRESTART_MANIFEST.json").write_bytes(replay.canonical_bytes(manifest))
    prestart_sha = replay.sha256(archive / "PRESTART_MANIFEST.json")
    with pytest.raises(ValueError, match="not in the committed"):
        replay.verify_archive(archive)
    def edit(registry):
        registry["campaigns"][campaign_sha] = {
            "label": "TEST ONLY additional exact identity", "prestart_sha256": prestart_sha,
            "source_commit": identity.SOURCE_COMMIT, "source_tree_sha256": identity.SOURCE_TREE_SHA256}
    install_registry(monkeypatch, tmp_path, edit)
    checked = replay.verify_archive(archive)
    assert checked["campaign_sha256"] == campaign_sha
    assert checked["registered_campaign_identity"]["prestart_sha256"] == prestart_sha
    assert checked["registered_campaign_identity"]["registry_sha256"] == hashlib.sha256(identity.REGISTRY_PATH.read_bytes()).hexdigest()
    source = archive / "campaign.json.source/research_validate.py"
    source.write_text(source.read_text() + "\n# TEST ONLY tamper\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        replay.verify_archive(archive)


@pytest.mark.parametrize("bad", [None, "legacy_registration", "wrong_profile_hash", "wrong_profile_id", "wrong_plan_id"])
def test_recovery_prestart_binds_registered_profile_before_frozen_code(monkeypatch, tmp_path, bad):
    # A minimal inert profile proves only the archive hash-binding mechanism.
    # The complete auditor separately requires the full execution profile,
    # actual diagnostic, 24 receipts and all 160 real child-driver flag tables.
    archive = tmp_path / "FABRICATED-profile-binding"
    shutil.copytree(ARCHIVE, archive)
    campaign = replay.read_json(archive / "campaign.json")
    campaign["campaign_id"] = identity.RECOVERY_CAMPAIGN_ID if bad != "wrong_plan_id" else "arbitrary-retry"
    (archive / "campaign.json").write_bytes(replay.canonical_bytes(campaign))
    campaign_sha = replay.sha256(archive / "campaign.json")
    (archive / "campaign.json.sha256").write_text(campaign_sha + "\n")
    profile = {"campaign_id": identity.RECOVERY_CAMPAIGN_ID if bad != "wrong_profile_id" else "arbitrary-retry",
               "source_commit": identity.SOURCE_COMMIT, "source_tree_sha256": identity.SOURCE_TREE_SHA256,
               "fabricated_notice": "TEST ONLY archive hash binding; not a complete profile"}
    (archive / "EXECUTION_PROFILE.json").write_bytes(replay.canonical_bytes(profile))
    profile_sha = replay.sha256(archive / "EXECUTION_PROFILE.json")
    manifest = replay.read_json(archive / "PRESTART_MANIFEST.json")
    manifest["campaign_sha256"] = campaign_sha
    for name in ("campaign.json", "campaign.json.sha256", "EXECUTION_PROFILE.json"):
        manifest["files_sha256"][name] = replay.sha256(archive / name)
    (archive / "PRESTART_MANIFEST.json").write_bytes(replay.canonical_bytes(manifest))
    def edit(registry):
        entry = recovery_entry()
        entry["prestart_sha256"] = replay.sha256(archive / "PRESTART_MANIFEST.json")
        entry["execution_profile_sha256"] = profile_sha if bad != "wrong_profile_hash" else "d" * 64
        if bad == "legacy_registration":
            entry.pop("campaign_id")
            entry.pop("execution_profile_sha256")
        registry["campaigns"][campaign_sha] = entry
    install_registry(monkeypatch, tmp_path, edit)
    if bad is not None:
        with pytest.raises(ValueError, match="execution profile"):
            replay.verify_archive(archive)
    else:
        checked = replay.verify_archive(archive)
        assert checked["registered_campaign_identity"]["execution_profile_sha256"] == profile_sha


@pytest.mark.parametrize("entrypoint", ["collector", "block_verifier", "auditor", "packager"])
@pytest.mark.parametrize("tamper", ["unknown_campaign", "wrong_prestart"])
def test_every_real_entrypoint_refuses_unregistered_anchors_before_execution(tmp_path, entrypoint, tamper):
    root = tmp_path / "TEST_ONLY-untrusted-evidence"
    root.mkdir()
    campaign = (ARCHIVE / "campaign.json").read_bytes()
    prestart = (ARCHIVE / "PRESTART_MANIFEST.json").read_bytes()
    if tamper == "unknown_campaign":
        value = json.loads(campaign)
        value["campaign_id"] = "TEST_ONLY unknown campaign"
        campaign = replay.canonical_bytes(value)
    else:
        prestart += b"\n"
    (root / "campaign.json").write_bytes(campaign)
    (root / "campaign.json.sha256").write_text(hashlib.sha256(campaign).hexdigest() + "\n")
    (root / "PRESTART_MANIFEST.json").write_bytes(prestart)
    expected = "not in the committed" if tamper == "unknown_campaign" else "Prestart checksum differs"
    if entrypoint == "auditor":
        report = audit_complete.audit_complete(root / "campaign.json", root / "absent-results", root,
            root / "absent-validation", root / "absent-analysis", tmp_path / "audit-output")
        assert report["status"] == "FAIL"
        assert expected in json.dumps(report)
    elif entrypoint == "packager":
        for name, body in [("confirm-driver.exit", "0\n"), ("ANALYSIS_READY", ""), ("confirm-driver.ended-utc", "TEST ONLY")]:
            (root / name).write_text(body)
        with pytest.raises(ValueError, match=expected):
            package_evidence.check_completion(root, root / "absent-audit", False)
    else:
        script = REPORTING.parent / "operations" / ("collect_core_checkpoint.py" if entrypoint == "collector" else "verify_core_checkpoint.py")
        args = ["--evidence", str(root), "--block", "block-01"] if entrypoint == "collector" else [
            "--archive", str(root), "--checkpoint-tar", str(root / "absent.tar")]
        output = tmp_path / "refused-output"
        result = subprocess.run([sys.executable, "-B", str(script), *args, "--output", str(output)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode != 0 and expected in result.stderr
        assert not output.exists()
