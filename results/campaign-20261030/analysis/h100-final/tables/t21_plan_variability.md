### Plan variability: Spark cells whose trials end in more than one final adaptive plan (descriptive)

| Suite | Scale | Query | Engine | Plan | Measured trials (per round) | Warm-ups | Measured s (min / median / max) | Exchanges | Shuffle readers | Stages / tasks (median per action) | Shuffle written (GiB, median) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| complex | SF30 | cx_q09 | Spark GPU | a4362d8c1e3e * | 4 (r1: 1; r3: 2; r4: 1) | 4 | 2.13/2.15/2.17 | 7 | coalesced 6, plain 1 | 12.0 / 217 | 1.11 |
| complex | SF30 | cx_q09 | Spark GPU | 5166c91b662d | 8 (r1: 2; r2: 3; r3: 1; r4: 2) | 0 | 4.58/4.76/4.80 | 8 | coalesced 6, local 1, plain 1 | 13.0 / 335 | 5.80 |

* plan with the lowest median measured time in its cell (the reference for the listed changes).
