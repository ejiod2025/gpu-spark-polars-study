#!/usr/bin/env python3
"""Untimed evidence for the original window port's non-null input domain."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import duckdb

data = Path(sys.argv[1])
output = Path(sys.argv[2])
if output.exists():
    raise FileExistsError(output)
evidence = {"schema_version": "h100-window-input-domain-v1", "duckdb_version": duckdb.__version__,
            "status": "RUNNING", "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "scales": []}
with duckdb.connect(":memory:") as connection:
    connection.execute("SET threads=26")
    connection.execute("SET memory_limit='64GB'")
    for scale in ("1", "10", "30", "100", "300"):
        manifest = data / f"sf{scale}" / "manifest.json"
        glob = str(data / f"sf{scale}" / "orders" / "*.parquet").replace("'", "''")
        sql = ("SELECT count(*) AS total_orders, "
               "count(*) - count(o_totalprice) AS null_prices, "
               "count(*) - count(o_orderdate) AS null_dates, "
               "count(*) - count(o_orderkey) AS null_order_keys, "
               "count(*) - count(o_custkey) AS null_customer_keys "
               f"FROM read_parquet('{glob}')")
        cursor = connection.execute(sql)
        values = dict(zip([column[0] for column in cursor.description], cursor.fetchone()))
        record = {"scale": scale, "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                  "sql": sql, "query_sha256": hashlib.sha256(sql.encode()).hexdigest(), **values}
        if values["total_orders"] != json.loads(manifest.read_text())["tables"]["orders"]["rows"]:
            raise ValueError(f"Order count differs from manifest at {scale}")
        if any(value != 0 for key, value in values.items() if key.startswith("null_")):
            raise ValueError(f"Original window port domain has nulls at {scale}: {values}")
        evidence["scales"].append(record)
        print(json.dumps(record), flush=True)
evidence.update(status="PASS", completed_at_utc=datetime.now(timezone.utc).isoformat())
with output.open("x") as handle:
    json.dump(evidence, handle, indent=2, allow_nan=False)
    handle.write("\n")
