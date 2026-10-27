"""Research schedule, integrity, resume, and real process-lifecycle contracts."""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import research_campaign as rc
import research_fixtures as fixtures
import run_experiment as runner


def configs():
    return [{"id": name, "engine": engine, "python": f"/env/{name}/python"}
            for name, engine in rc.CONFIG_ENGINES.items()]


def fixture_reports(root):
    entries = []
    for config, engine in rc.CONFIG_ENGINES.items():
        output = root / config
        (output / "plans").mkdir(parents=True)
        queries = {}
        for query in fixtures.QUERY_IDS:
            plan_name = f"plans/{query}.txt"
            plan = "GpuShuffledHashJoin LeftAnti\n" if engine == "spark_gpu" and query == "fx_semi_anti" else "fixture plan\n"
            (output / plan_name).write_text(plan)
            queries[query] = {"status": "PASS", "rows": fixtures.EXPECTED_ROWS[query],
                              "comparison": {"passed": True}, "plan": plan_name}
        value = {"schema_version": "research-fixtures-v1", "suite_version": fixtures.SUITE_VERSION,
                 "engine": engine, "version": "2.0.0" if config == "polars_cpu_200" else
                    "1.44.2" if engine.startswith("polars") else "3.5.9",
                 "status": "PASS", "completed_at_utc": "fixture", "queries": queries,
                 "supported_queries": len(queries), "unsupported_queries": [],
                 "nonempty_anti_semantics_passed": True, "gpu_left_anti_plan_observed": engine == "spark_gpu"}
        if engine == "polars_gpu":
            value["polars_gpu"] = {"raise_on_fail": True, "cpu_fallback_policy": "raise", "executor": "streaming",
                "streaming_fallback_policy": "warn", "session_guard": {
                    **{field: True for field in ("effective_config_verified", "tiny_query_passed",
                        "unsupported_canary_raised", "unsupported_canary_reason_verified")},
                    "effective_config": {"raise_on_fail": True, "executor": {"name": "streaming", "fallback_mode": "warn"}},
                    "unsupported_canary_error": "NotImplementedError: unsupported AnonymousFunction"}}
        path = output / "FIXTURES.json"
        path.write_text(json.dumps(value))
        entries.append({"config_id": config, "output": str(output), "report_sha256": rc.sha256(path),
                        "supported_queries": len(queries), "unsupported_queries": [],
                        "gpu_left_anti_plan_observed": engine == "spark_gpu"})
    return entries


def test_williams_balances_positions_and_every_directed_predecessor():
    ids = list(rc.CONFIG_ENGINES)
    orders = rc.williams_orders(ids, random.Random(102))
    assert len({tuple(row) for row in orders}) == 10
    assert Counter((position, name) for row in orders for position, name in enumerate(row)) == {
        (position, name): 2 for position in range(5) for name in ids}
    assert Counter((a, b) for row in orders for a, b in zip(row, row[1:])) == {
        (a, b): 2 for a in ids for b in ids if a != b}


def test_schedule_preserves_all_400_sessions_and_pairing(tmp_path):
    blocks, sessions = rc.make_schedule(configs(), 618, tmp_path)
    assert (blocks, sessions) == rc.make_schedule(configs(), 618, tmp_path)
    assert (blocks, sessions) != rc.make_schedule(configs(), 619, tmp_path)
    assert len(blocks) == 10 and len(sessions) == len({s["session_id"] for s in sessions}) == 400
    for block in blocks:
        assert {(c["suite"], c["scale"]) for c in block["cells"]} == set(rc.CELLS)
        for cell in block["cells"]:
            paired = [s for s in sessions if s["session_id"] in cell["session_ids"]]
            assert len(paired) == 5
            assert {s["query_seed"] for s in paired} == {cell["query_seed"]}
            assert [s["config_id"] for s in paired] == cell["config_order"]
    for suite, scale in rc.CELLS:
        observations = [s for s in sessions if (s["suite"], s["scale"]) == (suite, scale)]
        assert Counter((s["position"], s["config_id"]) for s in observations) == {
            (position, name): 2 for position in range(1, 6) for name in rc.CONFIG_ENGINES}


def test_duplicate_or_missing_configuration_rejected(tmp_path):
    bad = configs()
    bad[-1] = bad[0]
    with pytest.raises(ValueError, match="five distinct"):
        rc.make_schedule(bad, 1, tmp_path)


