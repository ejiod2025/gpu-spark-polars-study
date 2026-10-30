# Campaign statistics report

Source: `rerun/hosts/gh200/final/campaign-gh200`. Schema 1.0; keys documented in `STATS_SCHEMA.md`. Pre-registered plan: `docs/H100_EXPERIMENT_DESIGN.md` section 9.

## 1. Rounds

4 round(s) enter every estimate (df = 3, two-sided t critical value 3.18); trials per cell: [3]. With so few rounds the intervals are wide by construction; they are reported as estimates, not as tests, and no p-value is computed.

| Round | Status | Reason |
|---|---|---|
| round-1 | included | – |
| round-2 | included | – |
| round-3 | included | – |
| round-4 | included | – |

Loader warnings:

- meta.run_commit is null: run_campaign.py recorded no git commit (no git checkout on the host); the design commit was resolved from the design document's sha256

Observed engine order per round: round 1: spark_cpu → polars_gpu → spark_gpu → polars; round 2: polars_gpu → polars → spark_cpu → spark_gpu; round 3: polars → spark_gpu → polars_gpu → spark_cpu; round 4: spark_gpu → spark_cpu → polars → polars_gpu. Each engine occupies each position once: True; each ordered engine pair adjacent once: True.

## 2. Reading the ratios

A speedup is `t_ref / t_engine` per query and round, so above 1 the denominator engine is faster. The per-query estimate is the geometric mean over rounds with a 95% t-interval on the mean log-ratio (df = rounds − 1). The suite estimate is, per round, the geometric mean of the per-query ratios over queries available for both sides in every round, then the same interval across rounds. Round is the block.

## 3. Primary contrasts per suite and scale

### Polars CPU / Polars GPU (C1, RQ1)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.218 [0.214, 0.223] | 0.215–0.222 |
| baseline | SF10 | 6 | 0.764 [0.739, 0.791] | 0.744–0.783 |
| baseline | SF30 | 6 | 1.25 [1.24, 1.26] | 1.25–1.26 |
| baseline | SF100 | 6 | 1.61 [1.59, 1.63] | 1.59–1.62 |
| baseline | SF300 | 6 | 1.78 [1.76, 1.80] | 1.77–1.80 |
| complex | SF30 | 4 | 2.28 [2.07, 2.51] | 2.09–2.36 |
| complex | SF100 | 4 | 3.44 [3.30, 3.58] | 3.35–3.54 |
| complex | SF300 | 4 | 3.26 [3.20, 3.32] | 3.22–3.29 |

### Spark CPU / Spark GPU (C2, RQ1)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.581 [0.430, 0.786] | 0.490–0.731 |
| baseline | SF10 | 6 | 0.985 [0.906, 1.07] | 0.949–1.06 |
| baseline | SF30 | 6 | 1.59 [1.48, 1.70] | 1.50–1.66 |
| baseline | SF100 | 6 | 1.85 [1.70, 2.01] | 1.77–2.00 |
| baseline | SF300 | 6 | 1.82 [1.75, 1.90] | 1.76–1.87 |
| complex | SF30 | 6 | 2.54 [2.38, 2.72] | 2.42–2.68 |
| complex | SF100 | 6 | 3.28 [3.21, 3.35] | 3.23–3.34 |
| complex | SF300 | 6 | 3.10 [3.03, 3.18] | 3.04–3.14 |

### Spark GPU / Polars GPU (C3, RQ2)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 6.78 [6.54, 7.03] | 6.64–6.96 |
| baseline | SF10 | 6 | 6.39 [6.13, 6.65] | 6.17–6.57 |
| baseline | SF30 | 6 | 5.14 [5.06, 5.23] | 5.07–5.21 |
| baseline | SF100 | 6 | 4.02 [3.91, 4.12] | 3.93–4.08 |
| baseline | SF300 | 6 | 3.51 [3.49, 3.54] | 3.50–3.53 |
| complex | SF30 | 4 | 8.36 [7.74, 9.04] | 8.00–8.97 |
| complex | SF100 | 4 | 6.96 [6.57, 7.37] | 6.74–7.32 |
| complex | SF300 | 4 | 5.18 [5.02, 5.33] | 5.08–5.31 |

