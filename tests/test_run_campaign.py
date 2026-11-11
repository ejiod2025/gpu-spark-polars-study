"""run_campaign.py: the locked design, the on-disk state machine (resume, repeat, stop) and its guards.

Orchestration tests substitute tiny stand-ins for run_experiment.py and preflight.py (the campaign
only talks to them through argv, stdout events, schedule.json and the report), so they run in seconds.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import textwrap

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import power_telemetry as pt  # noqa: E402
import run_campaign as rc  # noqa: E402
from sysfs_fakes import C3_SOCKET_1, c3_powercap, deny, ticking_sleep  # noqa: E402



def valid_lease(minutes=180):
    """A lease name in the launch convention that expires `minutes` from now."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return f"p3-test-owner-ab12-expires-{expires:%Y%m%dT%H%M}Z"


# ---- design -----------------------------------------------------------------------------------------


def formal_units():
    cfg = rc.resolve_config(rc.parse_args(["--dry-run"]))
    return cfg, rc.build_units(cfg)


def test_seed_reproduces_the_registered_letters():
    assert rc.derive_letters() == {"A": "spark_cpu", "B": "polars_gpu", "C": "polars", "D": "spark_gpu"}


def test_williams_square_is_balanced():
    letters = rc.derive_letters()
    rc.check_williams(rc.SEQUENCES, letters)  # does not raise
    orders = [[letters[c] for c in seq] for seq in rc.SEQUENCES]
    assert orders[0] == ["spark_cpu", "polars_gpu", "spark_gpu", "polars"]
    assert orders[1] == ["polars_gpu", "polars", "spark_cpu", "spark_gpu"]
    assert orders[2] == ["polars", "spark_gpu", "polars_gpu", "spark_cpu"]
    assert orders[3] == ["spark_gpu", "spark_cpu", "polars", "polars_gpu"]


@pytest.mark.parametrize("sequences", [
    ("ABDC", "BCAD", "CDBA", "DABC"),   # an adjacent pair repeats, one is missing
    ("ABDC", "BCAD", "CDBA", "CACB"),   # not a permutation
    ("ABCD", "BCDA", "CDAB", "DABC"),   # Latin but first-order unbalanced (cyclic)
])
def test_unbalanced_squares_are_rejected(sequences):
    with pytest.raises(rc.CampaignError):
        rc.check_williams(sequences, rc.derive_letters())


def test_formal_design_counts_and_suite_alternation():
    cfg, units = formal_units()
    assert cfg.formal and cfg.test_flags == []
    main, side = [u for u in units if u.kind == "main"], [u for u in units if u.kind == "side"]
    assert [u.sessions for u in main] == [32] * 4 and [u.sessions for u in side] == [16] * 4
    assert [[j.suite for j in u.jobs] for u in main] == [["baseline", "complex"], ["complex", "baseline"]] * 2
    for unit in main:
        for job in unit.jobs:
            assert job.scales == rc.SUITES[job.suite]["scales"]
            assert sorted(job.order) == sorted(rc.ENGINES) and job.engines == list(rc.ENGINES)
    assert rc.SUITES["baseline"]["scales"] == ["1", "10", "30", "100", "300"]
    assert rc.SUITES["complex"]["scales"] == ["30", "100", "300"]


def test_side_study_orders_and_layout(tmp_path):
    (tmp_path / "env" / "bin").mkdir(parents=True)
    cfg = rc.resolve_config(rc.parse_args(["--root", str(tmp_path), "--a100compat-env", str(tmp_path / "env")]))
    side = [u for u in rc.build_units(cfg) if u.kind == "side"]
    assert [u.label for u in side] == ["AB", "BA", "BA", "AB"]
    first = side[0]  # round 1 runs baseline first, arm A (1.44.2) before arm B within each suite
    assert [(j.suite, j.arm) for j in first.jobs] == [
        ("baseline", "polars-1.44.2"), ("baseline", "polars-2.0.0"), ("complex", "polars-1.44.2"), ("complex", "polars-2.0.0")]
    assert first.jobs[1].python == str(tmp_path / "env" / "bin" / "python")  # a venv directory is accepted
    assert first.jobs[0].python == sys.executable
    assert first.attempt_path(0) == tmp_path / "side-study" / "round-1"
    assert first.attempt_path(1) == tmp_path / "side-study" / "round-1-repeat-1"
    assert first.jobs[0].out_rel == Path("polars-1.44.2/baseline")


def test_job_commands_carry_the_fixed_formal_flags(tmp_path):
    cfg = rc.resolve_config(rc.parse_args(["--root", str(tmp_path), "--data-root", "/d", "--jar", "/j.jar"]))
    _, units = formal_units()
    baseline, complex_ = units[0].jobs
    base_cmd = rc.job_command(baseline, tmp_path / "round-1" / "baseline", cfg)
    cx_cmd = rc.job_command(complex_, tmp_path / "round-1" / "complex", cfg)
    for command in (base_cmd, cx_cmd):
        for flag, value in (("--cpus", "26"), ("--driver-memory", "96g"), ("--runs", "3"),
                            ("--spark-gpu-profile", "a100-parity"), ("--polars-gpu-executor", "streaming"),
                            ("--jar", "/j.jar"), ("--data-root", "/d")):
            assert command[command.index(flag) + 1] == value
        assert "--keep-going" in command
        assert command[command.index("--engine-order") + 1:command.index("--engine-order") + 5] == \
            ["spark_cpu", "polars_gpu", "spark_gpu", "polars"]
    assert base_cmd[base_cmd.index("--query-cleanup") + 1] == "none" and "--cleanup-timeout-seconds" not in base_cmd
    assert cx_cmd[cx_cmd.index("--query-cleanup") + 1] == "verified_gc"
    assert cx_cmd[cx_cmd.index("--cleanup-timeout-seconds") + 1] == "60"


