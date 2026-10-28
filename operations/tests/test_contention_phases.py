"""Fabricated checkpoint boundaries only; no benchmark or cloud access."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from operations import contention_phases as phases


def checkpoint_fixture(tmp_path, monkeypatch, **receipt_changes):
    root = tmp_path / "SYNTHETIC_BOUNDARY_ONLY"
    root.mkdir()
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    control = {"phase": "pilot", "plan_sha256": "a" * 64,
               "tar_sha256": "b" * 64, "manifest_sha256": "c" * 64}
    (snapshots / "pilot.json").write_text(json.dumps(control))
    (root / "backup-receipts").mkdir()
    receipt = {**control, "status": "OFFHOST_SHA256_VERIFIED", **receipt_changes}
    (root / "backup-receipts/pilot.json").write_text(json.dumps(receipt))
    calls = []
    monkeypatch.setattr(phases.subprocess, "run", lambda command, **kwargs: calls.append(command))
    args = SimpleNamespace(plan=root / "CONTENTION_PLAN.json", backup_tool=tmp_path / "fixture-only.py",
                           snapshot_root=snapshots, plan_sha256="a" * 64, ack_timeout_seconds=1)
    return root, args, calls


def test_ack_binds_actual_archive_before_next_phase(tmp_path, monkeypatch):
    root, args, calls = checkpoint_fixture(tmp_path, monkeypatch)
    state = {}
    phases.checkpoint(args, state, "pilot")
    assert len(calls) == 1
    assert state["offhost_checkpoints"][0]["phase"] == "pilot"
    assert not (root / "WAITING_FOR_OFFHOST_BACKUP").exists()


@pytest.mark.parametrize("change", [{"tar_sha256": "d" * 64},
                                    {"plan_sha256": "d" * 64},
                                    {"status": "TRANSFER_ONLY"}])
def test_unverified_or_wrong_ack_never_advances(tmp_path, monkeypatch, change):
    root, args, calls = checkpoint_fixture(tmp_path, monkeypatch, **change)
    state = {}
    with pytest.raises(ValueError, match="does not bind"):
        phases.checkpoint(args, state, "pilot")
    assert not state.get("offhost_checkpoints")
    assert (root / "WAITING_FOR_OFFHOST_BACKUP").read_text().strip() == "pilot"


def test_changed_prestart_refused_before_any_phase(tmp_path, monkeypatch):
    root = tmp_path / "SYNTHETIC_BOUNDARY_ONLY"
    root.mkdir()
    (root / "CONTENTION_PLAN.json").write_text("{}")
    calls = []
    monkeypatch.setattr(phases.subprocess, "run", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match="prestart differs"):
        phases.main(["--plan", str(root / "CONTENTION_PLAN.json"), "--plan-sha256", "a" * 64,
                     "--prestart-sha256", "b" * 64, "--backup-tool", str(tmp_path / "fixture-only.py"),
                     "--snapshot-root", str(tmp_path / "snapshots")])
    assert not calls
