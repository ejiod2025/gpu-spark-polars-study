#!/usr/bin/env python3
"""Generate large shared Parquet datasets using isolated DuckDB dbgen children.

Each process holds at most one child in an in-memory database, exports it, and
exits. No full generation.duckdb file is created. Completed children are resumable
and checksummed. The final manifest and flat table directories are compatible
with generate_data.py and the existing six-query runners.

Example for the 30-vCPU / 216-GiB Lambda host:
  python generate_large_data.py --root data --scales 100 300 --workers 12
  python generate_large_data.py --self-test

Default chunk scale 5 means at most 60 scale factors resident with 12 workers.
Memory limits apply per worker; allow additional memory for dbgen's own buffers.
SF1000 needs roughly 350 GB of Parquet with this experiment's Snappy settings;
remove older datasets first if the combined size would exceed available space.

References (same source/version as the original generator):
  https://duckdb.org/docs/1.5/core_extensions/tpch
  https://github.com/duckdb/duckdb/blob/v1.5.6/extension/tpch/dbgen/dbgen.cpp
The explicit partial-generation path is serial inside each dbgen call. Separate
OS processes avoid sharing dbgen's global state. All eight tables, including
region and nation, are partitioned by dbgen: export each child's subset once.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import math
import multiprocessing
from pathlib import Path
import shutil
import tempfile
import time

import duckdb

from generate_data import FORMAT_VERSION, TABLES, scale_name, sha256, sql_string, validate_existing
from workloads import FLOAT_COLUMNS, QUERY_IDS, spark_sql


DUCKDB_VERSION = "1.5.6"
KEYS = {
    "region": ("r_regionkey",), "nation": ("n_nationkey",),
    "supplier": ("s_suppkey",), "customer": ("c_custkey",),
    "part": ("p_partkey",), "partsupp": ("ps_partkey", "ps_suppkey"),
    "orders": ("o_orderkey",), "lineitem": ("l_orderkey", "l_linenumber"),
}
PARQUET = {"compression": "snappy", "row_group_size": 122880,
           "file_size_target": "128MB", "per_thread_output": True}


def emit(event: str, **values) -> None:
    print(json.dumps({"event": event, **values}), flush=True)


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def load_tpch(connection) -> dict:
    connection.execute("LOAD tpch")
    version, origin = connection.execute(
        "SELECT extension_version, installed_from FROM duckdb_extensions() "
        "WHERE extension_name = 'tpch'"
    ).fetchone()
    if version != "v" + DUCKDB_VERSION:
        raise RuntimeError(f"Expected tpch v{DUCKDB_VERSION}, got {version}")
    return {"extension_version": version, "installed_from": origin}


def fingerprint(connection, table: str, relation: str | None = None) -> dict:
    relation = relation or table
    schema = [{"name": row[0], "type": row[1]}
              for row in connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()]
    columns = ", ".join('"' + item["name"] + '"' for item in schema)
    key_columns = ", ".join(KEYS[table])
    row = connection.execute(
        f"SELECT count(*), min({KEYS[table][0]}), max({KEYS[table][0]}), "
        f"coalesce(sum(hash({columns})::HUGEINT), 0)::VARCHAR, "
        f"coalesce(bit_xor(hash({columns})), 0)::VARCHAR, "
        f"coalesce(sum(hash({key_columns})::HUGEINT), 0)::VARCHAR, "
        f"coalesce(bit_xor(hash({key_columns})), 0)::VARCHAR FROM {relation}"
    ).fetchone()
    return {"rows": row[0], "schema": schema, "key_columns": list(KEYS[table]),
            "first_key_min": row[1], "first_key_max": row[2],
            "row_hash_sum": row[3], "row_hash_xor": row[4],
            "key_hash_sum": row[5], "key_hash_xor": row[6]}


def validate_files(staging: Path, metadata: dict) -> None:
    for table in TABLES:
        for item in metadata["tables"][table]["files"]:
            path = staging / item["path"]
            if not path.is_file() or path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
                raise RuntimeError(f"Completed child file failed checksum: {path}")


def generate_child(job: dict) -> dict:
    """One child per spawned process prevents retained memory between children."""
    staging, step = Path(job["staging"]), job["step"]
    step_path = staging / ".steps" / f"step-{step:06d}.json"
    if step_path.exists():
        metadata = json.loads(step_path.read_text())
        validate_files(staging, metadata)
        return metadata
    work = staging / ".work" / f"step-{step:06d}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    # A crash may leave files moved before the child metadata was committed.
    for table in TABLES:
        for orphan in (staging / table).glob(f"part-{step:06d}-*.parquet"):
            orphan.unlink()
    started = time.perf_counter()
    connection = duckdb.connect(":memory:")
    metadata = {"step": step, "tables": {}}
    try:
        connection.execute(f"SET threads = {job['threads']}")
        connection.execute(f"SET memory_limit = {sql_string(job['memory_limit'])}")
        connection.execute(f"SET temp_directory = {sql_string(work / 'spill')}")
        connection.execute(f"SET max_temp_directory_size = {sql_string(job['spill_limit'])}")
        connection.execute("SET preserve_insertion_order = false")
        load_tpch(connection)
        generation_started = time.perf_counter()
        connection.execute(
            f"CALL dbgen(sf = {job['scale']!r}, children = {job['children']}, step = {step})"
        )
        metadata["dbgen_seconds"] = time.perf_counter() - generation_started
        for table in TABLES:
            info = fingerprint(connection, table)
            info["files"] = []
            if info["rows"]:
                table_work = work / table
                connection.execute(
                    f"COPY {table} TO {sql_string(table_work)} "
                    "(FORMAT PARQUET, COMPRESSION SNAPPY, PER_THREAD_OUTPUT TRUE, "
                    "FILE_SIZE_BYTES '128MB', ROW_GROUP_SIZE 122880)"
                )
                exported_rows = connection.execute(
                    f"SELECT count(*) FROM read_parquet({sql_string(table_work / '*.parquet')})"
                ).fetchone()[0]
                if exported_rows != info["rows"]:
                    raise RuntimeError(f"Child {step} export count mismatch for {table}")
                for index, source in enumerate(sorted(table_work.glob("*.parquet"))):
                    path = staging / table / f"part-{step:06d}-{index:03d}.parquet"
                    source.replace(path)
                    info["files"].append({"path": str(path.relative_to(staging)),
                                          "bytes": path.stat().st_size, "sha256": sha256(path)})
                if not info["files"]:
                    raise RuntimeError(f"Child {step} produced no files for nonempty {table}")
            info["parquet_bytes"] = sum(item["bytes"] for item in info["files"])
            metadata["tables"][table] = info
            connection.execute(f"DROP TABLE {table}")
    finally:
        connection.close()
    metadata["seconds"] = time.perf_counter() - started
    metadata["parquet_bytes"] = sum(item["parquet_bytes"] for item in metadata["tables"].values())
    write_json(step_path, metadata)
    shutil.rmtree(work)
    return metadata


def combine_children(children: list[dict], scale: float) -> dict:
    tables = {}
    for table in TABLES:
        parts = [child["tables"][table] for child in sorted(children, key=lambda x: x["step"])]
        if any(part["schema"] != parts[0]["schema"] for part in parts):
            raise RuntimeError(f"Schema mismatch between children for {table}")
        nonempty = [part for part in parts if part["rows"]]
        if not nonempty:
            raise RuntimeError(f"Empty final table {table}")
        # Leading keys repeat within lineitem/partsupp, but must never cross children.
        for left, right in zip(nonempty, nonempty[1:]):
            if left["first_key_max"] >= right["first_key_min"]:
                raise RuntimeError(f"Overlapping child key ranges for {table}")
        tables[table] = {"rows": sum(part["rows"] for part in parts),
                         "schema": parts[0]["schema"], "key_columns": list(KEYS[table]),
                         "first_key_min": nonempty[0]["first_key_min"],
                         "first_key_max": nonempty[-1]["first_key_max"],
                         "files": [item for part in parts for item in part["files"]],
                         "parquet_bytes": sum(part["parquet_bytes"] for part in parts)}
        for kind in ("row", "key"):
            tables[table][kind + "_hash_sum"] = str(sum(int(part[kind + "_hash_sum"]) for part in parts))
            xor = 0
            for part in parts:
                xor ^= int(part[kind + "_hash_xor"])
            tables[table][kind + "_hash_xor"] = str(xor)
    bases = {"supplier": 10000, "customer": 150000, "part": 200000, "orders": 1500000}
    expected = {table: int(scale) * base if scale >= 1 else max(1, int(scale * 1000) * base // 1000)
                for table, base in bases.items()}
    expected.update(region=5, nation=25, partsupp=4 * expected["part"])
    for table, rows in expected.items():
        if tables[table]["rows"] != rows:
            raise RuntimeError(f"Unexpected {table} count: {tables[table]['rows']} != {rows}")
    return tables


def generate(root: Path, scale: float, workers: int = 12, chunk_scale: float = 5,
             memory_limit: str = "8GB", threads: int = 1, spill_limit: str = "1GB",
             children_override: int | None = None) -> dict:
    destination = root / scale_name(scale)
    if destination.exists():
        manifest = validate_existing(destination, scale)
        emit("dataset_validated", path=str(destination), scale_factor=scale)
        return manifest
    children_count = children_override or max(1, math.ceil(scale / chunk_scale))
    staging = root / ("." + scale_name(scale) + ".parallel-building")
    config = {"scale_factor": scale, "children": children_count,
              "duckdb_version": DUCKDB_VERSION, "parquet": PARQUET}
    # Advisory lock also protects against two invocations resuming the same stage.
    with (root / ("." + scale_name(scale) + ".generation.lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Another generator owns {scale_name(scale)}") from error
        if destination.exists():
            return validate_existing(destination, scale)
        staging.mkdir(exist_ok=True)
        config_path = staging / "generation_config.json"
        if config_path.exists() and json.loads(config_path.read_text()) != config:
            raise RuntimeError(f"Resume configuration changed; inspect or remove {staging}")
        write_json(config_path, config)
        for table in (*TABLES, ".work", ".steps"):
            (staging / table).mkdir(exist_ok=True)
        started = time.perf_counter()
        emit("generation_started", scale_factor=scale, children=children_count,
             workers=min(workers, children_count), chunk_scale=scale / children_count,
             memory_limit_per_worker=memory_limit, available_disk_bytes=shutil.disk_usage(root).free)
        # Install once, before spawning workers, to avoid extension-install races.
        with duckdb.connect(":memory:") as connection:
            connection.execute("INSTALL tpch")
            extension = load_tpch(connection)
        jobs = [{"staging": str(staging), "scale": scale, "children": children_count,
                 "step": step, "threads": threads, "memory_limit": memory_limit,
                 "spill_limit": spill_limit} for step in range(children_count)]
        completed = []
        with multiprocessing.get_context("spawn").Pool(
                processes=min(workers, children_count), maxtasksperchild=1) as pool:
            for child in pool.imap_unordered(generate_child, jobs, chunksize=1):
                completed.append(child)
                emit("child_complete", scale_factor=scale, step=child["step"],
                     completed=len(completed), children=children_count,
                     seconds=child["seconds"], parquet_bytes=child["parquet_bytes"])
        tables = combine_children(completed, scale)
        for table, info in tables.items():
            observed = {path.relative_to(staging).as_posix() for path in (staging / table).glob("*.parquet")}
            if observed != {item["path"] for item in info["files"]}:
                raise RuntimeError(f"Unexpected or missing files for {table}")
        manifest = {"format_version": FORMAT_VERSION, **config,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "dataset": "DuckDB tpch extension; TPC-H-derived, non-certified",
                    "generation_method": "isolated in-memory dbgen children; resumable process pool",
                    "generation_sql": f"CALL dbgen(sf = {scale!r}, children = {children_count}, step = <0..{children_count-1}>)",
                    "generation_workers": min(workers, children_count), "generation_threads_per_worker": threads,
                    "generation_memory_limit_per_worker": memory_limit,
                    "generation_spill_limit_per_worker": spill_limit, "tpch_extension": extension,
                    "fingerprint_algorithm": "DuckDB1.5.6 hash(all columns/primary key); HUGEINT sum and uint64 xor; order-independent",
                    "tables": tables,
                    "chunks": [{key: child[key] for key in ("step", "dbgen_seconds", "seconds", "parquet_bytes")}
                               for child in sorted(completed, key=lambda x: x["step"])],
                    "generation_seconds": time.perf_counter() - started}
        manifest["total_rows"] = sum(table["rows"] for table in tables.values())
        manifest["total_parquet_bytes"] = sum(table["parquet_bytes"] for table in tables.values())
        write_json(staging / "manifest.json", manifest)
        for name in (".steps", ".work"):
            shutil.rmtree(staging / name)
        config_path.unlink()
        staging.rename(destination)
        emit("generation_complete", path=str(destination), scale_factor=scale,
             seconds=manifest["generation_seconds"], parquet_bytes=manifest["total_parquet_bytes"])
        return manifest


def normalized_views(connection, schema: str, sources: dict[str, str]) -> None:
    connection.execute(f"CREATE SCHEMA {schema}")
    for table, source in sources.items():
        columns = [row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]
        projection = ", ".join(f"CAST({column} AS DOUBLE) AS {column}"
                               if column in FLOAT_COLUMNS.get(table, ()) else column for column in columns)
        connection.execute(f"CREATE VIEW {schema}.{table} AS SELECT {projection} FROM {source}")


def canonical(value):
    if isinstance(value, float):
        return format(value, ".9g")
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    return value


def self_test(output: Path | None = None) -> dict:
    """Exact multiset, key, fingerprint and six-query parity proof; no remote work."""
    started = time.perf_counter()
    report = {"duckdb_version": duckdb.__version__, "checks": []}
    with tempfile.TemporaryDirectory(prefix="tpch-child-proof-") as temporary:
        for scale, children_count in ((0.01, 7), (0.013, 31), (1.0, 7)):
            root = Path(temporary) / scale_name(scale)
            root.mkdir()
            manifest = generate(root, scale, workers=4, children_override=children_count, memory_limit="2GB")
            destination = root / scale_name(scale)
            check = {"scale_factor": scale, "children": children_count, "tables": {}, "queries": {}}
            with duckdb.connect(":memory:") as connection:
                connection.execute("SET threads = 2")
                load_tpch(connection)
                connection.execute(f"CALL dbgen(sf = {scale!r})")
                for table in TABLES:
                    source = f"read_parquet({sql_string(destination / table / '*.parquet')})"
                    reference, chunked = fingerprint(connection, table), fingerprint(connection, table, source)
                    if reference != chunked:
                        raise AssertionError(f"Fingerprint mismatch: SF{scale} {table}")
                    if any(chunked[key] != manifest["tables"][table][key] for key in chunked):
                        raise AssertionError(f"Manifest fingerprint mismatch: SF{scale} {table}")
                    differences = connection.execute(
                        f"SELECT count(*) FROM ((SELECT * FROM {table} EXCEPT ALL SELECT * FROM {source}) "
                        f"UNION ALL (SELECT * FROM {source} EXCEPT ALL SELECT * FROM {table}))"
                    ).fetchone()[0]
                    unique_keys = connection.execute(
                        f"SELECT count(DISTINCT ({', '.join(KEYS[table])})) FROM {source}"
                    ).fetchone()[0]
                    if differences or unique_keys != reference["rows"]:
                        raise AssertionError(f"Multiset/key mismatch: SF{scale} {table}")
                    check["tables"][table] = {**reference, "exact_multiset_differences": differences,
                                              "distinct_primary_keys": unique_keys}
                normalized_views(connection, "reference", {table: f"main.{table}" for table in TABLES})
                normalized_views(connection, "chunked", {table: f"read_parquet({sql_string(destination / table / '*.parquet')})"
                                                          for table in TABLES})
                for query_id in QUERY_IDS:
                    connection.execute("SET schema = 'reference'")
                    reference = connection.execute(spark_sql(query_id)).fetchall()
                    connection.execute("SET schema = 'chunked'")
                    chunked = connection.execute(spark_sql(query_id)).fetchall()
                    if len(reference) != len(chunked):
                        raise AssertionError(f"Query row count mismatch: {query_id}")
                    for left, right in zip(reference, chunked):
                        for a, b in zip(left, right):
                            if isinstance(a, float):
                                if not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-6):
                                    raise AssertionError(f"Query numeric mismatch: {query_id}: {a} != {b}")
                            elif a != b:
                                raise AssertionError(f"Query value mismatch: {query_id}: {a} != {b}")
                    reference_hash = hashlib.sha256(json.dumps(canonical(reference), separators=(",", ":")).encode()).hexdigest()
                    chunked_hash = hashlib.sha256(json.dumps(canonical(chunked), separators=(",", ":")).encode()).hexdigest()
                    # Floating reductions can straddle a rounding boundary, even
                    # when differences are tiny; report both hashes and enforce
                    # the experiment's numeric comparison instead of hash equality.
                    check["queries"][query_id] = {"rows": len(reference),
                        "reference_sha256_9_significant_digits": reference_hash,
                        "children_sha256_9_significant_digits": chunked_hash,
                        "numeric_equivalence": True, "relative_tolerance": 1e-10,
                        "absolute_tolerance": 1e-6}
            report["checks"].append(check)
            emit("equivalence_passed", scale_factor=scale, children=children_count,
                 tables=len(check["tables"]), queries=len(check["queries"]))
    report["seconds"] = time.perf_counter() - started
    if output:
        write_json(output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--scales", type=float, nargs="+", default=[100, 300])
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--chunk-scale", type=float, default=5)
    parser.add_argument("--threads-per-worker", type=int, default=1)
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--spill-limit", default="1GB")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--proof-output", type=Path)
    args = parser.parse_args()
    if duckdb.__version__ != DUCKDB_VERSION:
        parser.error(f"Requires duckdb=={DUCKDB_VERSION}; found {duckdb.__version__}")
    if args.self_test:
        report = self_test(args.proof_output)
        emit("self_test_passed", seconds=report["seconds"], configurations=len(report["checks"]))
        return
    if not args.root:
        parser.error("--root is required unless --self-test is selected")
    if (args.workers < 1 or args.threads_per_worker < 1 or not math.isfinite(args.chunk_scale)
            or args.chunk_scale <= 0 or any(not math.isfinite(scale) or not 1 <= scale <= 100000
                                           or not scale.is_integer() for scale in args.scales)):
        parser.error("workers/threads/chunk-scale must be positive; scales must be integers from 1 to 100000")
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for scale in args.scales:
        generate(root, scale, args.workers, args.chunk_scale, args.memory_limit,
                 args.threads_per_worker, args.spill_limit)


if __name__ == "__main__":
    main()
