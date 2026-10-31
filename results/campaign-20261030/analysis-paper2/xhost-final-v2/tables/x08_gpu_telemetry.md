### GPU telemetry inside measured actions of at least 1 s, per host (descriptive telemetry; not a pre-registered contrast; no causal claim)

| Suite | Scale | Engine | A100: GPU-idle / util median / power (of limit) | H100: GPU-idle / util median / power (of limit) | GH200: GPU-idle / util median / power (of limit) | B200: GPU-idle / util median / power (of limit) |
|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | – | – | – | – |
| baseline | SF1 | Spark GPU | 12% / 38% / 69 W (17%) | – | 30% / 32% / 135 W (19%) | – |
| baseline | SF10 | Polars GPU | – | – | – | – |
| baseline | SF10 | Spark GPU | 7% / 45% / 75 W (19%) | 13% / 49% / 124 W (18%) | 26% / 25% / 141 W (20%) | 14% / 34% / 202 W (20%) |
| baseline | SF30 | Polars GPU | – | – | – | – |
| baseline | SF30 | Spark GPU | 12% / 71% / 78 W (20%) | 16% / 70% / 135 W (19%) | 23% / 43% / 149 W (21%) | 15% / 44% / 209 W (21%) |
| baseline | SF100 | Polars GPU | 0% / 94% / 160 W (40%) | – | 12% / 74% / 237 W (34%) | 0% / 50% / 246 W (25%) |
| baseline | SF100 | Spark GPU | 9% / 81% / 92 W (23%) | 12% / 79% / 145 W (21%) | 21% / 54% / 159 W (23%) | 16% / 46% / 215 W (21%) |
| baseline | SF300 | Polars GPU | 0% / 89% / 155 W (39%) | 0% / 87% / 242 W (35%) | 1% / 77% / 251 W (36%) | 0% / 76% / 278 W (28%) |
| baseline | SF300 | Spark GPU | 6% / 84% / 96 W (24%) | 10% / 79% / 149 W (21%) | 13% / 64% / 165 W (24%) | 12% / 49% / 218 W (22%) |
| complex | SF30 | Polars GPU | – | – | – | – |
| complex | SF30 | Spark GPU | 23% / 45% / 76 W (19%) | 25% / 36% / 130 W (19%) | 38% / 19% / 146 W (21%) | 29% / 24% / 207 W (21%) |
| complex | SF100 | Polars GPU | 1% / 90% / 158 W (39%) | 0% / 90% / 254 W (36%) | 6% / 82% / 269 W (38%) | 0% / 80% / 295 W (29%) |
| complex | SF100 | Spark GPU | 17% / 47% / 78 W (20%) | 21% / 32% / 132 W (19%) | 29% / 28% / 157 W (22%) | 24% / 24% / 208 W (21%) |
| complex | SF300 | Polars GPU | 0% / 91% / 155 W (39%) | 1% / 86% / 285 W (41%) | 3% / 79% / 302 W (43%) | 0% / 73% / 339 W (34%) |
| complex | SF300 | Spark GPU | 23% / 36% / 70 W (18%) | 29% / 22% / 129 W (18%) | 25% / 44% / 163 W (23%) | 35% / 19% / 207 W (21%) |

descriptive telemetry; not a pre-registered contrast; no causal claim. GPU-idle = share of nvidia-smi samples (about every 200 ms) with utilization.gpu below 10% inside measured actions of at least 1 s; a dash: no action of that cell was at least 1 s long (or no gpu.csv). Power in watts and as a share of the card's current power limit.
