# Campaign statistics report

Source: `rerun/hosts/h100/final/campaign-h100`. Schema 1.0; keys documented in `STATS_SCHEMA.md`. Pre-registered plan: `docs/H100_EXPERIMENT_DESIGN.md` section 9.

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
| baseline | SF1 | 6 | 0.350 [0.320, 0.382] | 0.322–0.364 |
| baseline | SF10 | 6 | 1.23 [1.17, 1.29] | 1.20–1.28 |
| baseline | SF30 | 6 | 1.97 [1.92, 2.03] | 1.92–2.00 |
| baseline | SF100 | 6 | 2.56 [2.50, 2.63] | 2.51–2.61 |
| baseline | SF300 | 6 | 2.81 [2.64, 2.99] | 2.70–2.94 |
| complex | SF30 | 4 | 3.66 [3.54, 3.79] | 3.57–3.74 |
| complex | SF100 | 4 | 4.88 [4.80, 4.96] | 4.83–4.95 |
| complex | SF300 | 4 | 4.70 [4.47, 4.94] | 4.59–4.91 |

### Spark CPU / Spark GPU (C2, RQ1)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.662 [0.549, 0.799] | 0.597–0.753 |
| baseline | SF10 | 6 | 1.31 [1.28, 1.34] | 1.29–1.33 |
| baseline | SF30 | 6 | 2.29 [2.20, 2.38] | 2.21–2.35 |
| baseline | SF100 | 6 | 2.80 [2.68, 2.93] | 2.70–2.87 |
| baseline | SF300 | 6 | 2.82 [2.77, 2.88] | 2.78–2.86 |
| complex | SF30 | 6 | 3.92 [3.48, 4.40] | 3.74–4.37 |
| complex | SF100 | 6 | 4.53 [4.43, 4.63] | 4.45–4.60 |
| complex | SF300 | 6 | 4.02 [3.77, 4.28] | 3.88–4.24 |

### Spark GPU / Polars GPU (C3, RQ2)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 6.60 [6.04, 7.21] | 6.09–6.94 |
| baseline | SF10 | 6 | 5.41 [5.14, 5.69] | 5.25–5.63 |
| baseline | SF30 | 6 | 4.22 [3.99, 4.47] | 4.01–4.33 |
| baseline | SF100 | 6 | 3.51 [3.47, 3.55] | 3.49–3.55 |
| baseline | SF300 | 6 | 3.14 [3.08, 3.19] | 3.11–3.19 |
| complex | SF30 | 4 | 6.83 [5.76, 8.09] | 5.83–7.30 |
| complex | SF100 | 4 | 6.44 [6.35, 6.53] | 6.38–6.50 |
| complex | SF300 | 4 | 5.71 [5.63, 5.79] | 5.66–5.76 |

### Polars CPU / best GPU engine (C4, RQ2/RQ3)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.350 [0.320, 0.382] | 0.322–0.364 |
| baseline | SF10 | 6 | 1.23 [1.17, 1.29] | 1.20–1.28 |
| baseline | SF30 | 6 | 1.97 [1.92, 2.03] | 1.92–2.00 |
| baseline | SF100 | 6 | 2.56 [2.50, 2.63] | 2.51–2.61 |
| baseline | SF300 | 6 | 2.81 [2.64, 2.99] | 2.70–2.94 |
| complex | SF30 | 6 | 3.34 [3.26, 3.43] | 3.30–3.41 |
| complex | SF100 | 6 | 5.25 [5.17, 5.32] | 5.20–5.30 |
| complex | SF300 | 6 | 5.64 [5.39, 5.91] | 5.52–5.88 |

### Polars CPU / best GPU (both GPU engines supported) (C4 sensitivity, RQ3 sensitivity)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.350 [0.320, 0.382] | 0.322–0.364 |
| baseline | SF10 | 6 | 1.23 [1.17, 1.29] | 1.20–1.28 |
| baseline | SF30 | 6 | 1.97 [1.92, 2.03] | 1.92–2.00 |
| baseline | SF100 | 6 | 2.56 [2.50, 2.63] | 2.51–2.61 |
| baseline | SF300 | 6 | 2.81 [2.64, 2.99] | 2.70–2.94 |
| complex | SF30 | 4 | 3.66 [3.54, 3.79] | 3.57–3.74 |
| complex | SF100 | 4 | 4.88 [4.80, 4.96] | 4.83–4.95 |
| complex | SF300 | 4 | 4.70 [4.47, 4.94] | 4.59–4.91 |

### Interval tally

