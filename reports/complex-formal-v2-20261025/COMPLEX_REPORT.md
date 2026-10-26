# Complex-query experiment

This separate suite tests six joins, nested-query, window, exact-distinct, and self-join cases. Its score is not added to the original six-query experiment.

Verified cases: **18**; excluded incomplete/invalid cases: **0**. Only included cases enter timing tables. All saved warmup and measured outputs in each included case agree with its Polars warmup baseline (relative tolerance 1e-08, absolute 1e-06; integer pairs exact).

Times are per-query median [minimum, maximum] seconds. Ratios are CPU-engine time divided by GPU-plugin Spark time; values above 1 favor the GPU-plugin session. That session can contain GPU operators, a hybrid plan, or CPU fallback.

Resource protocol is part of the result: `none` is the preserved legacy session method; `verified_gc` adds a verified post-query resource barrier outside query timers. Do not silently combine those methods into one uniform scaling series.

| Scale / query | Type | Resource protocol | Polars CPU | CPU Spark | GPU-plugin Spark | Polars / GPU | CPU Spark / GPU | Inspected coverage |
|---|---|---|---:|---:|---:|---:|---:|---|
| sf30 / cx_q09 | Six-table join and composite join keys | verified_gc | 1.044 [0.996, 1.065] | 16.067 [14.646, 17.070] | 6.975 [6.530, 7.139] | 0.150x | 2.303x | gpu_operators_present |
| sf30 / cx_q13 | Left outer join and nested aggregation | verified_gc | 0.741 [0.698, 0.759] | 5.295 [4.919, 6.256] | 1.905 [1.869, 1.953] | 0.389x | 2.780x | gpu_operators_present |
| sf30 / cx_semi_anti | Correlated EXISTS/NOT EXISTS and semi/anti joins | verified_gc | 0.357 [0.348, 0.358] | 4.036 [3.989, 4.186] | 2.058 [1.999, 2.105] | 0.174x | 1.961x | gpu_operators_present |
| sf30 / cx_window | Two window orderings: LAG and ROW_NUMBER | verified_gc | 1.024 [0.963, 1.121] | 3.851 [3.069, 3.878] | 0.927 [0.904, 0.957] | 1.105x | 4.156x | gpu_operators_present |
| sf30 / cx_distinct | Four-table join and three exact distinct counts | verified_gc | 2.074 [2.005, 2.076] | 15.020 [13.472, 15.700] | 4.987 [4.781, 5.015] | 0.416x | 3.012x | gpu_operators_present |
| sf30 / cx_self_join | Fact self-join with an inequality condition | verified_gc | 0.539 [0.475, 0.539] | 4.963 [4.619, 5.564] | 1.311 [1.311, 1.364] | 0.411x | 3.785x | gpu_operators_present |
| sf100 / cx_q09 | Six-table join and composite join keys | verified_gc | 4.285 [4.275, 4.365] | 42.573 [41.480, 46.511] | 29.182 [27.098, 29.233] | 0.147x | 1.459x | gpu_operators_present |
| sf100 / cx_q13 | Left outer join and nested aggregation | verified_gc | 2.486 [2.456, 3.062] | 16.313 [16.135, 16.435] | 2.854 [2.725, 2.884] | 0.871x | 5.717x | gpu_operators_present |
| sf100 / cx_semi_anti | Correlated EXISTS/NOT EXISTS and semi/anti joins | verified_gc | 1.381 [1.340, 1.387] | 9.769 [8.821, 10.190] | 3.694 [3.617, 3.792] | 0.374x | 2.644x | gpu_operators_present |
| sf100 / cx_window | Two window orderings: LAG and ROW_NUMBER | verified_gc | 4.399 [4.203, 4.629] | 9.240 [8.709, 9.684] | 1.907 [1.786, 1.958] | 2.307x | 4.845x | gpu_operators_present |
| sf100 / cx_distinct | Four-table join and three exact distinct counts | verified_gc | 7.247 [7.241, 7.398] | 46.789 [46.783, 47.042] | 15.589 [15.524, 16.447] | 0.465x | 3.001x | gpu_operators_present |
| sf100 / cx_self_join | Fact self-join with an inequality condition | verified_gc | 1.648 [1.635, 1.701] | 13.627 [11.910, 15.214] | 2.967 [2.954, 3.015] | 0.555x | 4.593x | gpu_operators_present |
| sf300 / cx_q09 | Six-table join and composite join keys | verified_gc | 13.274 [12.994, 13.717] | 112.258 [106.564, 114.910] | 116.707 [115.184, 116.767] | 0.114x | 0.962x | gpu_operators_present |
| sf300 / cx_q13 | Left outer join and nested aggregation | verified_gc | 9.144 [9.061, 10.119] | 34.116 [32.506, 34.375] | 7.250 [7.062, 7.265] | 1.261x | 4.706x | gpu_operators_present |
| sf300 / cx_semi_anti | Correlated EXISTS/NOT EXISTS and semi/anti joins | verified_gc | 4.435 [4.208, 4.458] | 24.059 [21.714, 24.272] | 9.512 [9.321, 9.802] | 0.466x | 2.529x | gpu_operators_present |
| sf300 / cx_window | Two window orderings: LAG and ROW_NUMBER | verified_gc | 14.977 [14.461, 16.251] | 18.195 [18.084, 18.638] | 5.669 [5.603, 6.170] | 2.642x | 3.210x | gpu_operators_present |
| sf300 / cx_distinct | Four-table join and three exact distinct counts | verified_gc | 22.897 [22.577, 23.280] | 114.260 [113.654, 117.443] | 45.778 [45.539, 45.985] | 0.500x | 2.496x | gpu_operators_present |
| sf300 / cx_self_join | Fact self-join with an inequality condition | verified_gc | 5.937 [5.222, 7.183] | 22.337 [21.914, 24.889] | 7.736 [7.578, 7.847] | 0.768x | 2.887x | gpu_operators_present |

