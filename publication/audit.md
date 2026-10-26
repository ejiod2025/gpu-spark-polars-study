# Publication audit

Audit date: 2026-10-25. This is an internal review of the saved experiment, not publication copy. Existing measurements and source files were not changed.

## Finding

The evidence supports a **descriptive, fixed-configuration, single-host comparison** of six selected TPC-H-derived workloads. No numerical contradiction was found. It does not establish a product-wide winner, an exact crossover threshold, distributed-cluster performance, or cost efficiency.

At the largest observed scale, Polars' workload score is 28.186591586 seconds, GPU Spark's is 65.037434429 seconds, and CPU Spark's is 125.801552790 seconds. Polars is 2.307389108 times faster than GPU Spark by this score; GPU Spark is 1.934294517 times faster than CPU Spark. GPU Spark wins Q1 by 1.340728876 times. Polars wins the other five queries.

## Independent numerical verification

Raw `results.json` measured records were regrouped by query, and the medians and their sums were recomputed independently of the report CSV. Every measured group contains exactly trials 1, 2, and 3. The recomputed medians match the published CSV exactly.

| Scale | Line-item rows | Stored Parquet bytes, all eight tables | Polars sum (s) | CPU Spark sum (s) | GPU Spark sum (s) |
|---|---:|---:|---:|---:|---:|
| SF1 | 6,001,215 | 325,716,220 | 0.202174404 | 2.665100707 | 3.720681384 |
| SF10 | 59,986,052 | 3,442,803,048 | 1.106163913 | 9.212140812 | 6.202296909 |
| SF30 | 179,998,372 | 10,568,904,879 | 3.005902647 | 19.024656847 | 10.556893034 |
| SF100 | 600,037,902 | 35,652,242,574 | 9.257843284 | 47.735889446 | 23.432001159 |
| SF300 | 1,799,989,091 | 107,978,166,411 | 28.186591586 | 125.801552790 | 65.037434429 |

GPU Spark wins Q1 at SF30, SF100, and SF300. Polars wins the other 27 query/scale pairs. The observed Q1 winner changes between SF10 and SF30; this is a bracket between observations, not a measured exact threshold. No overall GPU win occurs at any of the five observed scales. No threshold should be extrapolated beyond them.

All 360 saved outputs compare successfully against the corresponding Polars warmup baseline, using relative tolerance `1e-8` and absolute tolerance `1e-6`; integer pairs and nonnumeric fields match exactly. The largest reported relative numeric difference is approximately `3.87e-12`. These 360 records comprise **270 measured executions and 90 warmups**, including 30 Polars warmup comparisons against themselves. They are not 360 independent oracle validations.

All 120 saved GPU plans were re-read with the strict final-plan inspector, excluding unused AQE initial-plan text. Each contains GPU scan plus GPU aggregation/join evidence. Across these final plans, the inspector counts 260 GPU scan, 236 GPU aggregate, and 140 GPU join occurrences. Its CPU scan/aggregate/join name check detects none. These are plan occurrences, not runtime fractions or unique stages; they do not establish that every operation or every elapsed second ran on the GPU.

All 15 engine sessions reference their scale's copied manifest SHA-256. Source snapshots match all recorded source hashes: baseline 6/6, SF100 9/9, and SF300 9/9. The baseline and SF100 snapshot environment hashes also match their original environment files. All 15 engine processes exited successfully. Post-run dataset validation logs exist for both larger datasets.

## Disclosures required beside the results

