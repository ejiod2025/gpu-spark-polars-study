# Measured GPU Spark crossover search

No overall GPU Spark crossover over CPU Polars was observed across the complete, validated scales: sf1, sf10, sf30, sf100, sf300.

The aggregate is the sum of the six per-query medians, not a directly measured complete-suite runtime. GPU / Polars is GPU elapsed time divided by Polars elapsed time: below 1 favors GPU Spark. Every included scale passes the original strict output and final GPU-plan validator for all six queries and all saved trials.

| Scale | Trials/query | Polars sum (s) | CPU Spark sum (s) | GPU Spark sum (s) | GPU / Polars | CPU Spark / GPU |
|---|---:|---:|---:|---:|---:|---:|
| sf1 | 3 | 0.202 | 2.665 | 3.721 | 18.403x | 0.716x |
| sf10 | 3 | 1.106 | 9.212 | 6.202 | 5.607x | 1.485x |
| sf30 | 3 | 3.006 | 19.025 | 10.557 | 3.512x | 1.802x |
| sf100 | 3 | 9.258 | 47.736 | 23.432 | 2.531x | 2.037x |
| sf300 | 3 | 28.187 | 125.802 | 65.037 | 2.307x | 1.934x |

## Observed crossover brackets

Brackets use adjacent complete, validated observations in numeric scale order. They locate an observed change of winner, not a fitted threshold, and do not imply monotonic performance between or beyond the measurements. Excluded scales inside a bracket provide no crossover evidence.

Overall: no observed change from Polars winning to GPU winning, or back, across the validated observations.

| Query | Observed scales where GPU is faster than Polars | Observed winner-change brackets |
|---|---|---|
| q01 | sf30, sf100, sf300 | sf10–sf30: GPU becomes faster |
| q03 | none | none observed |
| q06 | none | none observed |
| q10 | none | none observed |
| q12 | none | none observed |
| q14 | none | none observed |

## Per-query measured times

Median [minimum, maximum] seconds; ranges describe the saved trials.

