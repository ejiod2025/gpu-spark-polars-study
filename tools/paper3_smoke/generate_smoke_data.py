#!/usr/bin/env python3
"""Deterministic synthetic Parquet for the Paper 3 readiness (smoke) test; not TPC-H, not a study dataset.

Writes <root>/sf1/{small,facts,dims}/part-NNNN.parquet and <root>/sf1/manifest.json in the layout run_experiment.py reads
(the "sf1" directory name is the runner's scale "1"). Every part is drawn from its own numpy PCG64 stream seeded by
(seed, table, part), so the content depends only on the arguments, not on the order or the number of processes. The manifest is
marked synthetic_smoke. Only polars and numpy are needed (both are in the GPU environment of setup_h100.sh).

  small  s_id BIGINT, s_grp INT (0..99), s_v DOUBLE [0,1)                 200,000 rows   (query sm_light)
  facts  f_key BIGINT (0..dims-1), f_grp INT (0..999), f_x, f_y DOUBLE    80,000,000 rows (queries sm_join, sm_scan)
  dims   d_key BIGINT (0..dims-1), d_grp INT (0..99), d_w DOUBLE [.5,1.5) 1,000,000 rows (query sm_join)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

FORMAT_VERSION = 1
SEED = 20261116
TABLES = ("small", "facts", "dims")
TABLE_ID = {"small": 1, "facts": 2, "dims": 3}
SMALL_GROUPS, FACT_GROUPS, DIM_GROUPS = 100, 1000, 100
DEFAULT_ROWS = {"small": 200_000, "facts": 80_000_000, "dims": 1_000_000}
ROW_GROUP_SIZE = 122_880


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def part_frame(table: str, first: int, count: int, rows: dict, seed: int, part: int):
    """The rows [first, first+count) of a table as a polars DataFrame; depends on (seed, table, part) only."""
    import numpy as np
    import polars as pl

    rng = np.random.default_rng([seed, TABLE_ID[table], part])
    if table == "small":
        return pl.DataFrame({"s_id": np.arange(first, first + count, dtype=np.int64),
                             "s_grp": rng.integers(0, SMALL_GROUPS, count, dtype=np.int32), "s_v": rng.random(count)})
    if table == "facts":
        return pl.DataFrame({"f_key": rng.integers(0, rows["dims"], count, dtype=np.int64),
                             "f_grp": rng.integers(0, FACT_GROUPS, count, dtype=np.int32),
                             "f_x": rng.random(count), "f_y": rng.random(count)})
    return pl.DataFrame({"d_key": np.arange(first, first + count, dtype=np.int64),
                         "d_grp": rng.integers(0, DIM_GROUPS, count, dtype=np.int32), "d_w": 0.5 + rng.random(count)})


def split(total: int, parts: int):
    """(first, count) per part: as even as possible, no empty part."""
    parts = max(1, min(parts, total))
    base, extra = divmod(total, parts)
    first = 0
    for index in range(parts):
        count = base + (1 if index < extra else 0)
        yield index, first, count
        first += count


def generate(root: Path, rows: dict, parts: dict, seed: int) -> dict:
    import numpy as np
    import polars as pl

    destination = root / "sf1"
    if destination.exists():
        raise SystemExit(f"{destination} already exists: remove it first (the smoke data is cheap to regenerate)")
    staging = root / ".sf1.building"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    started = time.perf_counter()
    manifest = {
        "format_version": FORMAT_VERSION, "scale_factor": 1, "synthetic_smoke": True,
        "dataset": "synthetic readiness data for the Paper 3 smoke test; not TPC-H, not a study dataset",
        "generator": "tools/paper3_smoke/generate_smoke_data.py", "seed": seed,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "polars_version": pl.__version__, "numpy_version": np.__version__,
        "parquet": {"compression": "snappy", "row_group_size": ROW_GROUP_SIZE},
        "tables": {},
    }
    for table in TABLES:
        table_started = time.perf_counter()
        directory = staging / table
        directory.mkdir()
        files, schema = [], None
        for part, first, count in split(rows[table], parts[table]):
            frame = part_frame(table, first, count, rows, seed, part)
            schema = schema or [{"name": n, "type": str(t)} for n, t in frame.schema.items()]
            path = directory / f"part-{part:04d}.parquet"
            frame.write_parquet(path, compression="snappy", row_group_size=ROW_GROUP_SIZE)
            files.append({"path": str(path.relative_to(staging)), "bytes": path.stat().st_size, "sha256": sha256(path)})
        manifest["tables"][table] = {"rows": rows[table], "parquet_bytes": sum(f["bytes"] for f in files), "schema": schema, "files": files,
                                     "export_seconds": time.perf_counter() - table_started}
        print(json.dumps({"event": "table_written", "table": table, "rows": rows[table], "files": len(files),
                          "parquet_bytes": manifest["tables"][table]["parquet_bytes"]}), flush=True)
    manifest["total_rows"] = sum(t["rows"] for t in manifest["tables"].values())
    manifest["total_parquet_bytes"] = sum(t["parquet_bytes"] for t in manifest["tables"].values())
    manifest["generation_seconds"] = time.perf_counter() - started
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    staging.rename(destination)
    print(json.dumps({"event": "generation_complete", "path": str(destination), "seconds": manifest["generation_seconds"],
                      "parquet_bytes": manifest["total_parquet_bytes"]}), flush=True)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True, help="data root; the dataset is written to <root>/sf1")
    parser.add_argument("--small-rows", type=int, default=DEFAULT_ROWS["small"])
    parser.add_argument("--facts-rows", type=int, default=DEFAULT_ROWS["facts"])
    parser.add_argument("--dims-rows", type=int, default=DEFAULT_ROWS["dims"])
    parser.add_argument("--facts-parts", type=int, default=8, help="Parquet files of the fact table (Spark parallelism and polars scan)")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    rows = {"small": args.small_rows, "facts": args.facts_rows, "dims": args.dims_rows}
    if any(count < 1 for count in rows.values()) or args.facts_parts < 1:
        parser.error("row counts and --facts-parts must be positive")
    generate(args.root.expanduser().resolve(), rows, {"small": 1, "facts": args.facts_parts, "dims": 1}, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
