# Four-engine baseline comparison and crossover search

This report compares Polars CPU, Polars GPU, CPU Spark, GPU Spark. Polars GPU uses the cuDF engine for Polars; GPU Spark uses NVIDIA's Spark plugin. Their crossover findings are evaluated separately against Polars CPU.

No overall GPU Spark crossover over CPU Polars was observed across the complete, validated scales: sf1, sf10, sf30, sf100, sf300.

Polars GPU has a lower sum of six query medians than CPU Polars at sf10, sf30, sf100, sf300.

The aggregate is the sum of the six per-query medians, not a directly measured complete-suite runtime. GPU Spark / Polars CPU is GPU Spark elapsed time divided by Polars CPU elapsed time: below 1 favors GPU Spark. Every included scale passes the original strict output and final GPU-plan validator for all six queries and all saved trials.

Ratios involving Polars GPU (`Polars CPU / Polars GPU`, `Polars GPU / GPU Spark`) divide the first engine's time by the second's: above 1 favors the GPU engine in the denominator. Polars GPU is validated in strict mode (`raise_on_fail=True`); its failures, invalid evidence and output mismatches are reported per cell and never enter a sum, ratio or bracket.

| Scale | Trials/query | Polars CPU sum (s) | Polars GPU sum (s) | CPU Spark sum (s) | GPU Spark sum (s) | GPU Spark / Polars CPU | CPU Spark / GPU Spark | Polars CPU / Polars GPU | Polars GPU / GPU Spark |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| sf1 | 3 | 0.190 | 0.450 | 2.041 | 3.019 | 15.890x | 0.676x | 0.422x | 0.149x |
| sf10 | 3 | 1.253 | 0.845 | 7.873 | 4.811 | 3.839x | 1.636x | 1.483x | 0.176x |
| sf30 | 3 | 4.076 | 1.585 | 19.034 | 7.943 | 1.949x | 2.396x | 2.571x | 0.200x |
| sf100 | 3 | 13.466 | 4.064 | 53.035 | 16.789 | 1.247x | 3.159x | 3.314x | 0.242x |
| sf300 | 3 | 44.512 | 12.774 | 146.428 | 45.528 | 1.023x | 3.216x | 3.485x | 0.281x |

## GPU Spark against Polars CPU brackets

Brackets use adjacent complete, validated observations in numeric scale order. They locate an observed change of winner, not a fitted threshold, and do not imply monotonic performance between or beyond the measurements. Excluded scales inside a bracket provide no crossover evidence.

Overall: no observed change from Polars CPU winning to GPU Spark winning, or back, across the validated observations.

| Query | Observed scales where GPU Spark is faster than Polars CPU | Observed winner-change brackets |
|---|---|---|
| q01 | sf10, sf30, sf100, sf300 | sf1–sf10: GPU Spark becomes faster |
| q03 | none | none observed |
| q06 | none | none observed |
| q10 | none | none observed |
| q12 | none | none observed |
| q14 | none | none observed |

## Polars GPU against Polars CPU brackets

Same bracket rule using Polars GPU time / Polars CPU time at scales where Polars GPU has a complete aggregate (per query: scales where it ran). A scale or query where Polars GPU failed contributes no observation and cannot be a bracket endpoint.

Overall: sf1–sf10: Polars GPU becomes faster

| Query | Observed scales where Polars GPU is faster than Polars CPU | Observed winner-change brackets | Scales where Polars GPU did not run |
|---|---|---|---|
| q01 | sf10, sf30, sf100, sf300 | sf1–sf10: Polars GPU becomes faster | none |
| q03 | sf10, sf30, sf100, sf300 | sf1–sf10: Polars GPU becomes faster | none |
| q06 | sf100, sf300 | sf30–sf100: Polars GPU becomes faster | none |
| q10 | sf10, sf30, sf100, sf300 | sf1–sf10: Polars GPU becomes faster | none |
| q12 | sf10, sf30, sf100, sf300 | sf1–sf10: Polars GPU becomes faster | none |
| q14 | sf30, sf100, sf300 | sf10–sf30: Polars GPU becomes faster | none |

## Per-query measured times

Median [minimum, maximum] seconds; ranges describe the saved trials.

