"""Offline boundaries only. Wave/driver fixtures below are visibly fabricated.

The committed genuine prestart is used only to test its source/identity seal;
no test fabricates measured study outputs or calls a GPU, JVM or remote host.
"""
from pathlib import Path
import json
import sys
import types

import pytest

from research_reporting import contention_replay as replay


REPO = Path(__file__).resolve().parents[2]
ARCHIVE = REPO / "evidence-research/20261028/h100-contention-v1"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def test_unknown_identity_and_duplicate_json_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown"):
        replay.registered_identity("a" * 64, "b" * 64)
    path = tmp_path / "SYNTHETIC_DUPLICATE.json"
    path.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError, match="Duplicate"):
        replay.Inputs().json(path)


def test_runtime_oom_cannot_be_counted_as_expected_translation_rejection():
    wave = {"suite": "complex"}
    error = {"query": "cx_window", "phase": "warmup", "trial": 0,
             "message": "NotImplementedError: unsupported rank expression"}
    assert replay.strict_rejection(error, wave, "polars_gpu")
    assert not replay.strict_rejection(error | {"message": error["message"] + "; CUDA error: out of memory"}, wave, "polars_gpu")
    assert not replay.strict_rejection(error, wave, "spark_cpu")


def test_genuine_prestart_is_read_only_and_bounded():
    inputs = replay.Inputs()
    root, plan, profile, inventory, runner = replay.verify_canonical(ARCHIVE, inputs)
    assert len(inventory) == 66
    assert plan["experiment_id"] == replay.EXPERIMENT_ID
    assert len(runner.waves("pilot")) == 4
    assert len(runner.waves("main")) == 16
    assert not list(root.rglob("__pycache__"))
    inputs.recheck()


def test_tampered_canonical_stops_before_executing_archive(tmp_path, monkeypatch):
    import shutil
    copy = tmp_path / "COPIED_GENUINE_PRESTART_TAMPERED"
    shutil.copytree(ARCHIVE, copy)
    with (copy / "FROZEN_SOURCE/worker.py").open("a") as handle:
        handle.write("\n# Deliberate test-only tamper\n")
    monkeypatch.setattr(replay, "module_from", lambda *a: pytest.fail("Unverified code executed"))
    with pytest.raises(ValueError, match="byte mismatch"):
        replay.verify_canonical(copy, replay.Inputs())


def test_input_symlink_and_mutation_fail(tmp_path):
    original = tmp_path / "original.json"
    original.write_text("{}")
    link = tmp_path / "linked.json"
    link.symlink_to(original)
    with pytest.raises(ValueError, match="linked"):
        replay.Inputs().file(link)
    inputs = replay.Inputs()
    inputs.file(original)
    original.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="changed"):
        inputs.recheck()


def test_frozen_import_context_restores_existing_module(tmp_path):
    (tmp_path / "fixture_module.py").write_text("VALUE = 'frozen fixture'\n")
    old = types.ModuleType("fixture_module")
    old.VALUE = "existing unrelated module"
    sys.modules["fixture_module"] = old
    try:
        with replay.frozen_imports(tmp_path):
            module = __import__("fixture_module")
            assert module.VALUE == "frozen fixture"
        assert sys.modules["fixture_module"] is old
        assert not (tmp_path / "__pycache__").exists()
    finally:
        sys.modules.pop("fixture_module", None)