### Polars CPU / best GPU engine (C4, RQ2/RQ3)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.218 [0.214, 0.223] | 0.215–0.222 |
| baseline | SF10 | 6 | 0.764 [0.739, 0.791] | 0.744–0.783 |
| baseline | SF30 | 6 | 1.25 [1.24, 1.26] | 1.25–1.26 |
| baseline | SF100 | 6 | 1.61 [1.59, 1.63] | 1.59–1.62 |
| baseline | SF300 | 6 | 1.78 [1.76, 1.80] | 1.77–1.80 |
| complex | SF30 | 6 | 2.24 [2.10, 2.40] | 2.10–2.29 |
| complex | SF100 | 6 | 4.07 [3.94, 4.20] | 3.97–4.15 |
| complex | SF300 | 6 | 4.34 [4.29, 4.38] | 4.31–4.37 |

### Polars CPU / best GPU (both GPU engines supported) (C4 sensitivity, RQ3 sensitivity)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.218 [0.214, 0.223] | 0.215–0.222 |
| baseline | SF10 | 6 | 0.764 [0.739, 0.791] | 0.744–0.783 |
| baseline | SF30 | 6 | 1.25 [1.24, 1.26] | 1.25–1.26 |
| baseline | SF100 | 6 | 1.61 [1.59, 1.63] | 1.59–1.62 |
| baseline | SF300 | 6 | 1.78 [1.76, 1.80] | 1.77–1.80 |
| complex | SF30 | 4 | 2.28 [2.07, 2.51] | 2.09–2.36 |
| complex | SF100 | 4 | 3.44 [3.30, 3.58] | 3.35–3.54 |
| complex | SF300 | 4 | 3.26 [3.20, 3.32] | 3.22–3.29 |

### Interval tally

| Contrast | Suite | Scales with CI above 1 | Scales with CI below 1 | Scales with CI containing 1 |
|---|---|---|---|---|
| Polars CPU / Polars GPU | baseline | SF30, SF100, SF300 | SF1, SF10 | – |
| Polars CPU / Polars GPU | complex | SF30, SF100, SF300 | – | – |
| Spark CPU / Spark GPU | baseline | SF30, SF100, SF300 | SF1 | SF10 |
| Spark CPU / Spark GPU | complex | SF30, SF100, SF300 | – | – |
| Spark GPU / Polars GPU | baseline | SF1, SF10, SF30, SF100, SF300 | – | – |
| Spark GPU / Polars GPU | complex | SF30, SF100, SF300 | – | – |
| Polars CPU / best GPU engine | baseline | SF30, SF100, SF300 | SF1, SF10 | – |
| Polars CPU / best GPU engine | complex | SF30, SF100, SF300 | – | – |
| Polars CPU / best GPU (both GPU engines supported) | baseline | SF30, SF100, SF300 | SF1, SF10 | – |
| Polars CPU / best GPU (both GPU engines supported) | complex | SF30, SF100, SF300 | – | – |

## 4. RQ3 crossover

| Contrast | Suite | First scale CI > 1 | Stable from | First point estimate > 1 | Verdict per scale |
|---|---|---|---|---|---|
| C4 | baseline | SF30 | SF30 | SF30 | sf1:cpu faster; sf10:cpu faster; sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |
| C4 | complex | SF30 | SF30 | SF30 | sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |
| C4 (both GPU engines) | baseline | SF30 | SF30 | SF30 | sf1:cpu faster; sf10:cpu faster; sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |
| C4 (both GPU engines) | complex | SF30 | SF30 | SF30 | sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |

gpu faster: CI entirely above 1; cpu faster: entirely below 1; inconclusive: interval contains 1.

C4 takes Polars CPU as the CPU side (section 9). Spark CPU is faster than Polars CPU for: complex/SF30: cx_window; complex/SF100: cx_window; complex/SF300: cx_window.

## 5. RQ2 fastest engine

Count of queries on which each engine has the lowest geometric-mean time:

| Suite | Scale | Polars CPU | Polars GPU | Spark CPU | Spark GPU |
|---|---|---|---|---|---|
| baseline | SF1 | 6/6 | 0/6 | 0/6 | 0/6 |
| baseline | SF10 | 4/6 | 2/6 | 0/6 | 0/6 |
| baseline | SF30 | 2/6 | 4/6 | 0/6 | 0/6 |
| baseline | SF100 | 1/6 | 5/6 | 0/6 | 0/6 |
| baseline | SF300 | 1/6 | 5/6 | 0/6 | 0/6 |
| complex | SF30 | 1/6 | 4/6 | 0/6 | 1/6 |
| complex | SF100 | 0/6 | 4/6 | 0/6 | 2/6 |
| complex | SF300 | 0/6 | 4/6 | 0/6 | 2/6 |