| Contrast | Suite | Scales with CI above 1 | Scales with CI below 1 | Scales with CI containing 1 |
|---|---|---|---|---|
| Polars CPU / Polars GPU | baseline | SF10, SF30, SF100, SF300 | SF1 | – |
| Polars CPU / Polars GPU | complex | SF30, SF100, SF300 | – | – |
| Spark CPU / Spark GPU | baseline | SF10, SF30, SF100, SF300 | SF1 | – |
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
| baseline | SF30 | 2/6 | 4/6 | 0/6 | 0/6 |
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

**complex SF30 cx_q09 (Spark GPU)**: 2 final plans in 16 trials (12 measured, 4 warm-up); initial plan identical in every trial. The measured times of the plans do not overlap (median of the slowest over the fastest plan: 2.22).

- Plan 5166c91b662d against plan a4362d8c1e3e: exchanges `gpuhashpartitioning(l_partkey, 120)`: 0 to 1; shuffle readers `local`: 0 to 1. Operator lines only in this plan: `GpuColumnarExchange gpuhashpartitioning(l_partkey, 120, Murmur3Mode), ENSURE_REQUIREMENTS, [plan_id=N]`, `GpuCustomShuffleReader local`, `GpuShuffleCoalesce 1073741824`, `ShuffleQueryStage N`. Per action (Spark event log, median): stages 12.0 to 13.0; tasks 217 to 335; shuffle bytes written 1.11 GiB to 5.80 GiB.

| Suite | Scale | Query | Engine | Plan | Measured trials (per round) | Warm-ups | Measured s (min / median / max) | Exchanges | Shuffle readers | Stages / tasks (median per action) | Shuffle written (GiB, median) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| complex | SF30 | cx_q09 | Spark GPU | a4362d8c1e3e * | 4 (r1: 1; r3: 2; r4: 1) | 4 | 2.13/2.15/2.17 | 7 | coalesced 6, plain 1 | 12.0 / 217 | 1.11 |
| complex | SF30 | cx_q09 | Spark GPU | 5166c91b662d | 8 (r1: 2; r2: 3; r3: 1; r4: 2) | 0 | 4.58/4.76/4.80 | 8 | coalesced 6, local 1, plain 1 | 13.0 / 335 | 5.80 |

* plan with the lowest median measured time in its cell (the reference for the listed changes).


## 7. Variability and order effects

| Engine | Within: median CV | Within: p90 | Within: max | Between: median CV | Between: p90 | Between: max |
|---|---|---|---|---|---|---|
| Polars CPU | 3.1% | 11.2% | 64.1% | 1.8% | 4.6% | 24.0% |
| Polars GPU | 2.3% | 7.7% | 35.7% | 2.1% | 6.9% | 10.5% |
| Spark CPU | 4.5% | 17.4% | 106.6% | 3.2% | 9.6% | 23.0% |
| Spark GPU | 2.3% | 7.7% | 50.1% | 1.4% | 3.1% | 31.0% |

Within-session CV uses the 3 measured trials of each cell and round; between-round CV uses the per-round medians of each cell.


| Position | Cells | Mean deviation [95% CI] |
|---|---|---|
| 1 | 186 | +0.4% [-0.0, +0.9] |
| 2 | 186 | -0.1% [-2.2, +2.0] |
| 3 | 186 | -0.1% [-2.3, +2.1] |
| 4 | 186 | -0.2% [-1.4, +1.1] |

Deviation of each cell's time from its own mean over rounds, averaged over cells, then a t-interval over rounds. Reported descriptively; the Williams design balances positions across engines.


| Predecessor | Cells | Mean deviation [95% CI] |
|---|---|---|
| none (first in block) | 186 | +0.4% [-0.0, +0.9] |
| Polars CPU | 138 | -1.2% [-2.6, +0.2] |
| Polars GPU | 144 | +0.9% [-0.0, +1.8] |
| Spark CPU | 138 | -0.6% [-2.4, +1.3] |
| Spark GPU | 138 | +0.4% [-2.4, +3.3] |


## 8. Legacy sum-of-medians totals (kept separate from the geometric means)

