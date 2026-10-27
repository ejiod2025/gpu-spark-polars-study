"""Independent correctness, adversarial semantics, and durable oracle publication."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import research_fixtures as fixtures
import research_oracle as oracle

duckdb = pytest.importorskip("duckdb")


@pytest.mark.parametrize("query", fixtures.QUERY_IDS)
def test_hand_checked_sql_and_native_polars_results(query):
    with duckdb.connect() as connection:
        fixtures.register_duckdb(connection)
        sql_rows = oracle.execute_query(connection, fixtures.spark_sql(query))
    assert sql_rows == fixtures.EXPECTED_ROWS[query]
    pytest.importorskip("polars")
    native = fixtures.polars_query(query, fixtures.polars_tables()).collect().to_dicts()
    assert native == fixtures.EXPECTED_ROWS[query]


def test_anti_fixture_excludes_a_real_nonempty_matching_branch():
    with duckdb.connect() as connection:
        fixtures.register_duckdb(connection)
        semi = connection.execute("SELECT e.entity_id FROM fx_entities e WHERE EXISTS "
            "(SELECT 1 FROM fx_allowed a WHERE a.entity_id=e.entity_id) ORDER BY e.entity_id").fetchall()
        removed = connection.execute("SELECT e.entity_id FROM fx_entities e WHERE EXISTS "
            "(SELECT 1 FROM fx_allowed a WHERE a.entity_id=e.entity_id) AND EXISTS "
            "(SELECT 1 FROM fx_blocked b WHERE b.entity_id=e.entity_id) ORDER BY e.entity_id").fetchall()
    expected = fixtures.INTERMEDIATE_EXPECTATIONS["fx_semi_anti"]
    assert [row[0] for row in semi] == expected["semi_entity_ids"]
    assert [row[0] for row in removed] == expected["excluded_entity_ids"] == [2]


def test_original_window_port_null_ordering_limitation_is_preserved_and_explicit():
    """Exercise the original query, not a copy of its expression or corrected case."""
    pl = pytest.importorskip("polars")
    if pl.__version__ != "1.44.2":
        pytest.skip("Regression records the observed pinned Polars 1.44.2 behavior")
    import complex_workloads
    day = date(1995, 1, 1)
    rows = [(1 if group == "a" else None, item, day, score)
            for group, item, score in fixtures.TABLE_ROWS["fx_scores"]]
    frame = pl.DataFrame(rows, schema={"o_custkey": pl.Int64, "o_orderkey": pl.Int64,
        "o_orderdate": pl.Date, "o_totalprice": pl.Float64}, orient="row")
    actual = complex_workloads.polars_query("cx_window", {"orders": frame.lazy()}).collect().to_dicts()
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE orders(o_custkey BIGINT, o_orderkey BIGINT, "
                           "o_orderdate DATE, o_totalprice DOUBLE)")
        connection.executemany("INSERT INTO orders VALUES (?,?,?,?)", rows)
        expected = oracle.execute_query(connection, complex_workloads.spark_sql("cx_window"))
    assert [row["orderkey"] for row in actual if row["custkey"] == 1] == [3, 1, 2]
    assert [row["orderkey"] for row in expected if row["custkey"] == 1] == [1, 2, 4]
    assert oracle.normalized(actual) != expected


@pytest.fixture()
def tiny_data(tmp_path):
    """Minimal typed tables for all twelve unchanged SQL workloads; no extensions."""
    schemas = {
        "lineitem": "l_orderkey BIGINT,l_partkey BIGINT,l_suppkey BIGINT,l_linenumber BIGINT,"
            "l_quantity DECIMAL(15,2),l_extendedprice DECIMAL(15,2),l_discount DECIMAL(15,2),"
            "l_tax DECIMAL(15,2),l_returnflag VARCHAR,l_linestatus VARCHAR,l_shipdate DATE,"
            "l_commitdate DATE,l_receiptdate DATE,l_shipmode VARCHAR",
        "orders": "o_orderkey BIGINT,o_custkey BIGINT,o_orderstatus VARCHAR,o_totalprice DECIMAL(15,2),"
            "o_orderdate DATE,o_orderpriority VARCHAR,o_shippriority BIGINT,o_comment VARCHAR",
        "customer": "c_custkey BIGINT,c_name VARCHAR,c_address VARCHAR,c_nationkey BIGINT,"
            "c_phone VARCHAR,c_acctbal DECIMAL(15,2),c_mktsegment VARCHAR,c_comment VARCHAR",
        "part": "p_partkey BIGINT,p_name VARCHAR,p_type VARCHAR,p_retailprice DECIMAL(15,2)",
        "supplier": "s_suppkey BIGINT,s_nationkey BIGINT,s_acctbal DECIMAL(15,2)",
        "partsupp": "ps_partkey BIGINT,ps_suppkey BIGINT,ps_supplycost DECIMAL(15,2)",
        "nation": "n_nationkey BIGINT,n_name VARCHAR",
    }
    data = tmp_path / "data"
    data.mkdir()
    manifest = {"scale_factor": 0.001, "tables": {}}
    with duckdb.connect() as connection:
        for table, schema in schemas.items():
            connection.execute(f"CREATE TABLE {table} ({schema})")
        connection.execute("INSERT INTO lineitem VALUES "
            "(1,1,1,1,5,100,0.06,0.1,'R','F','1994-01-02','1994-01-03','1994-01-04','SHIP'),"
            "(2,1,1,1,10,200,0.05,0.1,'N','O','1995-09-02','1995-09-03','1995-09-04','AIR')")
        connection.execute("INSERT INTO orders VALUES "
            "(1,1,'F',100,'1993-10-02','1-URGENT',0,'ordinary'),"
            "(2,1,'O',200,'1995-01-02','2-HIGH',0,'ordinary')")
        connection.execute("INSERT INTO customer VALUES (1,'name','address',1,'phone',1,'BUILDING','ordinary')")
        connection.execute("INSERT INTO part VALUES (1,'green part','PROMO PART',1)")
        connection.execute("INSERT INTO supplier VALUES (1,1,1)")
        connection.execute("INSERT INTO partsupp VALUES (1,1,2)")
        connection.execute("INSERT INTO nation VALUES (1,'nation')")
        for table in schemas:
            path = data / table / "data.parquet"
            path.parent.mkdir()
            connection.execute(f"COPY {table} TO {oracle.sql_string(path)} (FORMAT PARQUET)")
            manifest["tables"][table] = {"files": [{"path": f"{table}/data.parquet",
                "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]}
    (data / "manifest.json").write_text(json.dumps(manifest))
    return data


@pytest.mark.parametrize("suite", ["baseline", "complex"])
def test_complete_oracle_identity_rows_and_no_overwrite(tiny_data, tmp_path, suite):
    output = tmp_path / f"{suite}-sf0.001.json"
    value = oracle.run_oracle(suite, tiny_data, output, threads=2, memory_limit="256MB",
                              temp_directory=tmp_path / "spill")
    module = oracle.suite_module(suite)
    assert value == json.loads(output.read_text())
    assert value["status"] == "PASS" and value["completed_at_utc"]
    assert value["suite"] == suite and value["suite_version"] == getattr(module, "SUITE_VERSION", "baseline-v1")
    assert value["manifest_sha256"] == hashlib.sha256((tiny_data / "manifest.json").read_bytes()).hexdigest()
    assert set(value["queries"]) == set(module.QUERY_IDS)
    for query, item in value["queries"].items():
        assert item["query_sha256"] == hashlib.sha256(module.spark_sql(query).encode()).hexdigest()
        assert item["row_count"] == len(item["rows"])
        assert "seconds" not in item
    checkpoint = json.loads(output.with_suffix(".partial.json").read_text())
    assert checkpoint["status"] == "CHECKPOINT"
    assert not output.with_suffix(".json.lock").exists()
    before = output.read_bytes()
    with pytest.raises(FileExistsError, match="overwrite"):
        oracle.run_oracle(suite, tiny_data, output)
    assert output.read_bytes() == before
    if suite == "baseline":
        assert value["queries"]["q06"]["rows"] == [{"revenue": 6.0}]
        assert value["queries"]["q14"]["rows"] == [{"promo_revenue": 100.0}]


def test_query_failure_keeps_completed_progress_without_final_pass(tiny_data, tmp_path, monkeypatch):
    execute = oracle.execute_query
    count = 0
    def fail_second(connection, sql):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("deliberate query failure")
        return execute(connection, sql)
    monkeypatch.setattr(oracle, "execute_query", fail_second)
    output = tmp_path / "failure.json"
    with pytest.raises(RuntimeError, match="deliberate"):
        oracle.run_oracle("baseline", tiny_data, output, threads=1, memory_limit="256MB")
    assert not output.exists()
    checkpoint = json.loads(output.with_suffix(".partial.json").read_text())
    assert checkpoint["status"] == "FAIL" and list(checkpoint["queries"]) == ["q01"]
    assert checkpoint["active_query"] == "q03" and checkpoint["error"]["type"] == "RuntimeError"
    with pytest.raises(FileExistsError):
        oracle.run_oracle("baseline", tiny_data, output)


def test_unlisted_parquet_file_and_changed_input_metadata_are_rejected(tiny_data, tmp_path, monkeypatch):
    table = tiny_data / "lineitem"
    extra = table / "unlisted.parquet"
    extra.write_bytes((table / "data.parquet").read_bytes())
    output = tmp_path / "extra.json"
    with pytest.raises(ValueError, match="inventory"):
        oracle.run_oracle("baseline", tiny_data, output)
    extra.unlink()
    execute = oracle.execute_query
    touched = False
    def mutate_metadata(connection, sql):
        nonlocal touched
        rows = execute(connection, sql)
        if not touched:
            import os
            path = table / "data.parquet"
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            touched = True
        return rows
    monkeypatch.setattr(oracle, "execute_query", mutate_metadata)
    with pytest.raises(ValueError, match="changed during"):
        oracle.run_oracle("baseline", tiny_data, tmp_path / "changed.json", threads=1, memory_limit="256MB")
    assert not (tmp_path / "changed.json").exists()


def test_atomic_publication_rejects_nonfinite_values_and_existing_symlinks(tmp_path):
    path = tmp_path / "artifact.json"
    with pytest.raises(ValueError):
        oracle.atomic_json(path, {"invalid": float("nan")})
    assert not path.exists()
    path.symlink_to(tmp_path / "missing-target")
    with pytest.raises(FileExistsError):
        oracle.atomic_json(path, {"status": "PASS"})
    assert path.is_symlink() and not path.exists()
    assert oracle.normalized({"date": date(2026, 10, 26), "decimal": Decimal("1.5")}) == {
        "date": "2026-10-26", "decimal": 1.5}
