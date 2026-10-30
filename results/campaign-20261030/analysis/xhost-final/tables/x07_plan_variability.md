### Spark cells that ended in more than one final adaptive plan, per host (equal plans have equal signatures on every host)

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