| Scale / query | Polars CPU | Polars GPU | CPU Spark | GPU Spark | GPU Spark / Polars CPU | Polars CPU / Polars GPU | Polars GPU / GPU Spark |
|---|---:|---:|---:|---:|---:|---:|---:|
| sf1 / q01 | 0.056 [0.049, 0.061] | 0.060 [0.060, 0.061] | 0.246 [0.232, 0.331] | 0.384 [0.376, 0.394] | 6.831x | 0.936x | 0.156x |
| sf1 / q03 | 0.031 [0.031, 0.032] | 0.062 [0.061, 0.063] | 0.405 [0.395, 0.493] | 0.569 [0.556, 0.590] | 18.339x | 0.503x | 0.108x |
| sf1 / q06 | 0.011 [0.011, 0.012] | 0.039 [0.039, 0.059] | 0.097 [0.094, 0.119] | 0.258 [0.256, 0.262] | 23.589x | 0.279x | 0.152x |
| sf1 / q10 | 0.058 [0.056, 0.063] | 0.167 [0.166, 0.168] | 0.699 [0.678, 1.007] | 0.953 [0.923, 1.075] | 16.501x | 0.346x | 0.175x |
| sf1 / q12 | 0.022 [0.021, 0.022] | 0.060 [0.059, 0.060] | 0.343 [0.332, 0.593] | 0.494 [0.491, 0.547] | 22.786x | 0.362x | 0.121x |
| sf1 / q14 | 0.012 [0.011, 0.013] | 0.062 [0.062, 0.063] | 0.251 [0.234, 0.277] | 0.360 [0.343, 0.364] | 29.284x | 0.198x | 0.172x |
| sf10 / q01 | 0.481 [0.444, 0.487] | 0.149 [0.138, 0.159] | 1.421 [1.191, 1.556] | 0.439 [0.425, 0.470] | 0.913x | 3.235x | 0.339x |
| sf10 / q03 | 0.243 [0.236, 0.249] | 0.153 [0.130, 0.158] | 2.579 [2.061, 2.816] | 1.073 [1.048, 1.148] | 4.414x | 1.586x | 0.143x |
| sf10 / q06 | 0.061 [0.060, 0.064] | 0.091 [0.083, 0.093] | 0.276 [0.273, 0.277] | 0.298 [0.285, 0.303] | 4.895x | 0.668x | 0.306x |
| sf10 / q10 | 0.236 [0.217, 0.244] | 0.212 [0.210, 0.238] | 1.699 [1.662, 1.759] | 1.685 [1.659, 1.759] | 7.133x | 1.115x | 0.126x |
| sf10 / q12 | 0.152 [0.150, 0.157] | 0.112 [0.111, 0.113] | 1.029 [1.024, 1.064] | 0.705 [0.688, 0.726] | 4.629x | 1.354x | 0.160x |
| sf10 / q14 | 0.080 [0.078, 0.089] | 0.128 [0.115, 0.132] | 0.869 [0.529, 0.918] | 0.612 [0.571, 0.619] | 7.661x | 0.625x | 0.209x |
| sf30 / q01 | 1.372 [1.354, 1.379] | 0.313 [0.302, 0.327] | 3.025 [3.024, 3.437] | 0.706 [0.685, 0.712] | 0.514x | 4.381x | 0.444x |
| sf30 / q03 | 1.118 [1.097, 1.142] | 0.271 [0.255, 0.275] | 6.114 [5.932, 6.281] | 1.926 [1.852, 1.972] | 1.723x | 4.117x | 0.141x |
| sf30 / q06 | 0.184 [0.184, 0.185] | 0.188 [0.185, 0.200] | 0.886 [0.600, 1.029] | 0.461 [0.445, 0.474] | 2.499x | 0.981x | 0.408x |
| sf30 / q10 | 0.697 [0.674, 0.697] | 0.363 [0.354, 0.372] | 4.801 [4.270, 4.847] | 2.796 [2.719, 2.937] | 4.014x | 1.919x | 0.130x |
| sf30 / q12 | 0.457 [0.445, 0.458] | 0.228 [0.207, 0.233] | 3.034 [3.018, 3.725] | 1.217 [1.206, 1.257] | 2.663x | 2.003x | 0.187x |
| sf30 / q14 | 0.248 [0.234, 0.257] | 0.222 [0.216, 0.222] | 1.174 [1.158, 1.195] | 0.837 [0.827, 0.897] | 3.376x | 1.119x | 0.265x |
| sf100 / q01 | 4.074 [4.051, 4.106] | 0.855 [0.827, 0.881] | 9.424 [9.309, 9.451] | 1.655 [1.625, 1.673] | 0.406x | 4.768x | 0.516x |
| sf100 / q03 | 3.887 [3.870, 3.899] | 0.718 [0.710, 0.732] | 15.929 [15.901, 16.073] | 4.332 [4.279, 4.462] | 1.114x | 5.413x | 0.166x |
| sf100 / q06 | 0.712 [0.623, 0.751] | 0.548 [0.544, 0.552] | 1.591 [1.587, 1.627] | 1.160 [1.131, 1.167] | 1.630x | 1.298x | 0.473x |
| sf100 / q10 | 2.278 [2.257, 2.956] | 0.804 [0.799, 0.805] | 13.238 [12.703, 13.895] | 5.182 [5.039, 5.453] | 2.275x | 2.832x | 0.155x |
| sf100 / q12 | 1.670 [1.598, 1.802] | 0.531 [0.522, 0.540] | 8.522 [8.446, 9.102] | 2.636 [2.594, 2.651] | 1.578x | 3.142x | 0.202x |
| sf100 / q14 | 0.845 [0.838, 0.860] | 0.607 [0.602, 0.616] | 4.331 [3.458, 4.623] | 1.823 [1.789, 1.833] | 2.157x | 1.393x | 0.333x |
| sf300 / q01 | 11.858 [11.842, 12.418] | 2.435 [2.429, 2.504] | 27.316 [26.857, 27.467] | 4.451 [4.405, 4.709] | 0.375x | 4.869x | 0.547x |
| sf300 / q03 | 12.622 [11.902, 14.138] | 2.863 [2.860, 2.871] | 47.018 [46.418, 47.230] | 13.290 [11.776, 14.320] | 1.053x | 4.408x | 0.215x |
| sf300 / q06 | 3.176 [1.846, 3.617] | 1.597 [1.578, 1.619] | 4.540 [4.508, 4.585] | 3.313 [3.090, 3.566] | 1.043x | 1.989x | 0.482x |
| sf300 / q10 | 9.286 [9.176, 11.370] | 2.695 [2.665, 2.719] | 35.053 [34.116, 35.137] | 12.652 [12.567, 14.066] | 1.362x | 3.446x | 0.213x |
| sf300 / q12 | 4.851 [4.809, 6.057] | 1.429 [1.410, 1.438] | 22.473 [22.152, 22.699] | 7.359 [7.033, 7.397] | 1.517x | 3.395x | 0.194x |
| sf300 / q14 | 2.719 [2.666, 2.734] | 1.754 [1.724, 1.756] | 10.028 [9.997, 10.495] | 4.463 [4.370, 5.493] | 1.641x | 1.550x | 0.393x |