1. **Define the score.** It is the sum of six per-query medians, not a measured complete-suite latency. Query timers cover query construction/optimization, reads, computation, and materialized collection. Startup, table setup, warmups, and plan serialization are excluded. Show startup separately rather than silently adding overlapping setup/process scopes.
2. **Name the workload.** Q1, Q3, Q6, Q10, Q12, and Q14 are selected TPC-H-derived queries with deterministic extra tie-breaking keys and Float64/DOUBLE casts for decimal quantity/money columns. These are neither a complete nor a certified TPC-H result, and not a reproduction of NVIDIA's advertised multi-node TPC-DS/NDS benchmarks.
3. **Identify the engines exactly.** Polars 2.0.0 CPU streaming; Spark/PySpark 3.5.9 CPU baseline; the same Spark with NVIDIA cuDF/RAPIDS plugin 26.08.1 (`rapids-4-spark_2.12-26.08.1.jar`). The RAPIDS artifact/class names identify the actual cuDF Spark product. Python 3.10.12, Java 17.0.20.1, DuckDB 1.5.6, PyArrow 22.0.0, psutil 7.1.2, NVIDIA driver 580.105.08 are recorded. The `nvidia-smi` CUDA compatibility number is not an installed-toolkit or JAR-build version.
4. **Describe resources accurately.** One A100-SXM4-40GB on a Lambda VM with 30 vCPUs and about 216 GiB host RAM. Polars' worker pool is 30; Spark uses `local[30]`. This is not a hard limit of 30 total OS threads per engine. Both Spark modes have a 96 GiB driver heap, 120 shuffle partitions, 128 MiB file partitions, and AQE. Polars has no equivalent explicit heap limit; GPU host allocations and VRAM are separate budgets. Publish the complete GPU configuration, including floating-point aggregation being enabled, pinned memory, and initial GPU concurrency.
5. **Describe cache and repetition scope.** Identical files are read to attempt filesystem preheating before each engine. There is no application DataFrame cache, but residency is not measured or guaranteed. One warmup per query precedes three measurements within one fresh engine session per scale. These are not three independently restarted experiments. Retain all trial values and ranges; do not claim confidence intervals or statistical significance from these samples.
6. **Describe the API comparison.** Spark SQL and equivalent native Polars lazy expressions implement the query semantics. API/optimizer and Python–JVM integration costs are included. This is not a kernel-only or identical-SQL-parser comparison. Polars' `to_dicts()` conversion is outside its timer; Spark's `.collect()` returns Python rows inside its timer. Final outputs are small (at most 20 rows), but the timing scopes should still be explicit.
7. **Scope size claims.** SF300 has 1,799,989,091 line items and 2,597,989,121 total rows across eight tables. Its Parquet size is 107.978 GB decimal / 100.563 GiB. The six queries reference five tables totaling 94,542,135,812 stored bytes before projection/filtering; the remaining three tables are unused. Do not write that Polars "processed all 108 GB in 28 seconds" or divide dataset bytes by this aggregate score as physical throughput.
8. **Disclose the generation change.** SF1/10/30 used whole-DuckDB generation and parallel export; SF100/300 used isolated `dbgen(children, step)` generation. The version and encoding settings remain the same; file layout/order can differ across scales. All three engines at a given scale use the exact same files. Exact whole-vs-child table parity and six-query parity were demonstrated at SF0.01/7 children, SF0.013/31, and SF1/7 on both local and Linux runs. Do not describe that proof as an independent whole-SF300 comparison.
9. **Limit causal and economic claims.** Plans prove GPU operators; utilization, a preallocated RMM pool, and standard Spark spill counters do not independently explain a bottleneck. Zero standard Spark spill is not proof of zero GPU device-to-host spill. Sampled process RSS is not total host usage or GPU VRAM. There is no equivalent-price CPU-instance, cluster-scaling, cold-storage, energy, or fully tuned-engine comparison here.

## Public reproducibility package requirements

- Include the exact queries, generation and runner code, pinned requirements, readable hardware/configuration metadata, all measured trial values, materialized outputs, input manifests, GPU final plans, and source snapshot verification records. Include validation and generation-equivalence proof artifacts, plus CSV and machine-readable results underlying the charts.
- Distinguish original source snapshots from later runner diagnostics changes. The existing snapshots allow exact reproduction of the recorded baseline and extended-source bytes. Do not replace old provenance with current-file hashes.
- Use portable relative links and paths. Existing reports and diagnostics contain local `/Users/...` paths and remote deployment paths; raw environments/logs can also expose the VM IP/hostname, GPU UUID/serial, and other host identifiers. Publish a deliberately normalized evidence copy while retaining the private originals. Recompute public-payload checksums and disclose any identifier normalization; do not claim changed files retain original byte hashes.
- Scope the package to the benchmark directory. Do not publish unrelated AIRFLOW files, SSH material, generated data, or NVIDIA JAR binaries by accident. Link the official dependency/download and data-generation sources. Confirm attribution/license treatment for included query derivatives and dependencies before choosing a public source license.

## Required wording corrections

Use: "On this one-host, six-query, warm-filesystem experiment, GPU Spark did not beat CPU Polars overall at any observed scale through SF300. It accelerated the CPU Spark baseline at the larger scales and beat Polars on Q1."

Avoid: "GPU Spark never beats Polars," "the crossover is exactly 180 million rows," "Polars scans 108 GB in 28 seconds," "360 independent correctness proofs," "100% GPU execution," "equal memory budgets," and unsupported price, energy, or distributed-production conclusions.

The narrow descriptive findings can be published with these qualifications and a portable evidence package. Stronger statistical, cost, causal-bottleneck, or production-wide claims require additional experiments rather than more confident wording.
