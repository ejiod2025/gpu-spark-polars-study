"""Complex join workloads with equivalent Spark SQL and native Polars plans.

These are TPC-H-derived analytical queries, not a certified TPC-H run. Q9 and
Q13 use the qualification parameters from sections 2.4.9 and 2.4.13 of the
official specification:
https://tpc.org/TPC_Documents_Current_Versions/pdf/tpc-h_v2.18.0.pdf

Readers must normalize the monetary and quantity columns in workloads.FLOAT_COLUMNS
to Float64/DOUBLE before building either engine's query. In particular, Q9 uses
floating-point arithmetic rather than TPC-H decimal arithmetic. Q9 and Q13 keep
their complete, small grouped outputs; the custom semi/anti query returns at most
100 customers. All outputs have deterministic ordering on TPC-H data.
"""

from datetime import date


QUERY_IDS = ["cx_q09", "cx_q13", "cx_semi_anti"]

REQUIRED_TABLES = {
    "cx_q09": ("part", "supplier", "lineitem", "partsupp", "orders", "nation"),
    "cx_q13": ("customer", "orders"),
    "cx_semi_anti": ("customer", "orders", "lineitem"),
}

DESCRIPTIONS = {
    "cx_q09": "Six-table join with a composite partsupp key and profit by nation/year",
    "cx_q13": "Left outer join and nested aggregation, including zero-order customers",
    "cx_semi_anti": "Correlated EXISTS/NOT EXISTS with nested semi and anti joins",
}

SQL = {
    "cx_q09": """
        SELECT nation, o_year, SUM(amount) AS sum_profit
        FROM (
            SELECT n_name AS nation, YEAR(o_orderdate) AS o_year,
                l_extendedprice * (1.0 - l_discount)
                    - ps_supplycost * l_quantity AS amount
            FROM lineitem
            JOIN part ON l_partkey = p_partkey
            JOIN supplier ON l_suppkey = s_suppkey
            JOIN partsupp ON l_partkey = ps_partkey AND l_suppkey = ps_suppkey
            JOIN orders ON l_orderkey = o_orderkey
            JOIN nation ON s_nationkey = n_nationkey
            WHERE p_name LIKE '%green%'
        ) AS profit
        GROUP BY nation, o_year
        ORDER BY nation, o_year DESC
    """,
    "cx_q13": """
        SELECT c_count, COUNT(*) AS custdist
        FROM (
            SELECT c_custkey, COUNT(o_orderkey) AS c_count
            FROM customer
            LEFT JOIN (
                SELECT o_custkey, o_orderkey
                FROM orders
                WHERE o_comment NOT LIKE '%special%requests%'
            ) AS eligible_orders ON c_custkey = o_custkey
            GROUP BY c_custkey
        ) AS customer_orders
        GROUP BY c_count
        ORDER BY custdist DESC, c_count DESC
    """,
    "cx_semi_anti": """
        SELECT c.c_custkey, c.c_name, c.c_nationkey
        FROM customer AS c
        WHERE EXISTS (
            SELECT 1
            FROM orders AS o
            WHERE o.o_custkey = c.c_custkey
                AND o.o_orderdate >= DATE '1995-01-01'
                AND o.o_orderdate < DATE '1996-01-01'
                AND EXISTS (
                    SELECT 1
                    FROM lineitem AS l
                    WHERE l.l_orderkey = o.o_orderkey
                        AND l.l_receiptdate > l.l_commitdate
                        AND l.l_shipmode IN ('AIR', 'SHIP')
                )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM orders AS o
            WHERE o.o_custkey = c.c_custkey
                AND o.o_orderdate >= DATE '1996-01-01'
                AND o.o_orderdate < DATE '1997-01-01'
                AND o.o_orderstatus = 'F'
        )
        ORDER BY c.c_custkey
        LIMIT 100
    """,
}


def build(query_id: str, tables: dict):
    """Build a native Polars LazyFrame against normalized tables; never collect."""
    import polars as pl

    c = pl.col

    if query_id == "cx_q09":
        amount = (
            c("l_extendedprice") * (1.0 - c("l_discount"))
            - c("ps_supplycost") * c("l_quantity")
        )
        return (
            tables["lineitem"]
            .join(
                tables["part"].filter(c("p_name").str.contains("green", literal=True)),
                left_on="l_partkey", right_on="p_partkey",
            )
            .join(tables["supplier"], left_on="l_suppkey", right_on="s_suppkey")
            .join(
                tables["partsupp"],
                left_on=["l_partkey", "l_suppkey"],
                right_on=["ps_partkey", "ps_suppkey"],
            )
            .join(tables["orders"], left_on="l_orderkey", right_on="o_orderkey")
            .join(tables["nation"], left_on="s_nationkey", right_on="n_nationkey")
            .with_columns(
                c("n_name").alias("nation"),
                c("o_orderdate").dt.year().alias("o_year"),
            )
            .group_by("nation", "o_year")
            .agg(amount.sum().alias("sum_profit"))
            .select("nation", "o_year", "sum_profit")
            .sort(["nation", "o_year"], descending=[False, True])
        )

    if query_id == "cx_q13":
        # DOTALL makes .* match SQL LIKE's % even if a comment contains newlines.
        # Filtering the right input preserves customers with no eligible orders.
        eligible_orders = tables["orders"].filter(
            ~c("o_comment").str.contains("(?s)special.*requests")
        )
        return (
            tables["customer"]
            .join(eligible_orders, left_on="c_custkey", right_on="o_custkey", how="left")
            .group_by("c_custkey")
            .agg(c("o_orderkey").count().cast(pl.Int64).alias("c_count"))
            .group_by("c_count")
            .agg(pl.len().cast(pl.Int64).alias("custdist"))
            .select("c_count", "custdist")
            .sort(["custdist", "c_count"], descending=[True, True])
        )

    if query_id == "cx_semi_anti":
        late_lineitems = tables["lineitem"].filter(
            (c("l_receiptdate") > c("l_commitdate"))
            & c("l_shipmode").is_in(["AIR", "SHIP"])
        )
        qualifying_orders = (
            tables["orders"]
            .filter(
                (c("o_orderdate") >= date(1995, 1, 1))
                & (c("o_orderdate") < date(1996, 1, 1))
            )
            .join(late_lineitems, left_on="o_orderkey", right_on="l_orderkey", how="semi")
        )
        disqualifying_orders = tables["orders"].filter(
            (c("o_orderdate") >= date(1996, 1, 1))
            & (c("o_orderdate") < date(1997, 1, 1))
            & (c("o_orderstatus") == "F")
        )
        return (
            tables["customer"]
            .join(qualifying_orders, left_on="c_custkey", right_on="o_custkey", how="semi")
            .join(disqualifying_orders, left_on="c_custkey", right_on="o_custkey", how="anti")
            .select("c_custkey", "c_name", "c_nationkey")
            .sort("c_custkey")
            .limit(100)
        )

    raise KeyError(f"Unknown query {query_id!r}; expected one of {QUERY_IDS}")