## 6. RQ4 GPU coverage

| Suite | Query | Scale | Engine | Status | Reason class | Pre-declared |
|---|---|---|---|---|---|---|
| complex | cx_q13 | SF30 | Polars GPU | failed (0/4 rounds) | unsupported operation | yes |
| complex | cx_q13 | SF100 | Polars GPU | failed (0/4 rounds) | unsupported operation | yes |
| complex | cx_q13 | SF300 | Polars GPU | failed (0/4 rounds) | unsupported operation | yes |
| complex | cx_window | SF30 | Polars GPU | failed (0/4 rounds) | unsupported operation | yes |
| complex | cx_window | SF100 | Polars GPU | failed (0/4 rounds) | unsupported operation | yes |
| complex | cx_window | SF300 | Polars GPU | failed (0/4 rounds) | unsupported operation | yes |

### Unstable coverage (amendment A1)

No cell has a coverage outcome in some rounds and not in others.

### Plan variability (descriptive)

96 Spark cells (1536 trials with a saved plan, 64 sessions) were checked; 1 has more than one final adaptive plan. Descriptive. The final adaptive plan of every Spark trial (plans/<query>-<phase>-<trial>.txt) is normalized (expression, plan, stage and lore ids removed) and hashed; a cell is listed when its trials do not all end in the same plan. Medians, estimates and intervals are unchanged by this section: every measured trial still enters its cell's median as designed.

**complex SF30 cx_q09 (Spark GPU)**: 2 final plans in 16 trials (12 measured, 4 warm-up); initial plan identical in every trial.

- Plan a4362d8c1e3e against plan 5166c91b662d: exchanges `gpuhashpartitioning(l_partkey, 120)`: 1 to 0; shuffle readers `local`: 1 to 0.

| Suite | Scale | Query | Engine | Plan | Measured trials (per round) | Warm-ups | Measured s (min / median / max) | Exchanges | Shuffle readers | Stages / tasks (median per action) | Shuffle written (GiB, median) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| complex | SF30 | cx_q09 | Spark GPU | 5166c91b662d * | 12 (r1: 3; r2: 3; r3: 3; r4: 3) | 2 | 4.33/4.94/6.70 | 8 | coalesced 6, local 1, plain 1 | 13.0 / 338 | 5.80 |
| complex | SF30 | cx_q09 | Spark GPU | a4362d8c1e3e | 0 | 2 | – | 7 | coalesced 6, plain 1 | – | – |

* plan with the lowest median measured time in its cell (the reference for the listed changes).


## 7. Variability and order effects

| Engine | Within: median CV | Within: p90 | Within: max | Between: median CV | Between: p90 | Between: max |
|---|---|---|---|---|---|---|
| Polars CPU | 0.7% | 2.3% | 7.2% | 0.6% | 1.4% | 2.7% |
| Polars GPU | 2.1% | 15.7% | 35.7% | 1.2% | 6.6% | 25.8% |
| Spark CPU | 4.2% | 19.1% | 40.1% | 4.1% | 19.8% | 49.8% |
| Spark GPU | 2.1% | 4.2% | 25.3% | 1.1% | 3.7% | 12.0% |

Within-session CV uses the 3 measured trials of each cell and round; between-round CV uses the per-round medians of each cell.


| Position | Cells | Mean deviation [95% CI] |
|---|---|---|
| 1 | 186 | -0.5% [-2.4, +1.3] |
| 2 | 186 | -0.1% [-1.8, +1.6] |
| 3 | 186 | +0.3% [-0.5, +1.2] |
| 4 | 186 | +0.3% [-1.8, +2.5] |

Deviation of each cell's time from its own mean over rounds, averaged over cells, then a t-interval over rounds. Reported descriptively; the Williams design balances positions across engines.


| Predecessor | Cells | Mean deviation [95% CI] |
|---|---|---|
| none (first in block) | 186 | -0.5% [-2.4, +1.3] |
| Polars CPU | 138 | +0.6% [-1.3, +2.6] |
| Polars GPU | 144 | +1.0% [-2.0, +4.0] |
| Spark CPU | 138 | -0.5% [-1.0, -0.1] |
| Spark GPU | 138 | -0.4% [-2.5, +1.8] |


