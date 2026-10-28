#!/usr/bin/env python3
"""Run the separately frozen contention phases with off-host idle checkpoints."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def utc():
    return datetime.now(timezone.utc).isoformat()


def checkpoint(args, state, phase):
    root = Path(args.plan).parent
    state.update(status="WAITING_FOR_OFFHOST_BACKUP", waiting_phase=phase)
    save(root / "PHASE_DRIVER.json", state)
    marker = root / "WAITING_FOR_OFFHOST_BACKUP"
    marker.write_text(phase + "\n")
    subprocess.run([sys.executable, str(args.backup_tool), "snapshot", "--evidence", str(root),
                    "--snapshot-root", str(args.snapshot_root), "--phase", phase], check=True)
    control = read(args.snapshot_root / (phase + ".json"))
    if control["plan_sha256"] != args.plan_sha256:
        raise ValueError("Snapshot belongs to another contention plan")
    deadline = time.monotonic() + args.ack_timeout_seconds
    receipt_path = root / "backup-receipts" / (phase + ".json")
    while not receipt_path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Off-host contention checkpoint deadline; no next phase started")
        time.sleep(2)
    receipt = read(receipt_path)
    if (receipt.get("status") != "OFFHOST_SHA256_VERIFIED"
            or any(receipt.get(key) != control.get(key)
                   for key in ("phase", "plan_sha256", "tar_sha256", "manifest_sha256"))):
        raise ValueError("Off-host contention receipt does not bind the actual snapshot")
    state.setdefault("offhost_checkpoints", []).append({"phase": phase,
        "receipt_sha256": sha(receipt_path), "control_sha256": sha(args.snapshot_root / (phase + ".json")),
        "acknowledged_at_utc": utc()})
    marker.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--prestart-sha256", required=True)
    parser.add_argument("--backup-tool", type=Path, required=True)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--ack-timeout-seconds", type=float, default=1200)
    args = parser.parse_args(argv)
    if not 1 <= args.ack_timeout_seconds <= 1200:
        parser.error("Acknowledgement deadline must be between 1 and 1200 seconds")
    root = args.plan.resolve().parent
    if (sha(args.plan) != args.plan_sha256
            or sha(root / "CONTENTION_PRESTART_MANIFEST.json") != args.prestart_sha256):
        raise ValueError("Actual off-host preserved contention prestart differs")
    plan = read(args.plan)
    if (plan.get("schema_version") != "h100-contention-plan-v1" or plan.get("synthetic") is not False
            or len(plan.get("pilot", [])) != 4 or len(plan.get("main", [])) != 16):
        raise ValueError("Expected the separately frozen four-pilot/sixteen-main wave design")
    runner = root / "tools" / "contention_runner.py"
    if sha(runner) != plan["tools_sha256"].get(str(runner)):
        raise ValueError("Frozen contention runner differs")
    if (root / "PHASE_DRIVER.json").exists() or any((root / phase).exists() for phase in ("pilot", "main")):
        raise ValueError("No implicit driver restart or repetition of measured phases")
    state = {"schema_version": "h100-contention-phases-driver-v1", "status": "RUNNING",
             "plan_sha256": args.plan_sha256, "prestart_sha256": args.prestart_sha256,
             "started_at_utc": utc(), "phases": [], "scope": "Separate descriptive contention only"}
    save(root / "PHASE_DRIVER.json", state)
    try:
        for phase in ("pilot", "main"):
            state.update(status="RUNNING", active_phase=phase, waiting_phase=None)
            save(root / "PHASE_DRIVER.json", state)
            # The frozen main runner independently revalidates the complete pilot.
            result = subprocess.run([sys.executable, str(runner), "run", "--plan", str(args.plan), "--phase", phase])
            state["phases"].append({"phase": phase, "returncode": result.returncode, "ended_at_utc": utc()})
            if result.returncode:
                raise RuntimeError(f"Contention {phase} exited {result.returncode}; no measured retry")
            phase_path = root / phase / "PHASE.json"
            phase_result = read(phase_path)
            if (phase_result.get("status") != "PASS" or phase_result.get("plan_sha256") != args.plan_sha256):
                raise ValueError("Actual contention phase failed its own scientific gates")
            state["phases"][-1]["phase_sha256"] = sha(phase_path)
            checkpoint(args, state, phase)
        state.update(status="COMPLETE_OFFHOST", completed_at_utc=utc(), active_phase=None, waiting_phase=None)
        save(root / "PHASE_DRIVER.json", state)
        return 0
    except BaseException as exc:
        state.update(status="FAILED", failed_at_utc=utc(), error={"type": type(exc).__name__, "message": str(exc)})
        save(root / "PHASE_DRIVER.json", state)
        try:
            checkpoint(args, state, "failed")
        except BaseException as backup_error:
            state["failure_backup_error"] = {"type": type(backup_error).__name__, "message": str(backup_error)}
        state.update(status="FAILED", completed_at_utc=utc())
        save(root / "PHASE_DRIVER.json", state)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
