"""Complex TPC-H-derived workloads with equivalent SQL and native Polars plans.

Readers must apply workloads.FLOAT_COLUMNS normalization before calling build
or registering SQL views. These workloads are not certified TPC-H queries.
Window ordering includes deterministic tie breakers and explicit NULLS LAST;
exact distinct counts exclude nulls, matching SQL COUNT(DISTINCT ...).
"""

from datetime import date


QUERY_IDS = ["cx_window", "cx_distinct", "cx_self_join"]

REQUIRED_TABLES = {
    "cx_window": ("orders",),
    "cx_distinct": ("lineitem", "orders", "supplier", "nation"),
    "cx_self_join": ("lineitem",),
}

DESCRIPTIONS = {
    "cx_window": "Two customer windows over 1994–1995 orders and top three per customer",
    "cx_distinct": "Four-table join with three exact distinct counts for 1995 orders",
    "cx_self_join": "1994 lineitem equijoin with a residual line-number inequality",
}

SQL = {
    "cx_window": """
        WITH windowed AS (
            SELECT o_custkey AS custkey, o_orderkey AS orderkey,
                o_orderdate AS orderdate, o_totalprice AS totalprice,
                LAG(o_totalprice) OVER (
                    PARTITION BY o_custkey
                    ORDER BY o_orderdate ASC NULLS LAST,
                        o_orderkey ASC NULLS LAST
                ) AS previous_totalprice,
                ROW_NUMBER() OVER (
                    PARTITION BY o_custkey
                    ORDER BY o_totalprice DESC NULLS LAST,
                        o_orderdate ASC NULLS LAST,
                        o_orderkey ASC NULLS LAST
                ) AS row_num
            FROM orders
            WHERE o_orderdate >= DATE '1994-01-01'
                AND o_orderdate < DATE '1996-01-01'
        )
        SELECT custkey, orderkey, orderdate, totalprice, previous_totalprice, row_num
        FROM windowed
        WHERE row_num <= 3
        ORDER BY custkey ASC NULLS LAST, row_num, orderkey ASC NULLS LAST
        LIMIT 100
    """,
    "cx_distinct": """
        SELECT n_name, o_orderpriority,
            COUNT(DISTINCT o_orderkey) AS distinct_orders,
            COUNT(DISTINCT o_custkey) AS distinct_customers,
            COUNT(DISTINCT l_partkey) AS distinct_parts
        FROM lineitem
        JOIN orders ON l_orderkey = o_orderkey
        JOIN supplier ON l_suppkey = s_suppkey
        JOIN nation ON s_nationkey = n_nationkey
        WHERE o_orderdate >= DATE '1995-01-01'
            AND o_orderdate < DATE '1996-01-01'
        GROUP BY n_name, o_orderpriority
        ORDER BY n_name ASC NULLS LAST, o_orderpriority ASC NULLS LAST
    """,
    "cx_self_join": """
        SELECT left_item.l_shipmode AS left_shipmode,
            right_item.l_shipmode AS right_shipmode,
            COUNT(*) AS pair_count
        FROM lineitem AS left_item
        JOIN lineitem AS right_item
            ON left_item.l_orderkey = right_item.l_orderkey
            AND left_item.l_linenumber < right_item.l_linenumber
        WHERE left_item.l_shipdate >= DATE '1994-01-01'
            AND left_item.l_shipdate < DATE '1995-01-01'
            AND right_item.l_shipdate >= DATE '1994-01-01'
            AND right_item.l_shipdate < DATE '1995-01-01'
        GROUP BY left_item.l_shipmode, right_item.l_shipmode
        ORDER BY left_shipmode ASC NULLS LAST, right_shipmode ASC NULLS LAST
    """,
}


