#!/usr/bin/env python3
"""Bundle completed raw evidence, with deterministic compression and chunk hashes.

Run only after data collection, final validation and analysis have terminated.
No benchmark, source data, original result or original gate is changed.
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
import stat
import sys
import tarfile
import zlib

FORBIDDEN_SUFFIXES = {".parquet", ".jar", ".pem", ".key", ".duckdb"}
FORBIDDEN_NAMES = {".env", ".git", ".venv", "venv", "id_rsa", "id_ed25519"}
REAL_CAMPAIGN_SHA256 = "69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_file(path):
    return json.loads(Path(path).read_text())


def publish_json(path, value):
    path = Path(path)
    temporary = path.with_name("." + path.name + ".tmp")
    try:
        with temporary.open("x") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)  # atomic publication without overwriting a pathname
    finally:
        temporary.unlink(missing_ok=True)


def inventory(root):
    """Retain every regular file; reject ambiguous links and accidental data/secrets."""
    result = {}
    for path in sorted(Path(root).rglob("*")):
        relative = path.relative_to(root)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError(f"Evidence contains a symlink: {relative}")
        if any(part in FORBIDDEN_NAMES for part in relative.parts) or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError(f"Outside raw-evidence scope: {relative}")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"Evidence contains a non-regular file: {relative}")
        before = path.stat()
        digest = sha256(path)
        after = path.stat()
        identity = lambda value: (value.st_size, value.st_mtime_ns, value.st_ino, value.st_dev)
        if identity(before) != identity(after):
            raise ValueError(f"Evidence changed while hashing: {relative}")
        result[str(relative)] = {"bytes": before.st_size, "sha256": digest,
                                 "executable": bool(mode & 0o111)}
    if not result:
        raise ValueError("Evidence is empty")
    return result


class ChunkWriter(io.RawIOBase):
    """One gzip stream split into bounded pieces; no giant intermediate needed."""
    def __init__(self, output, chunk_bytes):
        self.output = Path(output)
        self.chunk_bytes = chunk_bytes
        self.parts = []
        self.stream_hash = hashlib.sha256()
        self.handle = None
        self.part_hash = None
        self.part_size = 0
        self.total_size = 0
        super().__init__()

    def writable(self):
        return True

    def _open_part(self):
        name = f"evidence.tar.gz.part-{len(self.parts) + 1:06d}"
        self.handle = (self.output / name).open("xb")
        self.part_name = name
        self.part_hash = hashlib.sha256()
        self.part_size = 0

    def _finish_part(self):
        if self.handle is None:
            return
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        self.parts.append({"path": self.part_name, "bytes": self.part_size,
                           "sha256": self.part_hash.hexdigest()})
        self.handle = None

    def write(self, data):
        if self.closed:
            raise ValueError("write to closed chunk stream")
        view = memoryview(data)
        original_size = len(view)
        while view:
            if self.handle is None:
                self._open_part()
            amount = min(len(view), self.chunk_bytes - self.part_size)
            part = view[:amount]
            self.handle.write(part)
            self.part_hash.update(part)
            self.stream_hash.update(part)
            self.part_size += amount
            self.total_size += amount
            view = view[amount:]
            if self.part_size == self.chunk_bytes:
                self._finish_part()
        return original_size

    def flush(self):
        if self.handle is not None:
            self.handle.flush()

    def close(self):
        if not self.closed:
            self._finish_part()
        super().close()


def compress_files(root, output, entries, chunk_bytes):
    writer = ChunkWriter(output, chunk_bytes)
    try:
        with gzip.GzipFile(filename="", mode="wb", fileobj=writer, compresslevel=6, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for relative, expected in sorted(entries.items()):
                    path = root / relative
                    item = tarfile.TarInfo("evidence/" + relative)
                    item.size = expected["bytes"]
                    item.mode = 0o755 if expected["executable"] else 0o644
                    item.uid = item.gid = 0
                    item.uname = item.gname = ""
                    item.mtime = 0
                    with path.open("rb") as handle:
                        reader = HashingReader(handle)
                        archive.addfile(item, reader)
                        if reader.digest.hexdigest() != expected["sha256"] or reader.bytes != expected["bytes"]:
                            raise ValueError(f"Evidence changed while archiving: {relative}")
        writer.close()
        return {"archive_sha256": writer.stream_hash.hexdigest(), "archive_bytes": writer.total_size,
                "parts": writer.parts}
    finally:
        writer.close()


class HashingReader:
    """Hash the exact bytes handed to tar, including transient source mutations."""
    def __init__(self, handle):
        self.handle = handle
        self.digest = hashlib.sha256()
        self.bytes = 0

    def read(self, size=-1):
        data = self.handle.read(size)
        self.digest.update(data)
        self.bytes += len(data)
        return data


def check_completion(evidence, audit_path, allow_synthetic):
    synthetic_marker = evidence / "SYNTHETIC.json"
    synthetic = synthetic_marker.exists()
    if synthetic:
        if not allow_synthetic or json_file(synthetic_marker).get("synthetic") is not True:
            raise ValueError("Synthetic fixtures require explicit --allow-synthetic")
        return {"synthetic": True, "scope": "Synthetic archive mechanism test; no study observations"}
    if allow_synthetic:
        raise ValueError("--allow-synthetic is only valid for a marked synthetic fixture")
    if audit_path is None:
        raise ValueError("Real packaging requires --audit-complete")
    if (evidence / "confirm-driver.exit").read_text().strip() != "0" or not (evidence / "ANALYSIS_READY").is_file():
        raise ValueError("Confirmation driver and analysis must have finished successfully")
    if not (evidence / "confirm-driver.ended-utc").is_file():
        raise ValueError("Confirmation driver has not finished writing its terminal record")
    campaign_path = evidence / "campaign.json"
    digest = sha256(campaign_path)
    if digest != REAL_CAMPAIGN_SHA256 or (evidence / "campaign.json.sha256").read_text().strip() != digest:
        raise ValueError("Campaign checksum differs")
    plan = json_file(campaign_path)
    journal_path = evidence / "campaign-results" / "journal.json"
    journal = json_file(journal_path)
    expected = [session["session_id"] for session in plan["sessions"]]
    if (len(expected) != 400 or len(set(expected)) != 400 or set(journal["sessions"]) != set(expected)
            or journal.get("campaign_sha256") != digest or not journal.get("completed_at_utc")):
        raise ValueError("The complete fixed 400-session campaign is required")
    if any(item.get("status") != "succeeded" or len(item.get("attempts", [])) != 1 for item in journal["sessions"].values()):
        raise ValueError("All 400 sessions must succeed without an unreviewed replacement attempt")
    for relative, expected_hash in plan["source"]["files_sha256"].items():
        source = evidence / "campaign.json.source" / relative
        if sha256(source) != expected_hash:
            raise ValueError(f"Frozen source changed: {relative}")
    validation_path = evidence / "validation.json"
    validation = json_file(validation_path)
    if (validation.get("status") != "PASS" or validation.get("campaign_sha256") != digest
            or validation.get("journal_sha256") != sha256(journal_path)):
        raise ValueError("Final independent validation does not bind this campaign/journal")
    analysis = json_file(evidence / "analysis" / "analysis.json")
    if (analysis.get("primary_family_status") != "complete" or len(analysis.get("primary", [])) != 26
            or any(item.get("n_blocks") != 10 for item in analysis["primary"])):
        raise ValueError("The completed predefined analysis is required")
    audit = json_file(audit_path)
    if (audit.get("schema_version") != "research-complete-audit-v1" or audit.get("status") != "PASS"
            or audit.get("campaign_sha256") != digest or audit.get("journal_sha256") != sha256(journal_path)):
        raise ValueError("Complete publication audit must PASS and bind this campaign/journal")
    inputs = audit.get("input_files_sha256")
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError("Publication audit must bind its actual read-file hashes")
    for name, expected_hash in inputs.items():
        path = Path(name)
        if not path.is_absolute() or not path.is_file() or sha256(path) != expected_hash:
            raise ValueError(f"Audited input changed or is unavailable: {name}")
    return {"synthetic": False, "campaign_sha256": digest, "source_commit": plan["source"]["git_head"],
            "journal_sha256": sha256(journal_path), "validation_sha256": sha256(validation_path),
            "audit_sha256": sha256(audit_path), "audit_path": str(Path(audit_path).resolve()),
            "successful_outputs_compared": validation["successful_outputs_compared"]}


def package(evidence, output, *, audit_complete=None, chunk_bytes=1024**3, allow_synthetic=False):
    evidence = Path(evidence).resolve(strict=True)
    output = Path(output).absolute()
    if output.resolve().is_relative_to(evidence):
        raise ValueError("Bundle output must be outside evidence to prevent self-inclusion")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite bundle output: {output}")
    if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or chunk_bytes < 1:
        raise ValueError("chunk_bytes must be a positive integer")
    eligibility = check_completion(evidence, audit_complete, allow_synthetic)
    output.mkdir(parents=True)
    try:
        before = inventory(evidence)
        compression = compress_files(evidence, output, before, chunk_bytes)
        after = inventory(evidence)
        if before != after:
            raise ValueError("Evidence changed during packaging; incomplete bundle retained")
        # Recheck the terminal/audit identity too, including inputs outside the raw tree.
        if eligibility != check_completion(evidence, audit_complete, allow_synthetic):
            raise ValueError("Publication eligibility changed during packaging")
        if audit_complete is not None:
            with (output / "AUDIT_COMPLETE.json").open("xb") as handle:
                handle.write(Path(audit_complete).read_bytes())
            if sha256(output / "AUDIT_COMPLETE.json") != eligibility["audit_sha256"]:
                raise ValueError("Complete-audit bytes changed while copying")
        manifest = {"schema_version": "research-evidence-bundle-v1", "status": "PASS_SYNTHETIC" if eligibility["synthetic"] else "PASS",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "source_root": str(evidence),
                    "packager_sha256": sha256(__file__), "eligibility": eligibility,
                    "software": {"python": sys.version, "zlib_compile": zlib.ZLIB_VERSION,
                                 "zlib_runtime": zlib.ZLIB_RUNTIME_VERSION, "tar_format": "PAX",
                                 "gzip_mtime": 0, "compression_level": 6},
                    "file_count": len(before), "uncompressed_file_bytes": sum(row["bytes"] for row in before.values()),
                    "files": before, "chunk_limit_bytes": chunk_bytes, **compression}
        with (output / "CHUNKS.sha256").open("x") as handle:
            for part in manifest["parts"]:
                handle.write(f"{part['sha256']}  {part['path']}\n")
        label = "SYNTHETIC TEST FIXTURE — NO STUDY RESULTS\n\n" if eligibility["synthetic"] else "Completed research evidence\n\n"
        (output / "REASSEMBLE.md").write_text(label +
            "Verify every part with `sha256sum -c CHUNKS.sha256` (macOS: `shasum -a 256 -c CHUNKS.sha256`).\n\n"
            "Reassemble in lexical part order: `cat evidence.tar.gz.part-* > evidence.tar.gz`.\n\n"
            f"The joined SHA256 must be `{manifest['archive_sha256']}`. Verify before extracting.\n\n"
            "Extract into a new directory with `tar -xzf evidence.tar.gz`. Files are under `evidence/`; "
            "all original JSON paths and bytes are preserved. Use `evidence/campaign-results` as the replay artifact root.\n\n"
            "Use the separate one-command replay to regenerate correctness, analysis and figures. "
            "A bundle PASS certifies archive integrity and its completion gates, not public-release clearance.\n")
        publish_json(output / "BUNDLE_MANIFEST.json", manifest)
        return manifest
    except BaseException as exc:
        publish_json(output / "BUNDLE_FAILED.json", {"status": "FAIL", "type": type(exc).__name__, "message": str(exc),
                     "created_at_utc": datetime.now(timezone.utc).isoformat()})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-complete", type=Path)
    parser.add_argument("--chunk-bytes", type=int, default=1024**3)
    parser.add_argument("--allow-synthetic", action="store_true", help="mechanism tests only; never a study bundle")
    args = parser.parse_args()
    value = package(args.evidence, args.output, audit_complete=args.audit_complete,
                    chunk_bytes=args.chunk_bytes, allow_synthetic=args.allow_synthetic)
    print(json.dumps({key: value[key] for key in ("status", "file_count", "archive_bytes", "archive_sha256")}))


if __name__ == "__main__":
    main()