## 8. Legacy sum-of-medians totals (kept separate from the geometric means)

| Suite | Scale | Engine | Sum of medians (s) [min–max] |
|---|---|---|---|
| baseline | SF1 | Polars CPU | 0.108 [0.106–0.109] |
| baseline | SF1 | Polars GPU | 0.453 [0.447–0.461] |
| baseline | SF1 | Spark CPU | 1.93 [1.58–2.41] |
| baseline | SF1 | Spark GPU | 3.01 [2.99–3.03] |
| baseline | SF10 | Polars CPU | 0.618 [0.614–0.622] |
| baseline | SF10 | Polars GPU | 0.729 [0.713–0.755] |
| baseline | SF10 | Spark CPU | 5.16 [4.89–5.55] |
| baseline | SF10 | Spark GPU | 5.33 [5.29–5.36] |
| baseline | SF30 | Polars CPU | 2.08 [2.06–2.09] |
| baseline | SF30 | Polars GPU | 1.36 [1.34–1.38] |
| baseline | SF30 | Spark CPU | 13.6 [12.9–14.5] |
| baseline | SF30 | Spark GPU | 8.41 [8.34–8.45] |
| baseline | SF100 | Polars CPU | 7.30 [7.27–7.31] |
| baseline | SF100 | Polars GPU | 3.75 [3.70–3.81] |
| baseline | SF100 | Spark CPU | 33.7 [32.5–35.9] |
| baseline | SF100 | Spark GPU | 16.9 [16.7–17.1] |
| baseline | SF300 | Polars CPU | 24.2 [24.2–24.3] |
| baseline | SF300 | Polars GPU | 11.2 [11.2–11.3] |
| baseline | SF300 | Spark CPU | 86.9 [84.2–88.8] |
| baseline | SF300 | Spark GPU | 43.2 [42.8–44.1] |
| complex | SF30 | Polars CPU | 11.6 [11.6–11.7] |
| complex | SF30 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF30 | Spark CPU | 32.0 [31.2–33.0] |
| complex | SF30 | Spark GPU | 13.0 [12.2–13.9] |
| complex | SF100 | Polars CPU | 44.0 [43.6–44.6] |
| complex | SF100 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF100 | Spark CPU | 92.1 [91.3–92.6] |
| complex | SF100 | Spark GPU | 30.3 [29.4–32.4] |
| complex | SF300 | Polars CPU | 140 [140–141] |
| complex | SF300 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF300 | Spark CPU | 239 [238–240] |
| complex | SF300 | Spark GPU | 76.9 [75.4–78.9] |

Totals exist only when every query has a valid median in every round; never combined with the geometric means of t01.


| Suite | Scale | Contrast | Ratio of mean totals |
|---|---|---|---|
| baseline | SF1 | Polars CPU / Polars GPU | 0.238 |
| baseline | SF1 | Spark CPU / Spark GPU | 0.641 |
| baseline | SF1 | Spark GPU / Polars GPU | 6.65 |
| baseline | SF1 | Polars CPU / best GPU engine | 0.238 |
| baseline | SF1 | Polars CPU / best GPU (both GPU engines supported) | 0.238 |
| baseline | SF10 | Polars CPU / Polars GPU | 0.847 |
| baseline | SF10 | Spark CPU / Spark GPU | 0.969 |
| baseline | SF10 | Spark GPU / Polars GPU | 7.31 |
| baseline | SF10 | Polars CPU / best GPU engine | 0.847 |
| baseline | SF10 | Polars CPU / best GPU (both GPU engines supported) | 0.847 |
| baseline | SF30 | Polars CPU / Polars GPU | 1.53 |
| baseline | SF30 | Spark CPU / Spark GPU | 1.62 |
| baseline | SF30 | Spark GPU / Polars GPU | 6.18 |
| baseline | SF30 | Polars CPU / best GPU engine | 1.53 |
| baseline | SF30 | Polars CPU / best GPU (both GPU engines supported) | 1.53 |
| baseline | SF100 | Polars CPU / Polars GPU | 1.94 |
| baseline | SF100 | Spark CPU / Spark GPU | 2.00 |
| baseline | SF100 | Spark GPU / Polars GPU | 4.49 |
| baseline | SF100 | Polars CPU / best GPU engine | 1.94 |
| baseline | SF100 | Polars CPU / best GPU (both GPU engines supported) | 1.94 |
| baseline | SF300 | Polars CPU / Polars GPU | 2.15 |
| baseline | SF300 | Spark CPU / Spark GPU | 2.01 |
| baseline | SF300 | Spark GPU / Polars GPU | 3.84 |
| baseline | SF300 | Polars CPU / best GPU engine | 2.15 |
| baseline | SF300 | Polars CPU / best GPU (both GPU engines supported) | 2.15 |
| complex | SF30 | Polars CPU / Polars GPU | – |
| complex | SF30 | Spark CPU / Spark GPU | 2.46 |
| complex | SF30 | Spark GPU / Polars GPU | – |
| complex | SF30 | Polars CPU / best GPU engine | 0.894 |
| complex | SF30 | Polars CPU / best GPU (both GPU engines supported) | – |
| complex | SF100 | Polars CPU / Polars GPU | – |
| complex | SF100 | Spark CPU / Spark GPU | 3.04 |
| complex | SF100 | Spark GPU / Polars GPU | – |
| complex | SF100 | Polars CPU / best GPU engine | 1.45 |
| complex | SF100 | Polars CPU / best GPU (both GPU engines supported) | – |
| complex | SF300 | Polars CPU / Polars GPU | – |
| complex | SF300 | Spark CPU / Spark GPU | 3.11 |
| complex | SF300 | Spark GPU / Polars GPU | – |
| complex | SF300 | Polars CPU / best GPU engine | 1.82 |
| complex | SF300 | Polars CPU / best GPU (both GPU engines supported) | – |

