#!/usr/bin/env python3
"""Publish only after actual v2 downloads, decoded transports and replay pass."""
import argparse
from pathlib import Path
import platform
import subprocess

try:
    from . import h100_private_release_v2 as v2
    from . import h100_private_release_transfer as transfer
    from . import h100_private_retrieval_verify as verify
    from . import h100_private_archive as archive
    from . import research_backup as common
except ImportError:
    import h100_private_release_v2 as v2
    import h100_private_release_transfer as transfer
    import h100_private_retrieval_verify as verify
    import h100_private_archive as archive
    import research_backup as common


def file_ref(reference):
    return {k: reference[k] for k in ("path", "sha256")}


def finalize(commit, digest):
    v2.configure(commit, digest)
    root, records = transfer.ROOT, transfer.RECORDS
    tool = archive.ref(Path(__file__), root)
    catalog_ref, expected = transfer.trusted()
    catalog = archive.read(root / transfer.CATALOG_PATH)
    downloaded = archive.read(records / "DOWNLOADED_ASSETS.json")
    replay = archive.read(records / "GENUINE_RETRIEVED_REPLAY_VERIFICATION.json")
    all_transports = archive.read(records / "ALL_ORIGINAL_TRANSPORTS_VERIFIED.json")
    if (downloaded["status"] != "AUTHENTICATED_ASSET_BYTES_VERIFIED"
            or downloaded["catalog"] != catalog_ref or downloaded["catalog_commit"] != commit
            or replay["status"] != "GENUINE_RETRIEVED_PORTABLE_REPLAY_PASS" or replay["synthetic"] is not False
            or all_transports["status"] != "ALL_15_RETRIEVED_ORIGINAL_TRANSPORTS_VERIFIED"
            or len(all_transports["transport_records"]) != 15):
        raise ValueError("Actual full download, genuine replay and all original transport checks are mandatory")
    bundle = archive.read(transfer.DOWNLOAD / "BUNDLE_MANIFEST.json")
    for key in ("raw_bundle_verification", "canonical_verification"):
        reference = replay[key]
        if archive.ref(root / reference["path"], root) != reference:
            raise ValueError("Retrieved bundle/canonical verification record changed")
    raw_verified = archive.read(root / replay["raw_bundle_verification"]["path"])
    canonical_verified = archive.read(root / replay["canonical_verification"]["path"])
    if (raw_verified["files_verified"] != len(bundle["files"]) or len(bundle["files"]) != 13900
            or raw_verified["joined_compressed_stream"] != {"bytes": bundle["archive_bytes"], "sha256": bundle["archive_sha256"]}
            or canonical_verified["campaign_sha256"] != archive.CAMPAIGN
            or canonical_verified["prestart_sha256"] != archive.PRESTART
            or canonical_verified["scientific_evidence_commit"] != transfer.A
            or canonical_verified["files_verified"] != 346):
        raise ValueError("Actual retrieved raw/canonical inventories do not bind this completed campaign")
    supplemental_manifest = archive.read(transfer.DOWNLOAD / "SUPPLEMENTAL_FILES_MANIFEST.json")["archives"]
    if set(replay["supplemental_verifications"]) != set(supplemental_manifest) or len(supplemental_manifest) != 8:
        raise ValueError("Every actual supplemental archive must have independent member verification")
    for name, reference in replay["supplemental_verifications"].items():
        if archive.ref(root / reference["path"], root) != reference:
            raise ValueError("Supplemental member verification record changed")
        result = archive.read(root / reference["path"])
        if result["member_count"] != len(supplemental_manifest[name]["members"]):
            raise ValueError("Supplemental verified member count differs")
    for name, identity in expected.items():
        actual = archive.ref(transfer.DOWNLOAD / name, root)
        if (any(actual[k] != identity[k] for k in ("bytes", "sha256"))
                or actual != downloaded["downloaded_assets"][name]["downloaded_file"]
                or (transfer.DOWNLOAD / name).samefile(transfer.STAGE / name)):
            raise ValueError("Actual independent downloaded bytes changed")
    transport_results = {}
    for reference in all_transports["transport_records"]:
        if archive.ref(root / reference["path"], root) != reference:
            raise ValueError("Original transport verification record changed")
        result = archive.read(root / reference["path"])
        actual = archive.ref(root / result["retrieved_archive"]["path"], root)
        original = next(t for t in catalog["original_transports"] if t["cohort"] == result["cohort"] and t["phase"] == result["phase"])
        if result["status"] != "RETRIEVED_ORIGINAL_ARCHIVE_AND_EVERY_MEMBER_VERIFIED" or actual != result["retrieved_archive"]:
            raise ValueError("Verified newly decoded/retrieved original tar changed")
        if (any(actual[k] != original["original_archive"][k] for k in ("bytes", "sha256"))
                or result["original_control"] != original["control"]):
            raise ValueError("Retrieved original tar no longer matches the original historical control")
        transport_results[(result["cohort"], result["phase"])] = result
    if {p for c, p in transport_results if c == "recovery"} != set(archive.PHASES):
        raise ValueError("All thirteen genuine recovery transports are required")
    if {p for c, p in transport_results if c == "contention"} != {"pilot", "main"}:
        raise ValueError("Both genuine contention transports are required")
    for key in ("replay_manifest", "complete_audit"):
        if archive.ref(root / replay[key]["path"], root) != replay[key]:
            raise ValueError("Actual retrieved genuine replay output changed")
    replay_manifest = archive.read(root / replay["replay_manifest"]["path"])
    audit = archive.read(root / replay["complete_audit"]["path"])
    if (replay_manifest["status"] != "PASS" or replay_manifest["synthetic"] is not False
            or audit["status"] != "PASS" or audit["findings"] or audit["synthetic"] is not False):
        raise ValueError("Actual retrieved replay/audit no longer passes")
    privacy = transfer.privacy()
    actual = transfer.release()
    remote = transfer.match_remote(transfer.assets(actual["id"]), expected, complete=True)
    for name, row in remote.items():
        prior = downloaded["downloaded_assets"][name]["remote_asset"]
        if any(row[k] != prior[k] for k in ("id", "name", "size", "digest")):
            raise ValueError("Actual remote asset identities changed since independent download")
    prepublish = {"schema_version": "h100-private-draft-all-download-checks-v1", "status": "DRAFT_ALL_PRIVATE_DOWNLOAD_CHECKS_PASS",
                  "verified_at_utc": archive.now(), "catalog": catalog_ref, "scientific_evidence_commit": transfer.A,
                  "catalog_commit_B2": commit, "release": actual, "private_repository_observation": privacy,
                  "downloaded_assets_record": archive.ref(records / "DOWNLOADED_ASSETS.json", root),
                  "genuine_retrieved_replay": archive.ref(records / "GENUINE_RETRIEVED_REPLAY_VERIFICATION.json", root),
                  "all_original_transports": archive.ref(records / "ALL_ORIGINAL_TRANSPORTS_VERIFIED.json", root),
                  "finalization_source": tool}
    before_path = records / "DRAFT_ALL_DOWNLOAD_CHECKS_PASS.json"
    if not before_path.exists():
        common.exclusive_json(before_path, prepublish)
    elif archive.read(before_path)["catalog"] != catalog_ref:
        raise ValueError("Existing prepublication check record differs")
    if actual["draft"] is True:
        transfer.privacy()
        try:
            transfer.gh(["release", "edit", transfer.TAG, "--repo", transfer.REPO, "--draft=false", "--latest=false"])
        except RuntimeError:
            # Publishing may have completed despite a timeout; inspect, never replace.
            if transfer.release()["draft"] is not False:
                raise
    published = transfer.release()
    if published["draft"] is not False or not published["published_at"]:
        raise ValueError("Actual release has not been published privately")
    privacy = transfer.privacy()
    rows = transfer.assets(published["id"])
    published_assets = transfer.match_remote(rows, expected, complete=True)
    for name, row in published_assets.items():
        if any(row[k] != remote[name][k] for k in ("id", "name", "size", "digest")):
            raise ValueError("Published assets differ from independently verified draft bytes")
    import json
    tag = json.loads(transfer.gh(["api", f"repos/{transfer.REPO}/git/ref/tags/{transfer.TAG}"]))
    obj = tag["object"]
    while obj["type"] == "tag":
        obj = json.loads(transfer.gh(["api", f"repos/{transfer.REPO}/git/tags/{obj['sha']}"]))["object"]
    if obj["type"] != "commit" or obj["sha"] != commit:
        raise ValueError("Published exact tag does not resolve to actual reviewed B2")
    confirmation_path = records / "PUBLISHED_ASSET_CATALOG.json"
    transfer.gh(["release", "download", transfer.TAG, "--repo", transfer.REPO, "--pattern", "ASSET_CATALOG.json",
                 "--output", str(confirmation_path)], timeout=120)
    confirmation = archive.ref(confirmation_path, root)
    if confirmation["sha256"] != digest or confirmation["bytes"] != catalog_ref["bytes"]:
        raise ValueError("Fresh authenticated published-state catalog download differs")
    repository_url = "https://github.com/" + transfer.REPO
    contention_raw = {}
    terminal = archive.read(root / catalog["bindings"]["contention_terminal_replay"]["path"])
    if set(terminal["snapshots"]) != {"pilot", "main"}:
        raise ValueError("Actual terminal contention snapshot phase set differs")
    for phase in ("pilot", "main"):
        result = transport_results[("contention", phase)]
        name = result["ordered_asset_names"][0]
        actual_tar = result["retrieved_archive"]
        original = next(t for t in catalog["original_transports"] if t["cohort"] == "contention" and t["phase"] == phase)["original_archive"]
        if (root / actual_tar["path"]).samefile(root / original["path"]):
            raise ValueError("Contention retrieval must have an independent inode")
        contention_raw[phase] = {"archive_sha256": actual_tar["sha256"], "archive_bytes": actual_tar["bytes"],
                                "retrieval_verified": True, "retrieved_archive": file_ref(actual_tar),
                                "storage": {"repository_url": repository_url, "release_tag": transfer.TAG,
                                            "asset_ids": [published_assets[name]["id"]], "asset_names": [name]}}
    preservation = {"schema_version": "h100-contention-private-preservation-v1", "status": "PRIVATE_DOWNLOAD_VERIFIED",
                    "private": True, "experiment_id": "h100-contention-20261028", "plan_sha256": terminal["plan_sha256"],
                    "prestart_sha256": terminal["prestart_sha256"], "replay_sha256": archive.CONTENTION,
                    "repository_url": repository_url, "raw_backups": contention_raw, "verified_at_utc": archive.now(),
                    "catalog_commit_B2": commit, "catalog_sha256": digest, "published_release_id": published["id"],
                    "relocated_contention_replay_run": False,
                    "scope": "Both genuinely downloaded tar files and every member verified with existing helpers; exact original terminal audit retained. No relocated contention replay claim."}
    common.exclusive_json(records / "CONTENTION_PRIVATE_PRESERVATION.json", preservation)
    receipt = {"schema_version": "h100-private-evidence-release-v1", "status": "PRIVATE_DOWNLOAD_VERIFIED", "private": True,
               "verified_at_utc": archive.now(), "repository_url": repository_url, "repository": privacy,
               "campaign_sha256": archive.CAMPAIGN, "prestart_sha256": archive.PRESTART, "execution_profile_sha256": archive.PROFILE,
               "scientific_evidence_commit": transfer.A, "catalog_commit_B2": commit, "catalog": catalog_ref,
               "release": published, "published_tag": tag, "published_assets": rows,
               "independently_downloaded_assets": downloaded["downloaded_assets"],
               "fresh_authenticated_published_catalog_download": confirmation,
               "draft_full_verification": archive.ref(before_path, root),
               "genuine_retrieved_portable_replay": archive.ref(records / "GENUINE_RETRIEVED_REPLAY_VERIFICATION.json", root),
               "all_original_transports": archive.ref(records / "ALL_ORIGINAL_TRANSPORTS_VERIFIED.json", root),
               "contention_preservation": archive.ref(records / "CONTENTION_PRIVATE_PRESERVATION.json", root),
               "checks": {"exact_cataloged_33_downloaded_files": True, "wire_SHA256_and_bytes": True,
                          "all13_fresh_decoded_recovery_tar_original_hashes_and_member_inventories": True,
                          "both_independent_contention_tars_and_member_inventories": True,
                          "all8_supplemental_archive_member_inventories": True, "raw_bundle_joined_gzip_and_all13900_files": True,
                          "canonical_prestart_from_scientific_commit_A": True, "genuine_independent_portable_replay_PASS": True,
                          "actual_private_published_release_exact_B2_and_asset_identity_readback": True,
                          "fresh_authenticated_published_catalog_download": True},
               "reported_immutable": published.get("immutable"), "immutability_setting_changed": False,
               "versions": {"gh": subprocess.check_output(["gh", "--version"], text=True).splitlines()[0], "python": platform.python_version()},
               "finalization_source": tool, "free_bytes_after_verification": archive.reserve(root),
               "publication_or_VM_retirement_performed_by_storage_operator": False}
    if archive.ref(Path(__file__), root) != tool:
        raise ValueError("Finalization source changed during execution")
    common.exclusive_json(records / "PRIVATE_RELEASE_RETRIEVAL_RECEIPT.json", receipt)
    print(json.dumps({"status": receipt["status"], "release": published, "general_receipt": archive.ref(records / "PRIVATE_RELEASE_RETRIEVAL_RECEIPT.json", root),
                      "contention_preservation": archive.ref(records / "CONTENTION_PRIVATE_PRESERVATION.json", root)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-commit", required=True)
    parser.add_argument("--catalog-sha256", required=True)
    args = parser.parse_args()
    finalize(args.catalog_commit, args.catalog_sha256)


if __name__ == "__main__":
    main()
