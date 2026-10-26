# When does GPU-accelerated Spark beat Polars?

> **Owner-private research draft.** The original six-query results cover five scales; the separate complex suite is validated at SF30, SF100 and SF300. The [owner-private study](https://a100-spark-polars-study-oct2026.ejiod.chatgpt.site) requires sign-in. Public release, attribution/notices, and any required permission or policy clearance remain unresolved. This file is not an assertion of public-release clearance.

Adding a GPU made our larger Spark workloads faster, but it did not make Spark faster than CPU Polars overall on the six queries we initially tested. At the largest observed scale—1.800 billion line items—our workload score was **28.187 seconds for Polars, 65.037 seconds for Spark with NVIDIA's GPU plugin, and 125.802 seconds for CPU Spark**. GPU Spark did win one query: the broad grouped aggregation, Q1.

Those results describe one configured machine, one selected workload, and five observed data scales. They do not establish a universal crossover threshold. We extended the experiment with six more query designs covering joins, nested queries, windows, exact distinct counts, and a fact-table self-join. That separate suite also retains a lower aggregate score for Polars through SF300, while GPU Spark wins particular query shapes.

## The question and the comparison

We set out to measure whether NVIDIA's cuDF plugin speeds up an existing Spark SQL workload, and whether a larger dataset changes its position relative to a native CPU DataFrame engine. NVIDIA describes its [cuDF plugin for Apache Spark](https://docs.nvidia.com/cudf-spark/latest/overview.html) as using libcudf to accelerate Spark processing. The product's JAR and configuration retain RAPIDS names; that is the plugin used here.

A single host gives all three engines access to the same input files and CPU hardware. It is a useful comparison for an analytical workload that can run on one machine. It does not test Spark's distributed-cluster capabilities, equivalent-price CPU instances, remote-storage performance, or the cost claims of NVIDIA's published multi-node studies. NVIDIA's overview discusses different hardware, workloads, and cluster configurations; this experiment is not a replication of those results.

We compared CPU Polars using its streaming engine, a CPU Spark baseline, and the same Spark SQL with the NVIDIA plugin enabled. Spark receives identical SQL in its CPU and GPU-plugin runs. Polars uses equivalent native lazy expressions, so the comparison includes each engine's normal planning and execution path rather than isolating a kernel.

## What we ran

The Lambda VM exposes 30 AMD EPYC 7J13 vCPUs, approximately 216 GiB host RAM, and one NVIDIA A100-SXM4-40GB GPU. All engines use the same host. Polars' worker pool is set to 30, and Spark runs as `local[30]`; these settings are not a cap of 30 total operating-system threads.

| Component | Recorded version or setting |
|---|---|
| Polars CPU | 2.0.0, streaming execution; `POLARS_MAX_THREADS=30` |
| Spark/PySpark | 3.5.9; same version in both Spark modes |
| NVIDIA plugin | 26.08.1; `rapids-4-spark_2.12-26.08.1.jar` |
| Python / Java | Python 3.10.12 / OpenJDK 17.0.20.1 |
| NVIDIA driver | 580.105.08 |
| Data generator | DuckDB 1.5.6 `tpch` extension |
| Supporting Python packages | PyArrow 22.0.0; psutil 7.1.2 |
| Shared Spark controls | 96 GiB driver heap; 120 shuffle partitions; 128 MiB maximum file partition size; AQE enabled; UTC |

The GPU-plugin run adds the JAR and these recorded settings:

```text
spark.plugins=com.nvidia.spark.SQLPlugin
spark.rapids.sql.enabled=true
spark.rapids.sql.explain=NOT_ON_GPU
spark.rapids.sql.concurrentGpuTasks=2
spark.rapids.memory.pinnedPool.size=2G
spark.rapids.sql.variableFloatAgg.enabled=true
```

These are the experiment's fixed settings, not the result of a tuning competition. The concurrency value is a recorded initial setting. Host RAM, the JVM heap, and GPU VRAM are different budgets; Polars has no matching explicit 96 GiB heap limit.

### Data and physical layout

We generated eight decision-support tables with [DuckDB's `tpch` extension](https://duckdb.org/docs/1.5/core_extensions/tpch) and exported Snappy Parquet with 122,880-row groups and a 128 MB target file size. DuckDB documents both its generator's partition parameters and differences from the official specification. This is an unaudited, derived workload with our own execution and scoring method, not a complete or certified TPC-H result.

At each scale, all three engines read the exact same files. Manifests record schemas, row counts, stored bytes, and per-file SHA-256 checksums. The largest dataset contains **1,799,989,091 line-item rows** and **2,597,989,121 rows across all eight tables**, occupying **107,978,166,411 bytes: 107.978 GB decimal or 100.563 GiB**.

Dataset size is not the bytes read by each query. The original six queries reference five of the eight tables, and their optimizers project columns and apply predicates. The score below must not be read as processing every stored byte once in the reported number of seconds.

For SF1, SF10, and SF30, we generated a complete DuckDB dataset before export. For SF100 and SF300, independent `dbgen(children, step)` processes generated bounded partitions directly into Parquet. The generator version and encoding settings stayed fixed, but physical file ordering and partition counts can differ across scales. Exact table-content parity and six-query numerical parity between whole and partitioned generation were checked at SF0.01, SF0.013, and SF1 with different child counts, including on the Linux experiment host. This small-scale proof is not an independent monolithic regeneration of SF300. Full-scale input manifests and post-run checksum validation identify the files actually used.

## The original SQL scope already includes joins

The initial suite is Q1, Q3, Q6, Q10, Q12, and Q14. Four of these six queries join tables; together they contain seven inner equijoin clauses. The [SQL and native Polars implementations](../workloads.py) are the authoritative definitions.

| Query | Tables | SQL joins | Main work and output |
|---|---|---:|---|
| Q1 | `lineitem` | 0 | Broad ship-date filter; grouped sums, averages, counts, discounted-price and tax arithmetic; all groups returned |
| Q3 | `lineitem`, `orders`, `customer` | 2 inner | Selective customer/date predicates, revenue by order, deterministic top 10 |
| Q6 | `lineitem` | 0 | Selective 1994 shipment, discount, and quantity predicates; one revenue sum |
| Q10 | `customer`, `orders`, `lineitem`, `nation` | 3 inner | Returned-line revenue for 1993 Q4 orders, customer grouping including strings, deterministic top 20 |
| Q12 | `lineitem`, `orders` | 1 inner | Shipment/date predicates, conditional priority counts by MAIL/SHIP mode |
| Q14 | `lineitem`, `part` | 1 inner | September 1995 shipments; promotional discounted revenue divided by total revenue |

The decimal quantity and monetary inputs are normalized to Float64/DOUBLE for every engine. That choice permits parallel floating-point reductions and does not measure strict decimal financial arithmetic. Extra sort keys make top-N ties deterministic. These modifications, the selected query subset, and our execution method are additional reasons to describe the study as derived research rather than an official benchmark result.

## How to read the timings

Each engine starts a fresh process/session at each scale. We read the dataset files before starting that engine to attempt operating-system filesystem preheating. No application DataFrame cache is used, and preheating does not prove that every page stays resident. Generation does not overlap timed query execution.

Each session records one warmup per query, then three measured trials per query. Engine and query ordering use fixed shuffle seeds. The three measurements remain inside the same engine session: they are not three independently restarted experiments and do not establish confidence intervals or tail latency.

For both suites, the timer surrounds query construction and materialized collection. It includes planning/optimization, Parquet reads, computation, and collection. It excludes session/table setup, warmups, and plan serialization. Polars' timer stops after `collect(engine="streaming")` materializes a DataFrame, before `to_dicts()` converts it to Python dictionaries. Spark's timer stops after `.collect()` returns Python `Row` objects; the subsequent `asDict()` conversion is outside its timer. Thus Python result conversion is not identical across engines. The original suite returns at most 20 rows per query; the complex suite has different output sizes, and the same API boundary remains part of its comparison.

For an engine and scale, the reported workload score is:

```text
score = sum over the six queries of median(trial 1, trial 2, trial 3)
```

It is a **sum of per-query medians**, not a measured once-through suite runtime. Full-precision trial values and min–max ranges are retained in the [result CSV](../reports/crossover-20261025/summary.csv) and [technical report](../reports/crossover-20261025/CROSSOVER_REPORT.md).

## Results: the original six queries

The table shows seconds, with lower values better. Scores were calculated from unrounded per-query medians and are displayed to three decimal places.

| Scale | Line-item rows | Polars CPU | CPU Spark | Spark + NVIDIA plugin |
|---|---:|---:|---:|---:|
| SF1 | 6,001,215 | 0.202 | 2.665 | 3.721 |
| SF10 | 59,986,052 | 1.106 | 9.212 | 6.202 |
| SF30 | 179,998,372 | 3.006 | 19.025 | 10.557 |
| SF100 | 600,037,902 | 9.258 | 47.736 | 23.432 |
| SF300 | 1,799,989,091 | 28.187 | 125.802 | 65.037 |

At SF300, the GPU-plugin score is **1.93 times faster than CPU Spark's**, while **Polars is 2.31 times faster than the GPU-plugin score overall**. GPU Spark is slower than CPU Spark at the smallest scale and faster on this aggregate at the four larger observed scales.

The per-query comparison is different from the aggregate. GPU Spark beats Polars on Q1 at SF30, SF100, and SF300. At SF300, Q1's median is **6.415 seconds on GPU Spark versus 8.601 seconds on Polars**, a 1.34-times speedup. Polars wins the other five queries at SF300. Across all 30 query/scale pairs, Polars wins 27 and GPU Spark wins three. The observed Q1 winner changes between SF10 and SF30. We did not measure an exact threshold between those observations, and no overall GPU win was observed at any tested scale.

[Figure 1: workload scores and per-query GPU/Polars ratios](../reports/crossover-20261025/crossover.png) connects measured points only; its lines are not a fitted crossover model. There is no extrapolation to untested sizes.

Startup also matters for short jobs. At SF300, launch-to-engine-ready was **0.259 seconds for Polars, 5.548 seconds for CPU Spark, and 19.724 seconds for GPU Spark**. Those values include session/table setup and overlap the separately recorded setup field. Whole-process wall time additionally includes every warmup, all three measured rounds, serialization, and shutdown; it is not the latency of one six-query pass.

## What was validated

Across five scales and three engines, we retained **360 materialized outputs: 90 warmups and 270 measured executions**. Each was compared with its query/scale's saved Polars warmup result, using relative tolerance `1e-8` and absolute tolerance `1e-6`; integer pairs and nonnumeric fields were compared exactly. All comparisons passed. The count includes comparisons of Polars warmups against themselves, so it is not 360 independent oracle proofs. The largest reported relative numerical difference was approximately `3.87e-12`.

We also inspected all **120 final GPU execution plans**, including warmups and measured runs, after excluding unused AQE initial-plan sections. Each contains GPU scan and GPU aggregation/join evidence. That verifies the selection of GPU operators. It does not establish a GPU runtime percentage or prove that every elapsed second was spent on the GPU. The [validation record](../reports/crossover-20261025/correctness.json) retains the per-output and per-plan checks.

All engines at each scale reference the same input-manifest hash. Source snapshots preserve the exact recorded code bytes for the original and larger runs, including the later addition of failure diagnostics. Standard Spark spill counters, RAPIDS task memory counters, sampled process RSS, and GPU telemetry are retained separately; none should be substituted for another or used alone to claim a bottleneck.

## Complex-query extension — three validated scales

The next six cases form a **separate suite**, not six extra terms added to the score above. The original suite already has four join queries. This extension changes join shapes and adds operations that may be accelerated, run as hybrid plans, or fall back to the CPU with the plugin enabled.

| Case | Design being tested |
|---|---|
| `cx_q09` | Six-table profit query with five inner joins, a composite `(partkey, suppkey)` join, a part-name predicate, and nation/year aggregation |
| `cx_q13` | Left outer join preserving zero-order customers, a comment-pattern filter, and nested aggregation into a customer/order-count distribution |
| `cx_semi_anti` | Correlated `EXISTS`/`NOT EXISTS` input design: qualifying 1995 orders with late AIR/SHIP lines, excluding customers with specified 1996 orders; maximum 100 customers. Input semantics do not establish observed anti-join execution. |
| `cx_window` | Chronological `LAG` and a separately ordered price-ranking `ROW_NUMBER` per customer on 1994–1995 orders; top three per customer, final maximum 100 rows |
| `cx_distinct` | Four-table join with three exact, null-excluding distinct counts for orders, customers, and parts; grouped by nation/order priority |
| `cx_self_join` | Two filtered 1994 `lineitem` inputs joined on order key with a residual line-number inequality; pair counts by ship-mode pair |

Q9 and Q13 are derived query designs; the other four are custom queries on the same derived tables. The [suite registry](../complex_workloads.py), [join definitions](../complex_joins.py), and [analytics definitions](../complex_analytics.py) specify their exact semantics.

### The resource incident and revised protocol

The earlier complex-suite attempt used the legacy session lifecycle, without an explicit post-query cleanup barrier. Its SF300 GPU Spark session recorded disk-space errors after 13 saved outputs and did not complete the required case coverage. The attempt is retained as incident evidence in its own bundle namespace, including its saved outputs and errors. Its timings are excluded from the revised comparison. The incident is not a GPU win, an infinite latency, or proof of an inherent single-query capacity limit.

The revised `verified_gc` protocol keeps one engine session per scale but adds a resource barrier after each query execution function returns. Polars receives Python garbage collection. Spark receives Python/JVM garbage-collection requests, then waits for its observed owned shuffle-file count and bytes to reach zero with no active jobs. Merely requesting GC is insufficient; counts, elapsed cleanup time and status are recorded. A failed barrier aborts the engine session. The [worker](../worker.py) and [cleanup helper](../query_cleanup.py) define this boundary.

These barriers run **outside the query timer**, after local query/DataFrame references have been released. Their costs remain inside process wall time. Explicit collection can change the heap state seen by the next query, so all displayed complex scales and engines use the same revised protocol. Legacy v1 timings are not mixed into the v2 curve, and neither complex score is added to the original six-query score. The original baseline above retains its original lifecycle.

### Results at SF30, SF100 and SF300

The final [v2 report](../reports/complex-formal-v2-20261025/COMPLEX_REPORT.md) validates **18 query/scale cases**, covering all six queries at all three scales. Its [evidence](../reports/complex-formal-v2-20261025/correctness.json) contains **216 saved-output comparisons, 72 inspected final GPU-plugin plans and 216 passed cleanup barriers**, with no selected-case exclusions or session errors. These are nine completed engine sessions, including 54 warmup and 162 measured outputs. Functional smoke timings and the v1 incident attempt are excluded.

The following seconds are medians of three measured trials, rounded only for display. The last row at each scale is the sum of six medians, not a once-through suite time.

| Scale / case | Polars CPU | CPU Spark | Spark + NVIDIA plugin |
|---|---:|---:|---:|
| SF30 / `cx_q09` | 1.044 | 16.067 | 6.975 |
| SF30 / `cx_q13` | 0.741 | 5.295 | 1.905 |
| SF30 / `cx_semi_anti` | 0.357 | 4.036 | 2.058 |
| SF30 / `cx_window` | 1.024 | 3.851 | 0.927 |
| SF30 / `cx_distinct` | 2.074 | 15.020 | 4.987 |
| SF30 / `cx_self_join` | 0.539 | 4.963 | 1.311 |
| **SF30 / sum of six medians** | **5.779** | **49.231** | **18.163** |
| SF100 / `cx_q09` | 4.285 | 42.573 | 29.182 |
| SF100 / `cx_q13` | 2.486 | 16.313 | 2.854 |
| SF100 / `cx_semi_anti` | 1.381 | 9.769 | 3.694 |
| SF100 / `cx_window` | 4.399 | 9.240 | 1.907 |
| SF100 / `cx_distinct` | 7.247 | 46.789 | 15.589 |
| SF100 / `cx_self_join` | 1.648 | 13.627 | 2.967 |
| **SF100 / sum of six medians** | **21.445** | **138.310** | **56.193** |
| SF300 / `cx_q09` | 13.274 | 112.258 | 116.707 |
| SF300 / `cx_q13` | 9.144 | 34.116 | 7.250 |
| SF300 / `cx_semi_anti` | 4.435 | 24.059 | 9.512 |
| SF300 / `cx_window` | 14.977 | 18.195 | 5.669 |
| SF300 / `cx_distinct` | 22.897 | 114.260 | 45.778 |
| SF300 / `cx_self_join` | 5.937 | 22.337 | 7.736 |
| **SF300 / sum of six medians** | **70.664** | **325.225** | **192.652** |

The aggregate ratios use the unrounded scores. Above 1× favors GPU-plugin Spark; below 1× favors the named CPU engine.

| Scale | Polars / GPU-plugin Spark | CPU Spark / GPU-plugin Spark |
|---|---:|---:|
| SF30 | 0.318× | 2.710× |
| SF100 | 0.382× | 2.461× |
| SF300 | 0.367× | 1.688× |

At SF300, Polars' complex score is **70.664 seconds**, compared with **192.652 seconds for GPU-plugin Spark** and **325.225 seconds for CPU Spark**. Polars is 2.73 times faster than the GPU-plugin score; the GPU-plugin score is 1.69 times faster than CPU Spark. Polars has the lower aggregate score at all three observed scales, so this suite also yields no aggregate GPU-over-Polars crossover through SF300.

The individual results are more selective. GPU-plugin Spark beats Polars on `cx_window` at all three scales, reaching **5.669 seconds versus 14.977 seconds** at SF300, a **2.64-times** speedup. It also beats Polars on `cx_q13` at SF300: **7.250 seconds versus 9.144 seconds**, a **1.26-times** speedup. That outer-join case changes observed winner between SF100 and SF300; an exact crossover within that interval was not measured. Polars has the lower median on the other four SF300 cases. Across all 18 complex query/scale cases, GPU-plugin Spark wins four and Polars wins 14.

Compared with CPU Spark, GPU-plugin Spark has the lower median on 17 of the 18 cases. The exception is SF300 `cx_q09`: GPU-plugin Spark is **116.707 seconds**, compared with **112.258 seconds** for CPU Spark. This fixed configuration does not show a GPU advantage for every large join. The [CSV](../reports/complex-formal-v2-20261025/summary.csv) retains unrounded medians and descriptive min–max ranges; three within-session trials do not establish statistical significance.

The per-scale figures show observed query medians and ratios for [SF30](../reports/complex-formal-v2-20261025/complex-sf30.png), [SF100](../reports/complex-formal-v2-20261025/complex-sf100.png) and [SF300](../reports/complex-formal-v2-20261025/complex-sf300.png). They do not fit or extrapolate a threshold.

The resource costs are substantial enough to disclose separately. Each session below contains 24 barriers: six warmups plus 18 measured query actions. Process wall time includes startup, setup, all those actions, serialization, cleanup and shutdown; the columns overlap and must not be added.

| Scale / engine | Passed barriers | Cleanup sum (s) | Process wall (s) |
|---|---:|---:|---:|
| SF30 / Polars | 24 | 0.144 | 24.766 |
| SF30 / CPU Spark | 24 | 67.473 | 280.583 |
| SF30 / GPU-plugin Spark | 24 | 69.133 | 167.725 |
| SF100 / Polars | 24 | 0.155 | 90.467 |
| SF100 / CPU Spark | 24 | 92.812 | 670.006 |
| SF100 / GPU-plugin Spark | 24 | 96.410 | 353.565 |
| SF300 / Polars | 24 | 0.159 | 290.398 |
| SF300 / CPU Spark | 24 | 155.178 | 1489.023 |
| SF300 / GPU-plugin Spark | 24 | 166.074 | 979.724 |

Adding the cleanup sum over four actions per query to a sum of one median per query would not construct an end-to-end score. The recorded process wall is also not the latency of one six-query pass.

### Correctness and executed-plan scope

Each included case requires complete trial coverage and cross-engine output agreement with its saved Polars warmup, under the same integer and floating-point comparison rules as the original suite. No matching independent full-scale DuckDB oracle is supplied for these SF30/SF100/SF300 cases. A separate small-scale oracle checks only the matching manifest, SQL hash and scale it actually covers; it does not establish independent oracle correctness at every larger scale.

The complex report labels named final-plan coverage as GPU operators present, hybrid, or CPU fallback. All 72 saved v2 GPU-session plans contain GPU operators and have no CPU core nodes detected by the analyzer's name check. This classification does not mean every elapsed operation ran on the GPU. A faster GPU-plugin session that falls back to CPU must not be described as GPU compute acceleration. A failed or incomplete case is excluded, and a full six-case score is available only if all six cases pass. The [complex analyzer](../analyze_complex.py) and [plotter](../plot_complex.py) implement those gates.

The correlated case needs a further distinction between its SQL and the executed work. In the saved v1 SF30/SF100 actions, final plans retain two semi joins but omit the `LeftAnti` branch. Event evidence shows the exchanged anti input produced zero records and bytes, even though its scan/filter work ran. That establishes an empty exchanged input in those observed actions; it does not establish its generator-level cause or timed probing of a nonempty GPU anti join. The [plan notes](COMPLEX_PLAN_NOTES.md) document this v1-only coverage audit. The separate [SF300 v2 GPU/Polars pair audit](../artifacts/complex/sf300-gpu-polars-pair-notes.md), made before CPU completion, also finds `LeftAnti` in all four GPU initial plans and in none of their final plans. Its scope is the saved GPU/Polars pair; the full three-engine numerical validation is supplied by the final report above. The input remains a correlated `EXISTS`/`NOT EXISTS` design, not a demonstrated nonempty GPU anti-join workload. Neither plan-node presence nor absence establishes a runtime fraction.

## Reproducibility and the boundary of the conclusion

The private review bundle uses an allowlist of benchmark artifacts. It contains pinned requirements, query definitions, generators, runners, exact source snapshots, all selected saved trial values and outputs, final plans, input manifests, and validation evidence. Generated datasets and NVIDIA dependency binaries are not part of the download; the generation recipe and official dependency sources are provided instead.

After the final complex session ended, the read-only [input-integrity check](../artifacts/complex/postrun-input-integrity.json) rehashed all **1,773 Parquet files across the five scales**, totaling **157,967,833,132 bytes**. Every file matched its original manifest checksum, with no missing or extra Parquet files. The three complex-run manifests and all nine engine sessions reference those same verified dataset identities. The SF30 alias resolves to the verified shared dataset. This verifies the saved inputs after execution; it does not independently regenerate their contents or prove query semantics.

The [bundle exporter](export_bundle.py) records exported payload integrity in `SHA256SUMS`, with one line per file in the format `SHA256  relative/path`. For copied or transformed source-backed files, `PROVENANCE.json` records the original and exported SHA-256 values, source references, and normalization applied. Generated summaries are also covered by `SHA256SUMS`. `BUNDLE.json` records included runs and excluded dataset/dependency binaries. The ZIP has a separate `.sha256` sidecar and download metadata containing its byte length, archive hash, and content-manifest hash. Private host identifiers and machine-specific paths are normalized in the exported evidence copy; its transformed bytes are not claimed to retain the private originals' hashes.

On these observed inputs and settings, the plugin accelerates the larger CPU Spark workload, while CPU Polars remains faster on the six-query aggregate. Q1 demonstrates why per-query results matter. Neither outcome predicts an untested workload, a fully tuned engine comparison, a distributed cluster, cold object storage, equivalent-cost instances, or a universal winner. The separate complex suite adds observed window and outer-join wins for GPU Spark while retaining Polars' lower aggregate score through SF300. It does not change what the original results measure.

## Internal editorial notes — private update and later public release

- **Complex results:** the article now uses the final three-scale v2 report: 18 cases, 216 saved-output comparisons, 216 passed barriers and 72 final GPU-plugin plans. Keep smoke checks, v1 incident evidence, the revised protocol and the original suite separate. The final private-site/data-bundle update and post-run input-integrity evidence are owned by the publication workflow; do not mark them complete from this article edit alone.
- **Review destination and release:** the owner-private study uses the link above. This does not assert public availability or release clearance. Public release remains unresolved. Report, source and chart links above retain their repository context; the evidence bundle explicitly records which narrative-link targets it includes. Rebuild the bundle after subsequent artifact edits and verify its archive and payload hashes.
- **Policy, permission, and notices:** review [NOTICE_RESEARCH.md](NOTICE_RESEARCH.md) and its primary TPC/DuckDB sources. No TPC permission or policy clearance is asserted here. Final naming, result/chart notices, attribution, redistribution terms, and any required written permission remain to be resolved. Do not label the whole bundle MIT or imply endorsement based only on dependency licenses or this disclaimer.
- **Source references:** the product description was checked against [NVIDIA's official overview](https://docs.nvidia.com/cudf-spark/latest/overview.html); generation parameters and the specification caveat were checked against [DuckDB's version 1.5 extension documentation](https://duckdb.org/docs/1.5/core_extensions/tpch). These references identify sources, not approval to publish this study. The [internal audit](audit.md) records the limits of the saved evidence.
- **Copy and figures:** preserve the selected-workload, derived/non-certified status near the results and every chart as required by the resolved notice review. Avoid “108 GB processed in 28 seconds,” an exact unmeasured crossover, global product claims, or claims of statistical significance from three within-session trials.
