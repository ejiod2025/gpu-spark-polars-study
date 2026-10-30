# Campaign statistics report

Source: `rerun/hosts/b200/campaign-b200`. Schema 1.0; keys documented in `STATS_SCHEMA.md`. Pre-registered plan: `docs/H100_EXPERIMENT_DESIGN.md` section 9.

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
| baseline | SF1 | 6 | 0.403 [0.385, 0.421] | 0.393–0.415 |
| baseline | SF10 | 6 | 1.25 [1.21, 1.28] | 1.23–1.27 |
| baseline | SF30 | 6 | 1.95 [1.83, 2.09] | 1.84–2.01 |
| baseline | SF100 | 6 | 2.43 [2.38, 2.49] | 2.38–2.46 |
| baseline | SF300 | 6 | 2.76 [2.51, 3.03] | 2.63–3.01 |
| complex | SF30 | 4 | 3.30 [3.20, 3.40] | 3.24–3.38 |
| complex | SF100 | 4 | 4.39 [4.09, 4.72] | 4.22–4.65 |
| complex | SF300 | 4 | 3.93 [3.75, 4.12] | 3.83–4.07 |

### Spark CPU / Spark GPU (C2, RQ1)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.859 [0.692, 1.07] | 0.758–0.980 |
| baseline | SF10 | 6 | 1.70 [1.55, 1.87] | 1.59–1.80 |
| baseline | SF30 | 6 | 2.63 [2.26, 3.06] | 2.39–2.96 |
| baseline | SF100 | 6 | 3.26 [3.03, 3.51] | 3.15–3.48 |
| baseline | SF300 | 6 | 3.32 [3.14, 3.50] | 3.24–3.49 |
| complex | SF30 | 6 | 3.94 [3.65, 4.25] | 3.76–4.16 |
| complex | SF100 | 6 | 4.41 [4.17, 4.66] | 4.29–4.62 |
| complex | SF300 | 6 | 3.76 [3.70, 3.83] | 3.71–3.81 |

### Spark GPU / Polars GPU (C3, RQ2)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 5.33 [5.19, 5.47] | 5.22–5.43 |
| baseline | SF10 | 6 | 4.46 [4.36, 4.58] | 4.39–4.53 |
| baseline | SF30 | 6 | 3.79 [3.54, 4.05] | 3.56–3.92 |
| baseline | SF100 | 6 | 3.13 [3.06, 3.20] | 3.08–3.18 |
| baseline | SF300 | 6 | 2.79 [2.75, 2.84] | 2.76–2.83 |
| complex | SF30 | 4 | 6.22 [6.05, 6.40] | 6.07–6.31 |
| complex | SF100 | 4 | 5.66 [5.41, 5.92] | 5.51–5.89 |
| complex | SF300 | 4 | 4.72 [4.60, 4.85] | 4.64–4.81 |

### Polars CPU / best GPU engine (C4, RQ2/RQ3)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.403 [0.385, 0.421] | 0.393–0.415 |
| baseline | SF10 | 6 | 1.25 [1.21, 1.28] | 1.23–1.27 |
| baseline | SF30 | 6 | 1.95 [1.83, 2.09] | 1.84–2.01 |
| baseline | SF100 | 6 | 2.43 [2.38, 2.49] | 2.38–2.46 |
| baseline | SF300 | 6 | 2.76 [2.51, 3.03] | 2.63–3.01 |
| complex | SF30 | 6 | 3.08 [3.01, 3.15] | 3.03–3.13 |
| complex | SF100 | 6 | 4.79 [4.56, 5.02] | 4.66–4.98 |
| complex | SF300 | 6 | 4.82 [4.66, 4.99] | 4.74–4.96 |

### Polars CPU / best GPU (both GPU engines supported) (C4 sensitivity, RQ3 sensitivity)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.403 [0.385, 0.421] | 0.393–0.415 |
| baseline | SF10 | 6 | 1.25 [1.21, 1.28] | 1.23–1.27 |
| baseline | SF30 | 6 | 1.95 [1.83, 2.09] | 1.84–2.01 |
| baseline | SF100 | 6 | 2.43 [2.38, 2.49] | 2.38–2.46 |
| baseline | SF300 | 6 | 2.76 [2.51, 3.03] | 2.63–3.01 |
| complex | SF30 | 4 | 3.30 [3.20, 3.40] | 3.24–3.38 |
| complex | SF100 | 4 | 4.39 [4.09, 4.72] | 4.22–4.65 |
| complex | SF300 | 4 | 3.93 [3.75, 4.12] | 3.83–4.07 |