| Suite | Scale | Engine | Sum of medians (s) [min–max] |
|---|---|---|---|
| baseline | SF1 | Polars CPU | 0.156 [0.151–0.160] |
| baseline | SF1 | Polars GPU | 0.407 [0.398–0.432] |
| baseline | SF1 | Spark CPU | 1.89 [1.69–2.11] |
| baseline | SF1 | Spark GPU | 2.61 [2.58–2.65] |
| baseline | SF10 | Polars CPU | 1.08 [1.06–1.09] |
| baseline | SF10 | Polars GPU | 0.721 [0.696–0.738] |
| baseline | SF10 | Spark CPU | 5.95 [5.89–6.01] |
| baseline | SF10 | Spark GPU | 4.37 [4.34–4.41] |
| baseline | SF30 | Polars CPU | 3.57 [3.51–3.61] |
| baseline | SF30 | Polars GPU | 1.43 [1.40–1.48] |
| baseline | SF30 | Spark CPU | 17.1 [16.7–17.9] |
| baseline | SF30 | Spark GPU | 7.10 [7.05–7.13] |
| baseline | SF100 | Polars CPU | 11.9 [11.9–11.9] |
| baseline | SF100 | Polars GPU | 3.72 [3.68–3.78] |
| baseline | SF100 | Spark CPU | 45.5 [44.2–46.2] |
| baseline | SF100 | Spark GPU | 14.7 [14.6–14.9] |
| baseline | SF300 | Polars CPU | 39.1 [38.5–39.9] |
| baseline | SF300 | Polars GPU | 11.7 [11.6–11.7] |
| baseline | SF300 | Spark CPU | 126 [125–128] |
| baseline | SF300 | Spark GPU | 40.8 [39.8–41.6] |
| complex | SF30 | Polars CPU | 13.2 [13.1–13.2] |
| complex | SF30 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF30 | Spark CPU | 43.3 [43.0–43.7] |
| complex | SF30 | Spark GPU | 11.5 [9.55–12.2] |
| complex | SF100 | Polars CPU | 49.8 [49.2–50.2] |
| complex | SF100 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF100 | Spark CPU | 127 [126–129] |
| complex | SF100 | Spark GPU | 36.5 [36.3–36.7] |
| complex | SF300 | Polars CPU | 161 [159–163] |
| complex | SF300 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF300 | Spark CPU | 343 [334–356] |
| complex | SF300 | Spark GPU | 131 [130–132] |

Totals exist only when every query has a valid median in every round; never combined with the geometric means of t01.


| Suite | Scale | Contrast | Ratio of mean totals |
|---|---|---|---|
| baseline | SF1 | Polars CPU / Polars GPU | 0.382 |
| baseline | SF1 | Spark CPU / Spark GPU | 0.724 |
| baseline | SF1 | Spark GPU / Polars GPU | 6.40 |
| baseline | SF1 | Polars CPU / best GPU engine | 0.382 |
| baseline | SF1 | Polars CPU / best GPU (both GPU engines supported) | 0.382 |
| baseline | SF10 | Polars CPU / Polars GPU | 1.49 |
| baseline | SF10 | Spark CPU / Spark GPU | 1.36 |
| baseline | SF10 | Spark GPU / Polars GPU | 6.07 |
| baseline | SF10 | Polars CPU / best GPU engine | 1.49 |
| baseline | SF10 | Polars CPU / best GPU (both GPU engines supported) | 1.49 |
| baseline | SF30 | Polars CPU / Polars GPU | 2.50 |
| baseline | SF30 | Spark CPU / Spark GPU | 2.41 |
| baseline | SF30 | Spark GPU / Polars GPU | 4.98 |
| baseline | SF30 | Polars CPU / best GPU engine | 2.50 |
| baseline | SF30 | Polars CPU / best GPU (both GPU engines supported) | 2.50 |
| baseline | SF100 | Polars CPU / Polars GPU | 3.20 |
| baseline | SF100 | Spark CPU / Spark GPU | 3.09 |
| baseline | SF100 | Spark GPU / Polars GPU | 3.96 |
| baseline | SF100 | Polars CPU / best GPU engine | 3.20 |
| baseline | SF100 | Polars CPU / best GPU (both GPU engines supported) | 3.20 |
| baseline | SF300 | Polars CPU / Polars GPU | 3.34 |
| baseline | SF300 | Spark CPU / Spark GPU | 3.09 |
| baseline | SF300 | Spark GPU / Polars GPU | 3.49 |
| baseline | SF300 | Polars CPU / best GPU engine | 3.34 |
| baseline | SF300 | Polars CPU / best GPU (both GPU engines supported) | 3.34 |
| complex | SF30 | Polars CPU / Polars GPU | – |
| complex | SF30 | Spark CPU / Spark GPU | 3.78 |
| complex | SF30 | Spark GPU / Polars GPU | – |
| complex | SF30 | Polars CPU / best GPU engine | 1.15 |
| complex | SF30 | Polars CPU / best GPU (both GPU engines supported) | – |
| complex | SF100 | Polars CPU / Polars GPU | – |
| complex | SF100 | Spark CPU / Spark GPU | 3.49 |
| complex | SF100 | Spark GPU / Polars GPU | – |
| complex | SF100 | Polars CPU / best GPU engine | 1.36 |
| complex | SF100 | Polars CPU / best GPU (both GPU engines supported) | – |
| complex | SF300 | Polars CPU / Polars GPU | – |
| complex | SF300 | Spark CPU / Spark GPU | 2.61 |
| complex | SF300 | Spark GPU / Polars GPU | – |
| complex | SF300 | Polars CPU / best GPU engine | 1.23 |
| complex | SF300 | Polars CPU / best GPU (both GPU engines supported) | – |

