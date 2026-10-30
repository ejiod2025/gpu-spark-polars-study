### Suite-level speedup (geometric mean of per-query ratios; 95% t-interval over rounds)

| Contrast | Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|---|
| Polars CPU / Polars GPU | baseline | SF1 | 6 | 0.350 [0.320, 0.382] | 0.322–0.364 |
| Polars CPU / Polars GPU | baseline | SF10 | 6 | 1.23 [1.17, 1.29] | 1.20–1.28 |
| Polars CPU / Polars GPU | baseline | SF30 | 6 | 1.97 [1.92, 2.03] | 1.92–2.00 |
| Polars CPU / Polars GPU | baseline | SF100 | 6 | 2.56 [2.50, 2.63] | 2.51–2.61 |
| Polars CPU / Polars GPU | baseline | SF300 | 6 | 2.81 [2.64, 2.99] | 2.70–2.94 |
| Polars CPU / Polars GPU | complex | SF30 | 4 | 3.66 [3.54, 3.79] | 3.57–3.74 |
| Polars CPU / Polars GPU | complex | SF100 | 4 | 4.88 [4.80, 4.96] | 4.83–4.95 |
| Polars CPU / Polars GPU | complex | SF300 | 4 | 4.70 [4.47, 4.94] | 4.59–4.91 |
| Spark CPU / Spark GPU | baseline | SF1 | 6 | 0.662 [0.549, 0.799] | 0.597–0.753 |
| Spark CPU / Spark GPU | baseline | SF10 | 6 | 1.31 [1.28, 1.34] | 1.29–1.33 |
| Spark CPU / Spark GPU | baseline | SF30 | 6 | 2.29 [2.20, 2.38] | 2.21–2.35 |
| Spark CPU / Spark GPU | baseline | SF100 | 6 | 2.80 [2.68, 2.93] | 2.70–2.87 |
| Spark CPU / Spark GPU | baseline | SF300 | 6 | 2.82 [2.77, 2.88] | 2.78–2.86 |
| Spark CPU / Spark GPU | complex | SF30 | 6 | 3.92 [3.48, 4.40] | 3.74–4.37 |
| Spark CPU / Spark GPU | complex | SF100 | 6 | 4.53 [4.43, 4.63] | 4.45–4.60 |
| Spark CPU / Spark GPU | complex | SF300 | 6 | 4.02 [3.77, 4.28] | 3.88–4.24 |
| Spark GPU / Polars GPU | baseline | SF1 | 6 | 6.60 [6.04, 7.21] | 6.09–6.94 |
| Spark GPU / Polars GPU | baseline | SF10 | 6 | 5.41 [5.14, 5.69] | 5.25–5.63 |
| Spark GPU / Polars GPU | baseline | SF30 | 6 | 4.22 [3.99, 4.47] | 4.01–4.33 |
| Spark GPU / Polars GPU | baseline | SF100 | 6 | 3.51 [3.47, 3.55] | 3.49–3.55 |
| Spark GPU / Polars GPU | baseline | SF300 | 6 | 3.14 [3.08, 3.19] | 3.11–3.19 |
| Spark GPU / Polars GPU | complex | SF30 | 4 | 6.83 [5.76, 8.09] | 5.83–7.30 |
| Spark GPU / Polars GPU | complex | SF100 | 4 | 6.44 [6.35, 6.53] | 6.38–6.50 |
| Spark GPU / Polars GPU | complex | SF300 | 4 | 5.71 [5.63, 5.79] | 5.66–5.76 |
| Polars CPU / best GPU engine | baseline | SF1 | 6 | 0.350 [0.320, 0.382] | 0.322–0.364 |
| Polars CPU / best GPU engine | baseline | SF10 | 6 | 1.23 [1.17, 1.29] | 1.20–1.28 |
| Polars CPU / best GPU engine | baseline | SF30 | 6 | 1.97 [1.92, 2.03] | 1.92–2.00 |
| Polars CPU / best GPU engine | baseline | SF100 | 6 | 2.56 [2.50, 2.63] | 2.51–2.61 |
| Polars CPU / best GPU engine | baseline | SF300 | 6 | 2.81 [2.64, 2.99] | 2.70–2.94 |
| Polars CPU / best GPU engine | complex | SF30 | 6 | 3.34 [3.26, 3.43] | 3.30–3.41 |
| Polars CPU / best GPU engine | complex | SF100 | 6 | 5.25 [5.17, 5.32] | 5.20–5.30 |
| Polars CPU / best GPU engine | complex | SF300 | 6 | 5.64 [5.39, 5.91] | 5.52–5.88 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF1 | 6 | 0.350 [0.320, 0.382] | 0.322–0.364 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF10 | 6 | 1.23 [1.17, 1.29] | 1.20–1.28 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF30 | 6 | 1.97 [1.92, 2.03] | 1.92–2.00 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF100 | 6 | 2.56 [2.50, 2.63] | 2.51–2.61 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF300 | 6 | 2.81 [2.64, 2.99] | 2.70–2.94 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | complex | SF30 | 4 | 3.66 [3.54, 3.79] | 3.57–3.74 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | complex | SF100 | 4 | 4.88 [4.80, 4.96] | 4.83–4.95 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | complex | SF300 | 4 | 4.70 [4.47, 4.94] | 4.59–4.91 |

Speedup = t_ref / t_engine; above 1 the denominator engine is faster. Queries unsupported on a side are excluded from that suite geomean (see t12_coverage). Intervals use df = rounds − 1.
