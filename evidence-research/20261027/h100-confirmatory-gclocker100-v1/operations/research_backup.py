#!/usr/bin/env python3
"""Back up frozen prestart evidence and complete raw blocks between measurements.

Remote: ``snapshot`` writes an immutable tar/control outside the evidence tree.
Local: ``pull`` resumes transfer and verifies/extracts every byte; ``ack`` then
publishes a receipt. The supervisor accepts only a receipt for that exact tar.
Prestart acknowledgements additionally prove all archived files exist unchanged
in a local Git commit. This is transport integrity, not scientific validation.
No source Parquet, virtual environments, jars or credentials are collected.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import fcntl
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import signal
import subprocess
import tarfile
import tempfile
import time

SOURCE_TREE = "9514f0f7ce384763d0a675a9075666707e0edabcc003c729a3e0735a52ea25d8"
SOURCE_COMMIT = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
SCHEMA = "h100-offhost-backup-v1"
PHASE_PATTERN = r"(?:prestart|block-(?:0[1-9]|10)|final|completion)"
SAFE_REMOTE = re.compile(r"/[A-Za-z0-9_./-]+\Z")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def exclusive_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload(value))
        handle.flush()
        os.fsync(handle.fileno())


def safe_relative(name):
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or ".." in relative.parts
            or str(relative) != name or "\\" in name):
        raise ValueError(f"Unsafe archive name: {name!r}")
    return relative


def safe_file(path, root):
    relative = path.relative_to(root)
    safe_relative(relative.as_posix())
    if any(part.startswith(".venv") or part in {".git", ".ssh", "__pycache__"}
           for part in relative.parts):
        raise ValueError(f"Excluded private/runtime path: {relative}")
    if (path.suffix.lower() in {".parquet", ".jar", ".pem", ".key", ".duckdb"}
            or path.name.startswith(("id_rsa", "id_ed25519", ".env"))):
        raise ValueError(f"Excluded data/runtime/credential file: {relative}")
    # Reject symlinks in every component, not just in the final basename.
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"Symlink is not evidence: {relative}")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError(f"Not a regular contained evidence file: {relative}")
    return relative.as_posix()


def read_plan(root):
    plan_path = root / "campaign.json"
    digest = sha(plan_path)
    if (root / "campaign.json.sha256").read_text().strip() != digest:
        raise ValueError("Campaign sidecar does not match")
    plan = json.loads(plan_path.read_text())
    if (plan.get("schema_version") != "research-campaign-v1"
            or len(plan.get("sessions", [])) != 400
            or plan["source"]["source_tree_sha256"] != SOURCE_TREE):
        raise ValueError("Expected a new 400-session campaign using the frozen C84 source")
    return plan, digest


def tree_files(root):
    if root.is_symlink():
        raise ValueError(f"Symlink directory is not evidence: {root}")
    files = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlink is not evidence: {path}")
        if not path.is_dir():
            files.append(path)
    return files


def snapshot(evidence, snapshot_root, phase):
    if not re.fullmatch(PHASE_PATTERN, phase):
        raise ValueError("Invalid backup phase")
    evidence = evidence.resolve(strict=True)
    snapshot_root = snapshot_root.absolute()
    if snapshot_root.resolve().is_relative_to(evidence):
        raise ValueError("Snapshot storage must be outside the evidence tree")
    plan, digest = read_plan(evidence)
    files = {evidence / "campaign.json", evidence / "campaign.json.sha256"}
    generated = {}
    count = 0
    if phase == "prestart":
        if (evidence / "campaign-results/journal.json").exists():
            raise ValueError("Prestart snapshot must precede journal creation")
        # Explicit preparation roots: spill/scratch, backup controls and runtime
        # paths cannot enter the archive through a recursive evidence-root walk.
        directories = ("campaign.json.source", "readiness", "oracles", "manifests",
                       "dataset-manifests", "inventory", "test-results", "operations")
        for name in ("campaign.json.source", "readiness", "oracles"):
            if not (evidence / name).is_dir():
                raise ValueError(f"Missing required prestart directory: {name}")
        for path in evidence.iterdir():
            if path.is_file():
                files.add(path)
        for name in directories:
            directory = evidence / name
            if directory.exists():
                files.update(tree_files(directory))
        for relative, expected in plan["source"]["files_sha256"].items():
            if sha(evidence / "campaign.json.source" / relative) != expected:
                raise ValueError(f"Frozen source differs: {relative}")
        for cell, oracle in plan["oracles"].items():
            path = evidence / "oracles" / f"{cell}.json"
            if sha(path) != oracle["sha256"]:
                raise ValueError(f"Oracle differs: {cell}")
        if sha(evidence / "readiness/READINESS.json") != plan["readiness"]["sha256"]:
            raise ValueError("Readiness identity differs")
        for scale, dataset in plan["datasets"].items():
            candidates = [evidence / name / f"sf{scale}" / "manifest.json"
                          for name in ("manifests", "dataset-manifests")]
            candidates += [evidence / name / f"sf{scale}.json"
                           for name in ("manifests", "dataset-manifests")]
            if not any(path.is_file() and sha(path) == dataset["manifest_sha256"] for path in candidates):
                raise ValueError(f"Missing copied manifest for SF{scale}")
    elif phase == "completion":
        if ((evidence / "confirm-driver.exit").read_text().strip() != "0"
                or not (evidence / "confirm-driver.ended-utc").is_file()
                or not (evidence / "ANALYSIS_READY").is_file()):
            raise ValueError("Completion snapshot requires successful actual supervisor exit")
        driver_pid = int((evidence / "confirm-driver.pid").read_text())
        if Path(f"/proc/{driver_pid}").exists():
            raise ValueError("Supervisor has not actually exited; wait before final log collection")
        journal_bytes = (evidence / "campaign-results/journal.json").read_bytes()
        journal = json.loads(journal_bytes)
        if (journal.get("campaign_sha256") != digest or not journal.get("completed_at_utc")
                or len(journal["sessions"]) != 400
                or any(item["status"] != "succeeded" for item in journal["sessions"].values())):
            raise ValueError("Completion journal is incomplete")
        files.update(path for path in evidence.iterdir() if path.is_file())
        for name in ("backup-receipts", "analysis"):
            files.update(tree_files(evidence / name))
        generated["campaign-results/journal.json"] = journal_bytes
        count = 400
    else:
        journal_path = evidence / "campaign-results/journal.json"
        journal_bytes = journal_path.read_bytes()
        journal = json.loads(journal_bytes)
        if journal.get("campaign_sha256") != digest:
            raise ValueError("Journal belongs to another campaign")
        if any(item["status"] == "running" for item in journal["sessions"].values()):
            raise ValueError("Backup only at an idle block boundary")
        selected = [session for session in plan["sessions"]
                    if phase == "final" or session["block_id"] == phase]
        if len(selected) != (400 if phase == "final" else 40):
            raise ValueError("Unexpected number of selected sessions")
        for session in selected:
            item = journal["sessions"][session["session_id"]]
            if (item["status"] != "succeeded" or len(item["attempts"]) != 1
                    or item.get("scientific_validation", {}).get("status") != "PASS"):
                raise ValueError(f"Incomplete/invalid session: {session['session_id']}")
            gate = Path(item["scientific_validation"]["path"])
            safe_file(gate, evidence)
            if sha(gate) != item["scientific_validation"]["sha256"]:
                raise ValueError("Scientific gate identity differs")
            root = Path(session["output_root"])
            if not root.is_relative_to(evidence / "campaign-results"):
                raise ValueError("Session output is outside the campaign evidence")
            files.update(tree_files(root))
        count = len(selected)
        generated["campaign-results/journal.json"] = journal_bytes
        if phase == "final":
            if not journal.get("completed_at_utc") or not (evidence / "ANALYSIS_READY").is_file():
                raise ValueError("Final hash/validation/analysis have not completed")
            for directory in ("analysis",):
                files.update(tree_files(evidence / directory))
            files.update(path for path in evidence.iterdir() if path.is_file())
    files_by_name = {safe_file(path, evidence): path for path in files}
    if set(files_by_name) & set(generated):
        raise ValueError("Generated file collides with source")
    identities = {name: {"sha256": sha(path), "bytes": path.stat().st_size}
                  for name, path in sorted(files_by_name.items())}
    identities.update({name: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
                       for name, body in generated.items()})
    manifest = {"schema_version": SCHEMA, "phase": phase, "campaign_sha256": digest,
                "created_at_utc": utcnow(), "sessions": count, "files": identities,
                "scope": "All selected raw files; transport integrity only. No final scientific claim."}
    manifest_body = payload(manifest)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    tar_path = snapshot_root / f"{phase}.tar"
    control_path = snapshot_root / f"{phase}.json"
    if tar_path.exists() or control_path.exists():
        raise ValueError("Refusing to overwrite immutable snapshot files")
    with tarfile.open(tar_path, "x", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(identities):
            if name in generated:
                body = generated[name]
                info = tarfile.TarInfo(name)
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
            else:
                archive.add(files_by_name[name], arcname=name, recursive=False)
        info = tarfile.TarInfo("BACKUP_MANIFEST.json")
        info.size = len(manifest_body)
        archive.addfile(info, io.BytesIO(manifest_body))
    # Detect source drift during collection. No file in an accepted snapshot
    # may differ from the manifest even if a preparatory log was still open.
    if any(sha(path) != identities[name]["sha256"] for name, path in files_by_name.items()):
        raise ValueError("Evidence changed during snapshot; preserve tar and investigate")
    control = {"schema_version": SCHEMA, "phase": phase, "campaign_sha256": digest,
               "tar_sha256": sha(tar_path), "tar_bytes": tar_path.stat().st_size,
               "manifest_sha256": hashlib.sha256(manifest_body).hexdigest(),
               "files": len(identities), "sessions": count, "created_at_utc": utcnow()}
    exclusive_json(control_path, control)
    return control


def verify_archive(tar_path, control, destination):
    if (sha(tar_path) != control["tar_sha256"] or tar_path.stat().st_size != control["tar_bytes"]):
        raise ValueError("Transferred tar SHA256/size mismatch")
    if destination.is_symlink():
        raise ValueError("Extraction destination cannot be a symlink")
    if destination.exists():
        return verify_directory(destination, control)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # An interrupted extraction cannot masquerade as a completed copy on the
    # next attempt. Only a completely verified temporary tree is published.
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=f".{destination.name}.extract-") as temporary:
        staging = Path(temporary)
        with tarfile.open(tar_path, "r:") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ValueError("Duplicate tar paths")
            for member in members:
                safe_relative(member.name)
                if not member.isfile():
                    raise ValueError("Only regular files may be extracted")
            manifest_body = archive.extractfile("BACKUP_MANIFEST.json").read()
            if hashlib.sha256(manifest_body).hexdigest() != control["manifest_sha256"]:
                raise ValueError("Manifest identity differs")
            manifest = json.loads(manifest_body)
            if set(names) != set(manifest["files"]) | {"BACKUP_MANIFEST.json"}:
                raise ValueError("Tar file inventory differs")
            for member in members:
                path = staging / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as output, archive.extractfile(member) as source:
                    for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                        output.write(chunk)
        manifest = verify_directory(staging, control)
        staging.rename(destination)
    return manifest


def verify_directory(destination, control):
    destination = destination.resolve(strict=True)
    manifest_path = destination / "BACKUP_MANIFEST.json"
    if sha(manifest_path) != control["manifest_sha256"]:
        raise ValueError("Copied manifest differs")
    manifest = json.loads(manifest_path.read_text())
    if any(manifest[field] != control[field] for field in ("schema_version", "phase", "campaign_sha256", "sessions")):
        raise ValueError("Control/manifest identity mismatch")
    observed = {safe_file(path, destination) for path in tree_files(destination)}
    expected = set(manifest["files"]) | {"BACKUP_MANIFEST.json"}
    sealed = control["phase"] == "prestart" and (destination / "PRESTART_MANIFEST.json").is_file()
    if sealed:
        expected.add("PRESTART_MANIFEST.json")
    if observed != expected or len(manifest["files"]) != control["files"]:
        raise ValueError("Extracted file inventory differs")
    for name, identity in manifest["files"].items():
        safe_relative(name)
        path = destination / name
        if path.stat().st_size != identity["bytes"] or sha(path) != identity["sha256"]:
            raise ValueError(f"Copied evidence hash/size differs: {name}")
    if sealed:
        seal = json.loads((destination / "PRESTART_MANIFEST.json").read_text())
        required = {"schema_version": "h100-confirmatory-prestart-archive-v1", "status": "PASS",
                    "phase": "before-first-confirmatory-session", "source_commit": SOURCE_COMMIT,
                    "source_tree_sha256": SOURCE_TREE, "planned_sessions": 400, "planned_blocks": 10,
                    "primary_contrasts": 26, "campaign_sha256": control["campaign_sha256"]}
        if any(seal.get(key) != value for key, value in required.items()):
            raise ValueError("Canonical prestart seal identity differs")
        expected.remove("PRESTART_MANIFEST.json")
        if set(seal["files_sha256"]) != expected or any(
                sha(destination / name) != digest for name, digest in seal["files_sha256"].items()):
            raise ValueError("Canonical prestart seal inventory/hash differs")
    return manifest


def seal_prestart(directory, control):
    if control["phase"] != "prestart":
        raise ValueError("Only prestart archives can be sealed")
    verify_directory(directory, control)
    target = directory / "PRESTART_MANIFEST.json"
    if target.exists():
        return json.loads(target.read_text())
    plan = json.loads((directory / "campaign.json").read_text())
    if plan["source"].get("git_head") != SOURCE_COMMIT:
        raise ValueError("Campaign was not planned from frozen C84 commit")
    files = {path.relative_to(directory).as_posix(): sha(path) for path in sorted(tree_files(directory))}
    seal = {"schema_version": "h100-confirmatory-prestart-archive-v1", "status": "PASS",
            "phase": "before-first-confirmatory-session", "archived_at_utc": utcnow(),
            "source_commit": SOURCE_COMMIT, "source_tree_sha256": SOURCE_TREE,
            "planned_sessions": 400, "planned_blocks": 10, "primary_contrasts": 26,
            "campaign_sha256": control["campaign_sha256"], "files_sha256": files,
            "successful_readiness_outputs": json.loads((directory / "readiness/READINESS.json").read_text()).get("successful_outputs_compared"),
            "scope": "New replacement-host campaign; previous incomplete campaign is not pooled."}
    exclusive_json(target, seal)
    verify_directory(directory, control)
    return seal


def ssh_prefix(host, identity):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+", host):
        raise ValueError("Expected user@host")
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "IdentitiesOnly=yes",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3", "-i", str(identity), host]


def network_run(command, *, input=None, timeout=30, capture=False):
    """Bound network subprocesses and reap only their owned process group."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.PIPE if capture else None, start_new_session=True)
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=2)
        raise
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command, stdout, stderr)
    return stdout


