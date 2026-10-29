#!/usr/bin/env python3
"""Offline staging/catalog helpers for the exact completed private H100 study.

No network, credential, benchmark, VM, upload, publication or deletion command.
Original archives and historical receipts are never rewritten. A local stage
does not establish independent private download verification.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

try:
    from . import research_backup as common
    from . import contention_backup
except ImportError:
    import research_backup as common
    import contention_backup

GIB = 1024 ** 3
LIMIT = 2 * GIB
CAMPAIGN = "3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641"
PRESTART = "ea57fa1221b905e8ee287fe7860285c7a37535b084f13ce3dbbbca5ac4e29ace"
PROFILE = "a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69"
RECONSTRUCTION = "6ef171eaaebbda72bfd928b2b12c468a19529e15ec86224d3921c1fcad281b2d"
REPLAY = "b59450d0731180b5b9cc642c5174f872ab460ab585445b87230175a07c48655c"
AUDIT = "cb03c639c0b428f50f07a142d1201e8a3c39ad82ea21a2192db95d4ebfc5585d"
CONTENTION = "0cad03b7ab2edc7e0d62dbcc7b84f8fa0ea51325aba41eb354b5baae0c51a2d3"
SCIENTIFIC_CHECKPOINT = "1f9b67f7675f9438a0d1cb373f2684714ca2e1e5a199db196b4b297766a9cf72"
TAG = "h100-confirmatory-gclocker100-20261027-evidence-v1"
REPO = "ejiod2025/gpu-spark-polars-study"
PHASES = ["prestart"] + [f"block-{i:02d}" for i in range(1, 11)] + ["final", "completion"]


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return contention_backup.read(path)


def ref(path, root):
    path = Path(path).absolute()
    relative = common.safe_file(path, root)
    before = path.stat()
    value = {"path": relative, "bytes": before.st_size, "sha256": common.sha(path)}
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("Source changed while hashing: " + relative)
    return value


def reserve(path, needed=0):
    free = shutil.disk_usage(path).free
    if free - needed < 20 * GIB:
        raise ValueError(f"20 GiB reserve would be crossed: free={free}, needed={needed}")
    return free


def tree(directory, root):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Expected an existing regular allowlisted directory")
    return {common.safe_file(p, root): ref(p, root) for p in sorted(common.tree_files(directory))}


def match(path, expected, root):
    value = ref(path, root)
    if value["sha256"] != expected:
        raise ValueError("Exact genuine input hash differs: " + value["path"])
    return value


def hash_stream(paths):
    digest, count = hashlib.sha256(), 0
    for path in paths:
        with Path(path).open("rb") as handle:
            for body in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(body)
                count += len(body)
    return {"bytes": count, "sha256": digest.hexdigest()}


class PartsReader(io.RawIOBase):
    def __init__(self, paths):
        self.paths, self.handle = iter(paths), None

    def readable(self):
        return True

    def read(self, size=-1):
        if size < 0:
            raise ValueError("Unbounded stream reads are refused")
        result = bytearray()
        while len(result) < size:
            if self.handle is None:
                path = next(self.paths, None)
                if path is None:
                    break
                self.handle = Path(path).open("rb")
            body = self.handle.read(size - len(result))
            if body:
                result.extend(body)
            else:
                self.handle.close()
                self.handle = None
        return bytes(result)

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None
        super().close()


def verify_bundle(bundle, root, records):
    manifest_path = bundle / "BUNDLE_MANIFEST.json"
    manifest_ref = ref(manifest_path, root)
    manifest = read(manifest_path)
    eligible = manifest.get("eligibility", {})
    if (manifest.get("schema_version") != "research-evidence-bundle-v1"
            or manifest.get("status") != "PASS" or eligible.get("synthetic") is not False
            or eligible.get("campaign_sha256") != CAMPAIGN or eligible.get("audit_sha256") != AUDIT
            or eligible.get("canonical_prestart_archive", {}).get("prestart_manifest_sha256") != PRESTART):
        raise ValueError("Exact genuine completed raw bundle PASS is required")
    parts, names = [], []
    for part in manifest["parts"]:
        name = part["path"]
        common.safe_relative(name)
        if Path(name).name != name or name in names:
            raise ValueError("Bundle part paths must be unique flat names")
        actual = ref(bundle / name, root)
        if any(actual[k] != part[k] for k in ("bytes", "sha256")) or actual["bytes"] >= LIMIT:
            raise ValueError("Bundle part identity/asset size differs")
        names.append(name)
        parts.append(bundle / name)
    expected_set = set(names) | {"BUNDLE_MANIFEST.json", "CHUNKS.sha256", "REASSEMBLE.md", "AUDIT_COMPLETE.json"}
    if {p.name for p in bundle.iterdir()} != expected_set:
        raise ValueError("Bundle directory contains missing/extra files")
    joined = hash_stream(parts)
    if joined != {"bytes": manifest["archive_bytes"], "sha256": manifest["archive_sha256"]}:
        raise ValueError("Joined compressed stream differs")
    if common.sha(bundle / "AUDIT_COMPLETE.json") != AUDIT:
        raise ValueError("Bundle copied audit differs")
    expected_chunks = "".join(f"{p['sha256']}  {p['path']}\n" for p in manifest["parts"])
    if (bundle / "CHUNKS.sha256").read_text() != expected_chunks:
        raise ValueError("Bundle chunk checksum instructions differ")
    reserve(records, manifest["uncompressed_file_bytes"] + 128 * 1024 ** 2)
    observed = {}
    with tempfile.TemporaryDirectory(dir=records, prefix="bundle-local-verification-") as temporary:
        extraction = Path(temporary)
        with PartsReader(parts) as source, gzip.GzipFile(fileobj=source) as decompressed:
            with tarfile.open(fileobj=decompressed, mode="r|") as archive:
                for member in archive:
                    common.safe_relative(member.name)
                    if not member.isfile() or not member.name.startswith("evidence/"):
                        raise ValueError("Only contained regular bundle members are allowed")
                    name = member.name[len("evidence/"):]
                    common.safe_relative(name)
                    if name in observed or name not in manifest["files"]:
                        raise ValueError("Duplicate or unlisted bundle member")
                    expected = manifest["files"][name]
                    if member.size != expected["bytes"]:
                        raise ValueError("Bundle member byte count differs")
                    destination = extraction / member.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open("xb") as output, archive.extractfile(member) as handle:
                        shutil.copyfileobj(handle, output, 8 * 1024 * 1024)
                    os.chmod(destination, 0o755 if expected["executable"] else 0o644)
                    actual = ref(destination, extraction / "evidence")
                    if any(actual[k] != expected[k] for k in ("bytes", "sha256")):
                        raise ValueError("Extracted bundle member bytes differ: " + name)
                    observed[name] = {k: actual[k] for k in ("bytes", "sha256")}
        if set(observed) != set(manifest["files"]):
            raise ValueError("Bundle extraction inventory differs")
        rehashed = tree(extraction / "evidence", extraction / "evidence")
        if {k: {f: v[f] for f in ("bytes", "sha256")} for k, v in rehashed.items()} != observed:
            raise ValueError("Fresh extracted bundle inventory changed")
        record = {"schema_version": "h100-local-bundle-verification-v1", "status": "LOCAL_BUNDLE_BYTES_VERIFIED",
                  "verified_at_utc": now(), "bundle_manifest": manifest_ref, "joined_compressed_stream": joined,
                  "parts_verified": len(parts), "files_verified": len(observed), "extracted_files": observed,
                  "safe_member_checks": "unique contained regular files; exact trusted inventory; SHA256 and bytes",
                  "extraction": "New temporary tree, rehashed before this record; tree removed after durable record",
                  "private_download_verification": False}
        common.exclusive_json(records / "BUNDLE_LOCAL_VERIFICATION.json", record)
    return manifest


def split_original(source, stage, prefix, expected, root, piece_bytes=GIB):
    if piece_bytes <= 0 or piece_bytes >= LIMIT:
        raise ValueError("Split piece size must be positive and below 2 GiB")
    reserve(stage, expected["bytes"])
    before = ref(source, root)
    if any(before[k] != expected[k] for k in ("bytes", "sha256")):
        raise ValueError("Original archive differs from historical control")
    parts = []
    with source.open("rb") as handle:
        index = 0
        while True:
            first = handle.read(min(8 * 1024 * 1024, piece_bytes))
            if not first:
                break
            target = stage / f"{prefix}.part-{index:05d}"
            with target.open("xb") as output:
                output.write(first)
                left = piece_bytes - len(first)
                while left:
                    body = handle.read(min(8 * 1024 * 1024, left))
                    if not body:
                        break
                    output.write(body)
                    left -= len(body)
            parts.append(ref(target, root))
            index += 1
    joined = hash_stream([root / p["path"] for p in parts])
    if joined != expected or ref(source, root) != before:
        raise ValueError("Split/join identity differs or original changed")
    return parts


def supplemental(stage, name, files, root):
    target = stage / name
    reserve(stage, sum(f["bytes"] for f in files.values()))
    with target.open("xb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for relative, expected in sorted(files.items()):
                    path = root / relative
                    if ref(path, root) != expected:
                        raise ValueError("Allowlisted source changed before archiving")
                    info = tarfile.TarInfo(relative)
                    info.size, info.mode, info.mtime = expected["bytes"], 0o644, 0
                    with path.open("rb") as source:
                        archive.addfile(info, source)
    verified = {}
    with tarfile.open(target, "r:gz") as archive:
        for member in archive:
            common.safe_relative(member.name)
            if not member.isfile() or member.name in verified or member.name not in files:
                raise ValueError("Unsafe/duplicate/unlisted supplemental member")
            with archive.extractfile(member) as handle:
                digest = hashlib.sha256()
                count = 0
                for body in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    digest.update(body)
                    count += len(body)
            expected = files[member.name]
            if count != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
                raise ValueError("Supplemental member bytes differ")
            verified[member.name] = expected
    if verified != files or any(ref(root / k, root) != v for k, v in files.items()):
        raise ValueError("Supplemental inventory/source drift")
    return ref(target, root)


def stage_study(root, stage, records):
    root = root.resolve(strict=True)
    stage, records = stage.absolute(), records.absolute()
    for path in (stage, records):
        if path.exists() or not path.is_relative_to(root):
            raise ValueError("New stage and records must be absent and inside this repository")
        if path.resolve().is_relative_to(root / "rerun/gclocker100-20261027/reconstruction-v1/harvest"):
            raise ValueError("Staging must remain outside the immutable harvest")
    base = root / "rerun/gclocker100-20261027"
    reconstruction, replay, bundle = [base / n for n in ("reconstruction-v1", "replay-v1", "bundle-v1")]
    bindings = {
        "reconstruction_manifest": match(reconstruction / "RECONSTRUCTION_MANIFEST.json", RECONSTRUCTION, root),
        "replay_manifest": match(replay / "REPLAY_MANIFEST.json", REPLAY, root),
        "complete_audit": match(replay / "complete-audit/AUDIT_COMPLETE.json", AUDIT, root),
        "canonical_prestart_manifest": match(root / "evidence-research/20261027/h100-confirmatory-gclocker100-v1/PRESTART_MANIFEST.json", PRESTART, root),
        "contention_terminal_replay": match(root / "rerun/contention-20261028/offline-terminal-audit-v1/CONTENTION_REPLAY.json", CONTENTION, root)}
    terminal = read(root / bindings["contention_terminal_replay"]["path"])
    if (read(replay / "REPLAY_MANIFEST.json").get("status") != "PASS"
            or read(replay / "REPLAY_MANIFEST.json").get("synthetic") is not False
            or read(replay / "complete-audit/AUDIT_COMPLETE.json").get("status") != "PASS"
            or terminal.get("status") != "COMPLETE" or terminal.get("integrity_status") != "PASS"
            or terminal.get("synthetic") is not False):
        raise ValueError("Genuine terminal replay/audit gates are required")
    # Do not create any stage until the actual bundle manifest has been published.
    if not (bundle / "BUNDLE_MANIFEST.json").is_file():
        raise ValueError("Bundle packaging is not complete; no stage created")
    reserve(root, 12 * GIB)
    records.mkdir(parents=True)
    print(json.dumps({"phase": "local-bundle-stream-and-safe-extraction-verification"}), flush=True)
    bundle_manifest = verify_bundle(bundle, root, records)
    print(json.dumps({"phase": "local-bundle-verified", "files": bundle_manifest["file_count"]}), flush=True)
    bindings["bundle_manifest"] = ref(bundle / "BUNDLE_MANIFEST.json", root)
    stage.mkdir(parents=True)
    assets, transports, categories = [], [], {}

    def link(source, name, role, restoration=None):
        identity = ref(source, root)
        target = stage / name
        if Path(name).name != name or identity["bytes"] >= LIMIT:
            raise ValueError("Asset names must be flat and files below 2 GiB")
        os.link(source, target)
        if ref(target, root)["sha256"] != identity["sha256"]:
            raise ValueError("Staged hardlink identity differs")
        assets.append({"name": name, "role": role, "source": identity,
                       "restoration_path": restoration or identity["path"],
                       "bytes": identity["bytes"], "sha256": identity["sha256"], "staging_method": "hardlink"})

    def add(category, paths):
        values = categories.setdefault(category, {})
        for path in paths:
            item = ref(path, root)
            if item["path"] in values:
                raise ValueError("Duplicate supplemental source path")
            values[item["path"]] = item

    for part in bundle_manifest["parts"]:
        link(bundle / part["path"], part["path"], "complete-raw-bundle-part")
    for name in ("BUNDLE_MANIFEST.json", "CHUNKS.sha256", "REASSEMBLE.md", "AUDIT_COMPLETE.json"):
        link(bundle / name, name, "complete-raw-bundle-metadata")
    for cohort, backup, phases, manifest_name in [
            ("recovery", base / "raw-backups", PHASES, "BACKUP_MANIFEST.json"),
            ("contention", root / "rerun/contention-20261028/raw-backups", ["pilot", "main"], "CONTENTION_BACKUP_MANIFEST.json")]:
        for phase in phases:
            control_path, receipt_path, source = [backup / (phase + suffix) for suffix in (".json", ".verified.json", ".tar")]
            control, receipt = read(control_path), read(receipt_path)
            expected = {"bytes": control["tar_bytes"], "sha256": control["tar_sha256"]}
            if (control["phase"] != phase or receipt.get("status") != "OFFHOST_SHA256_VERIFIED"
                    or any(receipt.get(k) != v for k, v in control.items())
                    or Path(receipt["local_archive"]) != source
                    or Path(receipt["local_directory"]) != backup / phase):
                raise ValueError("Historical transport control/receipt differs")
            if cohort == "recovery":
                if control["campaign_sha256"] != CAMPAIGN:
                    raise ValueError("Recovery transport campaign differs")
                common.verify_directory(backup / phase, control)
            else:
                contention_backup.validate_control(control, terminal["plan_sha256"], phase)
                contention_backup.verify_directory(backup / phase, control)
            original = ref(source, root)
            if any(original[k] != expected[k] for k in expected):
                raise ValueError("Original transport SHA256/bytes differs")
            prefix = f"{cohort}-{phase}.tar"
            if original["bytes"] < LIMIT:
                link(source, prefix, f"{cohort}-original-transport")
                names = [prefix]
            else:
                pieces = split_original(source, stage, prefix, expected, root)
                names = []
                for piece in pieces:
                    name = Path(piece["path"]).name
                    names.append(name)
                    assets.append({"name": name, "role": f"{cohort}-original-transport-piece", "source": original,
                                   "restoration_path": original["path"] + ".parts/" + name,
                                   "bytes": piece["bytes"], "sha256": piece["sha256"], "staging_method": "new-binary-piece"})
            transports.append({"cohort": cohort, "phase": phase, "original_archive": original,
                               "restoration_path": original["path"], "ordered_asset_names": names,
                               "control": ref(control_path, root), "original_receipt": ref(receipt_path, root),
                               "local_original_extracted_inventory_verified": True})
            print(json.dumps({"phase": "original-transport-staged", "cohort": cohort, "transport": phase,
                              "assets": len(names), "bytes": original["bytes"]}), flush=True)
            add(f"{cohort}-original-phase-metadata", [control_path, receipt_path, backup / phase / manifest_name])
    seal = base / "raw-backups/prestart/PRESTART_MANIFEST.json"
    if seal.is_file():
        add("recovery-original-phase-metadata", [seal])
    lineage = read(reconstruction / "RECONSTRUCTION_MANIFEST.json")["lineage_files"]
    actual_lineage = tree(reconstruction / "lineage", root)
    if {str(Path(k).relative_to(reconstruction.relative_to(root) / "lineage")): {f: v[f] for f in ("bytes", "sha256")} for k, v in actual_lineage.items()} != lineage:
        raise ValueError("Reconstruction lineage differs from exact manifest")
    add("recovery-reconstruction-lineage", [reconstruction / "RECONSTRUCTION_MANIFEST.json"] + [root / p for p in actual_lineage])
    replay_record = read(replay / "REPLAY_MANIFEST.json")
    expected_replay = set(replay_record["output_files_sha256"]) | {"REPLAY_MANIFEST.json"}
    actual_replay = tree(replay, root)
    if {str(Path(p).relative_to(replay.relative_to(root))) for p in actual_replay} != expected_replay:
        raise ValueError("Completed replay contains missing/extra files")
    for p, digest in replay_record["output_files_sha256"].items():
        match(replay / p, digest, root)
    add("recovery-final-replay", [root / p for p in actual_replay])
    contention = root / "rerun/contention-20261028"
    for relative in ("final-metadata-v1", "final-handoff-v1", "held-original"):
        add("contention-terminal-metadata", [root / p for p in tree(contention / relative, root)])
    held = root / "evidence-research/20261028/h100-contention-held-original"
    add("contention-terminal-metadata", [root / p for p in tree(held, root)])
    checkpoint = root / "evidence-research/20261028/h100-contention-terminal-checkpoint"
    checkpoint_files = tree(checkpoint, root)
    for p in checkpoint_files:
        relative = Path(p).relative_to(checkpoint.relative_to(root))
        category = ("contention-terminal-replay" if relative.parts[0] == "offline-replay" else
                    "contention-final-figures" if relative.parts[0] == "figures" else "contention-terminal-metadata")
        add(category, [root / p])
    for relative, category in [("offline-terminal-audit-v1", "contention-terminal-replay"), ("figures-final-v1", "contention-final-figures")]:
        add(category, [root / p for p in tree(contention / relative, root)])
    historical = root / "rerun/replacement-20261027/incidents/aborted-20261027T202748Z"
    link(historical / "evidence.tar.gz", "historical-default-jvm-abort-evidence.tar.gz", "historical-default-jvm-failure-excluded-from-recovery")
    add("historical-default-jvm-abort-metadata", [historical / n for n in ("INCIDENT_MANIFEST.json", "OFFHOST_VERIFIED.json", "POST_ABORT_INPUT_GATE.json", "TRANSFER.json")])
    supplemental_manifest = {}
    for category, files in sorted(categories.items()):
        name = category + ".tar.gz"
        identity = supplemental(stage, name, files, root)
        assets.append({"name": name, "role": category, "source": {"allowlist_category": category},
                       "restoration_path": "supplemental/" + name, "bytes": identity["bytes"],
                       "sha256": identity["sha256"], "staging_method": "new-explicit-allowlist-archive"})
        supplemental_manifest[name] = {"archive": identity, "members": files, "member_count": len(files),
                                       "restoration": "Extract only safely validated regular members to the recorded repository-relative paths"}
        print(json.dumps({"phase": "supplemental-archive-verified", "name": name, "members": len(files),
                          "bytes": identity["bytes"]}), flush=True)
    common.exclusive_json(records / "SUPPLEMENTAL_FILES_MANIFEST.json", {"schema_version": "h100-private-supplemental-files-v1", "archives": supplemental_manifest})
    all_sources = {p: v for files in categories.values() for p, v in files.items()}
    common.exclusive_json(records / "STAGING_SOURCE_ALLOWLIST.json", {"schema_version": "h100-private-staging-source-allowlist-v1", "files": all_sources, "transports": transports})
    for name in ("SUPPLEMENTAL_FILES_MANIFEST.json", "STAGING_SOURCE_ALLOWLIST.json", "BUNDLE_LOCAL_VERIFICATION.json"):
        link(records / name, name, "private-storage-local-provenance")
    if len(assets) + 1 > 1000 or any(a["bytes"] >= LIMIT for a in assets):
        raise ValueError("Release service asset count/size bound exceeded")
    if {p.name for p in stage.iterdir()} != {a["name"] for a in assets}:
        raise ValueError("Exact stage asset set differs")
    statement = {"schema_version": "h100-private-staging-v1", "status": "LOCAL_STAGING_VERIFIED_CATALOG_COMMIT_PENDING",
                 "created_at_utc": now(), "repository": REPO, "candidate_release_tag": TAG,
                 "stage_path": str(stage), "records_path": str(records), "bindings": bindings,
                 "campaign_sha256": CAMPAIGN, "prestart_sha256": PRESTART, "execution_profile_sha256": PROFILE,
                 "contention_plan_sha256": terminal["plan_sha256"], "contention_prestart_sha256": terminal["prestart_sha256"],
                 "contention_replay_sha256": CONTENTION, "assets": assets, "original_transports": transports,
                 "asset_count_without_catalog": len(assets), "asset_bytes_without_catalog": sum(a["bytes"] for a in assets),
                 "free_bytes_after_staging": reserve(root), "minimum_free_bytes": 20 * GIB,
                 "scientific_evidence_commit_not_supplied": True, "release_mutations_performed": False,
                 "private_download_verification_performed": False,
                 "tool": ref(Path(__file__), root)}
    common.exclusive_json(records / "STAGING_MANIFEST.json", statement)
    return statement


def verify_git_reference(root, commit, reference):
    """Verify historical bytes in A even when a current operational tool advanced."""
    common.safe_relative(reference["path"])
    body = subprocess.check_output(["git", "show", commit + ":" + reference["path"]], cwd=root)
    if len(body) != reference["bytes"] or hashlib.sha256(body).hexdigest() != reference["sha256"]:
        raise ValueError("Scientific commit bytes differ: " + reference["path"])
    return reference


def catalog(root, records, commit, output):
    if not output.is_relative_to(root) or output.exists() or output.is_symlink():
        raise ValueError("Catalog must be a new regular file within this repository")
    for parent in output.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("Catalog parents cannot be symlinks")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("The actual full scientific-evidence commit A is required")
    subprocess.run(["git", "cat-file", "-e", commit + "^{commit}"], cwd=root, check=True)
    staged = read(records / "STAGING_MANIFEST.json")
    if staged.get("status") != "LOCAL_STAGING_VERIFIED_CATALOG_COMMIT_PENDING":
        raise ValueError("Completed verified staging manifest is required")
    stage = Path(staged["stage_path"])
    if {p.name for p in stage.iterdir()} != {a["name"] for a in staged["assets"]}:
        raise ValueError("Stage contains missing/extra files before catalog finalization")
    for asset in staged["assets"]:
        actual = ref(stage / asset["name"], root)
        if any(actual[k] != asset[k] for k in ("bytes", "sha256")):
            raise ValueError("Staged bytes changed before catalog finalization")
    staging_ref = ref(records / "STAGING_MANIFEST.json", root)
    fresh_ref = ref(records / "FRESH_STAGED_BYTES_VERIFICATION.json", root)
    correction_ref = ref(records / "STAGING_SOURCE_CORRECTION.json", root)
    fresh, correction = read(root / fresh_ref["path"]), read(root / correction_ref["path"])
    if (fresh.get("status") != "LOCAL_STAGED_BYTES_REVERIFIED"
            or fresh.get("original_staging_manifest") != staging_ref
            or fresh.get("assets_verified") != len(staged["assets"])
            or fresh.get("bytes_verified") != sum(a["bytes"] for a in staged["assets"])
            or fresh.get("private_download_verification_performed") is not False
            or correction.get("original_staging_manifest") != staging_ref
            or correction.get("fresh_local_verification") != fresh_ref
            or correction.get("original_manifest_unchanged") is not True):
        raise ValueError("Fresh staging verification/source correction do not bind exact original records")
    for asset in staged["assets"]:
        checked = fresh["assets"][asset["name"]]
        if any(checked[k] != asset[k] for k in ("bytes", "sha256")):
            raise ValueError("Fresh verification asset identity differs")
    provenance = {"original_staging_manifest": staging_ref, "fresh_staged_bytes_verification": fresh_ref,
                  "staging_source_correction": correction_ref,
                  "exact_executed_staging_source": correction["actual_executed_staging_source"],
                  "staging_source_provenance_note": correction["source_provenance_note"],
                  "fresh_verification_helper_source_in_A": fresh["verification_helper_source"],
                  "executed_fresh_verification_script": fresh["executed_verification_script"]}
    for reference in provenance.values():
        verify_git_reference(root, commit, reference)
    checkpoint = root / "evidence-research/20261028/h100-final-scientific-checkpoint"
    checkpoint_ref = match(checkpoint / "CHECKPOINT_MANIFEST.json", SCIENTIFIC_CHECKPOINT, root)
    verify_git_reference(root, commit, checkpoint_ref)
    checkpoint_files = read(checkpoint / "CHECKPOINT_MANIFEST.json")["files"]
    checkpoint_git_paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", commit, "--", checkpoint.relative_to(root).as_posix()],
        cwd=root, text=True).splitlines()
    expected_paths = {str(checkpoint.relative_to(root) / p) for p in checkpoint_files} | {checkpoint_ref["path"]}
    if set(checkpoint_git_paths) != expected_paths:
        raise ValueError("Scientific commit checkpoint inventory differs")
    for name, identity in checkpoint_files.items():
        verify_git_reference(root, commit, {"path": str(checkpoint.relative_to(root) / name), **identity})
    for path, expected in [
            ("evidence-research/20261027/h100-confirmatory-gclocker100-v1/PRESTART_MANIFEST.json", PRESTART),
            ("evidence-research/20261028/h100-contention-v1/CONTENTION_PRESTART_MANIFEST.json", staged["contention_prestart_sha256"])]:
        body = subprocess.check_output(["git", "show", commit + ":" + path], cwd=root)
        if hashlib.sha256(body).hexdigest() != expected:
            raise ValueError("Scientific commit does not preserve exact canonical identity")
    if len(staged["assets"]) + 1 != 42:
        raise ValueError("This reviewed genuine stage must contain exactly 42 release assets including catalog")
    bindings = {**staged["bindings"], "scientific_checkpoint_manifest": checkpoint_ref}
    value = {"schema_version": "h100-private-evidence-asset-catalog-v1", "status": "CATALOGED_LOCAL_BYTES",
             "created_at_utc": now(), "repository": REPO, "repository_url": "https://github.com/" + REPO,
             "release_tag": TAG, "scientific_evidence_commit": commit,
             "commit_binding": "Commit A above preserves scientific evidence; this catalog is committed in child B. Target release at actual B; record B and this catalog SHA256 in later retrieval receipts. No own-containing-commit hash is embedded.",
             "catalog_asset": {"name": "ASSET_CATALOG.json", "restoration_path": str(output.relative_to(root)),
                               "identity": "Exact Git blob in release target B; byte count and SHA256 recorded externally in actual retrieval receipts"},
             "scope": "Scientific raw-evidence preservation catalog; no assertion of completed paper, public deployment, release upload or independent private download verification",
             "bindings": bindings, "campaign_sha256": CAMPAIGN, "prestart_sha256": PRESTART,
             "execution_profile_sha256": PROFILE, "contention_plan_sha256": staged["contention_plan_sha256"],
             "contention_prestart_sha256": staged["contention_prestart_sha256"], "contention_replay_sha256": CONTENTION,
             "assets": staged["assets"], "original_transports": staged["original_transports"],
             "staging_manifest": staging_ref, "provenance_preserved_in_scientific_commit_A": provenance,
             "scientific_checkpoint_files_verified_in_A": len(expected_paths),
             "cataloging_tool_source_for_child_B": ref(Path(__file__), root),
             "historical_failure_scope": "Default-JVM incident retained as excluded historical failure; never pooled with recovery observations",
             "release_asset_count_including_catalog": len(staged["assets"]) + 1,
             "private_download_verification_performed": False}
    common.exclusive_json(output, value)
    os.link(output, stage / "ASSET_CATALOG.json")
    return {"catalog": ref(output, root), "asset_count": len(staged["assets"]) + 1,
            "status": "CATALOGED_LOCAL_BYTES_AWAITING_REVIEWED_PUSHED_COMMIT_B"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    commands = parser.add_subparsers(dest="command", required=True)
    staging = commands.add_parser("stage")
    staging.add_argument("--stage", type=Path, required=True)
    staging.add_argument("--records", type=Path, required=True)
    final = commands.add_parser("catalog")
    final.add_argument("--records", type=Path, required=True)
    final.add_argument("--scientific-evidence-commit", required=True)
    final.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.repository_root.resolve(strict=True)
    if args.command == "stage":
        result = stage_study(root, args.stage, args.records)
        print(json.dumps({k: result[k] for k in ("status", "stage_path", "records_path", "asset_count_without_catalog", "asset_bytes_without_catalog", "free_bytes_after_staging")}))
    else:
        print(json.dumps(catalog(root, args.records.resolve(strict=True), args.scientific_evidence_commit, args.output.absolute())))


if __name__ == "__main__":
    main()