Not comparable with the geometric means of t01: a sum is dominated by the longest queries.


## 9. Secondary metrics

### GPU memory per engine and scale (what is measured differs by engine: see the Measure column)

| Suite | Scale | Engine | Measure | Min–max over queries (GiB) | Median (GiB) | Max / device |
|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 1.9–1.9 | 1.9 | 2.4% |
| baseline | SF1 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| baseline | SF1 | Spark GPU | task-reported max (not a total) | 0.2–0.3 | 0.2 | 0.3% |
| baseline | SF10 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 9.4–9.4 | 9.4 | 11.8% |
| baseline | SF10 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| baseline | SF10 | Spark GPU | task-reported max (not a total) | 0.6–1.7 | 0.7 | 2.1% |
| baseline | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 11.1–11.4 | 11.2 | 14.3% |
| baseline | SF30 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| baseline | SF30 | Spark GPU | task-reported max (not a total) | 1.5–6.1 | 1.9 | 7.7% |
| baseline | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 14.7–14.7 | 14.7 | 18.5% |
| baseline | SF100 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| baseline | SF100 | Spark GPU | task-reported max (not a total) | 1.9–6.5 | 3.2 | 8.2% |
| baseline | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 29.1–29.1 | 29.1 | 36.6% |
| baseline | SF300 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| baseline | SF300 | Spark GPU | task-reported max (not a total) | 2.7–9.7 | 3.4 | 12.1% |
| complex | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 16.1–16.1 | 16.1 | 20.2% |
| complex | SF30 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| complex | SF30 | Spark GPU | task-reported max (not a total) | 0.8–5.2 | 2.2 | 6.5% |
| complex | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 22.4–22.4 | 22.4 | 28.2% |
| complex | SF100 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| complex | SF100 | Spark GPU | task-reported max (not a total) | 1.4–12.3 | 3.7 | 15.4% |
| complex | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 40.0–40.0 | 40.0 | 50.3% |
| complex | SF300 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| complex | SF300 | Spark GPU | task-reported max (not a total) | 1.6–30.0 | 5.5 | 37.7% |

max_bytes / median_bytes / min_bytes are the nvidia-smi samples of the worker process tree during the timed action (worker peak_gpu_memory_bytes); maximum over the 3 measured trials, then over rounds; a sampled peak can miss short transients. What the sample means differs by engine: see measure / measure_note, and the task_reported blocks for Spark GPU Polars GPU: nvidia-smi device memory of the worker process, sampled during the timed action. cudf-polars allocates through an RMM async pool that keeps freed blocks (release threshold 90% of the device), so the value is the pool's high-water mark in that session so far, not the working set of the query: it never falls within a session (see high_water_check) and an upper bound on what the query needs. Spark GPU: the largest device allocation a single task reported (RAPIDS task accumulator gpuMaxDeviceMemoryBytes, Spark event log) among the tasks launched inside the action; not a simultaneous total over concurrent tasks. The nvidia-smi samples (max_bytes, ...) are the RAPIDS pool reservation made at plugin start, not memory in use.

### Process wall time and startup (mean over rounds, seconds; overlapping scopes, do not add)

