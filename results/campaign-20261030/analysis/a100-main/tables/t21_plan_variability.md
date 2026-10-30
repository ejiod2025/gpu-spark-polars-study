### Plan variability: Spark cells whose trials end in more than one final adaptive plan (descriptive)

| Suite | Scale | Query | Engine | Plan | Measured trials (per round) | Warm-ups | Measured s (min / median / max) | Exchanges | Shuffle readers | Stages / tasks (median per action) | Shuffle written (GiB, median) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | SF10 | q03 | Spark CPU | 17ec724f7006 * | 10 (r1: 2; r2: 3; r3: 3; r4: 2) | 0 | 2.03/2.65/4.89 | 3 | coalesced 2, local 1 | 5.00 / 118 | 0.509 |
| baseline | SF10 | q03 | Spark CPU | 1a528022a1c0 | 2 (r1: 1; r4: 1) | 4 | 2.69/3.04/3.40 | 5 | coalesced 3, local 2 | 7.00 / 144 | 0.537 |
| complex | SF30 | cx_q09 | Spark GPU | a4362d8c1e3e * | 1 (r2: 1) | 4 | 3.14/3.14/3.14 | 7 | coalesced 6, plain 1 | 12.0 / 216 | 1.11 |
| complex | SF30 | cx_q09 | Spark GPU | 5166c91b662d | 11 (r1: 3; r2: 2; r3: 3; r4: 3) | 0 | 6.25/6.37/7.23 | 8 | coalesced 6, local 1, plain 1 | 13.0 / 336 | 5.78 |

* plan with the lowest median measured time in its cell (the reference for the listed changes).
