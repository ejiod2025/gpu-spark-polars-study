#!/usr/bin/env python3
"""Run a small, explicit Spark shuffle-cleanup lifecycle proof via spark-submit."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import query_cleanup
from query_cleanup import CleanupBarrier, CleanupTimeout, discover_blockmgr_dirs


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def query(spark):
    from pyspark.sql import functions as F

    return (
        spark.range(1_000_000, numPartitions=30)
        .select((F.col("id") % 1000).alias("k"), "id")
        .repartition(24, "k")
        .groupBy("k").agg(F.sum("id").alias("sum_id"))
        .orderBy("k").limit(10)
    )


def fresh_query(spark):
    """Return only detached Python rows and text; release every plan/RDD here."""
    frame = query(spark)
    rdd = frame.rdd
    rows = [row.asDict() for row in rdd.collect()]
    plan = frame._jdf.queryExecution().executedPlan().toString()
    return rows, plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"Refusing to replace an existing proof: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "proof_version": "spark-shuffle-cleanup-v1",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), Path(query_cleanup.__file__))
        },
        "all_pass": False,
        "passes": [],
    }
    spark = None
    try:
        from pyspark import SparkConf
        from pyspark.sql import SparkSession

        # Include configured scratch roots in both discovery snapshots.
        configured = SparkConf().get("spark.local.dir", "")
        roots = [Path(p.strip()) for p in configured.split(",") if p.strip()]
        before = discover_blockmgr_dirs(roots)
        spark = SparkSession.builder.appName("query-cleanup-lifecycle-proof").getOrCreate()
        spark.sparkContext.setLogLevel("WARN")
        owned = sorted(discover_blockmgr_dirs(roots) - before)
        record.update(
            spark_version=spark.version,
            spark_master=spark.sparkContext.master,
            before_blockmgr_dirs=sorted(str(p) for p in before),
            owned_blockmgr_dirs=[str(p) for p in owned],
        )
        if not owned:
            raise RuntimeError("No newly owned blockmgr directory; cannot verify cleanup")
        expected = [{"k": k, "sum_id": 499_500_000 + 1000 * k} for k in range(10)]
        record["expected_rows"] = expected
        record["expected_sha256"] = digest(expected)

        # Hold both the Python RDD and its actual JVM RDD dependency chain.
        held_frame = query(spark)
        held_rdd = held_frame.rdd
        held_java_rdd = held_rdd._jrdd.rdd()
        held_rows = [row.asDict() for row in held_rdd.collect()]
        held_plan = held_frame._jdf.queryExecution().executedPlan().toString()
        try:
            control = CleanupBarrier("spark_cpu", spark, owned, timeout_seconds=1.5).cleanup()
            control_status = "inconclusive_held_shuffle_was_cleaned"
        except CleanupTimeout as exc:
            control = exc.record
            control_status = ("expected_timeout" if control["after"]["shuffle_files"] > 0
                              and not control["after"]["active_job_ids"] else "inconclusive")
        record["held_control"] = {
            "status": control_status, "cleanup": control,
            "rows": held_rows, "rows_sha256": digest(held_rows),
            "matches_expected": held_rows == expected, "plan": held_plan,
        }

        # Release references before entering the positive barrier's stack frame.
        held_frame = held_rdd = held_java_rdd = None
        record["released"] = CleanupBarrier("spark_cpu", spark, owned).cleanup()
        for trial in range(1, 3):
            rows, plan = fresh_query(spark)
            cleanup = CleanupBarrier("spark_cpu", spark, owned).cleanup()
            record["passes"].append({
                "trial": trial, "rows": rows, "rows_sha256": digest(rows),
                "matches_expected": rows == expected, "plan": plan, "cleanup": cleanup,
            })
        record["all_pass"] = (
            control_status == "expected_timeout" and held_rows == expected
            and control["before"]["shuffle_files"] > 0
            and record["released"]["status"] == "passed"
            and record["released"]["after"]["shuffle_files"] == 0
            and all(p["matches_expected"] and p["rows_sha256"] == record["expected_sha256"]
                    and p["cleanup"]["status"] == "passed"
                    and p["cleanup"]["after"]["shuffle_files"] == 0
                    for p in record["passes"])
        )
    except Exception as exc:
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if isinstance(exc, CleanupTimeout):
            record["failed_cleanup"] = exc.record
    finally:
        if spark is not None:
            spark.stop()
        record["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
        temporary.replace(args.output)
    print(json.dumps({"all_pass": record["all_pass"], "output": str(args.output)}), flush=True)
    if not record["all_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
