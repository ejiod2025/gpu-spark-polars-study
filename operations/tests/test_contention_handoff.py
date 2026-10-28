"""Fabricated handoff mechanism boundaries; no VM, signals or workloads run."""
import importlib.util
import json
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("contention_handoff", Path(__file__).resolve().parents[1] / "contention_handoff.py")
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


def identity(pid=100):
    return {"pid": pid, "created": 123.5, "cmdline": ["python", "research_campaign.py"],
            "cwd": "/frozen", "ppid": 99, "pgid": 99, "status": "sleeping"}


def test_stop_refuses_changed_identity_without_any_signal(monkeypatch):
    monkeypatch.setattr(h, "same_process", lambda *a, **kw: False)
    monkeypatch.setattr(h.os, "kill", lambda *a: pytest.fail("Must not signal changed process"))
    with pytest.raises(ValueError, match="identity changed"):
        h.stop_scheduler(identity())


def test_stop_is_positive_pid_only_and_confirms_stopped(monkeypatch):
    calls = []
    monkeypatch.setattr(h, "same_process", lambda *a, **kw: True)
    monkeypatch.setattr(h.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    h.stop_scheduler(identity())
    assert calls == [(100, signal.SIGSTOP)]


def test_resume_requires_same_stopped_process_and_pid_only(monkeypatch):
    calls = []
    monkeypatch.setattr(h, "same_process", lambda *a, **kw: False)
    monkeypatch.setattr(h.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    with pytest.raises(ValueError, match="owned stopped"):
        h.resume_scheduler(identity())
    assert not calls
    monkeypatch.setattr(h, "same_process", lambda *a, **kw: kw.get("require_stopped") is True)
    h.resume_scheduler(identity())
    assert calls == [(100, signal.SIGCONT)]


def test_pid_reuse_is_never_owned(monkeypatch):
    monkeypatch.setattr(h.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 999.0))
    assert not h.same_process(identity(), strict=False)


def test_proc_stat_exit_handles_spaces_and_parentheses(tmp_path):
    fields = ["0"] * 50
    fields[0] = "Z"
    fields[49] = str(52 << 8)
    p = tmp_path / "stat"
    p.write_text("100 (a strange ) command) " + " ".join(fields))
    assert h.proc_stat_exit(p) == {"state": "Z", "wait_status": 52 << 8, "exit_code": 52}
    fields[0] = "S"
    p.write_text("100 (python) " + " ".join(fields))
    with pytest.raises(ValueError, match="retained zombie"):
        h.proc_stat_exit(p)


@pytest.mark.parametrize("command", [["--run"], ["--run", "a", "--run", "b"], ["a"]])
def test_ambiguous_command_argument_is_rejected(command):
    with pytest.raises(ValueError):
        h.argument(command, "--run")


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    campaign = evidence / "campaign.json"
    output = evidence / "campaign-results"
    output.mkdir()
    run = output / "session/attempt-001/run"
    run.mkdir(parents=True)
    attempt = {"pid": 101, "pid_create_time": 123.5, "run_root": str(run), "attempt_root": str(run.parent)}
    journal = {"campaign_sha256": "temporary", "sessions": {"session": {"status": "running", "attempts": [attempt]}}}
    plan = {"source": {"git_head": h.SOURCE_COMMIT}, "output_root": str(output)}
    campaign.write_text(json.dumps(plan))
    digest = h.sha(campaign)
    journal["campaign_sha256"] = digest
    (output / "journal.json").write_text(json.dumps(journal))
    scheduler = {**identity(), "cmdline": ["python", "-B", "research_campaign.py", "--run", str(campaign), "--max-sessions", "40"], "cwd": str(source)}
    parent = {**identity(99), "cmdline": ["bash", "/staging/run_h100_gclocker100.sh"]}
    runner = {**identity(101), "ppid": 100, "pgid": 101,
              "cmdline": ["python", str(source / "run_experiment.py"), "--output", str(run)]}
    monkeypatch.setattr(h, "same_process", lambda *a, **kw: True)
    monkeypatch.setattr(h, "proc_identity", lambda pid: {99: parent, 101: runner}[pid])
    monkeypatch.setattr(h, "held_lock", lambda *a: None)
    return SimpleNamespace(campaign=campaign, source=source, output=output, run=run,
                           scheduler=scheduler, parent=parent, runner=runner, attempt=attempt, digest=digest)


def test_boundary_retains_original_journal_and_exact_child(boundary):
    b = boundary
    before = (b.output / "journal.json").read_bytes()
    snapshot = h.check_scheduler_snapshot(b.scheduler, b.campaign, b.source, 99, b.digest)
    assert snapshot["runner"] == b.runner
    assert snapshot["session_id"] == "session"
    assert (b.output / "journal.json").read_bytes() == before


@pytest.mark.parametrize("change", ["wrong_child_parent", "wrong_child_group", "gated_child", "retry_flag", "wrong_supervisor"])
def test_uncertain_boundaries_refuse_before_stop(boundary, change):
    b = boundary
    if change == "wrong_child_parent":
        b.runner["ppid"] = 777
    elif change == "wrong_child_group":
        b.runner["pgid"] = 100
    elif change == "gated_child":
        b.runner["cmdline"].insert(1, "-c")
    elif change == "retry_flag":
        b.scheduler["cmdline"].append("--retry-failed")
    else:
        b.parent["cmdline"] = ["bash", "different.sh"]
    with pytest.raises(ValueError):
        h.check_scheduler_snapshot(b.scheduler, b.campaign, b.source, 99, b.digest)


def test_live_original_runner_is_not_hashed_or_signaled(boundary, monkeypatch):
    b = boundary
    monkeypatch.setattr(h.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 123.5, status=lambda: "sleeping"))
    monkeypatch.setattr(h, "sha", lambda *a: pytest.fail("No hashing while original is live"))
    assert h.original_drained({"attempt": b.attempt, "runner": b.runner}) is None