def barrier_fixture(tmp_path):
    directory = tmp_path / "pilot" / "SYNTHETIC_WAVE_ONLY"
    root = directory / "barriers"
    root.mkdir(parents=True)
    members = ["polars_gpu", "spark_cpu"]
    plan = {"root": "/TEST_ONLY_NOT_A_STUDY", "source_files_sha256": {"worker.py": "b" * 64},
            "tools_sha256": {"/TEST_ONLY_NOT_A_STUDY/tools/contention_worker.py": "a" * 64}}
    wave = {"wave_id": directory.name, "participants": members, "suite": "baseline"}
    identity = {"schema_version": "h100-contention-barrier-v1", "root": str(Path(plan["root"]) / "pilot" / directory.name / "barriers"),
                "participants": members, "worker_sha256": "b" * 64}
    barrier_id = __import__("hashlib").sha256(replay.canonical(identity)).hexdigest()
    save(root / "IDENTITY.json", identity | {"barrier_id": barrier_id})
    queries = ["test_q1", "test_q2"]
    keys = [replay.action_key("setup", "setup", 0)] + [replay.action_key(q, "warmup", 0) for q in queries]
    keys += [replay.action_key(q, "measured", 1) for q in queries]
    events = {member: [] for member in members}
    results = {member: {"pid": 100 + i, "records": []} for i, member in enumerate(members)}
    stamp = "2026-01-01T00:00:00+00:00"
    for member in members:
        pid = results[member]["pid"]
        save(root / ("participant-" + member + ".json"), {"barrier_id": barrier_id, "participant": member, "pid": pid})
        common = {"schema_version": identity["schema_version"], "barrier_id": barrier_id, "participant": member,
                  "pid": pid, "at_utc": stamp, "key": None}
        events[member] = [common | {"event": "participant_claimed", "monotonic_ns": 1},
                          common | {"event": "wrapper_provenance", "monotonic_ns": 2,
                                    "wrapper_sha256": "a" * 64, "frozen_worker_sha256": "b" * 64}]
    for number, key in enumerate(keys, 1):
        folder = root / __import__("hashlib").sha256(replay.canonical(key)).hexdigest()
        ready_ns = {member: number * 1_000_000_000 for member in members}
        target = max(ready_ns.values()) + 250_000_000
        for member in members:
            pid = results[member]["pid"]
            save(folder / ("ready-" + member + ".json"), {"barrier_id": barrier_id, "key": key,
                 "participant": member, "pid": pid, "ready_monotonic_ns": ready_ns[member], "ready_at_utc": stamp})
            base = {"schema_version": identity["schema_version"], "barrier_id": barrier_id,
                    "participant": member, "pid": pid, "key": key, "at_utc": stamp}
            events[member].append(base | {"event": "ready", "monotonic_ns": ready_ns[member] + 1})
            events[member].append(base | {"event": "released", "monotonic_ns": target + 1,
                                         "release_monotonic_ns": target, "ready_monotonic_ns": ready_ns})
            if key["phase"] != "setup":
                results[member]["records"].append(key | {"action_start_monotonic_seconds": (target + 2000) / 1e9})
        save(folder / "RELEASE.json", {"barrier_id": barrier_id, "key": key, "participants": members,
             "ready_monotonic_ns": ready_ns, "release_monotonic_ns": target})
    for member in members:
        events[member].append({"schema_version": identity["schema_version"], "barrier_id": barrier_id,
             "participant": member, "pid": results[member]["pid"], "key": None, "at_utc": stamp,
             "event": "worker_finished", "monotonic_ns": 99_000_000_000})
        (root / ("events-" + member + ".jsonl")).write_text("".join(json.dumps(row) + "\n" for row in events[member]))
    return plan, wave, directory, results, queries


def test_barrier_actual_ready_release_and_timer_are_bound(tmp_path):
    plan, wave, directory, results, queries = barrier_fixture(tmp_path)
    proof = replay.check_barriers(plan, wave, directory, results, replay.Inputs(), queries=queries)
    assert proof["status"] == "PASS" and proof["verified_releases"] == 5


def test_barrier_lifetime_does_not_substitute_for_action_release(tmp_path):
    plan, wave, directory, results, queries = barrier_fixture(tmp_path)
    results["spark_cpu"]["records"][0]["action_start_monotonic_seconds"] = 0
    with pytest.raises(ValueError, match="timer started before"):
        replay.check_barriers(plan, wave, directory, results, replay.Inputs(), queries=queries)


def test_barrier_missing_peer_release_is_rejected(tmp_path):
    plan, wave, directory, results, queries = barrier_fixture(tmp_path)
    path = directory / "barriers/events-spark_cpu.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows = [row for row in rows if not (row["event"] == "released" and row["key"]["phase"] == "measured")]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="coverage differs"):
        replay.check_barriers(plan, wave, directory, results, replay.Inputs(), queries=queries)


def test_no_handoff_can_never_be_terminal():
    proof = replay.handoff_proof(None, None, None, [], replay.Inputs())
    assert proof["status"] == "NOT_PROVIDED"
    assert not proof["same_scheduler_resumed"] and not proof["no_action_overlap"]


