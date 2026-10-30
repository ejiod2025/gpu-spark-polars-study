### Legacy sum of per-query medians (A100-study comparability; arithmetic, not a geometric mean)

| Suite | Scale | Engine | Sum of medians (s) [min–max] |
|---|---|---|---|
| baseline | SF1 | Polars CPU | 0.108 [0.106–0.109] |
| baseline | SF1 | Polars GPU | 0.453 [0.447–0.461] |
| baseline | SF1 | Spark CPU | 1.93 [1.58–2.41] |
| baseline | SF1 | Spark GPU | 3.01 [2.99–3.03] |
| baseline | SF10 | Polars CPU | 0.618 [0.614–0.622] |
| baseline | SF10 | Polars GPU | 0.729 [0.713–0.755] |
| baseline | SF10 | Spark CPU | 5.16 [4.89–5.55] |
| baseline | SF10 | Spark GPU | 5.33 [5.29–5.36] |
| baseline | SF30 | Polars CPU | 2.08 [2.06–2.09] |
| baseline | SF30 | Polars GPU | 1.36 [1.34–1.38] |
| baseline | SF30 | Spark CPU | 13.6 [12.9–14.5] |
| baseline | SF30 | Spark GPU | 8.41 [8.34–8.45] |
| baseline | SF100 | Polars CPU | 7.30 [7.27–7.31] |
| baseline | SF100 | Polars GPU | 3.75 [3.70–3.81] |
| baseline | SF100 | Spark CPU | 33.7 [32.5–35.9] |
| baseline | SF100 | Spark GPU | 16.9 [16.7–17.1] |
| baseline | SF300 | Polars CPU | 24.2 [24.2–24.3] |
| baseline | SF300 | Polars GPU | 11.2 [11.2–11.3] |
| baseline | SF300 | Spark CPU | 86.9 [84.2–88.8] |
| baseline | SF300 | Spark GPU | 43.2 [42.8–44.1] |
| complex | SF30 | Polars CPU | 11.6 [11.6–11.7] |
| complex | SF30 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF30 | Spark CPU | 32.0 [31.2–33.0] |
| complex | SF30 | Spark GPU | 13.0 [12.2–13.9] |
| complex | SF100 | Polars CPU | 44.0 [43.6–44.6] |
| complex | SF100 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF100 | Spark CPU | 92.1 [91.3–92.6] |
| complex | SF100 | Spark GPU | 30.3 [29.4–32.4] |
| complex | SF300 | Polars CPU | 140 [140–141] |
| complex | SF300 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF300 | Spark CPU | 239 [238–240] |
| complex | SF300 | Spark GPU | 76.9 [75.4–78.9] |

Totals exist only when every query has a valid median in every round; never combined with the geometric means of t01.
