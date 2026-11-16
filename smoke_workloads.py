"""Three synthetic queries for the pre-round-1 readiness (smoke) test of the Paper 3 harness; not a study workload.

The data is generated (tools/paper3_smoke/generate_smoke_data.py), not TPC-H, and no study query is run. The queries only
give the energy window rule something to act on: sm_light takes milliseconds on Polars and well under 0.2 s elsewhere, so its
window hits the 50-run cap; sm_join takes about a second on the CPU engines (a window of ~10 runs) and sm_scan a fraction of it.
Measured on a 10-core laptop, Polars 2.0, warm page cache: 3 ms, 1.1 s and 0.3 s per run. Same module contract as
workloads.py (QUERY_IDS, DESCRIPTIONS, REQUIRED_TABLES, FLOAT_COLUMNS, spark_sql, polars_query); worker.py and
run_experiment.py load it only for `--suite smoke --synthetic-smoke`, which run_campaign.py cannot pass.
"""

SUITE_VERSION = "smoke-v1"

QUERY_IDS = ["sm_light", "sm_join", "sm_scan"]

DESCRIPTIONS = {
    "sm_light": "Synthetic: filter and grouped aggregates on a 200k-row table (milliseconds on Polars; the window hits the 50-run cap)",
    "sm_join": "Synthetic: join of a 80M-row fact table with a 1M-row dimension, filter and grouped aggregates",
    "sm_scan": "Synthetic: filter and grouped aggregates over the 80M-row fact table",
}
descriptions = DESCRIPTIONS

REQUIRED_TABLES = {
    "sm_light": ("small",),
    "sm_join": ("facts", "dims"),
    "sm_scan": ("facts",),
}

FLOAT_COLUMNS = {
    "small": ("s_v",),
    "facts": ("f_x", "f_y"),
    "dims": ("d_w",),
}

_SQL = {
    "sm_light": """
        SELECT s_grp,
            SUM(s_v) AS sum_v,
            AVG(s_v) AS avg_v,
            COUNT(*) AS n
        FROM small
        WHERE s_v > 0.25
        GROUP BY s_grp
        ORDER BY s_grp
    """,
    "sm_join": """
        SELECT d_grp,
            SUM(f_x * d_w) AS revenue,
            AVG(f_y) AS avg_y,
            COUNT(*) AS n
        FROM facts
        JOIN dims ON f_key = d_key
        WHERE f_y < 0.9
        GROUP BY d_grp
        ORDER BY d_grp
    """,
    "sm_scan": """
        SELECT f_grp,
            SUM(f_x) AS sum_x,
            AVG(f_y) AS avg_y,
            MAX(f_x) AS max_x,
            COUNT(*) AS n
        FROM facts
        WHERE f_y > 0.5
        GROUP BY f_grp
        ORDER BY f_grp
    """,
}


def spark_sql(query_id: str) -> str:
    """Return SQL against identically named, normalized temporary views."""
    return _SQL[query_id].strip()


def polars_query(query_id: str, tables: dict):
    """Build an equivalent lazy CPU query; no data is collected here."""
    import polars as pl

    c = pl.col
    t = tables

    if query_id == "sm_light":
        return (
            t["small"].filter(c("s_v") > 0.25)
            .group_by("s_grp")
            .agg(
                c("s_v").sum().alias("sum_v"),
                c("s_v").mean().alias("avg_v"),
                pl.len().cast(pl.Int64).alias("n"),
            )
            .sort("s_grp")
        )

    if query_id == "sm_join":
        return (
            t["facts"].filter(c("f_y") < 0.9)
            .join(t["dims"], left_on="f_key", right_on="d_key")
            .group_by("d_grp")
            .agg(
                (c("f_x") * c("d_w")).sum().alias("revenue"),
                c("f_y").mean().alias("avg_y"),
                pl.len().cast(pl.Int64).alias("n"),
            )
            .sort("d_grp")
        )

    if query_id == "sm_scan":
        return (
            t["facts"].filter(c("f_y") > 0.5)
            .group_by("f_grp")
            .agg(
                c("f_x").sum().alias("sum_x"),
                c("f_y").mean().alias("avg_y"),
                c("f_x").max().alias("max_x"),
                pl.len().cast(pl.Int64).alias("n"),
            )
            .sort("f_grp")
        )

    raise KeyError(f"Unknown query {query_id!r}; expected one of {QUERY_IDS}")