## Full-suite summaries

A sum of six per-query medians is shown only when all six cases at that scale pass complete three-engine validation. It is a derived workload score, not a measured once-through suite runtime.

| Scale | Trials/query | Resource protocol | Polars sum (s) | CPU Spark sum (s) | GPU-plugin Spark sum (s) | Polars / GPU | CPU Spark / GPU |
|---|---:|---|---:|---:|---:|---:|---:|
| sf30 | 3 | verified_gc | 5.779 | 49.231 | 18.163 | 0.318x | 2.710x |
| sf100 | 3 | verified_gc | 21.445 | 138.310 | 56.193 | 0.382x | 2.461x |
| sf300 | 3 | verified_gc | 70.664 | 325.225 | 192.652 | 0.367x | 1.688x |

## GPU and CPU plan coverage

Coverage is inspected from the saved final AQE plan, excluding unused initial plans. Classification is based on named plan nodes, not runtime percentages. `gpu_operators_present` does not mean every operation ran on the GPU. `hybrid` means GPU and checked CPU core operators appear; `cpu_fallback` means no GPU-prefixed nodes were observed despite the enabled plugin. Union labels summarize all saved runs; per-record classifications and any recorded-label disagreements remain in `correctness.json`.

| Scale / query | GPU node names | Checked CPU core node names | Classes observed |
|---|---|---|---|
| sf30 / cx_q09 | GpuBroadcastExchange, GpuBroadcastHashJoin, GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledSymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |
| sf30 / cx_q13 | GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledAsymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |
| sf30 / cx_semi_anti | GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuProject, GpuShuffleCoalesce, GpuShuffledHashJoin, GpuTopN | none detected by name check | gpu_operators_present |
| sf30 / cx_window | GpuBatchedBoundedWindow, GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuProject, GpuRunningWindow, GpuShuffleCoalesce, GpuSort, GpuTopN | none detected by name check | gpu_operators_present |
| sf30 / cx_distinct | GpuBroadcastExchange, GpuBroadcastHashJoin, GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuExpand, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledSymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |
| sf30 / cx_self_join | GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledSymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |
| sf100 / cx_q09 | GpuBroadcastExchange, GpuBroadcastHashJoin, GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledSymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |
| sf100 / cx_q13 | GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledAsymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |
| sf100 / cx_semi_anti | GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuProject, GpuShuffleCoalesce, GpuShuffledHashJoin, GpuTopN | none detected by name check | gpu_operators_present |
| sf100 / cx_window | GpuBatchedBoundedWindow, GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuProject, GpuRunningWindow, GpuShuffleCoalesce, GpuSort, GpuTopN | none detected by name check | gpu_operators_present |
| sf100 / cx_distinct | GpuBroadcastExchange, GpuBroadcastHashJoin, GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuExpand, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledSymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |
| sf100 / cx_self_join | GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledSymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |
| sf300 / cx_q09 | GpuBroadcastExchange, GpuBroadcastHashJoin, GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledSymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |
| sf300 / cx_q13 | GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledAsymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |
| sf300 / cx_semi_anti | GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuProject, GpuShuffleCoalesce, GpuShuffledHashJoin, GpuTopN | none detected by name check | gpu_operators_present |
| sf300 / cx_window | GpuBatchedBoundedWindow, GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuFileGpuScan, GpuFilter, GpuProject, GpuRunningWindow, GpuShuffleCoalesce, GpuSort, GpuTopN | none detected by name check | gpu_operators_present |
| sf300 / cx_distinct | GpuBroadcastExchange, GpuBroadcastHashJoin, GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuExpand, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledSymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |
| sf300 / cx_self_join | GpuCoalesceBatches, GpuColumnarExchange, GpuColumnarToRow, GpuCustomShuffleReader, GpuFileGpuScan, GpuFilter, GpuHashAggregate, GpuProject, GpuShuffleCoalesce, GpuShuffledSymmetricHashJoin, GpuSort | none detected by name check | gpu_operators_present |