def handoff_fixture(tmp_path):
    """Fabricated lifecycle fields; never used with the full genuine replay gate."""
    import copy
    reference = json.loads((ARCHIVE / "REFERENCE_CAMPAIGN.json").read_text())
    role = json.loads((ARCHIVE / "CONTENTION_PLAN.json").read_text())
    planned = next(s for s in reference["sessions"] if s["session_id"] == "block-04-complex-sf30-spark_cpu")
    root = tmp_path / "SYNTHETIC_HANDOFF_ONLY"
    held = tmp_path / "SYNTHETIC_HELD_ONLY"
    attempt = tmp_path / "SYNTHETIC_ATTEMPT_ONLY"
    for directory in (root, held, attempt):
        directory.mkdir()
    remote_attempt = planned["output_root"] + "/attempt-001"
    old = {"attempt": 1, "status": "running", "attempt_root": remote_attempt, "run_root": remote_attempt + "/run",
           "pid": 11, "pgid": 11, "pid_create_time": 2.0}
    session = {"schema_version": "research-session-v1", "campaign_sha256": replay.REFERENCE_SHA,
               "session": planned, "attempt": old}
    journal = {"campaign_sha256": replay.REFERENCE_SHA,
               "sessions": {planned["session_id"]: {"status": "running", "attempts": [old]}}}
    save(held / "ORIGINAL_HELD_SESSION.json", session)
    save(held / "ORIGINAL_HELD_JOURNAL.json", journal)
    finalized = copy.deepcopy(session)
    finalized["attempt"].update(status="succeeded", returncode=0, ended_at_utc="2026-01-01T03:01:01+00:00",
                                orchestrator_wall_seconds=10861)
    save(attempt / "session.json", finalized)
    results_relative = Path("run/sf30/spark_cpu/results.json")
    results = {"engine": planned["engine"], "suite": planned["suite"],
               "manifest_sha256": reference["datasets"]["30"]["manifest_sha256"],
               "completed_at_utc": "2026-01-01T01:01:00+00:00",
               "records": [{"host_result_ended_at_utc": "2026-01-01T01:00:59+00:00"}],
               "scope": "FABRICATED UNIT LIFECYCLE ONLY"}
    save(attempt / results_relative, results)
    schedule_relative = Path("run/schedule.json")
    save(attempt / schedule_relative, [{"status": "complete", "returncode": 0,
                                      "engine": planned["engine"], "scale": planned["scale"]}])
    inventory = {str(p.relative_to(attempt)): {"bytes": p.stat().st_size, "sha256": replay.sha(p)}
                 for p in attempt.rglob("*") if p.is_file()}
    inventory["session.json"] = {"bytes": (held / "ORIGINAL_HELD_SESSION.json").stat().st_size,
                                "sha256": replay.sha(held / "ORIGINAL_HELD_SESSION.json")}
    save(root / "ORIGINAL_ATTEMPT_FILES.json", inventory)
    save(root / "ORIGINAL_RUNNER_DRAINED.json", {"runner_exit": {"state": "Z", "exit_code": 0, "wait_status": 0},
         "results_path": str(Path(remote_attempt) / results_relative), "results_sha256": replay.sha(attempt / results_relative),
         "schedule_path": str(Path(remote_attempt) / schedule_relative), "schedule_sha256": replay.sha(attempt / schedule_relative),
         "completed_at_utc": "2026-01-01T01:01:01+00:00"})
    scheduler = {"pid": 41, "created": 42.0, "ppid": 55, "cwd": role["original_source_root"],
                 "cmdline": ["/TEST_ONLY/python", "research_campaign.py", "--run",
                             str(Path(reference["output_root"]).parent / "campaign.json")]}
    spec = {"schema_version": "h100-contention-driver-spec-v1", "scope": "FABRICATED UNIT LIFECYCLE ONLY"}
    save(root / "DRIVER_SPEC.json", spec)
    config = {"schema_version": "h100-contention-handoff-v1", "scheduler": scheduler,
              "handoff_source_sha256": replay.sha(REPO / "operations/contention_handoff.py"),
              "campaign": str(Path(reference["output_root"]).parent / "campaign.json"),
              "profile": str(Path(reference["output_root"]).parent / "EXECUTION_PROFILE.json"),
              "source": role["original_source_root"], "driver_spec_sha256": replay.sha(root / "DRIVER_SPEC.json")}
    save(root / "CONFIG.json", config)
    save(root / "STATE.json", {"phase": "COMPLETE_RESUMED", "contention_started": True, "driver_returncode": 0,
         "snapshot": {"plan": reference, "session_id": planned["session_id"], "attempt": old,
                      "journal_sha256": replay.sha(held / "ORIGINAL_HELD_JOURNAL.json"), "supervisor": {"pid": 55}}})
    save(root / "RESUMED.json", {"schema_version": "h100-contention-handoff-resume-v1", "status": "RESUMED",
         "scheduler": scheduler, "contention_started": True, "actor": "handoff-manager",
         "resumed_at_utc": "2026-01-01T03:01:00+00:00"})
    checks = [{"scale": s, "files": count, "status": "pass"} for s, count in
              {"1": 57, "10": 123, "30": 185, "100": 371, "300": 918}.items()]
    idle = {"eligible": True, "nvidia_smi_returncode": 0, "gpu_compute_clients": "", "benchmark_processes": []}
    for filename, label, stamp in (("PROFILE_BEFORE_CONTENTION.json", "before-contention", "2026-01-01T01:02:00+00:00"),
                                    ("PROFILE_BEFORE_RESUME.json", "before-original-resume", "2026-01-01T03:00:00+00:00")):
        save(root / filename, {"schema_version": "h100-contention-interlude-profile-check-v1", "label": label,
             "status": "PASS", "completed_at_utc": stamp, "campaign_sha256": replay.REFERENCE_SHA,
             "execution_profile_sha256": replay.PROFILE_SHA, "input_integrity": {"checks": checks},
             "exclusive_host_before": idle, "exclusive_host_after": idle})
    save(root / "OWNED_GROUPS_AFTER_CLEANUP.json", {"live_members": []})
    for name in ("RESIDUAL_SCAN_before-profile.json", "RESIDUAL_SCAN_before-signal.json"):
        save(root / name, {"status": "CLEAR", "contention_processes": []})
    actions = [{"action_started_at_utc": "2026-01-01T02:00:00+00:00",
                "host_result_ended_at_utc": "2026-01-01T02:01:00+00:00"}]
    return root, held, attempt, actions, role, reference


