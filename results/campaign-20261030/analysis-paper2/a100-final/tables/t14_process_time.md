### Process wall time and startup (mean over rounds, seconds; overlapping scopes, do not add)

| Suite | Scale | Engine | Process wall | Launch → ready | Setup | Warmups | Measured |
|---|---|---|---|---|---|---|---|
| baseline | SF1 | Polars CPU | 1.47 | 0.187 | 0.104 | 0.291 | 0.677 |
| baseline | SF1 | Polars GPU | 6.39 | 1.75 | 1.67 | 1.06 | 1.84 |
| baseline | SF1 | Spark CPU | 27.8 | 5.71 | 4.11 | 10.1 | 8.83 |
| baseline | SF1 | Spark GPU | 43.2 | 20.0 | 18.1 | 7.40 | 10.6 |
| baseline | SF10 | Polars CPU | 6.95 | 0.186 | 0.103 | 1.63 | 4.10 |
| baseline | SF10 | Polars GPU | 8.79 | 1.69 | 1.61 | 1.55 | 3.62 |
| baseline | SF10 | Spark CPU | 55.1 | 5.56 | 4.05 | 16.5 | 27.9 |
| baseline | SF10 | Spark GPU | 54.3 | 19.6 | 17.9 | 10.6 | 18.8 |
| baseline | SF30 | Polars CPU | 19.9 | 0.193 | 0.105 | 4.97 | 13.3 |
| baseline | SF30 | Polars GPU | 12.6 | 1.67 | 1.58 | 2.52 | 6.60 |
| baseline | SF30 | Spark CPU | 105 | 5.57 | 4.05 | 29.0 | 62.8 |
| baseline | SF30 | Spark GPU | 73.9 | 19.6 | 17.9 | 15.5 | 31.0 |
| baseline | SF100 | Polars CPU | 60.4 | 0.186 | 0.103 | 14.9 | 44.4 |
| baseline | SF100 | Polars GPU | 27.3 | 1.72 | 1.63 | 6.16 | 17.5 |
| baseline | SF100 | Spark CPU | 246 | 5.59 | 4.07 | 64.5 | 160 |
| baseline | SF100 | Spark GPU | 128 | 19.6 | 17.9 | 28.0 | 66.3 |
| baseline | SF300 | Polars CPU | 208 | 0.195 | 0.111 | 53.1 | 154 |
| baseline | SF300 | Polars GPU | 84.2 | 1.79 | 1.71 | 20.6 | 59.8 |
| baseline | SF300 | Spark CPU | 593 | 5.61 | 4.09 | 148 | 405 |
| baseline | SF300 | Spark GPU | 290 | 19.8 | 18.0 | 61.4 | 176 |
| complex | SF30 | Polars CPU | 73.9 | 0.187 | 0.104 | 18.0 | 53.8 |
| complex | SF30 | Polars GPU | 12.2 | 1.71 | 1.63 | 2.30 | 5.83 |
| complex | SF30 | Spark CPU | 287 | 6.02 | 4.42 | 59.0 | 151 |
| complex | SF30 | Spark GPU | 165 | 20.1 | 18.2 | 18.8 | 51.2 |
| complex | SF100 | Polars CPU | 272 | 0.188 | 0.105 | 67.0 | 202 |
| complex | SF100 | Polars GPU | 28.5 | 1.68 | 1.60 | 6.42 | 18.0 |
| complex | SF100 | Spark CPU | 672 | 5.70 | 4.18 | 149 | 420 |
| complex | SF100 | Spark GPU | 329 | 19.8 | 18.1 | 58.5 | 147 |
| complex | SF300 | Polars CPU | 858 | 0.205 | 0.113 | 212 | 640 |
| complex | SF300 | Polars GPU | 110 | 1.88 | 1.79 | 27.1 | 79.1 |
| complex | SF300 | Spark CPU | 1590 | 6.70 | 4.90 | 363 | 1056 |
| complex | SF300 | Spark GPU | 900 | 21.1 | 19.1 | 191 | 517 |
