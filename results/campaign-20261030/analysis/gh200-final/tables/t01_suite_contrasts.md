### Suite-level speedup (geometric mean of per-query ratios; 95% t-interval over rounds)

| Contrast | Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|---|
| Polars CPU / Polars GPU | baseline | SF1 | 6 | 0.218 [0.214, 0.223] | 0.215–0.222 |
| Polars CPU / Polars GPU | baseline | SF10 | 6 | 0.764 [0.739, 0.791] | 0.744–0.783 |
| Polars CPU / Polars GPU | baseline | SF30 | 6 | 1.25 [1.24, 1.26] | 1.25–1.26 |
| Polars CPU / Polars GPU | baseline | SF100 | 6 | 1.61 [1.59, 1.63] | 1.59–1.62 |
| Polars CPU / Polars GPU | baseline | SF300 | 6 | 1.78 [1.76, 1.80] | 1.77–1.80 |
| Polars CPU / Polars GPU | complex | SF30 | 4 | 2.28 [2.07, 2.51] | 2.09–2.36 |
| Polars CPU / Polars GPU | complex | SF100 | 4 | 3.44 [3.30, 3.58] | 3.35–3.54 |
| Polars CPU / Polars GPU | complex | SF300 | 4 | 3.26 [3.20, 3.32] | 3.22–3.29 |
| Spark CPU / Spark GPU | baseline | SF1 | 6 | 0.581 [0.430, 0.786] | 0.490–0.731 |
| Spark CPU / Spark GPU | baseline | SF10 | 6 | 0.985 [0.906, 1.07] | 0.949–1.06 |
| Spark CPU / Spark GPU | baseline | SF30 | 6 | 1.59 [1.48, 1.70] | 1.50–1.66 |
| Spark CPU / Spark GPU | baseline | SF100 | 6 | 1.85 [1.70, 2.01] | 1.77–2.00 |
| Spark CPU / Spark GPU | baseline | SF300 | 6 | 1.82 [1.75, 1.90] | 1.76–1.87 |
| Spark CPU / Spark GPU | complex | SF30 | 6 | 2.54 [2.38, 2.72] | 2.42–2.68 |
| Spark CPU / Spark GPU | complex | SF100 | 6 | 3.28 [3.21, 3.35] | 3.23–3.34 |
| Spark CPU / Spark GPU | complex | SF300 | 6 | 3.10 [3.03, 3.18] | 3.04–3.14 |
| Spark GPU / Polars GPU | baseline | SF1 | 6 | 6.78 [6.54, 7.03] | 6.64–6.96 |
| Spark GPU / Polars GPU | baseline | SF10 | 6 | 6.39 [6.13, 6.65] | 6.17–6.57 |
| Spark GPU / Polars GPU | baseline | SF30 | 6 | 5.14 [5.06, 5.23] | 5.07–5.21 |
| Spark GPU / Polars GPU | baseline | SF100 | 6 | 4.02 [3.91, 4.12] | 3.93–4.08 |
| Spark GPU / Polars GPU | baseline | SF300 | 6 | 3.51 [3.49, 3.54] | 3.50–3.53 |
| Spark GPU / Polars GPU | complex | SF30 | 4 | 8.36 [7.74, 9.04] | 8.00–8.97 |
| Spark GPU / Polars GPU | complex | SF100 | 4 | 6.96 [6.57, 7.37] | 6.74–7.32 |
| Spark GPU / Polars GPU | complex | SF300 | 4 | 5.18 [5.02, 5.33] | 5.08–5.31 |
| Polars CPU / best GPU engine | baseline | SF1 | 6 | 0.218 [0.214, 0.223] | 0.215–0.222 |
| Polars CPU / best GPU engine | baseline | SF10 | 6 | 0.764 [0.739, 0.791] | 0.744–0.783 |
| Polars CPU / best GPU engine | baseline | SF30 | 6 | 1.25 [1.24, 1.26] | 1.25–1.26 |
| Polars CPU / best GPU engine | baseline | SF100 | 6 | 1.61 [1.59, 1.63] | 1.59–1.62 |
| Polars CPU / best GPU engine | baseline | SF300 | 6 | 1.78 [1.76, 1.80] | 1.77–1.80 |
| Polars CPU / best GPU engine | complex | SF30 | 6 | 2.24 [2.10, 2.40] | 2.10–2.29 |
| Polars CPU / best GPU engine | complex | SF100 | 6 | 4.07 [3.94, 4.20] | 3.97–4.15 |
| Polars CPU / best GPU engine | complex | SF300 | 6 | 4.34 [4.29, 4.38] | 4.31–4.37 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF1 | 6 | 0.218 [0.214, 0.223] | 0.215–0.222 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF10 | 6 | 0.764 [0.739, 0.791] | 0.744–0.783 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF30 | 6 | 1.25 [1.24, 1.26] | 1.25–1.26 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF100 | 6 | 1.61 [1.59, 1.63] | 1.59–1.62 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | baseline | SF300 | 6 | 1.78 [1.76, 1.80] | 1.77–1.80 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | complex | SF30 | 4 | 2.28 [2.07, 2.51] | 2.09–2.36 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | complex | SF100 | 4 | 3.44 [3.30, 3.58] | 3.35–3.54 |
| Polars CPU / best GPU (both GPU engines supported) (sensitivity) | complex | SF300 | 4 | 3.26 [3.20, 3.32] | 3.22–3.29 |

Speedup = t_ref / t_engine; above 1 the denominator engine is faster. Queries unsupported on a side are excluded from that suite geomean (see t12_coverage). Intervals use df = rounds − 1.
