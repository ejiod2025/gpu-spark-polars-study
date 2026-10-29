#!/usr/bin/env python3
"""Read-only authenticated download queue, waiting for each actual uploaded asset.

An explicit partial replay-ready record permits offline replay overlap. Only
the final exact full set can satisfy full download/transport/finalizer gates.
"""
import argparse
import json
from pathlib import Path
import time

try:
    from . import h100_private_release_v2 as v2
    from . import h100_private_release_transfer as transfer
    from . import h100_private_archive as archive
    from . import research_backup as common
except ImportError:
    import h100_private_release_v2 as v2
    import h100_private_release_transfer as transfer
    import h100_private_archive as archive
    import research_backup as common


def download(commit, digest):
    v2.configure(commit, digest)
    root, records, destination = transfer.ROOT, transfer.RECORDS, transfer.DOWNLOAD
    tool = archive.ref(Path(__file__), root)
    catalog_ref, expected = transfer.trusted()
    catalog = archive.read(root / transfer.CATALOG_PATH)
    needed = {a["name"] for a in catalog["assets"] if a.get("transport_encoding") != "gzip"}
    if len(needed) != 19 or destination.exists():
        raise ValueError("Expected19 replay-ready payloads and a genuinely new download directory")
    archive.reserve(root, sum(v["bytes"] for v in expected.values()))
    destination.mkdir()
    repository = transfer.privacy()
    actual = transfer.release()
    if actual is None or actual["draft"] is not True:
        raise ValueError("Exact actual private draft is required")
    downloaded = {}
    for name, identity in expected.items():
        while True:
            rows = transfer.assets(actual["id"])
            if {r["name"] for r in rows} - set(expected):
                raise ValueError("Unlisted remote asset appeared")
            current = next((r for r in rows if r["name"] == name), None)
            if current is not None and current["state"] == "uploaded":
                transfer.match_remote([current], expected)
                break
            if current is not None and current["state"] not in ("starter", "uploaded"):
                raise ValueError("Unexpected actual asset state")
            time.sleep(5)
        archive.reserve(root, identity["bytes"])
        transfer.gh(["release", "download", transfer.TAG, "--repo", transfer.REPO, "--pattern", name,
                     "--dir", str(destination)], timeout=900)
        path = destination / name
        result = archive.ref(path, root)
        if any(result[k] != identity[k] for k in ("bytes", "sha256")) or path.samefile(transfer.STAGE / name):
            raise ValueError("Independent download SHA256/bytes/inode differs")
        downloaded[name] = {"downloaded_file": result, "different_inode_from_stage": True, "remote_asset": current}
        common.exclusive_json(records / ("DOWNLOAD_" + name + ".verified.json"), {
            "schema_version": "h100-authenticated-single-asset-download-v1", "status": "AUTHENTICATED_SINGLE_ASSET_BYTES_VERIFIED",
            "verified_at_utc": archive.now(), "name": name, **downloaded[name], "download_source": tool})
        if set(downloaded) == needed:
            common.exclusive_json(records / "PREPARE_READY_ASSETS.json", {
                "schema_version": "h100-authenticated-replay-ready-subset-v1", "status": "AUTHENTICATED_REQUIRED_REPLAY_ASSET_BYTES_VERIFIED",
                "verified_at_utc": archive.now(), "catalog": catalog_ref, "downloaded_assets": dict(downloaded),
                "asset_count": len(downloaded), "catalog_commit": commit, "scientific_evidence_commit": transfer.A,
                "full_catalog_download_complete": False, "private_preservation_receipt_not_yet_created": True, "download_source": tool})
            print(json.dumps({"phase": "independent-replay-ready-subset-downloaded", "assets": len(downloaded)}), flush=True)
        print(json.dumps({"phase": "independent-asset-downloaded-verified", "name": name, "bytes": identity["bytes"]}), flush=True)
    after = transfer.match_remote(transfer.assets(actual["id"]), expected, complete=True)
    if {p.name for p in destination.iterdir()} != set(expected):
        raise ValueError("Final exact downloaded file set differs")
    for name, result in downloaded.items():
        if any(after[name][k] != result["remote_asset"][k] for k in ("id", "name", "size", "digest")):
            raise ValueError("Remote uploaded bytes changed during download queue")
    if archive.ref(Path(__file__), root) != tool:
        raise ValueError("Download queue source changed during execution")
    common.exclusive_json(records / "DOWNLOADED_ASSETS.json", {
        "schema_version": "h100-authenticated-private-asset-download-v1", "status": "AUTHENTICATED_ASSET_BYTES_VERIFIED",
        "completed_at_utc": archive.now(), "repository": transfer.privacy(), "release": transfer.release(), "catalog": catalog_ref,
        "scientific_evidence_commit": transfer.A, "catalog_commit": commit, "asset_count": len(downloaded),
        "downloaded_assets": downloaded, "transfer_source": tool, "free_bytes_after_download": archive.reserve(root),
        "full_archive_member_and_replay_verification_complete": False, "private_preservation_receipt_not_yet_created": True})
    print(json.dumps({"phase": "all33-independent-private-downloads-verified", "assets": len(downloaded)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-commit", required=True)
    parser.add_argument("--catalog-sha256", required=True)
    args = parser.parse_args()
    download(args.catalog_commit, args.catalog_sha256)