@pytest.mark.parametrize("flags", [
    ["--rounds", "2"], ["--only-side-study"], ["--scales-override", "1"], ["--engines-override", "polars"],
    ["--cpus-override", "4"], ["--driver-memory-override", "4g"],
])
def test_every_test_flag_marks_the_campaign_informal(flags):
    cfg = rc.resolve_config(rc.parse_args(flags))
    assert cfg.formal is False and cfg.test_flags == [flags[0]]


def test_run_control_flags_keep_the_campaign_formal():
    cfg = rc.resolve_config(rc.parse_args(["--stop-after-round", "1", "--engine-timeout-seconds", "3600",
                                           "--accept-design-doc-change", "--allow-any-gpu", "--host-label", "gpu_1x_b200_sxm6"]))
    assert cfg.formal is True and cfg.allow_any_gpu and cfg.host_label == "gpu_1x_b200_sxm6"


def test_gate_command_formal_and_informal(tmp_path):
    _, units = formal_units()
    formal = rc.resolve_config(rc.parse_args(["--allow-any-gpu"]))
    command = rc.gate_command(units[1], tmp_path / "round-2", formal, tmp_path / "round-2" / "preflight",
                              prior_reports=[tmp_path / "r1.json"])
    assert command[2:6] == ["--formal", "--skip-run", "--skip-env", "--skip-inputs"]
    assert command[command.index("--round") + 1] == "2" and "--allow-any-gpu" in command
    assert command[command.index("--prior-reports") + 1] == str(tmp_path / "r1.json")
    assert "--engines" not in command  # --formal pins the locked design itself
    informal = rc.resolve_config(rc.parse_args(["--scales-override", "1", "--engines-override", "polars", "spark_cpu",
                                                "--cpus-override", "4"]))
    command = rc.gate_command(units[0], tmp_path / "round-1", informal, tmp_path / "p")
    assert "--formal" not in command and command[command.index("--engines") + 1:command.index("--engines") + 3] == ["polars", "spark_cpu"]
    assert command[command.index("--baseline-scales") + 1] == "1" and command[command.index("--expect-cpus") + 1] == "4"
    assert command[command.index("--error-policy") + 1] == "classified" and "--allow-any-gpu" not in command


def test_dry_run_prints_the_full_schedule(capsys):
    assert rc.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "TOTAL: 128 main + 64 side = 192 sessions" in out
    assert "spark_cpu -> polars_gpu -> spark_gpu -> polars" in out and "spark_gpu -> spark_cpu -> polars -> polars_gpu" in out
    assert "formal: True" in out


# ---- orchestration with stand-ins -------------------------------------------------------------------

FAKE_RUN_EXPERIMENT = textwrap.dedent('''
    import argparse, json, os, sys
    from pathlib import Path
    p = argparse.ArgumentParser()
    p.add_argument("--suite"); p.add_argument("--output", type=Path); p.add_argument("--scales", nargs="+")
    p.add_argument("--engines", nargs="+"); p.add_argument("--engine-order", nargs="+")
    args, _ = p.parse_known_args()
    control = json.loads(Path(os.environ["FAKE_CONTROL"]).read_text())
    log = Path(os.environ["FAKE_LOG"])
    with log.open("a") as handle:
        handle.write(json.dumps({"tool": "run", "output": str(args.output), "argv": sys.argv[1:]}) + "\\n")
    args.output.mkdir(parents=True, exist_ok=False)
    order = list(args.engine_order)
    if control.get("shuffle_order"):
        order = order[::-1]
    schedule = []
    for scale in args.scales:
        for engine in order:
            item = {"scale": scale, "engine": engine, "command": [], "preheat": {"bytes": 0, "seconds": 0.01},
                    "start_utc": "x", "process_wall_seconds": 1.5, "returncode": 0, "status": "complete"}
            if control.get("fail_session") == engine:
                item.update(returncode=1, status="failed")
            schedule.append(item)
            print(json.dumps({"event": "engine_started", **item}), flush=True)
            print(json.dumps({"event": "engine_complete" if item["status"] == "complete" else "engine_failed", **item}), flush=True)
            sd = args.output / ("sf" + scale) / engine
            sd.mkdir(parents=True)
            (sd / "results.json").write_text(json.dumps({"engine": engine, "version": control.get("polars_version", "9"),
                                                         "completed_at_utc": "x", "query_errors": []}))
    (args.output / "schedule.json").write_text(json.dumps(schedule))
    if control.get("hang_suite") == args.suite:
        import time; time.sleep(600)
    sys.exit(control.get("run_exit", 0))
''')

FAKE_PREFLIGHT = textwrap.dedent('''
    import argparse, json, os, sys
    from pathlib import Path
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path)
    args, _ = p.parse_known_args()
    control_path = Path(os.environ["FAKE_CONTROL"])
    control = json.loads(control_path.read_text())
    Path(os.environ["FAKE_LOG"]).open("a").write(json.dumps({"tool": "preflight", "argv": sys.argv[1:]}) + "\\n")
    verdicts = control.get("verdicts", [])
    verdict = verdicts.pop(0) if verdicts else "pass"
    control["verdicts"] = verdicts
    control_path.write_text(json.dumps(control))
    if control.get("tool_error"):
        sys.exit(2)
    args.output.mkdir(parents=True)
    checks = [{"id": "x", "status": "FAIL", "detail": "boom"}] if verdict == "fail" else []
    (args.output / "preflight-report.json").write_text(json.dumps(
        {"verdict": verdict, "ok": verdict == "pass", "counts": {"FAIL": len(checks)}, "checks": checks}))
    sys.exit(0 if verdict == "pass" else 1)
''')