def remote_path(value):
    if not SAFE_REMOTE.fullmatch(value) or ".." in PurePosixPath(value).parts:
        raise ValueError("Remote paths must be safe absolute paths without spaces")
    return value


def pull(host, identity, remote_snapshot_root, phase, destination_root, expected_campaign=None):
    if not re.fullmatch(PHASE_PATTERN, phase):
        raise ValueError("Invalid backup phase")
    remote_snapshot_root = remote_path(remote_snapshot_root)
    prefix = ssh_prefix(host, identity)
    body = network_run(prefix + [f"cat {remote_snapshot_root}/{phase}.json"], capture=True)
    control = json.loads(body)
    if control.get("schema_version") != SCHEMA or control.get("phase") != phase:
        raise ValueError("Invalid remote backup control")
    if expected_campaign and control.get("campaign_sha256") != expected_campaign:
        raise ValueError("Remote snapshot does not match explicitly requested campaign SHA256")
    destination_root.mkdir(parents=True, exist_ok=True)
    local_control = destination_root / f"{phase}.json"
    if local_control.exists() and local_control.read_bytes() != body:
        raise ValueError("Existing backup control has changed")
    if not local_control.exists():
        with local_control.open("xb") as handle:
            handle.write(body)
    tar_path = destination_root / f"{phase}.tar"
    if not tar_path.exists():
        partial = destination_root / f"{phase}.tar.partial"
        # --partial is supported by macOS rsync2.6 and current Linux rsync;
        # the final complete-file SHA is always checked independently below.
        transport = shlex.join(prefix[:-1])
        network_run(["rsync", "--partial", "--checksum", "--timeout=30", "-e", transport,
                     f"{host}:{remote_snapshot_root}/{phase}.tar", str(partial)], timeout=600)
        if sha(partial) != control["tar_sha256"] or partial.stat().st_size != control["tar_bytes"]:
            raise ValueError("Transferred partial file differs; not acknowledging")
        partial.rename(tar_path)
    destination = destination_root / phase
    verify_archive(tar_path, control, destination)
    exclusive_report = destination_root / f"{phase}.verified.json"
    report = {**control, "status": "OFFHOST_SHA256_VERIFIED", "verified_at_utc": utcnow(),
              "local_directory": str(destination.resolve()), "local_archive": str(tar_path.resolve())}
    if not exclusive_report.exists():
        exclusive_json(exclusive_report, report)
    return report


