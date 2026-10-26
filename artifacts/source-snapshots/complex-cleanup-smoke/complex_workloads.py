"""Complex v1: joins, nested queries, windows and exact distinct counts.

These six queries are a separate suite. They are not added to the original
six-query workload score. Q9/Q13 derive from TPC-H; other queries are custom
queries on the same TPC-H-derived tables. All engines read identical files.
"""
import complex_joins
import complex_analytics
from workloads import FLOAT_COLUMNS

SUITE_VERSION = "complex-v1"
QUERY_IDS = [*complex_joins.QUERY_IDS, *complex_analytics.QUERY_IDS]
REQUIRED_TABLES = {**complex_joins.REQUIRED_TABLES, **complex_analytics.REQUIRED_TABLES}
DESCRIPTIONS = {**complex_joins.DESCRIPTIONS, **complex_analytics.DESCRIPTIONS}
CATEGORIES = {
    "cx_q09": "Six-table join and composite join keys",
    "cx_q13": "Left outer join and nested aggregation",
    "cx_semi_anti": "Correlated EXISTS/NOT EXISTS and semi/anti joins",
    "cx_window": "Two window orderings: LAG and ROW_NUMBER",
    "cx_distinct": "Four-table join and three exact distinct counts",
    "cx_self_join": "Fact self-join with an inequality condition",
}


def spark_sql(query_id):
    module = complex_joins if query_id in complex_joins.QUERY_IDS else complex_analytics
    return module.SQL[query_id].strip()


def polars_query(query_id, tables):
    module = complex_joins if query_id in complex_joins.QUERY_IDS else complex_analytics
    return module.build(query_id, tables)