| Suite | Scale | Engine | Process wall | Launch → ready | Setup | Warmups | Measured |
|---|---|---|---|---|---|---|---|
| baseline | SF1 | Polars CPU | 0.946 | 0.112 | 0.0610 | 0.200 | 0.466 |
| baseline | SF1 | Polars GPU | 4.04 | 1.42 | 1.37 | 0.671 | 1.25 |
| baseline | SF1 | Spark CPU | 18.6 | 3.84 | 2.79 | 6.63 | 6.40 |
| baseline | SF1 | Spark GPU | 31.4 | 15.2 | 14.0 | 5.28 | 7.92 |
| baseline | SF10 | Polars CPU | 5.02 | 0.112 | 0.0609 | 1.20 | 3.24 |
| baseline | SF10 | Polars GPU | 5.25 | 1.35 | 1.30 | 0.946 | 2.18 |
| baseline | SF10 | Spark CPU | 37.6 | 3.75 | 2.76 | 13.4 | 17.9 |
| baseline | SF10 | Spark GPU | 38.6 | 14.7 | 13.6 | 7.54 | 13.1 |
| baseline | SF30 | Polars CPU | 15.2 | 0.114 | 0.0606 | 3.69 | 10.7 |
| baseline | SF30 | Polars GPU | 7.99 | 1.35 | 1.30 | 1.59 | 4.30 |
| baseline | SF30 | Spark CPU | 82.8 | 3.77 | 2.78 | 23.9 | 50.9 |
| baseline | SF30 | Spark GPU | 50.6 | 14.7 | 13.6 | 10.4 | 21.3 |
| baseline | SF100 | Polars CPU | 49.4 | 0.112 | 0.0611 | 12.4 | 36.5 |
| baseline | SF100 | Polars GPU | 17.4 | 1.57 | 1.51 | 3.96 | 11.1 |
| baseline | SF100 | Spark CPU | 203 | 3.79 | 2.81 | 53.2 | 138 |
| baseline | SF100 | Spark GPU | 84.3 | 15.0 | 13.9 | 18.6 | 44.8 |
| baseline | SF300 | Polars CPU | 163 | 0.114 | 0.0632 | 41.6 | 121 |
| baseline | SF300 | Polars GPU | 49.3 | 1.42 | 1.37 | 11.9 | 35.2 |
| baseline | SF300 | Spark CPU | 535 | 3.80 | 2.82 | 136 | 380 |
| baseline | SF300 | Spark GPU | 194 | 15.2 | 14.0 | 42.7 | 121 |
| complex | SF30 | Polars CPU | 53.7 | 0.114 | 0.0617 | 13.2 | 39.5 |
| complex | SF30 | Polars GPU | 7.67 | 1.43 | 1.38 | 1.44 | 3.70 |
| complex | SF30 | Spark CPU | 244 | 3.87 | 2.89 | 48.7 | 129 |
| complex | SF30 | Spark GPU | 127 | 15.3 | 14.1 | 13.1 | 33.9 |
| complex | SF100 | Polars CPU | 201 | 0.113 | 0.0615 | 50.3 | 149 |
| complex | SF100 | Polars GPU | 16.8 | 1.37 | 1.32 | 3.75 | 10.6 |
| complex | SF100 | Spark CPU | 590 | 3.85 | 2.87 | 131 | 382 |
| complex | SF100 | Spark GPU | 239 | 14.9 | 13.8 | 41.6 | 109 |
| complex | SF300 | Polars CPU | 648 | 0.124 | 0.0696 | 162 | 484 |
| complex | SF300 | Polars GPU | 51.8 | 1.55 | 1.49 | 12.5 | 36.6 |
| complex | SF300 | Spark CPU | 1479 | 4.33 | 3.18 | 349 | 1025 |
| complex | SF300 | Spark GPU | 656 | 15.2 | 14.0 | 143 | 394 |

### verified_gc cleanup cost (outside the query timers)

| Suite | Scale | Engine | Per cleanup (s) | Max (s) | Per session (s) | Share of wall |
|---|---|---|---|---|---|---|
| complex | SF30 | Polars CPU | 0.00753 | 0.00955 | 0.181 | 0.3% |
| complex | SF30 | Polars GPU | 0.0179 | 0.0216 | 0.287 | 3.7% |
| complex | SF30 | Spark CPU | 2.54 | 3.19 | 60.9 | 25.0% |
| complex | SF30 | Spark GPU | 2.56 | 3.06 | 61.4 | 48.3% |
| complex | SF100 | Polars CPU | 0.00744 | 0.00933 | 0.179 | 0.1% |
| complex | SF100 | Polars GPU | 0.0192 | 0.0216 | 0.307 | 1.8% |
| complex | SF100 | Spark CPU | 2.95 | 4.78 | 70.7 | 12.0% |
| complex | SF100 | Spark GPU | 2.91 | 4.44 | 69.9 | 29.2% |
| complex | SF300 | Polars CPU | 0.00759 | 0.0103 | 0.182 | 0.0% |
| complex | SF300 | Polars GPU | 0.0191 | 0.0213 | 0.306 | 0.6% |
| complex | SF300 | Spark CPU | 4.08 | 9.23 | 98.0 | 6.6% |
| complex | SF300 | Spark GPU | 4.14 | 9.33 | 99.4 | 15.2% |

### GPU board energy proxy per session (mean power.draw × process wall time)

