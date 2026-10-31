# Cross-host comparison report

Hosts (GPU-generation order): A100, H100, GH200, B200. Reference: **H100**. Schema 1.0; keys in `STATS_SCHEMA.md`. Design: `docs/H100_EXPERIMENT_DESIGN.md` amendments A2 and A3.

## 1. Host identity

| Host | GPU | Device GiB | CPU model | Arch | vCPU | --cpus | Threads / core (SMT) | Physical cores | NUMA nodes | Cores occupied by --cpus threads | RAM GiB | Driver | CUDA | RAPIDS jar | Rounds |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A100 | NVIDIA A100-SXM4-40GB | 40 | AMD EPYC 7J13 64-Core Processor | x86_64 | 30 | 26 | 1 | 30 | 1 | 26 | 216.3 | 570.148.08 | 12.8 | rapids-4-spark_2.12-26.08.1.jar | 4 |
| H100 (ref.) | NVIDIA H100 80GB HBM3 | 79.6 | Intel(R) Xeon(R) Platinum 8480+ | x86_64 | 26 | 26 | 2 | 13 | 1 | 13 | 221.2 | 580.105.08 | 13.0 | rapids-4-spark_2.12-26.08.1.jar | 4 |
| GH200 | NVIDIA GH200 480GB | 95.6 | Neoverse-V2 | aarch64 | 64 | 26 | 1 | 64 | 9 | 26 | 525.7 | 570.148.08 | 12.8 | rapids-4-spark_2.12-26.08.1-cuda12-arm64.jar | 4 |
| B200 | NVIDIA B200 | 179.1 | INTEL(R) XEON(R) PLATINUM 8592+ | x86_64 | 26 | 26 | 2 | 13 | 1 | 13 | 354.1 | 570.148.08 | 12.8 | rapids-4-spark_2.12-26.08.1.jar | 4 |

Identity is read from environment.json and campaign.json; a dash marks a field that was not recorded. Topology (threads per core, physical cores, NUMA nodes) is what lscpu reports inside the machine; the cores occupied are bounded below by packing the --cpus threads onto sibling hardware threads and above by spreading them over distinct cores.


## 2. How the ratios are built

ratio = time on the host / time on the reference host (below 1: the host is faster). Per (suite, scale, query, engine) the estimate is the ratio of the hosts' geometric-mean round medians. The interval is a Welch-type interval on the log scale: rounds are independent runs on different machines, so the two sets of per-round log times are treated as unpaired samples (Welch-Satterthwaite df, two-sided t critical value). The suite-level ratio is the same estimator applied to the per-round geometric mean over the queries that have a valid median in every round on both hosts. Descriptive only: no hypothesis tests, no pooling across hosts, and every per-host estimate keeps its own campaign's rounds.

## 3. Suite-level time ratios

