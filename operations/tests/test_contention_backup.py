"""Fabricated local transport fixtures; never real experiments, SSH or GPU jobs."""
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from operations import contention_backup as backup


def fixture_tree(tmp_path, monkeypatch, phase="pilot"):
    root = tmp_path / "evidence"
    root.mkdir()
    plan = {"schema_version": "h100-contention-plan-v1", "experiment_id": "h100-contention-20261028",
            "synthetic": False, "test_fixture": "FABRICATED LOCAL TRANSPORT ONLY"}
    (root / "CONTENTION_PLAN.json").write_text(json.dumps(plan))
    digest = backup.common.sha(root / "CONTENTION_PLAN.json")
    (root / "CONTENTION_PLAN.sha256").write_text(digest + "\n")
    (root / "WAITING_FOR_OFFHOST_BACKUP").write_text(phase + "\n")
    (root / "TEST_ONLY.txt").write_text("Fabricated transport mechanism fixture. No study observations.\n")
    (root / phase).mkdir()
    (root / phase / "PHASE.json").write_text(json.dumps({"status": "PASS", "phase": phase, "plan_sha256": digest,
                                                        "test_fixture": "FABRICATED"}))
    monkeypatch.setattr(backup, "require_idle", lambda: {"eligible": True, "test_fixture": "MOCKED NO GPU"})
    snapshots = tmp_path / "snapshots"
    return root, snapshots, digest


def test_snapshot_roundtrip_exact_files_and_no_400_claim(tmp_path, monkeypatch):
    root, snapshots, digest = fixture_tree(tmp_path, monkeypatch)
    control = backup.snapshot(root, snapshots, "pilot")
    assert control["plan_sha256"] == digest
    assert "sessions" not in control and "campaign_sha256" not in control
    destination = tmp_path / "download"
    manifest = backup.verify_archive(snapshots / "pilot.tar", control, destination)
    assert manifest["schema_version"] == backup.SCHEMA
    for path in root.rglob("*"):
        if path.is_file():
            assert (destination / path.relative_to(root)).read_bytes() == path.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        backup.snapshot(root, snapshots, "pilot")
    (destination / "TEST_ONLY.txt").write_text("tamper")
    with pytest.raises(ValueError, match="bytes differ"):
        backup.verify_archive(snapshots / "pilot.tar", control, destination)


@pytest.mark.parametrize("kind", ["symlink", "parquet", "key", "env", "fifo"])
def test_rejects_unsafe_or_out_of_scope_source(tmp_path, monkeypatch, kind):
    root, snapshots, _ = fixture_tree(tmp_path, monkeypatch)
    path = root / {"parquet": "data.parquet", "key": "secret.key", "env": ".env", "symlink": "link", "fifo": "pipe"}[kind]
    if kind == "symlink":
        path.symlink_to(root / "TEST_ONLY.txt")
    elif kind == "fifo":
        import os
        os.mkfifo(path)
    else:
        path.write_text("fabricated")
    with pytest.raises(ValueError):
        backup.snapshot(root, snapshots, "pilot")
    assert not (snapshots / "pilot.json").exists()


def test_refuses_running_or_non_boundary_snapshot(tmp_path, monkeypatch):
    root, snapshots, _ = fixture_tree(tmp_path, monkeypatch)
    (root / "pilot/PHASE.json").write_text(json.dumps({"phase": "pilot", "status": "RUNNING"}))
    with pytest.raises(ValueError, match="terminal"):
        backup.snapshot(root, snapshots, "pilot")
    (root / "WAITING_FOR_OFFHOST_BACKUP").write_text("main")
    with pytest.raises(ValueError, match="boundary"):
        backup.snapshot(root, snapshots, "pilot")


@pytest.mark.parametrize("bad_name,link", [("../escape", False), ("/absolute", False), ("link", True)])
def test_safe_extraction_rejects_traversal_and_links(tmp_path, monkeypatch, bad_name, link):
    root, snapshots, _ = fixture_tree(tmp_path, monkeypatch)
    control = backup.snapshot(root, snapshots, "pilot")
    original = snapshots / "pilot.tar"
    evil = tmp_path / "evil.tar"
    with tarfile.open(original) as source, tarfile.open(evil, "w") as archive:
        for member in source.getmembers():
            archive.addfile(member, source.extractfile(member))
        member = tarfile.TarInfo(bad_name)
        if link:
            member.type, member.linkname = tarfile.SYMTYPE, "../escape"
            archive.addfile(member)
        else:
            member.size = 4
            archive.addfile(member, io.BytesIO(b"evil"))
    altered = control | {"tar_sha256": backup.common.sha(evil), "tar_bytes": evil.stat().st_size}
    with pytest.raises(ValueError):
        backup.verify_archive(evil, altered, tmp_path / "extracted")
    assert not (tmp_path / "escape").exists()