## Independent oracle scope

Oracle-checked included cases: **0**. Included cases without an independent oracle: **18**. Independent DuckDB checks apply only to matching manifest/SQL/scale cases; other cases use Polars warmup cross-engine agreement. Oracle completion, suite/version, manifest SHA-256, and exact registry SQL SHA-256 must match before outputs are compared. A small-scale oracle does not prove independent oracle correctness at larger scales.

## Startup and process scopes

Query timings exclude startup, table/view setup, warmups and plan serialization. Launch-to-ready includes session setup and overlaps that field. Process wall time also contains all warmups, measured queries, serialization, and shutdown. Under verified_gc it also includes the explicit resource barriers. Do not add overlapping fields or treat process wall time as one six-query pass.

| Scale / engine | Resource protocol | Launch → ready (s) | Session + table setup (s) | Saved warmup sum (s) | Explicit cleanup count / sum (s) | Process wall (s) |
|---|---|---:|---:|---:|---:|---:|
| sf30 / polars | verified_gc | 0.178 | 0.124 | 5.775 | 24 / 0.144 | 24.766 |
| sf30 / spark_cpu | verified_gc | 5.669 | 4.148 | 58.179 | 24 / 67.473 | 280.583 |
| sf30 / spark_gpu | verified_gc | 19.673 | 17.999 | 19.691 | 24 / 69.133 | 167.725 |
| sf100 / polars | verified_gc | 0.181 | 0.125 | 22.863 | 24 / 0.155 | 90.467 |
| sf100 / spark_cpu | verified_gc | 5.805 | 4.253 | 149.128 | 24 / 92.812 | 670.006 |
| sf100 / spark_gpu | verified_gc | 19.663 | 17.965 | 65.120 | 24 / 96.410 | 353.565 |
| sf300 / polars | verified_gc | 0.216 | 0.159 | 71.958 | 24 / 0.159 | 290.398 |
| sf300 / spark_cpu | verified_gc | 5.777 | 4.255 | 348.634 | 24 / 155.178 | 1489.023 |
| sf300 / spark_gpu | verified_gc | 19.746 | 18.038 | 209.442 | 24 / 166.074 | 979.724 |

## Post-query resource barriers

verified_gc barriers occur after each execute returns, outside query timers; explicit barrier duration is reported separately and remains inside process wall time. Legacy none sessions have no verified barrier measurement.

Under `verified_gc`, every saved warmup and measured output must have exactly one matching passed cleanup record. Polars receives Python garbage collection; Spark receives Python/JVM garbage collection followed by a wait for the owned shuffle-file count and bytes to reach zero. A request for garbage collection alone is insufficient. A global cleanup failure invalidates the engine session. The barrier occurs after query execution returns and changes the lifecycle of a long-lived session; it is a declared protocol change, not evidence that the legacy failed run succeeded. Legacy `none` sessions have no explicit verified-barrier measurement; that does not mean automatic cleanup took no time.

Per-case explicit cleanup below sums all saved barriers, including the warmup and measured trials. It is separate from the query medians and already lies inside process wall time.