class Rig:
    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rc, "running_workloads", lambda: [])  # other tests/teammates may run benchmarks
        self.monkeypatch = monkeypatch
        self.tmp = tmp_path
        self.root = tmp_path / "campaign"
        self.log = tmp_path / "fake.log"
        self.control = tmp_path / "control.json"
        (tmp_path / "run_experiment.py").write_text(FAKE_RUN_EXPERIMENT)
        (tmp_path / "preflight.py").write_text(FAKE_PREFLIGHT)
        monkeypatch.setattr(rc, "RUN_EXPERIMENT", tmp_path / "run_experiment.py")
        monkeypatch.setattr(rc, "PREFLIGHT", tmp_path / "preflight.py")
        monkeypatch.setenv("FAKE_CONTROL", str(self.control))
        monkeypatch.setenv("FAKE_LOG", str(self.log))
        data = tmp_path / "data" / "sf1"
        data.mkdir(parents=True)
        (data / "manifest.json").write_text("{}")
        self.set()
        self._handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}

    def set(self, **control):
        self.control.write_text(json.dumps(control))

    lease_flags = ["--no-lease-check"]   # a test sets this to [] (or its own flags) to exercise the lease

    def args(self, *extra):
        return ["--root", str(self.root), "--data-root", str(self.tmp / "data"), "--jar", str(self.tmp / "none.jar"),
                "--a100compat-env", sys.executable, "--scales-override", "1", "--engines-override", "polars", "spark_cpu",
                "--cpus-override", "4", "--driver-memory-override", "4g", *self.lease_flags, *extra]

    def run(self, *extra):
        argv = self.args(*extra)
        self.monkeypatch.setattr(sys, "argv", ["run_campaign.py", *argv])   # invocations.jsonl records the real command line
        return rc.main(argv)

    def calls(self, tool=None):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [r for r in rows if tool is None or r["tool"] == tool]

    def restore(self):
        for sig, handler in self._handlers.items():
            signal.signal(sig, handler)

    def gates(self, name):
        return json.loads((self.root / name / "gates.json").read_text())

    def status(self):
        return json.loads((self.root / "status.json").read_text())


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    yield r
    r.restore()


def test_tiny_campaign_runs_gates_and_side_study(rig):
    assert rig.run("--rounds", "2") == 0
    for k in (1, 2):
        assert rig.gates(f"round-{k}")["verdict"] == "pass"
        assert (rig.root / f"round-{k}" / "baseline" / "schedule.json").is_file()
        assert (rig.root / f"round-{k}" / "complex" / "schedule.json").is_file()
    assert (rig.root / "side-study" / "round-1" / "polars-1.44.2" / "baseline").is_dir()
    assert (rig.root / "side-study" / "round-2" / "polars-2.0.0" / "complex").is_dir()
    assert rig.gates("side-study/round-1")["verdict"] == "pass"
    campaign = json.loads((rig.root / "campaign.json").read_text())
    assert campaign["design"]["formal"] is False and campaign["design"]["test_flags"]
    assert len(campaign["provenance"]["design_doc_sha256"]) == 64 and campaign["provenance"]["git"]["commit"]
    status = rig.status()
    assert status["state"] == "complete" and status["sessions"]["completed"] == status["sessions"]["total"] == 16
    # the gate is called the way preflight.py is documented to be called
    argv = rig.calls("preflight")[0]["argv"]
    assert argv[:3] == ["--skip-run", "--skip-env", "--skip-inputs"]
    assert argv[argv.index("--expect-gpu-unsupported") + 1:argv.index("--expect-gpu-unsupported") + 3] == ["cx_q13", "cx_window"]
    assert argv[argv.index("--engines") + 1:argv.index("--engines") + 3] == ["polars", "spark_cpu"]
    # engine order of round 1 reached run_experiment.py as the Latin-square row (filtered to the selected engines)
    assert json.loads((rig.root / "round-1" / "attempt.json").read_text())["jobs"][0]["engine_order"] == ["spark_cpu", "polars"]
    assert json.loads((rig.root / "round-2" / "attempt.json").read_text())["jobs"][0]["engine_order"] == ["polars", "spark_cpu"]


