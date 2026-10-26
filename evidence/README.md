# Portable Spark / Polars reproducibility evidence (draft)

**Publication status: draft_not_cleared.** This technical evidence package has not
been cleared for external publication or redistribution. Sanitization and valid
checksums do not establish legal clearance. Read `NOTICES.md` and
`publication/NOTICE_RESEARCH.md` before using or distributing it.

This package contains benchmark Python sources, exact verified source snapshots,
pinned Python requirements, dataset manifests, all selected recorded trial timings
and materialized result rows, and normalized executed plans. It contains no
Parquet data, dependency JARs, raw machine inventory, event logs, or SSH material.
No blanket source license is asserted here; dependency and query-source rights
must be considered separately before redistribution.

## Included runs

- `production-20261025`: 9 engine sessions; completed sessions.
- `large-sf100-20261025`: 3 engine sessions; completed sessions.
- `large-sf300-20261025`: 3 engine sessions; completed sessions.
- `complex-smoke-sf1-20261025`: 3 engine sessions; completed sessions.
- `complex-cleanup-smoke-sf1-20261025`: 3 engine sessions; completed sessions.
- `complex-formal-20261025`: 8 engine sessions; incident support only.
- `complex-formal-v2-20261025`: 9 engine sessions; completed sessions.

This export is a snapshot of locally selected evidence. `BUNDLE.json` records
each run's declared, selected and pending scale directories. A
`progress_selected_scope` run can contain completed selected sessions while
larger declared scales remain pending; it is not a globally completed run.
Its summary rows are explicitly labeled `completed_selected_sessions_progress_run`.

`DOCUMENT_CONTEXT.json` identifies any included draft article/plan notes and
curated oracle, cleanup-proof or post-run-integrity artifacts. Draft narratives
retain repository-context links whose targets may not be bundled; all links are
not claimed to resolve here. Their text is a dated draft snapshot, not a release
notice or a claim of completed evidence. Proof numeric values and SHA-256 values
are retained; sensitive directory identities are normalized separately.

`results/` holds curated session JSON and relative plan references.
`INPUT_MANIFESTS/` holds the corresponding dataset metadata and file checksums.
`source-snapshots/` preserves source bytes verified against each run's original
environment hash inventory. Top-level Python files are the current reproduction
tools and can differ from those snapshots. `TRIALS.csv` retains warmups and
measurements, with an explicit evidence scope and cleanup policy. `SUMMARY.csv`
groups measured trials by run, suite, cleanup policy, scale, engine and query.
Runs with a recorded failure or incomplete evidence remain in their own raw
namespaces and `TRIALS.csv`, labeled `incident_support_only`, and are excluded
entirely from `SUMMARY.csv`. `INCIDENTS.json` and each affected run's `INCIDENT.json`
preserve those observations as nonperformance evidence. No failed query is
assigned an inferred performance bound. Baseline and complex suites remain
separate, as do legacy `none` and `verified_gc` resource protocols.

## Verify this download

From this package's root directory on Linux, run `sha256sum -c SHA256SUMS`.
The adjacent ZIP checksum and download JSON describe the archive itself.
`PROVENANCE.json` maps every selected private original file's SHA-256 to its
public file SHA-256 and lists normalization operations. Original files remain
private. Public JSON/plans are intentionally transformed copies and do not claim
byte identity with the originals. Source files marked exact are byte-identical.
Safe input manifests retain their original bytes. If a manifest needs identifier
normalization, session `manifest_sha256` references its public bytes and
`manifest_original_sha256` preserves the original input-manifest identity.

## Reproduce from this package's root directory

Use a Linux NVIDIA GPU host with a compatible driver and Java 17. The recorded
allocation is one A100 40 GB, 30 vCPUs and about 216 GiB RAM. Inspect each session's
actual engine version and compute configuration in `results/`; those observations
take precedence over current dependency defaults.

