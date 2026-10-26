"""Six selected TPC-H-derived analytical workloads; not a certified TPC-H run.

Query constants follow DuckDB's bundled TPC-H queries. Monetary and quantity
columns listed in FLOAT_COLUMNS must be cast to Float64/DOUBLE by each reader.
This intentionally uses floating-point arithmetic, not TPC-H decimal semantics.
Extra sort keys make top-N ties deterministic without changing primary ordering.

Reference: https://github.com/duckdb/duckdb/tree/main/extension/tpch/dbgen/queries
"""

from datetime import date

QUERY_IDS = ["q01", "q03", "q06", "q10", "q12", "q14"]

DESCRIPTIONS = {
    "q01": "Broad scan, arithmetic, and grouped aggregates",
    "q03": "Selective three-table join, aggregation, and top 10",
    "q06": "Selective scan and scalar revenue aggregation",
    "q10": "Four-table join, customer aggregation, and top 20",
    "q12": "Join and conditional grouped counts",
    "q14": "Part join and conditional revenue ratio",
}
descriptions = DESCRIPTIONS

REQUIRED_TABLES = {
    "q01": ("lineitem",),
    "q03": ("lineitem", "orders", "customer"),
    "q06": ("lineitem",),
    "q10": ("lineitem", "orders", "customer", "nation"),
    "q12": ("lineitem", "orders"),
    "q14": ("lineitem", "part"),
}

FLOAT_COLUMNS = {
    "lineitem": ("l_quantity", "l_extendedprice", "l_discount", "l_tax"),
    "customer": ("c_acctbal",),
    "orders": ("o_totalprice",),
    "part": ("p_retailprice",),
    "partsupp": ("ps_supplycost",),
    "supplier": ("s_acctbal",),
}

