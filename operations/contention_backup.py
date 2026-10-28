#!/usr/bin/env python3
"""Idle-boundary raw backups for the separate contention experiment.

Transport verification only: no 400-session, scientific or publication PASS.
Only the separate driver may create snapshots; the local watcher never runs or
restarts workloads. Every snapshot/control and extracted original stays intact.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tarfile
import tempfile
import time

try:
    from . import research_backup as common
except ImportError:
    import research_backup as common

SCHEMA = "h100-contention-offhost-backup-v1"
PHASES = {"pilot", "main", "failed"}
HEX = re.compile(r"[0-9a-f]{64}\Z")
MANIFEST = "CONTENTION_BACKUP_MANIFEST.json"
IDENTITY_FIELDS = ("schema_version", "phase", "plan_sha256", "tar_sha256", "tar_bytes", "manifest_sha256", "files")


def utc():
    return datetime.now(timezone.utc).isoformat()


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def read(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=no_duplicates)


def decode(body):
    return json.loads(body, object_pairs_hook=no_duplicates)


def payload(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def check_phase(phase):
    if phase not in PHASES:
        raise ValueError("Expected pilot, main or failed backup phase")
    return phase


def validate_control(control, expected=None, phase=None):
    if (control.get("schema_version") != SCHEMA or control.get("phase") not in PHASES
            or not HEX.fullmatch(control.get("plan_sha256", ""))
            or not HEX.fullmatch(control.get("tar_sha256", ""))
            or not HEX.fullmatch(control.get("manifest_sha256", ""))
            or type(control.get("tar_bytes")) is not int or control["tar_bytes"] <= 0
            or type(control.get("files")) is not int or control["files"] <= 0):
        raise ValueError("Invalid contention transport control")
    if expected is not None and control["plan_sha256"] != expected:
        raise ValueError("Contention plan identity differs")
    if phase is not None and control["phase"] != phase:
        raise ValueError("Contention transport phase differs")
    return control


def tree_inventory(root):
    """Reject links, special files and accidental datasets/runtime/secrets."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Evidence must be a regular directory")
    result = {}
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError("Symlinks are not transport evidence")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("Special files are not transport evidence")
        name = common.safe_file(path, root.resolve())
        if name == MANIFEST:
            raise ValueError("Generated backup manifest collides with source")
        before = path.stat()
        digest = common.sha(path)
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("Evidence changed while hashing")
        result[name] = {"bytes": before.st_size, "sha256": digest}
    if not result:
        raise ValueError("Evidence tree is empty")
    return result


def require_idle():
    import psutil
    blockers = []
    scripts = {"worker.py", "run_experiment.py", "contention_worker.py", "contention_runner.py"}
    modules = {"org.apache.spark.deploy.SparkSubmit", "pyspark.daemon", "pyspark.worker"}
    for p in psutil.process_iter(["pid", "cmdline", "status"]):
        if p.pid == os.getpid() or p.info["status"] == psutil.STATUS_ZOMBIE:
            continue
        markers = [arg for arg in (p.info.get("cmdline") or []) if Path(arg).name in scripts or arg in modules]
        if markers:
            blockers.append({"pid": p.pid, "markers": markers})
    probe = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=30)
    if blockers or probe.returncode != 0 or probe.stdout.strip():
        raise ValueError("Snapshot requires idle original/contention workers and GPU")
    return {"observed_at_utc": utc(), "eligible": True, "benchmark_processes": [], "gpu_compute_clients": ""}


class HashingReader:
    def __init__(self, handle):
        self.handle, self.digest, self.bytes = handle, hashlib.sha256(), 0

    def read(self, size=-1):
        body = self.handle.read(size)
        self.digest.update(body)
        self.bytes += len(body)
        return body


