#!/usr/bin/env python3
"""Hold only a known campaign scheduler, drain its child, run separate contention.

No original journal, experimental source, session record or formal profile log is
edited. A detached guardian recovers the same scheduler after manager death,
but fails closed while any foreign benchmark/GPU client remains. Linux only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

import psutil

CAMPAIGN_SHA = "3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641"
PROFILE_SHA = "a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69"
SOURCE_COMMIT = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
LAUNCH_GATE = "import os,sys\nif sys.stdin.buffer.read(1)!=b'G':raise SystemExit(99)\nos.execv(sys.argv[1],sys.argv[1:])\n"


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_bytes())


def atomic_json(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def event(output, kind, **fields):
    with (Path(output) / "EVENTS.jsonl").open("a") as handle:
        handle.write(json.dumps({"at_utc": utcnow(), "event": kind, **fields}, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def proc_identity(pid):
    p = psutil.Process(pid)
    return {"pid": p.pid, "created": p.create_time(), "cmdline": p.cmdline(),
            "cwd": p.cwd(), "ppid": p.ppid(), "pgid": os.getpgid(p.pid), "status": p.status()}


def same_process(identity, require_stopped=False, *, strict=True):
    try:
        p = psutil.Process(identity["pid"])
        if abs(p.create_time() - identity["created"]) > .01:
            return False
        if p.status() == psutil.STATUS_ZOMBIE:
            return False
        if strict and (p.cmdline() != identity["cmdline"] or p.cwd() != identity["cwd"]
                       or p.ppid() != identity["ppid"] or os.getpgid(p.pid) != identity["pgid"]):
            return False
        return not require_stopped or p.status() == psutil.STATUS_STOPPED
    except (psutil.Error, ProcessLookupError):
        return False


def stop_scheduler(identity):
    if identity["pid"] <= 1 or not same_process(identity):
        raise ValueError("Scheduler identity changed before PID-only stop")
    os.kill(identity["pid"], signal.SIGSTOP)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if same_process(identity, require_stopped=True):
            return
        time.sleep(.05)
    raise RuntimeError("Scheduler did not enter verified stopped state")


def resume_scheduler(identity):
    if identity["pid"] <= 1 or not same_process(identity, require_stopped=True):
        raise ValueError("Same owned stopped scheduler is required for PID-only resume")
    os.kill(identity["pid"], signal.SIGCONT)


def proc_stat_exit(path):
    """Linux proc_pid_stat fields 3 and 52; comm may contain spaces/parentheses."""
    text = Path(path).read_text()
    fields = text[text.rfind(")") + 2:].split()
    if len(fields) < 50 or fields[0] != "Z":
        raise ValueError("Original runner is not a retained zombie with an exit record")
    wait_status = int(fields[49])
    return {"state": fields[0], "wait_status": wait_status,
            "exit_code": os.waitstatus_to_exitcode(wait_status)}


def argument(command, name):
    if command.count(name) != 1 or command.index(name) + 1 >= len(command):
        raise ValueError(f"Expected exactly one {name} argument")
    return command[command.index(name) + 1]


def held_lock(path, scheduler_pid):
    """A nonmutating LOCK_NB probe; never truncate or replace the live lock."""
    if read_json(path).get("pid") != scheduler_pid:
        raise ValueError("Lock receipt belongs to a different scheduler")
    with Path(path).open("r") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    raise ValueError("Scheduler no longer holds its original campaign lock")


def check_scheduler_snapshot(identity, campaign, source, supervisor_pid, expected_hash=CAMPAIGN_SHA):
    campaign, source = Path(campaign).resolve(), Path(source).resolve()
    if sha(campaign) != expected_hash or not same_process(identity):
        raise ValueError("Campaign or live scheduler identity differs")
    command = identity["cmdline"]
    scripts = [v for v in command if Path(v).name == "research_campaign.py"]
    if (len(scripts) != 1 or Path(identity["cwd"]).resolve() != source
            or Path(argument(command, "--run")).resolve() != campaign
            or argument(command, "--max-sessions") != "40"
            or any(v in command for v in ("--retry-failed", "--recover-interrupted"))
            or identity["ppid"] != supervisor_pid):
        raise ValueError("Not the exact existing fixed-block C84 scheduler")
    parent = proc_identity(supervisor_pid)
    if not any(Path(v).name == "run_h100_gclocker100.sh" for v in parent["cmdline"]):
        raise ValueError("Scheduler is not owned by the original recovery supervisor")
    plan = read_json(campaign)
    if plan["source"]["git_head"] != SOURCE_COMMIT:
        raise ValueError("Frozen experimental source differs")
    journal_path = Path(plan["output_root"]) / "journal.json"
    journal = read_json(journal_path)
    if journal.get("campaign_sha256") != expected_hash:
        raise ValueError("Current journal campaign differs")
    active = [(name, item) for name, item in journal["sessions"].items() if item["status"] == "running"]
    if len(active) != 1 or len(active[0][1]["attempts"]) != 1:
        raise ValueError("Exactly one original first-attempt session must be running")
    session_id, item = active[0]
    attempt = item["attempts"][0]
    runner = proc_identity(attempt["pid"])
    if (abs(runner["created"] - attempt["pid_create_time"]) > .01
            or runner["ppid"] != identity["pid"] or runner["pgid"] != runner["pid"]
            or not any(Path(v).name == "run_experiment.py" for v in runner["cmdline"])
            or "-c" in runner["cmdline"][:3]
            or Path(argument(runner["cmdline"], "--output")).resolve() != Path(attempt["run_root"]).resolve()):
        raise ValueError("Current runner ownership/launch boundary is uncertain")
    locks = [Path(tempfile.gettempdir()) / "gpu-spark-polars-study-research.lock",
             Path(plan["output_root"]) / "campaign.lock"]
    for lock in locks:
        held_lock(lock, identity["pid"])
    return {"plan": plan, "journal_path": str(journal_path), "journal_sha256": sha(journal_path),
            "session_id": session_id, "attempt": attempt, "runner": runner, "supervisor": parent,
            "lock_paths": [str(p) for p in locks]}


def original_drained(snapshot):
    attempt, runner = snapshot["attempt"], snapshot["runner"]
    try:
        p = psutil.Process(runner["pid"])
        if abs(p.create_time() - runner["created"]) > .01:
            raise ValueError("Original runner PID reused")
        if p.status() != psutil.STATUS_ZOMBIE:
            return None
        code = proc_stat_exit(Path("/proc") / str(runner["pid"]) / "stat")
    except psutil.NoSuchProcess:
        raise ValueError("Original runner disappeared without its held parent's reaping evidence")
    if code["exit_code"] != 0:
        raise ValueError("Original runner failed; preserve it and resume normal failure handling")
    run = Path(attempt["run_root"])
    schedule = read_json(run / "schedule.json")
    if len(schedule) != 1 or schedule[0].get("status") != "complete" or schedule[0].get("returncode") != 0:
        raise ValueError("Original runner has no completed schedule")
    result = run / ("sf" + schedule[0]["scale"]) / schedule[0]["engine"] / "results.json"
    value = read_json(result)
    if not value.get("completed_at_utc") or value.get("failure"):
        raise ValueError("Original worker has no complete result")
    return {"runner_exit": code, "schedule_path": str(run / "schedule.json"),
            "schedule_sha256": sha(run / "schedule.json"), "results_path": str(result),
            "results_sha256": sha(result), "completed_at_utc": utcnow()}


def attempt_inventory(snapshot):
    """Hash the finished original attempt only after its runner has exited."""
    root = Path(snapshot["attempt"]["attempt_root"])
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Original attempt inventory contains a symlink")
        if path.is_file():
            files[str(path.relative_to(root))] = {"bytes": path.stat().st_size, "sha256": sha(path)}
        elif not path.is_dir():
            raise ValueError("Original attempt inventory contains a special file")
    if not files:
        raise ValueError("Original attempt inventory is empty")
    return files


def profile_check(config, label):
    # Imported lazily so ordinary local mechanism tests never invoke VM probes.
    try:
        from . import jvm_execution_profile as jvm
    except ImportError:
        import jvm_execution_profile as jvm
    if sha(config["campaign"]) != CAMPAIGN_SHA or sha(config["profile"]) != PROFILE_SHA:
        raise ValueError("Original frozen campaign/profile bytes changed")
    profile = read_json(config["profile"])
    idle = jvm.check_local(profile, Path(config["source"]), original=True)
    module = jvm.frozen_module(Path(config["source"]))
    plan = read_json(config["campaign"])
    module.check_frozen_inputs(plan, include_jar=True)
    module.check_frozen_runtimes(plan)
    byte_gate = module.verify_dataset_bytes(plan)
    # Recheck idle after the untimed full-byte gate, still before any engine launch.
    idle_after = module.observe_exclusive_host()
    if not idle_after["eligible"]:
        raise ValueError("Foreign benchmark appeared during interlude verification")
    return {"schema_version": "h100-contention-interlude-profile-check-v1", "label": label,
            "status": "PASS", "completed_at_utc": utcnow(), "campaign_sha256": CAMPAIGN_SHA,
            "execution_profile_sha256": PROFILE_SHA, "exclusive_host_before": idle,
            "exclusive_host_after": idle_after, "input_integrity": byte_gate}


def remember_owned(state):
    driver = state.get("driver")
    if not driver:
        return
    known = {(v["pid"], v["created"]): v for v in state.get("owned_driver_processes", [])}
    if same_process(driver, strict=False):
        candidates = [psutil.Process(driver["pid"])]
        try:
            candidates += candidates[0].children(recursive=True)
        except psutil.Error:
            pass
        for p in candidates:
            try:
                known[(p.pid, p.create_time())] = {"pid": p.pid, "created": p.create_time(), "pgid": os.getpgid(p.pid)}
            except (psutil.Error, ProcessLookupError):
                pass
    protected = state["protected_processes"]
    for identity in known.values():
        if any(identity["pid"] == p["pid"] or identity["pgid"] == p["pgid"] for p in protected):
            raise ValueError("Contention ownership overlaps an original campaign process/group")
    state["owned_driver_processes"] = list(known.values())


def cleanup_owned(state):
    """Signal only creation-time-bound contention PIDs; never the original runner."""
    remember_owned(state)
    for sig, grace in ((signal.SIGTERM, 20), (signal.SIGKILL, 10)):
        for identity in reversed(state.get("owned_driver_processes", [])):
            if same_process(identity, strict=False):
                os.kill(identity["pid"], sig)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            remember_owned(state)
            if not any(same_process(v, strict=False) for v in state.get("owned_driver_processes", [])):
                return
            time.sleep(.5)
    raise RuntimeError("Owned contention processes remain; original scheduler stays held")


def foreign_contention_processes(config, output):
    """Catch orphan wrappers even if ownership polling never observed them."""
    helpers = {os.getpid(): psutil.Process().create_time(),
               config["manager"]["pid"]: config["manager"]["created"]} if "manager" in config else {
                   os.getpid(): psutil.Process().create_time()}
    guardian_path = Path(output) / "GUARDIAN.json"
    if guardian_path.exists():
        value = read_json(guardian_path)
        helpers[value["pid"]] = value["created"]
    blockers = []
    for process in psutil.process_iter(["pid", "cmdline", "status", "create_time"]):
        try:
            info = process.info
            if info["status"] == psutil.STATUS_ZOMBIE:
                continue
            command = info.get("cmdline") or []
            markers = [arg for arg in command if Path(arg).name.startswith("contention_") and Path(arg).suffix == ".py"]
            if not markers:
                continue
            own_helper = (info["pid"] in helpers and abs(info["create_time"] - helpers[info["pid"]]) < .01
                          and any(Path(arg).resolve() == Path(__file__).resolve() for arg in markers))
            if not own_helper:
                blockers.append({"pid": info["pid"], "created": info["create_time"], "markers": markers})
        except (psutil.Error, OSError):
            # A process disappearing during a scan is not a live blocker.
            continue
    return blockers


def live_owned_group_members(state):
    groups = {v["pgid"] for v in state.get("owned_driver_processes", [])}
    protected_groups = {v["pgid"] for v in state.get("protected_processes", [])}
    if groups & protected_groups:
        raise ValueError("Driver group inventory overlaps original campaign groups")
    members = []
    for p in psutil.process_iter(["pid", "status", "create_time"]):
        try:
            if p.info["status"] != psutil.STATUS_ZOMBIE and os.getpgid(p.pid) in groups:
                members.append({"pid": p.pid, "created": p.info["create_time"], "pgid": os.getpgid(p.pid)})
        except (psutil.Error, ProcessLookupError):
            continue
    return members


def require_no_foreign_contention(config, output, label):
    blockers = foreign_contention_processes(config, output)
    value = {"schema_version": "h100-contention-residual-process-scan-v1", "label": label,
             "at_utc": utcnow(), "status": "CLEAR" if not blockers else "BLOCKED_RESUME",
             "contention_processes": blockers}
    atomic_json(output / ("RESIDUAL_SCAN_" + label + ".json"), value)
    if blockers:
        raise RuntimeError("Unknown residual contention processes remain; scheduler stays held")
    return value


def verified_resume(config, state, output, *, actor):
    with (output / "RESUME.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = output / "RESUMED.json"
        if receipt.exists():
            return read_json(receipt)
        scheduler = config["scheduler"]
        if not same_process(scheduler, require_stopped=True):
            raise ValueError("Cannot resume changed or unheld original scheduler")
        if state.get("contention_started"):
            cleanup_owned(state)
            members = live_owned_group_members(state)
            atomic_json(output / "OWNED_GROUPS_AFTER_CLEANUP.json", {"at_utc": utcnow(), "live_members": members})
            if members:
                raise RuntimeError("A recorded contention process group remains; no unsafe resume")
            require_no_foreign_contention(config, output, "before-profile")
            check = profile_check(config, "before-original-resume")
            atomic_json(output / "PROFILE_BEFORE_RESUME.json", check)
            if sha(state["snapshot"]["journal_path"]) != state["snapshot"]["journal_sha256"]:
                raise ValueError("Original journal changed while scheduler was held")
            if attempt_inventory(state["snapshot"]) != read_json(output / "ORIGINAL_ATTEMPT_FILES.json"):
                raise ValueError("Finished original attempt files changed during contention")
        # Before any contention, an abort just returns the unmodified scheduler
        # to its original child wait. Never hash data while that child is active.
        require_no_foreign_contention(config, output, "before-signal")
        resume_scheduler(scheduler)
        value = {"schema_version": "h100-contention-handoff-resume-v1", "status": "RESUMED",
                 "resumed_at_utc": utcnow(), "actor": actor, "scheduler": scheduler,
                 "contention_started": state.get("contention_started", False),
                 "scope": "Operational continuation only; no scientific completion or performance claim"}
        atomic_json(receipt, value)
        event(output, "ORIGINAL_SCHEDULER_RESUMED", actor=actor)
        return value


def guardian(output):
    config = read_json(output / "CONFIG.json")
    manager = config["manager"]
    observed = {}
    while not (output / "RESUMED.json").exists():
        state = read_json(output / "STATE.json")
        # Retain independently observed descendants even if the manager crashes
        # between ownership writes. Unknown residual jobs still fail idle checks.
        remember_owned(state)
        for value in state.get("owned_driver_processes", []):
            observed[(value["pid"], value["created"])] = value
        # A detached nohup manager may legitimately be reparented after SSH exits.
        alive = same_process(manager, strict=False)
        elapsed = time.time() - state.get("held_unix", config["created_unix"])
        limit = config["drain_timeout"] if not state.get("contention_started") else config["total_timeout"]
        if alive and elapsed > limit:
            os.kill(manager["pid"], signal.SIGTERM)
            event(output, "GUARDIAN_MANAGER_DEADLINE", elapsed_seconds=elapsed)
        if not alive:
            state["owned_driver_processes"] = list(observed.values())
            if same_process(config["scheduler"], require_stopped=True):
                try:
                    verified_resume(config, state, output, actor="detached-guardian")
                except Exception as exc:
                    atomic_json(output / "GUARDIAN_BLOCKED.json", {"status": "BLOCKED_RESUME", "at_utc": utcnow(),
                                "error_type": type(exc).__name__, "error": str(exc),
                                "scope": "Scheduler remains held; no unsafe continuation into foreign workload"})
                    return 2
            return 0
        time.sleep(2)
    return 0


def interrupted(signum, frame):
    raise InterruptedError(f"Handoff manager received signal {signum}")


def run(args):
    if sys.platform != "linux":
        raise ValueError("Real handoff requires Linux proc identity/exit evidence")
    campaign, source = args.campaign.resolve(), args.source.resolve()
    scheduler = proc_identity(args.scheduler_pid)
    if abs(scheduler["created"] - args.scheduler_created) > .01:
        raise ValueError("Explicit scheduler creation time differs")
    snapshot = check_scheduler_snapshot(scheduler, campaign, source, args.supervisor_pid)
    if sha(args.profile) != PROFILE_SHA:
        raise ValueError("Unregistered execution profile")
    spec = read_json(args.driver_spec)
    command = spec.get("command")
    if (spec.get("schema_version") != "h100-contention-driver-spec-v1" or not isinstance(command, list)
            or len(command) < 2 or any(not isinstance(v, str) or not v or "\0" in v for v in command)
            or not Path(command[1]).name.startswith("contention_") or not Path(command[1]).is_file()
            or not isinstance(spec.get("files_sha256"), dict) or not spec["files_sha256"]):
        raise ValueError("Explicit hash-bound Python contention driver spec required")
    for name, digest in spec["files_sha256"].items():
        if sha(name) != digest:
            raise ValueError("Contention driver source changed")
    if str(Path(command[1]).resolve()) not in spec["files_sha256"]:
        raise ValueError("Driver script itself must be hash-bound")
    output = args.output.resolve()
    if output.is_relative_to(source) or output.is_relative_to(campaign.parent):
        raise ValueError("Handoff output must be outside original source/evidence")
    output.mkdir(parents=True, exist_ok=False)
    config = {"schema_version": "h100-contention-handoff-v1", "created_at_utc": utcnow(),
              "created_unix": time.time(), "campaign": str(campaign), "source": str(source),
              "profile": str(args.profile.resolve()), "scheduler": scheduler,
              "manager": proc_identity(os.getpid()), "drain_timeout": args.drain_timeout,
              "driver_timeout": args.driver_timeout, "total_timeout": args.total_timeout,
              "driver_spec": str(args.driver_spec.resolve()),
              "driver_spec_sha256": sha(args.driver_spec), "handoff_source_sha256": sha(__file__)}
    protected = [scheduler, snapshot["supervisor"], snapshot["runner"]]
    for p in psutil.Process(snapshot["runner"]["pid"]).children(recursive=True):
        try:
            protected.append(proc_identity(p.pid))
        except psutil.Error:
            pass
    state = {"phase": "INTENT_ONLY", "snapshot": snapshot, "protected_processes": protected,
             "contention_started": False, "owned_driver_processes": []}
    atomic_json(output / "CONFIG.json", config)
    atomic_json(output / "STATE.json", state)
    (output / "DRIVER_SPEC.json").write_bytes(args.driver_spec.read_bytes())
    event(output, "HOLD_INTENT", scheduler=scheduler, active_session=snapshot["session_id"])
    with (output / "guardian.log").open("xb") as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--guardian", str(output)],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    atomic_json(output / "GUARDIAN.json", proc_identity(child.pid))
    previous = {s: signal.signal(s, interrupted) for s in (signal.SIGTERM, signal.SIGINT)}
    driver = None
    try:
        stop_scheduler(scheduler)
        state.update(phase="SCHEDULER_HELD_DRAINING", held_unix=time.time(), held_at_utc=utcnow())
        atomic_json(output / "STATE.json", state)
        event(output, "SCHEDULER_STOPPED_PID_ONLY")
        if sha(snapshot["journal_path"]) != snapshot["journal_sha256"]:
            raise ValueError("Scheduler crossed a session boundary before the hold; no contention started")
        deadline = time.monotonic() + args.drain_timeout
        while True:
            if not same_process(scheduler, require_stopped=True):
                raise ValueError("Original scheduler did not remain held")
            drained = original_drained(snapshot)
            if drained is not None:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Original runner drain deadline; no contention launched")
            time.sleep(2)
        atomic_json(output / "ORIGINAL_RUNNER_DRAINED.json", drained)
        atomic_json(output / "ORIGINAL_ATTEMPT_FILES.json", attempt_inventory(snapshot))
        before = profile_check(config, "before-contention")
        atomic_json(output / "PROFILE_BEFORE_CONTENTION.json", before)
        if sha(snapshot["journal_path"]) != snapshot["journal_sha256"]:
            raise ValueError("Original journal changed while scheduler was held")
        state.update(phase="READY_FOR_CONTENTION", ready_at_utc=utcnow())
        atomic_json(output / "STATE.json", state)
        event(output, "READY_FOR_CONTENTION", original_session=snapshot["session_id"])
        if sha(args.driver_spec) != config["driver_spec_sha256"]:
            raise ValueError("Contention driver specification changed during original drain")
        for name, digest in spec["files_sha256"].items():
            if sha(name) != digest:
                raise ValueError("Contention driver source changed during original drain")
        if args.total_timeout - (time.time() - state["held_unix"]) <= 0:
            raise TimeoutError("Total handoff deadline reached before contention launch")
        with (output / "contention-driver.log").open("xb") as log:
            driver = subprocess.Popen([sys.executable, "-c", LAUNCH_GATE, *command],
                                      cwd=spec.get("cwd", str(source)), stdin=subprocess.PIPE,
                                      stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            state.update(phase="CONTENTION_RUNNING", contention_started=True, driver=proc_identity(driver.pid),
                         driver_started_unix=time.time(), driver_started_at_utc=utcnow())
            remember_owned(state)
            atomic_json(output / "STATE.json", state)
            driver.stdin.write(b"G")
            driver.stdin.close()
            remaining_total = args.total_timeout - (time.time() - state["held_unix"])
            deadline = time.monotonic() + min(args.driver_timeout, remaining_total)
            while driver.poll() is None:
                if not same_process(scheduler, require_stopped=True):
                    raise ValueError("Original scheduler resumed during contention")
                remember_owned(state)
                atomic_json(output / "STATE.json", state)
                if time.monotonic() >= deadline:
                    raise TimeoutError("Contention driver reached its predeclared wall limit")
                time.sleep(1)
            state.update(driver_returncode=driver.returncode, phase="CONTENTION_DRIVER_EXITED", driver_ended_at_utc=utcnow())
            atomic_json(output / "STATE.json", state)
            if driver.returncode:
                raise RuntimeError(f"Contention driver failed with exit {driver.returncode}; evidence retained")
    except BaseException as exc:
        state.update(error_type=type(exc).__name__, error=str(exc), phase="HANDOFF_ERROR")
        atomic_json(output / "STATE.json", state)
        raise
    finally:
        # Repeated external SIGTERM must not interrupt owned cleanup/resume.
        for s in previous:
            signal.signal(s, signal.SIG_IGN)
        try:
            if driver is not None and not state.get("contention_started"):
                if driver.stdin is not None and not driver.stdin.closed:
                    driver.stdin.close()
                driver.wait(timeout=5)
            if same_process(scheduler, require_stopped=True):
                verified_resume(config, state, output, actor="handoff-manager")
        finally:
            for s, handler in previous.items():
                signal.signal(s, handler)
    state.update(phase="COMPLETE_RESUMED", completed_at_utc=utcnow())
    atomic_json(output / "STATE.json", state)
    return state


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--guardian"]:
        return guardian(Path(argv[1]).resolve())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduler-pid", type=int, required=True)
    parser.add_argument("--scheduler-created", type=float, required=True)
    parser.add_argument("--supervisor-pid", type=int, required=True)
    for field in ("campaign", "source", "profile", "driver-spec", "output"):
        parser.add_argument("--" + field, type=Path, required=True)
    parser.add_argument("--drain-timeout", type=float, default=2700)
    parser.add_argument("--driver-timeout", type=float, default=14400)
    parser.add_argument("--total-timeout", type=float, default=14400)
    args = parser.parse_args(argv)
    if (not 1 <= args.drain_timeout <= 2700 or not 1 <= args.driver_timeout <= 14400
            or not 1 <= args.total_timeout <= 14400):
        parser.error("Drain limit must be <=45min and total hold/contention limit <=4h")
    print(json.dumps({"status": run(args)["phase"], "scope": "Operational handoff only"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