_SQL = {
    "q01": """
        SELECT l_returnflag, l_linestatus,
            SUM(l_quantity) AS sum_qty,
            SUM(l_extendedprice) AS sum_base_price,
            SUM(l_extendedprice * (1 - l_discount)) AS sum_disc_price,
            SUM(l_extendedprice * (1 - l_discount) * (1 + l_tax)) AS sum_charge,
            AVG(l_quantity) AS avg_qty,
            AVG(l_extendedprice) AS avg_price,
            AVG(l_discount) AS avg_disc,
            COUNT(*) AS count_order
        FROM lineitem
        WHERE l_shipdate <= DATE '1998-09-02'
        GROUP BY l_returnflag, l_linestatus
        ORDER BY l_returnflag, l_linestatus
    """,
    "q03": """
        SELECT l_orderkey,
            SUM(l_extendedprice * (1 - l_discount)) AS revenue,
            o_orderdate, o_shippriority
        FROM lineitem
        JOIN orders ON l_orderkey = o_orderkey
        JOIN customer ON o_custkey = c_custkey
        WHERE c_mktsegment = 'BUILDING'
            AND o_orderdate < DATE '1995-03-15'
            AND l_shipdate > DATE '1995-03-15'
        GROUP BY l_orderkey, o_orderdate, o_shippriority
        ORDER BY revenue DESC, o_orderdate, l_orderkey, o_shippriority
        LIMIT 10
    """,
    "q06": """
        SELECT SUM(l_extendedprice * l_discount) AS revenue
        FROM lineitem
        WHERE l_shipdate >= DATE '1994-01-01'
            AND l_shipdate < DATE '1995-01-01'
            AND l_discount BETWEEN 0.05 AND 0.07
            AND l_quantity < 24
    """,
    "q10": """
        SELECT c_custkey, c_name,
            SUM(l_extendedprice * (1 - l_discount)) AS revenue,
            c_acctbal, n_name, c_address, c_phone, c_comment
        FROM customer
        JOIN orders ON c_custkey = o_custkey
        JOIN lineitem ON o_orderkey = l_orderkey
        JOIN nation ON c_nationkey = n_nationkey
        WHERE o_orderdate >= DATE '1993-10-01'
            AND o_orderdate < DATE '1994-01-01'
            AND l_returnflag = 'R'
        GROUP BY c_custkey, c_name, c_acctbal, n_name,
            c_address, c_phone, c_comment
        ORDER BY revenue DESC, c_custkey
        LIMIT 20
    """,
    "q12": """
        SELECT l_shipmode,
            SUM(CASE WHEN o_orderpriority IN ('1-URGENT', '2-HIGH')
                THEN 1 ELSE 0 END) AS high_line_count,
            SUM(CASE WHEN o_orderpriority NOT IN ('1-URGENT', '2-HIGH')
                THEN 1 ELSE 0 END) AS low_line_count
        FROM lineitem
        JOIN orders ON l_orderkey = o_orderkey
        WHERE l_shipmode IN ('MAIL', 'SHIP')
            AND l_commitdate < l_receiptdate
            AND l_shipdate < l_commitdate
            AND l_receiptdate >= DATE '1994-01-01'
            AND l_receiptdate < DATE '1995-01-01'
        GROUP BY l_shipmode
        ORDER BY l_shipmode
    """,
    "q14": """
        SELECT 100.0 * SUM(CASE WHEN p_type LIKE 'PROMO%'
            THEN l_extendedprice * (1 - l_discount) ELSE 0.0 END)
            / SUM(l_extendedprice * (1 - l_discount)) AS promo_revenue
        FROM lineitem
        JOIN part ON l_partkey = p_partkey
        WHERE l_shipdate >= DATE '1995-09-01'
            AND l_shipdate < DATE '1995-10-01'
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
    discounted = c("l_extendedprice") * (1.0 - c("l_discount"))

    if query_id == "q01":
        return (
            t["lineitem"]
            .filter(c("l_shipdate") <= date(1998, 9, 2))
            .group_by("l_returnflag", "l_linestatus")
            .agg(
                c("l_quantity").sum().alias("sum_qty"),
                c("l_extendedprice").sum().alias("sum_base_price"),
                discounted.sum().alias("sum_disc_price"),
                (discounted * (1.0 + c("l_tax"))).sum().alias("sum_charge"),
                c("l_quantity").mean().alias("avg_qty"),
                c("l_extendedprice").mean().alias("avg_price"),
                c("l_discount").mean().alias("avg_disc"),
                pl.len().cast(pl.Int64).alias("count_order"),
            )
            .sort("l_returnflag", "l_linestatus")
        )

    if query_id == "q03":
        return (
            t["lineitem"].filter(c("l_shipdate") > date(1995, 3, 15))
            .join(
                t["orders"].filter(c("o_orderdate") < date(1995, 3, 15)),
                left_on="l_orderkey", right_on="o_orderkey",
            )
            .join(
                t["customer"].filter(c("c_mktsegment") == "BUILDING"),
                left_on="o_custkey", right_on="c_custkey",
            )
            .group_by("l_orderkey", "o_orderdate", "o_shippriority")
            .agg(discounted.sum().alias("revenue"))
            .select("l_orderkey", "revenue", "o_orderdate", "o_shippriority")
            .sort(
                ["revenue", "o_orderdate", "l_orderkey", "o_shippriority"],
                descending=[True, False, False, False],
            )
            .limit(10)
        )

    if query_id == "q06":
        return (
            t["lineitem"]
            .filter(
                (c("l_shipdate") >= date(1994, 1, 1))
                & (c("l_shipdate") < date(1995, 1, 1))
                & c("l_discount").is_between(0.05, 0.07, closed="both")
                & (c("l_quantity") < 24)
            )
            .select((c("l_extendedprice") * c("l_discount")).sum().alias("revenue"))
        )

    if query_id == "q10":
        groups = ["c_custkey", "c_name", "c_acctbal", "n_name",
                  "c_address", "c_phone", "c_comment"]
        return (
            t["customer"]
            .join(
                t["orders"].filter(
                    (c("o_orderdate") >= date(1993, 10, 1))
                    & (c("o_orderdate") < date(1994, 1, 1))
                ),
                left_on="c_custkey", right_on="o_custkey",
            )
            .join(
                t["lineitem"].filter(c("l_returnflag") == "R"),
                left_on="o_orderkey", right_on="l_orderkey",
            )
            .join(t["nation"], left_on="c_nationkey", right_on="n_nationkey")
            .group_by(groups)
            .agg(discounted.sum().alias("revenue"))
            .select("c_custkey", "c_name", "revenue", "c_acctbal", "n_name",
                    "c_address", "c_phone", "c_comment")
            .sort(["revenue", "c_custkey"], descending=[True, False])
            .limit(20)
        )

    if query_id == "q12":
        high_priority = c("o_orderpriority").is_in(["1-URGENT", "2-HIGH"])
        return (
            t["lineitem"]
            .filter(
                c("l_shipmode").is_in(["MAIL", "SHIP"])
                & (c("l_commitdate") < c("l_receiptdate"))
                & (c("l_shipdate") < c("l_commitdate"))
                & (c("l_receiptdate") >= date(1994, 1, 1))
                & (c("l_receiptdate") < date(1995, 1, 1))
            )
            .join(t["orders"], left_on="l_orderkey", right_on="o_orderkey")
            .group_by("l_shipmode")
            .agg(
                high_priority.cast(pl.Int64).sum().alias("high_line_count"),
                (~high_priority).cast(pl.Int64).sum().alias("low_line_count"),
            )
            .sort("l_shipmode")
        )

    if query_id == "q14":
        return (
            t["lineitem"]
            .filter(
                (c("l_shipdate") >= date(1995, 9, 1))
                & (c("l_shipdate") < date(1995, 10, 1))
            )
            .join(t["part"], left_on="l_partkey", right_on="p_partkey")
            .select(
                (
                    100.0
                    * pl.when(c("p_type").str.starts_with("PROMO"))
                    .then(discounted).otherwise(0.0).sum()
                    / discounted.sum()
                ).alias("promo_revenue")
            )
        )

    raise KeyError(f"Unknown query {query_id!r}; expected one of {QUERY_IDS}")
