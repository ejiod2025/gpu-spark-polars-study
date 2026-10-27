#!/usr/bin/env python3
"""Independent DuckDB execution of complex-suite SQL on the shared files.

Use on a functional-check scale before timed experiments. Oracle execution is
not included as a fourth competitor and must not overlap measured runs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import duckdb
import complex_workloads as suite
from worker import normalized


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=30)
    args = parser.parse_args()
    manifest_path = args.data / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    evidence = {"suite": "complex", "suite_version": suite.SUITE_VERSION,
        "scale_factor": manifest["scale_factor"], "duckdb_version": duckdb.__version__,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "started_at_utc": datetime.now(timezone.utc).isoformat(), "queries": {}}
    with duckdb.connect(":memory:") as connection:
        connection.execute(f"SET threads={args.threads}")
        connection.execute("SET memory_limit='64GB'")
        connection.execute("SET enable_progress_bar=false")
        tables = sorted(set(table for query in suite.QUERY_IDS for table in suite.REQUIRED_TABLES[query]))
        for table in tables:
            glob = str((args.data / table / "*.parquet").resolve()).replace("'", "''")
            columns = connection.execute(f"DESCRIBE SELECT * FROM read_parquet('{glob}')").fetchall()
            projection = ", ".join(f"CAST({row[0]} AS DOUBLE) AS {row[0]}"
                if row[0] in suite.FLOAT_COLUMNS.get(table, ()) else row[0] for row in columns)
            connection.execute(f"CREATE VIEW {table} AS SELECT {projection} FROM read_parquet('{glob}')")
        for query in suite.QUERY_IDS:
            sql = suite.spark_sql(query)
            start = time.perf_counter()
            cursor = connection.execute(sql)
            names = [column[0] for column in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
            evidence["queries"][query] = {"rows": normalized(rows),
                "query_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                "seconds": time.perf_counter() - start}
            print(query, len(rows), flush=True)
    evidence["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