Not comparable with the geometric means of t01: a sum is dominated by the longest queries.


## 9. Secondary metrics

### GPU memory per engine and scale (what is measured differs by engine: see the Measure column)

| Suite | Scale | Engine | Measure | Min–max over queries (GiB) | Median (GiB) | Max / device |
|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 2.2–2.2 | 2.2 | 2.3% |
| baseline | SF1 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| baseline | SF1 | Spark GPU | task-reported max (not a total) | 0.2–0.3 | 0.2 | 0.3% |
| baseline | SF10 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 10.1–10.5 | 10.3 | 11.0% |
| baseline | SF10 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| baseline | SF10 | Spark GPU | task-reported max (not a total) | 0.6–1.7 | 0.7 | 1.8% |
| baseline | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 11.7–11.8 | 11.8 | 12.4% |
| baseline | SF30 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| baseline | SF30 | Spark GPU | task-reported max (not a total) | 1.4–5.4 | 1.6 | 5.7% |
| baseline | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 21.1–21.2 | 21.1 | 22.2% |
| baseline | SF100 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| baseline | SF100 | Spark GPU | task-reported max (not a total) | 1.3–6.5 | 3.2 | 6.8% |
| baseline | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 29.8–29.8 | 29.8 | 31.2% |
| baseline | SF300 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| baseline | SF300 | Spark GPU | task-reported max (not a total) | 2.6–13.5 | 5.4 | 14.1% |
| complex | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 14.7–16.5 | 16.5 | 17.3% |
| complex | SF30 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| complex | SF30 | Spark GPU | task-reported max (not a total) | 0.6–5.8 | 2.4 | 6.1% |
| complex | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 22.0–22.0 | 22.0 | 23.0% |
| complex | SF100 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| complex | SF100 | Spark GPU | task-reported max (not a total) | 1.6–11.8 | 5.3 | 12.3% |
| complex | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 42.4–42.4 | 42.4 | 44.4% |
| complex | SF300 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| complex | SF300 | Spark GPU | task-reported max (not a total) | 1.8–28.3 | 5.5 | 29.6% |

max_bytes / median_bytes / min_bytes are the nvidia-smi samples of the worker process tree during the timed action (worker peak_gpu_memory_bytes); maximum over the 3 measured trials, then over rounds; a sampled peak can miss short transients. What the sample means differs by engine: see measure / measure_note, and the task_reported blocks for Spark GPU Polars GPU: nvidia-smi device memory of the worker process, sampled during the timed action. cudf-polars allocates through an RMM async pool that keeps freed blocks (release threshold 90% of the device), so the value is the pool's high-water mark in that session so far, not the working set of the query: it never falls within a session (see high_water_check) and an upper bound on what the query needs. Spark GPU: the largest device allocation a single task reported (RAPIDS task accumulator gpuMaxDeviceMemoryBytes, Spark event log) among the tasks launched inside the action; not a simultaneous total over concurrent tasks. The nvidia-smi samples (max_bytes, ...) are the RAPIDS pool reservation made at plugin start, not memory in use.