def test_second_invocation_skips_everything_that_passed(rig):
    assert rig.run("--rounds", "2") == 0
    before = len(rig.calls())
    assert rig.run("--rounds", "2") == 0
    assert len(rig.calls()) == before  # nothing re-ran, nothing was re-gated
    assert sorted(p.name for p in rig.root.iterdir() if p.name.startswith("round")) == ["round-1", "round-2"]
    lines = (rig.root / "invocations.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_failed_gate_repeats_the_round_once_in_a_new_directory(rig):
    rig.set(verdicts=["fail", "pass", "pass", "pass"])
    assert rig.run("--rounds", "1") == 0
    assert rig.gates("round-1")["verdict"] == "fail"
    assert rig.gates("round-1-repeat-1")["verdict"] == "pass"
    assert (rig.root / "round-1" / "baseline").is_dir()  # the failed attempt is kept, never overwritten
    assert rig.gates("round-1")["preflight"]["failing_checks"][0]["id"] == "x"
    assert rig.status()["rounds"]["main-r1"]["accepted_dir"].endswith("round-1-repeat-1")


def test_repeat_failing_too_stops_the_campaign_and_stays_stopped(rig):
    rig.set(verdicts=["fail", "fail"])
    assert rig.run("--rounds", "2") == rc.EXIT_STOPPED
    assert rig.status()["state"] == "stopped"
    assert not (rig.root / "round-2").exists() and not (rig.root / "round-1-repeat-2").exists()
    runs = len(rig.calls("run"))
    assert rig.run("--rounds", "2") == rc.EXIT_STOPPED  # resume does not retry a round that failed twice
    assert len(rig.calls("run")) == runs


def test_gate_tool_error_does_not_count_as_a_failed_round(rig):
    rig.set(tool_error=True)
    assert rig.run("--rounds", "1") == rc.EXIT_GATE_TOOL
    assert not (rig.root / "round-1" / "gates.json").exists()
    runs = len(rig.calls("run"))
    rig.set()
    assert rig.run("--rounds", "1") == 0  # same run directories, gated now
    assert len(rig.calls("run")) == runs + 2 * 2  # only the side study ran (2 suites x 2 arms)
    assert rig.gates("round-1")["verdict"] == "pass" and not (rig.root / "round-1-repeat-1").exists()


def test_interrupted_run_is_kept_as_evidence_and_redone(rig):
    assert rig.run("--rounds", "2", "--stop-after-round", "1") == 0
    # simulate a campaign killed while 'complex' was running in round 2 of a later invocation
    attempt = rig.root / "round-2"
    (attempt / "baseline").mkdir(parents=True)
    (attempt / "complex").mkdir()
    (attempt / "baseline" / "partial.txt").write_text("evidence")
    meta = {"unit": "main-r2", "round": 2, "attempt": 0, "jobs": [
        {"key": "complex", "suite": "complex", "out_rel": "complex", "scales": ["1"], "engines": ["polars", "spark_cpu"],
         "state": "running", "command": []},
        {"key": "baseline", "suite": "baseline", "out_rel": "baseline", "scales": ["1"], "engines": ["polars", "spark_cpu"],
         "state": "pending", "command": []}]}
    (attempt / "attempt.json").write_text(json.dumps(meta))
    assert rig.run("--rounds", "2") == 0
    assert (attempt / "abandoned.json").is_file() and (attempt / "baseline" / "partial.txt").read_text() == "evidence"
    assert rig.gates("round-2-repeat-1")["verdict"] == "pass"
    assert not (rig.root / "round-2" / "gates.json").exists()


def test_finished_but_ungated_round_is_only_gated(rig):
    rig.set(tool_error=True)
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == rc.EXIT_GATE_TOOL
    rig.set()
    runs = len(rig.calls("run"))
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    assert len(rig.calls("run")) == runs and rig.gates("round-1")["verdict"] == "pass"


def test_resume_finishes_a_round_whose_remaining_suite_never_started(rig):
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    # drop the second suite and the verdict, as if the campaign died between the two suites
    import shutil
    second = rig.root / "round-1" / "complex"
    shutil.rmtree(second)
    (rig.root / "round-1" / "gates.json").unlink()
    meta = json.loads((rig.root / "round-1" / "attempt.json").read_text())
    meta["jobs"][1].update(state="pending")
    (rig.root / "round-1" / "attempt.json").write_text(json.dumps(meta))
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    assert second.is_dir() and not (rig.root / "round-1-repeat-1").exists()
    assert rig.gates("round-1")["verdict"] == "pass"


def test_wrong_engine_order_is_an_integrity_failure(rig):
    rig.set(shuffle_order=True, verdicts=["pass", "pass"])
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == rc.EXIT_STOPPED  # fails, repeats, fails again
    gates = rig.gates("round-1")
    assert gates["verdict"] == "fail" and gates["design_conformance"]["problems"]
    assert "engine order" in gates["design_conformance"]["problems"][0]


def test_run_experiment_failure_fails_the_gate(rig):
    rig.set(run_exit=1)
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == rc.EXIT_STOPPED
    assert "run_experiment.py exited non-zero" in " ".join(rig.gates("round-1")["reasons"])


def test_stop_after_round_pauses_and_skips_the_side_study(rig):
    assert rig.run("--rounds", "3", "--stop-after-round", "1") == 0
    assert rig.status()["state"] == "paused"
    assert (rig.root / "round-1").is_dir() and not (rig.root / "round-2").exists()
    assert not (rig.root / "side-study").exists()
    assert rig.run("--rounds", "3", "--stop-after-round", "2") == 0 and (rig.root / "round-2").is_dir()


def test_only_side_study(rig):
    assert rig.run("--only-side-study", "--rounds", "1") == 0
    assert not (rig.root / "round-1").exists() and (rig.root / "side-study" / "round-1").is_dir()


def test_a_changed_design_is_refused_on_resume(rig, capsys):
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    args = rig.args("--rounds", "1")
    args[args.index("--cpus-override") + 1] = "8"
    assert rc.main(args) == rc.EXIT_DESIGN
    assert "different resolved design" in capsys.readouterr().err


def test_changed_design_document_needs_explicit_acceptance(rig, monkeypatch, tmp_path, capsys):
    doc = tmp_path / "design.md"
    doc.write_text("locked v1")
    monkeypatch.setattr(rc, "DESIGN_DOC", doc)
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    doc.write_text("locked v1 + amendment A1")
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == rc.EXIT_DESIGN
    assert "--accept-design-doc-change" in capsys.readouterr().err
    assert rig.run("--rounds", "1", "--stop-after-round", "1", "--accept-design-doc-change") == 0
    last = json.loads((rig.root / "invocations.jsonl").read_text().splitlines()[-1])
    assert "design_doc_sha256 (accepted)" in last["drift"]


def test_changed_input_manifest_is_refused(rig, tmp_path, capsys):
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    (tmp_path / "data" / "sf1" / "manifest.json").write_text('{"regenerated": true}')
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == rc.EXIT_DESIGN
    assert "manifests changed" in capsys.readouterr().err


def test_second_campaign_process_is_locked_out(rig, capsys):
    import fcntl
    rig.root.mkdir(parents=True)
    with (rig.root / ".campaign.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert rig.run("--rounds", "1") == 1
    assert "already holds" in capsys.readouterr().err


def test_formal_campaign_enforces_registered_polars_versions(tmp_path, monkeypatch, capsys):
    """A formal run in an environment with the wrong Polars must refuse to start."""
    env = {"python": "3.11.9", "polars": "2.0.0", "executable": "x", "cudf_polars_importable_spec": True, "spark_submit": True}
    monkeypatch.setattr(rc, "probe_python", lambda python: dict(env))
    rig_ = Rig(tmp_path, monkeypatch)
    try:
        data = tmp_path / "data"
        for scale in rc.SUITES["baseline"]["scales"]:
            (data / f"sf{scale}").mkdir(exist_ok=True)
            (data / f"sf{scale}" / "manifest.json").write_text("{}")
        (tmp_path / "rapids.jar").write_bytes(b"")
        code = rc.main(["--root", str(rig_.root), "--data-root", str(data), "--jar", str(tmp_path / "rapids.jar"),
                        "--a100compat-env", sys.executable, "--lease-name", valid_lease()])
        assert code == rc.EXIT_DESIGN and "design section 2 says 1.44.2" in capsys.readouterr().err
        assert not (rig_.root / "campaign.json").exists()
        env["polars"] = rc.MAIN_POLARS_VERSION  # main env right, side-study env (also this probe) wrong
        code = rc.main(["--root", str(rig_.root), "--data-root", str(data), "--jar", str(tmp_path / "rapids.jar"),
                        "--a100compat-env", sys.executable, "--lease-name", valid_lease()])
        assert code == rc.EXIT_DESIGN and "design section 10 says 2.0.0" in capsys.readouterr().err
        env.update(polars="2.0.0", cudf_polars_importable_spec=False)
        code = rc.main(["--root", str(rig_.root), "--data-root", str(data), "--jar", str(tmp_path / "rapids.jar"),
                        "--a100compat-env", sys.executable, "--lease-name", valid_lease()])
        assert code == rc.EXIT_DESIGN and "cudf_polars is not installed" in capsys.readouterr().err
    finally:
        rig_.restore()


def test_status_has_live_fields_and_eta_after_history(rig):
    assert rig.run("--rounds", "1") == 0
    status = rig.status()
    assert {"state", "current", "sessions", "rounds", "eta", "started_utc", "updated_utc", "pid"} <= set(status)
    assert status["rounds"]["main-r1"]["sessions"] == 4 and status["rounds"]["side-r1"]["sessions"] == 4


def test_sigterm_stops_the_suite_and_resume_repeats_the_round(rig):
    import threading
    import time

    def terminate_once_a_suite_is_running():
        deadline = time.time() + 60
        while time.time() < deadline and not rig.calls("run"):
            time.sleep(0.05)
        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGTERM)

    rig.set(hang_suite="baseline")
    threading.Thread(target=terminate_once_a_suite_is_running, daemon=True).start()
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == rc.EXIT_INTERRUPTED
    assert rig.status()["state"] == "interrupted" and not (rig.root / "round-1" / "gates.json").exists()
    assert json.loads((rig.root / "round-1" / "attempt.json").read_text())["jobs"][0]["state"] == "interrupted"
    rig.set()
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    assert (rig.root / "round-1" / "abandoned.json").is_file()
    assert rig.gates("round-1-repeat-1")["verdict"] == "pass"


def test_formal_campaign_refuses_to_start_next_to_other_benchmark_processes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rc, "running_workloads", lambda: ["4242: python worker.py --engine spark_cpu"])
    monkeypatch.setattr(rc, "probe_python", lambda python: {"python": "3.11", "polars": "1.44.2", "executable": "x",
                                                            "cudf_polars_importable_spec": True, "spark_submit": True})
    data = tmp_path / "data"
    for scale in rc.SUITES["baseline"]["scales"]:
        (data / f"sf{scale}").mkdir(parents=True)
        (data / f"sf{scale}" / "manifest.json").write_text("{}")
    (tmp_path / "rapids.jar").write_bytes(b"")
    code = rc.main(["--root", str(tmp_path / "c"), "--data-root", str(data), "--jar", str(tmp_path / "rapids.jar"),
                    "--a100compat-env", sys.executable, "--lease-name", valid_lease()])
    assert code == rc.EXIT_DESIGN and "4242" in capsys.readouterr().err
    assert not (tmp_path / "c" / "campaign.json").exists()