### Interval tally

| Contrast | Suite | Scales with CI above 1 | Scales with CI below 1 | Scales with CI containing 1 |
|---|---|---|---|---|
| Polars CPU / Polars GPU | baseline | SF10, SF30, SF100, SF300 | SF1 | – |
| Polars CPU / Polars GPU | complex | SF30, SF100, SF300 | – | – |
| Spark CPU / Spark GPU | baseline | SF10, SF30, SF100, SF300 | – | SF1 |
| Spark CPU / Spark GPU | complex | SF30, SF100, SF300 | – | – |
| Spark GPU / Polars GPU | baseline | SF1, SF10, SF30, SF100, SF300 | – | – |
| Spark GPU / Polars GPU | complex | SF30, SF100, SF300 | – | – |
| Polars CPU / best GPU engine | baseline | SF10, SF30, SF100, SF300 | SF1 | – |
| Polars CPU / best GPU engine | complex | SF30, SF100, SF300 | – | – |
| Polars CPU / best GPU (both GPU engines supported) | baseline | SF10, SF30, SF100, SF300 | SF1 | – |
| Polars CPU / best GPU (both GPU engines supported) | complex | SF30, SF100, SF300 | – | – |

## 4. RQ3 crossover

| Contrast | Suite | First scale CI > 1 | Stable from | First point estimate > 1 | Verdict per scale |
|---|---|---|---|---|---|
| C4 | baseline | SF10 | SF10 | SF10 | sf1:cpu faster; sf10:gpu faster; sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |
| C4 | complex | SF30 | SF30 | SF30 | sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |
| C4 (both GPU engines) | baseline | SF10 | SF10 | SF10 | sf1:cpu faster; sf10:gpu faster; sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |
| C4 (both GPU engines) | complex | SF30 | SF30 | SF30 | sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |

gpu faster: CI entirely above 1; cpu faster: entirely below 1; inconclusive: interval contains 1.

C4 takes Polars CPU as the CPU side (section 9). Spark CPU is faster than Polars CPU for: complex/SF30: cx_window; complex/SF100: cx_window; complex/SF300: cx_window.

## 5. RQ2 fastest engine

Count of queries on which each engine has the lowest geometric-mean time:

| Suite | Scale | Polars CPU | Polars GPU | Spark CPU | Spark GPU |
|---|---|---|---|---|---|
| baseline | SF1 | 6/6 | 0/6 | 0/6 | 0/6 |
| baseline | SF10 | 2/6 | 4/6 | 0/6 | 0/6 |
| baseline | SF30 | 1/6 | 5/6 | 0/6 | 0/6 |
| baseline | SF100 | 0/6 | 6/6 | 0/6 | 0/6 |
| baseline | SF300 | 0/6 | 6/6 | 0/6 | 0/6 |
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

**complex SF30 cx_q09 (Spark GPU)**: 2 final plans in 16 trials (12 measured, 4 warm-up); initial plan identical in every trial. The measured times of the plans do not overlap (median of the slowest over the fastest plan: 2.20).

- Plan 5166c91b662d against plan a4362d8c1e3e: exchanges `gpuhashpartitioning(l_partkey, 120)`: 0 to 1; shuffle readers `local`: 0 to 1. Operator lines only in this plan: `GpuColumnarExchange gpuhashpartitioning(l_partkey, 120, Murmur3Mode), ENSURE_REQUIREMENTS, [plan_id=N]`, `GpuCustomShuffleReader local`, `GpuShuffleCoalesce 1073741824`, `ShuffleQueryStage N`. Per action (Spark event log, median): stages 12.0 to 13.0; tasks 217 to 335; shuffle bytes written 1.11 GiB to 5.80 GiB.

| Suite | Scale | Query | Engine | Plan | Measured trials (per round) | Warm-ups | Measured s (min / median / max) | Exchanges | Shuffle readers | Stages / tasks (median per action) | Shuffle written (GiB, median) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| complex | SF30 | cx_q09 | Spark GPU | a4362d8c1e3e * | 4 (r1: 1; r2: 1; r3: 1; r4: 1) | 4 | 2.18/2.24/2.29 | 7 | coalesced 6, plain 1 | 12.0 / 217 | 1.11 |
| complex | SF30 | cx_q09 | Spark GPU | 5166c91b662d | 8 (r1: 2; r2: 2; r3: 2; r4: 2) | 0 | 4.70/4.93/5.11 | 8 | coalesced 6, local 1, plain 1 | 13.0 / 335 | 5.80 |