def check_git_commit(directory, repository, commit):
    if not re.fullmatch(r"[a-f0-9]{40}", commit or ""):
        raise ValueError("Prestart acknowledgement requires a full Git commit SHA")
    repository = repository.resolve(strict=True)
    directory = directory.resolve(strict=True)
    if not directory.is_relative_to(repository):
        raise ValueError("Prestart extracted archive must be inside its Git repository")
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(repository).as_posix()
        local_blob = subprocess.check_output(["git", "-C", str(repository), "hash-object", str(path)], text=True).strip()
        recorded = subprocess.check_output(["git", "-C", str(repository), "rev-parse", f"{commit}:{relative}"], text=True).strip()
        if local_blob != recorded:
            raise ValueError(f"Git commit does not preserve exact prestart bytes: {relative}")


def acknowledge(host, identity, remote_snapshot_root, evidence, phase, destination_root,
                git_repository=None, git_commit=None, archive_directory=None, expected_campaign=None):
    control = json.loads((destination_root / f"{phase}.json").read_text())
    if expected_campaign and control.get("campaign_sha256") != expected_campaign:
        raise ValueError("Local snapshot does not match explicitly requested campaign SHA256")
    directory = archive_directory or destination_root / phase
    verify_directory(directory, control)
    if phase == "prestart":
        if git_repository is None:
            raise ValueError("Prestart needs --git-repository and --git-commit")
        if not (directory / "PRESTART_MANIFEST.json").is_file():
            raise ValueError("Prestart needs its canonical seal before Git acknowledgement")
        check_git_commit(directory, git_repository, git_commit)
    receipt = {**control, "status": "OFFHOST_SHA256_VERIFIED", "verified_at_utc": utcnow(),
               "git_commit": git_commit, "local_directory": str(directory.resolve())}
    if phase == "prestart":
        receipt["prestart_manifest_sha256"] = sha(directory / "PRESTART_MANIFEST.json")
    # Atomic no-overwrite publication after verifying the remote snapshot's
    # immutable control; an SSH disconnect cannot leave a truncated receipt.
    code = """import json,os,pathlib,sys,tempfile
control=pathlib.Path(sys.argv[1]); target=pathlib.Path(sys.argv[2]); body=sys.stdin.buffer.read(); receipt=json.loads(body)
expected=json.loads(control.read_bytes())
if not all(receipt[k]==expected[k] for k in ('schema_version','phase','campaign_sha256','tar_sha256','manifest_sha256','files','sessions')): raise ValueError('Remote control identity changed')
target.parent.mkdir(parents=True,exist_ok=True)
if target.exists():
 old=json.loads(target.read_bytes())
 if not all(old[k]==receipt[k] for k in ('phase','campaign_sha256','tar_sha256','manifest_sha256','git_commit')): raise ValueError('Existing receipt identity changed')
 if receipt['phase']=='prestart' and old.get('prestart_manifest_sha256')!=receipt.get('prestart_manifest_sha256'): raise ValueError('Prestart seal changed')
else:
 fd,name=tempfile.mkstemp(prefix='.'+target.name+'.',dir=target.parent); temp=pathlib.Path(name)
 try:
  with os.fdopen(fd,'wb') as h: h.write(body); h.flush(); os.fsync(h.fileno())
  os.link(temp,target)
 finally: temp.unlink(missing_ok=True)
 directory=os.open(str(target.parent),os.O_RDONLY)
 try: os.fsync(directory)
 finally: os.close(directory)
"""
    root = remote_path(remote_snapshot_root)
    evidence = remote_path(evidence)
    command = shlex.join(["python3", "-c", code, f"{root}/{phase}.json", f"{evidence}/backup-receipts/{phase}.json"])
    network_run(ssh_prefix(host, identity) + [command], input=payload(receipt))
    return receipt