def test_running_workloads_detects_a_real_process(tmp_path):
    import subprocess
    import time
    script = tmp_path / "worker.py"
    script.write_text("import time; time.sleep(30)")
    process = subprocess.Popen([sys.executable, str(script)])
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not any(str(process.pid) in line for line in rc.running_workloads()):
            time.sleep(0.1)
        assert any(str(process.pid) in line for line in rc.running_workloads())
    finally:
        process.kill()
        process.wait()


# ---- the lease: no valid lease, no campaign -------------------------------------------------------------

def read_campaign(rig):
    return json.loads((rig.root / "campaign.json").read_text())


def test_a_campaign_without_a_lease_does_not_start_and_creates_nothing(rig, capsys, monkeypatch):
    monkeypatch.setattr(rc, "LEASE_FILE", str(rig.tmp / "no-lease-file"))
    rig.lease_flags = []
    assert rig.run("--rounds", "1") == rc.EXIT_LEASE == 6
    err = capsys.readouterr().err
    assert "no lease" in err and "--lease-name" in err and str(rig.tmp / "no-lease-file") in err and "--no-lease-check" in err
    assert not rig.root.exists() and rig.calls() == []


@pytest.mark.parametrize("name, problem", [
    ("h100-box-1", "does not match"),
    ("p3-main-owner-ab12-expires-20250101T0000Z", "expired"),
    ("p3-main-owner-ab12-expires-2026111T1807Z", "does not match"),
    ("P3-main-owner-ab12-expires-29991231T2359Z", "does not match"),
    ("p3-main-owner-ab12-expires-20261399T9999Z", "not a valid UTC time"),
])
def test_an_invalid_or_expired_lease_does_not_start(rig, capsys, name, problem):
    rig.lease_flags = ["--lease-name", name]
    assert rig.run("--rounds", "1") == rc.EXIT_LEASE
    err = capsys.readouterr().err
    assert problem in err and "is not valid" in err
    assert not rig.root.exists() and rig.calls() == []


