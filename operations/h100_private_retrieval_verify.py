#!/usr/bin/env python3
"""Offline safe extraction/replay verification of actual fresh private downloads.

Only newly created verification trees are removed, after durable verification
records. Original archives/trees and all independent downloaded tars remain.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

try:
    from . import h100_private_archive as archive
    from . import h100_private_release_transfer as transfer
    from . import research_backup as common
    from . import contention_backup
except ImportError:
    import h100_private_archive as archive
    import h100_private_release_transfer as transfer
    import research_backup as common
    import contention_backup

ROOT, BASE, RECORDS, DOWNLOAD = transfer.ROOT, transfer.BASE, transfer.RECORDS, transfer.DOWNLOAD
TREES = BASE / "retrieval-verification-trees-v1"
RAW = BASE / "retrieved-raw-bundle-v1"
CANONICAL = BASE / "retrieved-canonical-prestart-v1"
REPLAY = BASE / "retrieved-raw-replay-v1"
REASSEMBLED = BASE / "release-retrieval-reassembled-v1"


def save(name, value):
    common.exclusive_json(RECORDS / name, value)
    return archive.ref(RECORDS / name, ROOT)


def cleanup_new_tree(path, reason):
    allowed = path == RAW or path.parent == TREES
    if not allowed or path.is_symlink() or not path.is_dir():
        raise ValueError("Cleanup is restricted to this helper's newly created verification trees")
    with (RECORDS / "VERIFIED_TEMPORARY_TREE_CLEANUP.jsonl").open("a") as handle:
        handle.write(json.dumps({"cleanup_started_at_utc": archive.now(), "path": str(path), "reason": reason,
                                 "original_evidence_removed": False}) + "\n")
    shutil.rmtree(path)
    with (RECORDS / "VERIFIED_TEMPORARY_TREE_CLEANUP.jsonl").open("a") as handle:
        handle.write(json.dumps({"cleanup_completed_at_utc": archive.now(), "path": str(path),
                                 "path_absent_after_cleanup": not path.exists(), "original_evidence_removed": False}) + "\n")


def trusted_downloads():
    catalog_ref, expected = transfer.trusted()
    receipt = archive.read(RECORDS / "DOWNLOADED_ASSETS.json")
    if receipt["status"] != "AUTHENTICATED_ASSET_BYTES_VERIFIED" or receipt["catalog"] != catalog_ref:
        raise ValueError("Actual authenticated downloaded-byte record is required")
    if {p.name for p in DOWNLOAD.iterdir()} != set(expected):
        raise ValueError("Actual retrieved asset set differs")
    catalog = archive.read(ROOT / transfer.CATALOG_PATH)
    return catalog, receipt


def extract_members(tar_path, destination, expected):
    if destination.exists():
        raise ValueError("Fresh supplemental extraction destination already exists")
    archive.reserve(ROOT, sum(v["bytes"] for v in expected.values()) + 1024 ** 2)
    destination.mkdir()
    observed = {}
    with tarfile.open(tar_path, "r:gz") as opened:
        for member in opened:
            common.safe_relative(member.name)
            if not member.isfile() or member.name in observed or member.name not in expected:
                raise ValueError("Unsafe/duplicate/unlisted supplemental member")
            identity = expected[member.name]
            if member.size != identity["bytes"]:
                raise ValueError("Supplemental member byte count differs")
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output, opened.extractfile(member) as source:
                shutil.copyfileobj(source, output, 8 * 1024 * 1024)
            result = archive.ref(target, destination)
            if any(result[k] != identity[k] for k in ("bytes", "sha256")):
                raise ValueError("Supplemental extracted bytes differ")
            observed[member.name] = result
    if set(observed) != set(expected):
        raise ValueError("Supplemental exact extracted inventory differs")
    rehashed = archive.tree(destination, destination)
    if rehashed != observed:
        raise ValueError("Supplemental extracted inventory changed")
    return observed


def prepare():
    tool = archive.ref(Path(__file__), ROOT)
    catalog, downloaded = trusted_downloads()
    if TREES.exists() or RAW.exists() or CANONICAL.exists() or REPLAY.exists():
        raise ValueError("All initial verification/extraction/replay paths must be new")
    TREES.mkdir()
    supplement = archive.read(DOWNLOAD / "SUPPLEMENTAL_FILES_MANIFEST.json")["archives"]
    supplemental_results = {}
    for name, metadata in sorted(supplement.items()):
        expected_asset = downloaded["downloaded_assets"][name]["downloaded_file"]
        if archive.ref(DOWNLOAD / name, ROOT) != expected_asset:
            raise ValueError("Downloaded supplemental bytes changed")
        destination = TREES / (name + ".extracted")
        observed = extract_members(DOWNLOAD / name, destination, metadata["members"])
        result = save("SUPPLEMENTAL_" + name + ".verified.json", {
            "schema_version": "h100-retrieved-supplemental-verification-v1", "status": "DOWNLOADED_ARCHIVE_AND_MEMBERS_VERIFIED",
            "verified_at_utc": archive.now(), "archive": expected_asset, "member_count": len(observed), "members": observed,
            "fresh_safe_extraction": True, "inventory_rehashed": True, "verification_tool": tool})
        supplemental_results[name] = result
        cleanup_new_tree(destination, "Exact supplemental archive/member inventory durably verified")
    manifest = archive.read(DOWNLOAD / "BUNDLE_MANIFEST.json")
    if (manifest["status"] != "PASS" or manifest["eligibility"]["synthetic"] is not False
            or manifest["eligibility"]["campaign_sha256"] != archive.CAMPAIGN
            or manifest["eligibility"]["audit_sha256"] != archive.AUDIT):
        raise ValueError("Genuine retrieved completed bundle is required")
    parts = []
    for part in manifest["parts"]:
        common.safe_relative(part["path"])
        actual = archive.ref(DOWNLOAD / part["path"], ROOT)
        if any(actual[k] != part[k] for k in ("bytes", "sha256")):
            raise ValueError("Retrieved raw bundle part differs")
        parts.append(DOWNLOAD / part["path"])
    joined = archive.hash_stream(parts)
    if joined != {"bytes": manifest["archive_bytes"], "sha256": manifest["archive_sha256"]}:
        raise ValueError("Retrieved joined gzip differs")
    archive.reserve(ROOT, manifest["uncompressed_file_bytes"] + 256 * 1024 ** 2)
    RAW.mkdir()
    raw_files = {}
    with archive.PartsReader(parts) as stream, gzip.GzipFile(fileobj=stream) as compressed:
        with tarfile.open(fileobj=compressed, mode="r|") as opened:
            for member in opened:
                common.safe_relative(member.name)
                if not member.isfile() or not member.name.startswith("evidence/"):
                    raise ValueError("Unsafe retrieved bundle member")
                name = member.name[len("evidence/"):]
                common.safe_relative(name)
                if name in raw_files or name not in manifest["files"]:
                    raise ValueError("Duplicate/unlisted retrieved bundle member")
                expected = manifest["files"][name]
                if member.size != expected["bytes"]:
                    raise ValueError("Retrieved bundle member byte count differs")
                target = RAW / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output, opened.extractfile(member) as source:
                    shutil.copyfileobj(source, output, 8 * 1024 * 1024)
                os.chmod(target, 0o755 if expected["executable"] else 0o644)
                actual = archive.ref(target, RAW / "evidence")
                if any(actual[k] != expected[k] for k in ("bytes", "sha256")):
                    raise ValueError("Retrieved raw file hash differs")
                raw_files[name] = actual
    if set(raw_files) != set(manifest["files"]) or archive.tree(RAW / "evidence", RAW / "evidence") != raw_files:
        raise ValueError("Retrieved raw inventory differs/changed")
    raw_record = save("RAW_BUNDLE_RETRIEVAL_VERIFICATION.json", {
        "schema_version": "h100-retrieved-raw-bundle-verification-v1", "status": "RETRIEVED_RAW_BUNDLE_AND_EVERY_FILE_VERIFIED",
        "verified_at_utc": archive.now(), "downloaded_bundle_manifest": archive.ref(DOWNLOAD / "BUNDLE_MANIFEST.json", ROOT),
        "joined_compressed_stream": joined, "files_verified": len(raw_files), "files": raw_files,
        "fresh_extraction_path": str(RAW), "safe_regular_member_inventory_verified": True, "verification_tool": tool})
    # Recover the canonical prestart from the trusted scientific Git commit A.
    prefix = "evidence-research/20261027/h100-confirmatory-gclocker100-v1"
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", transfer.A, "--", prefix], cwd=ROOT, text=True).splitlines()
    CANONICAL.mkdir()
    for name in names:
        relative = Path(name).relative_to(prefix).as_posix()
        common.safe_relative(relative)
        body = subprocess.check_output(["git", "show", transfer.A + ":" + name], cwd=ROOT)
        target = CANONICAL / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(body)
    from research_reporting.replay import verify_archive
    checked = verify_archive(CANONICAL)
    if checked["campaign_sha256"] != archive.CAMPAIGN or checked["prestart_sha256"] != archive.PRESTART:
        raise ValueError("Retrieved Git canonical identity differs")
    canonical_record = save("CANONICAL_GIT_RETRIEVAL_VERIFICATION.json", {
        "schema_version": "h100-private-git-canonical-retrieval-v1", "status": "CANONICAL_GIT_ARCHIVE_VERIFIED",
        "verified_at_utc": archive.now(), "scientific_evidence_commit": transfer.A, "canonical_directory": str(CANONICAL),
        "files_verified": len(names), "campaign_sha256": checked["campaign_sha256"], "prestart_sha256": checked["prestart_sha256"],
        "archive_files_sha256": checked["archive_files_sha256"], "registered_campaign_identity": checked["registered_campaign_identity"]})
    command = [sys.executable, "-B", str(ROOT / "research_reporting/replay.py"), "--archive", str(CANONICAL),
               "--artifacts-root", str(RAW / "evidence/campaign-results"), "--output", str(REPLAY)]
    started, before = archive.now(), time.monotonic()
    with (RECORDS / "retrieved-replay.command.log").open("x") as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    if result.returncode:
        raise ValueError("Actual independently retrieved portable replay failed; preserve all outputs")
    replay = archive.read(REPLAY / "REPLAY_MANIFEST.json")
    audit = archive.read(REPLAY / "complete-audit/AUDIT_COMPLETE.json")
    if (replay["status"] != "PASS" or replay["phase"] != "complete" or replay["synthetic"] is not False
            or audit["status"] != "PASS" or audit["findings"] or audit["synthetic"] is not False
            or any(c.get("returncode") != 0 for c in replay["commands"])):
        raise ValueError("Actual retrieved replay/audit gates did not PASS")
    for relative, digest in replay["output_files_sha256"].items():
        archive.match(REPLAY / relative, digest, ROOT)
    replay_record = save("GENUINE_RETRIEVED_REPLAY_VERIFICATION.json", {
        "schema_version": "h100-private-retrieved-portable-replay-v1", "status": "GENUINE_RETRIEVED_PORTABLE_REPLAY_PASS",
        "started_at_utc": started, "completed_at_utc": archive.now(), "elapsed_seconds": time.monotonic() - before,
        "command": command, "returncode": result.returncode, "replay_manifest": archive.ref(REPLAY / "REPLAY_MANIFEST.json", ROOT),
        "complete_audit": archive.ref(REPLAY / "complete-audit/AUDIT_COMPLETE.json", ROOT), "synthetic": False,
        "campaign_sha256": archive.CAMPAIGN, "raw_bundle_verification": raw_record, "canonical_verification": canonical_record,
        "supplemental_verifications": supplemental_results, "verification_tool": tool})
    cleanup_new_tree(RAW, "All raw files and actual genuine portable replay PASS durably recorded; compressed independent downloads retained")
    print(json.dumps({"phase": "genuine-independent-retrieved-replay-pass", "verification": replay_record}), flush=True)


def transports():
    tool = archive.ref(Path(__file__), ROOT)
    catalog, downloaded = trusted_downloads()
    if archive.read(RECORDS / "GENUINE_RETRIEVED_REPLAY_VERIFICATION.json")["status"] != "GENUINE_RETRIEVED_PORTABLE_REPLAY_PASS":
        raise ValueError("Actual independent retrieved replay PASS must precede transport extraction")
    if REASSEMBLED.exists():
        raise ValueError("Fresh reassembled transport directory must not exist")
    REASSEMBLED.mkdir()
    records = []
    for transport in catalog["original_transports"]:
        cohort, phase = transport["cohort"], transport["phase"]
        expected = transport["original_archive"]
        names = transport["ordered_asset_names"]
        encoding = transport.get("transport_encoding", "identity")
        if encoding == "gzip":
            if len(names) != 1:
                raise ValueError("A gzip envelope requires exactly one cataloged wire asset")
            archive.reserve(ROOT, expected["bytes"])
            path = REASSEMBLED / f"{cohort}-{phase}.tar"
            with path.open("xb") as output, gzip.open(DOWNLOAD / names[0], "rb") as source:
                shutil.copyfileobj(source, output, 8 * 1024 * 1024)
        elif encoding != "identity":
            raise ValueError("Unknown catalog transport encoding")
        elif len(names) == 1:
            path = DOWNLOAD / names[0]
        else:
            archive.reserve(ROOT, expected["bytes"])
            path = REASSEMBLED / f"{cohort}-{phase}.tar"
            with path.open("xb") as output:
                for name in names:
                    with (DOWNLOAD / name).open("rb") as source:
                        shutil.copyfileobj(source, output, 8 * 1024 * 1024)
        actual = archive.ref(path, ROOT)
        if any(actual[k] != expected[k] for k in ("bytes", "sha256")) or path.samefile(ROOT / expected["path"]):
            raise ValueError("Retrieved original transport bytes/inode differ")
        control_ref = archive.ref(ROOT / transport["control"]["path"], ROOT)
        if control_ref != transport["control"]:
            raise ValueError("Trusted original phase control changed")
        control = archive.read(ROOT / control_ref["path"])
        archive.reserve(ROOT, control["tar_bytes"] + 128 * 1024 ** 2)
        destination = TREES / f"{cohort}-{phase}"
        if destination.exists():
            raise ValueError("Fresh transport extraction destination already exists")
        helper = common if cohort == "recovery" else contention_backup
        manifest = helper.verify_archive(path, control, destination)
        if helper.verify_directory(destination, control) != manifest:
            raise ValueError("Retrieved original directory verification differs")
        record = {"schema_version": "h100-private-original-transport-retrieval-v1", "status": "RETRIEVED_ORIGINAL_ARCHIVE_AND_EVERY_MEMBER_VERIFIED",
                  "verified_at_utc": archive.now(), "cohort": cohort, "phase": phase, "retrieved_archive": actual,
                  "original_control": control_ref, "original_verification_receipt": transport["original_receipt"],
                  "transport_encoding": encoding,
                  "downloaded_wire_files": [downloaded["downloaded_assets"][n]["downloaded_file"] for n in names],
                  "ordered_asset_names": names, "asset_ids": [downloaded["downloaded_assets"][n]["remote_asset"]["id"] for n in names],
                  "independent_inode_from_original": True, "fresh_safe_extraction": True,
                  "helper_verify_archive_and_verify_directory_pass": True, "files_verified": len(manifest["files"]),
                  "manifest_sha256": control["manifest_sha256"], "member_inventory": manifest["files"], "verification_tool": tool}
        receipt = save(f"TRANSPORT_{cohort}_{phase}.verified.json", record)
        records.append(receipt)
        cleanup_new_tree(destination, "Both existing archive/directory helpers verified exact downloaded original inventory; receipt saved")
        print(json.dumps({"phase": "retrieved-original-transport-verified", "cohort": cohort, "transport": phase,
                          "files": len(manifest["files"]), "bytes": actual["bytes"]}), flush=True)
    save("ALL_ORIGINAL_TRANSPORTS_VERIFIED.json", {"schema_version": "h100-private-all-original-transports-v1",
         "status": "ALL_15_RETRIEVED_ORIGINAL_TRANSPORTS_VERIFIED", "completed_at_utc": archive.now(),
         "recovery_phases": archive.PHASES, "contention_phases": ["pilot", "main"], "transport_records": records,
         "verification_tool": tool, "free_bytes_after_verification": archive.reserve(ROOT)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "transports"))
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare()
    else:
        transports()


if __name__ == "__main__":
    main()
