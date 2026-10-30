### exploratory (post-hoc; not in the locked design §9): Polars 2.0.0 CPU (side-study sessions) against the GPU engines of the main rounds, next to the locked-design contrasts

| Exploratory | Suite | Scale | Queries | Exploratory ratio [Welch 95% CI, unpaired] | Rounds (CPU side / GPU side) | Locked-design counterpart [t 95% CI over rounds] |
|---|---|---|---|---|---|---|
| E1 (exploratory) | baseline | SF1 | 6 | 0.204 [0.200, 0.209] | 4 / 4 | C1 0.218 [0.214, 0.223] (6 queries) |
| E1 (exploratory) | baseline | SF10 | 6 | 0.646 [0.627, 0.666] | 4 / 4 | C1 0.764 [0.739, 0.791] (6 queries) |
| E1 (exploratory) | baseline | SF30 | 6 | 0.978 [0.966, 0.991] | 4 / 4 | C1 1.25 [1.24, 1.26] (6 queries) |
| E1 (exploratory) | baseline | SF100 | 6 | 1.22 [1.21, 1.24] | 4 / 4 | C1 1.61 [1.59, 1.63] (6 queries) |
| E1 (exploratory) | baseline | SF300 | 6 | 1.30 [1.29, 1.32] | 4 / 4 | C1 1.78 [1.76, 1.80] (6 queries) |
| E1 (exploratory) | complex | SF30 | 4 | 1.48 [1.35, 1.64] | 4 / 4 | C1 2.28 [2.07, 2.51] (4 queries) |
| E1 (exploratory) | complex | SF100 | 4 | 2.12 [2.07, 2.17] | 4 / 4 | C1 3.44 [3.30, 3.58] (4 queries) |
| E1 (exploratory) | complex | SF300 | 4 | 1.94 [1.91, 1.98] | 4 / 4 | C1 3.26 [3.20, 3.32] (4 queries) |
| E4 (exploratory) | baseline | SF1 | 6 | 0.204 [0.200, 0.209] | 4 / 4 | C4 0.218 [0.214, 0.223] (6 queries) |
| E4 (exploratory) | baseline | SF10 | 6 | 0.646 [0.627, 0.666] | 4 / 4 | C4 0.764 [0.739, 0.791] (6 queries) |
| E4 (exploratory) | baseline | SF30 | 6 | 0.978 [0.966, 0.991] | 4 / 4 | C4 1.25 [1.24, 1.26] (6 queries) |
| E4 (exploratory) | baseline | SF100 | 6 | 1.22 [1.21, 1.24] | 4 / 4 | C4 1.61 [1.59, 1.63] (6 queries) |
| E4 (exploratory) | baseline | SF300 | 6 | 1.30 [1.29, 1.32] | 4 / 4 | C4 1.78 [1.76, 1.80] (6 queries) |
| E4 (exploratory) | complex | SF30 | 6 | 1.04 [0.970, 1.12] | 4 / 4 | C4 2.24 [2.10, 2.40] (6 queries) |
| E4 (exploratory) | complex | SF100 | 6 | 1.98 [1.94, 2.02] | 4 / 4 | C4 4.07 [3.94, 4.20] (6 queries) |
| E4 (exploratory) | complex | SF300 | 6 | 2.13 [2.11, 2.16] | 4 / 4 | C4 4.34 [4.29, 4.38] (6 queries) |

exploratory (post-hoc; not in the locked design §9). Ratio = time of the Polars CPU side-study sessions / time of the GPU side of the main rounds (above 1: the GPU side was faster, as for C1 and C4). Welch-type 95% interval on the log scale: the CPU times come from the side-study sessions and the GPU times from the main rounds, different sessions in different rounds, so the two sets of per-round values are unpaired (Welch-Satterthwaite df); no interval with fewer than two rounds on either side.