def verify_receipt(control_path, receipt_path):
    control = json.loads(control_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("status") != "OFFHOST_SHA256_VERIFIED" or any(receipt.get(key) != control.get(key)
            for key in ("schema_version", "phase", "campaign_sha256", "tar_sha256", "manifest_sha256", "files", "sessions"))):
        raise ValueError("Missing or mismatched off-host receipt")
    if control["phase"] == "prestart" and not re.fullmatch(r"[a-f0-9]{40}", receipt.get("git_commit") or ""):
        raise ValueError("Prestart receipt lacks committed archive identity")
    if control["phase"] == "prestart" and not re.fullmatch(r"[a-f0-9]{64}", receipt.get("prestart_manifest_sha256") or ""):
        raise ValueError("Prestart receipt lacks canonical seal identity")
    return receipt


def remote_status(host, identity, evidence):
    evidence = remote_path(evidence)
    code = """import hashlib,json,os,pathlib,sys
r=pathlib.Path(sys.argv[1])
def read(name):
 p=r/name
 return p.read_text().strip() if p.is_file() else None
pid=read('confirm-driver.pid'); alive=False
if pid:
 try: os.kill(int(pid),0); alive=True
 except ProcessLookupError: pass
j=r/'campaign-results/journal.json'; counts={}
if j.is_file():
 for v in json.loads(j.read_bytes())['sessions'].values(): counts[v['status']]=counts.get(v['status'],0)+1
print(json.dumps({'campaign_sha256':hashlib.sha256((r/'campaign.json').read_bytes()).hexdigest(),'waiting_phase':read('WAITING_FOR_OFFHOST_BACKUP'),'driver_exit':read('confirm-driver.exit'),'driver_alive':alive,'counts':counts}))
"""
    command = shlex.join(["python3", "-c", code, evidence])
    return json.loads(network_run(ssh_prefix(host, identity) + [command], capture=True))


