### Process wall time and startup (mean over rounds, seconds; overlapping scopes, do not add)

| Suite | Scale | Engine | Process wall | Launch → ready | Setup | Warmups | Measured |
|---|---|---|---|---|---|---|---|
| baseline | SF1 | Polars CPU | 0.678 | 0.104 | 0.0581 | 0.117 | 0.322 |
| baseline | SF1 | Polars GPU | 4.43 | 1.24 | 1.19 | 0.710 | 1.39 |
| baseline | SF1 | Spark CPU | 16.5 | 3.94 | 2.99 | 5.71 | 6.03 |
| baseline | SF1 | Spark GPU | 39.9 | 21.7 | 20.6 | 5.70 | 9.08 |
| baseline | SF10 | Polars CPU | 2.77 | 0.102 | 0.0570 | 0.629 | 1.86 |
| baseline | SF10 | Polars GPU | 6.67 | 1.24 | 1.19 | 1.81 | 2.26 |
| baseline | SF10 | Spark CPU | 31.4 | 3.93 | 2.98 | 10.5 | 15.6 |
| baseline | SF10 | Spark GPU | 49.1 | 21.7 | 20.6 | 8.32 | 16.0 |
| baseline | SF30 | Polars CPU | 8.59 | 0.101 | 0.0562 | 2.08 | 6.25 |
| baseline | SF30 | Polars GPU | 9.20 | 1.25 | 1.20 | 2.52 | 4.16 |
| baseline | SF30 | Spark CPU | 64.5 | 3.94 | 2.99 | 18.2 | 40.7 |
| baseline | SF30 | Spark GPU | 61.5 | 21.7 | 20.6 | 11.2 | 25.3 |
| baseline | SF100 | Polars CPU | 29.5 | 0.102 | 0.0566 | 7.26 | 21.9 |
| baseline | SF100 | Polars GPU | 19.0 | 1.27 | 1.22 | 5.15 | 11.2 |
| baseline | SF100 | Spark CPU | 154 | 3.94 | 2.99 | 42.9 | 103 |
| baseline | SF100 | Spark GPU | 98.3 | 21.7 | 20.7 | 19.7 | 50.9 |
| baseline | SF300 | Polars CPU | 97.0 | 0.101 | 0.0557 | 24.2 | 72.5 |
| baseline | SF300 | Polars GPU | 50.5 | 1.27 | 1.23 | 14.1 | 33.9 |
| baseline | SF300 | Spark CPU | 373 | 3.97 | 3.01 | 91.4 | 263 |
| baseline | SF300 | Spark GPU | 216 | 21.8 | 20.7 | 45.5 | 132 |
| complex | SF30 | Polars CPU | 47.4 | 0.102 | 0.0568 | 11.7 | 35.0 |
| complex | SF30 | Polars GPU | 8.94 | 1.25 | 1.21 | 2.64 | 3.63 |
| complex | SF30 | Spark CPU | 195 | 4.08 | 3.12 | 35.7 | 95.6 |
| complex | SF30 | Spark GPU | 139 | 21.9 | 20.8 | 14.3 | 39.3 |
| complex | SF100 | Polars CPU | 177 | 0.104 | 0.0576 | 44.0 | 132 |
| complex | SF100 | Polars GPU | 18.4 | 1.27 | 1.23 | 5.65 | 10.00 |
| complex | SF100 | Spark CPU | 446 | 4.07 | 3.12 | 95.6 | 279 |
| complex | SF100 | Spark GPU | 218 | 21.9 | 20.8 | 35.5 | 91.5 |
| complex | SF300 | Polars CPU | 563 | 0.103 | 0.0570 | 140 | 422 |
| complex | SF300 | Polars GPU | 53.4 | 1.27 | 1.22 | 16.3 | 34.3 |
| complex | SF300 | Spark CPU | 1051 | 4.11 | 3.15 | 238 | 718 |
| complex | SF300 | Spark GPU | 426 | 21.9 | 20.8 | 82.6 | 231 |