@pytest.fixture()
def frozen(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for relative in ("run_experiment.py", "worker.py", "tests/test_x.py", "requirements.txt",
                     "setup_research_control.sh", "docs/H100_CONFIRMATORY_PROTOCOL.md", "pytest.ini"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture: {relative}\n")
    monkeypatch.setattr(rc, "HERE", source)
    control = tmp_path / "control-python"
    control.symlink_to(sys.executable)

    def runtime(python):
        return {"python_info": [3, 11, 13], "python_version": "CPython fixture build",
                "python_build": ["fixture", "today"], "base_executable": str(Path(sys.executable).resolve()),
                "distributions": {**{name: "1" for name in rc.COMMON_PACKAGES},
                                  "polars": "2.0.0" if python == str(control) else "1.44.2"}}

    monkeypatch.setattr(rc, "runtime_identity", runtime)
    data = tmp_path / "data"
    for scale in ("1", "10", "30", "100", "300"):
        path = data / f"sf{scale}" / "region" / "data.parquet"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"synthetic input " + scale.encode())
        manifest = {"scale_factor": int(scale), "tables": {"region": {"files": [
            {"path": "region/data.parquet", "bytes": path.stat().st_size, "sha256": rc.sha256(path)}]}},
                    "total_parquet_bytes": path.stat().st_size}
        (path.parent.parent / "manifest.json").write_text(json.dumps(manifest))
    jar = tmp_path / "plugin.jar"
    jar.write_bytes(b"jar fixture")
    oracle_root = tmp_path / "oracles"
    oracle_root.mkdir()
    for suite, scale in rc.CELLS:
        module = importlib.import_module("workloads" if suite == "baseline" else "complex_workloads")
        oracle = {"suite": suite, "scale_factor": int(scale),
                  "manifest_sha256": rc.sha256(data / f"sf{scale}" / "manifest.json"),
                  "suite_version": getattr(module, "SUITE_VERSION", "baseline-v1"),
                  "completed_at_utc": "fixture", "status": "PASS", "queries": {
                      query: {"query_sha256": hashlib.sha256(module.spark_sql(query).encode()).hexdigest(),
                              "rows": [{"fixture": 1}]} for query in module.QUERY_IDS}}
        (oracle_root / f"{suite}-sf{scale}.json").write_text(json.dumps(oracle))
    readiness = tmp_path / "READINESS.json"
    readiness.write_text(json.dumps({"status": "PASS", "source": rc.source_identity(),
        "semantic_fixtures": fixture_reports(tmp_path / "fixtures"),
        "successful_outputs_compared": 116, "sessions": [
            {"suite": suite, "config_id": config} for suite in ("baseline", "complex")
            for config in rc.CONFIG_ENGINES]}))
    args = SimpleNamespace(plan=tmp_path / "campaign.json", output_root=tmp_path / "runs",
                           data_root=data, jar=jar, python=sys.executable,
                           oracle_root=oracle_root, readiness=readiness,
                           polars_cpu_200_python=str(control), seed=92, cpus=26,
                           driver_memory="96g", engine_timeout_seconds=7200,
                           cleanup_timeout_seconds=60, campaign_id="test")
    plan = rc.freeze_plan(args)
    return args, plan, source


def test_frozen_plan_has_exact_snapshot_and_cannot_overwrite(frozen):
    args, plan, source = frozen
    loaded, digest = rc.load_plan(args.plan)
    assert loaded == plan and digest == rc.sha256(args.plan)
    assert "requirements.txt" in plan["source"]["files_sha256"]
    assert plan["protocol_sha256"] == rc.sha256(source / "docs/H100_CONFIRMATORY_PROTOCOL.md")
    assert plan["analysis_predeclarations"]["primary_family_size"] == 26
    assert set(plan["semantic_fixtures"]) == set(rc.CONFIG_ENGINES)
    control = next(c for c in plan["configs"] if c["id"] == "polars_cpu_200")
    assert control["python"].endswith("control-python")  # venv symlink was preserved
    rc.check_frozen_inputs(plan, include_jar=True)
    rc.check_frozen_runtimes(plan)
    with pytest.raises(ValueError, match="overwrite"):
        rc.freeze_plan(args)


def test_schedule_tamper_rejected_even_after_rehash(frozen):
    args, plan, _ = frozen
    plan["sessions"][0]["query_seed"] += 1
    args.plan.write_bytes(rc.canonical_bytes(plan))
    args.plan.with_name(args.plan.name + ".sha256").write_text(rc.sha256(args.plan))
    with pytest.raises(ValueError, match="design/seed"):
        rc.load_plan(args.plan)


def test_planning_rejects_readiness_from_different_source(frozen):
    args, _, _ = frozen
    args.plan = args.plan.with_name("second-campaign.json")
    value = json.loads(args.readiness.read_text())
    value["source"]["files_sha256"]["worker.py"] = "wrong source"
    args.readiness.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Readiness must PASS"):
        rc.freeze_plan(args)


def test_planning_requires_all_eight_complete_oracles(frozen):
    args, _, _ = frozen
    args.plan = args.plan.with_name("second-campaign.json")
    (args.oracle_root / "complex-sf300.json").unlink()
    with pytest.raises(FileNotFoundError):
        rc.freeze_plan(args)


@pytest.mark.parametrize("drift", ["source", "snapshot", "manifest", "jar", "oracle", "readiness", "fixture", "fixtureplan"])
def test_frozen_input_and_source_drift_are_fatal(frozen, drift):
    _, plan, source = frozen
    paths = {"source": source / "worker.py",
             "snapshot": Path(plan["source"]["snapshot_directory"]) / "worker.py",
             "manifest": Path(plan["datasets"]["1"]["path"]) / "manifest.json",
             "jar": Path(plan["settings"]["jar"]),
             "oracle": Path(plan["oracles"]["baseline-sf1"]["path"]),
             "readiness": Path(plan["readiness"]["path"]),
             "fixture": Path(plan["semantic_fixtures"]["polars_cpu_144"]["path"]),
             "fixtureplan": Path(plan["semantic_fixtures"]["polars_cpu_144"]["path"]).parent / "plans/fx_outer_counts.txt"}
    paths[drift].write_text("changed")
    with pytest.raises(ValueError, match="drift|changed"):
        rc.check_frozen_inputs(plan, include_jar=True)


def edit_fixture(entries, config, edit):
    entry = next(entry for entry in entries if entry["config_id"] == config)
    path = Path(entry["output"]) / "FIXTURES.json"
    value = json.loads(path.read_text())
    edit(value)
    path.write_text(json.dumps(value))
    entry["report_sha256"] = rc.sha256(path)
    entry["supported_queries"] = value["supported_queries"]
    entry["unsupported_queries"] = value["unsupported_queries"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_readiness_semantics_require_exactly_five_distinct_configs(tmp_path, mutation):
    entries = fixture_reports(tmp_path / "fixtures")
    if mutation == "missing":
        entries.pop()
    else:
        entries[-1] = entries[0]
    with pytest.raises(ValueError, match="exactly five distinct"):
        rc.validate_readiness_fixtures({"semantic_fixtures": entries})


@pytest.mark.parametrize("mutation", ["overall_fail", "wrong_rows", "anti_flag", "gpu_guard", "unknown_query"])
def test_readiness_checks_actual_fixture_content_beyond_report_hash(tmp_path, mutation):
    entries = fixture_reports(tmp_path / "fixtures")
    def edit(value):
        if mutation == "overall_fail":
            value["status"] = "FAIL"
        elif mutation == "wrong_rows":
            value["queries"]["fx_outer_counts"]["rows"][0]["joined_rows"] = 12345
        elif mutation == "anti_flag":
            value["nonempty_anti_semantics_passed"] = False
        elif mutation == "gpu_guard":
            value["polars_gpu"]["raise_on_fail"] = False
        else:
            value["queries"]["invented_query"] = {"status": "PASS"}
    edit_fixture(entries, "polars_gpu", edit)
    with pytest.raises(ValueError):
        rc.validate_readiness_fixtures({"semantic_fixtures": entries})


def test_strict_gpu_rank_and_regex_rejections_are_explicit_valid_coverage(tmp_path):
    entries = fixture_reports(tmp_path / "fixtures")
    def edit(value):
        queries = ["fx_rank_ties", "fx_regex_distinct"]
        for query in queries:
            value["queries"][query] = {"status": "UNSUPPORTED", "error": "NotImplementedError: unsupported expression"}
        value["supported_queries"] = 3
        value["unsupported_queries"] = queries
    edit_fixture(entries, "polars_gpu", edit)
    bound = rc.validate_readiness_fixtures({"semantic_fixtures": entries})
    assert len(bound["polars_gpu"]["plans_sha256"]) == 3


def test_strict_gpu_core_join_rejection_must_be_diagnosed_before_formal_campaign(tmp_path):
    entries = fixture_reports(tmp_path / "fixtures")
    def edit(value):
        value["queries"]["fx_semi_anti"] = {"status": "UNSUPPORTED", "error": "NotImplementedError: unsupported expression"}
        value["nonempty_anti_semantics_passed"] = False
        value["supported_queries"] = 4
        value["unsupported_queries"] = ["fx_semi_anti"]
    edit_fixture(entries, "polars_gpu", edit)
    with pytest.raises(ValueError, match="Invalid semantic fixture rejection"):
        rc.validate_readiness_fixtures({"semantic_fixtures": entries})


def test_gpu_spark_left_anti_claim_requires_actual_saved_final_plan(tmp_path):
    entries = fixture_reports(tmp_path / "fixtures")
    entry = next(entry for entry in entries if entry["config_id"] == "spark_gpu")
    plan = Path(entry["output"]) / "plans/fx_semi_anti.txt"
    plan.write_text("== Final Plan ==\nSortMergeJoin LeftAnti\n== Initial Plan ==\nGpuShuffledHashJoin LeftAnti\n")
    with pytest.raises(ValueError, match="GPU LeftAnti final plan"):
        rc.validate_readiness_fixtures({"semantic_fixtures": entries})


def test_full_byte_gate_detects_changed_file_without_manifest_change(frozen):
    _, plan, _ = frozen
    assert len(rc.verify_dataset_bytes(plan)["checks"]) == 5
    path = Path(plan["datasets"]["30"]["path"]) / "region/data.parquet"
    path.write_bytes(path.read_bytes().replace(b"synthetic", b"SynthEtiC"))
    with pytest.raises(ValueError, match="Input bytes differ"):
        rc.verify_dataset_bytes(plan)


def test_runtime_drift_is_fatal(frozen, monkeypatch):
    _, plan, _ = frozen
    original = rc.runtime_identity
    monkeypatch.setattr(rc, "runtime_identity", lambda python: {**original(python), "python_version": "new build"})
    with pytest.raises(ValueError, match="runtime changed"):
        rc.check_frozen_runtimes(plan)


def test_command_uses_control_interpreter_and_shared_query_seed(frozen, tmp_path):
    _, plan, _ = frozen
    session = next(s for s in plan["sessions"] if s["config_id"] == "polars_cpu_200")
    command = rc.command_for(plan, session, tmp_path / "run")
    assert command[0] == session["python"]
    for flag, expected in (("--engines", "polars"), ("--runs", "3"),
                           ("--query-seed", str(session["query_seed"]))):
        assert command[command.index(flag) + 1] == expected
    assert "--cache-observation" in command


def journal_item(attempt):
    return {"sessions": {"session-1": {"status": "running", "attempts": [attempt]}}}


def test_resume_refuses_live_pid_even_with_explicit_recovery():
    me = psutil.Process()
    journal = journal_item({"pid": me.pid, "pid_create_time": me.create_time(), "pgid": os.getpgrp()})
    with pytest.raises(RuntimeError, match="live PID"):
        rc.reconcile_journal(journal, recover_interrupted=True)


def test_stale_running_requires_explicit_recovery(monkeypatch):
    monkeypatch.setattr(rc, "pid_matches", lambda attempt: False)
    monkeypatch.setattr(rc, "group_members", lambda pgid: [])
    journal = journal_item({"pid": 1234, "pid_create_time": 1, "pgid": 1234})
    with pytest.raises(RuntimeError, match="recover-interrupted"):
        rc.reconcile_journal(journal)
    rc.reconcile_journal(journal, recover_interrupted=True)
    assert journal["sessions"]["session-1"]["status"] == "interrupted"
    assert "recovery_reason" in journal["sessions"]["session-1"]["attempts"][0]


def test_orphan_group_prevents_recovery(monkeypatch):
    monkeypatch.setattr(rc, "pid_matches", lambda attempt: False)
    monkeypatch.setattr(rc, "group_members", lambda pgid: [4321])
    with pytest.raises(RuntimeError, match="live process group"):
        rc.reconcile_journal(journal_item({"pid": 1234, "pgid": 1234}), True)


def test_second_executor_cannot_take_live_lock(tmp_path):
    with rc.campaign_lock(tmp_path / "lock"):
        with pytest.raises(RuntimeError, match="holds this campaign lock"):
            with rc.campaign_lock(tmp_path / "lock"):
                pytest.fail("Lock admitted a competing executor")


def test_launch_gate_eof_never_launches_benchmark(tmp_path):
    marker = tmp_path / "launched"
    command = [sys.executable, "-c", rc.LAUNCH_GATE, sys.executable, "-c",
               f"from pathlib import Path; Path({str(marker)!r}).touch()"]
    result = subprocess.run(command, input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode == 99 and not marker.exists()


def test_campaign_termination_reaps_descendant_in_separate_group(tmp_path):
    marker = tmp_path / "child.pid"
    child_code = "import time; time.sleep(120)"
    parent_code = ("import subprocess,sys,time; from pathlib import Path; "
                   f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}],start_new_session=True); "
                   f"Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(120)")
    process = subprocess.Popen([sys.executable, "-c", parent_code], start_new_session=True)
    child = None
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        child = psutil.Process(int(marker.read_text()))
        identity = {"pid": child.pid, "pid_create_time": child.create_time()}
        rc.terminate_owned_group(process, grace=0.3)
        assert process.poll() is not None and not rc.pid_matches(identity)
        assert not rc.group_members(process.pid)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if child is not None and child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
            os.killpg(child.pid, signal.SIGKILL)