def snapshot(evidence, snapshot_root, phase):
    check_phase(phase)
    evidence = Path(evidence).resolve(strict=True)
    snapshot_root = Path(snapshot_root).absolute()
    if snapshot_root.resolve().is_relative_to(evidence):
        raise ValueError("Snapshot storage must be outside evidence")
    if (evidence / "WAITING_FOR_OFFHOST_BACKUP").read_text().strip() != phase:
        raise ValueError("Driver has not entered this idle backup boundary")
    plan_path = evidence / "CONTENTION_PLAN.json"
    digest = common.sha(plan_path)
    plan = read(plan_path)
    if ((evidence / "CONTENTION_PLAN.sha256").read_text().strip() != digest
            or plan.get("schema_version") != "h100-contention-plan-v1"
            or plan.get("experiment_id") != "h100-contention-20261028" or plan.get("synthetic") is not False):
        raise ValueError("Genuine separately frozen contention plan required")
    if phase != "failed":
        terminal = read(evidence / phase / "PHASE.json")
        if terminal.get("phase") != phase or terminal.get("status") != "PASS" or terminal.get("plan_sha256") != digest:
            raise ValueError("Successful phase snapshot requires its actual terminal report")
    idle = require_idle()
    identities = tree_inventory(evidence)
    manifest = {"schema_version": SCHEMA, "phase": phase, "plan_sha256": digest,
                "created_at_utc": utc(), "files": identities, "idle_observation": idle,
                "scope": "All contention raw files at an idle driver boundary; transport integrity only, no scientific completion claim"}
    body = payload(manifest)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    tar_path, control_path = snapshot_root / (phase + ".tar"), snapshot_root / (phase + ".json")
    if tar_path.exists() or control_path.exists():
        raise ValueError("Refusing to overwrite immutable snapshot files")
    with tarfile.open(tar_path, "x", format=tarfile.PAX_FORMAT) as archive:
        for name, identity in identities.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = identity["bytes"], 0o644, 0
            with (evidence / name).open("rb") as handle:
                source = HashingReader(handle)
                archive.addfile(info, source)
                if source.bytes != identity["bytes"] or source.digest.hexdigest() != identity["sha256"]:
                    raise ValueError("Evidence changed while archiving")
        info = tarfile.TarInfo(MANIFEST)
        info.size, info.mode, info.mtime = len(body), 0o644, 0
        archive.addfile(info, io.BytesIO(body))
    if tree_inventory(evidence) != identities:
        raise ValueError("Evidence inventory changed during snapshot; preserve partial archive")
    require_idle()
    control = {"schema_version": SCHEMA, "phase": phase, "plan_sha256": digest,
               "tar_sha256": common.sha(tar_path), "tar_bytes": tar_path.stat().st_size,
               "manifest_sha256": hashlib.sha256(body).hexdigest(), "files": len(identities), "created_at_utc": utc()}
    common.exclusive_json(control_path, control)
    return control


def verify_directory(directory, control):
    validate_control(control)
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Extraction destination is not a regular directory")
    directory = directory.resolve(strict=True)
    manifest_path = directory / MANIFEST
    if common.sha(manifest_path) != control["manifest_sha256"]:
        raise ValueError("Manifest bytes differ")
    manifest = read(manifest_path)
    if any(manifest.get(k) != control[k] for k in ("schema_version", "phase", "plan_sha256")):
        raise ValueError("Manifest/control identity differs")
    # The generated manifest is the only file outside the original inventory.
    actual = {common.safe_file(p, directory.resolve()) for p in common.tree_files(directory)}
    if actual != set(manifest["files"]) | {MANIFEST} or len(manifest["files"]) != control["files"]:
        raise ValueError("Extracted file inventory differs")
    for name, identity in manifest["files"].items():
        common.safe_relative(name)
        if name in (".", MANIFEST):
            raise ValueError("Unsafe manifest source path")
        path = directory / name
        if path.stat().st_size != identity["bytes"] or common.sha(path) != identity["sha256"]:
            raise ValueError("Extracted source bytes differ: " + name)
    if common.sha(directory / "CONTENTION_PLAN.json") != control["plan_sha256"]:
        raise ValueError("Extracted plan identity differs")
    return manifest


def verify_archive(tar_path, control, destination):
    validate_control(control)
    tar_path, destination = Path(tar_path), Path(destination)
    if tar_path.is_symlink() or common.sha(tar_path) != control["tar_sha256"] or tar_path.stat().st_size != control["tar_bytes"]:
        raise ValueError("Transferred tar SHA256/bytes differ")
    if destination.is_symlink():
        raise ValueError("Extraction destination cannot be a symlink")
    if destination.exists():
        return verify_directory(destination, control)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix="." + destination.name + "-") as tmp:
        staging = Path(tmp)
        with tarfile.open(tar_path, "r:") as archive:
            members = archive.getmembers()
            names = [m.name for m in members]
            if len(names) != len(set(names)) or MANIFEST not in names:
                raise ValueError("Duplicate or missing archive members")
            for member in members:
                common.safe_relative(member.name)
                if member.name == "." or not member.isfile():
                    raise ValueError("Only contained regular archive files are allowed")
            manifest_body = archive.extractfile(MANIFEST).read()
            if hashlib.sha256(manifest_body).hexdigest() != control["manifest_sha256"]:
                raise ValueError("Archive manifest identity differs")
            manifest = decode(manifest_body)
            if set(names) != set(manifest["files"]) | {MANIFEST}:
                raise ValueError("Archive inventory differs")
            for member in members:
                path = staging / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as output, archive.extractfile(member) as source:
                    for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                        output.write(chunk)
        manifest = verify_directory(staging, control)
        staging.rename(destination)
    return manifest