Chart generation has a separate optional dependency set. Install it with
`python -m pip install -r requirements-reporting.txt` before running plot scripts;
it pins matplotlib 3.10.8 and is separate from the timed-engine requirements.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p jars
curl -fL https://repo.maven.apache.org/maven2/com/nvidia/rapids-4-spark_2.12/26.08.1/rapids-4-spark_2.12-26.08.1.jar -o jars/rapids-4-spark_2.12-26.08.1.jar
sha256sum -c DEPENDENCY_SHA256.txt
python generate_data.py --root data --scales 1 10 30
python generate_large_data.py --root data --scales 100 300 --workers 12
python run_experiment.py --data-root data --output rerun/baseline --scales 30 100 300 --runs 3 --cpus 30 --driver-memory 96g --jar jars/rapids-4-spark_2.12-26.08.1.jar --suite baseline
python run_experiment.py --data-root data --output rerun/complex --scales 30 100 300 --runs 3 --cpus 30 --driver-memory 96g --jar jars/rapids-4-spark_2.12-26.08.1.jar --suite complex --keep-going --query-cleanup verified_gc --cleanup-timeout-seconds 60
python analyze_results.py --results rerun/baseline --output rerun/baseline-report
python analyze_complex.py --results rerun/complex --output rerun/complex-report
```

For historical execution, use the matching `source-snapshots/<run>/` sources;
older runners predate `--suite` and failure-handling flags. Their `--help` documents
the supported arguments. New generated files need not have identical compressed
Parquet bytes or layout; compare schemas, row counts and semantic fingerprints.
Saved manifests identify the actual input files shared by all three engines.

For functional checking, `python complex_oracle.py --data data/sf1 --output
rerun/complex-oracle-sf1.json` runs all six SQL definitions in DuckDB outside timed
competitor sessions. Pass its output to `analyze_complex.py --oracle` alongside
the matching SF1 results only. Run labels are preserved under `results/<run>/`;
baseline and complex roots containing the same scale remain separate evidence.

Under `verified_gc`, a barrier follows each returned action outside the query
clock. The barrier's elapsed time remains within process wall time and is saved
separately. Cleanup records retain before/after shuffle file and byte counts,
root free bytes, active job IDs, GC requests, outcome and timestamps. Owned
block-manager directory identities are replaced by portable tokens. A recorded
passed barrier means no remaining observed owned shuffle files or active jobs;
it is not a claim that all memory allocations were released. Legacy `none` runs
have no explicit verified barrier measurement. `prove_query_cleanup.py`, when
included, is a separate small Spark lifecycle proof, not a timed competitor.

`validation/verify_input_integrity.py`, when included, is exact read-only source
for the post-run input check. It must run only after all nine complex engine
sessions finish and the orchestrator stops. It expects the recorded five-scale
layout: `data/sf1`, `data/sf10`, `data/sf30`, `data-large/sf100`, `data-large/sf300`,
and a `data-large/sf30` alias to `data/sf30`. From the package root, use Python
stdin so `generate_data` resolves from that directory. Set
`COMPLETED_BENCHMARK_PID` to the actual finished orchestrator's PID, compute
`INTEGRITY_SCRIPT_SHA256` from the included script bytes, and run:

```sh
python - --run rerun/complex --orchestrator-pid "$COMPLETED_BENCHMARK_PID" --source-sha256 "$INTEGRITY_SCRIPT_SHA256" --output artifacts/complex/postrun-input-integrity.json < validation/verify_input_integrity.py
```

The helper refuses a live benchmark orchestrator and an existing proof output.
Its inclusion does not mean the check has run; only an included successful
`postrun-input-integrity.json` records that outcome.

## Scope and limitations

The original six selected TPC-H-derived queries and the six complex workloads
are separate experiments. Their tables and per-query observations must remain
separate. This is neither the complete nor a certified TPC-H benchmark. The
queries use the same Float64/DOUBLE projections for decimal quantities/money.
Spark SQL is compared with equivalent native Polars lazy expressions. Outputs
are materialized. Per-query timers exclude engine/table setup, warmups and plan
serialization; startup/setup fields are retained separately. Filesystem preheating
is attempted before each engine; cache residency is not guaranteed.

Three measurements within one fresh engine session are not three independently
restarted experiments. A sum of per-query medians is a workload score, not a
measured complete-suite latency. Failed/missing complex queries must be disclosed;
do not treat incomplete engine totals as comparable complete-suite scores. GPU
operators in plans establish operator selection, not a GPU runtime percentage.
This one-host fixed configuration does not establish product-wide performance,
distributed scaling, equal memory budgets, cost efficiency or statistical
significance. Dataset size is not physical bytes read by every query.

Data generation uses DuckDB's [TPC-H extension](https://duckdb.org/docs/1.5/core_extensions/tpch).
Query definitions derive from the [TPC-H specification](https://www.tpc.org/tpch/).
The GPU dependency is NVIDIA's [cuDF/RAPIDS Spark plugin](https://nvidia.github.io/cudf-spark/docs/download.html).