| Engine | Suite | Scale | A100 / H100 [95% CI] | GH200 / H100 [95% CI] | B200 / H100 [95% CI] |
|---|---|---|---|---|---|
| Polars CPU | baseline | SF1 | 1.43 [1.29, 1.58] | 0.696 [0.681, 0.712] | 1.23 [1.17, 1.29] |
| Polars CPU | baseline | SF10 | 1.25 [1.21, 1.28] | 0.618 [0.607, 0.628] | 1.09 [1.07, 1.12] |
| Polars CPU | baseline | SF30 | 1.17 [1.12, 1.23] | 0.599 [0.584, 0.614] | 1.02 [0.996, 1.05] |
| Polars CPU | baseline | SF100 | 1.19 [1.10, 1.28] | 0.614 [0.609, 0.619] | 1.02 [1.01, 1.03] |
| Polars CPU | baseline | SF300 | 1.19 [1.06, 1.34] | 0.605 [0.566, 0.647] | 1.03 [0.935, 1.14] |
| Polars CPU | complex | SF30 | 1.26 [1.24, 1.27] | 0.666 [0.661, 0.670] | 0.996 [0.984, 1.01] |
| Polars CPU | complex | SF100 | 1.24 [1.23, 1.25] | 0.719 [0.709, 0.729] | 1.04 [0.974, 1.12] |
| Polars CPU | complex | SF300 | 1.21 [1.16, 1.27] | 0.710 [0.680, 0.742] | 1.02 [0.974, 1.06] |
| Polars GPU | baseline | SF1 | 1.52 [1.41, 1.64] | 1.11 [1.04, 1.20] | 1.07 [0.990, 1.15] |
| Polars GPU | baseline | SF10 | 1.67 [1.59, 1.76] | 0.995 [0.946, 1.05] | 1.08 [1.03, 1.14] |
| Polars GPU | baseline | SF30 | 1.53 [1.46, 1.61] | 0.945 [0.903, 0.989] | 1.03 [0.965, 1.11] |
| Polars GPU | baseline | SF100 | 1.54 [1.50, 1.57] | 0.977 [0.957, 0.997] | 1.07 [1.05, 1.10] |
| Polars GPU | baseline | SF300 | 1.62 [1.59, 1.66] | 0.954 [0.943, 0.965] | 1.05 [1.04, 1.07] |
| Polars GPU | complex | SF30 | 1.56 [1.49, 1.63] | 0.964 [0.879, 1.06] | 1.12 [1.09, 1.15] |
| Polars GPU | complex | SF100 | 1.67 [1.65, 1.68] | 0.932 [0.909, 0.955] | 1.17 [1.13, 1.22] |
| Polars GPU | complex | SF300 | 1.71 [1.69, 1.73] | 0.923 [0.907, 0.940] | 1.23 [1.20, 1.25] |
| Spark CPU | baseline | SF1 | 1.36 [1.14, 1.62] | 1.01 [0.758, 1.33] | 1.12 [0.894, 1.40] |
| Spark CPU | baseline | SF10 | 1.50 [1.33, 1.70] | 0.884 [0.817, 0.957] | 1.16 [1.05, 1.28] |
| Spark CPU | baseline | SF30 | 1.21 [0.959, 1.52] | 0.798 [0.749, 0.850] | 1.06 [0.925, 1.22] |
| Spark CPU | baseline | SF100 | 1.16 [1.07, 1.25] | 0.736 [0.687, 0.788] | 1.11 [1.04, 1.19] |
| Spark CPU | baseline | SF300 | 1.06 [1.01, 1.10] | 0.689 [0.665, 0.714] | 1.10 [1.04, 1.16] |
| Spark CPU | complex | SF30 | 1.18 [1.15, 1.21] | 0.738 [0.699, 0.778] | 1.02 [0.947, 1.11] |
| Spark CPU | complex | SF100 | 1.13 [1.10, 1.15] | 0.707 [0.691, 0.723] | 1.02 [0.986, 1.06] |
| Spark CPU | complex | SF300 | 1.03 [0.973, 1.10] | 0.669 [0.628, 0.712] | 0.980 [0.922, 1.04] |
| Spark GPU | baseline | SF1 | 1.34 [1.31, 1.37] | 1.15 [1.12, 1.17] | 0.862 [0.841, 0.884] |
| Spark GPU | baseline | SF10 | 1.41 [1.37, 1.44] | 1.17 [1.16, 1.19] | 0.892 [0.878, 0.907] |
| Spark GPU | baseline | SF30 | 1.46 [1.44, 1.48] | 1.15 [1.14, 1.16] | 0.926 [0.916, 0.936] |
| Spark GPU | baseline | SF100 | 1.51 [1.43, 1.61] | 1.12 [1.10, 1.14] | 0.957 [0.944, 0.971] |
| Spark GPU | baseline | SF300 | 1.47 [1.42, 1.52] | 1.07 [1.05, 1.09] | 0.937 [0.913, 0.960] |
| Spark GPU | complex | SF30 | 1.50 [1.36, 1.66] | 1.14 [1.03, 1.26] | 1.02 [0.915, 1.13] |
| Spark GPU | complex | SF100 | 1.43 [1.40, 1.45] | 0.977 [0.950, 1.00] | 1.05 [1.03, 1.08] |
| Spark GPU | complex | SF300 | 1.43 [1.39, 1.47] | 0.866 [0.851, 0.881] | 1.05 [1.04, 1.05] |

