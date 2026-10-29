#!/usr/bin/env python3
"""Use reviewed actual B2 to run existing private transfer/retrieval helpers."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess

try:
    from . import h100_private_archive as archive
    from . import h100_private_release_transfer as transfer
    from . import h100_private_retrieval_verify as verify
    from . import h100_private_transport_v2 as transport
    from . import research_backup as common
except ImportError:
    import h100_private_archive as archive
    import h100_private_release_transfer as transfer
    import h100_private_retrieval_verify as verify
    import h100_private_transport_v2 as transport
    import research_backup as common


def configure(commit, digest):
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Actual reviewed full B2 and catalog SHA256 are required")
    path = transport.CATALOG.relative_to(transfer.ROOT).as_posix()
    blob = subprocess.check_output(["git", "show", commit + ":" + path], cwd=transfer.ROOT)
    if hashlib.sha256(blob).hexdigest() != digest or common.sha(transport.CATALOG) != digest:
        raise ValueError("Actual B2/local catalog bytes do not match reviewed identity")
    transfer.B, transfer.CATALOG_SHA, transfer.CATALOG_PATH = commit, digest, path
    transfer.TAG, transfer.STAGE, transfer.EXPECTED_ASSET_COUNT = transport.TAG, transport.STAGE, 33
    transfer.RECORDS = transfer.BASE / "private-storage-retrieval-v2"
    transfer.DOWNLOAD = transfer.BASE / "release-retrieval-v2"
    verify.RECORDS, verify.DOWNLOAD = transfer.RECORDS, transfer.DOWNLOAD
    verify.TREES = transfer.BASE / "retrieval-verification-trees-v2"
    verify.RAW = transfer.BASE / "retrieved-raw-bundle-v2"
    verify.CANONICAL = transfer.BASE / "retrieved-canonical-prestart-v2"
    verify.REPLAY = transfer.BASE / "retrieved-raw-replay-v2"
    verify.REASSEMBLED = transfer.BASE / "release-retrieval-reassembled-v2"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("upload", "download", "prepare", "transports"))
    parser.add_argument("--catalog-commit", required=True)
    parser.add_argument("--catalog-sha256", required=True)
    args = parser.parse_args()
    configure(args.catalog_commit, args.catalog_sha256)
    if args.mode == "upload":
        if not transfer.RECORDS.exists():
            transfer.RECORDS.mkdir()
            common.exclusive_json(transfer.RECORDS / "OPERATION_CONFIG.json", {
                "schema_version": "h100-private-wire-transport-v2-operation-v1", "created_at_utc": archive.now(),
                "scientific_evidence_commit": transfer.A, "catalog_commit_B2": args.catalog_commit,
                "catalog_sha256": args.catalog_sha256, "release_tag": transfer.TAG,
                "wrapper_source": archive.ref(Path(__file__), transfer.ROOT)})
            notes = (f"Private H100 scientific raw evidence. Scientific commit A: `{transfer.A}`. "
                     f"Reviewed transport catalog/release target B2: `{args.catalog_commit}`. "
                     f"ASSET_CATALOG.json SHA256: `{args.catalog_sha256}`.\n\n"
                     "This separate v2 release uses lossless gzip envelopes for all thirteen original recovery tars. "
                     "Each compressed wire hash/size and decoded original tar hash/size are cataloged. "
                     "Decode into new tar files, require their original control identities, and run the existing safe archive/directory verifiers. "
                     "Both contention tars retain their exact original bytes. Bundle, supplemental, replay, figures and source-provenance bindings are unchanged. "
                     "The default-JVM incident is excluded historical evidence. No scientific observations or source were changed.\n\n"
                     "The prior v1 catalog, draft and already uploaded assets remain preserved as superseded/incomplete transport. "
                     "This private archive does not itself assert finished public paper deployment or conditional retirement. "
                     "Later actual retrieval receipts record authenticated downloads, decoded original verification, genuine portable replay and final release state.\n")
            with (transfer.RECORDS / "RELEASE_NOTES.md").open("x") as handle:
                handle.write(notes)
        else:
            saved = archive.read(transfer.RECORDS / "OPERATION_CONFIG.json")
            if saved["catalog_commit_B2"] != args.catalog_commit or saved["catalog_sha256"] != args.catalog_sha256:
                raise ValueError("Existing operation config differs; no reuse")
        transfer.upload()
    elif args.mode == "download":
        transfer.download()
    elif args.mode == "prepare":
        verify.prepare()
    else:
        verify.transports()


if __name__ == "__main__":
    main()