| Scale / query | Polars | CPU Spark | GPU Spark | GPU / Polars |
|---|---:|---:|---:|---:|
| sf1 / q01 | 0.053 [0.052, 0.054] | 0.268 [0.268, 0.490] | 0.467 [0.461, 0.489] | 8.882x |
| sf1 / q03 | 0.038 [0.038, 0.042] | 0.552 [0.474, 0.721] | 0.682 [0.675, 0.758] | 17.724x |
| sf1 / q06 | 0.011 [0.011, 0.012] | 0.093 [0.077, 0.228] | 0.324 [0.309, 0.328] | 29.718x |
| sf1 / q10 | 0.068 [0.064, 0.080] | 0.991 [0.962, 1.642] | 1.180 [1.147, 1.249] | 17.274x |
| sf1 / q12 | 0.019 [0.019, 0.021] | 0.503 [0.488, 0.753] | 0.618 [0.604, 0.653] | 32.157x |
| sf1 / q14 | 0.013 [0.012, 0.014] | 0.259 [0.258, 0.320] | 0.449 [0.435, 0.451] | 35.483x |
| sf10 / q01 | 0.452 [0.334, 0.464] | 1.501 [1.252, 1.954] | 0.557 [0.506, 0.573] | 1.232x |
| sf10 / q03 | 0.198 [0.196, 0.222] | 2.827 [2.213, 3.083] | 1.513 [1.479, 1.547] | 7.649x |
| sf10 / q06 | 0.056 [0.054, 0.058] | 0.218 [0.209, 0.496] | 0.349 [0.342, 0.387] | 6.261x |
| sf10 / q10 | 0.248 [0.245, 0.273] | 2.610 [2.097, 2.671] | 2.177 [2.121, 2.180] | 8.769x |
| sf10 / q12 | 0.076 [0.068, 0.084] | 1.239 [1.148, 1.514] | 0.906 [0.851, 0.914] | 11.976x |
| sf10 / q14 | 0.077 [0.068, 0.079] | 0.817 [0.694, 0.931] | 0.701 [0.691, 0.724] | 9.143x |
| sf30 / q01 | 1.182 [1.113, 1.184] | 2.641 [2.432, 3.323] | 0.963 [0.918, 1.010] | 0.814x |
| sf30 / q03 | 0.620 [0.605, 0.622] | 5.418 [5.331, 5.505] | 2.656 [2.652, 2.835] | 4.281x |
| sf30 / q06 | 0.161 [0.150, 0.181] | 0.459 [0.458, 0.490] | 0.621 [0.604, 0.655] | 3.859x |
| sf30 / q10 | 0.600 [0.581, 0.685] | 6.182 [4.232, 6.203] | 3.609 [3.544, 3.917] | 6.017x |
| sf30 / q12 | 0.214 [0.207, 0.216] | 2.519 [2.413, 2.737] | 1.490 [1.486, 1.678] | 6.965x |
| sf30 / q14 | 0.228 [0.206, 0.253] | 1.805 [1.104, 1.945] | 1.218 [1.210, 1.259] | 5.330x |
| sf100 / q01 | 3.131 [3.079, 3.147] | 9.141 [9.129, 9.933] | 2.377 [2.336, 2.422] | 0.759x |
| sf100 / q03 | 2.176 [2.174, 2.191] | 15.398 [14.882, 16.168] | 6.616 [6.610, 6.749] | 3.041x |
| sf100 / q06 | 0.562 [0.560, 0.628] | 1.371 [1.172, 1.469] | 1.619 [1.615, 1.651] | 2.881x |
| sf100 / q10 | 1.981 [1.967, 1.988] | 10.674 [10.529, 11.679] | 6.880 [6.688, 6.944] | 3.473x |
| sf100 / q12 | 0.682 [0.671, 0.735] | 7.991 [7.853, 9.445] | 3.368 [3.353, 3.593] | 4.939x |
| sf100 / q14 | 0.726 [0.694, 0.778] | 3.161 [2.853, 3.291] | 2.571 [2.497, 2.688] | 3.542x |
| sf300 / q01 | 8.601 [8.359, 8.623] | 27.090 [25.901, 27.417] | 6.415 [6.399, 6.420] | 0.746x |
| sf300 / q03 | 6.786 [6.675, 6.914] | 38.782 [37.851, 42.219] | 20.266 [19.600, 24.043] | 2.986x |
| sf300 / q06 | 1.719 [1.686, 1.761] | 3.684 [3.633, 4.465] | 4.407 [4.368, 4.445] | 2.565x |
| sf300 / q10 | 6.181 [6.141, 6.256] | 29.586 [29.041, 30.394] | 18.067 [17.028, 19.512] | 2.923x |
| sf300 / q12 | 2.319 [2.048, 2.537] | 18.425 [18.137, 20.566] | 9.618 [8.904, 9.896] | 4.148x |
| sf300 / q14 | 2.581 [2.571, 2.716] | 8.234 [8.014, 11.526] | 6.262 [6.157, 6.428] | 2.426x |

## Startup and observed process time

Query timing excludes engine startup, table setup, warmup and plan serialization. Launch-to-ready includes session/table setup; do not add these overlapping fields. Process wall time includes startup, all warmups and measured trials, serialization and shutdown; it is not a single six-query latency measurement.

| Scale / engine | Launch → ready (s) | Session + table setup (s) | Warmup sum (s) | Process wall (s) |
|---|---:|---:|---:|---:|
| sf1 / polars | 0.178 | 0.124 | 0.310 | 1.341 |
| sf1 / spark_cpu | 5.464 | 3.944 | 11.173 | 29.092 |
| sf1 / spark_gpu | 19.191 | 17.523 | 7.684 | 41.881 |
| sf10 / polars | 0.180 | 0.128 | 1.312 | 5.483 |
| sf10 / spark_cpu | 5.425 | 3.919 | 19.215 | 58.068 |
| sf10 / spark_gpu | 19.302 | 17.626 | 10.548 | 52.630 |
| sf30 / polars | 0.180 | 0.127 | 3.978 | 14.190 |
| sf30 / spark_cpu | 5.458 | 3.952 | 29.583 | 98.173 |
| sf30 / spark_gpu | 19.295 | 17.574 | 15.738 | 73.169 |
| sf100 / polars | 0.177 | 0.125 | 9.630 | 38.095 |
| sf100 / spark_cpu | 5.523 | 4.016 | 71.618 | 231.980 |
| sf100 / spark_gpu | 19.457 | 17.773 | 29.370 | 127.716 |
| sf300 / polars | 0.259 | 0.204 | 28.880 | 114.569 |
| sf300 / spark_cpu | 5.548 | 4.024 | 151.178 | 567.150 |
| sf300 / spark_gpu | 19.724 | 18.038 | 69.023 | 317.894 |

