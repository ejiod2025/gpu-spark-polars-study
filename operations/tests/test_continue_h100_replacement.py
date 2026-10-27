"""Controller failure boundaries; no network or benchmark is executed."""
import ast
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("replacement_controller", Path(__file__).resolve().parents[1] / "continue_h100_replacement.py")
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)


@pytest.fixture
def plan(monkeypatch):
    files = {f"file-{i}.py": "a" * 64 for i in range(52)}
    tree = controller.hashlib.sha256(controller.json_bytes(files)).hexdigest()
    monkeypatch.setattr(controller, "SOURCE_TREE", tree)
    return {"schema_version": "research-campaign-v1", "campaign_id": controller.CAMPAIGN_ID,
            "seed": 20261026, "sessions": [{} for _ in range(400)], "blocks": [{} for _ in range(10)],
            "primary_contrasts": [{} for _ in range(26)], "output_root": "/evidence/campaign-results",
            "source": {"files_sha256": files, "source_tree_sha256": tree,
                       "git_head": controller.SOURCE_COMMIT, "git_status": ""},
            "settings": {"cpus": 26, "runs": 3},
            "datasets": {str(scale): {"manifest_sha256": str(scale)} for scale in (1, 10, 30, 100, 300)}}


def domain(plan):
    return {"schema_version": "h100-replacement-input-domain-v1", "duckdb_version": "1.5.6",
            "completed_at_utc": "2026-10-27T00:00:00Z",
            "tpch_extension_files_sha256": {"/home/ubuntu/.duckdb/tpch.duckdb_extension": "a" * 64},
            "scales": {scale: {"manifest_sha256": value["manifest_sha256"],
                               "null_counts": {name: 0 for name in ("o_totalprice", "o_orderdate", "o_orderkey", "o_custkey")}}
                       for scale, value in plan["datasets"].items()}}


@pytest.mark.parametrize("change", ["old_campaign", "partial_sample", "dirty_source", "other_tree", "seed", "threads"])
def test_replacement_gate_rejects_wrong_cohort_or_design(plan, change):
    controller.validate_plan(plan, "/evidence")
    bad = copy.deepcopy(plan)
    if change == "old_campaign":
        bad["campaign_id"] = "original"
    elif change == "partial_sample":
        bad["sessions"].pop()
    elif change == "dirty_source":
        bad["source"]["git_status"] = " M workloads.py"
    elif change == "other_tree":
        bad["source"]["files_sha256"]["file-0.py"] = "b" * 64
    elif change == "seed":
        bad["seed"] += 1
    else:
        bad["settings"]["cpus"] = 25
    with pytest.raises(ValueError, match="Replacement plan differs"):
        controller.validate_plan(bad, "/evidence")


def test_domain_binds_all_new_manifests_and_original_tested_domain(plan):
    value = domain(plan)
    controller.validate_domain(value, plan)
    value["scales"]["300"]["manifest_sha256"] = "old files"
    with pytest.raises(ValueError, match="another physical dataset"):
        controller.validate_domain(value, plan)
    value = domain(plan)
    value["scales"]["300"]["null_counts"]["o_totalprice"] = 1
    with pytest.raises(ValueError, match="nonnull performance domain"):
        controller.validate_domain(value, plan)


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", controller.BRANCH, str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", f"https://github.com/{controller.REPOSITORY}.git"], check=True)
    (repo / "tracked.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=fixture@example.test", "-c", "user.name=Fixture", "commit", "-qm", "fixture"], check=True)
    return repo


def test_repository_gate_refuses_other_branch_origin_and_user_changes(repository):
    controller.require_repository(repository)
    (repository / "tracked.txt").write_text("user work\n")
    with pytest.raises(ValueError, match="Unrelated tracked changes"):
        controller.require_repository(repository)
    controller.require_repository(repository, ["tracked.txt"])
    subprocess.run(["git", "-C", str(repository), "checkout", "-qb", "other"], check=True)
    with pytest.raises(ValueError, match="requires branch"):
        controller.require_repository(repository)
    subprocess.run(["git", "-C", str(repository), "checkout", "-q", controller.BRANCH], check=True)
    subprocess.run(["git", "-C", str(repository), "remote", "set-url", "origin", "https://github.com/elsewhere/repo.git"], check=True)
    with pytest.raises(ValueError, match="Unexpected Git origin"):
        controller.require_repository(repository)