## Pairwise speedup matrices

Each cell is the row engine's sum of per-query medians divided by the column engine's: above 1, the column engine is faster. A cell is `n/a` when either engine lacks a timing for at least one query at that scale (a failed, invalid or mismatching query): a sum over only the surviving queries is not a suite total and is never used. Per-query pairs, which exist wherever both engines have a timing, are in `pairwise_speedups.csv`.

### sf1

| Row / column | Polars | Polars GPU | CPU Spark | GPU Spark |
|---|---:|---:|---:|---:|
| Polars | — | 0.42x | 0.09x | 0.06x |
| Polars GPU | 2.37x | — | 0.22x | 0.15x |
| CPU Spark | 10.74x | 4.54x | — | 0.68x |
| GPU Spark | 15.89x | 6.71x | 1.48x | — |

### sf10

| Row / column | Polars | Polars GPU | CPU Spark | GPU Spark |
|---|---:|---:|---:|---:|
| Polars | — | 1.48x | 0.16x | 0.26x |
| Polars GPU | 0.67x | — | 0.11x | 0.18x |
| CPU Spark | 6.28x | 9.32x | — | 1.64x |
| GPU Spark | 3.84x | 5.69x | 0.61x | — |

### sf30

| Row / column | Polars | Polars GPU | CPU Spark | GPU Spark |
|---|---:|---:|---:|---:|
| Polars | — | 2.57x | 0.21x | 0.51x |
| Polars GPU | 0.39x | — | 0.08x | 0.20x |
| CPU Spark | 4.67x | 12.01x | — | 2.40x |
| GPU Spark | 1.95x | 5.01x | 0.42x | — |