def test_runner_stop_reaps_group_after_leader_has_exited(tmp_path):
    marker = tmp_path / "child.pid"
    code = ("import subprocess,sys; from pathlib import Path; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
            f"Path({str(marker)!r}).write_text(str(p.pid))")
    process = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    process.wait(timeout=5)
    child_pid = int(marker.read_text())
    child = psutil.Process(child_pid)
    identity = {"pid": child_pid, "pid_create_time": child.create_time()}
    try:
        runner.stop_process_group(process, grace_seconds=0.1)
        deadline = time.monotonic() + 3
        while rc.pid_matches(identity) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not rc.pid_matches(identity)
    finally:
        if rc.pid_matches(identity):
            os.killpg(process.pid, signal.SIGKILL)


def test_runner_sigterm_kills_worker_group_and_retains_seed(tmp_path):
    source = tmp_path / "runner-source"
    source.mkdir()
    shutil.copyfile(REPO / "run_experiment.py", source / "run_experiment.py")
    marker = tmp_path / "worker.json"
    (source / "worker.py").write_text(
        "import os,sys,json,time,subprocess\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\n"
        f"Path({str(marker)!r}).write_text(json.dumps({{'pid':os.getpid(),'child':child.pid,'args':sys.argv}}))\n"
        "time.sleep(120)\n")
    data = tmp_path / "data" / "sf1"
    data.mkdir(parents=True)
    (data / "manifest.json").write_text('{"scale_factor":1}')
    output = tmp_path / "results"
    command = [sys.executable, str(source / "run_experiment.py"), "--data-root", str(data.parent),
               "--output", str(output), "--jar", str(tmp_path / "unused.jar"),
               "--engines", "polars", "--scales", "1", "--query-seed", "827", "--cpus", "1"]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    identities = []
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "Fake worker did not launch"
        details = json.loads(marker.read_text())
        identities = [{"pid": pid, "pid_create_time": psutil.Process(pid).create_time()}
                      for pid in (details["pid"], details["child"])]
        assert details["args"][details["args"].index("--seed") + 1] == "827"
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=20)
        deadline = time.monotonic() + 3
        while any(rc.pid_matches(identity) for identity in identities) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(rc.pid_matches(identity) for identity in identities)
        schedule = json.loads((output / "schedule.json").read_text())
        assert schedule[0]["status"] == "interrupted" and schedule[0]["query_seed"] == 827
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for identity in identities:
            if rc.pid_matches(identity):
                os.kill(identity["pid"], signal.SIGKILL)