def test_handoff_checks_real_resume_byte_and_timeline_contract(tmp_path):
    root, held, attempt, actions, role, reference = handoff_fixture(tmp_path)
    proof = replay.handoff_proof(root, held, attempt, actions, replay.Inputs(), role, reference)
    assert proof["status"] == "PASS" and proof["same_scheduler_resumed"]
    assert proof["all_original_input_files"] == 1654
    assert proof["files"]["held_journal"]["sha256"] == replay.sha(held / "ORIGINAL_HELD_JOURNAL.json")


def test_handoff_rejects_changed_original_worker_payload(tmp_path):
    root, held, attempt, actions, role, reference = handoff_fixture(tmp_path)
    (attempt / "run/sf30/spark_cpu/results.json").write_text("{\"tampered\":true}")
    with pytest.raises(ValueError, match="payload changed"):
        replay.handoff_proof(root, held, attempt, actions, replay.Inputs(), role, reference)


def test_handoff_rejects_timed_overlap_even_after_all_jobs_exit(tmp_path):
    root, held, attempt, actions, role, reference = handoff_fixture(tmp_path)
    actions[0]["action_started_at_utc"] = "2026-01-01T01:00:00+00:00"
    with pytest.raises(ValueError, match="overlap"):
        replay.handoff_proof(root, held, attempt, actions, replay.Inputs(), role, reference)


def test_actual_error_finally_resume_shape_is_accepted(tmp_path):
    root, held, attempt, actions, role, reference = handoff_fixture(tmp_path)
    state = json.loads((root / "STATE.json").read_text())
    state.update(phase="HANDOFF_ERROR", error="FABRICATED participant failure", driver_returncode=1)
    save(root / "STATE.json", state)
    assert replay.handoff_proof(root, held, attempt, actions, replay.Inputs(), role, reference)["status"] == "PASS"