### sf100

| Row / column | Polars | Polars GPU | CPU Spark | GPU Spark |
|---|---:|---:|---:|---:|
| Polars | — | 3.31x | 0.25x | 0.80x |
| Polars GPU | 0.30x | — | 0.08x | 0.24x |
| CPU Spark | 3.94x | 13.05x | — | 3.16x |
| GPU Spark | 1.25x | 4.13x | 0.32x | — |

### sf300

| Row / column | Polars | Polars GPU | CPU Spark | GPU Spark |
|---|---:|---:|---:|---:|
| Polars | — | 3.48x | 0.30x | 0.98x |
| Polars GPU | 0.29x | — | 0.09x | 0.28x |
| CPU Spark | 3.29x | 11.46x | — | 3.22x |
| GPU Spark | 1.02x | 3.56x | 0.31x | — |

## Polars GPU numeric agreement with Polars CPU

Every saved Polars GPU output is compared with the Polars CPU warmup at relative 1e-08, absolute 1e-06, exact integers. The tolerance is not loosened for the GPU engine; the largest observed relative difference per query is reported as a finding, including when it is within tolerance. Values are maxima over all numeric cells and all saved outputs of the query.

| Scale / query | Outputs compared | Outputs outside tolerance | Max relative difference | Max absolute difference |
|---|---:|---:|---:|---:|
| sf1 / q01 | 4 | 0 | 7.996e-13 | 4.883e-04 |
| sf1 / q03 | 4 | 0 | 2.869e-16 | 1.164e-10 |
| sf1 / q06 | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf1 / q10 | 4 | 0 | 3.675e-16 | 2.328e-10 |
| sf1 / q12 | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf1 / q14 | 4 | 0 | 2.169e-16 | 3.553e-15 |
| sf10 / q01 | 4 | 0 | 3.230e-12 | 1.025e-02 |
| sf10 / q03 | 4 | 0 | 1.369e-16 | 5.821e-11 |
| sf10 / q06 | 4 | 0 | 1.938e-16 | 2.384e-07 |
| sf10 / q10 | 4 | 0 | 3.156e-16 | 2.328e-10 |
| sf10 / q12 | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf10 / q14 | 4 | 0 | 2.134e-16 | 3.553e-15 |
| sf30 / q01 | 4 | 0 | 4.148e-12 | 3.467e-02 |
| sf30 / q03 | 4 | 0 | 1.328e-16 | 5.821e-11 |
| sf30 / q06 | 4 | 0 | 1.290e-16 | 4.768e-07 |
| sf30 / q10 | 4 | 0 | 2.695e-16 | 2.328e-10 |
| sf30 / q12 | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf30 / q14 | 4 | 0 | 4.270e-16 | 7.105e-15 |
| sf100 / q01 | 4 | 0 | 4.029e-11 | 7.715e-02 |
| sf100 / q03 | 4 | 0 | 1.258e-16 | 5.821e-11 |
| sf100 / q06 | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf100 / q10 | 4 | 0 | 3.078e-16 | 2.328e-10 |
| sf100 / q12 | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf100 / q14 | 4 | 0 | 4.270e-16 | 7.105e-15 |
| sf300 / q01 | 4 | 0 | 3.888e-10 | 8.320e-01 |
| sf300 / q03 | 4 | 0 | 1.247e-16 | 5.821e-11 |
| sf300 / q06 | 4 | 0 | 2.063e-16 | 7.629e-06 |
| sf300 / q10 | 4 | 0 | 3.166e-16 | 2.328e-10 |
| sf300 / q12 | 4 | 0 | 0.000e+00 | 0.000e+00 |
| sf300 / q14 | 4 | 0 | 0.000e+00 | 0.000e+00 |

## Peak GPU memory

`peak_gpu_memory_bytes` is the worker's per-process-tree device-memory peak sampled during the timed action; the table shows the maximum over measured trials, in GiB. Empty means sampling was unavailable. It is a sampled peak, not an allocator-level trace.