def test_failed_attempt_is_durable_and_retry_gets_new_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "command_for", lambda *_: [sys.executable, "-c", "raise SystemExit(7)"])
    session = {"session_id": "test-session", "output_root": str(tmp_path / "session")}
    item = {"status": "planned", "attempts": []}
    journal = {"sessions": {"test-session": item}}
    path = tmp_path / "journal.json"
    for number in (1, 2):
        assert rc.execute_session({}, "digest", session, item, journal, path) == "failed"
        saved = json.loads(path.read_text())["sessions"]["test-session"]
        assert len(saved["attempts"]) == number
        assert saved["attempts"][-1]["returncode"] == 7
        assert Path(saved["attempts"][-1]["attempt_root"]).name == f"attempt-{number:03d}"
        assert (Path(saved["attempts"][-1]["attempt_root"]) / "session.json").exists()


def test_interrupted_attempt_retains_error_and_terminal_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "command_for", lambda *_: [sys.executable, "-c", "pass"])
    def interrupt(*_):
        raise KeyboardInterrupt("test interrupt at artifact boundary")
    monkeypatch.setattr(rc, "completed_artifacts", interrupt)
    session = {"session_id": "test-session", "output_root": str(tmp_path / "session")}
    item = {"status": "planned", "attempts": []}
    journal = {"sessions": {"test-session": item}}
    path = tmp_path / "journal.json"
    with pytest.raises(KeyboardInterrupt):
        rc.execute_session({}, "digest", session, item, journal, path)
    saved = json.loads(path.read_text())["sessions"]["test-session"]
    assert saved["status"] == "interrupted"
    assert saved["attempts"][0]["error"]["type"] == "KeyboardInterrupt"
    assert saved["attempts"][0]["ended_at_utc"]