def test_repository_gate_refuses_someone_elses_staged_new_file(repository):
    (repository / "unrelated.txt").write_text("user work\n")
    subprocess.run(["git", "-C", str(repository), "add", "unrelated.txt"], check=True)
    with pytest.raises(ValueError, match="Unexpected staged files|Unrelated tracked changes"):
        controller.require_repository(repository)


def test_registration_preserves_original_and_rejects_conflicting_existing_identity(tmp_path):
    repo = tmp_path / "repo"
    registry = repo / controller.REGISTRY
    registry.parent.mkdir(parents=True)
    original = {"schema_version": "h100-registered-campaign-identities-v1", "campaigns": {"old": {"fixed": True}}}
    registry.write_bytes(controller.json_bytes(original))
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "PRESTART_MANIFEST.json").write_text("actual sealed bytes\n")
    entry = controller.register_archive(repo, archive, "a" * 64)
    assert json.loads(registry.read_bytes())["campaigns"]["old"] == original["campaigns"]["old"]
    assert entry["prestart_sha256"] == controller.sha(archive / "PRESTART_MANIFEST.json")
    prior = registry.read_bytes()
    controller.register_archive(repo, archive, "a" * 64)
    assert registry.read_bytes() == prior
    (archive / "PRESTART_MANIFEST.json").write_text("changed\n")
    with pytest.raises(ValueError, match="existing campaign registry entry differs"):
        controller.register_archive(repo, archive, "a" * 64)


def stub_remote_python(monkeypatch, capsys, captured):
    def execute(code, *args, **kwargs):
        monkeypatch.setattr(sys, "argv", ["-c", *map(str, args)])
        def popen(command, **values):
            captured.append(command)
            return SimpleNamespace(pid=424242)
        monkeypatch.setattr(subprocess, "Popen", popen)
        namespace = {}
        try:
            exec(compile(code, "remote-control.py", "exec"), namespace)
        except SystemExit as exc:
            assert exc.code == 0
        return json.loads(capsys.readouterr().out)
    return execute


@pytest.mark.parametrize("program,expected_exit", [("/usr/bin/true", "0"), ("/usr/bin/false", "1")])
def test_actual_generated_domain_shell_records_success_and_failure(tmp_path, monkeypatch, capsys, program, expected_exit):
    obj = object.__new__(controller.Controller)
    obj.args = SimpleNamespace(evidence=str(tmp_path), staging=str(tmp_path / "stage"))
    captured = []
    original_popen = subprocess.Popen
    obj.remote = stub_remote_python(monkeypatch, capsys, captured)
    obj.record = lambda *args, **kwargs: None
    obj.wait_for = lambda *args: None
    obj.run_domain({"input_domain": None})
    script = captured[0][2]
    assert script.startswith("set -euo pipefail\n")
    monkeypatch.setattr(subprocess, "Popen", original_popen)
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    script = script.replace("/home/ubuntu/study/.venv/bin/python", program)
    result = subprocess.run(["bash", "-c", script], capture_output=True)
    assert result.returncode == int(expected_exit)
    assert (tmp_path / "controller-domain.exit").read_text() == expected_exit + "\n"


def test_lost_launch_reply_reconciles_one_durable_remote_marker(tmp_path, monkeypatch, capsys):
    obj = object.__new__(controller.Controller)
    obj.args = SimpleNamespace(evidence=str(tmp_path), staging=str(tmp_path / "stage"))
    obj.record = lambda *args, **kwargs: None
    captured = []
    actual_remote = stub_remote_python(monkeypatch, capsys, captured)
    def lost_reply(code, *args):
        actual_remote(code, *args)
        raise subprocess.TimeoutExpired("ssh", 30)
    obj.remote = lost_reply
    obj.wait_for = lambda *args: {"CONTROLLER_SUPERVISOR_LAUNCH.json": json.loads((tmp_path / "CONTROLLER_SUPERVISOR_LAUNCH.json").read_bytes())}
    obj.launch_supervisor()
    assert len(captured) == 1
    # A new controller attempt can read the marker, but cannot fork again.
    obj.remote = actual_remote
    obj.launch_supervisor()
    assert len(captured) == 1


