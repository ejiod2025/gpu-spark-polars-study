#!/usr/bin/env python3
"""Generate shared, checksummed Parquet for a TPC-H-derived experiment.

DuckDB's tpch extension is a convenient deterministic data generator, with small
differences from the TPC-H specification. These files are not a certified TPC-H
dataset. Decimal columns are retained in the files; query runners may apply the
same documented floating-point projection for all engines.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

import duckdb


TABLES = ("region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem")
FORMAT_VERSION = 1


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scale_name(scale: float) -> str:
    return "sf" + format(scale, "g").replace(".", "p")


def validate_existing(destination: Path, scale: float) -> dict:
    manifest_path = destination / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"Incomplete dataset at {destination}: missing manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("format_version") != FORMAT_VERSION or manifest.get("scale_factor") != scale:
        raise RuntimeError(f"Dataset manifest does not match requested format/scale: {destination}")
    if set(manifest.get("tables", {})) != set(TABLES):
        raise RuntimeError(f"Dataset manifest is missing tables: {destination}")
    for table, metadata in manifest["tables"].items():
        expected_files = set()
        for item in metadata["files"]:
            path = destination / item["path"]
            expected_files.add(path.resolve())
            if not path.is_file() or path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
                raise RuntimeError(f"Dataset checksum validation failed: {path}")
        observed_files = {p.resolve() for p in (destination / table).glob("*.parquet")}
        if observed_files != expected_files:
            raise RuntimeError(f"Unexpected or missing Parquet files in {destination / table}")
    return manifest


def generate(root: Path, scale: float, threads: int, memory_limit: str) -> dict:
    destination = root / scale_name(scale)
    if destination.exists():
        manifest = validate_existing(destination, scale)
        print(json.dumps({"event": "dataset_validated", "path": str(destination), "scale_factor": scale}), flush=True)
        return manifest

    staging = root / ("." + scale_name(scale) + ".building")
    if staging.exists():
        # This directory is owned solely by this generator and was never published.
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    started = time.perf_counter()
    database_path = staging / "generation.duckdb"
    connection = duckdb.connect(str(database_path))
    manifest = {
        "format_version": FORMAT_VERSION,
        "scale_factor": scale,
        "duckdb_version": duckdb.__version__,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": "DuckDB tpch extension; TPC-H-derived, non-certified",
        "generation_sql": f"CALL dbgen(sf = {scale!r})",
        "generation_threads": threads,
        "generation_memory_limit": memory_limit,
        "parquet": {"compression": "snappy", "row_group_size": 122880, "file_size_target": "128MB", "per_thread_output": True},
        "tables": {},
    }
    print(json.dumps({"event": "generation_started", "scale_factor": scale}), flush=True)
    try:
        connection.execute(f"SET threads = {threads}")
        connection.execute(f"SET memory_limit = {sql_string(memory_limit)}")
        connection.execute("SET preserve_insertion_order = false")
        connection.execute("INSTALL tpch")
        connection.execute("LOAD tpch")
        connection.execute(manifest["generation_sql"])
        for table in TABLES:
            table_started = time.perf_counter()
            rows = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            schema = [{"name": row[0], "type": row[1]} for row in connection.execute(f"DESCRIBE {table}").fetchall()]
            table_path = staging / table
            copy_sql = (
                f"COPY {table} TO {sql_string(table_path)} "
                "(FORMAT PARQUET, COMPRESSION SNAPPY, PER_THREAD_OUTPUT TRUE, "
                "FILE_SIZE_BYTES '128MB', ROW_GROUP_SIZE 122880)"
            )
            connection.execute(copy_sql)
            files = sorted(table_path.glob("*.parquet"))
            if not files:
                raise RuntimeError(f"COPY produced no files for {table}")
            exported_rows = connection.execute(
                f"SELECT count(*) FROM read_parquet({sql_string(table_path / '*.parquet')})"
            ).fetchone()[0]
            if rows != exported_rows:
                raise RuntimeError(f"Export row count mismatch for {table}: {rows} != {exported_rows}")
            file_metadata = [
                {"path": str(path.relative_to(staging)), "bytes": path.stat().st_size, "sha256": sha256(path)}
                for path in files
            ]
            manifest["tables"][table] = {
                "rows": rows,
                "parquet_bytes": sum(item["bytes"] for item in file_metadata),
                "schema": schema,
                "files": file_metadata,
                "export_seconds": time.perf_counter() - table_started,
                "export_sql": copy_sql.replace(str(staging), "<dataset>"),
            }
            print(json.dumps({"event": "table_exported", "scale_factor": scale, "table": table, "rows": rows, "files": len(files), "parquet_bytes": manifest["tables"][table]["parquet_bytes"]}), flush=True)
    finally:
        connection.close()

    # Remove transient generation storage before atomically publishing the dataset.
    for suffix in ("", ".wal"):
        (Path(str(database_path) + suffix)).unlink(missing_ok=True)
    spill_path = Path(str(database_path) + ".tmp")
    if spill_path.exists():
        shutil.rmtree(spill_path)
    manifest["total_rows"] = sum(item["rows"] for item in manifest["tables"].values())
    manifest["total_parquet_bytes"] = sum(item["parquet_bytes"] for item in manifest["tables"].values())
    manifest["generation_seconds"] = time.perf_counter() - started
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    staging.rename(destination)
    print(json.dumps({"event": "generation_complete", "path": str(destination), "scale_factor": scale, "seconds": manifest["generation_seconds"], "parquet_bytes": manifest["total_parquet_bytes"]}), flush=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scales", type=float, nargs="+", default=[1, 10, 30])
    parser.add_argument("--threads", type=int, default=30)
    parser.add_argument("--memory-limit", default="64GB")
    args = parser.parse_args()
    if args.threads < 1 or any(not 0 < scale < 1_000_000 for scale in args.scales):
        parser.error("threads and scale factors must be positive; scale must be finite and below 1,000,000")
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for scale in args.scales:
        generate(root, scale, args.threads, args.memory_limit)


if __name__ == "__main__":
    main()