Time ratio host / H100: below 1 the host is faster. The interval is Welch-type on the log scale (unpaired rounds); n=k marks a single round on one side (no interval).


## 4. Speedup of each host's own campaign

| Contrast | Suite | Scale | A100 | H100 | GH200 | B200 |
|---|---|---|---|---|---|---|
| C1 | baseline | SF1 | 0.330 [0.297, 0.366] | 0.350 [0.320, 0.382] | 0.218 [0.214, 0.223] | 0.403 [0.385, 0.421] |
| C1 | baseline | SF10 | 0.917 [0.885, 0.950] | 1.23 [1.17, 1.29] | 0.764 [0.739, 0.791] | 1.25 [1.21, 1.28] |
| C1 | baseline | SF30 | 1.51 [1.42, 1.61] | 1.97 [1.92, 2.03] | 1.25 [1.24, 1.26] | 1.95 [1.83, 2.09] |
| C1 | baseline | SF100 | 1.98 [1.86, 2.11] | 2.56 [2.50, 2.63] | 1.61 [1.59, 1.63] | 2.43 [2.38, 2.49] |
| C1 | baseline | SF300 | 2.07 [1.83, 2.34] | 2.81 [2.64, 2.99] | 1.78 [1.76, 1.80] | 2.76 [2.51, 3.03] |
| C1 | complex | SF30 | 2.87 [2.68, 3.06] | 3.66 [3.54, 3.79] | 2.28 [2.07, 2.51] | 3.30 [3.20, 3.40] |
| C1 | complex | SF100 | 3.57 [3.50, 3.65] | 4.88 [4.80, 4.96] | 3.44 [3.30, 3.58] | 4.39 [4.09, 4.72] |
| C1 | complex | SF300 | 3.32 [3.15, 3.49] | 4.70 [4.47, 4.94] | 3.26 [3.20, 3.32] | 3.93 [3.75, 4.12] |
| C2 | baseline | SF1 | 0.674 [0.592, 0.768] | 0.662 [0.549, 0.799] | 0.581 [0.430, 0.786] | 0.859 [0.692, 1.07] |
| C2 | baseline | SF10 | 1.40 [1.21, 1.61] | 1.31 [1.28, 1.34] | 0.985 [0.906, 1.07] | 1.70 [1.55, 1.87] |
| C2 | baseline | SF30 | 1.89 [1.49, 2.41] | 2.29 [2.20, 2.38] | 1.59 [1.48, 1.70] | 2.63 [2.26, 3.06] |
| C2 | baseline | SF100 | 2.14 [2.03, 2.25] | 2.80 [2.68, 2.93] | 1.85 [1.70, 2.01] | 3.26 [3.03, 3.51] |
| C2 | baseline | SF300 | 2.03 [1.95, 2.11] | 2.82 [2.77, 2.88] | 1.82 [1.75, 1.90] | 3.32 [3.14, 3.50] |
| C2 | complex | SF30 | 3.08 [2.90, 3.28] | 3.92 [3.48, 4.40] | 2.54 [2.38, 2.72] | 3.94 [3.65, 4.25] |
| C2 | complex | SF100 | 3.58 [3.47, 3.69] | 4.53 [4.43, 4.63] | 3.28 [3.21, 3.35] | 4.41 [4.17, 4.66] |
| C2 | complex | SF300 | 2.91 [2.82, 3.01] | 4.02 [3.77, 4.28] | 3.10 [3.03, 3.18] | 3.76 [3.70, 3.83] |
| C3 | baseline | SF1 | 5.82 [5.69, 5.95] | 6.60 [6.04, 7.21] | 6.78 [6.54, 7.03] | 5.33 [5.19, 5.47] |
| C3 | baseline | SF10 | 4.55 [4.45, 4.66] | 5.41 [5.14, 5.69] | 6.39 [6.13, 6.65] | 4.46 [4.36, 4.58] |
| C3 | baseline | SF30 | 4.02 [3.82, 4.24] | 4.22 [3.99, 4.47] | 5.14 [5.06, 5.23] | 3.79 [3.54, 4.05] |
| C3 | baseline | SF100 | 3.46 [3.25, 3.69] | 3.51 [3.47, 3.55] | 4.02 [3.91, 4.12] | 3.13 [3.06, 3.20] |
| C3 | baseline | SF300 | 2.84 [2.70, 2.98] | 3.14 [3.08, 3.19] | 3.51 [3.49, 3.54] | 2.79 [2.75, 2.84] |
| C3 | complex | SF30 | 6.59 [5.99, 7.25] | 6.83 [5.76, 8.09] | 8.36 [7.74, 9.04] | 6.22 [6.05, 6.40] |
| C3 | complex | SF100 | 5.50 [5.42, 5.57] | 6.44 [6.35, 6.53] | 6.96 [6.57, 7.37] | 5.66 [5.41, 5.92] |
| C3 | complex | SF300 | 4.68 [4.64, 4.72] | 5.71 [5.63, 5.79] | 5.18 [5.02, 5.33] | 4.72 [4.60, 4.85] |
| C4 | baseline | SF1 | 0.330 [0.297, 0.366] | 0.350 [0.320, 0.382] | 0.218 [0.214, 0.223] | 0.403 [0.385, 0.421] |
| C4 | baseline | SF10 | 0.917 [0.885, 0.950] | 1.23 [1.17, 1.29] | 0.764 [0.739, 0.791] | 1.25 [1.21, 1.28] |
| C4 | baseline | SF30 | 1.51 [1.42, 1.61] | 1.97 [1.92, 2.03] | 1.25 [1.24, 1.26] | 1.95 [1.83, 2.09] |
| C4 | baseline | SF100 | 1.98 [1.86, 2.11] | 2.56 [2.50, 2.63] | 1.61 [1.59, 1.63] | 2.43 [2.38, 2.49] |
| C4 | baseline | SF300 | 2.07 [1.83, 2.34] | 2.81 [2.64, 2.99] | 1.78 [1.76, 1.80] | 2.76 [2.51, 3.03] |
| C4 | complex | SF30 | 2.74 [2.62, 2.87] | 3.34 [3.26, 3.43] | 2.24 [2.10, 2.40] | 3.08 [3.01, 3.15] |
| C4 | complex | SF100 | 4.10 [4.09, 4.11] | 5.25 [5.17, 5.32] | 4.07 [3.94, 4.20] | 4.79 [4.56, 5.02] |
| C4 | complex | SF300 | 4.19 [4.15, 4.24] | 5.64 [5.39, 5.91] | 4.34 [4.29, 4.38] | 4.82 [4.66, 4.99] |
| C4s | baseline | SF1 | 0.330 [0.297, 0.366] | 0.350 [0.320, 0.382] | 0.218 [0.214, 0.223] | 0.403 [0.385, 0.421] |
| C4s | baseline | SF10 | 0.917 [0.885, 0.950] | 1.23 [1.17, 1.29] | 0.764 [0.739, 0.791] | 1.25 [1.21, 1.28] |
| C4s | baseline | SF30 | 1.51 [1.42, 1.61] | 1.97 [1.92, 2.03] | 1.25 [1.24, 1.26] | 1.95 [1.83, 2.09] |
| C4s | baseline | SF100 | 1.98 [1.86, 2.11] | 2.56 [2.50, 2.63] | 1.61 [1.59, 1.63] | 2.43 [2.38, 2.49] |
| C4s | baseline | SF300 | 2.07 [1.83, 2.34] | 2.81 [2.64, 2.99] | 1.78 [1.76, 1.80] | 2.76 [2.51, 3.03] |
| C4s | complex | SF30 | 2.87 [2.68, 3.06] | 3.66 [3.54, 3.79] | 2.28 [2.07, 2.51] | 3.30 [3.20, 3.40] |
| C4s | complex | SF100 | 3.57 [3.50, 3.65] | 4.88 [4.80, 4.96] | 3.44 [3.30, 3.58] | 4.39 [4.09, 4.72] |
| C4s | complex | SF300 | 3.32 [3.15, 3.49] | 4.70 [4.47, 4.94] | 3.26 [3.20, 3.32] | 3.93 [3.75, 4.12] |