### Process wall time and startup (mean over rounds, seconds; overlapping scopes, do not add)

| Suite | Scale | Engine | Process wall | Launch → ready | Setup | Warmups | Measured |
|---|---|---|---|---|---|---|---|
| baseline | SF1 | Polars CPU | 0.678 | 0.104 | 0.0581 | 0.117 | 0.322 |
| baseline | SF1 | Polars GPU | 4.43 | 1.24 | 1.19 | 0.710 | 1.39 |
| baseline | SF1 | Spark CPU | 16.5 | 3.94 | 2.99 | 5.71 | 6.03 |
| baseline | SF1 | Spark GPU | 39.9 | 21.7 | 20.6 | 5.70 | 9.08 |
| baseline | SF10 | Polars CPU | 2.77 | 0.102 | 0.0570 | 0.629 | 1.86 |
| baseline | SF10 | Polars GPU | 6.67 | 1.24 | 1.19 | 1.81 | 2.26 |
| baseline | SF10 | Spark CPU | 31.4 | 3.93 | 2.98 | 10.5 | 15.6 |
| baseline | SF10 | Spark GPU | 49.1 | 21.7 | 20.6 | 8.32 | 16.0 |
| baseline | SF30 | Polars CPU | 8.59 | 0.101 | 0.0562 | 2.08 | 6.25 |
| baseline | SF30 | Polars GPU | 9.20 | 1.25 | 1.20 | 2.52 | 4.16 |
| baseline | SF30 | Spark CPU | 64.5 | 3.94 | 2.99 | 18.2 | 40.7 |
| baseline | SF30 | Spark GPU | 61.5 | 21.7 | 20.6 | 11.2 | 25.3 |
| baseline | SF100 | Polars CPU | 29.5 | 0.102 | 0.0566 | 7.26 | 21.9 |
| baseline | SF100 | Polars GPU | 19.0 | 1.27 | 1.22 | 5.15 | 11.2 |
| baseline | SF100 | Spark CPU | 154 | 3.94 | 2.99 | 42.9 | 103 |
| baseline | SF100 | Spark GPU | 98.3 | 21.7 | 20.7 | 19.7 | 50.9 |
| baseline | SF300 | Polars CPU | 97.0 | 0.101 | 0.0557 | 24.2 | 72.5 |
| baseline | SF300 | Polars GPU | 50.5 | 1.27 | 1.23 | 14.1 | 33.9 |
| baseline | SF300 | Spark CPU | 373 | 3.97 | 3.01 | 91.4 | 263 |
| baseline | SF300 | Spark GPU | 216 | 21.8 | 20.7 | 45.5 | 132 |
| complex | SF30 | Polars CPU | 47.4 | 0.102 | 0.0568 | 11.7 | 35.0 |
| complex | SF30 | Polars GPU | 8.94 | 1.25 | 1.21 | 2.64 | 3.63 |
| complex | SF30 | Spark CPU | 195 | 4.08 | 3.12 | 35.7 | 95.6 |
| complex | SF30 | Spark GPU | 139 | 21.9 | 20.8 | 14.3 | 39.3 |
| complex | SF100 | Polars CPU | 177 | 0.104 | 0.0576 | 44.0 | 132 |
| complex | SF100 | Polars GPU | 18.4 | 1.27 | 1.23 | 5.65 | 10.00 |
| complex | SF100 | Spark CPU | 446 | 4.07 | 3.12 | 95.6 | 279 |
| complex | SF100 | Spark GPU | 218 | 21.9 | 20.8 | 35.5 | 91.5 |
| complex | SF300 | Polars CPU | 563 | 0.103 | 0.0570 | 140 | 422 |
| complex | SF300 | Polars GPU | 53.4 | 1.27 | 1.22 | 16.3 | 34.3 |
| complex | SF300 | Spark CPU | 1051 | 4.11 | 3.15 | 238 | 718 |
| complex | SF300 | Spark GPU | 426 | 21.9 | 20.8 | 82.6 | 231 |

### verified_gc cleanup cost (outside the query timers)