| Suite | Scale | Engine | Mean power (W) | Wall (s) | Energy (Wh) |
|---|---|---|---|---|---|
| baseline | SF1 | Polars CPU | 98.1 | 0.946 | 0.0258 |
| baseline | SF1 | Polars GPU | 106 | 4.04 | 0.118 |
| baseline | SF1 | Spark CPU | 73.5 | 18.6 | 0.379 |
| baseline | SF1 | Spark GPU | 99.8 | 31.4 | 0.870 |
| baseline | SF10 | Polars CPU | 75.1 | 5.02 | 0.105 |
| baseline | SF10 | Polars GPU | 130 | 5.25 | 0.189 |
| baseline | SF10 | Spark CPU | 70.6 | 37.6 | 0.737 |
| baseline | SF10 | Spark GPU | 108 | 38.6 | 1.15 |
| baseline | SF30 | Polars CPU | 69.7 | 15.2 | 0.294 |
| baseline | SF30 | Polars GPU | 163 | 7.99 | 0.361 |
| baseline | SF30 | Spark CPU | 69.6 | 82.8 | 1.60 |
| baseline | SF30 | Spark GPU | 118 | 50.6 | 1.66 |
| baseline | SF100 | Polars CPU | 69.6 | 49.4 | 0.956 |
| baseline | SF100 | Polars GPU | 207 | 17.4 | 1.00 |
| baseline | SF100 | Spark CPU | 69.6 | 203 | 3.93 |
| baseline | SF100 | Spark GPU | 135 | 84.3 | 3.16 |
| baseline | SF300 | Polars CPU | 69.5 | 163 | 3.15 |
| baseline | SF300 | Polars GPU | 240 | 49.3 | 3.29 |
| baseline | SF300 | Spark CPU | 69.6 | 535 | 10.3 |
| baseline | SF300 | Spark GPU | 147 | 194 | 7.91 |
| complex | SF30 | Polars CPU | 69.6 | 53.7 | 1.04 |
| complex | SF30 | Polars GPU | 171 | 7.67 | 0.365 |
| complex | SF30 | Spark CPU | 69.5 | 244 | 4.71 |
| complex | SF30 | Spark GPU | 118 | 127 | 4.18 |
| complex | SF100 | Polars CPU | 69.6 | 201 | 3.89 |
| complex | SF100 | Polars GPU | 227 | 16.8 | 1.06 |
| complex | SF100 | Spark CPU | 69.5 | 590 | 11.4 |
| complex | SF100 | Spark GPU | 128 | 239 | 8.51 |
| complex | SF300 | Polars CPU | 69.5 | 648 | 12.5 |
| complex | SF300 | Polars GPU | 281 | 51.8 | 4.04 |
| complex | SF300 | Spark CPU | 69.5 | 1479 | 28.6 |
| complex | SF300 | Spark GPU | 131 | 656 | 23.9 |

GPU board energy proxy = mean nvidia-smi power.draw over the session window x process wall time; CPU and host power are not included, and CPU-only engines show the idle GPU draw

### GPU telemetry inside measured actions (descriptive telemetry; not a pre-registered contrast; no causal claim)

For each GPU engine, suite and scale: the nvidia-smi samples (utilization.gpu, utilization.memory, power.draw, clocks.current.sm) that fall inside the measured actions of the accepted rounds, pooled over queries, trials and rounds. idle_fraction is the share of samples with utilization.gpu below 10% (equal to the share of action wall time at uniform sampling). utilization.gpu is nvidia-smi's share of the sample period in which at least one kernel was executing, not SM occupancy. Descriptive; not a pre-registered contrast; no causal claim. Nvidia-smi is sampled about every 200 ms, so an action of 1 s holds about 5 samples and the first and last sample of an action are partly outside it; actions shorter than 1 s (most SF1 actions) are left out of every statistic and counted in n_actions_excluded_short. Power limit 700 W (nvidia-smi -q, Current Power Limit of the first GPU); maximum SM clock 1980 MHz.


| Suite | Scale | Engine | Actions used / left out (< 1 s) | Samples | GPU util median / p90 (%) | GPU-idle fraction (util < 10%) | Power median W (fraction of limit) | SM clock median MHz (fraction of max) | Memory util median (%) |
|---|---|---|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF1 | Spark GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF10 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF10 | Spark GPU | 24 / 48 | 157 | 49 / 100 | 13.4% | 124 W (18%) | 1980 (100%) | 0 |
| baseline | SF30 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF30 | Spark GPU | 37 / 35 | 331 | 70 / 100 | 16.0% | 135 W (19%) | 1980 (100%) | 1 |
| baseline | SF100 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF100 | Spark GPU | 72 / 0 | 907 | 79 / 98 | 12.3% | 145 W (21%) | 1980 (100%) | 3 |
| baseline | SF300 | Polars GPU | 72 / 0 | 710 | 87 / 97 | 0.1% | 242 W (35%) | 1980 (100%) | 14 |
| baseline | SF300 | Spark GPU | 72 / 0 | 2433 | 79 / 98 | 9.9% | 149 W (21%) | 1980 (100%) | 3 |
| complex | SF30 | Polars GPU | 0 / 48 | 0 | – | – | – | – | – |
| complex | SF30 | Spark GPU | 48 / 24 | 604 | 36 / 97 | 25.5% | 130 W (19%) | 1980 (100%) | 1 |
| complex | SF100 | Polars GPU | 14 / 34 | 93 | 90 / 97 | 0.0% | 254 W (36%) | 1980 (100%) | 16 |
| complex | SF100 | Spark GPU | 72 / 0 | 2194 | 32 / 92 | 21.1% | 132 W (19%) | 1980 (100%) | 2 |
| complex | SF300 | Polars GPU | 48 / 0 | 740 | 86 / 95 | 0.5% | 285 W (41%) | 1980 (100%) | 18 |
| complex | SF300 | Spark GPU | 72 / 0 | 7881 | 22 / 89 | 28.7% | 129 W (18%) | 1980 (100%) | 2 |

