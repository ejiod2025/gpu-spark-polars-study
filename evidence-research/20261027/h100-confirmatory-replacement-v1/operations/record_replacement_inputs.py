#!/usr/bin/env python3
"""Record replacement dataset domain and generator identity before measurement."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import duckdb


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to replace input-domain evidence")
    result = {"schema_version": "h100-replacement-input-domain-v1",
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "duckdb_version": duckdb.__version__, "scales": {},
              "scope": "Input domain and generation recipe inventory; full byte integrity is a separate campaign gate."}
    with duckdb.connect() as connection:
        connection.execute("SET threads=26")
        for scale in (1, 10, 30, 100, 300):
            root = args.data_root / f"sf{scale}"
            manifest_path = root / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            pattern = str(root / "orders" / "*.parquet").replace("'", "''")
            row = connection.execute(
                "SELECT count(*), count(*) FILTER (WHERE o_totalprice IS NULL), "
                "count(*) FILTER (WHERE o_orderdate IS NULL), "
                "count(*) FILTER (WHERE o_orderkey IS NULL), "
                "count(*) FILTER (WHERE o_custkey IS NULL) "
                f"FROM read_parquet('{pattern}')").fetchone()
            if row[0] != manifest["tables"]["orders"]["rows"]:
                raise ValueError(f"Orders row count differs from manifest at SF{scale}")
            result["scales"][str(scale)] = {
                "manifest_sha256": digest(manifest_path), "orders_rows": row[0],
                "null_counts": dict(zip(("o_totalprice", "o_orderdate", "o_orderkey", "o_custkey"), row[1:])),
                "all_table_rows": {name: value["rows"] for name, value in manifest["tables"].items()},
                "parquet_files": sum(len(value["files"]) for value in manifest["tables"].values()),
                "total_parquet_bytes": manifest["total_parquet_bytes"],
                "generation_threads": manifest["generation_threads"],
                "generation_memory_limit": manifest["generation_memory_limit"],
                "parquet_recipe": manifest["parquet"]}
    result["tpch_extension_files_sha256"] = {
        str(path): digest(path) for path in sorted((Path.home() / ".duckdb" / "extensions").rglob("tpch.duckdb_extension"))}
    result["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"status": "RECORDED", "scales": len(result["scales"]),
                      "output_sha256": digest(args.output)}))


if __name__ == "__main__":
    main()