| Scale / query | Engine | Passed barriers | Explicit cleanup sum (s) |
|---|---|---:|---:|
| sf30 / cx_q09 | polars | 4 | 0.026 |
| sf30 / cx_q09 | spark_cpu | 4 | 15.348 |
| sf30 / cx_q09 | spark_gpu | 4 | 14.803 |
| sf30 / cx_q13 | polars | 4 | 0.024 |
| sf30 / cx_q13 | spark_cpu | 4 | 9.938 |
| sf30 / cx_q13 | spark_gpu | 4 | 10.338 |
| sf30 / cx_semi_anti | polars | 4 | 0.022 |
| sf30 / cx_semi_anti | spark_cpu | 4 | 9.914 |
| sf30 / cx_semi_anti | spark_gpu | 4 | 10.179 |
| sf30 / cx_window | polars | 4 | 0.024 |
| sf30 / cx_window | spark_cpu | 4 | 9.817 |
| sf30 / cx_window | spark_gpu | 4 | 10.269 |
| sf30 / cx_distinct | polars | 4 | 0.026 |
| sf30 / cx_distinct | spark_cpu | 4 | 12.524 |
| sf30 / cx_distinct | spark_gpu | 4 | 13.235 |
| sf30 / cx_self_join | polars | 4 | 0.023 |
| sf30 / cx_self_join | spark_cpu | 4 | 9.931 |
| sf30 / cx_self_join | spark_gpu | 4 | 10.308 |
| sf100 / cx_q09 | polars | 4 | 0.031 |
| sf100 / cx_q09 | spark_cpu | 4 | 28.207 |
| sf100 / cx_q09 | spark_gpu | 4 | 30.617 |
| sf100 / cx_q13 | polars | 4 | 0.024 |
| sf100 / cx_q13 | spark_cpu | 4 | 11.246 |
| sf100 / cx_q13 | spark_gpu | 4 | 11.164 |
| sf100 / cx_semi_anti | polars | 4 | 0.025 |
| sf100 / cx_semi_anti | spark_cpu | 4 | 10.046 |
| sf100 / cx_semi_anti | spark_gpu | 4 | 10.339 |
| sf100 / cx_window | polars | 4 | 0.023 |
| sf100 / cx_window | spark_cpu | 4 | 11.923 |
| sf100 / cx_window | spark_gpu | 4 | 12.132 |
| sf100 / cx_distinct | polars | 4 | 0.029 |
| sf100 / cx_distinct | spark_cpu | 4 | 20.378 |
| sf100 / cx_distinct | spark_gpu | 4 | 20.982 |
| sf100 / cx_self_join | polars | 4 | 0.024 |
| sf100 / cx_self_join | spark_cpu | 4 | 11.012 |
| sf100 / cx_self_join | spark_gpu | 4 | 11.177 |
| sf300 / cx_q09 | polars | 4 | 0.027 |
| sf300 / cx_q09 | spark_cpu | 4 | 56.521 |
| sf300 / cx_q09 | spark_gpu | 4 | 67.397 |
| sf300 / cx_q13 | polars | 4 | 0.024 |
| sf300 / cx_q13 | spark_cpu | 4 | 15.057 |
| sf300 / cx_q13 | spark_gpu | 4 | 15.428 |
| sf300 / cx_semi_anti | polars | 4 | 0.024 |
| sf300 / cx_semi_anti | spark_cpu | 4 | 11.304 |
| sf300 / cx_semi_anti | spark_gpu | 4 | 12.346 |
| sf300 / cx_window | polars | 4 | 0.030 |
| sf300 / cx_window | spark_cpu | 4 | 15.956 |
| sf300 / cx_window | spark_gpu | 4 | 16.432 |
| sf300 / cx_distinct | polars | 4 | 0.030 |
| sf300 / cx_distinct | spark_cpu | 4 | 42.073 |
| sf300 / cx_distinct | spark_gpu | 4 | 39.941 |
| sf300 / cx_self_join | polars | 4 | 0.024 |
| sf300 / cx_self_join | spark_cpu | 4 | 14.267 |
| sf300 / cx_self_join | spark_gpu | 4 | 14.529 |

## Exclusions and errors

No selected cases were excluded.

## Interpretation and reproducibility

- This is a fixed-configuration single-host experiment on the same shared Parquet inputs per scale. It does not establish distributed scaling, equivalent-cost performance, or a product-wide winner.
- Polars uses CPU streaming; both Spark modes share recorded Spark controls and CPU allocation. The GPU-plugin session adds NVIDIA's plugin and its GPU settings. Host RAM, the Spark heap, and GPU VRAM are different budgets.
- Filesystem preheating is attempted before each engine; no application DataFrame cache is used. Residency is not guaranteed, so these are not certified pure-compute or cold-storage measurements.
- One warmup plus three measured trials within one engine session is descriptive. It is not three independently restarted experiments and does not establish tail latency or statistical significance.
- Q9/Q13 are TPC-H-derived and the other cases are custom queries on the same derived tables; this is not a complete or certified TPC-H run. Float64/DOUBLE casts and deterministic output ordering are retained across engines.
- A failed or incomplete engine/case is excluded rather than assigned an infinite latency or counted as a GPU win. A full-suite score requires all six cases, even when only five succeed.
- Raw roots retain actual outputs, plans, configuration, manifests, logs, source hashes and telemetry. Later roots replace earlier roots for a duplicate scale; an incomplete replacement is never silently replaced by older success.
- Raw results: `/Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/complex-formal-v2-20261025`.
