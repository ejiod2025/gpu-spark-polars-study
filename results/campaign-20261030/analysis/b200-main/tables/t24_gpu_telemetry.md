### GPU telemetry inside measured actions of at least 1 s (descriptive telemetry; not a pre-registered contrast; no causal claim)

| Suite | Scale | Engine | Actions used / left out (< 1 s) | Samples | GPU util median / p90 (%) | GPU-idle fraction (util < 10%) | Power median W (fraction of limit) | SM clock median MHz (fraction of max) | Memory util median (%) |
|---|---|---|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF1 | Spark GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF10 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF10 | Spark GPU | 16 / 56 | 115 | 34 / 100 | 13.9% | 202 W (20%) | 1965 (100%) | 0 |
| baseline | SF30 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF30 | Spark GPU | 35 / 37 | 315 | 44 / 100 | 15.2% | 209 W (21%) | 1965 (100%) | 0 |
| baseline | SF100 | Polars GPU | 4 / 68 | 21 | 50 / 86 | 0.0% | 246 W (25%) | 1965 (100%) | 3 |
| baseline | SF100 | Spark GPU | 60 / 12 | 846 | 46 / 82 | 16.4% | 215 W (21%) | 1965 (100%) | 1 |
| baseline | SF300 | Polars GPU | 72 / 0 | 736 | 76 / 92 | 0.4% | 278 W (28%) | 1965 (100%) | 5 |
| baseline | SF300 | Spark GPU | 72 / 0 | 2391 | 49 / 79 | 12.4% | 218 W (22%) | 1965 (100%) | 1 |
| complex | SF30 | Polars GPU | 0 / 48 | 0 | – | – | – | – | – |
| complex | SF30 | Spark GPU | 48 / 24 | 609 | 24 / 98 | 28.9% | 207 W (21%) | 1965 (100%) | 0 |
| complex | SF100 | Polars GPU | 18 / 30 | 132 | 80 / 96 | 0.0% | 295 W (29%) | 1965 (100%) | 8 |
| complex | SF100 | Spark GPU | 72 / 0 | 2433 | 24 / 69 | 24.2% | 208 W (21%) | 1965 (100%) | 1 |
| complex | SF300 | Polars GPU | 48 / 0 | 874 | 73 / 95 | 0.2% | 339 W (34%) | 1965 (100%) | 7 |
| complex | SF300 | Spark GPU | 72 / 0 | 8511 | 19 / 64 | 34.8% | 207 W (21%) | 1965 (100%) | 0 |

descriptive telemetry; not a pre-registered contrast; no causal claim. nvidia-smi is sampled about every 200 ms, so an action of 1 s holds about 5 samples and the first and last sample of an action are partly outside it; actions shorter than 1 s (most SF1 actions) are left out of every statistic and counted in n_actions_excluded_short.