def test_failed_original_exit_does_not_start_contention(boundary, monkeypatch):
    b = boundary
    monkeypatch.setattr(h.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 123.5, status=lambda: h.psutil.STATUS_ZOMBIE))
    monkeypatch.setattr(h, "proc_stat_exit", lambda p: {"exit_code": 52})
    with pytest.raises(ValueError, match="Original runner failed"):
        h.original_drained({"attempt": b.attempt, "runner": b.runner})


def test_attempt_inventory_rejects_links(boundary):
    b = boundary
    (b.run / "safe.json").write_text("{}")
    initial = h.attempt_inventory({"attempt": b.attempt})
    assert "run/safe.json" in initial
    (b.run / "link").symlink_to(b.run / "safe.json")
    with pytest.raises(ValueError, match="symlink"):
        h.attempt_inventory({"attempt": b.attempt})


def test_contention_ownership_can_never_include_original_group(monkeypatch):
    monkeypatch.setattr(h, "same_process", lambda *a, **kw: False)
    state = {"driver": identity(200), "owned_driver_processes": [{"pid": 201, "created": 123.5, "pgid": 101}],
             "protected_processes": [{"pid": 101, "pgid": 101}]}
    with pytest.raises(ValueError, match="overlaps"):
        h.remember_owned(state)


