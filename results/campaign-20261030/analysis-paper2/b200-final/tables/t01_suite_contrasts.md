### Suite-level speedup (geometric mean of per-query ratios; 95% t-interval over rounds)

| Contrast | Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|---|
| Polars CPU / Polars GPU | baseline | SF1 | 6 | 0.403 [0.385, 0.421] | 0.393–0.415 |
| Polars CPU / Polars GPU | baseline | SF10 | 6 | 1.25 [1.21, 1.28] | 1.23–1.27 |
| Polars CPU / Polars GPU | baseline | SF30 | 6 | 1.95 [1.83, 2.09] | 1.84–2.01 |
| Polars CPU / Polars GPU | baseline | SF100 | 6 | 2.43 [2.38, 2.49] | 2.38–2.46 |
| Polars CPU / Polars GPU | baseline | SF300 | 6 | 2.76 [2.51, 3.03] | 2.63–3.01 |
| Polars CPU / Polars GPU | complex | SF30 | 4 | 3.30 [3.20, 3.40] | 3.24–3.38 |
| Polars CPU / Polars GPU | complex | SF100 | 4 | 4.39 [4.09, 4.72] | 4.22–4.65 |
| Polars CPU / Polars GPU | complex | SF300 | 4 | 3.93 [3.75, 4.12] | 3.83–4.07 |
| Spark CPU / Spark GPU | baseline | SF1 | 6 | 0.859 [0.692, 1.07] | 0.758–0.980 |
| Spark CPU / Spark GPU | baseline | SF10 | 6 | 1.70 [1.55, 1.87] | 1.59–1.80 |
| Spark CPU / Spark GPU | baseline | SF30 | 6 | 2.63 [2.26, 3.06] | 2.39–2.96 |
| Spark CPU / Spark GPU | baseline | SF100 | 6 | 3.26 [3.03, 3.51] | 3.15–3.48 |
| Spark CPU / Spark GPU | baseline | SF300 | 6 | 3.32 [3.14, 3.50] | 3.24–3.49 |
| Spark CPU / Spark GPU | complex | SF30 | 6 | 3.94 [3.65, 4.25] | 3.76–4.16 |
| Spark CPU / Spark GPU | complex | SF100 | 6 | 4.41 [4.17, 4.66] | 4.29–4.62 |
| Spark CPU / Spark GPU | complex | SF300 | 6 | 3.76 [3.70, 3.83] | 3.71–3.81 |
| Spark GPU / Polars GPU | baseline | SF1 | 6 | 5.33 [5.19, 5.47] | 5.22–5.43 |
| Spark GPU / Polars GPU | baseline | SF10 | 6 | 4.46 [4.36, 4.58] | 4.39–4.53 |
| Spark GPU / Polars GPU | baseline | SF30 | 6 | 3.79 [3.54, 4.05] | 3.56–3.92 |
| Spark GPU / Polars GPU | baseline | SF100 | 6 | 3.13 [3.06, 3.20] | 3.08–3.18 |
| Spark GPU / Polars GPU | baseline | SF300 | 6 | 2.79 [2.75, 2.84] | 2.76–2.83 |
| Spark GPU / Polars GPU | complex | SF30 | 4 | 6.22 [6.05, 6.40] | 6.07–6.31 |
| Spark GPU / Polars GPU | complex | SF100 | 4 | 5.66 [5.41, 5.92] | 5.51–5.89 |
| Spark GPU / Polars GPU | complex | SF300 | 4 | 4.72 [4.60, 4.85] | 4.64–4.81 |
| Polars CPU / best GPU engine | baseline | SF1 | 6 | 0.403 [0.385, 0.421] | 0.393–0.415 |
| Polars CPU / best GPU engine | baseline | SF10 | 6 | 1.25 [1.21, 1.28] | 1.23–1.27 |
| Polars CPU / best GPU engine | baseline | SF30 | 6 | 1.95 [1.83, 2.09] | 1.84–2.01 |
| Polars CPU / best GPU engine | baseline | SF100 | 6 | 2.43 [2.38, 2.49] | 2.38–2.46 |
| Polars CPU / best GPU engine | baseline | SF300 | 6 | 2.76 [2.51, 3.03] | 2.63–3.01 |
| Polars CPU / best GPU engine | complex | SF30 | 6 | 3.08 [3.01, 3.15] | 3.03–3.13 |
| Polars CPU / best GPU engine | complex | SF100 | 6 | 4.79 [4.56, 5.02] | 4.66–4.98 |
| Polars CPU / best GPU engine | complex | SF300 | 6 | 4.82 [4.66, 4.99] | 4.74–4.96 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF1 | 6 | 0.403 [0.385, 0.421] | 0.393–0.415 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF10 | 6 | 1.25 [1.21, 1.28] | 1.23–1.27 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF30 | 6 | 1.95 [1.83, 2.09] | 1.84–2.01 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF100 | 6 | 2.43 [2.38, 2.49] | 2.38–2.46 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF300 | 6 | 2.76 [2.51, 3.03] | 2.63–3.01 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | complex | SF30 | 4 | 3.30 [3.20, 3.40] | 3.24–3.38 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | complex | SF100 | 4 | 4.39 [4.09, 4.72] | 4.22–4.65 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | complex | SF300 | 4 | 3.93 [3.75, 4.12] | 3.83–4.07 |

Speedup = t_ref / t_engine; above 1 the denominator engine is faster. Queries unsupported on a side are excluded from that suite geomean (see t12_coverage). Intervals use df = rounds − 1.
