# Four-engine complex-query comparison

This separate suite tests six joins, nested-query, window, exact-distinct, and self-join cases. Its score is not added to the original six-query experiment. Engines: Polars CPU, Polars GPU, CPU Spark, GPU Spark. Polars GPU uses the cuDF engine for Polars; GPU Spark uses NVIDIA's Spark plugin. These are separate GPU engines.

Verified cases: **18**; excluded incomplete/invalid cases: **0**. Only validated engine cells carry timings. Failed, invalid or mismatching Polars GPU cells remain explicit without timings; each retained cell's saved warmup and measured outputs agree with its Polars warmup baseline (relative tolerance 1e-08, absolute 1e-06; integer pairs exact).

Times are per-query median [minimum, maximum] seconds. Each ratio divides the first named engine's time by the second's; above 1 favors the second engine. GPU Spark coverage is classified separately from strict Polars GPU execution.

Resource protocol is part of the result: `none` is the preserved legacy session method; `verified_gc` adds a verified post-query resource barrier outside query timers. Do not silently combine those methods into one uniform scaling series.

| Scale / query | Type | Resource protocol | Polars CPU | Polars GPU | CPU Spark | GPU Spark | Polars CPU / GPU Spark | CPU Spark / GPU Spark | Polars CPU / Polars GPU | Polars GPU / GPU Spark | GPU Spark coverage |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| sf30 / cx_q09 | Six-table join and composite join keys | verified_gc | 1.920 [1.893, 1.961] | 0.367 [0.359, 0.378] | 14.015 [13.398, 14.175] | 5.317 [2.484, 5.340] | 0.361x | 2.636x | 5.225x | 0.069x | gpu_operators_present |
| sf30 / cx_q13 | Left outer join and nested aggregation | verified_gc | 0.968 [0.951, 1.015] | FAILED: ComputeError: 'cuda' conversion failed: NotImplementedError: ('Quer... | 7.885 [6.715, 7.896] | 1.368 [1.336, 1.387] | 0.707x | 5.765x | n/a | n/a | gpu_operators_present |
| sf30 / cx_semi_anti | Correlated EXISTS/NOT EXISTS and semi/anti joins | verified_gc | 0.595 [0.587, 0.598] | 0.229 [0.219, 0.232] | 3.933 [3.895, 4.042] | 1.535 [1.510, 1.545] | 0.388x | 2.562x | 2.598x | 0.149x | gpu_operators_present |
| sf30 / cx_window | Two window orderings: LAG and ROW_NUMBER | verified_gc | 8.684 [8.598, 8.802] | FAILED: ComputeError: 'cuda' conversion failed: NotImplementedError: ("Quer... | 3.383 [3.056, 3.703] | 0.742 [0.723, 0.793] | 11.696x | 4.556x | n/a | n/a | gpu_operators_present |
| sf30 / cx_distinct | Four-table join and three exact distinct counts | verified_gc | 2.423 [2.363, 2.447] | 0.532 [0.522, 0.532] | 16.398 [15.661, 16.769] | 3.816 [3.741, 3.847] | 0.635x | 4.298x | 4.558x | 0.139x | gpu_operators_present |
| sf30 / cx_self_join | Fact self-join with an inequality condition | verified_gc | 0.702 [0.642, 0.703] | 0.211 [0.207, 0.215] | 4.156 [4.129, 4.227] | 1.013 [0.980, 1.045] | 0.692x | 4.101x | 3.325x | 0.208x | gpu_operators_present |
| sf100 / cx_q09 | Six-table join and composite join keys | verified_gc | 7.470 [7.260, 7.630] | 1.044 [1.016, 1.056] | 48.362 [47.788, 49.026] | 21.294 [21.029, 21.477] | 0.351x | 2.271x | 7.152x | 0.049x | gpu_operators_present |
| sf100 / cx_q13 | Left outer join and nested aggregation | verified_gc | 4.021 [3.958, 4.098] | FAILED: ComputeError: 'cuda' conversion failed: NotImplementedError: ('Quer... | 19.345 [18.855, 20.192] | 2.094 [1.982, 2.184] | 1.921x | 9.240x | n/a | n/a | gpu_operators_present |
| sf100 / cx_semi_anti | Correlated EXISTS/NOT EXISTS and semi/anti joins | verified_gc | 2.064 [1.995, 2.229] | 0.635 [0.625, 0.640] | 9.388 [9.184, 10.094] | 2.600 [2.592, 2.670] | 0.794x | 3.611x | 3.250x | 0.244x | gpu_operators_present |
| sf100 / cx_window | Two window orderings: LAG and ROW_NUMBER | verified_gc | 30.885 [30.841, 31.174] | FAILED: ComputeError: 'cuda' conversion failed: NotImplementedError: ("Quer... | 8.730 [8.608, 9.011] | 1.447 [1.387, 1.449] | 21.345x | 6.034x | n/a | n/a | gpu_operators_present |
| sf100 / cx_distinct | Four-table join and three exact distinct counts | verified_gc | 11.491 [11.233, 11.682] | 1.416 [1.396, 1.424] | 51.572 [51.256, 51.702] | 11.668 [11.610, 12.082] | 0.985x | 4.420x | 8.118x | 0.121x | gpu_operators_present |
| sf100 / cx_self_join | Fact self-join with an inequality condition | verified_gc | 2.508 [2.224, 2.577] | 0.705 [0.674, 0.722] | 10.099 [9.714, 10.288] | 2.159 [2.142, 2.166] | 1.162x | 4.679x | 3.557x | 0.327x | gpu_operators_present |
| sf300 / cx_q09 | Six-table join and composite join keys | verified_gc | 23.559 [23.406, 26.187] | 3.680 [3.602, 3.749] | 134.914 [134.786, 136.385] | 87.735 [87.386, 88.324] | 0.269x | 1.538x | 6.402x | 0.042x | gpu_operators_present |
| sf300 / cx_q13 | Left outer join and nested aggregation | verified_gc | 13.442 [13.337, 14.062] | FAILED: ComputeError: 'cuda' conversion failed: NotImplementedError: ('Quer... | 45.983 [45.590, 46.132] | 5.206 [5.201, 5.231] | 2.582x | 8.833x | n/a | n/a | gpu_operators_present |
| sf300 / cx_semi_anti | Correlated EXISTS/NOT EXISTS and semi/anti joins | verified_gc | 6.824 [6.507, 9.394] | 1.957 [1.933, 1.972] | 25.750 [25.310, 25.968] | 6.726 [6.600, 6.760] | 1.015x | 3.829x | 3.487x | 0.291x | gpu_operators_present |
| sf300 / cx_window | Two window orderings: LAG and ROW_NUMBER | verified_gc | 94.197 [93.866, 95.279] | FAILED: ComputeError: 'cuda' conversion failed: NotImplementedError: ("Quer... | 19.504 [19.231, 19.522] | 3.614 [3.502, 3.634] | 26.064x | 5.397x | n/a | n/a | gpu_operators_present |
| sf300 / cx_distinct | Four-table join and three exact distinct counts | verified_gc | 37.060 [36.378, 37.613] | 4.541 [4.519, 4.559] | 140.629 [140.351, 141.478] | 34.173 [34.086, 34.771] | 1.084x | 4.115x | 8.162x | 0.133x | gpu_operators_present |
| sf300 / cx_self_join | Fact self-join with an inequality condition | verified_gc | 9.363 [8.690, 9.988] | 2.902 [2.875, 2.925] | 24.364 [24.277, 25.397] | 5.586 [5.566, 5.589] | 1.676x | 4.362x | 3.227x | 0.519x | gpu_operators_present |

## Full-suite summaries

Each engine's sum of six per-query medians is shown only when all six of its cases at that scale pass validation. An engine's sum is `n/a` when any case failed, was invalid or mismatched; other engines retain their own complete scores. It is a derived workload score, not a measured once-through suite runtime.

| Scale | Trials/query | Resource protocol | Polars CPU sum (s) | Polars GPU sum (s) | CPU Spark sum (s) | GPU Spark sum (s) | Polars CPU / GPU Spark | CPU Spark / GPU Spark | Polars CPU / Polars GPU | Polars GPU / GPU Spark |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| sf30 | 3 | verified_gc | 15.291 | n/a (4/6 cases; cx_q13 failed, cx_window failed) | 49.770 | 13.791 | 1.109x | 3.609x | n/a | n/a |
| sf100 | 3 | verified_gc | 58.440 | n/a (4/6 cases; cx_q13 failed, cx_window failed) | 147.497 | 41.261 | 1.416x | 3.575x | n/a | n/a |
| sf300 | 3 | verified_gc | 184.446 | n/a (4/6 cases; cx_q13 failed, cx_window failed) | 391.143 | 143.039 | 1.289x | 2.735x | n/a | n/a |

Complete 4-engine suites: **0**.

## GPU Spark final-plan coverage

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

## Engine versions and configuration

| Scale / engine | Version | Configuration |
|---|---|---|
| sf30 / polars | 1.44.2 | — |
| sf30 / polars_gpu | 1.44.2 | executor=streaming; cudf-polars 26.10.02; device 0; raise_on_fail=True |
| sf30 / spark_cpu | 3.5.9 | — |
| sf30 / spark_gpu | 3.5.9 | profile=a100-parity |
| sf100 / polars | 1.44.2 | — |
| sf100 / polars_gpu | 1.44.2 | executor=streaming; cudf-polars 26.10.02; device 0; raise_on_fail=True |
| sf100 / spark_cpu | 3.5.9 | — |
| sf100 / spark_gpu | 3.5.9 | profile=a100-parity |
| sf300 / polars | 1.44.2 | — |
| sf300 / polars_gpu | 1.44.2 | executor=streaming; cudf-polars 26.10.02; device 0; raise_on_fail=True |
| sf300 / spark_cpu | 3.5.9 | — |
| sf300 / spark_gpu | 3.5.9 | profile=a100-parity |

## Startup and process scopes

Query timings exclude startup, table/view setup, warmups and plan serialization. Launch-to-ready includes session setup and overlaps that field. Process wall time also contains all warmups, measured queries, serialization, and shutdown. Under verified_gc it also includes the explicit resource barriers. Do not add overlapping fields or treat process wall time as one six-query pass.

| Scale / engine | Resource protocol | Launch → ready (s) | Session + table setup (s) | Saved warmup sum (s) | Explicit cleanup count / sum (s) | Process wall (s) |
|---|---|---:|---:|---:|---:|---:|
| sf30 / polars | verified_gc | 0.137 | 0.074 | 15.272 | 24 / 0.188 | 62.335 |
| sf30 / polars_gpu | verified_gc | 1.454 | 1.390 | 1.566 | 16 / 0.312 | 8.430 |
| sf30 / spark_cpu | verified_gc | 4.774 | 3.567 | 56.666 | 24 / 61.358 | 271.611 |
| sf30 / spark_gpu | verified_gc | 18.270 | 16.938 | 14.962 | 24 / 61.848 | 137.743 |
| sf100 / polars | verified_gc | 0.138 | 0.075 | 58.789 | 24 / 0.186 | 236.120 |
| sf100 / polars_gpu | verified_gc | 1.442 | 1.379 | 4.153 | 16 / 0.313 | 18.349 |
| sf100 / spark_cpu | verified_gc | 4.862 | 3.579 | 155.635 | 24 / 73.206 | 679.899 |
| sf100 / spark_gpu | verified_gc | 18.210 | 16.888 | 47.312 | 24 / 74.058 | 267.617 |
| sf300 / polars | verified_gc | 0.137 | 0.073 | 188.151 | 24 / 0.196 | 749.908 |
| sf300 / polars_gpu | verified_gc | 1.475 | 1.411 | 13.531 | 16 / 0.315 | 55.663 |
| sf300 / spark_cpu | verified_gc | 5.725 | 4.062 | 395.265 | 24 / 112.567 | 1692.233 |
| sf300 / spark_gpu | verified_gc | 19.446 | 17.768 | 152.998 | 24 / 110.062 | 717.042 |

## Post-query resource barriers

verified_gc barriers occur after each execute returns, outside query timers; explicit barrier duration is reported separately and remains inside process wall time. Legacy none sessions have no verified barrier measurement.

Under `verified_gc`, every saved warmup and measured output must have exactly one matching passed cleanup record. Polars receives Python garbage collection; Spark receives Python/JVM garbage collection followed by a wait for the owned shuffle-file count and bytes to reach zero. A request for garbage collection alone is insufficient. A global cleanup failure invalidates the engine session. The barrier occurs after query execution returns and changes the lifecycle of a long-lived session; it is a declared protocol change, not evidence that the legacy failed run succeeded. Legacy `none` sessions have no explicit verified-barrier measurement; that does not mean automatic cleanup took no time.

Per-case explicit cleanup below sums all saved barriers, including the warmup and measured trials. It is separate from the query medians and already lies inside process wall time.

| Scale / query | Engine | Passed barriers | Explicit cleanup sum (s) |
|---|---|---:|---:|
| sf30 / cx_q09 | polars | 4 | 0.031 |
| sf30 / cx_q09 | polars_gpu | 4 | 0.079 |
| sf30 / cx_q09 | spark_cpu | 4 | 12.012 |
| sf30 / cx_q09 | spark_gpu | 4 | 10.897 |
| sf30 / cx_q13 | polars | 4 | 0.031 |
| sf30 / cx_q13 | polars_gpu | n/a | n/a |
| sf30 / cx_q13 | spark_cpu | 4 | 9.645 |
| sf30 / cx_q13 | spark_gpu | 4 | 10.107 |
| sf30 / cx_semi_anti | polars | 4 | 0.031 |
| sf30 / cx_semi_anti | polars_gpu | 4 | 0.078 |
| sf30 / cx_semi_anti | spark_cpu | 4 | 9.682 |
| sf30 / cx_semi_anti | spark_gpu | 4 | 9.972 |
| sf30 / cx_window | polars | 4 | 0.031 |
| sf30 / cx_window | polars_gpu | n/a | n/a |
| sf30 / cx_window | spark_cpu | 4 | 9.620 |
| sf30 / cx_window | spark_gpu | 4 | 9.968 |
| sf30 / cx_distinct | polars | 4 | 0.035 |
| sf30 / cx_distinct | polars_gpu | 4 | 0.078 |
| sf30 / cx_distinct | spark_cpu | 4 | 10.727 |
| sf30 / cx_distinct | spark_gpu | 4 | 11.010 |
| sf30 / cx_self_join | polars | 4 | 0.031 |
| sf30 / cx_self_join | polars_gpu | 4 | 0.077 |
| sf30 / cx_self_join | spark_cpu | 4 | 9.672 |
| sf30 / cx_self_join | spark_gpu | 4 | 9.895 |
| sf100 / cx_q09 | polars | 4 | 0.032 |
| sf100 / cx_q09 | polars_gpu | 4 | 0.079 |
| sf100 / cx_q09 | spark_cpu | 4 | 18.774 |
| sf100 / cx_q09 | spark_gpu | 4 | 19.396 |
| sf100 / cx_q13 | polars | 4 | 0.031 |
| sf100 / cx_q13 | polars_gpu | n/a | n/a |
| sf100 / cx_q13 | spark_cpu | 4 | 9.754 |
| sf100 / cx_q13 | spark_gpu | 4 | 9.780 |
| sf100 / cx_semi_anti | polars | 4 | 0.031 |
| sf100 / cx_semi_anti | polars_gpu | 4 | 0.078 |
| sf100 / cx_semi_anti | spark_cpu | 4 | 9.780 |
| sf100 / cx_semi_anti | spark_gpu | 4 | 9.880 |
| sf100 / cx_window | polars | 4 | 0.030 |
| sf100 / cx_window | polars_gpu | n/a | n/a |
| sf100 / cx_window | spark_cpu | 4 | 10.670 |
| sf100 / cx_window | spark_gpu | 4 | 10.899 |
| sf100 / cx_distinct | polars | 4 | 0.030 |
| sf100 / cx_distinct | polars_gpu | 4 | 0.078 |
| sf100 / cx_distinct | spark_cpu | 4 | 14.456 |
| sf100 / cx_distinct | spark_gpu | 4 | 14.298 |
| sf100 / cx_self_join | polars | 4 | 0.032 |
| sf100 / cx_self_join | polars_gpu | 4 | 0.078 |
| sf100 / cx_self_join | spark_cpu | 4 | 9.773 |
| sf100 / cx_self_join | spark_gpu | 4 | 9.805 |
| sf300 / cx_q09 | polars | 4 | 0.032 |
| sf300 / cx_q09 | polars_gpu | 4 | 0.079 |
| sf300 / cx_q09 | spark_cpu | 4 | 39.916 |
| sf300 / cx_q09 | spark_gpu | 4 | 37.579 |
| sf300 / cx_q13 | polars | 4 | 0.031 |
| sf300 / cx_q13 | polars_gpu | n/a | n/a |
| sf300 / cx_q13 | spark_cpu | 4 | 11.747 |
| sf300 / cx_q13 | spark_gpu | 4 | 11.962 |
| sf300 / cx_semi_anti | polars | 4 | 0.031 |
| sf300 / cx_semi_anti | polars_gpu | 4 | 0.078 |
| sf300 / cx_semi_anti | spark_cpu | 4 | 9.896 |
| sf300 / cx_semi_anti | spark_gpu | 4 | 10.802 |
| sf300 / cx_window | polars | 4 | 0.031 |
| sf300 / cx_window | polars_gpu | n/a | n/a |
| sf300 / cx_window | spark_cpu | 4 | 12.693 |
| sf300 / cx_window | spark_gpu | 4 | 12.916 |
| sf300 / cx_distinct | polars | 4 | 0.032 |
| sf300 / cx_distinct | polars_gpu | 4 | 0.079 |
| sf300 / cx_distinct | spark_cpu | 4 | 26.451 |
| sf300 / cx_distinct | spark_gpu | 4 | 24.665 |
| sf300 / cx_self_join | polars | 4 | 0.039 |
| sf300 / cx_self_join | polars_gpu | 4 | 0.078 |
| sf300 / cx_self_join | spark_cpu | 4 | 11.863 |
| sf300 / cx_self_join | spark_gpu | 4 | 12.137 |

## Exclusions and errors

No selected cases were excluded.

## Pairwise speedup matrices

Each cell is the row engine's sum of per-query medians divided by the column engine's: above 1, the column engine is faster. A cell is `n/a` when either engine lacks a timing for at least one query at that scale (a failed, invalid or mismatching query): a sum over only the surviving queries is not a suite total and is never used. Per-query pairs, which exist wherever both engines have a timing, are in `pairwise_speedups.csv`.

### sf30

| Row / column | Polars | Polars GPU | CPU Spark | GPU Spark |
|---|---:|---:|---:|---:|
| Polars | — | n/a | 0.31x | 1.11x |
| Polars GPU | n/a | — | n/a | n/a |
| CPU Spark | 3.25x | n/a | — | 3.61x |
| GPU Spark | 0.90x | n/a | 0.28x | — |

Engines without a complete suite at this scale: Polars GPU: 4/6 queries; cx_q13 failed, cx_window failed.

### sf100

| Row / column | Polars | Polars GPU | CPU Spark | GPU Spark |
|---|---:|---:|---:|---:|
| Polars | — | n/a | 0.40x | 1.42x |
| Polars GPU | n/a | — | n/a | n/a |
| CPU Spark | 2.52x | n/a | — | 3.57x |
| GPU Spark | 0.71x | n/a | 0.28x | — |

Engines without a complete suite at this scale: Polars GPU: 4/6 queries; cx_q13 failed, cx_window failed.

### sf300

| Row / column | Polars | Polars GPU | CPU Spark | GPU Spark |
|---|---:|---:|---:|---:|
| Polars | — | n/a | 0.47x | 1.29x |
| Polars GPU | n/a | — | n/a | n/a |
| CPU Spark | 2.12x | n/a | — | 2.73x |
| GPU Spark | 0.78x | n/a | 0.37x | — |

Engines without a complete suite at this scale: Polars GPU: 4/6 queries; cx_q13 failed, cx_window failed.

## Polars GPU numeric agreement with Polars CPU

Every saved Polars GPU output is compared with the Polars CPU warmup at relative 1e-08, absolute 1e-06, exact integers. The tolerance is not loosened for the GPU engine; the largest observed relative difference per query is reported as a finding, including when it is within tolerance. Values are maxima over all numeric cells and all saved outputs of the query.

| Scale / query | Outputs compared | Outputs outside tolerance | Max relative difference | Max absolute difference |
|---|---:|---:|---:|---:|
| sf30 / cx_q09 | 4 | 0 | 1.845e-14 | 2.575e-05 |
| sf30 / cx_semi_anti | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf30 / cx_distinct | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf30 / cx_self_join | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf100 / cx_q09 | 4 | 0 | 2.780e-14 | 1.307e-04 |
| sf100 / cx_semi_anti | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf100 / cx_distinct | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf100 / cx_self_join | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf300 / cx_q09 | 4 | 0 | 1.433e-14 | 2.022e-04 |
| sf300 / cx_semi_anti | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf300 / cx_distinct | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf300 / cx_self_join | 4 | 0 | 0.000e+00 | 0.000e+00 |

## Peak GPU memory

`peak_gpu_memory_bytes` is the worker's per-process-tree device-memory peak sampled during the timed action; the table shows the maximum over measured trials, in GiB. Empty means sampling was unavailable. It is a sampled peak, not an allocator-level trace.

| Scale / query | Polars GPU | GPU Spark |
|---|---:|---:|
| sf30 / cx_q09 | 13.40 | 78.59 |
| sf30 / cx_q13 | n/a | 78.59 |
| sf30 / cx_semi_anti | 13.40 | 78.59 |
| sf30 / cx_window | n/a | 78.59 |
| sf30 / cx_distinct | 13.40 | 78.59 |
| sf30 / cx_self_join | 13.40 | 78.59 |
| sf100 / cx_q09 | 20.75 | 78.58 |
| sf100 / cx_q13 | n/a | 78.58 |
| sf100 / cx_semi_anti | 20.66 | 78.58 |
| sf100 / cx_window | n/a | 78.58 |
| sf100 / cx_distinct | 20.78 | 78.58 |
| sf100 / cx_self_join | 20.78 | 78.58 |
| sf300 / cx_q09 | 42.34 | 78.58 |
| sf300 / cx_q13 | n/a | 78.58 |
| sf300 / cx_semi_anti | 42.34 | 78.58 |
| sf300 / cx_window | n/a | 78.58 |
| sf300 / cx_distinct | 42.34 | 78.58 |
| sf300 / cx_self_join | 42.34 | 78.58 |

## Engine failures and integrity findings

`failed`: no usable run (error recorded by the engine, incomplete or failed session; for Polars GPU in strict mode, a query that cannot run fully on the GPU). `invalid`: integrity violation (strict-GPU metadata missing or inconsistent). `mismatch`: output differs from the Polars CPU baseline beyond tolerance. None of these cells carries a timing, and none enters a sum or ratio.

| Scale | Engine | Query | Kind | Reason |
|---|---|---|---|---|
| sf30 | polars_gpu | cx_q13 | failed | ComputeError: 'cuda' conversion failed: NotImplementedError: ('Query execution with GPU not possible: unsupported operations.\nThe errors were:\n- NotImplementedError: Unsupported regex (?s)special.*requests for GPU engine.', [NotImplementedError('Unsupported regex (?s)special.*requests for GPU engine.')]) |
| sf30 | polars_gpu | cx_window | failed | ComputeError: 'cuda' conversion failed: NotImplementedError: ("Query execution with GPU not possible: unsupported operations.\nThe errors were:\n- NotImplementedError: No support for <class 'cudf_polars.dsl.expressions.unary.UnaryFunction'> in groupby/rolling", [NotImplementedError("No support for <class 'cudf_polars.dsl.expressions.unary.UnaryFunction'> in groupby/rolling")]) |
| sf100 | polars_gpu | cx_q13 | failed | ComputeError: 'cuda' conversion failed: NotImplementedError: ('Query execution with GPU not possible: unsupported operations.\nThe errors were:\n- NotImplementedError: Unsupported regex (?s)special.*requests for GPU engine.', [NotImplementedError('Unsupported regex (?s)special.*requests for GPU engine.')]) |
| sf100 | polars_gpu | cx_window | failed | ComputeError: 'cuda' conversion failed: NotImplementedError: ("Query execution with GPU not possible: unsupported operations.\nThe errors were:\n- NotImplementedError: No support for <class 'cudf_polars.dsl.expressions.unary.UnaryFunction'> in groupby/rolling", [NotImplementedError("No support for <class 'cudf_polars.dsl.expressions.unary.UnaryFunction'> in groupby/rolling")]) |
| sf300 | polars_gpu | cx_q13 | failed | ComputeError: 'cuda' conversion failed: NotImplementedError: ('Query execution with GPU not possible: unsupported operations.\nThe errors were:\n- NotImplementedError: Unsupported regex (?s)special.*requests for GPU engine.', [NotImplementedError('Unsupported regex (?s)special.*requests for GPU engine.')]) |
| sf300 | polars_gpu | cx_window | failed | ComputeError: 'cuda' conversion failed: NotImplementedError: ("Query execution with GPU not possible: unsupported operations.\nThe errors were:\n- NotImplementedError: No support for <class 'cudf_polars.dsl.expressions.unary.UnaryFunction'> in groupby/rolling", [NotImplementedError("No support for <class 'cudf_polars.dsl.expressions.unary.UnaryFunction'> in groupby/rolling")]) |

## Interpretation and reproducibility

- This is a fixed-configuration single-host experiment on the same shared Parquet inputs per scale. It does not establish distributed scaling, equivalent-cost performance, or a product-wide winner.
- Polars uses CPU streaming; both Spark modes share recorded Spark controls and CPU allocation. The GPU-plugin session adds NVIDIA's plugin and its GPU settings. Host RAM, the Spark heap, and GPU VRAM are different budgets.
- Filesystem preheating is attempted before each engine; no application DataFrame cache is used. Residency is not guaranteed, so these are not certified pure-compute or cold-storage measurements.
- One warmup plus three measured trials within one engine session is descriptive. It is not three independently restarted experiments and does not establish tail latency or statistical significance.
- Q9/Q13 are TPC-H-derived and the other cases are custom queries on the same derived tables; this is not a complete or certified TPC-H run. Float64/DOUBLE casts and deterministic output ordering are retained across engines.
- A failed or incomplete engine/case is excluded rather than assigned an infinite latency or counted as a GPU win. A full-suite score requires all six cases, even when only five succeed.
- Raw roots retain actual outputs, plans, configuration, manifests, logs, source hashes and telemetry. Later roots replace earlier roots for a duplicate scale; an incomplete replacement is never silently replaced by older success.
- Polars GPU runs the cuDF GPU engine with `raise_on_fail=True`. A case it cannot run fully on the GPU is a recorded failure for that engine only: the case stays in the tables for the other engines, the Polars GPU cell states the reason, and the case never enters a Polars GPU sum or ratio. Timings include device-to-host transfer of the collected result and exclude conversion to Python dictionaries, as for Polars CPU.
- Raw results: `/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/complex`.
