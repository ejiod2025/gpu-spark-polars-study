### Plan variability: Spark cells whose trials end in more than one final adaptive plan (descriptive)

| Suite | Scale | Query | Engine | Plan | Measured trials (per round) | Warm-ups | Measured s (min / median / max) | Exchanges | Shuffle readers | Stages / tasks (median per action) | Shuffle written (GiB, median) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| complex | SF30 | cx_q09 | Spark GPU | 5166c91b662d * | 12 (r1: 3; r2: 3; r3: 3; r4: 3) | 2 | 4.33/4.94/6.70 | 8 | coalesced 6, local 1, plain 1 | 13.0 / 338 | 5.80 |
| complex | SF30 | cx_q09 | Spark GPU | a4362d8c1e3e | 0 | 2 | – | 7 | coalesced 6, plain 1 | – | – |

* plan with the lowest median measured time in its cell (the reference for the listed changes).
