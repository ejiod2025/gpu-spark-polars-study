### exploratory (post-hoc; not in the locked design §9): Polars 2.0.0 CPU (side-study sessions) against the GPU engines of the main rounds, next to the locked-design contrasts

| Exploratory | Suite | Scale | Queries | Exploratory ratio [Welch 95% CI, unpaired] | Rounds (CPU side / GPU side) | Locked-design counterpart [t 95% CI over rounds] |
|---|---|---|---|---|---|---|
| E1 (exploratory) | baseline | SF1 | 6 | 0.426 [0.406, 0.448] | 4 / 4 | C1 0.403 [0.385, 0.421] (6 queries) |
| E1 (exploratory) | baseline | SF10 | 6 | 1.08 [1.05, 1.10] | 4 / 4 | C1 1.25 [1.21, 1.28] (6 queries) |
| E1 (exploratory) | baseline | SF30 | 6 | 1.54 [1.43, 1.65] | 4 / 4 | C1 1.95 [1.83, 2.09] (6 queries) |
| E1 (exploratory) | baseline | SF100 | 6 | 1.86 [1.83, 1.90] | 4 / 4 | C1 2.43 [2.38, 2.49] (6 queries) |
| E1 (exploratory) | baseline | SF300 | 6 | 1.97 [1.93, 2.00] | 4 / 4 | C1 2.76 [2.51, 3.03] (6 queries) |
| E1 (exploratory) | complex | SF30 | 4 | 2.13 [2.07, 2.18] | 4 / 4 | C1 3.30 [3.20, 3.40] (4 queries) |
| E1 (exploratory) | complex | SF100 | 4 | 2.68 [2.56, 2.80] | 4 / 4 | C1 4.39 [4.09, 4.72] (4 queries) |
| E1 (exploratory) | complex | SF300 | 4 | 2.33 [2.27, 2.39] | 4 / 4 | C1 3.93 [3.75, 4.12] (4 queries) |
| E4 (exploratory) | baseline | SF1 | 6 | 0.426 [0.406, 0.448] | 4 / 4 | C4 0.403 [0.385, 0.421] (6 queries) |
| E4 (exploratory) | baseline | SF10 | 6 | 1.08 [1.05, 1.10] | 4 / 4 | C4 1.25 [1.21, 1.28] (6 queries) |
| E4 (exploratory) | baseline | SF30 | 6 | 1.54 [1.43, 1.65] | 4 / 4 | C4 1.95 [1.83, 2.09] (6 queries) |
| E4 (exploratory) | baseline | SF100 | 6 | 1.86 [1.83, 1.90] | 4 / 4 | C4 2.43 [2.38, 2.49] (6 queries) |
| E4 (exploratory) | baseline | SF300 | 6 | 1.97 [1.93, 2.00] | 4 / 4 | C4 2.76 [2.51, 3.03] (6 queries) |
| E4 (exploratory) | complex | SF30 | 6 | 1.61 [1.59, 1.64] | 4 / 4 | C4 3.08 [3.01, 3.15] (6 queries) |
| E4 (exploratory) | complex | SF100 | 6 | 2.43 [2.34, 2.53] | 4 / 4 | C4 4.79 [4.56, 5.02] (6 queries) |
| E4 (exploratory) | complex | SF300 | 6 | 2.44 [2.36, 2.51] | 4 / 4 | C4 4.82 [4.66, 4.99] (6 queries) |

exploratory (post-hoc; not in the locked design §9). Ratio = time of the Polars CPU side-study sessions / time of the GPU side of the main rounds (above 1: the GPU side was faster, as for C1 and C4). Welch-type 95% interval on the log scale: the CPU times come from the side-study sessions and the GPU times from the main rounds, different sessions in different rounds, so the two sets of per-round values are unpaired (Welch-Satterthwaite df); no interval with fewer than two rounds on either side.