def test_explicit_remote_launch_rejection_is_not_hidden_as_network_retry():
    obj = object.__new__(controller.Controller)
    obj.args = SimpleNamespace(evidence="/evidence", staging="/staging")
    obj.remote = lambda *args: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "ssh", stderr=b"already started"))
    obj.wait_for = lambda *args: pytest.fail("A remote rejection must not enter a transport retry")
    with pytest.raises(RuntimeError, match="launch was rejected"):
        obj.launch_supervisor()


def test_failure_before_prestart_commit_cannot_acknowledge_or_start_watcher(plan, monkeypatch):
    obj = object.__new__(controller.Controller)
    obj.args = SimpleNamespace(evidence="/evidence")
    obj.repo = Path("/repository")
    monkeypatch.setattr(controller, "require_repository", lambda *args: None)
    monkeypatch.setattr(controller, "git", lambda *args, **kwargs: "tracked")
    status = {"prepare.exit": "0", "READY_TO_ARCHIVE_PLAN": "ready", "readiness": {"status": "PASS", "outputs": 116, "errors": [], "fixtures": 5},
              "plan": plan, "campaign_sha256": "a" * 64, "campaign_sidecar": "a" * 64,
              "backup-receipts/prestart.json": None, "CONTROLLER_SUPERVISOR_LAUNCH.json": None}
    obj.wait_for = lambda predicate, phase: status
    obj.record = lambda *args, **kwargs: None
    obj.deploy = lambda: None
    obj.run_domain = lambda status: None
    obj.launch_supervisor = lambda: None
    obj.archive_and_commit = lambda campaign: (_ for _ in ()).throw(RuntimeError("archive or push failed"))
    obj.acknowledge_and_watch = lambda *args: pytest.fail("Prestart must not be acknowledged after failed archival/push")
    with pytest.raises(RuntimeError, match="archive or push failed"):
        obj.run()


def test_status_probe_does_not_treat_kernel_cmdline_as_missing_pid():
    definition = next(item for item in ast.parse(controller.STATUS_CODE).body
                      if isinstance(item, ast.FunctionDef) and item.name == "proc")
    requests = []
    class ProcessPath:
        def __init__(self, value):
            requests.append(value)
        def __truediv__(self, value):
            return self
        def read_bytes(self):
            raise ProcessLookupError("exited between process probes")
    namespace = {"pathlib": SimpleNamespace(Path=ProcessPath)}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), "proc-probe", "exec"), namespace)
    assert namespace["proc"](None) is None
    assert requests == []
    assert namespace["proc"]("123") is None


@pytest.mark.parametrize("exit_marker,command,message", [("1", "alive", "recording failed"), (None, None, "disappeared")])
def test_domain_failure_or_dead_process_never_relaunches(exit_marker, command, message):
    obj = object.__new__(controller.Controller)
    obj.args = SimpleNamespace(evidence="/evidence", staging="/staging")
    launches = []
    obj.remote = lambda *args: launches.append(args)
    obj.record = lambda *args, **kwargs: None
    def wait(predicate, phase):
        return predicate({"controller-domain.exit": exit_marker,
                          "controller-domain-launch.json": {"pid": 123}, "domain_command": command})
    obj.wait_for = wait
    with pytest.raises(RuntimeError, match=message):
        obj.run_domain({"input_domain": None})
    assert len(launches) == 1


def test_transport_retries_preserve_failure_boundaries(monkeypatch):
    obj = object.__new__(controller.Controller)
    obj.args = SimpleNamespace(interval_seconds=60)
    events = []
    obj.record = lambda *args, **kwargs: events.append(args[0])
    monkeypatch.setattr(controller.time, "sleep", lambda value: None)
    remaining = [subprocess.TimeoutExpired("ssh", 30), subprocess.CalledProcessError(255, "ssh"), "verified"]
    def operation():
        value = remaining.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value
    assert obj.transport_retry(operation, "retry") == "verified"
    assert events == ["retry", "retry"]
    with pytest.raises(ValueError, match="tampered"):
        obj.transport_retry(lambda: (_ for _ in ()).throw(ValueError("tampered")), "never")
    with pytest.raises(subprocess.CalledProcessError):
        obj.transport_retry(lambda: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "remote guard")), "never")