def test_precontention_abort_resumes_without_heavy_checks(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(h, "same_process", lambda *a, **kw: True)
    monkeypatch.setattr(h, "profile_check", lambda *a: pytest.fail("Do not hash while original runner is active"))
    monkeypatch.setattr(h, "resume_scheduler", lambda value: calls.append(value["pid"]))
    monkeypatch.setattr(h, "foreign_contention_processes", lambda *a: [])
    result = h.verified_resume({"scheduler": identity()}, {"contention_started": False}, tmp_path, actor="test-fixture")
    assert calls == [100] and result["status"] == "RESUMED"
    assert result["contention_started"] is False


def test_changed_original_attempt_blocks_resume(tmp_path, monkeypatch):
    journal = tmp_path / "journal.json"
    journal.write_text("{}")
    h.atomic_json(tmp_path / "ORIGINAL_ATTEMPT_FILES.json", {"original": "expected"})
    state = {"contention_started": True, "snapshot": {"journal_path": str(journal), "journal_sha256": h.sha(journal)}}
    monkeypatch.setattr(h, "same_process", lambda *a, **kw: True)
    monkeypatch.setattr(h, "cleanup_owned", lambda value: None)
    monkeypatch.setattr(h, "live_owned_group_members", lambda value: [])
    monkeypatch.setattr(h, "foreign_contention_processes", lambda *a: [])
    monkeypatch.setattr(h, "profile_check", lambda *a: {"scope": "fabricated mechanism fixture"})
    monkeypatch.setattr(h, "attempt_inventory", lambda *a: {"changed": "bytes"})
    monkeypatch.setattr(h, "resume_scheduler", lambda *a: pytest.fail("Must not resume altered original evidence"))
    with pytest.raises(ValueError, match="attempt files changed"):
        h.verified_resume({"scheduler": identity()}, state, tmp_path, actor="test-fixture")


def test_already_resumed_receipt_does_not_signal_again(tmp_path, monkeypatch):
    h.atomic_json(tmp_path / "RESUMED.json", {"status": "RESUMED", "scope": "fabricated fixture"})
    monkeypatch.setattr(h, "same_process", lambda *a, **kw: pytest.fail("No second signal"))
    assert h.verified_resume({}, {}, tmp_path, actor="test-fixture")["status"] == "RESUMED"


def test_unobserved_orphan_cpu_contention_wrapper_blocks_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "same_process", lambda *a, **kw: True)
    monkeypatch.setattr(h, "foreign_contention_processes", lambda *a: [
        {"pid": 999, "created": 444.0, "markers": ["/staging/contention_worker.py"]}])
    monkeypatch.setattr(h, "resume_scheduler", lambda *a: pytest.fail("Never resume into orphan CPU contention"))
    with pytest.raises(RuntimeError, match="Unknown residual"):
        h.verified_resume({"scheduler": identity()}, {"contention_started": False}, tmp_path, actor="fixture")
    record = h.read_json(tmp_path / "RESIDUAL_SCAN_before-signal.json")
    assert record["status"] == "BLOCKED_RESUME" and record["contention_processes"][0]["pid"] == 999


def test_residual_scan_excludes_only_identity_bound_handoff_helpers(tmp_path, monkeypatch):
    current = h.os.getpid()
    made = 123.5
    process = lambda pid, script, status="sleeping": SimpleNamespace(info={
        "pid": pid, "create_time": made, "cmdline": ["python", script], "status": status})
    monkeypatch.setattr(h.psutil, "Process", lambda *a: SimpleNamespace(create_time=lambda: made))
    monkeypatch.setattr(h.psutil, "process_iter", lambda *a: [
        process(current, str(Path(h.__file__).resolve())),
        process(333, "/staging/contention_worker.py"),
        process(444, "/staging/contention_runner.py", h.psutil.STATUS_ZOMBIE)])
    blockers = h.foreign_contention_processes({"manager": {"pid": current, "created": made}}, tmp_path)
    assert [p["pid"] for p in blockers] == [333]


def test_unobserved_member_of_recorded_driver_group_blocks_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "same_process", lambda *a, **kw: True)
    monkeypatch.setattr(h, "cleanup_owned", lambda value: None)
    monkeypatch.setattr(h, "live_owned_group_members", lambda value: [{"pid": 777, "created": 3.0, "pgid": 200}])
    monkeypatch.setattr(h, "profile_check", lambda *a: pytest.fail("No profile/resume while owned group remains"))
    monkeypatch.setattr(h, "resume_scheduler", lambda *a: pytest.fail("No signal while owned group remains"))
    with pytest.raises(RuntimeError, match="process group remains"):
        h.verified_resume({"scheduler": identity()}, {"contention_started": True}, tmp_path, actor="fixture")
