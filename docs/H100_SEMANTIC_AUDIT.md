# H100 semantic diagnostic audit

These diagnostics are separate from the twelve unchanged performance workloads.
They test behavior on small, manually specified inputs, including values outside
the generated study dataset's non-null domain. Passing a diagnostic is a
correctness result, not a performance measurement or proof of general SQL
equivalence.

## Diagnostic contract

`research_fixtures.py` defines typed `TABLE_SCHEMAS` and `TABLE_ROWS`, five SQL
queries shared by DuckDB and Spark, equivalent native `polars_query()` plans,
and hand-specified `EXPECTED_ROWS`. `table_data()` returns named typed records;
`polars_tables()` returns lazy frames. No original workload is replaced.

| Case | Deliberate edge conditions |
| --- | --- |
| `fx_inner_duplicates_nulls` | Duplicate keys multiply join rows; null keys do not match under ordinary equality. |
| `fx_outer_counts` | Unmatched left rows survive; `COUNT(*)` includes them while `COUNT(fact_id)` excludes null values, including a matched null fact ID. |
| `fx_semi_anti` | Duplicate and null keys on both right inputs; semi produces IDs 1, 2, 3; the nonempty anti branch excludes ID 2, leaving 1, 3. |
| `fx_rank_ties` | Equal scores, deterministic row-number tie breaker, null scores, a null partition key, SQL rank gaps, and lag that respects nulls. |
| `fx_regex_distinct` | A newline between `special` and `requests`, reversed words, null comments, duplicate distinct values and null distinct values. |

Strict GPU rejection is an explicit coverage outcome. CPU fallback must not be
counted as successful GPU execution. Saved final plans and runtime diagnostics
are needed before making GPU operator claims.

## Observed limitation in the preserved window port

On 2026-10-26, local Polars 1.44.2 (Python 3.12; DuckDB 1.5.6 as independent
SQL reference) exposed a null-ordering difference in the original `cx_window`
native plan. The expression counts rows over an ordering of negated price,
date, and order key and requests `nulls_last=True`. For one customer with order
keys 1, 2, 3, 4 and prices 100, 100, null, 50 on the same date, it assigns order
key 3 the first row number. Its top-three result is therefore order keys
**3, 1, 2**. The SQL `ORDER BY price DESC NULLS LAST, date, orderkey` top three
are **1, 2, 4**.

The regression test calls the actual preserved `complex_workloads.polars_query`
and SQL entry points and records this mismatch on pinned Polars 1.44.2. The
separate `fx_rank_ties` native plan supplies an explicit null-indicator ordering
key before negated price, giving the manually expected SQL ordering. The
original performance query remains unchanged to preserve the pilot and its
declared workload identity.

The generated TPC-H-derived `orders.o_totalprice` input has no null values. This
diagnostic therefore does not demonstrate a mismatch on the benchmark input.
Full-scale independent oracle validation remains necessary, and claims about
the original window port must be bounded to the tested non-null input domain.
The local observation does not establish behavior for other Polars versions or
the H100 Python environment until those diagnostics execute there.

## Full-scale oracle

Run `research_oracle.py` independently of all timed sessions, once for each
suite and scale. It executes the unchanged SQL entry points using DuckDB,
casts the study's monetary/quantity columns to DOUBLE, and saves every ordered
result row. A final `PASS` file is published atomically only after all queries
finish and input manifest/inventory metadata are stable. Its separate
`.partial.json` progress checkpoint is never accepted as a completed oracle.

The oracle records manifest and SQL hashes, source hashes, software versions,
effective resource settings, and exact declared input-file references. It
checks file inventory and sizes, then checks metadata stability; it does not
substitute for the campaign's separate full input-byte SHA256 verification.

Example on the H100, outside performance execution:

```sh
python research_oracle.py --suite complex --data /path/to/data/sf300 \
  --output /path/to/oracles/complex-sf300.json --threads 26 \
  --memory-limit 128GB --temp-directory /path/to/large-local-disk/oracle-spill
```

Output and checkpoint paths are protected from overwrite. A failed run retains
completed query rows and the error in its checkpoint; use a new explicitly
named output for a new attempt so failed evidence remains reviewable.