def test_prestart_without_raw_data_remains_partial(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("A subprocess was executed during offline replay"))
    raw = tmp_path / "NO_MEASUREMENT_FIXTURE"
    raw.mkdir()
    args = types.SimpleNamespace(archive=ARCHIVE, raw_backups=raw, output=tmp_path / "pending-audit",
                                 final_metadata=None, handoff=None, held_original=None, original_attempt=None)
    result = replay.replay(args)
    assert result["status"] == "PARTIAL" and result["integrity_status"] == "PASS"
    assert result["publication_ready"] is False and result["counts"]["recorded_outputs"] == 0
    assert result["disposition"]["main"]["attempted_waves"] == 0
    with pytest.raises(ValueError, match="new directory"):
        replay.replay(args)


def test_replay_cannot_write_inside_immutable_inputs(tmp_path):
    raw = tmp_path / "SYNTHETIC_RAW_DIRECTORY_ONLY"
    raw.mkdir()
    args = types.SimpleNamespace(archive=ARCHIVE, raw_backups=raw, output=raw / "forbidden-output",
                                 final_metadata=None, handoff=None, held_original=None, original_attempt=None)
    with pytest.raises(ValueError, match="immutable audit inputs"):
        replay.replay(args)
    assert not args.output.exists()


def terminal_fixture(tmp_path):
    root = tmp_path / "SYNTHETIC_DRIVER_METADATA_ONLY"
    root.mkdir()
    inputs = replay.Inputs()
    snapshots, disposition, checkpoints = {}, {}, []
    for phase, count in (("pilot", 4), ("main", 16)):
        control = {"schema_version": "h100-contention-offhost-backup-v1", "phase": phase,
                   "plan_sha256": replay.PLAN_SHA, "tar_sha256": "a" * 64, "manifest_sha256": "b" * 64,
                   "tar_bytes": 100, "files": 10, "scope": "FABRICATED UNIT CONTRACT ONLY"}
        control_path = tmp_path / (phase + ".json")
        save(control_path, control)
        ack_path = root / "backup-receipts" / (phase + ".json")
        save(ack_path, control | {"status": "OFFHOST_SHA256_VERIFIED"})
        snapshots[phase] = {"control_path": str(control_path), "control_sha256": replay.sha(control_path)}
        disposition[phase] = {"status": "PASS", "phase_sha256": "c" * 64}
        checkpoints.append({"phase": phase, "receipt_sha256": replay.sha(ack_path), "control_sha256": replay.sha(control_path)})
    value = {"schema_version": "h100-contention-phases-driver-v1", "plan_sha256": replay.PLAN_SHA,
             "prestart_sha256": replay.PRESTART_SHA, "status": "COMPLETE_OFFHOST", "active_phase": None,
             "waiting_phase": None, "completed_at_utc": "2026-01-01T04:00:00+00:00", "offhost_checkpoints": checkpoints,
             "phases": [{"phase": phase, "returncode": 0, "phase_sha256": "c" * 64} for phase in ("pilot", "main")]}
    save(root / "PHASE_DRIVER.json", value)
    return root, snapshots, disposition, value


def test_terminal_driver_requires_all_actual_ack_bytes(tmp_path):
    root, snapshots, disposition, value = terminal_fixture(tmp_path)
    proof = replay.terminal_driver(root, {}, snapshots, disposition, replay.Inputs())
    assert proof["terminal"] is True and set(proof["remote_receipts"]) == {"pilot", "main"}
    ack = root / "backup-receipts/main.json"
    data = json.loads(ack.read_text())
    data["tar_sha256"] = "d" * 64
    save(ack, data)
    with pytest.raises(ValueError, match="ACK bytes differ"):
        replay.terminal_driver(root, {}, snapshots, disposition, replay.Inputs())


def test_terminal_driver_rejects_missing_checkpoint(tmp_path):
    root, snapshots, disposition, value = terminal_fixture(tmp_path)
    value["offhost_checkpoints"].pop()
    save(root / "PHASE_DRIVER.json", value)
    with pytest.raises(ValueError, match="both completed off-host phases"):
        replay.terminal_driver(root, {}, snapshots, disposition, replay.Inputs())