def test_rejects_tar_and_control_drift(tmp_path, monkeypatch):
    root, snapshots, digest = fixture_tree(tmp_path, monkeypatch)
    control = backup.snapshot(root, snapshots, "pilot")
    with pytest.raises(ValueError, match="plan identity"):
        backup.validate_control(control, "a" * 64)
    with pytest.raises(ValueError, match="SHA256"):
        backup.verify_archive(snapshots / "pilot.tar", control | {"tar_sha256": "a" * 64}, tmp_path / "out")
    with pytest.raises(ValueError, match="Duplicate JSON"):
        backup.decode(b'{"a":1,"a":2}')


def test_acknowledges_only_verified_exact_bytes_atomically_without_ssh(tmp_path, monkeypatch):
    root, snapshots, digest = fixture_tree(tmp_path, monkeypatch)
    control = backup.snapshot(root, snapshots, "pilot")
    destination = tmp_path / "local"
    destination.mkdir()
    import shutil
    shutil.copyfile(snapshots / "pilot.tar", destination / "pilot.tar")
    (destination / "pilot.json").write_bytes((snapshots / "pilot.json").read_bytes())
    backup.verify_archive(destination / "pilot.tar", control, destination / "pilot")
    report = control | {"status": "OFFHOST_SHA256_VERIFIED", "verified_at_utc": backup.utc(),
                        "local_archive": str(destination / "pilot.tar"), "local_directory": str(destination / "pilot")}
    (destination / "pilot.verified.json").write_text(json.dumps(report))
    calls = []
    def local_remote(command, *, input=None, capture=False, **kwargs):
        # Execute only the generated remote receipt writer on this local fixture;
        # never execute the ssh prefix or open any network connection.
        argv = shlex.split(command[-1])
        assert argv[:2] == ["python3", "-c"]
        calls.append(argv)
        return subprocess.run(argv, input=input, capture_output=True, check=True).stdout
    monkeypatch.setattr(backup.common, "network_run", local_remote)
    result = backup.acknowledge("test@fixture", tmp_path / "unused-ssh-key", str(root), str(snapshots), "pilot", destination, digest)
    assert result["status"] == "OFFHOST_SHA256_VERIFIED"
    assert json.loads((root / "backup-receipts/pilot.json").read_text())["tar_sha256"] == control["tar_sha256"]
    assert len(calls) == 1
    (destination / "pilot/TEST_ONLY.txt").write_text("changed")
    with pytest.raises(ValueError):
        backup.acknowledge("test@fixture", tmp_path / "unused-ssh-key", str(root), str(snapshots), "pilot", destination, digest)
    assert len(calls) == 1


def test_watcher_treats_missing_control_as_pending_and_never_starts_jobs(tmp_path, monkeypatch):
    expected = "a" * 64
    states = iter([{"waiting_phase": "pilot", "plan_sha256": expected, "control_ready": False},
                   {"waiting_phase": "main", "plan_sha256": expected, "control_ready": False},
                   {"waiting_phase": "main", "plan_sha256": expected, "control_ready": True}])
    monkeypatch.setattr(backup, "remote_status", lambda *args: next(states))
    calls = []
    monkeypatch.setattr(backup, "pull", lambda *args: calls.append(("pull", args[3])) or {"files": 3})
    monkeypatch.setattr(backup, "acknowledge", lambda *args: calls.append(("ack", args[4])))
    monkeypatch.setattr(backup.time, "sleep", lambda value: None)
    args = SimpleNamespace(plan_sha256=expected, interval_seconds=10, destination=tmp_path / "local",
                           host="test@fixture", identity_file=tmp_path / "unused-key", evidence="/fixture/evidence", snapshot_root="/fixture/snapshots")
    backup.watch(args)
    assert calls == [("pull", "main"), ("ack", "main")]
    rows = [json.loads(line) for line in (args.destination / "watcher.jsonl").read_text().splitlines()]
    assert rows[-1]["event"] == "OFFHOST_BACKUP_ACKNOWLEDGED"
