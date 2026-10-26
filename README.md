# Spark and Polars CPU/GPU study

Private development and reproducibility repository for the [public study](https://a100-spark-polars-study-oct2026.ejiod.chatgpt.site). It preserves the author-created benchmark and publication sources, historical source snapshots, final reports, and curated evidence. Repository visibility and the public website are separate: this repository is intended to remain private.

## H100 development

The completed exploratory [H100 study](reports/H100_STUDY.md) includes five baseline scales,
three complex scales, and separate CPU Polars 2.0 controls. All 936 successful
formal outputs agree, and all 1,776 input files passed post-run re-hashing.
At SF300, Polars GPU's baseline score is 12.774 s versus 31.540 s for CPU Polars
2.0 (2.47× faster). GPU Spark wins selected queries but has no aggregate win
against that CPU control; strict Polars GPU mode rejects two original complex cases.

A prospective [confirmatory protocol](docs/H100_CONFIRMATORY_PROTOCOL.md) adds
ten paired blocks (400 fresh engine sessions), independent SQL oracles, balanced
execution order, and a common Python 3.11 build for both CPU Polars versions.
The pilot observations are kept separate from those new measurements. See
[the research runbook](docs/H100_RESEARCH_RUNBOOK.md) for execution and validation.
The [campaign status](reports/H100_RESEARCH_STATUS.md) records the latest saved
progress snapshot; confirmatory conclusions remain pending full collection.

Branch `H100_main_dev` extends the experiment to four engines: Polars CPU, Polars GPU with cuDF, CPU Spark, and GPU Spark. The H100 host has one 80 GB H100, 26 Intel Xeon vCPUs, and about 221 GiB RAM. Both Polars engines use 1.44.2 with `cudf-polars-cu12==26.10.2` on Python 3.11; the A100 record below used Polars 2.0.0 on Python 3.10. Within-H100 comparisons are the primary result. Hardware, CPU allocation, and software differences prevent interpreting A100/H100 ratios as an isolated GPU upgrade effect.

The original query expressions and saved A100 evidence remain available. Polars GPU disables CPU fallback and verifies this with a successful GPU query, resolved engine settings, and an unsupported Python-UDF canary before timing. Streaming may use a single GPU partition with a recorded warning; this is distinct from CPU fallback. Unsupported queries and other failures are retained and cannot create a complete suite score.

See [the H100 plan](docs/H100_PLAN.md), [compatibility notes](docs/POLARS_GPU_COMPAT.md), `setup_h100.sh`, `preflight.py`, and `verify_generated_inputs.py`. Revalidate historical evidence with the commands below. `SOURCE_MANIFEST.json` describes the original repository import; new runs record their own source hashes.

The four-engine invocation explicitly adds `polars_gpu`:

```bash
python run_experiment.py \
  --data-root data --output rerun/h100-baseline-new \
  --scales 1 10 30 100 300 --runs 3 --cpus 26 --driver-memory 96g \
  --engines polars polars_gpu spark_cpu spark_gpu \
  --jar jars/rapids-4-spark_2.12-26.08.1.jar --keep-going
```

For the complex suite, add `--suite complex --query-cleanup verified_gc` and use scales `30 100 300` with a separate output directory. Validate data and run the SF1 preflight before formal timings. New H100 development does not change the published A100 website.

## Recorded A100 experiment

The study compares CPU Polars, CPU Apache Spark, and Spark with NVIDIA's [cuDF plugin](https://docs.nvidia.com/cudf-spark/latest/overview.html) on the same generated Parquet files. The host had one NVIDIA A100-SXM4-40GB, 30 AMD EPYC 7J13 vCPUs, and approximately 216 GiB RAM. Polars used CPU streaming with a pool of 30; Spark used `local[30]`, a 96 GiB driver heap, 120 shuffle partitions, and adaptive query execution.

## Results and scope

The original suite contains six selected TPC-H-derived queries: Q1, Q3, Q6, Q10, Q12 and Q14. Four already contain joins. The separate complex suite contains six cases covering a six-table join, outer join with nested aggregation, correlated `EXISTS`/`NOT EXISTS`, two window orderings, exact distinct counts, and a fact self-join. These are selected, unaudited derived/custom workloads, not complete or certified TPC-H results.

Scores below are **sums of six per-query medians**, in seconds. Each median uses three measured trials within one engine session, following one recorded warmup. Scores are not once-through suite runtimes, startup latency, or throughput through every stored dataset byte.

| Original suite scale | Line-item rows | Polars CPU | CPU Spark | Spark + GPU plugin |
|---|---:|---:|---:|---:|
| SF1 | 6,001,215 | 0.202 | 2.665 | 3.721 |
| SF10 | 59,986,052 | 1.106 | 9.212 | 6.202 |
| SF30 | 179,998,372 | 3.006 | 19.025 | 10.557 |
| SF100 | 600,037,902 | 9.258 | 47.736 | 23.432 |
| SF300 | 1,799,989,091 | 28.187 | 125.802 | 65.037 |

| Complex v2 scale | Polars CPU | CPU Spark | Spark + GPU plugin |
|---|---:|---:|---:|
| SF30 | 5.779 | 49.231 | 18.163 |
| SF100 | 21.445 | 138.310 | 56.193 |
| SF300 | 70.664 | 325.225 | 192.652 |

Polars had the lower aggregate score at every observed scale in both suites. GPU Spark won selected cases: baseline Q1 at SF30/SF100/SF300, complex windows at all three scales, and complex Q13 at SF300. At SF300, the Q1, window and Q13 speedups over Polars were 1.34×, 2.64× and 1.26× respectively. No aggregate GPU-over-Polars crossover was observed, and these fixed single-host results do not establish a universal winner, distributed scaling, equal memory budgets, cost efficiency or statistical significance.

The original suite retains its original lifecycle. Formal complex v2 uses **`verified_gc` after every returned query action**, outside the query timer. It requests Python GC for Polars and Python/JVM GC for Spark, then observes zero remaining owned Spark shuffle files/bytes and no active jobs. GC can change the heap state seen by the next query. Cleanup costs are reported separately and remain within process wall time; do not mix legacy/v2 curves or add four-action cleanup totals to a sum of query medians. The earlier complex v1 disk-space failure is retained as incident evidence and excluded from performance summaries.

Other boundaries matter: filesystem preheating is attempted, residency is not guaranteed, and application DataFrame caching is disabled. Quantity/money columns use Float64/DOUBLE. Polars' conversion to Python dictionaries is outside its timer; Spark's collection into Python `Row` objects is inside, with `asDict()` outside. Final GPU plan nodes show operator selection rather than runtime fractions. The correlated case's SQL does not establish execution of a nonempty GPU anti join; consult the saved final plans and coverage notes.

## Repository map

- Root Python modules: generators, query definitions, runner/worker, analyzers, plotting, oracle and cleanup helpers.
- [`reports/crossover-20261025/`](reports/crossover-20261025/): five-scale original report, CSV, correctness evidence and figures.
- [`reports/complex-formal-v2-20261025/`](reports/complex-formal-v2-20261025/): three-scale complex report, CSV, correctness/cleanup evidence and per-scale figures.
- [`evidence/`](evidence/): expanded curated final bundle, including seven run namespaces under `results/`, input manifests, result rows, final plans, exact source snapshots, source provenance and checksums.
- [`artifacts/source-snapshots/`](artifacts/source-snapshots/): exact historical benchmark source snapshots. Current root tools can differ from the code that produced older runs.
- [`publication/`](publication/): exporter and publication tools, local preview source/templates, and a static copy of the public hosted study. Nested Git metadata and hosting credentials are excluded as documented in the repository scope.
- [`docs/experiment-methods.md`](docs/experiment-methods.md): preserved original working README. Its historical run instructions predate some extensions; use the explicit formal complex command below.
- [`docs/REPOSITORY_SCOPE.md`](docs/REPOSITORY_SCOPE.md): inclusion, exclusion, provenance and operational-replay boundaries.

## Revalidate saved evidence

These commands recompute comparisons from the curated saved outputs and plans. They do not rerun engine timings or require a GPU, JVM, generated Parquet dataset or plugin JAR. Run them from the repository root and use new output directories:

```bash
python3 analyze_crossover.py \
  --results evidence/results/production-20261025 \
            evidence/results/large-sf100-20261025 \
            evidence/results/large-sf300-20261025 \
  --output rerun/rechecked-baseline

python3 analyze_complex.py \
  --results evidence/results/complex-formal-v2-20261025 \
  --output rerun/rechecked-complex

(cd evidence && sha256sum -c SHA256SUMS)
```

Expected coverage is five baseline scales, 30 cases, 360 saved-output comparisons and 120 GPU plans; complex v2 has three scales, 18 cases, 216 saved-output comparisons, 216 cleanup barriers and 72 GPU plans. Counts include warmups and Polars warmup self-comparisons. Output agreement uses relative tolerance `1e-8`, absolute tolerance `1e-6`, and exact integer-pair checks. Small-scale independent oracle coverage does not prove independent full-scale oracle correctness.

## Run a new experiment

The recorded software was Python 3.10.12, Java 17.0.20.1, Polars 2.0.0, PySpark 3.5.9, DuckDB 1.5.6, PyArrow 22.0.0, psutil 7.1.2, and NVIDIA plugin 26.08.1 for Scala 2.12/CUDA 12. The host driver was 580.105.08. Dependencies and the GPU JAR are **not vendored**. On a suitably provisioned Linux GPU host:

```bash
python3.10 -m venv .venv
. .venv/bin/activate
python -m pip install -r evidence/requirements.lock.txt
mkdir -p jars
curl -fL \
  https://repo.maven.apache.org/maven2/com/nvidia/rapids-4-spark_2.12/26.08.1/rapids-4-spark_2.12-26.08.1.jar \
  -o jars/rapids-4-spark_2.12-26.08.1.jar
sha256sum -c evidence/DEPENDENCY_SHA256.txt
```

Generate compatible inputs under `data/sf30`, `data/sf100` and `data/sf300` before timing. See the preserved methods, generators and recorded source snapshots for historical generation details; generation must not overlap timed runs. New generation can change physical layout and compressed file hashes. All engines in a new comparison must share the same files and manifest.

The formal complex command explicitly preserves the revised lifecycle:

```bash
python run_experiment.py \
  --suite complex --query-cleanup verified_gc --cleanup-timeout-seconds 60 \
  --data-root data --output rerun/complex-formal-new \
  --scales 30 100 300 --runs 3 --cpus 30 --driver-memory 96g \
  --jar jars/rapids-4-spark_2.12-26.08.1.jar --keep-going
python analyze_complex.py \
  --results rerun/complex-formal-new --output rerun/complex-formal-new-report
```

The runner requires a new output directory. For a new original-suite comparison, use `--suite baseline --query-cleanup none`; the historical five-scale evidence was collected across three separate run roots. Optional plotting dependencies are pinned separately in `evidence/requirements-reporting.txt`.

Historical deployment and operational audits require original host metadata and native service records; the curated repository alone cannot reconstruct them. Read the included notices and source/dependency provenance. This private repository does not assert a blanket MIT license, a general license grant for third-party query/dependency material, or endorsement by NVIDIA, DuckDB or TPC.