* plan with the lowest median measured time in its cell (the reference for the listed changes).


## 7. Variability and order effects

| Engine | Within: median CV | Within: p90 | Within: max | Between: median CV | Between: p90 | Between: max |
|---|---|---|---|---|---|---|
| Polars CPU | 3.3% | 12.6% | 75.5% | 2.1% | 6.6% | 24.8% |
| Polars GPU | 3.4% | 10.4% | 18.7% | 2.8% | 7.0% | 16.8% |
| Spark CPU | 4.5% | 16.4% | 40.2% | 5.8% | 15.7% | 34.0% |
| Spark GPU | 3.1% | 8.2% | 39.9% | 1.6% | 3.8% | 9.2% |

Within-session CV uses the 3 measured trials of each cell and round; between-round CV uses the per-round medians of each cell.


| Position | Cells | Mean deviation [95% CI] |
|---|---|---|
| 1 | 186 | +0.9% [-2.4, +4.2] |
| 2 | 186 | -1.2% [-5.4, +3.1] |
| 3 | 186 | +0.1% [-2.3, +2.6] |
| 4 | 186 | +0.2% [-1.8, +2.4] |

Deviation of each cell's time from its own mean over rounds, averaged over cells, then a t-interval over rounds. Reported descriptively; the Williams design balances positions across engines.


| Predecessor | Cells | Mean deviation [95% CI] |
|---|---|---|
| none (first in block) | 186 | +0.9% [-2.4, +4.2] |
| Polars CPU | 138 | +0.6% [-2.2, +3.6] |
| Polars GPU | 144 | -0.3% [-2.4, +1.8] |
| Spark CPU | 138 | -0.2% [-3.2, +2.8] |
| Spark GPU | 138 | -1.2% [-9.8, +8.2] |


## 8. Legacy sum-of-medians totals (kept separate from the geometric means)

| Suite | Scale | Engine | Sum of medians (s) [min–max] |
|---|---|---|---|
| baseline | SF1 | Polars CPU | 0.198 [0.192–0.205] |
| baseline | SF1 | Polars GPU | 0.431 [0.426–0.434] |
| baseline | SF1 | Spark CPU | 2.13 [1.79–2.45] |
| baseline | SF1 | Spark GPU | 2.31 [2.29–2.34] |
| baseline | SF10 | Polars CPU | 1.20 [1.19–1.23] |
| baseline | SF10 | Polars GPU | 0.785 [0.765–0.795] |
| baseline | SF10 | Spark CPU | 6.80 [6.42–7.21] |
| baseline | SF10 | Spark GPU | 4.01 [3.97–4.06] |
| baseline | SF30 | Polars CPU | 3.78 [3.74–3.81] |
| baseline | SF30 | Polars GPU | 1.50 [1.45–1.61] |
| baseline | SF30 | Spark CPU | 17.9 [16.3–19.6] |
| baseline | SF30 | Spark GPU | 6.81 [6.78–6.84] |
| baseline | SF100 | Polars CPU | 12.6 [12.6–12.6] |
| baseline | SF100 | Polars GPU | 4.08 [4.03–4.17] |
| baseline | SF100 | Spark CPU | 50.8 [49.5–53.2] |
| baseline | SF100 | Spark GPU | 14.7 [14.6–14.9] |
| baseline | SF300 | Polars CPU | 42.3 [41.4–44.1] |
| baseline | SF300 | Polars GPU | 12.3 [12.2–12.4] |
| baseline | SF300 | Spark CPU | 137 [131–144] |
| baseline | SF300 | Spark GPU | 40.0 [38.7–40.7] |
| complex | SF30 | Polars CPU | 12.9 [12.7–13.0] |
| complex | SF30 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF30 | Spark CPU | 44.4 [42.5–46.9] |
| complex | SF30 | Spark GPU | 12.1 [12.1–12.2] |
| complex | SF100 | Polars CPU | 51.5 [50.6–53.8] |
| complex | SF100 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF100 | Spark CPU | 131 [129–137] |
| complex | SF100 | Spark GPU | 40.1 [39.4–41.0] |
| complex | SF300 | Polars CPU | 162 [161–164] |
| complex | SF300 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF300 | Spark CPU | 340 [339–343] |
| complex | SF300 | Spark GPU | 142 [141–143] |