## Dataset and raw evidence

Stored Parquet counts all generated tables, not uncompressed bytes or projected per-query inputs. Raw roots retain manifests, configuration, plans, logs, event logs and telemetry. Later supplied roots replace earlier roots for duplicate numeric scales; an incomplete replacement is excluded rather than silently falling back to an earlier run.

| Scale | Stored Parquet (GiB) | Lineitem rows | Orders rows | Selected raw root |
|---|---:|---:|---:|---|
| sf1 | 0.303 | 6,001,215 | 1,500,000 | [/Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/production-20261025](</Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/production-20261025>) |
| sf10 | 3.206 | 59,986,052 | 15,000,000 | [/Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/production-20261025](</Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/production-20261025>) |
| sf30 | 9.843 | 179,998,372 | 45,000,000 | [/Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/production-20261025](</Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/production-20261025>) |
| sf100 | 33.204 | 600,037,902 | 150,000,000 | [/Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/large-sf100-20261025](</Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/large-sf100-20261025>) |
| sf300 | 100.563 | 1,799,989,091 | 450,000,000 | [/Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/large-sf300-20261025](</Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/large-sf300-20261025>) |

| Supplied raw root | Scales found | Schedule note |
|---|---|---|
| [/Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/production-20261025](</Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/production-20261025>) | sf1, sf10, sf30 | readable |
| [/Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/large-sf100-20261025](</Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/large-sf100-20261025>) | sf100 | readable |
| [/Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/large-sf300-20261025](</Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results/large-sf300-20261025>) | sf300 | readable |

## Failures and incomplete scales

None among the selected scales.

## Interpretation and limits

- The experiment targets one A100 40 GB VM with 30 CPU threads and 216 GiB host RAM. Each raw root's `environment.json` and engine configuration are the evidence for the actual host and allocation. This is local Spark, not a distributed scaling or equivalent-cost comparison. A recorded 96 GiB Spark driver heap and Polars' use of host memory are different memory budgets; host RAM, JVM heap and GPU memory are separate limits.
- Files are read before each engine to preheat the filesystem, with no application DataFrame cache. Preheating cannot guarantee residency: compressed inputs, engine memory and OS memory compete for host RAM. Scales exceeding available cache introduce storage effects and cannot be interpreted as pure compute scaling. The same preheat policy does not establish an identical cache state.
- These are six selected TPC-H-derived queries with Float64/DOUBLE inputs and tolerance-checked parallel reductions, not a complete or certified TPC-H result. Correctness is relative to the saved Polars warmup baseline at each included scale.
- One warmup per query and three measured trials provide descriptive medians and ranges, not strong tail-latency or statistical claims. A one-trial smoke session, if present, has no repeatability estimate. Fresh process sessions across different scales are not independent repeated experiments.
- Generation uses DuckDB's TPC-H generator (recorded version(s): 1.5.6). The larger-data generation workflow may export parallel dbgen children while retaining the same DuckDB 1.5.6 generator. Parallel export can change physical row/file ordering, partition counts, compression and cache behavior; manifests and their source-generation metadata remain authoritative. Equal scale factors do not imply byte-identical files across separate generations.
- Generation storage is separate from query working memory: a complete DuckDB database, Parquet exports and temporary spill can coexist. A scale that cannot be generated or executed within the host's disk/RAM/GPU limits does not establish that another engine is faster.
- Sampled process RSS can miss peaks or double-count shared pages; GPU-plan checks demonstrate listed GPU operators, not the fraction of total runtime on GPU. No crossover is extrapolated beyond the complete, validated measurements.