def test_a_lease_with_too_little_time_left_is_refused_when_a_minimum_is_asked_for(rig, capsys):
    rig.lease_flags = ["--lease-name", valid_lease(minutes=20), "--lease-min-minutes", "45"]
    assert rig.run("--rounds", "1") == rc.EXIT_LEASE
    assert re.search(r"less than 45(\.0)? minutes left", capsys.readouterr().err) and not rig.root.exists()
    rig.lease_flags = ["--lease-name", valid_lease(minutes=90), "--lease-min-minutes", "45"]
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0


def test_a_valid_lease_name_starts_the_campaign_and_is_recorded_without_the_owner(rig):
    name = valid_lease(minutes=240)
    rig.lease_flags = ["--lease-name", name]
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    stored = read_campaign(rig)
    lease = stored["lease"]
    assert lease["checked"] is True and lease["source"] == "--lease-name" and lease["purpose"] == "test"
    assert 230 < lease["minutes_left_at_start"] <= 240 and lease["expires_utc"].endswith("Z")
    assert lease["name_sha256"] == hashlib.sha256(name.encode()).hexdigest()
    text = (rig.root / "campaign.json").read_text() + (rig.root / "invocations.jsonl").read_text()
    assert "ab12" not in text.replace(lease["name_sha256"], "") and name not in text     # the owner id is not published
    invocation = json.loads((rig.root / "invocations.jsonl").read_text().splitlines()[0])
    assert invocation["lease"] == lease
    assert invocation["argv"][invocation["argv"].index("--lease-name") + 1] == "sha256:" + lease["name_sha256"]
    assert stored["design"]["formal"] is False                                            # (the rig uses test overrides anyway)
    assert "--no-lease-check" not in stored["design"]["test_flags"]


def test_the_lease_can_come_from_the_file_the_launcher_wrote(rig, monkeypatch):
    lease_file = rig.tmp / "paper3-lease"
    lease_file.write_text(valid_lease(minutes=120) + "\nignored second line\n")
    monkeypatch.setattr(rc, "LEASE_FILE", str(lease_file))
    rig.lease_flags = []
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    assert read_campaign(rig)["lease"]["source"] == str(lease_file)
    explicit = rig.tmp / "second"
    rig.root = explicit
    rig.lease_flags = ["--lease-name", valid_lease(minutes=120)]
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    assert read_campaign(rig)["lease"]["source"] == "--lease-name"                     # the flag wins over the file


@pytest.mark.parametrize("argv, expected", [
    (["run_campaign.py", "--lease-name", "p3-a-owner-zz9-expires-20261111T1807Z", "--root", "r"],
     ["run_campaign.py", "--lease-name", "sha256:" + hashlib.sha256(b"p3-a-owner-zz9-expires-20261111T1807Z").hexdigest(), "--root", "r"]),
    (["--lease-name=p3-a-owner-zz9-expires-20261111T1807Z"],
     ["--lease-name=sha256:" + hashlib.sha256(b"p3-a-owner-zz9-expires-20261111T1807Z").hexdigest()]),
    (["--lease-nam", "p3-a-owner-zz9-expires-20261111T1807Z"],
     ["--lease-nam", "sha256:" + hashlib.sha256(b"p3-a-owner-zz9-expires-20261111T1807Z").hexdigest()]),
    (["--lease-min-minutes", "30", "--lease-name"], ["--lease-min-minutes", "30", "--lease-name"]),
    (["--root", "x", "--host-label", "gpu_1x_h100_sxm5"], ["--root", "x", "--host-label", "gpu_1x_h100_sxm5"]),
])
def test_the_lease_name_never_reaches_the_published_command_line(argv, expected):
    assert rc.logged_argv(argv) == expected


def test_an_empty_or_unreadable_lease_file_is_no_lease(rig, monkeypatch, capsys):
    (rig.tmp / "paper3-lease").write_text("\n")
    monkeypatch.setattr(rc, "LEASE_FILE", str(rig.tmp / "paper3-lease"))
    rig.lease_flags = []
    assert rig.run("--rounds", "1") == rc.EXIT_LEASE and "no lease" in capsys.readouterr().err


def test_no_lease_check_is_recorded_and_makes_the_campaign_informal(rig):
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    stored = read_campaign(rig)
    assert stored["lease"] == {"checked": False, "reason": "--no-lease-check (local tests only)"}
    assert stored["design"]["formal"] is False and "--no-lease-check" in stored["design"]["test_flags"]
    cfg = rc.resolve_config(rc.parse_args(["--no-lease-check"]))
    assert cfg.formal is False and cfg.test_flags == ["--no-lease-check"]
    assert json.loads((rig.root / "invocations.jsonl").read_text().splitlines()[0])["lease"]["checked"] is False


def test_a_formal_campaign_with_a_lease_stays_formal():
    cfg = rc.resolve_config(rc.parse_args(["--lease-name", valid_lease(), "--lease-min-minutes", "60"]))
    assert cfg.formal is True and cfg.test_flags == [] and cfg.lease_min_minutes == 60


def test_a_dry_run_needs_no_lease(capsys, monkeypatch):
    monkeypatch.setattr(rc, "LEASE_FILE", "/nonexistent/paper3-lease")
    assert rc.main(["--dry-run"]) == 0