Totals exist only when every query has a valid median in every round; never combined with the geometric means of t01.


| Suite | Scale | Contrast | Ratio of mean totals |
|---|---|---|---|
| baseline | SF1 | Polars CPU / Polars GPU | 0.458 |
| baseline | SF1 | Spark CPU / Spark GPU | 0.922 |
| baseline | SF1 | Spark GPU / Polars GPU | 5.36 |
| baseline | SF1 | Polars CPU / best GPU engine | 0.458 |
| baseline | SF1 | Polars CPU / best GPU (both GPU engines supported) | 0.458 |
| baseline | SF10 | Polars CPU / Polars GPU | 1.53 |
| baseline | SF10 | Spark CPU / Spark GPU | 1.70 |
| baseline | SF10 | Spark GPU / Polars GPU | 5.10 |
| baseline | SF10 | Polars CPU / best GPU engine | 1.53 |
| baseline | SF10 | Polars CPU / best GPU (both GPU engines supported) | 1.53 |
| baseline | SF30 | Polars CPU / Polars GPU | 2.52 |
| baseline | SF30 | Spark CPU / Spark GPU | 2.63 |
| baseline | SF30 | Spark GPU / Polars GPU | 4.54 |
| baseline | SF30 | Polars CPU / best GPU engine | 2.52 |
| baseline | SF30 | Polars CPU / best GPU (both GPU engines supported) | 2.52 |
| baseline | SF100 | Polars CPU / Polars GPU | 3.09 |
| baseline | SF100 | Spark CPU / Spark GPU | 3.45 |
| baseline | SF100 | Spark GPU / Polars GPU | 3.61 |
| baseline | SF100 | Polars CPU / best GPU engine | 3.09 |
| baseline | SF100 | Polars CPU / best GPU (both GPU engines supported) | 3.09 |
| baseline | SF300 | Polars CPU / Polars GPU | 3.44 |
| baseline | SF300 | Spark CPU / Spark GPU | 3.43 |
| baseline | SF300 | Spark GPU / Polars GPU | 3.26 |
| baseline | SF300 | Polars CPU / best GPU engine | 3.44 |
| baseline | SF300 | Polars CPU / best GPU (both GPU engines supported) | 3.44 |
| complex | SF30 | Polars CPU / Polars GPU | – |
| complex | SF30 | Spark CPU / Spark GPU | 3.65 |
| complex | SF30 | Spark GPU / Polars GPU | – |
| complex | SF30 | Polars CPU / best GPU engine | 1.06 |
| complex | SF30 | Polars CPU / best GPU (both GPU engines supported) | – |
| complex | SF100 | Polars CPU / Polars GPU | – |
| complex | SF100 | Spark CPU / Spark GPU | 3.28 |
| complex | SF100 | Spark GPU / Polars GPU | – |
| complex | SF100 | Polars CPU / best GPU engine | 1.28 |
| complex | SF100 | Polars CPU / best GPU (both GPU engines supported) | – |
| complex | SF300 | Polars CPU / Polars GPU | – |
| complex | SF300 | Spark CPU / Spark GPU | 2.40 |
| complex | SF300 | Spark GPU / Polars GPU | – |
| complex | SF300 | Polars CPU / best GPU engine | 1.14 |
| complex | SF300 | Polars CPU / best GPU (both GPU engines supported) | – |

Not comparable with the geometric means of t01: a sum is dominated by the longest queries.


## 9. Secondary metrics

### GPU memory per engine and scale (what is measured differs by engine: see the Measure column)

