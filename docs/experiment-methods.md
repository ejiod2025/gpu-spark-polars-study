# Polars, CPU Spark, and GPU Spark experiment

This experiment compares CPU Polars with Apache Spark on the CPU and Spark with
NVIDIA's cuDF plugin on the same Lambda VM. The machine has 30 AMD EPYC 7J13
vCPUs, 216 GiB host RAM, and one NVIDIA A100-SXM4 with 40 GB GPU memory. Each
engine receives the same Parquet files and runs sequentially with 30 CPU threads.
The GPU run also uses the A100.

The experiment uses six selected TPC-H-derived queries and scale factors 1, 10,
and 30. Scale factor is a data-generation parameter; it is not the compressed
Parquet size. Read the generated manifests for actual row counts and file bytes.
These results are not certified TPC-H results and should not be compared with
published compliant TPC-H scores.

## Software and setup

Run the following from this directory on the Linux GPU host after copying the
Python source files there. The existing NVIDIA driver must work with the CUDA 12
plugin build. The experiment host has driver 580.105.08; `nvidia-smi` reports the
maximum supported CUDA driver API version, not the plugin's build version.

| Component | Version |
| --- | --- |
| Python | 3.10 |
| Java | OpenJDK 17 |
| PySpark / Spark | 3.5.9, Scala 2.12 distribution |
| Polars | 2.0.0 |
| DuckDB data generator | 1.5.6 |
| PyArrow | 22.0.0 |
| psutil | 7.1.2 |
| NVIDIA cuDF / RAPIDS Spark plugin | 26.08.1, Scala 2.12, CUDA 12 JAR |

DuckDB 1.5.6 is pinned because it supports this Python 3.10 environment. The
benchmark records the installed package list, Java version, driver information,
hardware inventory, and source hashes for each run.

```bash
sudo apt-get update
sudo apt-get install -y openjdk-17-jre-headless python3.10-venv curl
python3.10 -m venv .venv
. .venv/bin/activate
python -m pip install pyspark==3.5.9 polars==2.0.0 duckdb==1.5.6 pyarrow==22.0.0 psutil==7.1.2
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
mkdir -p jars
curl -fL --retry 3 \
  https://repo.maven.apache.org/maven2/com/nvidia/rapids-4-spark_2.12/26.08.1/rapids-4-spark_2.12-26.08.1.jar \
  -o jars/rapids-4-spark_2.12-26.08.1.jar
java -version
nvidia-smi
```

