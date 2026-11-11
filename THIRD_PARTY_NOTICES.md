# Third-party notices

## TPC (Transaction Processing Performance Council)

Parts of the workload (query definitions adapted from TPC-H, via DuckDB 1.5.6's TPC-H extension sources)
derive from TPC material. They are not relicensed by this repository. Terms:

- TPC End User License Agreement 2.2.0: <https://www.tpc.org/TPC_Documents_Current_Versions/txt/EULA_v2.2.0.txt>
- TPC Policies (current version): <https://www.tpc.org/TPC_Documents_Current_Versions/pdf/Policies_v6.20.pdf>
- TPC-H specification 3.0.1 (© 1993–2022 Transaction Processing Performance Council):
  <https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC-H_v3.0.1.pdf>
- Tagged TPC-origin software license in DuckDB: <https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/LICENSE>

This repository does not redistribute TPC software (dbgen); data are generated at run time with DuckDB's
`tpch` extension. Where TPC software terms apply to redistributed portions, the EULA requires the legend:

**THE TPC SOFTWARE IS AVAILABLE WITHOUT CHARGE FROM TPC.**

Results in this repository are from a workload derived from the TPC-H Benchmark and are not comparable to
published TPC-H Benchmark results, as they do not comply with the TPC-H Benchmark Specification.

## DuckDB

DuckDB is MIT-licensed: Copyright 2018-2025 Stichting DuckDB Foundation.
<https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/LICENSE>

## Other dependencies

Polars, cudf-polars / RAPIDS, Apache Spark, the RAPIDS Accelerator for Apache Spark, PyArrow and other
dependencies are used under their own licenses and are not redistributed here (pins are in
`requirements*.txt` and `evidence/requirements.lock.txt`).