def watch(host, identity, evidence, remote_snapshot_root, destination, expected_campaign,
          remote_tool, interval=60):
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "watcher.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another watcher already owns this backup destination") from exc
        return watch_locked(host, identity, evidence, remote_snapshot_root, destination,
                            expected_campaign, remote_tool, interval)


def watch_locked(host, identity, evidence, remote_snapshot_root, destination, expected_campaign,
                 remote_tool, interval=60):
    if not re.fullmatch(r"[a-f0-9]{64}", expected_campaign):
        raise ValueError("Watcher needs an explicit full campaign SHA256")
    if interval < 10:
        raise ValueError("Polling interval must be at least 10 seconds")
    destination.mkdir(parents=True, exist_ok=True)
    log = destination / "watcher.jsonl"
    def record(event, **values):
        row = {"at_utc": utcnow(), "event": event, **values}
        with log.open("ab") as handle:
            handle.write((json.dumps(row, sort_keys=True) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        print(json.dumps(row), flush=True)
    record("WATCHER_STARTED", campaign_sha256=expected_campaign, host=host)
    while True:
        try:
            status = remote_status(host, identity, evidence)
            if status["campaign_sha256"] != expected_campaign:
                raise ValueError("Remote campaign differs; no transfer/acknowledgement performed")
            record("STATUS", **status)
            if status["driver_exit"] not in (None, "0"):
                raise RuntimeError(f"Supervisor exited {status['driver_exit']}; preserve all evidence and investigate")
            phase = status["waiting_phase"]
            if phase == "prestart":
                record("MANUAL_PRESTART_ACK_REQUIRED")
            elif status["driver_exit"] == "0" and not status["driver_alive"]:
                # Final tar predates the EXIT trap. Collect the actual exit
                # markers/closed supervisor log separately after process exit.
                phase = "completion"
                tool = remote_path(remote_tool)
                root = remote_path(remote_snapshot_root)
                code = shlex.join(["python3", tool, "snapshot", "--evidence", remote_path(evidence),
                                   "--snapshot-root", root, "--phase", phase])
                command = f"test -e {root}/completion.json || {code}"
                try:
                    network_run(ssh_prefix(host, identity) + [command], capture=True, timeout=30)
                except subprocess.CalledProcessError as exc:
                    if exc.returncode != 255:
                        raise RuntimeError(f"Completion metadata snapshot failed: {exc.stderr!r}") from exc
                    raise
                pull(host, identity, remote_snapshot_root, phase, destination, expected_campaign)
                acknowledge(host, identity, remote_snapshot_root, evidence, phase, destination,
                            expected_campaign=expected_campaign)
                record("COMPLETE_OFFHOST", campaign_sha256=expected_campaign)
                return
            elif phase is not None:
                if not re.fullmatch(r"block-(?:0[1-9]|10)|final", phase):
                    raise ValueError(f"Unexpected supervisor wait phase: {phase}")
                report = pull(host, identity, remote_snapshot_root, phase, destination, expected_campaign)
                acknowledge(host, identity, remote_snapshot_root, evidence, phase, destination,
                            expected_campaign=expected_campaign)
                record("OFFHOST_BACKUP_ACKNOWLEDGED", phase=phase, files=report["files"], sessions=report["sessions"])
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            # An unavailable transport changes no benchmark state. Every child
            # group has been terminated/reaped before this retry is recorded.
            record("NETWORK_RETRY", error=repr(exc))
        except BaseException as exc:
            record("WATCHER_STOPPED_ATTENTION", error=repr(exc))
            raise
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    create = modes.add_parser("snapshot")
    create.add_argument("--evidence", type=Path, required=True)
    create.add_argument("--snapshot-root", type=Path, required=True)
    create.add_argument("--phase", required=True)
    seal = modes.add_parser("seal-prestart")
    seal.add_argument("--archive-directory", type=Path, required=True)
    seal.add_argument("--control", type=Path, required=True)
    for name in ("pull", "ack"):
        command = modes.add_parser(name)
        command.add_argument("--host", required=True)
        command.add_argument("--identity", type=Path, required=True)
        command.add_argument("--remote-snapshot-root", required=True)
        command.add_argument("--phase", required=True)
        command.add_argument("--destination", type=Path, required=True)
        command.add_argument("--campaign-sha256", required=True)
        if name == "ack":
            command.add_argument("--evidence", required=True)
            command.add_argument("--git-repository", type=Path)
            command.add_argument("--git-commit")
            command.add_argument("--archive-directory", type=Path)
    verify = modes.add_parser("verify-receipt")
    verify.add_argument("--control", type=Path, required=True)
    verify.add_argument("--receipt", type=Path, required=True)
    watcher = modes.add_parser("watch")
    watcher.add_argument("--host", required=True)
    watcher.add_argument("--identity-file", type=Path, required=True)
    watcher.add_argument("--evidence", required=True)
    watcher.add_argument("--remote-snapshot-root", required=True)
    watcher.add_argument("--destination", type=Path, required=True)
    watcher.add_argument("--campaign-sha256", required=True)
    watcher.add_argument("--remote-tool", required=True)
    watcher.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()
    if args.mode == "snapshot":
        result = snapshot(args.evidence, args.snapshot_root, args.phase)
    elif args.mode == "seal-prestart":
        result = seal_prestart(args.archive_directory, json.loads(args.control.read_text()))
    elif args.mode == "pull":
        result = pull(args.host, args.identity, args.remote_snapshot_root, args.phase, args.destination, args.campaign_sha256)
    elif args.mode == "ack":
        result = acknowledge(args.host, args.identity, args.remote_snapshot_root, args.evidence,
                             args.phase, args.destination, args.git_repository, args.git_commit, args.archive_directory,
                             args.campaign_sha256)
    elif args.mode == "watch":
        result = watch(args.host, args.identity_file, args.evidence, args.remote_snapshot_root,
                       args.destination, args.campaign_sha256, args.remote_tool, args.interval_seconds)
    else:
        result = verify_receipt(args.control, args.receipt)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