def test_resuming_after_the_lease_expired_is_refused(rig, capsys, monkeypatch):
    rig.lease_flags = ["--lease-name", valid_lease(minutes=30)]
    assert rig.run("--rounds", "2", "--stop-after-round", "1") == 0
    real = rc.lease_status
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    monkeypatch.setattr(rc, "lease_status", lambda name, min_minutes=0: real(name, now=later, min_minutes=min_minutes))
    calls_before = len(rig.calls())
    assert rig.run("--rounds", "2") == rc.EXIT_LEASE
    assert "expired" in capsys.readouterr().err and len(rig.calls()) == calls_before


def test_the_lease_decision_does_not_change_the_design_hash_of_a_leased_campaign():
    plain = rc.resolve_config(rc.parse_args(["--lease-name", valid_lease(minutes=60)]))
    other = rc.resolve_config(rc.parse_args(["--lease-name", valid_lease(minutes=600), "--lease-min-minutes", "5"]))
    units = rc.build_units(plain)
    assert rc.design_dict(plain, units) == rc.design_dict(other, rc.build_units(other))
    assert "pinning" not in rc.design_dict(plain, units)


# ---- CPU pinning and the CPU-energy rule ----------------------------------------------------------------

def test_pinning_flags_reach_every_job_only_when_given(c3, tmp_path):
    plain = rc.resolve_config(rc.parse_args(["--root", str(tmp_path), "--data-root", "/d", "--jar", "/j.jar"]))
    pinned = rc.resolve_config(rc.parse_args(["--root", str(tmp_path), "--data-root", "/d", "--jar", "/j.jar", "--socket", "1"]))
    listed = rc.resolve_config(rc.parse_args(["--root", str(tmp_path), "--data-root", "/d", "--jar", "/j.jar", "--cpu-list", "0-25"]))
    job = rc.build_units(plain)[0].jobs[0]
    base = rc.job_command(job, tmp_path / "x", plain)
    assert "--socket" not in base and "--cpu-list" not in base
    command = rc.job_command(job, tmp_path / "x", pinned)
    assert command[:len(base)] == base and command[command.index("--socket") + 1] == "1" and "--cpu-list" not in command
    command = rc.job_command(job, tmp_path / "x", listed)
    assert command[command.index("--cpu-list") + 1] == "0-25" and "--socket" not in command
    with pytest.raises(SystemExit):
        rc.parse_args(["--socket", "0", "--cpu-list", "0-3"])


def test_the_pinning_request_is_part_of_the_design_only_when_given(c3):
    plain = rc.resolve_config(rc.parse_args([]))
    pinned = rc.resolve_config(rc.parse_args(["--socket", "0"]))
    assert "pinning" not in rc.design_dict(plain, rc.build_units(plain))
    assert rc.design_dict(pinned, rc.build_units(pinned))["pinning"] == {"socket": 0, "cpu_list": None, "membind": True}
    unbound = rc.resolve_config(rc.parse_args(["--socket", "0", "--no-membind"]))
    assert rc.design_dict(unbound, rc.build_units(unbound))["pinning"] == {"socket": 0, "cpu_list": None, "membind": False}


def test_a_pinned_campaign_records_the_resolved_cpus_and_expects_them_from_every_session(rig, c3):
    assert rig.run("--rounds", "1", "--stop-after-round", "1", "--socket", "1", "--cpus-override", "26") == 0
    stored = read_campaign(rig)
    assert stored["pinning"]["cpus"] == C3_SOCKET_1 and stored["pinning"]["requested"] == {"socket": 1, "cpu_list": None}
    assert stored["pinning"]["sockets"] == [1] and stored["pinning"]["numa_nodes"] == [2, 3]
    assert stored["design"]["pinning"] == {"socket": 1, "cpu_list": None, "membind": True}
    assert json.loads((rig.root / "invocations.jsonl").read_text().splitlines()[0])["pinning"]["cpus"] == C3_SOCKET_1
    runs = rig.calls("run")
    assert runs and all(r["argv"][r["argv"].index("--socket") + 1] == "1" for r in runs)
    gates = rig.calls("preflight")
    assert gates and all(g["argv"][g["argv"].index("--expect-affinity") + 1] == C3_SOCKET_1 for g in gates)


def test_an_unpinned_campaign_asks_nothing_about_affinity(rig, c3):
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    assert "pinning" not in read_campaign(rig)
    assert all("--expect-affinity" not in g["argv"] and "--socket" not in g["argv"] for g in rig.calls("preflight"))
    assert all("--socket" not in r["argv"] and "--cpu-list" not in r["argv"] for r in rig.calls("run"))


@pytest.mark.parametrize("flags, message", [
    (["--socket", "7"], "no such socket"),
    (["--cpu-list", "0-3", "--cpus-override", "26"], "oversubscribed"),
    (["--cpu-list", "500-510"], "not online"),
])
def test_an_impossible_pinning_does_not_start_the_campaign(rig, c3, capsys, flags, message):
    assert rig.run("--rounds", "1", *flags) == rc.EXIT_DESIGN
    assert message in capsys.readouterr().err and not (rig.root / "campaign.json").exists() and rig.calls() == []


def test_a_campaign_cannot_resume_on_different_cpus(rig, c3, capsys):
    assert rig.run("--rounds", "1", "--stop-after-round", "1", "--socket", "0", "--cpus-override", "26") == 0
    import shutil
    shutil.rmtree(c3.cpu_root / "cpu143" / "topology")                       # one CPU of socket 0 went offline
    assert rig.run("--rounds", "1", "--stop-after-round", "1", "--socket", "0", "--cpus-override", "26") == rc.EXIT_DESIGN
    err = capsys.readouterr().err
    assert "must not change" in err and "0-47,96-142" in err
    assert "different resolved design" not in err