| Scale / query | Polars GPU | GPU Spark |
|---|---:|---:|
| sf1 / q01 | 2.09 | 78.59 |
| sf1 / q03 | 2.03 | 78.59 |
| sf1 / q06 | 2.03 | 78.59 |
| sf1 / q10 | 2.03 | 78.59 |
| sf1 / q12 | 2.09 | 78.59 |
| sf1 / q14 | 2.09 | 78.59 |
| sf10 / q01 | 9.06 | 78.59 |
| sf10 / q03 | 9.06 | 78.59 |
| sf10 / q06 | 9.06 | 78.59 |
| sf10 / q10 | 9.06 | 78.59 |
| sf10 / q12 | 9.06 | 78.59 |
| sf10 / q14 | 9.06 | 78.59 |
| sf30 / q01 | 11.37 | 78.58 |
| sf30 / q03 | 11.37 | 78.58 |
| sf30 / q06 | 11.37 | 78.58 |
| sf30 / q10 | 11.37 | 78.58 |
| sf30 / q12 | 11.37 | 78.58 |
| sf30 / q14 | 11.37 | 78.58 |
| sf100 / q01 | 18.16 | 78.58 |
| sf100 / q03 | 12.66 | 78.58 |
| sf100 / q06 | 12.66 | 78.58 |
| sf100 / q10 | 12.66 | 78.58 |
| sf100 / q12 | 18.16 | 78.58 |
| sf100 / q14 | 18.16 | 78.58 |
| sf300 / q01 | 29.63 | 78.58 |
| sf300 / q03 | 29.32 | 78.58 |
| sf300 / q06 | 29.32 | 78.58 |
| sf300 / q10 | 29.32 | 78.58 |
| sf300 / q12 | 29.63 | 78.58 |
| sf300 / q14 | 29.63 | 78.58 |


## Startup and observed process time

Query timing excludes engine startup, table setup, warmup and plan serialization. Launch-to-ready includes session/table setup; do not add these overlapping fields. Process wall time includes startup, all warmups and measured trials, serialization and shutdown; it is not a single six-query latency measurement.

| Scale / engine | Launch → ready (s) | Session + table setup (s) | Warmup sum (s) | Process wall (s) |
|---|---:|---:|---:|---:|
| sf1 / polars | 0.136 | 0.074 | 0.221 | 1.122 |
| sf1 / polars_gpu | 1.451 | 1.389 | 0.696 | 4.473 |
| sf1 / spark_cpu | 4.662 | 3.473 | 8.332 | 21.625 |
| sf1 / spark_gpu | 18.001 | 16.661 | 6.223 | 37.093 |
| sf10 / polars | 0.137 | 0.074 | 1.375 | 5.727 |
| sf10 / polars_gpu | 1.442 | 1.379 | 1.062 | 6.176 |
| sf10 / spark_cpu | 4.603 | 3.414 | 15.872 | 46.273 |
| sf10 / spark_gpu | 18.036 | 16.707 | 8.715 | 44.671 |
| sf30 / polars | 0.135 | 0.073 | 4.147 | 17.141 |
| sf30 / polars_gpu | 1.441 | 1.378 | 1.781 | 9.029 |
| sf30 / spark_cpu | 4.607 | 3.421 | 27.845 | 93.958 |
| sf30 / spark_gpu | 18.024 | 16.680 | 12.174 | 58.265 |
| sf100 / polars | 0.135 | 0.072 | 13.844 | 55.703 |
| sf100 / polars_gpu | 1.462 | 1.399 | 4.309 | 19.001 |
| sf100 / spark_cpu | 4.654 | 3.428 | 63.406 | 237.143 |
| sf100 / spark_gpu | 18.326 | 16.980 | 21.323 | 96.264 |
| sf300 / polars | 0.136 | 0.072 | 46.839 | 184.673 |
| sf300 / polars_gpu | 1.590 | 1.526 | 13.172 | 54.161 |
| sf300 / spark_cpu | 4.689 | 3.452 | 156.202 | 615.346 |
| sf300 / spark_gpu | 18.082 | 16.754 | 48.458 | 223.502 |

## Engine versions and configuration