descriptive telemetry; not a pre-registered contrast; no causal claim. nvidia-smi is sampled about every 200 ms, so an action of 1 s holds about 5 samples and the first and last sample of an action are partly outside it; actions shorter than 1 s (most SF1 actions) are left out of every statistic and counted in n_actions_excluded_short.


## 10. RQ5 Polars version bridge

| Suite | Scale | Queries | Ratio 2.0.0 / 1.44.2 [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.918 [0.897, 0.940] | 0.900–0.931 |
| baseline | SF10 | 6 | 0.857 [0.834, 0.881] | 0.841–0.873 |
| baseline | SF30 | 6 | 0.793 [0.774, 0.812] | 0.781–0.807 |
| baseline | SF100 | 6 | 0.768 [0.757, 0.780] | 0.760–0.775 |
| baseline | SF300 | 6 | 0.729 [0.691, 0.768] | 0.698–0.752 |
| complex | SF30 | 6 | 0.511 [0.494, 0.528] | 0.504–0.527 |
| complex | SF100 | 6 | 0.516 [0.507, 0.525] | 0.509–0.521 |
| complex | SF300 | 6 | 0.508 [0.505, 0.511] | 0.507–0.510 |


### EXPLORATORY (post-hoc; not in the locked design §9): Polars 2.0.0 CPU against the GPU engines

**This is not part of the pre-registered analysis (design section 9) and is never used by C1-C4 or the crossover verdict.** E1 = time of Polars 2.0.0 CPU (side-study rounds [1, 2, 3, 4]) / time of Polars GPU (main rounds [1, 2, 3, 4]); E4 = the same CPU time / the better GPU engine per query and round. Orientation as for C1 and C4: above 1 the GPU side was faster. The GPU comparisons of the locked design use Polars 1.44.2 on the CPU side (C1, C4), which is why the version bridge above matters. Welch-type 95% interval on the log scale: the CPU times come from the side-study sessions and the GPU times from the main rounds, different sessions in different rounds, so the two sets of per-round values are unpaired (Welch-Satterthwaite df); no interval with fewer than two rounds on either side.

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


## 11. A100 → H100 (GPU engines only; non-equivalent hosts)

The A100 side is the **2026-10-25 A100 study** (sessions on 2026-10-25), the saved evidence of a previous study, not the A100 campaign. Engines in that study: Polars CPU, Spark CPU, Spark GPU; compared here: Spark GPU (no Polars GPU run in that study). CPU engines are never compared across the two.

Main differences: Polars 2.0.0 against 1.44.2 (CPU engine; not in the ratio); 30 against 26 threads; protocol: 1 session per cell against 4 rounds; fixed engine rotation per scale against rotated, balanced orders; query cleanup policy, failure policy, suite version, Spark GPU profile not recorded in the earlier study.

| Engine | Suite | Scale | Queries | A100 / H100 time [95% CI over H100 rounds] |
|---|---|---|---|---|
| Spark GPU | baseline | SF1 | 6 | 1.44 [1.40, 1.48] |
| Spark GPU | baseline | SF10 | 6 | 1.39 [1.38, 1.40] |
| Spark GPU | baseline | SF30 | 6 | 1.50 [1.48, 1.52] |
| Spark GPU | baseline | SF100 | 6 | 1.58 [1.56, 1.60] |
| Spark GPU | baseline | SF300 | 6 | 1.58 [1.54, 1.61] |
| Spark GPU | complex | SF30 | 6 | 1.54 [1.39, 1.72] |
| Spark GPU | complex | SF100 | 6 | 1.53 [1.52, 1.55] |
| Spark GPU | complex | SF300 | 6 | 1.56 [1.55, 1.57] |

time on A100 / time on H100 for the same GPU engine, query and scale; above 1 means the H100 host was faster. The A100 study is a single session per cell (no rounds), so the interval reflects the H100 round-to-round variation only. Hosts differ in GPU, vCPU count, RAM and software versions: not an equivalent comparison. The A100 side is the previous study's saved evidence (see study.label and study.differences), not the A100 campaign. A100 = the 2026-10-25 A100 study (saved evidence of a previous study, not the A100 campaign). Polars 2.0.0 against 1.44.2 (CPU engine; not in the ratio); 30 against 26 threads; protocol: 1 session per cell against 4 rounds; fixed engine rotation per scale against rotated, balanced orders; query cleanup policy, failure policy, suite version, Spark GPU profile not recorded in the earlier study.


Cells of the earlier study whose trials ended in more than one final adaptive plan (descriptive; every trial enters that study's median; signatures differ from the campaign's because the earlier study's scan locations are sanitized, so plans are compared by structure):

- baseline SF10 q03 (Spark CPU): plan 8527f8f: 3 measured and 0 warm-up trial(s) (2.21-3.08 s), 5 exchanges, shuffle readers coalesced 3, local 2; plan 630079f: 0 measured and 1 warm-up trial(s), 3 exchanges, shuffle readers coalesced 2, local 1
- complex SF30 cx_q09 (Spark GPU): plan 90e42e8: 3 measured and 0 warm-up trial(s) (6.53-7.14 s), 8 exchanges, shuffle readers coalesced 6, local 1, plain 1; plan 608a9e4: 0 measured and 1 warm-up trial(s), 7 exchanges, shuffle readers coalesced 6, plain 1

### What differs from the H100 campaign (read from the result files)

| Field | Engines | Suites | A100 study | H100 campaign | Engine in the ratio |
|---|---|---|---|---|---|
| library version | Polars CPU | baseline, complex | 2.0.0 | 1.44.2 | no |
| threads | Polars CPU, Spark CPU, Spark GPU | baseline, complex | 30 | 26 | yes |
| sessions per cell | all | all | 1 | 4 | yes |
| engine order | all | all | spark_cpu > polars > spark_gpu / spark_gpu > polars > spark_cpu / spark_gpu > spark_cpu > polars | polars > spark_gpu > polars_gpu > spark_cpu / polars_gpu > polars > spark_cpu > spark_gpu / spark_cpu > polars_gpu > spark_gpu > polars / spark_gpu > spark_cpu > polars > polars_gpu | yes |
| query cleanup policy | Polars CPU, Spark CPU, Spark GPU | baseline | not recorded | none | yes |
| failure policy | Polars CPU, Spark CPU, Spark GPU | baseline | not recorded | raise | yes |
| failure policy | Polars CPU, Spark CPU, Spark GPU | complex | not recorded | record_and_continue | yes |
| suite version | Polars CPU, Spark CPU, Spark GPU | baseline | not recorded | baseline-v1 | yes |
| Spark GPU profile | Spark GPU | baseline, complex | not recorded | a100-parity | yes |
| Spark jars | Spark GPU | baseline, complex | not recorded | rapids-4-spark_2.12-26.08.1.jar | yes |
| GPU | all | all | NVIDIA A100-SXM4-40GB | NVIDIA H100 80GB HBM3 | yes |
| vCPUs | all | all | 30 | 26 | yes |
| host RAM (GiB) | all | all | 216 | 221.2 | yes |
| spark.master | Spark GPU | all | local[30] | local[26] | yes |
| spark.rapids.sql.multiThreadedRead.memoryLimit.size | Spark GPU | all | 8 values | 32 values | yes |

'not recorded': the earlier study's result files carry no such field. CPU engines are not compared across the two.


A100 host: 1 x NVIDIA A100-SXM4-40GB, 30 vCPU, 216 GiB.

## 12. Caveats

- Four rounds give df = 3; intervals are wide and are not hypothesis tests. Multiplicity across contrasts, suites and scales is not adjusted.
- Round is a block: all engines run once per round, so round-level drift cancels in each per-round ratio. Session-level effects remain inside the interval.
- C4 uses, per query and round, the faster of the GPU engines that produced a timing; a query that only one GPU engine supports is included with that engine. `c4_strict` restricts to queries both GPU engines ran. C1, C3 and `c4_strict` exclude queries unsupported on Polars GPU; they stay visible in the coverage table.
- Geometric means weight queries equally; the legacy sums of medians are dominated by the longest queries. They answer different questions and are never mixed.
- Peak GPU memory is a sampled per-process-tree value; the energy proxy is GPU board power only (host CPU power excluded; CPU engines show idle GPU draw).
- The A100 study is one session per cell and ran on different hardware, vCPU count and software: its ratios to H100 describe a host change, not an architecture effect.
