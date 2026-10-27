#!/usr/bin/env python3
"""Untimed cross-engine execution of hand-checked adversarial semantic cases.

For Spark invoke through spark-submit with the same plugin/version settings.
Unsupported GPU expressions are explicit coverage outcomes, never CPU timings.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from analyze_results import compare_values
import research_fixtures as fixtures
from worker import init_polars_gpu, normalized, save, gpu_physical_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("polars", "polars_gpu", "spark_cpu", "spark_gpu"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "plans").mkdir()
    evidence = {"schema_version": "research-fixtures-v1", "suite_version": fixtures.SUITE_VERSION,
                "engine": args.engine, "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "RUNNING", "scope": "untimed semantic diagnostics; not performance observations",
                "queries": {}, "intermediate_expectations": fixtures.INTERMEDIATE_EXPECTATIONS}
    spark = None
    try:
        if args.engine.startswith("polars"):
            import polars as pl
            evidence["version"] = pl.__version__
            tables = fixtures.polars_tables()
            engine = "streaming"
            if args.engine == "polars_gpu":
                engine, evidence["polars_gpu"] = init_polars_gpu(pl, 0, "streaming", {})
        else:
            from pyspark.sql import SparkSession
            from pyspark.sql.types import StructType, StructField, LongType, DoubleType, StringType
            spark = SparkSession.builder.appName("research-adversarial-fixtures").getOrCreate()
            spark.sparkContext.setLogLevel("WARN")
            evidence["version"] = spark.version
            evidence["spark_conf"] = dict(spark.sparkContext.getConf().getAll())
            if args.engine == "spark_gpu" and "com.nvidia.spark.SQLPlugin" not in evidence["spark_conf"].get("spark.plugins", ""):
                raise RuntimeError("GPU Spark fixture run requires RAPIDS plugin")
            types = {"BIGINT": LongType(), "DOUBLE": DoubleType(), "VARCHAR": StringType()}
            for table, columns in fixtures.TABLE_SCHEMAS.items():
                schema = StructType([StructField(name, types[kind], True) for name, kind in columns.items()])
                spark.createDataFrame(fixtures.TABLE_ROWS[table], schema).createOrReplaceTempView(table)
        for query in fixtures.QUERY_IDS:
            try:
                if args.engine.startswith("polars"):
                    frame = fixtures.polars_query(query, tables)
                    rows = normalized(frame.collect(engine=engine).to_dicts())
                    plan = frame.explain()
                    if args.engine == "polars_gpu":
                        plan += "\n== cudf-polars lowering (diagnostic, not runtime trace) ==\n" + gpu_physical_plan(frame, engine)
                else:
                    frame = spark.sql(fixtures.spark_sql(query))
                    rows = normalized([row.asDict(recursive=True) for row in frame.collect()])
                    plan = frame._jdf.queryExecution().executedPlan().toString()
                check = compare_values(fixtures.EXPECTED_ROWS[query], rows)
                plan_name = "plans/" + query + ".txt"
                (args.output / plan_name).write_text(plan + "\n")
                final = plan.split("== Final Plan ==", 1)[-1].split("== Initial Plan ==", 1)[0]
                evidence["queries"][query] = {"status": "PASS" if check["passed"] else "FAIL",
                    "rows": rows, "expected_rows": fixtures.EXPECTED_ROWS[query], "comparison": check,
                    "plan": plan_name, "gpu_operators": sorted(set(re.findall(r"\bGpu[A-Za-z0-9_]+", final))),
                    "left_anti_plan_lines": [line for line in final.splitlines() if "LeftAnti" in line]}
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                unsupported = args.engine == "polars_gpu" and any(
                    token in message.lower() for token in ("notimplementederror", "not supported", "unsupported operations"))
                evidence["queries"][query] = {"status": "UNSUPPORTED" if unsupported else "FAIL", "error": message[:4000]}
            save(args.output / "FIXTURES.json", evidence)
            print(json.dumps({"query": query, "status": evidence["queries"][query]["status"]}), flush=True)
        evidence["supported_queries"] = sum(q["status"] == "PASS" for q in evidence["queries"].values())
        evidence["unsupported_queries"] = [q for q, value in evidence["queries"].items() if value["status"] == "UNSUPPORTED"]
        evidence["status"] = "FAIL" if any(q["status"] == "FAIL" for q in evidence["queries"].values()) else "PASS"
        # Hand-specified input has semi IDs [1,2,3], with a real blocked match 2;
        # result [1,3] cannot arise from an optimized-away empty anti relation.
        anti = evidence["queries"].get("fx_semi_anti", {})
        evidence["nonempty_anti_semantics_passed"] = anti.get("status") == "PASS"
        if args.engine == "spark_gpu":
            evidence["gpu_left_anti_plan_observed"] = any("Gpu" in line for line in anti.get("left_anti_plan_lines", []))
    finally:
        evidence["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        if evidence["status"] == "RUNNING":
            evidence["status"] = "FAIL"
        save(args.output / "FIXTURES.json", evidence)
        if spark is not None:
            spark.stop()
    return 1 if evidence["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
