### exploratory (post-hoc; not in the locked design §9): Polars 2.0.0 CPU (side-study sessions) against the GPU engines of the main rounds, next to the locked-design contrasts

| Exploratory | Suite | Scale | Queries | Exploratory ratio [Welch 95% CI, unpaired] | Rounds (CPU side / GPU side) | Locked-design counterpart [t 95% CI over rounds] |
|---|---|---|---|---|---|---|
| E1 (exploratory) | baseline | SF1 | 6 | 0.295 [0.286, 0.304] | 4 / 4 | C1 0.330 [0.297, 0.366] (6 queries) |
| E1 (exploratory) | baseline | SF10 | 6 | 0.704 [0.686, 0.723] | 4 / 4 | C1 0.917 [0.885, 0.950] (6 queries) |
| E1 (exploratory) | baseline | SF30 | 6 | 1.07 [1.03, 1.12] | 4 / 4 | C1 1.51 [1.42, 1.61] (6 queries) |
| E1 (exploratory) | baseline | SF100 | 6 | 1.34 [1.31, 1.36] | 4 / 4 | C1 1.98 [1.86, 2.11] (6 queries) |
| E1 (exploratory) | baseline | SF300 | 6 | 1.32 [1.28, 1.35] | 4 / 4 | C1 2.07 [1.83, 2.34] (6 queries) |
| E1 (exploratory) | complex | SF30 | 4 | 1.69 [1.62, 1.77] | 4 / 4 | C1 2.87 [2.68, 3.06] (4 queries) |
| E1 (exploratory) | complex | SF100 | 4 | 2.06 [2.04, 2.08] | 4 / 4 | C1 3.57 [3.50, 3.65] (4 queries) |
| E1 (exploratory) | complex | SF300 | 4 | 1.84 [1.80, 1.89] | 4 / 4 | C1 3.32 [3.15, 3.49] (4 queries) |
| E4 (exploratory) | baseline | SF1 | 6 | 0.295 [0.286, 0.304] | 4 / 4 | C4 0.330 [0.297, 0.366] (6 queries) |
| E4 (exploratory) | baseline | SF10 | 6 | 0.704 [0.686, 0.723] | 4 / 4 | C4 0.917 [0.885, 0.950] (6 queries) |
| E4 (exploratory) | baseline | SF30 | 6 | 1.07 [1.03, 1.12] | 4 / 4 | C4 1.51 [1.42, 1.61] (6 queries) |
| E4 (exploratory) | baseline | SF100 | 6 | 1.34 [1.31, 1.36] | 4 / 4 | C4 1.98 [1.86, 2.11] (6 queries) |
| E4 (exploratory) | baseline | SF300 | 6 | 1.32 [1.28, 1.35] | 4 / 4 | C4 2.07 [1.83, 2.34] (6 queries) |
| E4 (exploratory) | complex | SF30 | 6 | 1.25 [1.21, 1.29] | 4 / 4 | C4 2.74 [2.62, 2.87] (6 queries) |
| E4 (exploratory) | complex | SF100 | 6 | 1.90 [1.88, 1.93] | 4 / 4 | C4 4.10 [4.09, 4.11] (6 queries) |
| E4 (exploratory) | complex | SF300 | 6 | 1.94 [1.89, 1.99] | 4 / 4 | C4 4.19 [4.15, 4.24] (6 queries) |

exploratory (post-hoc; not in the locked design §9). Ratio = time of the Polars CPU side-study sessions / time of the GPU side of the main rounds (above 1: the GPU side was faster, as for C1 and C4). Welch-type 95% interval on the log scale: the CPU times come from the side-study sessions and the GPU times from the main rounds, different sessions in different rounds, so the two sets of per-round values are unpaired (Welch-Satterthwaite df); no interval with fewer than two rounds on either side.
