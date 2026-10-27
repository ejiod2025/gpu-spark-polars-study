"""Boundary tests use fabricated evidence, mocked registry and mocked transports.

These fixtures are not scientific results; no API credential or VM is accessed.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
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


@pytest.fixture
def complete(tmp_path, monkeypatch):
    base = tmp_path / "evidence"
    base.mkdir()
    expected = {"instance_id": guard.AUTHORIZED_INSTANCE_ID, "ip": guard.AUTHORIZED_IP,
                "gpu_uuid": guard.AUTHORIZED_GPU_UUID, "gpu_name": guard.AUTHORIZED_GPU_NAME,
                "driver_version": guard.AUTHORIZED_DRIVER, "api_instance_type": guard.AUTHORIZED_INSTANCE_TYPE,
                "api_gpu_description": "H100 fixture description"}
    stamp = datetime.now(timezone.utc).isoformat()
    end, post = "2026-10-01T01:00:00Z", "2026-10-01T01:01:00Z"
    sessions = [{"session_id": f"session-{i:03d}"} for i in range(400)]
    campaign_path = write(base / "campaign.json", {"synthetic": False, "campaign_id": guard.RECOVERY_CAMPAIGN_ID,
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
    publication_path = write(base / "paper/PUBLICATION.json", {"schema_version": "h100-final-publication-v1", "status": "PASS",
        "full_paper": True, "final_claim_audit": "PASS", "campaign_sha256": campaign,
        "paper_pdf_sha256": guard.sha(pdf_path), "paper_source_sha256": guard.sha(source_path)})
    page = b"<html><p>Fixture only</p></html>"
    web_path = write(base / "WEB.json", {"schema_version": "h100-public-web-deployment-v1", "status": "succeeded",
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
            "full_gates": full_gates, "raw_final": raw_final, "raw_completion": raw_completion}


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
