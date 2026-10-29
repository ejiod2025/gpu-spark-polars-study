#!/usr/bin/env python3
"""Exact allowlisted private-release transfer via ordinary authenticated gh.

No credential readers, overwrite/delete flags, VM action or publication command.
Upload and authenticated-download records are intermediate operational facts;
they never assert complete private preservation or genuine retrieved replay.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import time

try:
    from . import h100_private_archive as archive
    from . import research_backup as common
except ImportError:
    import h100_private_archive as archive
    import research_backup as common

ROOT = Path(__file__).resolve().parents[1]
REPO = archive.REPO
TAG = archive.TAG
A = "9faf7648e04233de3d3ca44cfe96b2cc15048ade"
B = "9315acbe1cf1f0dd47ac82767b3eb9674c3cdfa6"
CATALOG_SHA = "8c193cea70498201c9eefdb5b8bd13c633c994e199876788d536bc57d5fe664e"
EXPECTED_ASSET_COUNT = 42
CATALOG_PATH = "evidence-research/20261028/h100-private-storage-v1/ASSET_CATALOG.json"
BASE = ROOT / "rerun/gclocker100-20261027"
STAGE = BASE / "release-stage-v1"
RECORDS = BASE / "private-storage-retrieval-v1"
DOWNLOAD = BASE / "release-retrieval-v1"
ASSET_FIELDS = ("id", "name", "state", "size", "digest", "created_at", "updated_at", "url", "browser_download_url")


def safe_error(value):
    # gh's normal errors contain no auth header; never retain query redirects.
    return re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[query omitted]", value)[:3000]


def event(kind, **values):
    row = {"observed_at_utc": archive.now(), "kind": kind, **values}
    with (RECORDS / "TRANSFER_EVENTS.jsonl").open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def gh(args, *, timeout=120, missing_ok=False):
    try:
        result = subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        event("gh-timeout", command=["gh", *args], timeout_seconds=timeout)
        raise RuntimeError("Ambiguous gh timeout; reconcile actual remote state")
    if result.returncode:
        if missing_ok and "HTTP 404" in result.stderr:
            body = json.loads(result.stdout)
            if body.get("status") == "404":
                return None
        event("gh-error", command=["gh", *args], returncode=result.returncode,
              stderr=safe_error(result.stderr), stdout=safe_error(result.stdout))
        raise RuntimeError(safe_error(result.stderr or result.stdout))
    return result.stdout


def privacy():
    observed = json.loads(gh(["repo", "view", REPO, "--json", "nameWithOwner,isPrivate,visibility,viewerPermission"]))
    if observed.get("nameWithOwner") != REPO or observed.get("isPrivate") is not True or observed.get("visibility") != "PRIVATE":
        raise ValueError("Authenticated repository PRIVATE assertion failed")
    event("privacy-asserted", repository=observed)
    return observed


def release():
    value = gh(["api", f"repos/{REPO}/releases/tags/{TAG}"], missing_ok=True)
    if value is None:
        # Unpublished drafts may not be exposed by the tags endpoint. Reconcile
        # the full authenticated release list before deciding one is absent.
        pages = json.loads(gh(["api", f"repos/{REPO}/releases", "--paginate", "--slurp"]))
        matches = [item for page in pages for item in page if item.get("tag_name") == TAG]
        if len(matches) > 1:
            raise ValueError("Multiple actual releases match the fixed tag")
        if not matches:
            return None
        raw = matches[0]
    else:
        raw = json.loads(value)
    result = {k: raw.get(k) for k in ("id", "tag_name", "target_commitish", "draft", "prerelease", "created_at", "published_at", "html_url", "url", "immutable")}
    if result["tag_name"] != TAG or result["target_commitish"] != B or type(result["id"]) is not int or result["id"] <= 0:
        raise ValueError("Actual release does not match exact tag/target B")
    return result


def assets(release_id):
    pages = json.loads(gh(["api", f"repos/{REPO}/releases/{release_id}/assets", "--paginate", "--slurp"]))
    rows = [item for page in pages for item in page]
    result = [{k: row.get(k) for k in ASSET_FIELDS} for row in rows]
    names = [r["name"] for r in result]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate actual release asset names")
    for row in result:
        for key in ("url", "browser_download_url"):
            if not isinstance(row[key], str) or "?" in row[key] or "#" in row[key]:
                raise ValueError("Only stable non-query asset locators may be persisted")
    return result


def trusted():
    path = ROOT / CATALOG_PATH
    actual = archive.ref(path, ROOT)
    body = subprocess.check_output(["git", "show", B + ":" + CATALOG_PATH], cwd=ROOT)
    if common.sha(path) != CATALOG_SHA or __import__("hashlib").sha256(body).hexdigest() != CATALOG_SHA:
        raise ValueError("Catalog must match exact reviewed private commit B")
    catalog = archive.read(path)
    if catalog["scientific_evidence_commit"] != A or catalog["release_asset_count_including_catalog"] != EXPECTED_ASSET_COUNT:
        raise ValueError("Reviewed catalog identities differ")
    expected = {a["name"]: {"bytes": a["bytes"], "sha256": a["sha256"]} for a in catalog["assets"]}
    expected["ASSET_CATALOG.json"] = {k: actual[k] for k in ("bytes", "sha256")}
    if len(expected) != EXPECTED_ASSET_COUNT or {p.name for p in STAGE.iterdir()} != set(expected):
        raise ValueError("Exact reviewed asset stage is required")
    return actual, expected


def match_remote(rows, expected, *, complete=False):
    by_name = {r["name"]: r for r in rows}
    if set(by_name) - set(expected) or (complete and set(by_name) != set(expected)):
        raise ValueError("Actual release assets differ from exact trusted catalog")
    for name, row in by_name.items():
        if (type(row["id"]) is not int or row["id"] <= 0 or row["state"] != "uploaded"
                or row["size"] != expected[name]["bytes"]):
            raise ValueError("Actual asset state/size/ID differs: " + name)
        if row["digest"] is not None and row["digest"] != "sha256:" + expected[name]["sha256"]:
            raise ValueError("Actual asset digest conflicts: " + name)
    return by_name


def upload():
    tool = archive.ref(Path(__file__), ROOT)
    catalog_ref, expected = trusted()
    repo = privacy()
    remote_B = json.loads(gh(["api", f"repos/{REPO}/git/commits/{B}"]))["sha"]
    if remote_B != B:
        raise ValueError("Actual B is not present in authenticated remote")
    actual = release()
    if actual is None:
        tag = gh(["api", f"repos/{REPO}/git/ref/tags/{TAG}"], missing_ok=True)
        if tag is not None:
            raise ValueError("Candidate tag exists without release; reconcile before creating")
        privacy()
        try:
            gh(["release", "create", TAG, "--repo", REPO, "--draft", "--latest=false", "--target", B,
                "--title", "H100 confirmatory study: private evidence",
                "--notes-file", str(RECORDS / "RELEASE_NOTES.md")], timeout=120)
        except RuntimeError:
            # Creation may have succeeded despite a timeout. Never create a replacement.
            if release() is None:
                raise
        actual = release()
        # A successful draft creation can briefly precede visibility in the
        # authenticated REST list. Only repeat reads; never repeat creation.
        for _ in range(10):
            if actual is not None:
                break
            time.sleep(2)
            actual = release()
    if actual is None or actual["draft"] is not True:
        raise ValueError("Uploads require the exact matching private draft")
    observed = match_remote(assets(actual["id"]), expected)
    for name, identity in expected.items():
        path = STAGE / name
        local = archive.ref(path, ROOT)
        if any(local[k] != identity[k] for k in identity):
            raise ValueError("Local staged bytes changed before upload")
        if name in observed:
            if observed[name]["digest"] is None:
                raise ValueError("Existing asset has no API digest; independently reconcile before resuming")
            continue
        for attempt in range(3):
            privacy()
            try:
                gh(["release", "upload", TAG, str(path), "--repo", REPO], timeout=900)
            except RuntimeError:
                observed = match_remote(assets(actual["id"]), expected)
                if name in observed:
                    if observed[name]["digest"] is None:
                        raise ValueError("Ambiguous upload needs independent byte reconciliation")
                    break
                if attempt == 2:
                    raise
                time.sleep(3)
                continue
            observed = match_remote(assets(actual["id"]), expected)
            if name not in observed:
                raise ValueError("CLI upload success is absent from actual asset listing")
            break
        print(json.dumps({"phase": "private-draft-asset-uploaded", "name": name,
                          "bytes": identity["bytes"], "asset_id": observed[name]["id"]}), flush=True)
    actual = release()
    rows = assets(actual["id"])
    match_remote(rows, expected, complete=True)
    repo = privacy()
    if archive.ref(Path(__file__), ROOT) != tool:
        raise ValueError("Transfer source changed during upload")
    record = {"schema_version": "h100-private-draft-upload-v1", "status": "DRAFT_ASSETS_UPLOADED_METADATA_VERIFIED",
              "observed_at_utc": archive.now(), "repository": repo, "release": actual, "assets": rows,
              "scientific_evidence_commit": A, "catalog_commit": B, "catalog": catalog_ref,
              "asset_count": len(rows), "asset_bytes": sum(r["size"] for r in rows), "transfer_source": tool,
              "private_download_verification_performed": False}
    common.exclusive_json(RECORDS / "DRAFT_REMOTE_ASSETS.json", record)
    print(json.dumps({"phase": "private-draft-all-assets-uploaded", "release_id": actual["id"], "assets": len(rows)}), flush=True)


def download():
    tool = archive.ref(Path(__file__), ROOT)
    catalog_ref, expected = trusted()
    repo = privacy()
    actual = release()
    if actual is None or actual["draft"] is not True:
        raise ValueError("Independent initial retrieval requires the exact draft")
    before = match_remote(assets(actual["id"]), expected, complete=True)
    if DOWNLOAD.exists():
        raise ValueError("Fresh retrieval destination must not exist")
    archive.reserve(ROOT, sum(v["bytes"] for v in expected.values()))
    DOWNLOAD.mkdir()
    downloaded = {}
    for name, identity in expected.items():
        archive.reserve(ROOT, identity["bytes"])
        gh(["release", "download", TAG, "--repo", REPO, "--pattern", name, "--dir", str(DOWNLOAD)], timeout=900)
        path = DOWNLOAD / name
        observed = archive.ref(path, ROOT)
        if any(observed[k] != identity[k] for k in identity) or path.samefile(STAGE / name):
            raise ValueError("Independent downloaded bytes/inode differ from trusted catalog")
        downloaded[name] = {"downloaded_file": observed, "different_inode_from_stage": True, "remote_asset": before[name]}
        print(json.dumps({"phase": "authenticated-asset-downloaded-sha256-verified", "name": name, "bytes": identity["bytes"]}), flush=True)
    if {p.name for p in DOWNLOAD.iterdir()} != set(expected):
        raise ValueError("Exact independent downloaded file set differs")
    after = match_remote(assets(actual["id"]), expected, complete=True)
    if after != before:
        raise ValueError("Remote asset identities changed during download")
    if archive.ref(Path(__file__), ROOT) != tool:
        raise ValueError("Transfer source changed during download")
    record = {"schema_version": "h100-authenticated-private-asset-download-v1", "status": "AUTHENTICATED_ASSET_BYTES_VERIFIED",
              "completed_at_utc": archive.now(), "repository": privacy(), "release": release(), "catalog": catalog_ref,
              "scientific_evidence_commit": A, "catalog_commit": B, "asset_count": len(downloaded),
              "downloaded_assets": downloaded, "transfer_source": tool, "free_bytes_after_download": archive.reserve(ROOT),
              "full_archive_member_and_replay_verification_complete": False,
              "private_preservation_receipt_not_yet_created": True}
    common.exclusive_json(RECORDS / "DOWNLOADED_ASSETS.json", record)
    print(json.dumps({"phase": "all-private-assets-independently-downloaded", "assets": len(downloaded)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("upload", "download"))
    args = parser.parse_args()
    if args.mode == "upload":
        upload()
    else:
        download()


if __name__ == "__main__":
    main()