| Suite | Scale | Engine | Per cleanup (s) | Max (s) | Per session (s) | Share of wall |
|---|---|---|---|---|---|---|
| complex | SF30 | Polars CPU | 0.00563 | 0.00617 | 0.135 | 0.3% |
| complex | SF30 | Polars GPU | 0.0171 | 0.0185 | 0.274 | 3.1% |
| complex | SF30 | Spark CPU | 2.43 | 2.68 | 58.3 | 29.8% |
| complex | SF30 | Spark GPU | 2.50 | 2.77 | 59.9 | 43.1% |
| complex | SF100 | Polars CPU | 0.00571 | 0.00653 | 0.137 | 0.1% |
| complex | SF100 | Polars GPU | 0.0188 | 0.0201 | 0.300 | 1.6% |
| complex | SF100 | Spark CPU | 2.71 | 4.52 | 65.0 | 14.6% |
| complex | SF100 | Spark GPU | 2.70 | 4.64 | 64.9 | 29.8% |
| complex | SF300 | Polars CPU | 0.00577 | 0.00678 | 0.139 | 0.0% |
| complex | SF300 | Polars GPU | 0.0192 | 0.0214 | 0.308 | 0.6% |
| complex | SF300 | Spark CPU | 3.64 | 7.15 | 87.5 | 8.3% |
| complex | SF300 | Spark GPU | 3.41 | 6.98 | 81.9 | 19.2% |

### GPU board energy proxy per session (mean power.draw × process wall time)

| Suite | Scale | Engine | Mean power (W) | Wall (s) | Energy (Wh) |
|---|---|---|---|---|---|
| baseline | SF1 | Polars CPU | 126 | 0.678 | 0.0237 |
| baseline | SF1 | Polars GPU | 129 | 4.43 | 0.159 |
| baseline | SF1 | Spark CPU | 94.2 | 16.5 | 0.432 |
| baseline | SF1 | Spark GPU | 125 | 39.9 | 1.39 |
| baseline | SF10 | Polars CPU | 108 | 2.77 | 0.0828 |
| baseline | SF10 | Polars GPU | 149 | 6.67 | 0.276 |
| baseline | SF10 | Spark CPU | 92.9 | 31.4 | 0.811 |
| baseline | SF10 | Spark GPU | 131 | 49.1 | 1.78 |
| baseline | SF30 | Polars CPU | 96.8 | 8.59 | 0.231 |
| baseline | SF30 | Polars GPU | 174 | 9.20 | 0.446 |
| baseline | SF30 | Spark CPU | 92.8 | 64.5 | 1.66 |
| baseline | SF30 | Spark GPU | 138 | 61.5 | 2.36 |
| baseline | SF100 | Polars CPU | 94.4 | 29.5 | 0.772 |
| baseline | SF100 | Polars GPU | 214 | 19.0 | 1.13 |
| baseline | SF100 | Spark CPU | 93.4 | 154 | 3.99 |
| baseline | SF100 | Spark GPU | 152 | 98.3 | 4.14 |
| baseline | SF300 | Polars CPU | 94.2 | 97.0 | 2.54 |
| baseline | SF300 | Polars GPU | 249 | 50.5 | 3.49 |
| baseline | SF300 | Spark CPU | 94.5 | 373 | 9.78 |
| baseline | SF300 | Spark GPU | 163 | 216 | 9.76 |
| complex | SF30 | Polars CPU | 90.8 | 47.4 | 1.20 |
| complex | SF30 | Polars GPU | 181 | 8.94 | 0.449 |
| complex | SF30 | Spark CPU | 90.6 | 195 | 4.92 |
| complex | SF30 | Spark GPU | 137 | 139 | 5.30 |
| complex | SF100 | Polars CPU | 90.5 | 177 | 4.45 |
| complex | SF100 | Polars GPU | 232 | 18.4 | 1.19 |
| complex | SF100 | Spark CPU | 91.9 | 446 | 11.4 |
| complex | SF100 | Spark GPU | 148 | 218 | 8.94 |
| complex | SF300 | Polars CPU | 90.5 | 563 | 14.2 |
| complex | SF300 | Polars GPU | 287 | 53.4 | 4.26 |
| complex | SF300 | Spark CPU | 92.9 | 1051 | 27.1 |
| complex | SF300 | Spark GPU | 159 | 426 | 18.8 |

GPU board energy proxy = mean nvidia-smi power.draw over the session window x process wall time; CPU and host power are not included, and CPU-only engines show the idle GPU draw