def artifact_fixture(tmp_path):
    run = tmp_path / "run"
    output = run / "sf1" / "polars_gpu"
    output.mkdir(parents=True)
    observation = {"cache": {"errors": [], "files": {"x.parquet": {}}}, "host": {}}
    schedule = [{"status": "complete", "returncode": 0, "engine": "polars_gpu", "scale": "1",
                 "cache_observations": {key: observation for key in (
                     "before_preheat", "after_preheat", "after_worker")}}]
    (run / "schedule.json").write_text(json.dumps(schedule))
    result = {"engine": "polars_gpu", "suite": "complex", "runs": 3, "seed": 123,
              "completed_at_utc": "2026-10-26T01:00:00Z", "query_execution_status": "partial",
              "manifest_sha256": "fixture"}
    (output / "results.json").write_text(json.dumps(result))
    plan = {"settings": {"runs": 3}, "datasets": {"1": {"manifest_sha256": "fixture"}}}
    session = {"engine": "polars_gpu", "suite": "complex", "scale": "1", "query_seed": 123}
    return plan, session, run, output, result


def test_partial_coverage_is_operational_success_but_not_full_suite_score(tmp_path):
    plan, session, run, _, _ = artifact_fixture(tmp_path)
    assert rc.completed_artifacts(plan, session, run)["query_execution_status"] == "partial"