| Suite | Scale | Engine | Measure | Min–max over queries (GiB) | Median (GiB) | Max / device |
|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 2.2–2.2 | 2.2 | 1.2% |
| baseline | SF1 | Spark GPU | nvidia-smi sample (not constant within sessions) | 2.6–2.6 | 2.6 | 1.5% |
| baseline | SF1 | Spark GPU | task-reported max (not a total) | 0.2–0.3 | 0.2 | 0.1% |
| baseline | SF10 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 9.4–10.1 | 9.8 | 5.7% |
| baseline | SF10 | Spark GPU | nvidia-smi sample (not constant within sessions) | 3.8–3.8 | 3.8 | 2.1% |
| baseline | SF10 | Spark GPU | task-reported max (not a total) | 0.5–1.3 | 0.7 | 0.7% |
| baseline | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 11.0–11.9 | 11.5 | 6.7% |
| baseline | SF30 | Spark GPU | nvidia-smi sample (not constant within sessions) | 8.9–8.9 | 8.9 | 5.0% |
| baseline | SF30 | Spark GPU | task-reported max (not a total) | 1.2–5.3 | 1.9 | 2.9% |
| baseline | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 14.4–14.4 | 14.4 | 8.0% |
| baseline | SF100 | Spark GPU | nvidia-smi sample (not constant within sessions) | 9.5–9.5 | 9.5 | 5.3% |
| baseline | SF100 | Spark GPU | task-reported max (not a total) | 1.6–6.5 | 3.2 | 3.6% |
| baseline | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 22.8–22.8 | 22.8 | 12.8% |
| baseline | SF300 | Spark GPU | nvidia-smi sample (not constant within sessions) | 13.3–13.3 | 13.3 | 7.4% |
| baseline | SF300 | Spark GPU | task-reported max (not a total) | 2.6–10.5 | 3.7 | 5.9% |
| complex | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 16.7–16.7 | 16.7 | 9.3% |
| complex | SF30 | Spark GPU | nvidia-smi sample (not constant within sessions) | 7.8–7.8 | 7.8 | 4.4% |
| complex | SF30 | Spark GPU | task-reported max (not a total) | 0.6–5.2 | 2.5 | 2.9% |
| complex | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 22.9–22.9 | 22.9 | 12.8% |
| complex | SF100 | Spark GPU | nvidia-smi sample (not constant within sessions) | 18.0–18.0 | 18.0 | 10.0% |
| complex | SF100 | Spark GPU | task-reported max (not a total) | 2.0–13.5 | 5.8 | 7.6% |
| complex | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 40.9–40.9 | 40.9 | 22.9% |
| complex | SF300 | Spark GPU | nvidia-smi sample (not constant within sessions) | 37.9–37.9 | 37.9 | 21.2% |
| complex | SF300 | Spark GPU | task-reported max (not a total) | 1.7–34.0 | 5.8 | 19.0% |

max_bytes / median_bytes / min_bytes are the nvidia-smi samples of the worker process tree during the timed action (worker peak_gpu_memory_bytes); maximum over the 3 measured trials, then over rounds; a sampled peak can miss short transients. What the sample means differs by engine: see measure / measure_note, and the task_reported blocks for Spark GPU Polars GPU: nvidia-smi device memory of the worker process, sampled during the timed action. cudf-polars allocates through an RMM async pool that keeps freed blocks (release threshold 90% of the device), so the value is the pool's high-water mark in that session so far, not the working set of the query: it never falls within a session (see high_water_check) and an upper bound on what the query needs. Spark GPU: the largest device allocation a single task reported (RAPIDS task accumulator gpuMaxDeviceMemoryBytes, Spark event log) among the tasks launched inside the action; not a simultaneous total over concurrent tasks. The nvidia-smi samples (max_bytes, ...) are the worker process tree's device memory when the action was sampled; they are constant within a session in only 0 of 32 sessions, so on this host they are not a fixed pool reservation (the first sampled action of a session reads 177.8 GiB and the minimum over the later actions 8.3 GiB, medians over sessions) and they are not task-level usage.

### Process wall time and startup (mean over rounds, seconds; overlapping scopes, do not add)

