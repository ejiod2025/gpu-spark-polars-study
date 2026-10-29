"""Boundary tests use fabricated evidence, mocked registry and mocked transports.

These fixtures are not scientific results; no API credential or VM is accessed.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

spec = importlib.util.spec_from_file_location("lambda_shutdown_guard", Path(__file__).resolve().parents[1] / "lambda_shutdown_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(guard.backup.payload(value) if isinstance(value, (dict, list)) else value)
    return path


def ref(base, path):
    return {"path": str(path.relative_to(base)), "sha256": guard.sha(path)}


def fabricated_contention(base, campaign, profile, journal, original_results, original_schedule, stopped=False):
    """Entirely fabricated terminal mechanics; never real contention findings."""
    code = base / "contention/code"
    audit_code = write(code / "contention_replay.py", guard.CONTENTION_AUDITOR_PATH.read_bytes())
    handoff_code = write(code / "contention_handoff.py", guard.HANDOFF_SOURCE_PATH.read_bytes())
    source_copies = [audit_code, handoff_code,
        write(code / "contention_backup.py", Path(guard.contention_backup.__file__).read_bytes()),
        write(code / "research_backup.py", Path(guard.backup.__file__).read_bytes())]
    canonical = base / "contention/prestart"
    plan = {"schema_version": "h100-contention-plan-v1", "experiment_id": guard.CONTENTION_EXPERIMENT_ID,
        "synthetic": False, "fixture_only": "FABRICATED; no real waves or study results",
        "source_commit": guard.campaign_identity.SOURCE_COMMIT,
        "reference_campaign_sha256": campaign, "reference_execution_profile_sha256": guard.sha(profile),
        "pilot": [{"wave_id": f"pilot-{i:02d}"} for i in range(1, 5)],
        "main": [{"wave_id": f"main-{i:02d}"} for i in range(1, 17)]}
    plan_path = write(canonical / "CONTENTION_PLAN.json", plan)
    plan_sha = guard.sha(plan_path)
    write(canonical / "CONTENTION_PLAN.sha256", (plan_sha + "\n").encode())
    for number in range(63):
        write(canonical / f"FABRICATED-source-{number:02d}.txt", f"FABRICATED SOURCE {number}\n".encode())
    files = {str(p.relative_to(canonical)): guard.sha(p) for p in canonical.iterdir()}
    prestart = write(canonical / "CONTENTION_PRESTART_MANIFEST.json", {
        "schema_version": "h100-contention-prestart-v1", "status": "FROZEN_NOT_RUN", "plan_sha256": plan_sha,
        "files_sha256": files})
    prestart_sha = guard.sha(prestart)
    canonical_files = {str(p.relative_to(canonical)): guard.sha(p) for p in canonical.iterdir()}
    inputs = {str(p.resolve()): guard.sha(p) for p in [*canonical.iterdir(), audit_code, handoff_code]}
    small = list(canonical.iterdir()) + source_copies
    snapshots, controls = {}, {}
    phases = ("failed",) if stopped else ("pilot", "main")
    for phase in phases:
        directory = base / "contention/raw" / phase
        shutil.copytree(canonical, directory)
        write(directory / f"{phase}/FABRICATED-RAW.json", {"fixture_only": True, "phase": phase})
        raw_files = {str(p.relative_to(directory)): {"sha256": guard.sha(p), "bytes": p.stat().st_size}
                     for p in directory.rglob("*") if p.is_file()}
        manifest = write(directory / guard.contention_backup.MANIFEST, {
            "schema_version": guard.contention_backup.SCHEMA, "phase": phase, "plan_sha256": plan_sha,
            "files": raw_files})
        tar_path = base / f"contention/raw/{phase}.tar"
        with tarfile.open(tar_path, "w") as archive:
            for p in sorted(directory.rglob("*")):
                if p.is_file(): archive.add(p, arcname=str(p.relative_to(directory)))
        control = {"schema_version": guard.contention_backup.SCHEMA, "phase": phase, "plan_sha256": plan_sha,
                   "files": len(raw_files), "manifest_sha256": guard.sha(manifest),
                   "tar_sha256": guard.sha(tar_path), "tar_bytes": tar_path.stat().st_size}
        control_path = write(base / f"contention/raw/{phase}.json", control)
        receipt_path = write(base / f"contention/raw/{phase}.verified.json", {**control,
            "status": "OFFHOST_SHA256_VERIFIED", "local_archive": str(tar_path), "local_directory": str(directory)})
        snapshots[phase] = {"archive": ref(base, tar_path), "control": ref(base, control_path),
            "receipt": ref(base, receipt_path), "directory": str(directory), "manifest_sha256": control["manifest_sha256"]}
        controls[phase] = control
        for p in [tar_path, control_path, receipt_path, *[p for p in directory.rglob("*") if p.is_file()]]:
            inputs[str(p.resolve())] = guard.sha(p)
        small += [control_path, receipt_path, manifest]
    metadata = base / "contention/terminal"
    checkpoints = []
    for phase in phases:
        remote_ack = write(metadata / "backup-receipts" / f"{phase}.json",
                           (base / snapshots[phase]["receipt"]["path"]).read_bytes())
        checkpoints.append({"phase": phase, "receipt_sha256": guard.sha(remote_ack),
            "control_sha256": snapshots[phase]["control"]["sha256"]})
        inputs[str(remote_ack.resolve())] = guard.sha(remote_ack)
        small.append(remote_ack)
    driver_status = "FAILED" if stopped else "COMPLETE_OFFHOST"
    driver = write(metadata / "PHASE_DRIVER.json", {"schema_version": "h100-contention-phases-driver-v1",
        "status": driver_status, "plan_sha256": plan_sha, "prestart_sha256": prestart_sha,
        "completed_at_utc": "2026-10-01T00:31:00Z", "offhost_checkpoints": [] if stopped == "late_ack" else checkpoints,
        **({"error": {"type": "FABRICATED_ERROR", "message": "Preserved fixture failure"}} if stopped else {})})
    inputs[str(driver.resolve())] = guard.sha(driver); small.append(driver)
    handoff_root = base / "contention/handoff"
    scheduler = {"pid": 321, "created": 1234.5, "cmdline": ["FABRICATED research_campaign.py"], "cwd": "/FABRICATED"}
    old_attempt = {"status": "running", "run_root": "/FABRICATED-VM/campaign-results/session-000/attempt-001/run"}
    held_journal = write(handoff_root / "ORIGINAL_HELD_JOURNAL.json", {"campaign_sha256": campaign,
        "sessions": {"session-000": {"status": "running", "attempts": [old_attempt]}}})
    held_session = write(handoff_root / "ORIGINAL_HELD_SESSION.json", {"campaign_sha256": campaign,
        "session": {"session_id": "session-000"}, "attempt": old_attempt})
    handoff_paths = {"held_journal": held_journal, "held_session": held_session,
        "config": write(handoff_root / "CONFIG.json", {"schema_version": "h100-contention-handoff-v1",
                        "scheduler": scheduler, "handoff_source_sha256": guard.sha(handoff_code)}),
        "resume": write(handoff_root / "RESUMED.json", {"schema_version": "h100-contention-handoff-resume-v1",
            "status": "RESUMED", "contention_started": True, "scheduler": scheduler,
            "actor": "detached-guardian" if stopped == "guardian" else "handoff-manager", "resumed_at_utc": "2026-10-01T00:33:00Z"}),
        "state": write(handoff_root / "STATE.json", {"phase": "CONTENTION_RUNNING" if stopped == "guardian" else "HANDOFF_ERROR" if stopped else "COMPLETE_RESUMED",
            **({"error": {"type": "FABRICATED_ERROR"}} if stopped else {}),
            "contention_started": True, "snapshot": {"journal_sha256": guard.sha(held_journal), "attempt": old_attempt}}),
        "attempt_inventory": write(handoff_root / "ORIGINAL_ATTEMPT_FILES.json", {"session.json": {"sha256": guard.sha(held_session)}}),
        "original_results": original_results, "original_schedule": original_schedule,
        "original_runner_drained": write(handoff_root / "ORIGINAL_RUNNER_DRAINED.json", {"completed_at_utc": "2026-10-01T00:00:30Z"}),
        "owned_groups": write(handoff_root / "OWNED_GROUPS_AFTER_CLEANUP.json", {"live_members": []}),
        "residual_before_profile": write(handoff_root / "RESIDUAL_SCAN_before-profile.json", {"status": "CLEAR", "contention_processes": []}),
        "residual_before_signal": write(handoff_root / "RESIDUAL_SCAN_before-signal.json", {"status": "CLEAR", "contention_processes": []}),
        "driver_spec": write(handoff_root / "DRIVER_SPEC.json", {"fixture_only": True})}
    counts = {"1": 57, "10": 123, "30": 185, "100": 371, "300": 918}
    for name, label, stamp in (("profile_before", "before-contention", "00:01"), ("profile_after", "before-original-resume", "00:32")):
        handoff_paths[name] = write(handoff_root / (name + ".json"), {
            "schema_version": "h100-contention-interlude-profile-check-v1", "status": "PASS", "label": label,
            "campaign_sha256": campaign, "execution_profile_sha256": guard.sha(profile),
            "completed_at_utc": "2026-10-01T" + stamp + ":00Z",
            "exclusive_host_before": {"eligible": True, "nvidia_smi_returncode": 0, "gpu_compute_clients": "", "benchmark_processes": []},
            "exclusive_host_after": {"eligible": True, "nvidia_smi_returncode": 0, "gpu_compute_clients": "", "benchmark_processes": []},
            "input_integrity": {"checks": [{"scale": s, "files": n, "status": "pass"} for s, n in counts.items()]}})
    for p in handoff_paths.values(): inputs[str(p.resolve())] = guard.sha(p)
    small += list(handoff_paths.values())
    disposition = {phase: {"status": "PASS", "planned_waves": n, "attempted_waves": n,
        "passed_waves": n, "failed_waves": 0, "unattempted_waves": []} for phase, n in (("pilot", 4), ("main", 16))}
    if stopped:
        attempted = 0 if stopped == "prelaunch" else 2
        disposition["pilot"].update(status="FAIL", attempted_waves=attempted, passed_waves=0 if attempted == 0 else 1,
            failed_waves=0 if attempted == 0 else 1,
            unattempted_waves=[w["wave_id"] for w in plan["pilot"][attempted:]], recorded_error={"type": "FABRICATED_ERROR"})
        disposition["main"].update(status="NOT_ATTEMPTED_AFTER_STOP" if stopped == "closed_suffix" else "NOT_OBSERVED",
            attempted_waves=0, passed_waves=0,
            unattempted_waves=[w["wave_id"] for w in plan["main"]])
    receipt = {"schema_version": "h100-contention-replay-v1", "experiment_id": guard.CONTENTION_EXPERIMENT_ID,
        "synthetic": False, "status": "STOPPED_WITH_FAILURE" if stopped else "COMPLETE", "integrity_status": "PASS",
        "publication_ready": True, "findings": [], "plan_sha256": plan_sha, "prestart_sha256": prestart_sha,
        "reference_campaign_sha256": campaign, "reference_execution_profile_sha256": guard.sha(profile),
        "audit_source_sha256": guard.sha(audit_code), "canonical_directory": str(canonical),
        "canonical_plan": ref(base, plan_path), "canonical_prestart": ref(base, prestart),
        "canonical_files_sha256": canonical_files, "input_files_sha256": inputs, "snapshots": snapshots,
        "terminal_driver": {"status": driver_status, "terminal": True, "path": str(driver), "sha256": guard.sha(driver),
            "remote_receipts": {phase: {"path": str(metadata / 'backup-receipts' / (phase + '.json')),
                "sha256": guard.sha(metadata / 'backup-receipts' / (phase + '.json'))} for phase in phases}},
        "disposition": disposition, "counts": {"successful_outputs": 12 if stopped else 460, "expected_strict_rejections_observed": 0 if stopped else 10},
        "handoff": {"status": "PASS", "same_scheduler_resumed": True, "no_action_overlap": True,
            "all_original_input_files": 1654, "reference_campaign_sha256": campaign, "execution_profile_sha256": guard.sha(profile),
            "source_sha256": guard.sha(handoff_code), "scheduler": scheduler,
            "files": {name: {"path": str(p), "sha256": guard.sha(p)} for name, p in handoff_paths.items()},
            "first_contention_action_utc_epoch": datetime(2026, 10, 1, 0, 2, tzinfo=timezone.utc).timestamp(),
            "last_contention_host_result_utc_epoch": datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc).timestamp()}}
    replay_path = write(base / "contention/replay/CONTENTION_REPLAY.json", receipt)
    preservation = {"schema_version": "h100-contention-private-preservation-v1", "status": "PRIVATE_DOWNLOAD_VERIFIED",
        "private": True, "experiment_id": guard.CONTENTION_EXPERIMENT_ID, "plan_sha256": plan_sha,
        "prestart_sha256": prestart_sha, "replay_sha256": guard.sha(replay_path),
        "repository_url": "git@github.com:fixture/private-fixture.git", "raw_backups": {}}
    for phase in phases:
        archive = base / snapshots[phase]["archive"]["path"]
        retrieved = write(base / f"contention/retrieved/{phase}.tar", archive.read_bytes())
        preservation["raw_backups"][phase] = {"archive_sha256": guard.sha(archive), "archive_bytes": archive.stat().st_size,
            "retrieved_archive": ref(base, retrieved), "retrieval_verified": True,
            "storage": {"repository_url": preservation["repository_url"], "release_tag": "FABRICATED-private-release",
                        "asset_ids": [1], "asset_names": [phase + ".tar"]}}
    preservation_path = write(base / "contention/PRESERVATION.json", preservation)
    small += [replay_path, preservation_path]
    review = {"status": "COMPLETE", "experiment_id": guard.CONTENTION_EXPERIMENT_ID, "plan_sha256": plan_sha,
              "prestart_sha256": prestart_sha, "replay_sha256": guard.sha(replay_path), "disposition": receipt["status"]}
    return {"replay": replay_path, "preservation": preservation_path, "review": review, "small": small,
            "audit_code": audit_code, "handoff_code": handoff_code, "plan_sha": plan_sha, "prestart_sha": prestart_sha,
            "handoff_paths": handoff_paths, "snapshots": snapshots, "driver": driver, "canonical": canonical}


@pytest.fixture
def complete(tmp_path, monkeypatch, request):
    base = tmp_path / "evidence"
    base.mkdir()
    expected = {"instance_id": guard.AUTHORIZED_INSTANCE_ID, "ip": guard.AUTHORIZED_IP,
                "gpu_uuid": guard.AUTHORIZED_GPU_UUID, "gpu_name": guard.AUTHORIZED_GPU_NAME,
                "driver_version": guard.AUTHORIZED_DRIVER, "api_instance_type": guard.AUTHORIZED_INSTANCE_TYPE,
                "api_gpu_description": "H100 fixture description"}
    stamp = datetime.now(timezone.utc).isoformat()
    end, post = "2026-10-01T01:00:00Z", "2026-10-01T01:01:00Z"
    sessions = [{"session_id": f"session-{i:03d}"} for i in range(400)]
    # Frozen C84 make_plan omits the synthetic field for genuine campaigns.
    campaign_path = write(base / "campaign.json", {"campaign_id": guard.RECOVERY_CAMPAIGN_ID,
                          "output_root": "/FABRICATED-VM/campaign-results",
                          "sessions": sessions, "datasets": {"1": {}, "10": {}}})
    campaign = guard.sha(campaign_path)
    prestart = base / "prestart"
    write(prestart / "source.txt", b"fixture frozen source\n")
    profile_path = write(prestart / guard.PROFILE_NAME, {"schema_version": "h100-jvm-execution-profile-v1",
        "campaign_id": guard.RECOVERY_CAMPAIGN_ID, "source_commit": guard.campaign_identity.SOURCE_COMMIT,
        "source_tree_sha256": guard.campaign_identity.SOURCE_TREE_SHA256, "fixture_only": True})
    profile_sha = guard.sha(profile_path)
    gates = [{"schema_version": "h100-jvm-execution-profile-gate-v1", "phase": phase, "status": "PASS",
              "profile_sha256": profile_sha, "fixture_only": True} for phase in guard.PROFILE_GATE_PHASES]
    def gate_bytes(selected):
        return b"".join((json.dumps(gate, sort_keys=True) + "\n").encode() for gate in selected)
    archived_gates = write(prestart / guard.PROFILE_GATES_NAME, gate_bytes(gates[:2]))
    prestart_path = write(prestart / "PRESTART_MANIFEST.json", {
        "schema_version": "h100-confirmatory-prestart-archive-v1", "status": "PASS", "campaign_sha256": campaign,
        "files_sha256": {"source.txt": guard.sha(prestart / "source.txt"), guard.PROFILE_NAME: profile_sha,
                         guard.PROFILE_GATES_NAME: guard.sha(archived_gates)}})
    registered = {"fixture_only": True, "campaign_id": guard.RECOVERY_CAMPAIGN_ID,
                  "campaign_sha256": campaign, "prestart_sha256": guard.sha(prestart_path),
                  "execution_profile_sha256": profile_sha, "source_commit": guard.campaign_identity.SOURCE_COMMIT,
                  "source_tree_sha256": guard.campaign_identity.SOURCE_TREE_SHA256}
    journal = {"campaign_sha256": campaign, "completed_at_utc": post,
               "sessions": {s["session_id"]: {"status": "succeeded", "attempts": [{"status": "succeeded", "returncode": 0,
                             "ended_at_utc": end}]} for s in sessions},
               "input_integrity_gates": [{"completed_at_utc": post,
                   "checks": [{"scale": scale, "files": 3, "status": "pass"} for scale in (1, 10)]}]}
    raw_final = base / "raw/final"
    write(raw_final / guard.PROFILE_NAME, profile_path.read_bytes())
    full_gates = write(raw_final / guard.PROFILE_GATES_NAME, gate_bytes(gates))
    journal_path = write(raw_final / "campaign-results/journal.json", journal)
    for session in sessions:
        write(raw_final / f"campaign-results/{session['session_id']}/results.json", {"fixture": session["session_id"]})
    held_run = raw_final / "campaign-results/session-000/attempt-001/run"
    original_results = write(held_run / "sf1/spark_cpu/results.json", {"fixture_only": True,
        "completed_at_utc": "2026-10-01T00:00:01Z", "records": [{"host_result_ended_at_utc": "2026-10-01T00:00:00Z"}]})
    original_schedule = write(held_run / "schedule.json", [{"fixture_only": True, "status": "complete", "returncode": 0}])
    journal["sessions"]["session-000"]["attempts"][0].update(
        run_root="/FABRICATED-VM/campaign-results/session-000/attempt-001/run",
        artifacts={"results_path": "/FABRICATED-VM/campaign-results/session-000/attempt-001/run/sf1/spark_cpu/results.json"})
    write(journal_path, journal)
    raw_completion = base / "raw/completion"
    write(raw_completion / guard.PROFILE_NAME, profile_path.read_bytes())
    write(raw_completion / guard.PROFILE_GATES_NAME, gate_bytes(gates))
    write(raw_completion / "campaign-results/journal.json", journal)
    write(raw_completion / "confirm-driver.exit", b"0\n")
    write(raw_completion / "confirm-driver.ended-utc", (post + "\n").encode())
    raw_refs = {}
    for phase, directory in (("final", raw_final), ("completion", raw_completion)):
        files = {str(p.relative_to(directory)): {"bytes": p.stat().st_size, "sha256": guard.sha(p)}
                 for p in directory.rglob("*") if p.is_file()}
        raw_manifest = {"schema_version": guard.backup.SCHEMA, "phase": phase,
                        "campaign_sha256": campaign, "sessions": 400, "files": files}
        manifest_path = write(directory / "BACKUP_MANIFEST.json", raw_manifest)
        archive_path = base / f"raw/{phase}.tar"
        with tarfile.open(archive_path, "w") as archive:
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    archive.add(path, arcname=str(path.relative_to(directory)))
        control = {"schema_version": guard.backup.SCHEMA, "phase": phase, "campaign_sha256": campaign,
                   "sessions": 400, "files": len(files), "manifest_sha256": guard.sha(manifest_path),
                   "tar_sha256": guard.sha(archive_path), "tar_bytes": archive_path.stat().st_size}
        control_path = write(base / f"raw/{phase}.json", control)
        receipt_path = write(base / f"raw/{phase}.verified.json", {**control, "status": "OFFHOST_SHA256_VERIFIED",
                             "local_directory": str(directory)})
        raw_refs[phase] = {"archive": ref(base, archive_path), "control": ref(base, control_path),
                           "receipt": ref(base, receipt_path), "directory": str(directory.relative_to(base))}
    audit_path = write(base / "replay/complete-audit/AUDIT_COMPLETE.json", {
        "schema_version": "research-complete-audit-v1", "status": "PASS", "synthetic": False,
        "campaign_sha256": campaign, "journal_sha256": guard.sha(journal_path), "counts": guard.COUNTS,
        "findings": [], "checks_evaluated": 42, "source_sha256": guard.sha(guard.AUDITOR_PATH),
        "registered_campaign_identity": registered,
        "execution_profile": {"path": guard.PROFILE_NAME, "sha256": profile_sha,
                              "spark_driver_logs": 160, "profile_gates": 24, "status": "PASS"},
        "input_files_sha256": {str(path): guard.sha(path) for path in
            (campaign_path, journal_path, profile_path, prestart_path, archived_gates, full_gates)}})
    replay_path = write(base / "replay/REPLAY_MANIFEST.json", {
        "schema_version": "research-artifact-replay-v1", "status": "PASS", "phase": "complete", "synthetic": False,
        "campaign_sha256": campaign, "campaign_id": guard.RECOVERY_CAMPAIGN_ID,
        "registered_campaign_identity": registered, "auditor_source_sha256": guard.sha(guard.AUDITOR_PATH),
        "prestart_sha256": guard.sha(prestart_path), "complete_audit": {"sha256": guard.sha(audit_path)},
        "commands": [{"name": name, "status": "PASS", "returncode": 0}
                     for name in ("validation", "analysis", "complete_audit", "rendering")],
        "archive": str(prestart), "archive_files_sha256": {str(path.relative_to(prestart)): guard.sha(path)
                                                          for path in prestart.iterdir()},
        "artifacts_root": str(raw_final / "campaign-results"),
        "artifact_files_sha256": {str(p.relative_to(raw_final / "campaign-results")): guard.sha(p)
                                  for p in (raw_final / "campaign-results").rglob("*") if p.is_file()},
        "output_files_sha256": {"complete-audit/AUDIT_COMPLETE.json": guard.sha(audit_path)}})
    pdf_path = write(base / "paper/full-paper.pdf", b"%PDF-1.7\nfixture only\n")
    source_path = write(base / "paper/full-paper.tex", b"fixture only, not a research paper\n")
    fixture_mode = getattr(request, "param", None)
    contention = fabricated_contention(base, campaign, profile_path, journal, original_results, original_schedule,
                                      stopped=fixture_mode if fixture_mode in {"prelaunch", "guardian", "late_ack", "closed_suffix"} else fixture_mode == "stopped")
    monkeypatch.setattr(guard, "CONTENTION_AUDITOR_PATH", contention["audit_code"])
    monkeypatch.setattr(guard, "HANDOFF_SOURCE_PATH", contention["handoff_code"])
    def registered_contention(plan_sha, prestart_sha):
        if (plan_sha, prestart_sha) != (contention["plan_sha"], contention["prestart_sha"]):
            raise ValueError("Unknown fabricated contention registry identity")
        return {"fixture_only": True}
    monkeypatch.setattr(guard.contention_audit, "registered_identity", registered_contention)
    publication_path = write(base / "paper/PUBLICATION.json", {"schema_version": "h100-final-publication-v1", "status": "PASS",
        "full_paper": True, "final_claim_audit": "PASS", "campaign_sha256": campaign,
        "contention_review": contention["review"],
        "paper_pdf_sha256": guard.sha(pdf_path), "paper_source_sha256": guard.sha(source_path)})
    page = b"<html><p>Fixture only</p></html>"
    web_path = write(base / "WEB.json", {"schema_version": "h100-public-web-deployment-v1", "status": "succeeded",
        "contention_review": contention["review"],
        "audience": "public", "version": "fixture-v1", "campaign_sha256": campaign,
        "paper_pdf_sha256": guard.sha(pdf_path), "version_url": "https://fixture.example/version-1/",
        "page_sha256": hashlib.sha256(page).hexdigest()})
    repo = base / "private-git"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    preserved = {}
    for name, path in {"campaign": campaign_path, "journal": journal_path, "complete_audit": audit_path,
                       "replay_manifest": replay_path, "paper_pdf": pdf_path, "paper_source": source_path}.items():
        write(repo / name, path.read_bytes())
        preserved[name] = guard.sha(path)
    for index, path in enumerate(contention["small"]):
        write(repo / f"contention-{index:03d}", path.read_bytes())
        preserved[f"contention-{index:03d}"] = guard.sha(path)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "commit", "-qm", "Fabricated boundary-test evidence"], check=True)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    git_path = write(base / "PRIVATE_GIT.json", {"schema_version": "h100-private-git-evidence-v1",
        "status": "PRIVATE_PUSH_VERIFIED", "private": True, "campaign_sha256": campaign,
        "repository_url": "git@github.com:fixture/private-fixture.git", "commit": commit, "remote_commit": commit,
        "local_repository": "private-git", "files_sha256": preserved})
    log = write(base / "identity-idle.log", b"fixture only: no process/host access\n")
    idle_path = write(base / "IDENTITY_IDLE.json", {"schema_version": "h100-offhost-identity-idle-v1", "instance": expected,
        "campaign_sha256": campaign, "captured_at_utc": stamp, "driver_exit": 0, "driver_alive": False,
        "benchmark_processes": [], "gpu_compute_processes": [], "capture_log_path": log.name, "capture_log_sha256": guard.sha(log)})
    paths = {"campaign": campaign_path, "journal": journal_path, "complete_audit": audit_path, "replay_manifest": replay_path,
             "contention_replay": contention["replay"], "contention_preservation": contention["preservation"],
             "paper_pdf": pdf_path, "paper_source": source_path, "publication_receipt": publication_path,
             "web_receipt": web_path, "private_git_receipt": git_path, "identity_idle_receipt": idle_path}
    manifest = {"schema_version": guard.SCHEMA, "status": "COMPLETE", "offhost": True, "instance": expected,
                "campaign_sha256": campaign, "artifacts": {name: ref(base, path) for name, path in paths.items()},
                "raw_backups": raw_refs}
    manifest_path = write(base / "COMPLETION.json", manifest)
    monkeypatch.setattr(guard.campaign_identity, "registered_identity", lambda *a: registered)
    monkeypatch.setattr(guard.time, "sleep", lambda _: None)
    return {"base": base, "path": manifest_path, "manifest": manifest, "paths": paths, "expected": expected, "page": page, "registered": registered,
            "profile_path": profile_path, "prestart_path": prestart_path, "archived_gates": archived_gates,
            "full_gates": full_gates, "raw_final": raw_final, "raw_completion": raw_completion,
            "contention": contention}


def changed(complete, name, alter):
    value = guard.read_json(complete["paths"][name])
    alter(value)
    write(complete["paths"][name], value)
    complete["manifest"]["artifacts"][name] = ref(complete["base"], complete["paths"][name])
    write(complete["path"], complete["manifest"])


def test_full_local_verification_is_offline(complete, monkeypatch):
    monkeypatch.setattr(guard, "request_json", lambda *a: pytest.fail("offline verification made API request"))
    result = guard.verify_completion(complete["path"], complete["expected"])
    assert result["campaign_sha256"] == complete["manifest"]["campaign_sha256"]



def test_explicit_synthetic_plan_refuses_before_any_api_call(complete):
    changed(complete, "campaign", lambda value: value.update(synthetic=True))
    complete["manifest"]["campaign_sha256"] = guard.sha(complete["paths"]["campaign"])
    write(complete["path"], complete["manifest"])
    with pytest.raises(ValueError, match="genuine recovery"):
        guard.terminate(complete["path"], complete["expected"], "fixture-secret-only",
                        api=lambda *args: pytest.fail("API called"))

@pytest.mark.parametrize("name", ["campaign", "journal", "complete_audit", "replay_manifest", "paper_pdf", "paper_source",
                                    "publication_receipt", "web_receipt", "private_git_receipt", "identity_idle_receipt"])
def test_each_missing_artifact_fails_closed(complete, name):
    complete["paths"][name].unlink()
    with pytest.raises(ValueError):
        guard.verify_completion(complete["path"], complete["expected"])


@pytest.mark.parametrize("name,alter", [
    ("complete_audit", lambda v: v["counts"].update(successful_outputs=9359)),
    ("complete_audit", lambda v: v.update(synthetic=True)),
    ("complete_audit", lambda v: v["execution_profile"].update(spark_driver_logs=159)),
    ("complete_audit", lambda v: v["execution_profile"].update(profile_gates=23)),
    ("complete_audit", lambda v: v["execution_profile"].update(sha256="0" * 64)),
    ("complete_audit", lambda v: v.update(source_sha256="0" * 64)),
    ("complete_audit", lambda v: v.update(registered_campaign_identity={})),
    ("replay_manifest", lambda v: v.update(auditor_source_sha256="0" * 64)),
    ("replay_manifest", lambda v: v.update(registered_campaign_identity={})),
    ("replay_manifest", lambda v: v.update(campaign_id="h100-confirmatory-replacement-20261027")),
    ("replay_manifest", lambda v: v.update(status="FAIL")),
    ("journal", lambda v: v.update(input_integrity_gates=[])),
    ("journal", lambda v: v["sessions"]["session-000"].update(status="running")),
    ("publication_receipt", lambda v: v.update(full_paper=False)),
    ("web_receipt", lambda v: v.update(audience="owner-private")),
    ("web_receipt", lambda v: v.update(version_url="http://fixture.example/v1/")),
    ("private_git_receipt", lambda v: v.update(private=False)),
    ("private_git_receipt", lambda v: v.update(remote_commit="0" * 40)),
    ("identity_idle_receipt", lambda v: v.update(driver_alive=True)),
    ("identity_idle_receipt", lambda v: v.update(gpu_compute_processes=[{"pid": 123}])),
    ("identity_idle_receipt", lambda v: v.update(captured_at_utc="2026-10-01T03:00:00Z")),
    ("identity_idle_receipt", lambda v: v["instance"].update(gpu_uuid="GPU-wrong")),
])
def test_incomplete_gate_refuses_before_any_api_call(complete, name, alter):
    changed(complete, name, alter)
    with pytest.raises(ValueError):
        guard.terminate(complete["path"], complete["expected"], "fixture-secret-only", api=lambda *a: pytest.fail("API called"))


@pytest.mark.parametrize("campaign_id", ["h100-confirmatory-v1", "h100-confirmatory-replacement-20261027",
                                         "arbitrary-replacement-cohort"])
def test_historical_or_arbitrary_campaign_cannot_permit_termination(complete, campaign_id):
    changed(complete, "campaign", lambda value: value.update(campaign_id=campaign_id))
    # Keep the outer digest honest: the recovery-ID rule must still refuse the
    # relabelled fabricated evidence before any transport is considered.
    complete["manifest"]["campaign_sha256"] = guard.sha(complete["paths"]["campaign"])
    write(complete["path"], complete["manifest"])
    with pytest.raises(ValueError, match="genuine recovery"):
        guard.terminate(complete["path"], complete["expected"], "fixture-secret-only",
                        api=lambda *args: pytest.fail("API called"))


@pytest.mark.parametrize("name", ["profile_path", "prestart_path", "archived_gates", "full_gates"])
def test_profile_prestart_and_receipts_must_be_exact_auditor_inputs(complete, name):
    changed(complete, "complete_audit", lambda value: value["input_files_sha256"].pop(str(complete[name])))
    changed(complete, "replay_manifest", lambda value: (
        value["complete_audit"].update(sha256=guard.sha(complete["paths"]["complete_audit"])),
        value["output_files_sha256"].update({"complete-audit/AUDIT_COMPLETE.json": guard.sha(complete["paths"]["complete_audit"])})))
    with pytest.raises(ValueError, match="missing from exact complete-audit inputs"):
        guard.terminate(complete["path"], complete["expected"], "fixture-secret-only",
                        api=lambda *args: pytest.fail("API called"))


def test_profile_bytes_must_match_actual_registered_prestart_seal(complete):
    replay = guard.read_json(complete["paths"]["replay_manifest"])
    audit = guard.read_json(complete["paths"]["complete_audit"])
    plan = guard.read_json(complete["paths"]["campaign"])
    complete["profile_path"].write_bytes(b'{"fixture_only":"changed"}\n')
    with pytest.raises(ValueError, match="Registered prestart"):
        guard.verify_recovery_profile(complete["base"], plan, complete["manifest"]["campaign_sha256"], audit,
                                      replay, complete["registered"],
                                      {"final": complete["raw_final"], "completion": complete["raw_completion"]})


@pytest.mark.parametrize("name", [guard.PROFILE_NAME, guard.PROFILE_GATES_NAME])
def test_raw_completion_profile_and_receipts_must_equal_audited_bytes(complete, name):
    replay = guard.read_json(complete["paths"]["replay_manifest"])
    audit = guard.read_json(complete["paths"]["complete_audit"])
    plan = guard.read_json(complete["paths"]["campaign"])
    (complete["raw_completion"] / name).write_bytes(b"changed fabricated raw completion bytes\n")
    with pytest.raises(ValueError, match="Raw final/completion profile or receipts differ"):
        guard.verify_recovery_profile(complete["base"], plan, complete["manifest"]["campaign_sha256"], audit,
                                      replay, complete["registered"],
                                      {"final": complete["raw_final"], "completion": complete["raw_completion"]})


@pytest.mark.parametrize("campaign", [guard.campaign_identity.ORIGINAL_CAMPAIGN_SHA256,
                                       guard.campaign_identity.ABORTED_V1_CAMPAIGN_SHA256])
def test_registered_historical_hash_cannot_be_promoted_by_profile_claim(complete, campaign):
    replay = guard.read_json(complete["paths"]["replay_manifest"])
    audit = guard.read_json(complete["paths"]["complete_audit"])
    plan = guard.read_json(complete["paths"]["campaign"])
    with pytest.raises(ValueError, match="exact registered recovery campaign/profile"):
        guard.verify_recovery_profile(complete["base"], plan, campaign, audit, replay, complete["registered"], {})


@pytest.mark.parametrize("kind", ["missing", "duplicate", "wrong_profile", "failed", "nonobject"])
def test_full_profile_receipts_cannot_be_replaced_by_summary_counts(complete, kind):
    replay = guard.read_json(complete["paths"]["replay_manifest"])
    audit = guard.read_json(complete["paths"]["complete_audit"])
    plan = guard.read_json(complete["paths"]["campaign"])
    records = [guard.strict_json(line) for line in complete["full_gates"].read_bytes().splitlines()]
    if kind == "missing":
        records.pop()
    elif kind == "duplicate":
        records[-1] = records[-2]
    elif kind == "wrong_profile":
        records[-1]["profile_sha256"] = "0" * 64
    elif kind == "nonobject":
        records[-1] = []
    else:
        records[-1]["status"] = "FAIL"
    complete["full_gates"].write_bytes(b"".join((json.dumps(record, sort_keys=True) + "\n").encode() for record in records))
    audit["input_files_sha256"][str(complete["full_gates"])] = guard.sha(complete["full_gates"])
    with pytest.raises(ValueError, match="24 ordered passing|JSON objects"):
        guard.verify_recovery_profile(complete["base"], plan, complete["manifest"]["campaign_sha256"], audit,
                                      replay, complete["registered"], {})


def cloud(complete):
    expected = complete["expected"]
    return {"id": expected["instance_id"], "ip": expected["ip"], "status": "active",
            "instance_type": {"name": expected["api_instance_type"], "gpu_description": expected["api_gpu_description"], "specs": {"gpus": 1}},
            "actions": {"terminate": {"available": True}}, "jupyter_token": "must-never-print"}


@pytest.mark.parametrize("change", [lambda v: v.update(id="2" * 32), lambda v: v.update(ip="192.0.2.5"),
                                    lambda v: v["instance_type"].update(name="gpu-other"),
                                    lambda v: v["actions"]["terminate"].update(available=False)])
def test_cloud_identity_mismatch_prevents_post(complete, change):
    instance, calls = cloud(complete), []
    change(instance)
    def api(method, *args):
        calls.append(method)
        assert method == "GET"
        return {"data": instance}
    with pytest.raises(ValueError):
        guard.terminate(complete["path"], complete["expected"], "fixture-secret-only", api, lambda _: complete["page"])
    assert calls == ["GET"]


def test_public_page_mismatch_prevents_post(complete):
    def api(method, *args):
        assert method == "GET"
        return {"data": cloud(complete)}
    with pytest.raises(ValueError, match="Public deployed version"):
        guard.terminate(complete["path"], complete["expected"], "fixture-secret-only", api, lambda _: b"login page")


def test_complete_matching_public_result_allows_one_exact_post(complete):
    calls = []
    def api(method, path, key, body=None):
        calls.append((method, path, body))
        return {"data": cloud(complete)} if method == "GET" else {
            "data": {"terminated_instances": [{"id": complete["expected"]["instance_id"], "status": "terminating"}]}}
    result = guard.terminate(complete["path"], complete["expected"], "fixture-secret-only", api, lambda _: complete["page"])
    assert calls == [("GET", "/instances/" + complete["expected"]["instance_id"], None),
                     ("POST", "/instance-operations/terminate", {"instance_ids": [complete["expected"]["instance_id"]]})]
    assert result["status"] == "terminating" and "fixture-secret-only" not in json.dumps(result)


@pytest.mark.parametrize("http_status", [200, 403])
def test_default_public_fetch_uses_only_benign_client_headers_and_keeps_http_gate(complete, monkeypatch, http_status):
    from types import SimpleNamespace
    calls, fetched = [], []
    expected_url = guard.read_json(complete["paths"]["web_receipt"])["version_url"]

    class Response:
        status = http_status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            assert limit == 16 * 1024 * 1024 + 1
            return complete["page"]

    def open_public(request, timeout):
        assert isinstance(request, guard.Request)
        assert request.full_url == expected_url and request.get_method() == "GET" and request.data is None
        assert {name.lower(): value for name, value in request.header_items()} == {
            "user-agent": "Mozilla/5.0", "x-requested-with": "XMLHttpRequest"}
        assert request.unredirected_hdrs == {} and timeout == 30
        fetched.append(request.full_url)
        return Response()

    def opener(*handlers):
        assert handlers == (guard.NoRedirect,)
        return SimpleNamespace(open=open_public)

    def api(method, path, key, body=None):
        calls.append(method)
        return {"data": cloud(complete)} if method == "GET" else {
            "data": {"terminated_instances": [{"id": complete["expected"]["instance_id"], "status": "terminating"}]}}

    monkeypatch.setattr(guard, "build_opener", opener)
    monkeypatch.setattr(guard.time, "sleep", lambda _: None)
    if http_status == 200:
        assert guard.terminate(complete["path"], complete["expected"], "fixture-secret-only", api)["status"] == "terminating"
        assert calls == ["GET", "POST"]
    else:
        with pytest.raises(ValueError, match="did not return HTTP 200"):
            guard.terminate(complete["path"], complete["expected"], "fixture-secret-only", api)
        assert calls == ["GET"]
    assert fetched == [expected_url]


def test_corrupt_raw_archive_cannot_use_old_verified_receipt(complete):
    (complete["base"] / complete["manifest"]["raw_backups"]["final"]["archive"]["path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA256 differs"):
        guard.verify_completion(complete["path"], complete["expected"])


def test_symlink_evidence_is_refused(complete):
    path = complete["paths"]["paper_source"]
    other = path.with_suffix(".other")
    path.rename(other)
    path.symlink_to(other)
    with pytest.raises(ValueError, match="Symlink"):
        guard.verify_completion(complete["path"], complete["expected"])


def test_key_accepts_stdin_pipe_only(tmp_path):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"fixture-secret-only\n")
    os.close(write_fd)
    with os.fdopen(read_fd) as stream:
        assert guard.read_api_key(stream) == "fixture-secret-only"
    path = write(tmp_path / "not-allowed.key", b"fixture-secret-only\n")
    with path.open() as stream, pytest.raises(ValueError, match="secure stdin pipe"):
        guard.read_api_key(stream)


def test_duplicate_json_and_unapproved_api_operation_refused(tmp_path):
    path = write(tmp_path / "duplicate.json", b'{"status":"PASS","status":"FAIL"}')
    with pytest.raises(ValueError, match="Duplicate"):
        guard.read_json(path)
    with pytest.raises(ValueError, match="Unapproved"):
        guard.request_json("POST", "/instance-operations/restart", "fixture-secret-only")
    with pytest.raises(ValueError, match="Unapproved"):
        guard.request_json("POST", "/instances/" + "1" * 32, "fixture-secret-only")


def test_unregistered_campaign_refused(complete, monkeypatch):
    def no_identity(*args):
        raise ValueError("Campaign is not in the committed H100 identity registry")
    monkeypatch.setattr(guard.campaign_identity, "registered_identity", no_identity)
    with pytest.raises(ValueError, match="identity registry"):
        guard.verify_completion(complete["path"], complete["expected"])


def test_post_response_uncertain_is_not_retried(complete):
    calls = []
    def api(method, path, key, body=None):
        calls.append(method)
        return {"data": cloud(complete)} if method == "GET" else {"data": {"terminated_instances": []}}
    with pytest.raises(ValueError, match="not definitive"):
        guard.terminate(complete["path"], complete["expected"], "fixture-secret-only", api, lambda _: complete["page"])
    assert calls == ["GET", "POST"]


def test_cli_identity_pins_the_authorized_instance_and_gpu(complete):
    from types import SimpleNamespace
    args = SimpleNamespace(**{"expected_" + k: v for k, v in complete["expected"].items()})
    assert guard.expected_identity(args) == complete["expected"]
    args.expected_instance_id = "0" * 32
    with pytest.raises(ValueError, match="authorized replacement VM"):
        guard.expected_identity(args)


def test_request_layer_refuses_wrong_termination_target():
    with pytest.raises(ValueError, match="Unapproved termination target"):
        guard.request_json("POST", "/instance-operations/terminate", "fixture-secret-only", {"instance_ids": ["0" * 32]})
    with pytest.raises(ValueError, match="Redirect refused"):
        guard.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/")


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/instances/" + guard.AUTHORIZED_INSTANCE_ID, None),
    ("POST", "/instance-operations/terminate", {"instance_ids": [guard.AUTHORIZED_INSTANCE_ID]}),
])
def test_api_request_keeps_exact_authorized_operation_and_explicit_user_agent(monkeypatch, method, path, body):
    from types import SimpleNamespace
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            assert limit == 2 * 1024 * 1024
            return b'{"fixture_only": true}'

    def open_api(request, timeout):
        assert request.full_url == guard.API_BASE + path and request.get_method() == method
        assert request.data == (None if body is None else json.dumps(body).encode())
        assert {name.lower(): value for name, value in request.header_items()} == {
            "authorization": "Bearer fixture-secret-only", "accept": "application/json",
            "content-type": "application/json", "user-agent": "Mozilla/5.0"}
        assert request.unredirected_hdrs == {} and timeout == 30
        calls.append(request)
        return Response()

    def opener(*handlers):
        assert handlers == (guard.NoRedirect,)
        return SimpleNamespace(open=open_api)

    monkeypatch.setattr(guard, "build_opener", opener)
    assert guard.request_json(method, path, "fixture-secret-only", body) == {"fixture_only": True}
    assert len(calls) == 1


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/instances/" + "0" * 32, None),
    ("POST", "/instance-operations/restart", {"instance_ids": [guard.AUTHORIZED_INSTANCE_ID]}),
    ("GET", "/instance-operations/terminate", None),
    ("POST", "/instances/" + guard.AUTHORIZED_INSTANCE_ID, None),
    ("GET", "/instances/" + guard.AUTHORIZED_INSTANCE_ID, {}),
    ("POST", "/instance-operations/terminate", {"instance_ids": ["0" * 32]}),
])
def test_api_user_agent_does_not_expand_fixed_endpoint_method_or_body(monkeypatch, method, path, body):
    monkeypatch.setattr(guard, "build_opener", lambda *args: pytest.fail("unapproved operation reached transport"))
    with pytest.raises(ValueError, match="Unapproved"):
        guard.request_json(method, path, "fixture-secret-only", body)


def test_api_http_error_stays_sanitized_and_is_not_retried(monkeypatch):
    from types import SimpleNamespace
    calls = []

    def open_api(request, timeout):
        calls.append(request)
        raise guard.HTTPError(request.full_url, 403, "fixture response", {}, None)

    monkeypatch.setattr(guard, "build_opener", lambda *args: SimpleNamespace(open=open_api))
    with pytest.raises(ValueError, match="^Lambda API HTTP 403; no automatic retry$") as error:
        guard.request_json("GET", "/instances/" + guard.AUTHORIZED_INSTANCE_ID, "fixture-secret-only")
    assert len(calls) == 1 and "fixture-secret-only" not in str(error.value)


@pytest.mark.parametrize("body,accepted", [(b"fixture-secret-only", True), (b"", False),
                                          (b"fixture-secret-only\nsecond-line\n", False),
                                          (b"fixture secret with spaces\n", False)])
def test_stdin_key_line_validation(body, accepted):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, body)
    os.close(write_fd)
    with os.fdopen(read_fd) as stream:
        if accepted:
            assert guard.read_api_key(stream) == body.decode()
        else:
            with pytest.raises(ValueError, match="exactly one API-key line"):
                guard.read_api_key(stream)


def cli_identity_args(expected):
    return [value for name, expected_value in expected.items()
            for value in ("--expected-" + name.replace("_", "-"), expected_value)]


def test_cli_refuses_incomplete_manifest_before_reading_credential(complete, monkeypatch, capsys):
    complete["paths"]["paper_pdf"].unlink()
    monkeypatch.setattr(guard, "read_api_key", lambda _: pytest.fail("credential read before completion"))
    assert guard.main(["terminate", "--manifest", str(complete["path"]), *cli_identity_args(complete["expected"])]) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)["status"] == "REFUSED" and not output.out


def test_cli_status_prints_only_sanitized_identity(complete, monkeypatch, capsys):
    monkeypatch.setattr(guard, "read_api_key", lambda _: "fixture-secret-only")
    monkeypatch.setattr(guard, "request_json", lambda *a: {"data": cloud(complete)})
    assert guard.main(["status", *cli_identity_args(complete["expected"])]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["mutation_performed"] is False
    assert "must-never-print" not in output and "fixture-secret-only" not in output and "jupyter" not in output


def call_contention(complete):
    paths = complete['paths']
    return guard.verify_contention(complete['base'], paths, complete['manifest']['campaign_sha256'],
        complete['registered'], guard.read_json(paths['journal']), guard.read_json(paths['replay_manifest']),
        guard.read_json(paths['publication_receipt']), guard.read_json(paths['web_receipt']),
        guard.read_json(paths['private_git_receipt']))


def change_contention_input(complete, name, alter):
    receipt = guard.read_json(complete['paths']['contention_replay'])
    path = Path(receipt['handoff']['files'][name]['path'])
    value = guard.read_json(path)
    alter(value)
    write(path, value)
    receipt['handoff']['files'][name]['sha256'] = guard.sha(path)
    receipt['input_files_sha256'][str(path.resolve())] = guard.sha(path)
    write(complete['paths']['contention_replay'], receipt)


@pytest.mark.parametrize('role', ['contention_replay', 'contention_preservation'])
def test_original_400_gates_cannot_bypass_new_authorized_role(complete, monkeypatch, role):
    complete['manifest']['artifacts'].pop(role)
    write(complete['path'], complete['manifest'])
    monkeypatch.setattr(guard, 'read_api_key', lambda _: pytest.fail('credential accepted before contention completion'))
    monkeypatch.setattr(guard, 'request_json', lambda *a: pytest.fail('API called'))
    assert guard.main(['terminate', '--manifest', str(complete['path']), *cli_identity_args(complete['expected'])]) == 1


@pytest.mark.parametrize('mutation', [
    lambda r: r.update(status='PARTIAL'), lambda r: r.update(status='FAIL'),
    lambda r: r.update(integrity_status='FAIL'), lambda r: r.update(publication_ready=False),
    lambda r: r.update(synthetic=True), lambda r: r.update(findings=[{'type': 'real integrity failure'}]),
    lambda r: r.update(audit_source_sha256='0' * 64), lambda r: r.update(experiment_id='arbitrary-contention-retry'),
    lambda r: r.update(plan_sha256='0' * 64), lambda r: r.update(prestart_sha256='0' * 64),
    lambda r: r.update(reference_campaign_sha256='0' * 64),
    lambda r: r.update(reference_execution_profile_sha256='0' * 64),
    lambda r: r['handoff'].update(same_scheduler_resumed=False), lambda r: r['handoff'].update(no_action_overlap=False),
    lambda r: r['handoff'].update(all_original_input_files=1653), lambda r: r['handoff'].update(source_sha256='0' * 64),
    lambda r: r['terminal_driver'].update(terminal=False),
    lambda r: r['disposition']['main'].update(unattempted_waves=['dropped-wave']),
    lambda r: r['counts'].update(successful_outputs=459),
])
def test_contention_must_be_actual_registered_terminal_evidence(complete, mutation):
    value = guard.read_json(complete['paths']['contention_replay'])
    mutation(value)
    write(complete['paths']['contention_replay'], value)
    with pytest.raises(ValueError):
        call_contention(complete)


@pytest.mark.parametrize('complete', ['stopped', 'prelaunch', 'guardian', 'late_ack', 'closed_suffix'], indirect=True)
def test_honest_terminal_failure_preserves_failed_and_unattempted_waves(complete):
    value = guard.read_json(complete['paths']['contention_replay'])
    assert value['status'] == 'STOPPED_WITH_FAILURE'
    assert set(value['snapshots']) == {'failed'}
    assert value['disposition']['main']['attempted_waves'] == 0
    assert len(value['disposition']['main']['unattempted_waves']) == 16
    assert guard.verify_completion(complete['path'], complete['expected'])['campaign_sha256'] == complete['manifest']['campaign_sha256']


@pytest.mark.parametrize('name,alter,message', [
    ('resume', lambda r: r['scheduler'].update(pid=999), 'same held scheduler'),
    ('resume', lambda r: r.update(status='STILL_HELD'), 'same held scheduler'),
    ('profile_after', lambda r: r['input_integrity']['checks'][0].update(files=56), 'all1654'),
    ('profile_before', lambda r: r.update(execution_profile_sha256='0' * 64), 'all1654'),
    ('profile_after', lambda r: r['exclusive_host_after'].update(eligible=False), 'all1654'),
    ('profile_after', lambda r: r['exclusive_host_after'].update(gpu_compute_clients='123'), 'idle probe contradicts'),
    ('owned_groups', lambda r: r.update(live_members=[{'pid': 123}]), 'workers remained'),
    ('residual_before_signal', lambda r: r.update(contention_processes=[{'pid': 123}]), 'workers remained'),
    ('held_journal', lambda r: r.update(extra='changed held journal bytes'), 'held original journal'),
    ('held_session', lambda r: r['attempt'].update(run_root='/different/attempt'), 'held original journal'),
    ('state', lambda r: r.update(phase='CONTENTION_RUNNING'), 'held original journal'),
])
def test_summary_flags_cannot_replace_actual_resume_byte_and_runtime_proofs(complete, name, alter, message):
    change_contention_input(complete, name, alter)
    with pytest.raises(ValueError, match=message):
        call_contention(complete)


def test_original_action_bytes_must_match_final_isolated_evidence(complete):
    receipt = guard.read_json(complete['paths']['contention_replay'])
    original = Path(receipt['handoff']['files']['original_results']['path'])
    different = complete['base'] / 'contention/different-original-results.json'
    changed_result = guard.read_json(original)
    changed_result['fixture_changed'] = True
    write(different, changed_result)
    receipt['handoff']['files']['original_results'] = {'path': str(different), 'sha256': guard.sha(different)}
    receipt['input_files_sha256'][str(different.resolve())] = guard.sha(different)
    write(complete['paths']['contention_replay'], receipt)
    with pytest.raises(ValueError, match='original endpoint bytes differ'):
        call_contention(complete)


@pytest.mark.parametrize('kind', ['before_original_ended', 'after_resume'])
def test_actual_timed_overlap_with_original_work_is_never_accepted(complete, kind):
    receipt = guard.read_json(complete['paths']['contention_replay'])
    if kind == 'before_original_ended':
        receipt['handoff']['first_contention_action_utc_epoch'] = datetime(2026, 9, 30, tzinfo=timezone.utc).timestamp()
    else:
        receipt['handoff']['last_contention_host_result_utc_epoch'] = datetime(2026, 10, 2, tzinfo=timezone.utc).timestamp()
    write(complete['paths']['contention_replay'], receipt)
    with pytest.raises(ValueError, match='overlap original isolated'):
        call_contention(complete)


@pytest.mark.parametrize('kind', ['raw_tar', 'raw_file', 'missing_ack', 'missing_input'])
def test_raw_contention_and_ack_evidence_must_be_preserved_exactly(complete, kind):
    receipt = guard.read_json(complete['paths']['contention_replay'])
    phase = 'pilot'
    entry = receipt['snapshots'][phase]
    if kind == 'raw_tar':
        (complete['base'] / entry['archive']['path']).write_bytes(b'FABRICATED corruption')
    elif kind == 'raw_file':
        (Path(entry['directory']) / 'pilot/FABRICATED-RAW.json').write_bytes(b'FABRICATED changed raw bytes')
    elif kind == 'missing_ack':
        (complete['contention']['driver'].parent / 'backup-receipts/pilot.json').unlink()
    else:
        receipt['input_files_sha256'].pop(receipt['handoff']['files']['held_journal']['path'])
        write(complete['paths']['contention_replay'], receipt)
    with pytest.raises(ValueError):
        call_contention(complete)


@pytest.mark.parametrize('role', ['publication_receipt', 'web_receipt'])
def test_paper_and_web_must_review_the_same_terminal_contention_receipt(complete, role):
    changed(complete, role, lambda r: r.pop('contention_review'))
    with pytest.raises(ValueError, match='paper and public webpage reviews'):
        call_contention(complete)


@pytest.mark.parametrize('kind', ['private', 'missing_phase', 'retrieval', 'same_file', 'hardlink', 'corrupt_retrieval', 'signed_url'])
def test_private_storage_requires_independent_verified_raw_retrieval(complete, kind):
    path = complete['paths']['contention_preservation']
    value = guard.read_json(path)
    item = value['raw_backups']['pilot']
    if kind == 'private': value['private'] = False
    elif kind == 'missing_phase': value['raw_backups'].pop('main')
    elif kind == 'retrieval': item['retrieval_verified'] = False
    elif kind == 'same_file': item['retrieved_archive'] = complete['contention']['snapshots']['pilot']['archive']
    elif kind == 'hardlink':
        retrieved = complete['base'] / item['retrieved_archive']['path']
        retrieved.unlink()
        os.link(complete['base'] / complete['contention']['snapshots']['pilot']['archive']['path'], retrieved)
    elif kind == 'corrupt_retrieval': (complete['base'] / item['retrieved_archive']['path']).write_bytes(b'FABRICATED corrupted download')
    else: item['storage']['download_url'] = 'https://fixture.example/signed?token=not-a-real-token'
    write(path, value)
    with pytest.raises(ValueError):
        call_contention(complete)


def test_contention_metadata_and_actual_terminal_audit_must_be_in_private_git(complete):
    # Keep the existing private commit valid while removing only the required new
    # audit's digest from its declared preserved inventory.
    changed(complete, 'private_git_receipt', lambda r: r.update(files_sha256={
        name: digest for name, digest in r['files_sha256'].items()
        if digest != guard.sha(complete['paths']['contention_replay'])}))
    with pytest.raises(ValueError, match='Private Git commit is missing canonical contention'):
        guard.verify_completion(complete['path'], complete['expected'])


@pytest.mark.parametrize('complete', ['stopped'], indirect=True)
@pytest.mark.parametrize('bad', ['passing_unattempted_main', 'fabricated_main_snapshot'])
def test_stopped_pilot_cannot_fabricate_passing_or_raw_main_phase(complete, bad):
    receipt = guard.read_json(complete['paths']['contention_replay'])
    if bad == 'passing_unattempted_main':
        receipt['disposition']['main']['status'] = 'PASS'
    else:
        receipt['snapshots']['main'] = dict(receipt['snapshots']['failed'])
    write(complete['paths']['contention_replay'], receipt)
    with pytest.raises(ValueError, match='phase status contradicts|stopped pilot cannot fabricate'):
        call_contention(complete)


@pytest.mark.parametrize('complete', ['closed_suffix'], indirect=True)
@pytest.mark.parametrize('bad', ['complete_claim', 'claimed_attempt'])
def test_closed_stop_alias_requires_terminal_failure_and_zero_attempts(complete, bad):
    receipt = guard.read_json(complete['paths']['contention_replay'])
    if bad == 'complete_claim':
        receipt['status'] = 'COMPLETE'
    else:
        item = receipt['disposition']['main']
        item.update(attempted_waves=1, passed_waves=1)
        item['unattempted_waves'] = item['unattempted_waves'][1:]
    write(complete['paths']['contention_replay'], receipt)
    with pytest.raises(ValueError, match='phase status contradicts'):
        call_contention(complete)