def build(query_id: str, tables: dict):
    """Build a native lazy Polars plan without collecting any input or output."""
    import polars as pl

    c = pl.col

    if query_id == "cx_window":
        return (
            tables["orders"]
            .select("o_custkey", "o_orderkey", "o_orderdate", "o_totalprice")
            .filter(
                (c("o_orderdate") >= date(1994, 1, 1))
                & (c("o_orderdate") < date(1996, 1, 1))
            )
            .with_columns(
                # Order each customer partition directly; shift deliberately
                # respects null prices, matching SQL LAG's default behavior.
                c("o_totalprice").shift(1).over(
                    "o_custkey", order_by=[c("o_orderdate"), c("o_orderkey")],
                    nulls_last=True,
                ).alias("previous_totalprice"),
                # Count a guaranteed non-null expression, including on null-key
                # fixtures. Negating the normalized Float64 price implements
                # descending price while keeping the other order keys ascending.
                c("o_orderkey").fill_null(0).cum_count().over(
                    "o_custkey",
                    order_by=[-c("o_totalprice"), c("o_orderdate"), c("o_orderkey")],
                    nulls_last=True,
                ).cast(pl.Int64).alias("row_num"),
            )
            # Both windows must precede the per-customer top-three filter.
            .filter(c("row_num") <= 3)
            .select(
                c("o_custkey").alias("custkey"),
                c("o_orderkey").alias("orderkey"),
                c("o_orderdate").alias("orderdate"),
                c("o_totalprice").alias("totalprice"),
                "previous_totalprice", "row_num",
            )
            .sort(["custkey", "row_num", "orderkey"], nulls_last=True)
            .limit(100)
        )

    if query_id == "cx_distinct":
        return (
            tables["lineitem"].select("l_orderkey", "l_suppkey", "l_partkey")
            .join(
                tables["orders"]
                .filter(
                    (c("o_orderdate") >= date(1995, 1, 1))
                    & (c("o_orderdate") < date(1996, 1, 1))
                )
                .select("o_orderkey", "o_custkey", "o_orderpriority"),
                left_on="l_orderkey", right_on="o_orderkey", how="inner",
            )
            .join(
                tables["supplier"].select("s_suppkey", "s_nationkey"),
                left_on="l_suppkey", right_on="s_suppkey", how="inner",
            )
            .join(
                tables["nation"].select("n_nationkey", "n_name"),
                left_on="s_nationkey", right_on="n_nationkey", how="inner",
            )
            .group_by("n_name", "o_orderpriority")
            .agg(
                # The left join key is retained by Polars' equijoin.
                c("l_orderkey").drop_nulls().n_unique().cast(pl.Int64)
                .alias("distinct_orders"),
                c("o_custkey").drop_nulls().n_unique().cast(pl.Int64)
                .alias("distinct_customers"),
                c("l_partkey").drop_nulls().n_unique().cast(pl.Int64)
                .alias("distinct_parts"),
            )
            .sort(["n_name", "o_orderpriority"], nulls_last=True)
        )

    if query_id == "cx_self_join":
        items = (
            tables["lineitem"]
            .filter(
                (c("l_shipdate") >= date(1994, 1, 1))
                & (c("l_shipdate") < date(1995, 1, 1))
            )
            .select("l_orderkey", "l_linenumber", "l_shipmode")
        )
        return (
            items.select(
                "l_orderkey", c("l_linenumber").alias("left_linenumber"),
                c("l_shipmode").alias("left_shipmode"),
            )
            # Genuine bounded equijoin on order key, followed by the residual
            # inequality. No Cartesian join or algebraic pair-count rewrite.
            .join(
                items.select(
                    "l_orderkey", c("l_linenumber").alias("right_linenumber"),
                    c("l_shipmode").alias("right_shipmode"),
                ),
                on="l_orderkey", how="inner",
            )
            .filter(c("left_linenumber") < c("right_linenumber"))
            .group_by("left_shipmode", "right_shipmode")
            .agg(pl.len().cast(pl.Int64).alias("pair_count"))
            .sort(["left_shipmode", "right_shipmode"], nulls_last=True)
        )

    raise KeyError(f"Unknown query {query_id!r}; expected one of {QUERY_IDS}")