| Suite | Scale | Engine | Process wall | Launch → ready | Setup | Warmups | Measured |
|---|---|---|---|---|---|---|---|
| baseline | SF1 | Polars CPU | 1.12 | 0.110 | 0.0597 | 0.216 | 0.602 |
| baseline | SF1 | Polars GPU | 4.47 | 1.43 | 1.38 | 0.655 | 1.32 |
| baseline | SF1 | Spark CPU | 18.8 | 3.81 | 2.81 | 6.57 | 6.70 |
| baseline | SF1 | Spark GPU | 30.9 | 15.2 | 14.1 | 5.31 | 7.04 |
| baseline | SF10 | Polars CPU | 5.58 | 0.109 | 0.0599 | 1.36 | 3.61 |
| baseline | SF10 | Polars GPU | 5.93 | 1.41 | 1.36 | 1.00 | 2.39 |
| baseline | SF10 | Spark CPU | 40.1 | 3.84 | 2.84 | 13.3 | 20.5 |
| baseline | SF10 | Spark GPU | 38.3 | 15.2 | 14.1 | 7.43 | 12.2 |
| baseline | SF30 | Polars CPU | 16.1 | 0.111 | 0.0601 | 3.98 | 11.3 |
| baseline | SF30 | Polars GPU | 8.78 | 1.46 | 1.40 | 1.71 | 4.51 |
| baseline | SF30 | Spark CPU | 86.4 | 3.86 | 2.85 | 24.4 | 53.8 |
| baseline | SF30 | Spark GPU | 50.7 | 15.3 | 14.1 | 10.3 | 20.5 |
| baseline | SF100 | Polars CPU | 52.3 | 0.110 | 0.0594 | 13.1 | 38.7 |
| baseline | SF100 | Polars GPU | 18.9 | 1.44 | 1.39 | 4.10 | 12.3 |
| baseline | SF100 | Spark CPU | 220 | 3.85 | 2.85 | 55.1 | 152 |
| baseline | SF100 | Spark GPU | 83.9 | 15.2 | 14.1 | 18.5 | 44.4 |
| baseline | SF300 | Polars CPU | 178 | 0.111 | 0.0602 | 45.1 | 132 |
| baseline | SF300 | Polars GPU | 51.8 | 1.57 | 1.51 | 12.4 | 36.6 |
| baseline | SF300 | Spark CPU | 583 | 3.88 | 2.88 | 146 | 411 |
| baseline | SF300 | Spark GPU | 194 | 15.3 | 14.2 | 41.6 | 119 |
| complex | SF30 | Polars CPU | 53.0 | 0.110 | 0.0601 | 13.0 | 38.8 |
| complex | SF30 | Polars GPU | 8.55 | 1.45 | 1.40 | 1.58 | 4.14 |
| complex | SF30 | Spark CPU | 249 | 4.03 | 3.02 | 49.1 | 133 |
| complex | SF30 | Spark GPU | 128 | 15.3 | 14.2 | 13.3 | 34.2 |
| complex | SF100 | Polars CPU | 208 | 0.110 | 0.0594 | 51.5 | 154 |
| complex | SF100 | Polars GPU | 19.6 | 1.45 | 1.40 | 4.22 | 12.5 |
| complex | SF100 | Spark CPU | 607 | 4.01 | 3.00 | 134 | 395 |
| complex | SF100 | Spark GPU | 256 | 15.6 | 14.4 | 45.2 | 121 |
| complex | SF300 | Polars CPU | 654 | 0.112 | 0.0606 | 163 | 488 |
| complex | SF300 | Polars GPU | 61.1 | 1.47 | 1.42 | 14.8 | 43.4 |
| complex | SF300 | Spark CPU | 1476 | 4.10 | 3.09 | 344 | 1023 |
| complex | SF300 | Spark GPU | 691 | 15.3 | 14.2 | 148 | 426 |

### verified_gc cleanup cost (outside the query timers)

| Suite | Scale | Engine | Per cleanup (s) | Max (s) | Per session (s) | Share of wall |
|---|---|---|---|---|---|---|
| complex | SF30 | Polars CPU | 0.00769 | 0.0101 | 0.185 | 0.3% |
| complex | SF30 | Polars GPU | 0.0132 | 0.0197 | 0.211 | 2.5% |
| complex | SF30 | Spark CPU | 2.55 | 2.96 | 61.3 | 24.7% |
| complex | SF30 | Spark GPU | 2.57 | 3.00 | 61.8 | 48.1% |
| complex | SF100 | Polars CPU | 0.00768 | 0.0102 | 0.184 | 0.1% |
| complex | SF100 | Polars GPU | 0.0182 | 0.0203 | 0.291 | 1.5% |
| complex | SF100 | Spark CPU | 2.96 | 4.61 | 71.1 | 11.7% |
| complex | SF100 | Spark GPU | 2.94 | 4.48 | 70.7 | 27.6% |
| complex | SF300 | Polars CPU | 0.00767 | 0.0101 | 0.184 | 0.0% |
| complex | SF300 | Polars GPU | 0.0189 | 0.0204 | 0.302 | 0.5% |
| complex | SF300 | Spark CPU | 4.25 | 8.76 | 102 | 6.9% |
| complex | SF300 | Spark GPU | 4.07 | 7.97 | 97.6 | 14.1% |

