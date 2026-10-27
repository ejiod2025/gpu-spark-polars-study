"""Transport/gating regressions: no benchmark or GPU is executed."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import shlex
import sys
import tarfile

import pytest

spec = importlib.util.spec_from_file_location("research_backup", Path(__file__).resolve().parents[1] / "research_backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(backup.payload(value) if isinstance(value, (dict, list)) else value)


@pytest.fixture
def evidence(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    write(root / "campaign.json.source/workloads.py", b"frozen\n")
    write(root / "readiness/READINESS.json", {"status": "PASS"})
    write(root / "readiness/raw.log", b"readiness raw log\n")
    write(root / "oracles/baseline-sf1.json", {"status": "PASS"})
    write(root / "manifests/sf1/manifest.json", {"scale_factor": 1})
    plan = {"schema_version": "research-campaign-v1", "source": {
        "source_tree_sha256": backup.SOURCE_TREE, "git_head": backup.SOURCE_COMMIT,
        "files_sha256": {"workloads.py": backup.sha(root / "campaign.json.source/workloads.py")}},
        "readiness": {"sha256": backup.sha(root / "readiness/READINESS.json")},
        "datasets": {"1": {"manifest_sha256": backup.sha(root / "manifests/sf1/manifest.json")}},
        "oracles": {"baseline-sf1": {"sha256": backup.sha(root / "oracles/baseline-sf1.json")}},
        "sessions": [{"session_id": f"session-{i:03d}", "block_id": f"block-{i//40+1:02d}",
                      "output_root": str(root / f"campaign-results/session-{i:03d}")}
                     for i in range(400)]}
    write(root / "campaign.json", plan)
    write(root / "campaign.json.sha256", (backup.sha(root / "campaign.json") + "\n").encode())
    return root


def block_journal(root):
    plan = json.loads((root / "campaign.json").read_text())
    sessions = {}
    for i, session in enumerate(plan["sessions"]):
        if i >= 40:
            sessions[session["session_id"]] = {"status": "planned", "attempts": []}
            continue
        directory = Path(session["output_root"]) / "attempt-001"
        gate = directory / "SCIENTIFIC_VALIDATION.json"
        write(gate, {"status": "PASS"})
        write(directory / "orchestrator.log", b"raw log\n")
        write(directory / "run/events.jsonl", b'{"event":"action"}\n')
        write(directory / "run/telemetry.jsonl", b'{"memory":10}\n')
        sessions[session["session_id"]] = {"status": "succeeded", "attempts": [{"status": "succeeded"}],
            "scientific_validation": {"status": "PASS", "path": str(gate), "sha256": backup.sha(gate)}}
    journal = {"campaign_sha256": backup.sha(root / "campaign.json"), "sessions": sessions}
    write(root / "campaign-results/journal.json", journal)
    return journal


def test_prestart_roundtrip_preserves_every_file_and_detects_change(evidence, tmp_path):
    control = backup.snapshot(evidence, tmp_path / "snapshots", "prestart")
    destination = tmp_path / "copy"
    manifest = backup.verify_archive(tmp_path / "snapshots/prestart.tar", control, destination)
    assert manifest["sessions"] == 0
    assert "readiness/raw.log" in manifest["files"]
    assert "manifests/sf1/manifest.json" in manifest["files"]
    backup.verify_archive(tmp_path / "snapshots/prestart.tar", control, destination)
    (destination / "readiness/raw.log").write_bytes(b"tampered\n")
    with pytest.raises(ValueError, match="hash/size differs"):
        backup.verify_directory(destination, control)


def test_snapshot_is_immutable_and_outside_input(evidence, tmp_path):
    with pytest.raises(ValueError, match="outside"):
        backup.snapshot(evidence, evidence / "backup", "prestart")
    backup.snapshot(evidence, tmp_path / "snapshots", "prestart")
    with pytest.raises(ValueError, match="overwrite"):
        backup.snapshot(evidence, tmp_path / "snapshots", "prestart")


def test_prestart_rejects_late_or_missing_manifest(evidence, tmp_path):
    (evidence / "manifests/sf1/manifest.json").unlink()
    with pytest.raises(ValueError, match="Missing copied manifest"):
        backup.snapshot(evidence, tmp_path / "a", "prestart")
    write(evidence / "campaign-results/journal.json", {})
    with pytest.raises(ValueError, match="precede journal"):
        backup.snapshot(evidence, tmp_path / "b", "prestart")


@pytest.mark.parametrize("bad_name", ["raw.parquet", "raw.jar", "raw.pem", "id_rsa", ".env"])
def test_snapshot_excludes_data_and_credentials(evidence, tmp_path, bad_name):
    write(evidence / "readiness" / bad_name, b"must not transfer")
    with pytest.raises(ValueError, match="Excluded"):
        backup.snapshot(evidence, tmp_path / "snapshots", "prestart")


def test_snapshot_rejects_symlink_directory(evidence, tmp_path):
    (evidence / "readiness/link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        backup.snapshot(evidence, tmp_path / "snapshots", "prestart")


def test_block_includes_raw_events_logs_telemetry(evidence, tmp_path):
    journal = block_journal(evidence)
    control = backup.snapshot(evidence, tmp_path / "snapshots", "block-01")
    assert control["sessions"] == 40
    manifest = backup.verify_archive(tmp_path / "snapshots/block-01.tar", control, tmp_path / "copy")
    assert len([name for name in manifest["files"] if name.endswith("telemetry.jsonl")]) == 40
    assert len([name for name in manifest["files"] if name.endswith("events.jsonl")]) == 40
    assert (tmp_path / "copy/campaign-results/journal.json").read_bytes() == backup.payload(journal)


@pytest.mark.parametrize("change", ["running", "retry", "gate"])
def test_incomplete_or_altered_block_is_not_backed_up(evidence, tmp_path, change):
    journal = block_journal(evidence)
    item = journal["sessions"]["session-000"]
    if change == "running":
        item["status"] = "running"
    elif change == "retry":
        item["attempts"].append({})
    else:
        Path(item["scientific_validation"]["path"]).write_text("{}")
    write(evidence / "campaign-results/journal.json", journal)
    with pytest.raises(ValueError):
        backup.snapshot(evidence, tmp_path / "snapshots", "block-01")


def test_receipt_binds_exact_tar_and_prestart_commit(tmp_path):
    control = {"schema_version": backup.SCHEMA, "phase": "prestart", "campaign_sha256": "a"*64,
               "tar_sha256": "b"*64, "manifest_sha256": "c"*64, "files": 5, "sessions": 0}
    write(tmp_path / "control.json", control)
    receipt = {**control, "status": "OFFHOST_SHA256_VERIFIED", "git_commit": "d"*40,
               "prestart_manifest_sha256": "e"*64}
    write(tmp_path / "receipt.json", receipt)
    backup.verify_receipt(tmp_path / "control.json", tmp_path / "receipt.json")
    for key, changed in (("tar_sha256", "x"*64), ("git_commit", None), ("status", "PASS")):
        bad = {**receipt, key: changed}
        write(tmp_path / "receipt.json", bad)
        with pytest.raises(ValueError):
            backup.verify_receipt(tmp_path / "control.json", tmp_path / "receipt.json")


def test_git_prestart_commit_is_exact(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    write(repository / "prestart/example.json", b"original\n")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.email=fixture@example.test", "-c", "user.name=Fixture", "commit", "-qm", "fixture"], check=True)
    commit = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
    backup.check_git_commit(repository / "prestart", repository, commit)
    write(repository / "prestart/example.json", b"changed\n")
    with pytest.raises(ValueError, match="exact prestart"):
        backup.check_git_commit(repository / "prestart", repository, commit)


def test_prestart_seal_and_ack_validate_both_manifests_and_commit(evidence, tmp_path, monkeypatch):
    control = backup.snapshot(evidence, tmp_path / "snapshots", "prestart")
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    directory = repository / "archive"
    backup.verify_archive(tmp_path / "snapshots/prestart.tar", control, directory)
    seal = backup.seal_prestart(directory, control)
    assert "BACKUP_MANIFEST.json" in seal["files_sha256"]
    assert "PRESTART_MANIFEST.json" not in seal["files_sha256"]
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.email=fixture@example.test", "-c", "user.name=Fixture", "commit", "-qm", "fixture"], check=True)
    commit = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
    # Execute the real remote receipt-publisher code locally; do not use SSH.
    original = backup.network_run
    def local_transport(command, **kwargs):
        assert command[0] == "ssh"
        return original(shlex.split(command[-1]), **kwargs)
    monkeypatch.setattr(backup, "network_run", local_transport)
    receipt = backup.acknowledge("ubuntu@fixture", tmp_path / "identity", str(tmp_path / "snapshots"),
                               str(evidence), "prestart", tmp_path / "snapshots", repository, commit,
                               directory, control["campaign_sha256"])
    backup.verify_receipt(tmp_path / "snapshots/prestart.json", evidence / "backup-receipts/prestart.json")
    assert receipt["prestart_manifest_sha256"] == backup.sha(directory / "PRESTART_MANIFEST.json")
    backup.acknowledge("ubuntu@fixture", tmp_path / "identity", str(tmp_path / "snapshots"),
                       str(evidence), "prestart", tmp_path / "snapshots", repository, commit,
                       directory, control["campaign_sha256"])


def test_watcher_never_autoacks_prestart_or_restarts_failed_supervisor(tmp_path, monkeypatch):
    states = iter([{"campaign_sha256": "a"*64, "waiting_phase": "prestart", "driver_exit": None,
                    "driver_alive": True, "counts": {}},
                   {"campaign_sha256": "a"*64, "waiting_phase": None, "driver_exit": "1",
                    "driver_alive": False, "counts": {}}])
    monkeypatch.setattr(backup, "remote_status", lambda *args: next(states))
    monkeypatch.setattr(backup.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(backup, "acknowledge", lambda *args, **kwargs: pytest.fail("prestart autoack"))
    with pytest.raises(RuntimeError, match="Supervisor exited 1"):
        backup.watch("ubuntu@fixture", tmp_path / "key", "/evidence", "/backups", tmp_path / "copies",
                     "a"*64, "/tool.py")
    log = (tmp_path / "copies/watcher.jsonl").read_text()
    assert "MANUAL_PRESTART_ACK_REQUIRED" in log and "WATCHER_STOPPED_ATTENTION" in log


def test_watcher_rejects_wrong_campaign_before_any_transfer(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "remote_status", lambda *args: {"campaign_sha256": "b"*64})
    monkeypatch.setattr(backup, "pull", lambda *args, **kwargs: pytest.fail("wrong campaign transferred"))
    with pytest.raises(ValueError, match="Remote campaign differs"):
        backup.watch("ubuntu@fixture", tmp_path / "key", "/evidence", "/backups", tmp_path / "copies",
                     "a"*64, "/tool.py")


def test_network_timeout_reaps_owned_child_group(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        backup.network_run([sys.executable, "-c", "import time;time.sleep(5)"], timeout=0.02, capture=True)


def test_malicious_archive_member_cannot_escape(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("../escape")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    control = {"tar_sha256": backup.sha(archive), "tar_bytes": archive.stat().st_size}
    with pytest.raises(ValueError, match="Unsafe archive"):
        backup.verify_archive(archive, control, tmp_path / "copy")
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "copy").exists()
    assert not list(tmp_path.glob(".copy.extract-*"))


def test_extraction_failure_leaves_no_partial_published_directory(evidence, tmp_path, monkeypatch):
    control = backup.snapshot(evidence, tmp_path / "snapshots", "prestart")
    original = backup.verify_directory
    def interrupt(*args):
        raise OSError("simulated disk interruption")
    monkeypatch.setattr(backup, "verify_directory", interrupt)
    with pytest.raises(OSError, match="disk interruption"):
        backup.verify_archive(tmp_path / "snapshots/prestart.tar", control, tmp_path / "copy")
    assert not (tmp_path / "copy").exists()
    monkeypatch.setattr(backup, "verify_directory", original)
    backup.verify_archive(tmp_path / "snapshots/prestart.tar", control, tmp_path / "copy")


def test_completion_contains_real_exit_and_closed_log(evidence, tmp_path):
    journal = block_journal(evidence)
    for item in journal["sessions"].values():
        item["status"] = "succeeded"
    journal["completed_at_utc"] = "2026-10-30T00:00:00Z"
    write(evidence / "campaign-results/journal.json", journal)
    write(evidence / "confirm-driver.exit", b"0\n")
    write(evidence / "confirm-driver.pid", b"999999999\n")
    write(evidence / "confirm-driver.ended-utc", b"2026-10-30T00:00:01Z\n")
    write(evidence / "ANALYSIS_READY", b"2026-10-30T00:00:00Z\n")
    write(evidence / "confirm-driver.log", b"closed final log\n")
    control = backup.snapshot(evidence, tmp_path / "snapshots", "completion")
    manifest = backup.verify_archive(tmp_path / "snapshots/completion.tar", control, tmp_path / "copy")
    assert "confirm-driver.exit" in manifest["files"]
    assert "confirm-driver.ended-utc" in manifest["files"]
    assert (tmp_path / "copy/confirm-driver.log").read_bytes() == b"closed final log\n"


def test_watcher_collects_completion_only_after_actual_exit(tmp_path, monkeypatch):
    states = iter([{"campaign_sha256": "a"*64, "waiting_phase": "block-01", "driver_exit": None,
                    "driver_alive": True, "counts": {}},
                   {"campaign_sha256": "a"*64, "waiting_phase": "final", "driver_exit": None,
                    "driver_alive": True, "counts": {}},
                   {"campaign_sha256": "a"*64, "waiting_phase": None, "driver_exit": "0",
                    "driver_alive": True, "counts": {}},
                   {"campaign_sha256": "a"*64, "waiting_phase": None, "driver_exit": "0",
                    "driver_alive": False, "counts": {}}])
    monkeypatch.setattr(backup, "remote_status", lambda *args: next(states))
    monkeypatch.setattr(backup.time, "sleep", lambda seconds: None)
    transferred, acknowledged, commands = [], [], []
    def pull(*args):
        transferred.append(args[3])
        return {"files": 5, "sessions": 40}
    def ack(*args, **kwargs):
        acknowledged.append(args[4])
    monkeypatch.setattr(backup, "pull", pull)
    monkeypatch.setattr(backup, "acknowledge", ack)
    monkeypatch.setattr(backup, "network_run", lambda command, **kwargs: commands.append(command))
    backup.watch("ubuntu@fixture", tmp_path / "key", "/evidence", "/backups", tmp_path / "copies",
                 "a"*64, "/tool.py")
    assert transferred == acknowledged == ["block-01", "final", "completion"]
    assert len(commands) == 1 and "completion" in commands[0][-1]
    assert "COMPLETE_OFFHOST" in (tmp_path / "copies/watcher.jsonl").read_text()


def test_replacement_wrapper_finalizes_after_last_block():
    wrapper = (Path(__file__).resolve().parents[1] / "run_h100_replacement.sh").read_text()
    subprocess.run(["bash", "-n"], input=wrapper.encode(), check=True)
    assert "--max-sessions 40" in wrapper
    assert "confirm-final-integrity.log" in wrapper
    assert wrapper.index("wait_for_backup prestart") < wrapper.index("--max-sessions 40")
    assert wrapper.index("done\n\n# --max-sessions40") < wrapper.index("confirm-final-integrity.log")