| Scale / engine | Version | Configuration |
|---|---|---|
| sf1 / polars | 1.44.2 | — |
| sf1 / polars_gpu | 1.44.2 | executor=streaming; cudf-polars 26.10.02; device 0; raise_on_fail=True |
| sf1 / spark_cpu | 3.5.9 | — |
| sf1 / spark_gpu | 3.5.9 | profile=a100-parity |
| sf10 / polars | 1.44.2 | — |
| sf10 / polars_gpu | 1.44.2 | executor=streaming; cudf-polars 26.10.02; device 0; raise_on_fail=True |
| sf10 / spark_cpu | 3.5.9 | — |
| sf10 / spark_gpu | 3.5.9 | profile=a100-parity |
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

## Dataset and raw evidence

Stored Parquet counts all generated tables, not uncompressed bytes or projected per-query inputs. Raw roots retain manifests, configuration, plans, logs, event logs and telemetry. Later supplied roots replace earlier roots for duplicate numeric scales; an incomplete replacement is excluded rather than silently falling back to an earlier run.

| Scale | Stored Parquet (GiB) | Lineitem rows | Orders rows | Selected raw root |
|---|---:|---:|---:|---|
| sf1 | 0.303 | 6,001,215 | 1,500,000 | [/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline](</Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline>) |
| sf10 | 3.206 | 59,986,052 | 15,000,000 | [/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline](</Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline>) |
| sf30 | 9.843 | 179,998,372 | 45,000,000 | [/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline](</Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline>) |
| sf100 | 33.204 | 600,037,902 | 150,000,000 | [/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline](</Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline>) |
| sf300 | 100.563 | 1,799,989,091 | 450,000,000 | [/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline](</Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline>) |

| Supplied raw root | Scales found | Schedule note |
|---|---|---|
| [/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline](</Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/evidence-h100/20261026/results/baseline>) | sf1, sf10, sf30, sf100, sf300 | readable |

## Failures and incomplete scales

None among the selected scales.

## Engine failures and integrity findings

No failed, invalid or mismatching engine results among the analyzed non-legacy engines.

## Interpretation and limits

- The experiment targets one single-GPU VM per raw root: 1 x NVIDIA H100 80GB HBM3, 26 vCPU, 221.2 GiB. Each raw root's `environment.json` and engine configuration are the evidence for the actual host and allocation. This is local Spark, not a distributed scaling or equivalent-cost comparison. A recorded 96g Spark driver heap and the engines' use of host memory are different memory budgets; host RAM, JVM heap and GPU memory are separate limits.
- Files are read before each engine to preheat the filesystem, with no application DataFrame cache. Preheating cannot guarantee residency: compressed inputs, engine memory and OS memory compete for host RAM. Scales exceeding available cache introduce storage effects and cannot be interpreted as pure compute scaling. The same preheat policy does not establish an identical cache state.
- These are six selected TPC-H-derived queries with Float64/DOUBLE inputs and tolerance-checked parallel reductions, not a complete or certified TPC-H result. Correctness is relative to the saved Polars warmup baseline at each included scale.
- One warmup per query and three measured trials provide descriptive medians and ranges, not strong tail-latency or statistical claims. A one-trial smoke session, if present, has no repeatability estimate. Fresh process sessions across different scales are not independent repeated experiments.
- Generation uses DuckDB's TPC-H generator (recorded version(s): 1.5.6). The larger-data generation workflow may export parallel dbgen children while retaining the same DuckDB 1.5.6 generator. Parallel export can change physical row/file ordering, partition counts, compression and cache behavior; manifests and their source-generation metadata remain authoritative. Equal scale factors do not imply byte-identical files across separate generations.
- Generation storage is separate from query working memory: a complete DuckDB database, Parquet exports and temporary spill can coexist. A scale that cannot be generated or executed within the host's disk/RAM/GPU limits does not establish that another engine is faster.
- Sampled process RSS can miss peaks or double-count shared pages; GPU-plan checks demonstrate listed GPU operators, not the fraction of total runtime on GPU. No crossover is extrapolated beyond the complete, validated measurements.
- Polars GPU runs the cuDF GPU engine with `raise_on_fail=True`: a query that cannot run fully on the GPU is a recorded failure, not a CPU timing. Timings include host-to-device and device-to-host transfer inside the collected action and exclude conversion to Python dictionaries; a failure at one scale (for example device-memory exhaustion) does not predict other scales.