The CUDA toolkit is optional for this plugin; do not replace a working NVIDIA
driver merely to reproduce the Python environment. See NVIDIA's
[local-mode installation instructions](https://docs.nvidia.com/cudf-spark/latest/getting-started/on-premise.html)
and [release downloads and compatibility](https://nvidia.github.io/cudf-spark/docs/download.html).

## Generate and run

```bash
python generate_data.py --root data --scales 1 10 30 --threads 30 --memory-limit 64GB
python run_experiment.py \
  --data-root data \
  --output results/run-01 \
  --scales 1 10 30 \
  --cpus 30 \
  --driver-memory 96g \
  --runs 3 \
  --jar jars/rapids-4-spark_2.12-26.08.1.jar
python analyze_results.py --results results/run-01 --output reports/run-01
```

`run_experiment.py --output` must name a new directory. It refuses to overwrite
an earlier run; choose a new name for each rerun. Keep the same data directory to
reuse the exact files. When a scale already exists, `generate_data.py` validates
its manifest and each Parquet file's SHA-256 checksum before reusing it. Generation
requires network access for DuckDB's `tpch` extension on its first installation.

For a short functional check, use `--scales 0.01` in generation and execution and
a separate output directory. Such a check does not characterize A100 throughput.
The runner also accepts `--engines polars spark_cpu spark_gpu` and `--queries q01
q03 q06 q10 q12 q14` to select subsets. A full comparison needs all three engines
and all six queries at every scale.

## Workloads and measurement

| Query | Work tested |
| --- | --- |
| Q1 | Broad scan, arithmetic, grouped sums and averages |
| Q3 | Selective three-table join, aggregation, top 10 |
| Q6 | Filtered scan and scalar revenue sum |
| Q10 | Four-table join, customer aggregation, top 20 |
| Q12 | Join and conditional grouped counts |
| Q14 | Part join and conditional revenue ratio |

`workloads.py` contains Spark SQL and equivalent native Polars lazy expressions.
Both readers normalize the listed decimal quantity and monetary columns to
Float64 / DOUBLE. The GPU enables variable floating-point aggregation, so
validation allows the small numerical differences caused by parallel reduction.
This choice does not measure strict decimal financial arithmetic. Extra sort keys
make top-N ties deterministic. The query constants follow
[DuckDB's TPC-H query sources](https://github.com/duckdb/duckdb/tree/main/extension/tpch/dbgen/queries).

`generate_data.py` generates all eight tables with DuckDB's `tpch` extension and
exports Snappy Parquet, targeting 128 MB files and 122,880-row row groups. Each
scale has a manifest with schemas, row counts, byte sizes, and checksums. See the
[DuckDB TPC-H extension documentation](https://duckdb.org/docs/stable/core_extensions/tpch).

`run_experiment.py` randomizes engine order deterministically at each scale. It
reads all dataset files before each engine starts to preheat the operating
system's filesystem cache. Each engine has a fresh process/session, and no
application DataFrame cache is used. These are warm-filesystem measurements;
they do not test remote object-store or cold-disk performance.

`worker.py` runs one untimed warmup of every query, followed by three measured
trials with a deterministically shuffled query order in each trial. It builds and
collects a fresh query for each trial. The timer includes query construction,
optimization, Parquet reads, execution, and result collection. It excludes session
and table setup, result JSON serialization, and plan serialization. Launch-to-engine-ready time, session/table
setup, and whole-process elapsed time are recorded separately. Startup therefore
must not be silently included in the per-query comparisons.

Polars explicitly uses its CPU streaming engine with `POLARS_MAX_THREADS=30`;
see [Polars collection engine options](https://docs.pola.rs/api/python/stable/reference/lazyframe/api/polars.LazyFrame.collect.html).
Both Spark modes use `local[30]`, a 96 GiB driver heap, 120 shuffle partitions,
128 MiB maximum file partitions, adaptive query execution, and UTC. They execute
the same SQL. The GPU mode adds the plugin JAR and these settings:

```text
spark.plugins=com.nvidia.spark.SQLPlugin
spark.rapids.sql.enabled=true
spark.rapids.sql.explain=NOT_ON_GPU
spark.rapids.sql.concurrentGpuTasks=2
spark.rapids.memory.pinnedPool.size=2G
spark.rapids.sql.variableFloatAgg.enabled=true
```

These are the recorded initial settings, not the result of a tuning competition.
The plugin may retain some CPU operators. Inspect the saved final executed plans
and fallback logs to assess coverage. The worker requires GPU scan and aggregation
operators in each GPU query plan and fails if either is absent. GPU telemetry is
sampled every 200 ms, and host process memory every 100 ms; sampled peaks can miss
short spikes and are not exact allocation totals. NVIDIA's
[tuning guide](https://docs.nvidia.com/cudf-spark/latest/tuning-guide.html)
explains the relevant tradeoffs.

## Evidence and interpretation

The results directory retains `environment.json`, `schedule.json`, `gpu.csv`,
per-engine logs, Spark event logs, and per-scale/per-engine `results.json` files.
Each query record includes the materialized output, timing, sampled peak RSS, and
a link to its saved physical plan. The analyzer checks outputs across engines
and trials before presenting per-query timing comparisons. Retain raw evidence
alongside the generated report.

Interpret speedups per query and scale. Three repetitions provide descriptive
timings and variability, not strong statistical claims. Any aggregate depends on
these six selected workloads. This is one VM with local Spark, not a distributed
cluster comparison or a full evaluation of production Spark workloads. The CPU
engines use the GPU VM's CPU and RAM; their performance does not establish the
price or value of a separate CPU-only instance. No cost comparison is made without
a specified CPU-only alternative and its price.

To render charts after validation, install Matplotlib in a separate plotting environment and run:

```bash
python plot_results.py --report reports/run-01
```

The plotter writes `comparison.png` and `comparison.svg` and refuses unverified evidence.

## Larger-scale crossover search

The larger search retains the same six queries, engine versions, 30 CPU threads,
96 GiB Spark driver heap, three measured trials, and Spark settings. SF100 has
about 600 million line items; SF300 has about 1.8 billion. These are scale-factor
labels, not compressed Parquet gigabytes.

`generate_large_data.py` uses independent DuckDB 1.5.6 `dbgen(children, step)`
processes to generate bounded chunks directly into shared Parquet. It avoids
retaining a complete generation database. All eight tables, including region and
nation, are partitioned by the generator. Exact table-multiset and primary-key
equivalence to whole-dataset generation, plus six-query numeric parity, are
checked by `--self-test`. Proofs are retained in `artifacts/`. Physical row/file
ordering can differ from the original export; each engine at a given scale uses
the identical checksummed files.

Generate and benchmark one scale at a time so generation cannot contend with
timed queries:

```bash
python generate_large_data.py --self-test --proof-output artifacts/generation-proof.json
python generate_large_data.py --root data-large --scales 100 --workers 12 --chunk-scale 5
python run_experiment.py --data-root data-large --output results/large-sf100 \
  --scales 100 --runs 3 --cpus 30 --driver-memory 96g \
  --jar jars/rapids-4-spark_2.12-26.08.1.jar \
  --keep-going --engine-timeout-seconds 3600
python generate_large_data.py --root data-large --scales 300 --workers 12 --chunk-scale 5
python run_experiment.py --data-root data-large --output results/large-sf300 \
  --scales 300 --runs 3 --cpus 30 --driver-memory 96g \
  --jar jars/rapids-4-spark_2.12-26.08.1.jar \
  --keep-going --engine-timeout-seconds 3600
python analyze_crossover.py --results results/run-01 results/large-sf100 results/large-sf300 \
  --output reports/crossover
```

The runner records active-query diagnostics, available host RAM, cache, swap and
free disk, and continues to other engines after a failure when `--keep-going` is
set. Timeouts terminate the complete engine process group. Failed or incomplete
scales are excluded from verified latency comparisons; an out-of-memory error
does not count as a crossover. The crossover analyzer combines validated scales,
reports observed changes of winner, and makes no extrapolated threshold claim.
For duplicate scales it selects the last supplied results root.

Check free disk and memory before adding a larger scale. SF1000 can require about
350 GB of stored Parquet with this encoding, leaving limited room for older
datasets and execution spill. Its input exceeds host RAM, so the original
filesystem-preheat policy cannot guarantee the same cache regime.

The completed extended experiment is in `results/large-sf100-20261025` and
`results/large-sf300-20261025`; its combined report and chart are in
`reports/crossover-20261025`. SF100 used 12 generation workers and SF300 used 16,
both with chunk scale 5 and an 8 GB limit per generation worker. Timed engine
settings remain the same.

```bash
python plot_crossover.py --report reports/crossover-20261025
python inspect_spark_metrics.py \
  --results results/production-20261025 results/large-sf100-20261025 results/large-sf300-20261025 \
  --output reports/crossover-20261025/spark_metrics.json
```

The optional metrics inspector maps SQL executions and stages to each saved query
and separates standard Spark spill counters from RAPIDS task memory metrics.
Missing GPU spill counters are not interpreted as zero device-to-host spilling.
Exact source snapshots, verified against each run's recorded hashes, are in
`artifacts/source-snapshots/`. Checksummed data remain on the Lambda host under
`<BENCHMARK_ROOT>/data` and `data-large`.

## Separate complex-query experiment

The complex-v1 suite is separate from the original six-query score. It adds:

| Query ID | Operations |
| --- | --- |
| `cx_q09` | Six-table profit join, including a composite part/supplier key |
| `cx_q13` | Left outer join and two levels of aggregation, preserving zero-order customers |
| `cx_semi_anti` | Nested `EXISTS` and `NOT EXISTS`, implemented with semi/anti joins |
| `cx_window` | `LAG` and `ROW_NUMBER` over two customer window orderings, then top three |
| `cx_distinct` | Four-table join and three exact distinct counts |
| `cx_self_join` | Line-item self-join on order key with a line-number inequality |

SQL and native Polars definitions are in `complex_joins.py` and
`complex_analytics.py`, registered by `complex_workloads.py`. Both ordered windows
use native partitioned Polars expressions; no application-side collection or
precomputed results are used. The final output of each query is small, but its
joins, windows, and distinct operations must execute before collection.

```bash
python complex_oracle.py --data data/sf1 --output artifacts/complex-oracle-sf1.json
python run_experiment.py --suite complex --data-root data \
  --output results/complex-smoke-sf1 --scales 1 --runs 1 --cpus 30 --driver-memory 96g \
  --jar jars/rapids-4-spark_2.12-26.08.1.jar --keep-going --engine-timeout-seconds 1200
python analyze_complex.py --results results/complex-smoke-sf1 \
  --output reports/complex-smoke-sf1 --oracle artifacts/complex-oracle-sf1.json
python run_experiment.py --suite complex --data-root data-large \
  --output results/complex-formal --scales 30 100 300 --runs 3 --cpus 30 --driver-memory 96g \
  --jar jars/rapids-4-spark_2.12-26.08.1.jar --keep-going --engine-timeout-seconds 3600
python analyze_complex.py --results results/complex-formal --output reports/complex-formal
python plot_complex.py --report reports/complex-formal
```

For the formal command, `data-large/sf30` must contain the original SF30 files or
be a symlink to `../data/sf30`. Generation must finish before any timed workload.
The independent DuckDB oracle runs only on the specified functional-check scale;
it is not a fourth performance competitor and does not overlap timed engines.
Small-scale native/SQL checks also cover nulls, duplicate existence matches,
composite keys, date boundaries, and window tie ordering.

Complex runs retain the original measurement and hardware settings. Unlike the
original aggregate-focused suite, they do not require a GPU aggregate in every
plan: a window query need not aggregate, and CPU fallback is an observed product
behavior. The analyzer inspects final plans and labels GPU operators, hybrid
plans, or CPU fallback without inferring runtime fractions from operator counts.
Failed cases are disclosed and excluded; the six-query summary is available only
when all six cases have complete, correct results for all three engines.