def test_wrong_seed_or_missing_cache_evidence_is_not_success(tmp_path):
    plan, session, run, output, result = artifact_fixture(tmp_path)
    result["seed"] = 42
    (output / "results.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="query seed"):
        rc.completed_artifacts(plan, session, run)
    result["seed"] = 123
    (output / "results.json").write_text(json.dumps(result))
    schedule = json.loads((run / "schedule.json").read_text())
    del schedule[0]["cache_observations"]["after_worker"]
    (run / "schedule.json").write_text(json.dumps(schedule))
    with pytest.raises(ValueError, match="cache observations"):
        rc.completed_artifacts(plan, session, run)


@pytest.mark.parametrize("returncode,clients,eligible", [(0, "", True), (0, "99, python, 500", False), (1, "", False)])
def test_gpu_idle_guard_refuses_clients_and_probe_failures(monkeypatch, returncode, clients, eligible):
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=returncode, stdout=clients, stderr=""))
    monkeypatch.setattr(rc.psutil, "process_iter", lambda *a, **kw: [])
    assert rc.observe_exclusive_host()["eligible"] is eligible


def test_bounded_run_resumes_without_repeating_completed_session(frozen, monkeypatch):
    args, plan, _ = frozen
    monkeypatch.setattr(rc, "observe_exclusive_host", lambda: {"eligible": True})
    monkeypatch.setattr(rc, "scientific_gate", lambda *_: {"status": "PASS"})

    def fake_command(plan, session, run_root):
        run_root = Path(run_root)
        output = run_root / f"sf{session['scale']}" / session["engine"]
        observation = {"cache": {"errors": [], "files": {"fixture": {}}}, "host": {}}
        schedule = [{"status": "complete", "returncode": 0,
                     "engine": session["engine"], "scale": session["scale"],
                     "cache_observations": {key: observation for key in (
                         "before_preheat", "after_preheat", "after_worker")}}]
        result = {"engine": session["engine"], "suite": session["suite"], "runs": 3,
                  "seed": session["query_seed"], "completed_at_utc": "fixture",
                  "manifest_sha256": plan["datasets"][session["scale"]]["manifest_sha256"],
                  "query_execution_status": "complete"}
        code = ("import json; from pathlib import Path; "
                f"output=Path({str(output)!r}); output.mkdir(parents=True); "
                f"(output/'results.json').write_text({json.dumps(result)!r}); "
                f"Path({str(run_root / 'schedule.json')!r}).write_text({json.dumps(schedule)!r})")
        return [sys.executable, "-c", code]

    monkeypatch.setattr(rc, "command_for", fake_command)
    run_args = SimpleNamespace(run=args.plan, recover_interrupted=False, retry_failed=False, max_sessions=1)
    first = rc.run_campaign(run_args)
    assert Counter(item["status"] for item in first["sessions"].values()) == {"succeeded": 1, "planned": 399}
    second = rc.run_campaign(run_args)
    assert Counter(item["status"] for item in second["sessions"].values()) == {"succeeded": 2, "planned": 398}
    first_id = plan["sessions"][0]["session_id"]
    assert len(second["sessions"][first_id]["attempts"]) == 1


