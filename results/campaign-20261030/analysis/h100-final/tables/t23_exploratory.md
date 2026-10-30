### exploratory (post-hoc; not in the locked design §9): Polars 2.0.0 CPU (side-study sessions) against the GPU engines of the main rounds, next to the locked-design contrasts

| Exploratory | Suite | Scale | Queries | Exploratory ratio [Welch 95% CI, unpaired] | Rounds (CPU side / GPU side) | Locked-design counterpart [t 95% CI over rounds] |
|---|---|---|---|---|---|---|
| E1 (exploratory) | baseline | SF1 | 6 | 0.335 [0.311, 0.360] | 4 / 4 | C1 0.350 [0.320, 0.382] (6 queries) |
| E1 (exploratory) | baseline | SF10 | 6 | 1.11 [1.05, 1.17] | 4 / 4 | C1 1.23 [1.17, 1.29] (6 queries) |
| E1 (exploratory) | baseline | SF30 | 6 | 1.60 [1.52, 1.67] | 4 / 4 | C1 1.97 [1.92, 2.03] (6 queries) |
| E1 (exploratory) | baseline | SF100 | 6 | 2.01 [1.95, 2.08] | 4 / 4 | C1 2.56 [2.50, 2.63] (6 queries) |
| E1 (exploratory) | baseline | SF300 | 6 | 2.09 [2.05, 2.14] | 4 / 4 | C1 2.81 [2.64, 2.99] (6 queries) |
| E1 (exploratory) | complex | SF30 | 4 | 2.34 [2.28, 2.40] | 4 / 4 | C1 3.66 [3.54, 3.79] (4 queries) |
| E1 (exploratory) | complex | SF100 | 4 | 3.12 [3.09, 3.16] | 4 / 4 | C1 4.88 [4.80, 4.96] (4 queries) |
| E1 (exploratory) | complex | SF300 | 4 | 2.84 [2.80, 2.88] | 4 / 4 | C1 4.70 [4.47, 4.94] (4 queries) |
| E4 (exploratory) | baseline | SF1 | 6 | 0.335 [0.311, 0.360] | 4 / 4 | C4 0.350 [0.320, 0.382] (6 queries) |
| E4 (exploratory) | baseline | SF10 | 6 | 1.11 [1.05, 1.17] | 4 / 4 | C4 1.23 [1.17, 1.29] (6 queries) |
| E4 (exploratory) | baseline | SF30 | 6 | 1.60 [1.52, 1.67] | 4 / 4 | C4 1.97 [1.92, 2.03] (6 queries) |
| E4 (exploratory) | baseline | SF100 | 6 | 2.01 [1.95, 2.08] | 4 / 4 | C4 2.56 [2.50, 2.63] (6 queries) |
| E4 (exploratory) | baseline | SF300 | 6 | 2.09 [2.05, 2.14] | 4 / 4 | C4 2.81 [2.64, 2.99] (6 queries) |
| E4 (exploratory) | complex | SF30 | 6 | 1.73 [1.70, 1.76] | 4 / 4 | C4 3.34 [3.26, 3.43] (6 queries) |
| E4 (exploratory) | complex | SF100 | 6 | 2.77 [2.74, 2.80] | 4 / 4 | C4 5.25 [5.17, 5.32] (6 queries) |
| E4 (exploratory) | complex | SF300 | 6 | 2.88 [2.85, 2.91] | 4 / 4 | C4 5.64 [5.39, 5.91] (6 queries) |

exploratory (post-hoc; not in the locked design §9). Ratio = time of the Polars CPU side-study sessions / time of the GPU side of the main rounds (above 1: the GPU side was faster, as for C1 and C4). Welch-type 95% interval on the log scale: the CPU times come from the side-study sessions and the GPU times from the main rounds, different sessions in different rounds, so the two sets of per-round values are unpaired (Welch-Satterthwaite df); no interval with fewer than two rounds on either side.