### GPU telemetry inside measured actions (descriptive telemetry; not a pre-registered contrast; no causal claim)

For each GPU engine, suite and scale: the nvidia-smi samples (utilization.gpu, utilization.memory, power.draw, clocks.current.sm) that fall inside the measured actions of the accepted rounds, pooled over queries, trials and rounds. idle_fraction is the share of samples with utilization.gpu below 10% (equal to the share of action wall time at uniform sampling). utilization.gpu is nvidia-smi's share of the sample period in which at least one kernel was executing, not SM occupancy. Descriptive; not a pre-registered contrast; no causal claim. Nvidia-smi is sampled about every 200 ms, so an action of 1 s holds about 5 samples and the first and last sample of an action are partly outside it; actions shorter than 1 s (most SF1 actions) are left out of every statistic and counted in n_actions_excluded_short. Power limit 700 W (nvidia-smi -q, Current Power Limit of the first GPU); maximum SM clock 1980 MHz.


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


## 10. RQ5 Polars version bridge

| Suite | Scale | Queries | Ratio 2.0.0 / 1.44.2 [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.943 [0.933, 0.953] | 0.935–0.949 |
| baseline | SF10 | 6 | 0.848 [0.837, 0.859] | 0.841–0.854 |
| baseline | SF30 | 6 | 0.785 [0.781, 0.788] | 0.783–0.787 |
| baseline | SF100 | 6 | 0.761 [0.756, 0.767] | 0.759–0.766 |
| baseline | SF300 | 6 | 0.734 [0.728, 0.740] | 0.730–0.739 |
| complex | SF30 | 6 | 0.465 [0.462, 0.467] | 0.462–0.466 |
| complex | SF100 | 6 | 0.485 [0.476, 0.493] | 0.478–0.490 |
| complex | SF300 | 6 | 0.493 [0.488, 0.499] | 0.489–0.497 |


### EXPLORATORY (post-hoc; not in the locked design §9): Polars 2.0.0 CPU against the GPU engines

**This is not part of the pre-registered analysis (design section 9) and is never used by C1-C4 or the crossover verdict.** E1 = time of Polars 2.0.0 CPU (side-study rounds [1, 2, 3, 4]) / time of Polars GPU (main rounds [1, 2, 3, 4]); E4 = the same CPU time / the better GPU engine per query and round. Orientation as for C1 and C4: above 1 the GPU side was faster. The GPU comparisons of the locked design use Polars 1.44.2 on the CPU side (C1, C4), which is why the version bridge above matters. Welch-type 95% interval on the log scale: the CPU times come from the side-study sessions and the GPU times from the main rounds, different sessions in different rounds, so the two sets of per-round values are unpaired (Welch-Satterthwaite df); no interval with fewer than two rounds on either side.

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


## 11. A100 → H100 (GPU engines only; non-equivalent hosts)

| Engine | Suite | Scale | Queries | A100 / H100 time [95% CI over H100 rounds] |
|---|---|---|---|---|

time on A100 / time on H100 for the same GPU engine, query and scale; above 1 means the H100 host was faster. The A100 study is a single session per cell (no rounds), so the interval reflects the H100 round-to-round variation only. Hosts differ in GPU, vCPU count, RAM and software versions: not an equivalent comparison. The A100 side is the previous study's saved evidence (see study.label and study.differences), not the A100 campaign.


## 12. Caveats

- Four rounds give df = 3; intervals are wide and are not hypothesis tests. Multiplicity across contrasts, suites and scales is not adjusted.
- Round is a block: all engines run once per round, so round-level drift cancels in each per-round ratio. Session-level effects remain inside the interval.
- C4 uses, per query and round, the faster of the GPU engines that produced a timing; a query that only one GPU engine supports is included with that engine. `c4_strict` restricts to queries both GPU engines ran. C1, C3 and `c4_strict` exclude queries unsupported on Polars GPU; they stay visible in the coverage table.
- Geometric means weight queries equally; the legacy sums of medians are dominated by the longest queries. They answer different questions and are never mixed.
- Peak GPU memory is a sampled per-process-tree value; the energy proxy is GPU board power only (host CPU power excluded; CPU engines show idle GPU draw).
- The A100 study is one session per cell and ran on different hardware, vCPU count and software: its ratios to H100 describe a host change, not an architecture effect.