C1 Polars CPU / Polars GPU, C2 Spark CPU / Spark GPU, C3 Spark GPU / Polars GPU, C4 Polars CPU / best GPU engine; each host's intervals come from its own rounds.


## 5. Coverage outcomes that differ from the reference

None: every host has the same coverage outcome as the reference in every cell.

## 6. Output agreement with Polars (integrity evidence)

44 (suite, scale, engine) cell(s) show a larger relative difference than on H100; all are listed in table x06, cells outside the 1e-8 gate: 0.

## 7. Plan variability of the Spark cells (descriptive)

Cells whose trials ended in more than one final adaptive plan on a host (a plan's signature is the same on every host). The trials of a variable cell still enter the cell's median as designed.

| Host | Suite | Scale | Query | Engine | Plan | Measured trials | Warm-ups | Measured median (s) | Warm-up median (s) | Exchanges | Shuffle written (GiB, median) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A100 | baseline | SF10 | q03 | Spark CPU | 17ec724f7006 | 10 | 0 | 2.65 | – | 3 | 0.509 |
| A100 | baseline | SF10 | q03 | Spark CPU | 1a528022a1c0 | 2 | 4 | 3.04 | 4.71 | 5 | 0.537 |
| A100 | complex | SF30 | cx_q09 | Spark GPU | a4362d8c1e3e | 1 | 4 | 3.14 | 6.44 | 7 | 1.11 |
| A100 | complex | SF30 | cx_q09 | Spark GPU | 5166c91b662d | 11 | 0 | 6.37 | – | 8 | 5.78 |
| H100 | complex | SF30 | cx_q09 | Spark GPU | a4362d8c1e3e | 4 | 4 | 2.15 | 4.46 | 7 | 1.11 |
| H100 | complex | SF30 | cx_q09 | Spark GPU | 5166c91b662d | 8 | 0 | 4.76 | – | 8 | 5.80 |
| GH200 | complex | SF30 | cx_q09 | Spark GPU | 5166c91b662d | 12 | 2 | 4.94 | 6.35 | 8 | 5.80 |
| GH200 | complex | SF30 | cx_q09 | Spark GPU | a4362d8c1e3e | 0 | 2 | – | 4.65 | 7 | – |
| B200 | complex | SF30 | cx_q09 | Spark GPU | a4362d8c1e3e | 4 | 4 | 2.24 | 4.75 | 7 | 1.11 |
| B200 | complex | SF30 | cx_q09 | Spark GPU | 5166c91b662d | 8 | 0 | 4.93 | – | 8 | 5.80 |

Descriptive; a trial that ends in a different plan still enters its cell's median. Warm-up trials are not measured trials.


## 8. GPU telemetry inside measured actions (descriptive)

descriptive telemetry; not a pre-registered contrast; no causal claim. Per host, engine, suite and scale: the share of nvidia-smi samples with utilization.gpu below 10% (GPU-idle), the median utilization.gpu and the median power draw with its share of the card's power limit, from the samples inside measured actions of at least 1 s (shorter actions are left out; the sampling interval is about 200 ms).

| Suite | Scale | Engine | A100: GPU-idle / util median / power (of limit) | H100: GPU-idle / util median / power (of limit) | GH200: GPU-idle / util median / power (of limit) | B200: GPU-idle / util median / power (of limit) |
|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | – | – | – | – |
| baseline | SF1 | Spark GPU | 12% / 38% / 69 W (17%) | – | 30% / 32% / 135 W (19%) | – |
| baseline | SF10 | Polars GPU | – | – | – | – |
| baseline | SF10 | Spark GPU | 7% / 45% / 75 W (19%) | 13% / 49% / 124 W (18%) | 26% / 25% / 141 W (20%) | 14% / 34% / 202 W (20%) |
| baseline | SF30 | Polars GPU | – | – | – | – |
| baseline | SF30 | Spark GPU | 12% / 71% / 78 W (20%) | 16% / 70% / 135 W (19%) | 23% / 43% / 149 W (21%) | 15% / 44% / 209 W (21%) |
| baseline | SF100 | Polars GPU | 0% / 94% / 160 W (40%) | – | 12% / 74% / 237 W (34%) | 0% / 50% / 246 W (25%) |
| baseline | SF100 | Spark GPU | 9% / 81% / 92 W (23%) | 12% / 79% / 145 W (21%) | 21% / 54% / 159 W (23%) | 16% / 46% / 215 W (21%) |
| baseline | SF300 | Polars GPU | 0% / 89% / 155 W (39%) | 0% / 87% / 242 W (35%) | 1% / 77% / 251 W (36%) | 0% / 76% / 278 W (28%) |
| baseline | SF300 | Spark GPU | 6% / 84% / 96 W (24%) | 10% / 79% / 149 W (21%) | 13% / 64% / 165 W (24%) | 12% / 49% / 218 W (22%) |
| complex | SF30 | Polars GPU | – | – | – | – |
| complex | SF30 | Spark GPU | 23% / 45% / 76 W (19%) | 25% / 36% / 130 W (19%) | 38% / 19% / 146 W (21%) | 29% / 24% / 207 W (21%) |
| complex | SF100 | Polars GPU | 1% / 90% / 158 W (39%) | 0% / 90% / 254 W (36%) | 6% / 82% / 269 W (38%) | 0% / 80% / 295 W (29%) |
| complex | SF100 | Spark GPU | 17% / 47% / 78 W (20%) | 21% / 32% / 132 W (19%) | 29% / 28% / 157 W (22%) | 24% / 24% / 208 W (21%) |
| complex | SF300 | Polars GPU | 0% / 91% / 155 W (39%) | 1% / 86% / 285 W (41%) | 3% / 79% / 302 W (43%) | 0% / 73% / 339 W (34%) |
| complex | SF300 | Spark GPU | 23% / 36% / 70 W (18%) | 29% / 22% / 129 W (18%) | 25% / 44% / 163 W (23%) | 35% / 19% / 207 W (21%) |

descriptive telemetry; not a pre-registered contrast; no causal claim. GPU-idle = share of nvidia-smi samples (about every 200 ms) with utilization.gpu below 10% inside measured actions of at least 1 s; a dash: no action of that cell was at least 1 s long (or no gpu.csv). Power in watts and as a share of the card's current power limit.


## 9. Caveats

- Hosts are separate campaigns (amendments A2/A3); data are never pooled and the reference host remains the primary analysis.
- A GPU-engine ratio confounds GPU generation, CPU, host memory, driver and software build; it does not isolate the GPU.
- CPU-engine ratios reflect the CPU model, architecture, core and SMT layout and memory system (all hosts ran with the same --cpus, which occupies a different number of physical cores where SMT differs); the GPU is idle.
- Rounds are not paired across hosts, so the Welch interval ignores any common drift; with 4 rounds per host the df is at most 6.
- A cell that failed on one host (coverage outcome, A1) is excluded from that host's ratios and listed under coverage_differences.

**A100 versus H100**

- CPU model differs (AMD EPYC 7J13 64-Core Processor vs Intel(R) Xeon(R) Platinum 8480+).
- vCPU count differs (30 vs 26); all engines ran with --cpus 26, so the extra vCPUs sit idle but cache, memory bandwidth and NUMA layout may still differ.
- SMT differs (A100: 1 thread per core, H100: 2): 26 threads occupy 26 physical core(s) on A100 (30 in total) and 13 on H100 (13 in total); CPU-engine ratios compare different numbers of physical cores (hardware threads sharing a core versus whole cores), not only different CPUs, and are not per-core ratios. The topology is what lscpu reports inside the machine; under a hypervisor the mapping of vCPUs to physical cores and threads is the hypervisor's.
- Host RAM differs (216.3 vs 221.2 GiB).
- Device memory differs (40.0 vs 79.6 GiB): out-of-memory boundaries differ, so some cells exist on only one host.
- Driver / CUDA differ (driver 570.148.08 vs 580.105.08; CUDA 12.8 vs 13.0).
- The campaigns started under different design versions: A100 under afd07c0 (4 amendments), H100 under ee2e6a7 (3 amendments); later amendments change the protocol only where they say so (see the design document, section 11).

**GH200 versus H100**

- CPU architecture differs (aarch64 vs x86_64): wheels, JDK and RAPIDS jar are platform builds, so CPU-engine and host-side GPU-engine ratios include the architecture, not only the GPU.
- CPU model differs (Neoverse-V2 vs Intel(R) Xeon(R) Platinum 8480+).
- vCPU count differs (64 vs 26); all engines ran with --cpus 26, so the extra vCPUs sit idle but cache, memory bandwidth and NUMA layout may still differ.
- SMT differs (GH200: 1 thread per core, H100: 2): 26 threads occupy 26 physical core(s) on GH200 (64 in total) and 13 on H100 (13 in total); CPU-engine ratios compare different numbers of physical cores (hardware threads sharing a core versus whole cores), not only different CPUs, and are not per-core ratios. The topology is what lscpu reports inside the machine; under a hypervisor the mapping of vCPUs to physical cores and threads is the hypervisor's.
- NUMA node count differs (9 vs 1; nodes that hold CPUs: 1 vs 1).
- Hypervisor line in lscpu differs (none reported vs KVM); an absent line does not establish bare metal.
- Host RAM differs (525.7 vs 221.2 GiB).
- Device memory differs (95.6 vs 79.6 GiB): out-of-memory boundaries differ, so some cells exist on only one host.
- Driver / CUDA differ (driver 570.148.08 vs 580.105.08; CUDA 12.8 vs 13.0).
- RAPIDS Accelerator jar differs (rapids-4-spark_2.12-26.08.1-cuda12-arm64.jar vs rapids-4-spark_2.12-26.08.1.jar): platform builds of the same plugin version, recorded with their sha256.
- The campaigns started under different design versions: GH200 under afd07c0 (4 amendments), H100 under ee2e6a7 (3 amendments); later amendments change the protocol only where they say so (see the design document, section 11).

**B200 versus H100**

- CPU model differs (INTEL(R) XEON(R) PLATINUM 8592+ vs Intel(R) Xeon(R) Platinum 8480+).
- Host RAM differs (354.1 vs 221.2 GiB).
- Device memory differs (179.1 vs 79.6 GiB): out-of-memory boundaries differ, so some cells exist on only one host.
- Driver / CUDA differ (driver 570.148.08 vs 580.105.08; CUDA 12.8 vs 13.0).
- The campaigns started under different design versions: B200 under afd07c0 (4 amendments), H100 under ee2e6a7 (3 amendments); later amendments change the protocol only where they say so (see the design document, section 11).

