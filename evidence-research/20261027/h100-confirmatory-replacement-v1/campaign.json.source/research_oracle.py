#!/usr/bin/env python3
"""Independent, untimed DuckDB correctness oracle for the unchanged study suites.

Run separately from performance work. Every SQL query is executed over the
manifest's exact Parquet inventory, with the study's Float64 normalization.
The completed artifact contains all ordered rows, not a sample or digest.
Neither oracle execution nor its progress messages are performance evidence.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import tempfile


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalized(value):
    """Match the workers' JSON representation without importing an engine worker."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalized(item) for item in value]
    return value


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def sql_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def atomic_json(path, value, *, replace=False):
    """Publish complete JSON atomically; final artifacts never replace a pathname."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # link, unlike rename/replace, atomically refuses an existing target.
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def suite_module(name):
    if name not in ("baseline", "complex"):
        raise ValueError(f"Unknown suite: {name}")
    return importlib.import_module("workloads" if name == "baseline" else "complex_workloads")


def input_inventory(data, manifest, tables):
    """Check inventory/size; the campaign's independent byte gate checks hashes.

    Keep declared digests as references, explicitly not freshly computed digests.
    File metadata is checked again after SQL execution to catch concurrent edits.
    """
    inventory = {}
    for table in tables:
        entries = manifest["tables"][table]["files"]
        paths = []
        for entry in entries:
            relative = Path(entry["path"])
            path = data / relative
            if (relative.is_absolute() or not path.resolve().is_relative_to(data)
                    or relative.parent != Path(table) or path.suffix != ".parquet"):
                raise ValueError(f"Unsafe or unexpected manifest path: {relative}")
            stat = path.stat()
            if not path.is_file() or stat.st_size != entry["bytes"]:
                raise ValueError(f"Input size differs from manifest: {path}")
            if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64:
                raise ValueError(f"Missing manifest SHA256: {path}")
            paths.append({"path": str(relative), "bytes": stat.st_size,
                          "declared_sha256": entry["sha256"],
                          "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
                          "device": stat.st_dev, "inode": stat.st_ino})
        declared = [entry["path"] for entry in paths]
        observed = {str(path.relative_to(data)) for path in (data / table).glob("*.parquet")}
        if not declared or len(declared) != len(set(declared)) or set(declared) != observed:
            raise ValueError(f"Parquet inventory differs from manifest: {table}")
        inventory[table] = sorted(paths, key=lambda entry: entry["path"])
    return inventory


def register_views(connection, data, inventory, float_columns):
    for table, entries in inventory.items():
        files = ", ".join(sql_string(data / entry["path"]) for entry in entries)
        source = f"read_parquet([{files}])"
        columns = connection.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()
        projection = ", ".join(
            f"CAST({sql_identifier(row[0])} AS DOUBLE) AS {sql_identifier(row[0])}"
            if row[0] in float_columns.get(table, ()) else sql_identifier(row[0])
            for row in columns)
        connection.execute(f"CREATE VIEW {sql_identifier(table)} AS SELECT {projection} FROM {source}")


def execute_query(connection, sql):
    cursor = connection.execute(sql)
    names = [column[0] for column in cursor.description]
    if len(set(names)) != len(names):
        raise ValueError("Oracle query returned duplicate column names")
    return normalized([dict(zip(names, row)) for row in cursor.fetchall()])


def run_oracle(suite, data, output, *, threads=26, memory_limit="128GB", temp_directory=None):
    """Run the complete suite, checkpointing progress without publishing partial PASS."""
    import duckdb

    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        raise ValueError("threads must be a positive integer")
    module = suite_module(suite)
    data = Path(data).resolve(strict=True)
    output = Path(output).absolute()
    partial = output.with_suffix(".partial.json")
    lock = output.with_suffix(output.suffix + ".lock")
    output.parent.mkdir(parents=True, exist_ok=True)
    if any(os.path.lexists(path) for path in (output, partial, lock)):
        raise FileExistsError(f"Refusing to overwrite oracle output/checkpoint/lock: {output}")
    manifest_path = data / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    tables = sorted({table for query in module.QUERY_IDS for table in module.REQUIRED_TABLES[query]})
    inventory = input_inventory(data, manifest, tables)
    temp_directory = Path(temp_directory).absolute() if temp_directory else output.parent / (output.stem + ".spill")
    temp_directory.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema_version": "research-oracle-v1", "status": "RUNNING", "suite": suite,
        "suite_version": getattr(module, "SUITE_VERSION", "baseline-v1"),
        "scale_factor": manifest["scale_factor"], "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_bytes(manifest_bytes), "started_at_utc": utc_now(),
        "duckdb_version": duckdb.__version__, "python_version": platform.python_version(),
        "float_columns": {table: list(module.FLOAT_COLUMNS.get(table, ())) for table in tables},
        "input_files": inventory,
        "input_verification": "Exact manifest inventory and file sizes checked; metadata stability checked before/after. Declared SHA256 values require the separate campaign byte-verification gate.",
        "timing_scope": "Correctness only; no benchmark timings are collected.",
        "settings": {"threads": threads, "memory_limit": memory_limit,
                     "temp_directory": str(temp_directory)},
        "source_sha256": {}, "queries": {},
    }
    for name in ("research_oracle", "workloads", "complex_workloads", "complex_joins", "complex_analytics"):
        path = Path(__file__).with_name(name + ".py")
        evidence["source_sha256"][path.name] = sha256_bytes(path.read_bytes())
    with lock.open("x") as handle:
        handle.write(f"{os.getpid()}\n")
    owns_checkpoint = False
    try:
        atomic_json(partial, evidence)
        owns_checkpoint = True
        with duckdb.connect(":memory:") as connection:
            connection.execute(f"SET threads = {threads}")
            connection.execute(f"SET memory_limit = {sql_string(memory_limit)}")
            connection.execute(f"SET temp_directory = {sql_string(temp_directory)}")
            connection.execute("SET enable_progress_bar = false")
            evidence["effective_settings"] = {key: connection.execute(
                f"SELECT current_setting({sql_string(key)})").fetchone()[0]
                for key in ("threads", "memory_limit", "temp_directory")}
            register_views(connection, data, inventory, module.FLOAT_COLUMNS)
            for query in module.QUERY_IDS:
                evidence["active_query"] = query
                atomic_json(partial, evidence, replace=True)
                sql = module.spark_sql(query)
                rows = execute_query(connection, sql)
                json.dumps(rows, allow_nan=False)  # reject non-finite output before checkpoint mutation
                evidence["queries"][query] = {"rows": rows, "row_count": len(rows),
                    "query_sha256": sha256_bytes(sql.encode()), "sql": sql,
                    "completed_at_utc": utc_now()}
                atomic_json(partial, evidence, replace=True)
                print(json.dumps({"event": "oracle_query_complete", "suite": suite,
                                  "query": query, "row_count": len(rows)}), flush=True)
        if manifest_path.read_bytes() != manifest_bytes or input_inventory(data, manifest, tables) != inventory:
            raise ValueError("Input manifest or file metadata changed during oracle execution")
        evidence.pop("active_query", None)
        evidence.update(status="PASS", completed_at_utc=utc_now())
        # Retain the separate checkpoint as durable progress, never as a complete oracle.
        checkpoint = {**evidence, "status": "CHECKPOINT", "completed_output": str(output),
                      "note": "All queries finished; only the final output is accepted as an oracle."}
        atomic_json(partial, checkpoint, replace=True)
        atomic_json(output, evidence)
        return evidence
    except BaseException as exc:
        evidence.update(status="FAIL", failed_at_utc=utc_now(),
                        error={"type": type(exc).__name__, "message": str(exc)})
        if owns_checkpoint:
            atomic_json(partial, evidence, replace=True)
        raise
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("baseline", "complex"), required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=26)
    parser.add_argument("--memory-limit", default="128GB")
    parser.add_argument("--temp-directory", type=Path)
    args = parser.parse_args(argv)
    result = run_oracle(args.suite, args.data, args.output, threads=args.threads,
                        memory_limit=args.memory_limit, temp_directory=args.temp_directory)
    print(json.dumps({"event": "oracle_complete", "status": result["status"],
                      "suite": result["suite"], "output": str(args.output)}), flush=True)


if __name__ == "__main__":
    main()
