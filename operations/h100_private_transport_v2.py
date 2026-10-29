#!/usr/bin/env python3
"""Prepare a separate lossless gzip transport catalog; no remote mutations."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import os
import platform
import subprocess
import time
import zlib

try:
    from . import h100_private_archive as archive
    from . import h100_private_release_transfer as v1
    from . import research_backup as common
except ImportError:
    import h100_private_archive as archive
    import h100_private_release_transfer as v1
    import research_backup as common

ROOT, BASE = v1.ROOT, v1.BASE
TAG = "h100-confirmatory-gclocker100-20261027-evidence-v2"
STAGE = BASE / "release-stage-v2"
RECORDS = BASE / "private-storage-stage-v2"
CATALOG = ROOT / "evidence-research/20261028/h100-private-storage-v2/ASSET_CATALOG.json"


def compress_envelope(source, target, expected, root):
    common.safe_file(source, root)
    before = source.stat()
    archive.reserve(root, min(expected["bytes"], archive.GIB))
    clock = time.monotonic()
    digest, count = hashlib.sha256(), 0
    with source.open("rb") as handle, target.open("xb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=6, mtime=0) as compressed:
            for body in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                compressed.write(body)
                digest.update(body)
                count += len(body)
    after = source.stat()
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or count != expected["bytes"] or digest.hexdigest() != expected["sha256"]):
        raise ValueError("Original source changed or differs from historical control")
    wire = archive.ref(target, root)
    if wire["bytes"] >= archive.LIMIT:
        raise ValueError("Compressed wire asset is not below 2 GiB")
    decoded_digest, decoded_count = hashlib.sha256(), 0
    with gzip.open(target, "rb") as handle:
        for body in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            decoded_digest.update(body)
            decoded_count += len(body)
    if decoded_count != count or decoded_digest.hexdigest() != digest.hexdigest():
        raise ValueError("Fresh independent gzip decoding does not restore original bytes")
    return {"wire_gzip": wire, "decoded_original": expected, "decoded_verification_sha256": decoded_digest.hexdigest(),
            "decoded_verification_bytes": decoded_count, "elapsed_seconds": time.monotonic() - clock,
            "gzip": {"filename": "", "mtime": 0, "compression_level": 6},
            "software": {"python": platform.python_version(), "zlib_compile": zlib.ZLIB_VERSION, "zlib_runtime": zlib.ZLIB_RUNTIME_VERSION},
            "original_source_stat_unchanged": True}


def prepare():
    tool = archive.ref(Path(__file__), ROOT)
    if STAGE.exists() or RECORDS.exists() or CATALOG.exists():
        raise ValueError("All transport v2 output paths must be new")
    original = archive.read(ROOT / v1.CATALOG_PATH)
    body = subprocess.check_output(["git", "show", v1.B + ":" + v1.CATALOG_PATH], cwd=ROOT)
    if hashlib.sha256(body).hexdigest() != v1.CATALOG_SHA or original["scientific_evidence_commit"] != v1.A:
        raise ValueError("Exact immutable v1 scientific/catalog binding is required")
    archive.reserve(ROOT, 3 * archive.GIB)
    STAGE.mkdir()
    RECORDS.mkdir()
    assets, envelopes, transports = [], {}, []
    for item in original["assets"]:
        if item["role"].startswith("recovery-original-transport"):
            continue
        source = v1.STAGE / item["name"]
        observed = archive.ref(source, ROOT)
        if any(observed[k] != item[k] for k in ("bytes", "sha256")):
            raise ValueError("Reused immutable payload changed")
        os.link(source, STAGE / item["name"])
        assets.append(item)
    for transport in original["original_transports"]:
        if transport["cohort"] != "recovery":
            transports.append({**transport, "transport_encoding": "identity"})
            continue
        expected = transport["original_archive"]
        source = ROOT / expected["path"]
        control = archive.read(ROOT / transport["control"]["path"])
        if control["tar_bytes"] != expected["bytes"] or control["tar_sha256"] != expected["sha256"]:
            raise ValueError("Original recovery control changed")
        name = "recovery-" + transport["phase"] + ".tar.gz"
        envelope = compress_envelope(source, STAGE / name, expected, ROOT)
        envelope.update({"phase": transport["phase"], "original_control": transport["control"],
                         "original_verification_receipt": transport["original_receipt"],
                         "decoded_restoration_path": expected["path"]})
        envelopes[transport["phase"]] = envelope
        wire = envelope["wire_gzip"]
        assets.append({"name": name, "role": "recovery-original-transport-gzip-envelope", "source": expected,
                       "restoration_path": expected["path"] + ".gz", "bytes": wire["bytes"], "sha256": wire["sha256"],
                       "transport_encoding": "gzip", "decoded_archive": expected,
                       "decoded_restoration_path": expected["path"], "staging_method": "new-lossless-gzip-envelope"})
        transports.append({**transport, "ordered_asset_names": [name], "transport_encoding": "gzip",
                           "decoded_archive": expected, "wire_sha256": wire["sha256"], "wire_bytes": wire["bytes"]})
        print(json.dumps({"phase": "lossless-envelope-and-decoded-original-verified", "transport": transport["phase"],
                          "wire_bytes": wire["bytes"], "original_bytes": expected["bytes"]}), flush=True)
    if len(assets) != 32 or len(envelopes) != 13 or {p.name for p in STAGE.iterdir()} != {a["name"] for a in assets}:
        raise ValueError("Exact 32 payload assets and 13 recovery envelopes are required")
    if archive.ref(Path(__file__), ROOT) != tool:
        raise ValueError("V2 preparation source changed during execution")
    envelope_manifest = {"schema_version": "h100-lossless-original-transport-envelopes-v1",
                         "status": "ALL_13_GZIP_ENVELOPES_AND_DECODED_ORIGINAL_HASHES_VERIFIED",
                         "completed_at_utc": archive.now(), "scientific_evidence_commit": v1.A,
                         "original_catalog_commit": v1.B, "original_catalog_sha256": v1.CATALOG_SHA,
                         "envelopes": envelopes, "preparation_source": tool,
                         "scientific_observations_or_sources_changed": False}
    common.exclusive_json(RECORDS / "TRANSPORT_ENVELOPES.json", envelope_manifest)
    stage_manifest = {"schema_version": "h100-private-staging-v2", "status": "LOSSLESS_GZIP_STAGE_BYTES_VERIFIED",
                      "created_at_utc": archive.now(), "assets": assets, "original_transports": transports,
                      "wire_payload_bytes": sum(a["bytes"] for a in assets), "payload_asset_count": len(assets),
                      "transport_envelopes": archive.ref(RECORDS / "TRANSPORT_ENVELOPES.json", ROOT),
                      "free_bytes_after_staging": archive.reserve(ROOT), "private_download_verification_performed": False}
    common.exclusive_json(RECORDS / "STAGING_MANIFEST.json", stage_manifest)
    catalog = dict(original)
    catalog.update({"schema_version": "h100-private-evidence-asset-catalog-v2", "status": "CATALOGED_LOSSLESS_GZIP_LOCAL_BYTES",
                    "created_at_utc": archive.now(), "release_tag": TAG, "assets": assets, "original_transports": transports,
                    "release_asset_count_including_catalog": 33,
                    "catalog_asset": {"name": "ASSET_CATALOG.json", "restoration_path": CATALOG.relative_to(ROOT).as_posix(),
                                      "identity": "Exact Git blob in actual reviewed catalog commit B2; hash/bytes recorded externally in later receipts"},
                    "commit_binding": "Scientific commit A is unchanged. This separate v2 catalog and operational envelope manifests are committed in B2; target actual B2 after root review/private push. No own-containing-commit hash is embedded.",
                    "prior_catalog": {"commit": v1.B, "path": v1.CATALOG_PATH, "sha256": v1.CATALOG_SHA,
                                      "disposition": "Original v1 catalog preserved; its incomplete draft is superseded only in wire transport"},
                    "staging_manifest": archive.ref(RECORDS / "STAGING_MANIFEST.json", ROOT),
                    "transport_envelopes_manifest": archive.ref(RECORDS / "TRANSPORT_ENVELOPES.json", ROOT),
                    "prior_staging_manifest": original["staging_manifest"],
                    "prior_cataloging_tool_source": original["cataloging_tool_source_for_child_B"],
                    "cataloging_tool_source_for_transport_v2": tool,
                    "lossless_transport_rule": "For recovery transports only, hash compressed wire bytes, decode gzip into a genuinely new tar, require original control SHA256/bytes, and run unchanged archive/directory verifiers. Contention tars remain exact original wire bytes. No scientific observation or source changes.",
                    "private_download_verification_performed": False})
    catalog.pop("cataloging_tool_source_for_child_B", None)
    common.exclusive_json(CATALOG, catalog)
    os.link(CATALOG, STAGE / "ASSET_CATALOG.json")
    print(json.dumps({"catalog": archive.ref(CATALOG, ROOT), "stage": str(STAGE), "assets": 33,
                      "wire_bytes_including_catalog": stage_manifest["wire_payload_bytes"] + CATALOG.stat().st_size,
                      "original_recovery_bytes": sum(e["decoded_original"]["bytes"] for e in envelopes.values()),
                      "recovery_wire_bytes": sum(e["wire_gzip"]["bytes"] for e in envelopes.values())}), flush=True)


if __name__ == "__main__":
    prepare()