def test_rapl_that_exists_but_is_unreadable_refuses_the_campaign(rig, capsys, monkeypatch):
    tree = c3_powercap(rig.tmp / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    deny(monkeypatch, *(tree.glob("intel-rapl*/energy_uj")))
    assert rig.run("--rounds", "1") == rc.EXIT_DESIGN
    err = capsys.readouterr().err
    assert "CPU energy" in err and "./setup_h100.sh --power-only --enable-rapl-read" in err and "--cpu-energy off" in err
    assert not (rig.root / "campaign.json").exists() and rig.calls() == []


@pytest.mark.parametrize("flags", [["--cpu-energy", "off"], ["--power-telemetry", "off"]])
def test_the_explicit_opt_outs_start_on_such_a_host(rig, monkeypatch, flags):
    tree = c3_powercap(rig.tmp / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    deny(monkeypatch, *(tree.glob("intel-rapl*/energy_uj")))
    assert rig.run("--rounds", "1", "--stop-after-round", "1", *flags) == 0
    assert list(read_campaign(rig)["telemetry"].values()) == [flags[1]]


def test_require_refuses_a_host_without_cpu_energy_and_is_passed_to_the_runner_otherwise(rig, capsys, monkeypatch):
    assert rig.run("--rounds", "1", "--cpu-energy", "require") == rc.EXIT_DESIGN
    assert "--cpu-energy require" in capsys.readouterr().err and rig.calls() == []
    tree = c3_powercap(rig.tmp / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    real = pt.check_advancing
    monkeypatch.setattr(pt, "check_advancing", lambda zones, seconds, sleep=None: real(zones, seconds, ticking_sleep(tree)))
    monkeypatch.setattr(pt.shutil, "which", lambda name: None)
    assert rig.run("--rounds", "1", "--stop-after-round", "1", "--cpu-energy", "require") == 0
    assert read_campaign(rig)["telemetry"] == {"cpu_energy": "require"}
    runs = rig.calls("run")
    assert runs and all(r["argv"][r["argv"].index("--cpu-energy") + 1] == "require" for r in runs)


def test_a_host_without_rapl_warns_and_starts_in_auto(rig, capsys):
    assert rig.run("--rounds", "1", "--stop-after-round", "1") == 0
    assert "no CPU energy counter" in capsys.readouterr().out


# ---- NUMA memory binding in socket mode ------------------------------------------------------------------

def test_a_pinned_campaign_records_the_numa_nodes_and_the_binding_policy(rig, c3):
    assert rig.run("--rounds", "1", "--stop-after-round", "1", "--socket", "1", "--cpus-override", "26") == 0
    membind = read_campaign(rig)["pinning"]["membind"]
    assert membind["enabled"] is True and membind["policy"] == "MPOL_BIND" and membind["nodes"] == [2, 3]
    assert membind["probe"] == {"ok": True, "mechanism": "fake set_mempolicy", "numactl": None, "libnuma": None}
    assert c3.membind.probes == [[2, 3]]                                     # asked the host before the first session
    assert all("--no-membind" not in r["argv"] for r in rig.calls("run"))


def test_a_host_that_cannot_bind_memory_does_not_start_a_pinned_campaign(rig, c3, capsys):
    c3.membind.fail = "Operation not permitted"
    assert rig.run("--rounds", "1", "--socket", "0", "--cpus-override", "26") == rc.EXIT_DESIGN
    err = capsys.readouterr().err
    assert "memory cannot be bound to NUMA node(s) [0, 1]" in err and "Operation not permitted" in err
    assert "apt install numactl" in err and "--no-membind" in err
    assert not (rig.root / "campaign.json").exists() and rig.calls() == []


def test_no_membind_is_the_recorded_way_to_run_pinned_without_binding(rig, c3, capsys):
    c3.membind.fail = "Operation not permitted"
    assert rig.run("--rounds", "1", "--stop-after-round", "1", "--socket", "0", "--no-membind", "--cpus-override", "26") == 0
    stored = read_campaign(rig)
    assert stored["design"]["pinning"] == {"socket": 0, "cpu_list": None, "membind": False}
    membind = stored["pinning"]["membind"]
    assert membind["enabled"] is False and membind["policy"] == "default" and membind["nodes"] == [0, 1]
    assert "--no-membind" in membind["reason"] and c3.membind.probes == []
    assert "WARNING: memory is not bound" in capsys.readouterr().out
    runs = rig.calls("run")
    assert runs and all("--no-membind" in r["argv"] for r in runs)


def test_a_pinned_resume_cannot_flip_the_binding(rig, c3, capsys):
    assert rig.run("--rounds", "1", "--stop-after-round", "1", "--socket", "0", "--cpus-override", "26") == 0
    assert rig.run("--rounds", "1", "--stop-after-round", "1", "--socket", "0", "--no-membind", "--cpus-override", "26") == rc.EXIT_DESIGN
    assert "different resolved design" in capsys.readouterr().err


def test_no_membind_without_a_pinning_is_a_usage_error():
    with pytest.raises(SystemExit):
        rc.parse_args(["--no-membind"])
    cfg = rc.resolve_config(rc.parse_args(["--cpu-list", "0-3", "--no-membind"]))
    assert cfg.no_membind is True


def test_no_numa_nodes_means_nothing_to_bind_and_nothing_to_probe(rig, c3):
    import shutil
    shutil.rmtree(c3.node_root)
    assert rig.run("--rounds", "1", "--stop-after-round", "1", "--socket", "0", "--cpus-override", "26") == 0
    membind = read_campaign(rig)["pinning"]["membind"]
    assert membind["enabled"] is False and "no NUMA node" in membind["reason"] and c3.membind.probes == []
