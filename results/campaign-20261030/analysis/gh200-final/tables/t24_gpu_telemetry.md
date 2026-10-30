### GPU telemetry inside measured actions of at least 1 s (descriptive telemetry; not a pre-registered contrast; no causal claim)

| Suite | Scale | Engine | Actions used / left out (< 1 s) | Samples | GPU util median / p90 (%) | GPU-idle fraction (util < 10%) | Power median W (fraction of limit) | SM clock median MHz (fraction of max) | Memory util median (%) |
|---|---|---|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF1 | Spark GPU | 10 / 62 | 54 | 32 / 85 | 29.6% | 135 W (19%) | 1980 (100%) | 0 |
| baseline | SF10 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF10 | Spark GPU | 24 / 48 | 203 | 25 / 100 | 26.1% | 141 W (20%) | 1980 (100%) | 0 |
| baseline | SF30 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF30 | Spark GPU | 48 / 24 | 458 | 43 / 97 | 22.9% | 149 W (21%) | 1980 (100%) | 0 |
| baseline | SF100 | Polars GPU | 9 / 63 | 52 | 74 / 96 | 11.5% | 237 W (34%) | 1980 (100%) | 12 |
| baseline | SF100 | Spark GPU | 69 / 3 | 1023 | 54 / 98 | 21.0% | 159 W (23%) | 1980 (100%) | 1 |
| baseline | SF300 | Polars GPU | 72 / 0 | 692 | 77 / 96 | 1.4% | 251 W (36%) | 1980 (100%) | 12 |
| baseline | SF300 | Spark GPU | 72 / 0 | 2637 | 64 / 98 | 13.3% | 165 W (24%) | 1980 (100%) | 2 |
| complex | SF30 | Polars GPU | 0 / 48 | 0 | – | – | – | – | – |
| complex | SF30 | Spark GPU | 60 / 12 | 761 | 19 / 93 | 38.0% | 146 W (21%) | 1980 (100%) | 1 |
| complex | SF100 | Polars GPU | 14 / 34 | 88 | 82 / 96 | 5.7% | 269 W (38%) | 1980 (100%) | 13 |
| complex | SF100 | Spark GPU | 72 / 0 | 1857 | 28 / 87 | 29.3% | 157 W (22%) | 1980 (100%) | 2 |
| complex | SF300 | Polars GPU | 48 / 0 | 699 | 79 / 93 | 2.9% | 302 W (43%) | 1980 (100%) | 16 |
| complex | SF300 | Spark GPU | 72 / 0 | 4649 | 44 / 90 | 25.2% | 163 W (23%) | 1980 (100%) | 2 |

descriptive telemetry; not a pre-registered contrast; no causal claim. nvidia-smi is sampled about every 200 ms, so an action of 1 s holds about 5 samples and the first and last sample of an action are partly outside it; actions shorter than 1 s (most SF1 actions) are left out of every statistic and counted in n_actions_excluded_short.