def test_scientific_failure_halts_and_cannot_be_silently_reaudited(frozen, monkeypatch, tmp_path):
    import research_validate as rv
    args, plan, _ = frozen
    session = plan["sessions"][0]
    attempt_root = tmp_path / "attempt"
    attempt_root.mkdir()
    item = {"status": "succeeded", "attempts": [{"attempt_root": str(attempt_root)}]}
    journal = {"sessions": {session["session_id"]: item}}
    journal_path = tmp_path / "journal.json"
    report = {"session_id": session["session_id"], "status": "FAIL", "errors": ["Oracle mismatch"]}
    monkeypatch.setattr(rv, "audit_session", lambda *_: report)
    with pytest.raises(ValueError, match="Scientific validation failed"):
        rc.scientific_gate(plan, rc.sha256(args.plan), session, item, journal, journal_path)
    assert item["status"] == "succeeded" and item["scientific_validation"]["status"] == "FAIL"
    saved_hash = item["scientific_validation"]["sha256"]
    monkeypatch.setattr(rv, "audit_session", lambda *_: {**report, "status": "PASS", "errors": []})
    with pytest.raises(ValueError, match="reviewed protocol change"):
        rc.scientific_gate(plan, rc.sha256(args.plan), session, item, journal, journal_path)
    assert rc.sha256(item["scientific_validation"]["path"]) == saved_hash


def test_successful_scientific_gate_checks_saved_hash_on_resume(frozen, monkeypatch, tmp_path):
    import research_validate as rv
    args, plan, _ = frozen
    session = plan["sessions"][0]
    attempt_root = tmp_path / "attempt"
    attempt_root.mkdir()
    item = {"status": "succeeded", "attempts": [{"attempt_root": str(attempt_root)}]}
    journal = {"sessions": {session["session_id"]: item}}
    journal_path = tmp_path / "journal.json"
    report = {"session_id": session["session_id"], "status": "PASS", "errors": []}
    monkeypatch.setattr(rv, "audit_session", lambda *_: report)
    assert rc.scientific_gate(plan, rc.sha256(args.plan), session, item, journal, journal_path) == report
    assert rc.scientific_gate(plan, rc.sha256(args.plan), session, item, journal, journal_path) == report
    Path(item["scientific_validation"]["path"]).write_text("{}")
    with pytest.raises(ValueError, match="validation evidence changed"):
        rc.scientific_gate(plan, rc.sha256(args.plan), session, item, journal, journal_path)