def pull(host, identity, snapshot_root, phase, destination, expected):
    check_phase(phase)
    snapshot_root = common.remote_path(snapshot_root)
    prefix = common.ssh_prefix(host, identity)
    body = common.network_run(prefix + [f"cat {snapshot_root}/{phase}.json"], capture=True)
    control = validate_control(decode(body), expected, phase)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    local_control = destination / (phase + ".json")
    if local_control.exists() and local_control.read_bytes() != body:
        raise ValueError("Existing immutable local control changed")
    if not local_control.exists():
        with local_control.open("xb") as handle:
            handle.write(body)
    tar_path = destination / (phase + ".tar")
    if not tar_path.exists():
        partial = destination / (phase + ".tar.partial")
        common.network_run(["rsync", "--partial", "--checksum", "--timeout=30", "-e", shlex.join(prefix[:-1]),
                            f"{host}:{snapshot_root}/{phase}.tar", str(partial)], timeout=600)
        if common.sha(partial) != control["tar_sha256"] or partial.stat().st_size != control["tar_bytes"]:
            raise ValueError("Transferred partial file does not match; no ACK")
        partial.rename(tar_path)
    verify_archive(tar_path, control, destination / phase)
    report_path = destination / (phase + ".verified.json")
    if report_path.exists():
        report = read(report_path)
        if any(report.get(k) != control[k] for k in IDENTITY_FIELDS) or report.get("status") != "OFFHOST_SHA256_VERIFIED":
            raise ValueError("Existing off-host receipt differs")
        if (Path(report["local_archive"]).resolve() != tar_path.resolve()
                or Path(report["local_directory"]).resolve() != (destination / phase).resolve()):
            raise ValueError("Existing receipt paths differ")
    else:
        report = control | {"status": "OFFHOST_SHA256_VERIFIED", "verified_at_utc": utc(),
                            "local_archive": str(tar_path.resolve()), "local_directory": str((destination / phase).resolve())}
        common.exclusive_json(report_path, report)
    return report


def acknowledge(host, identity, evidence, snapshot_root, phase, destination, expected):
    destination = Path(destination)
    control = validate_control(read(destination / (phase + ".json")), expected, phase)
    report = read(destination / (phase + ".verified.json"))
    if report.get("status") != "OFFHOST_SHA256_VERIFIED" or any(report.get(k) != control[k] for k in IDENTITY_FIELDS):
        raise ValueError("Missing or mismatched verified off-host receipt")
    if (Path(report["local_archive"]).resolve() != (destination / (phase + ".tar")).resolve()
            or Path(report["local_directory"]).resolve() != (destination / phase).resolve()):
        raise ValueError("Verified receipt paths differ from the actual off-host copy")
    verify_archive(destination / (phase + ".tar"), control, destination / phase)
    receipt = {k: control[k] for k in IDENTITY_FIELDS} | {"status": "OFFHOST_SHA256_VERIFIED", "verified_at_utc": report["verified_at_utc"]}
    code = '''import json,os,pathlib,sys
r,s,phase=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]),sys.argv[3]
receipt=json.load(sys.stdin)
control=json.loads((s/(phase+'.json')).read_bytes())
keys=('schema_version','phase','plan_sha256','tar_sha256','tar_bytes','manifest_sha256','files')
if receipt.get('status')!='OFFHOST_SHA256_VERIFIED' or any(receipt[k]!=control[k] for k in keys): raise ValueError('Remote control changed')
target=r/'backup-receipts'/(phase+'.json')
if target.exists():
 old=json.loads(target.read_bytes())
 if old.get('status')!=receipt['status'] or any(old[k]!=receipt[k] for k in keys): raise ValueError('Existing receipt changed')
else:
 if (r/'WAITING_FOR_OFFHOST_BACKUP').read_text().strip()!=phase: raise ValueError('Driver left this boundary')
 target.parent.mkdir(parents=True,exist_ok=True)
 temporary=target.with_name('.'+target.name+'.'+str(os.getpid())+'.tmp')
 try:
  with temporary.open('xb') as handle:
   handle.write((json.dumps(receipt,sort_keys=True)+'\\n').encode());handle.flush();os.fsync(handle.fileno())
  os.link(temporary,target)
 finally: temporary.unlink(missing_ok=True)
print(json.dumps({'phase':phase,'status':'ACKNOWLEDGED'}))
'''
    command = shlex.join(["python3", "-c", code, common.remote_path(evidence), common.remote_path(snapshot_root), phase])
    common.network_run(common.ssh_prefix(host, identity) + [command], input=payload(receipt), capture=True)
    return receipt