### GPU board energy proxy per session (mean power.draw × process wall time)

| Suite | Scale | Engine | Mean power (W) | Wall (s) | Energy (Wh) |
|---|---|---|---|---|---|
| baseline | SF1 | Polars CPU | 174 | 1.12 | 0.0541 |
| baseline | SF1 | Polars GPU | 188 | 4.47 | 0.233 |
| baseline | SF1 | Spark CPU | 150 | 18.8 | 0.782 |
| baseline | SF1 | Spark GPU | 178 | 30.9 | 1.53 |
| baseline | SF10 | Polars CPU | 152 | 5.58 | 0.235 |
| baseline | SF10 | Polars GPU | 206 | 5.93 | 0.339 |
| baseline | SF10 | Spark CPU | 147 | 40.1 | 1.64 |
| baseline | SF10 | Spark GPU | 185 | 38.3 | 1.97 |
| baseline | SF30 | Polars CPU | 146 | 16.1 | 0.657 |
| baseline | SF30 | Polars GPU | 225 | 8.78 | 0.548 |
| baseline | SF30 | Spark CPU | 146 | 86.4 | 3.51 |
| baseline | SF30 | Spark GPU | 193 | 50.7 | 2.72 |
| baseline | SF100 | Polars CPU | 146 | 52.3 | 2.13 |
| baseline | SF100 | Polars GPU | 257 | 18.9 | 1.35 |
| baseline | SF100 | Spark CPU | 146 | 220 | 8.95 |
| baseline | SF100 | Spark GPU | 206 | 83.9 | 4.80 |
| baseline | SF300 | Polars CPU | 146 | 178 | 7.25 |
| baseline | SF300 | Polars GPU | 287 | 51.8 | 4.12 |
| baseline | SF300 | Spark CPU | 146 | 583 | 23.6 |
| baseline | SF300 | Spark GPU | 215 | 194 | 11.6 |
| complex | SF30 | Polars CPU | 146 | 53.0 | 2.15 |
| complex | SF30 | Polars GPU | 265 | 8.55 | 0.628 |
| complex | SF30 | Spark CPU | 146 | 249 | 10.1 |
| complex | SF30 | Spark GPU | 197 | 128 | 7.03 |
| complex | SF100 | Polars CPU | 146 | 208 | 8.42 |
| complex | SF100 | Polars GPU | 319 | 19.6 | 1.74 |
| complex | SF100 | Spark CPU | 146 | 607 | 24.7 |
| complex | SF100 | Spark GPU | 205 | 256 | 14.6 |
| complex | SF300 | Polars CPU | 146 | 654 | 26.6 |
| complex | SF300 | Polars GPU | 353 | 61.1 | 5.99 |
| complex | SF300 | Spark CPU | 146 | 1476 | 59.9 |
| complex | SF300 | Spark GPU | 208 | 691 | 40.0 |

GPU board energy proxy = mean nvidia-smi power.draw over the session window x process wall time; CPU and host power are not included, and CPU-only engines show the idle GPU draw

### GPU telemetry inside measured actions (descriptive telemetry; not a pre-registered contrast; no causal claim)

For each GPU engine, suite and scale: the nvidia-smi samples (utilization.gpu, utilization.memory, power.draw, clocks.current.sm) that fall inside the measured actions of the accepted rounds, pooled over queries, trials and rounds. idle_fraction is the share of samples with utilization.gpu below 10% (equal to the share of action wall time at uniform sampling). utilization.gpu is nvidia-smi's share of the sample period in which at least one kernel was executing, not SM occupancy. Descriptive; not a pre-registered contrast; no causal claim. Nvidia-smi is sampled about every 200 ms, so an action of 1 s holds about 5 samples and the first and last sample of an action are partly outside it; actions shorter than 1 s (most SF1 actions) are left out of every statistic and counted in n_actions_excluded_short. Power limit 1000 W (nvidia-smi -q, Current Power Limit of the first GPU); maximum SM clock 1965 MHz.


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


## 10. RQ5 Polars version bridge

| Suite | Scale | Queries | Ratio 2.0.0 / 1.44.2 [95% CI] | Round range |
|---|---|---|---|---|


No side-study data found.

### EXPLORATORY (post-hoc; not in the locked design §9): Polars 2.0.0 CPU against the GPU engines

Not computed: no side study with two Polars versions.

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