def remote_status(host, identity, evidence, snapshot_root, expected):
    code = '''import hashlib,json,pathlib,sys
r,s=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2])
p=r/'WAITING_FOR_OFFHOST_BACKUP'; phase=p.read_text().strip() if p.is_file() else None
print(json.dumps({'plan_sha256':hashlib.sha256((r/'CONTENTION_PLAN.json').read_bytes()).hexdigest(),'waiting_phase':phase,'control_ready':phase in ('pilot','main','failed') and (s/(phase+'.json')).is_file()}))
'''
    command = shlex.join(["python3", "-c", code, common.remote_path(evidence), common.remote_path(snapshot_root)])
    status = decode(common.network_run(common.ssh_prefix(host, identity) + [command], capture=True))
    if status.get("plan_sha256") != expected or status.get("waiting_phase") not in PHASES | {None}:
        raise ValueError("Remote contention identity/phase changed")
    return status


def watch(args):
    if not HEX.fullmatch(args.plan_sha256) or not 10 <= args.interval_seconds <= 300:
        raise ValueError("Watcher requires exact plan SHA256 and interval 10..300 seconds")
    destination = Path(args.destination)
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "watcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        log = destination / "watcher.jsonl"
        def record(event, **fields):
            row = {"at_utc": utc(), "event": event, **fields}
            with log.open("ab") as handle:
                handle.write((json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode());handle.flush();os.fsync(handle.fileno())
            print(json.dumps(row), flush=True)
        record("WATCHER_STARTED", plan_sha256=args.plan_sha256)
        while True:
            try:
                state = remote_status(args.host, args.identity_file, args.evidence, args.snapshot_root, args.plan_sha256)
                record("STATUS", **state)
                phase = state["waiting_phase"]
                if phase is not None and state["control_ready"]:
                    report = pull(args.host, args.identity_file, args.snapshot_root, phase, destination, args.plan_sha256)
                    acknowledge(args.host, args.identity_file, args.evidence, args.snapshot_root, phase, destination, args.plan_sha256)
                    record("OFFHOST_BACKUP_ACKNOWLEDGED", phase=phase, files=report["files"])
                    if phase in {"main", "failed"}:
                        return
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                record("NETWORK_RETRY", error_type=type(exc).__name__)
            except BaseException as exc:
                record("STOPPED_ATTENTION", error_type=type(exc).__name__, error=str(exc))
                raise
            time.sleep(args.interval_seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("snapshot")
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--snapshot-root", type=Path, required=True)
    p.add_argument("--phase", choices=sorted(PHASES), required=True)
    p = sub.add_parser("verify")
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--control", type=Path, required=True)
    p.add_argument("--directory", type=Path, required=True)
    for mode in ("pull", "ack", "watch"):
        p = sub.add_parser(mode)
        p.add_argument("--host", required=True)
        p.add_argument("--identity-file", type=Path, required=True)
        p.add_argument("--snapshot-root", required=True)
        p.add_argument("--destination", type=Path, required=True)
        p.add_argument("--plan-sha256", required=True)
        if mode in {"ack", "watch"}:
            p.add_argument("--evidence", required=True)
        if mode != "watch":
            p.add_argument("--phase", choices=sorted(PHASES), required=True)
        else:
            p.add_argument("--interval-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    if args.mode == "snapshot":
        result = snapshot(args.evidence, args.snapshot_root, args.phase)
    elif args.mode == "verify":
        result = verify_archive(args.archive, read(args.control), args.directory)
        result = {"status": "OFFHOST_SHA256_VERIFIED", "files": len(result["files"]), "scope": "Transport only"}
    elif args.mode == "watch":
        watch(args)
        return
    elif args.mode == "pull":
        result = pull(args.host, args.identity_file, args.snapshot_root, args.phase, args.destination, args.plan_sha256)
    else:
        result = acknowledge(args.host, args.identity_file, args.evidence, args.snapshot_root, args.phase, args.destination, args.plan_sha256)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
