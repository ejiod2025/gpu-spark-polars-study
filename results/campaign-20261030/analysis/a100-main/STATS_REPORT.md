# Campaign statistics report

Source: `rerun/hosts/a100/campaign-a100`. Schema 1.0; keys documented in `STATS_SCHEMA.md`. Pre-registered plan: `docs/H100_EXPERIMENT_DESIGN.md` section 9.

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
- preflight preflight-host-2 (host rehearsal) ran with --min-free-gib 250: a recorded operational threshold for the host's free-disk check, not a design parameter

Observed engine order per round: round 1: spark_cpu → polars_gpu → spark_gpu → polars; round 2: polars_gpu → polars → spark_cpu → spark_gpu; round 3: polars → spark_gpu → polars_gpu → spark_cpu; round 4: spark_gpu → spark_cpu → polars → polars_gpu. Each engine occupies each position once: True; each ordered engine pair adjacent once: True.

## 2. Reading the ratios

A speedup is `t_ref / t_engine` per query and round, so above 1 the denominator engine is faster. The per-query estimate is the geometric mean over rounds with a 95% t-interval on the mean log-ratio (df = rounds − 1). The suite estimate is, per round, the geometric mean of the per-query ratios over queries available for both sides in every round, then the same interval across rounds. Round is the block.

## 3. Primary contrasts per suite and scale

### Polars CPU / Polars GPU (C1, RQ1)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.330 [0.297, 0.366] | 0.307–0.350 |
| baseline | SF10 | 6 | 0.917 [0.885, 0.950] | 0.900–0.943 |
| baseline | SF30 | 6 | 1.51 [1.42, 1.61] | 1.46–1.57 |
| baseline | SF100 | 6 | 1.98 [1.86, 2.11] | 1.91–2.08 |
| baseline | SF300 | 6 | 2.07 [1.83, 2.34] | 1.90–2.29 |
| complex | SF30 | 4 | 2.87 [2.68, 3.06] | 2.72–3.01 |
| complex | SF100 | 4 | 3.57 [3.50, 3.65] | 3.53–3.64 |
| complex | SF300 | 4 | 3.32 [3.15, 3.49] | 3.22–3.47 |

### Spark CPU / Spark GPU (C2, RQ1)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.674 [0.592, 0.768] | 0.614–0.744 |
| baseline | SF10 | 6 | 1.40 [1.21, 1.61] | 1.23–1.50 |
| baseline | SF30 | 6 | 1.89 [1.49, 2.41] | 1.64–2.22 |
| baseline | SF100 | 6 | 2.14 [2.03, 2.25] | 2.06–2.23 |
| baseline | SF300 | 6 | 2.03 [1.95, 2.11] | 1.96–2.07 |
| complex | SF30 | 6 | 3.08 [2.90, 3.28] | 2.93–3.22 |
| complex | SF100 | 6 | 3.58 [3.47, 3.69] | 3.49–3.64 |
| complex | SF300 | 6 | 2.91 [2.82, 3.01] | 2.84–2.97 |

### Spark GPU / Polars GPU (C3, RQ2)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 5.82 [5.69, 5.95] | 5.72–5.92 |
| baseline | SF10 | 6 | 4.55 [4.45, 4.66] | 4.46–4.60 |
| baseline | SF30 | 6 | 4.02 [3.82, 4.24] | 3.92–4.22 |
| baseline | SF100 | 6 | 3.46 [3.25, 3.69] | 3.35–3.67 |
| baseline | SF300 | 6 | 2.84 [2.70, 2.98] | 2.71–2.90 |
| complex | SF30 | 4 | 6.59 [5.99, 7.25] | 6.28–7.18 |
| complex | SF100 | 4 | 5.50 [5.42, 5.57] | 5.45–5.56 |
| complex | SF300 | 4 | 4.68 [4.64, 4.72] | 4.66–4.72 |

### Polars CPU / best GPU engine (C4, RQ2/RQ3)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.330 [0.297, 0.366] | 0.307–0.350 |
| baseline | SF10 | 6 | 0.917 [0.885, 0.950] | 0.900–0.943 |
| baseline | SF30 | 6 | 1.51 [1.42, 1.61] | 1.46–1.57 |
| baseline | SF100 | 6 | 1.98 [1.86, 2.11] | 1.91–2.08 |
| baseline | SF300 | 6 | 2.07 [1.83, 2.34] | 1.90–2.29 |
| complex | SF30 | 6 | 2.74 [2.62, 2.87] | 2.62–2.78 |
| complex | SF100 | 6 | 4.10 [4.09, 4.11] | 4.09–4.10 |
| complex | SF300 | 6 | 4.19 [4.15, 4.24] | 4.15–4.22 |

### Polars CPU / best GPU (both GPU engines supported) (C4 sensitivity, RQ3 sensitivity)

| Suite | Scale | Queries | Speedup [95% CI] | Round range |
|---|---|---|---|---|
| baseline | SF1 | 6 | 0.330 [0.297, 0.366] | 0.307–0.350 |
| baseline | SF10 | 6 | 0.917 [0.885, 0.950] | 0.900–0.943 |
| baseline | SF30 | 6 | 1.51 [1.42, 1.61] | 1.46–1.57 |
| baseline | SF100 | 6 | 1.98 [1.86, 2.11] | 1.91–2.08 |
| baseline | SF300 | 6 | 2.07 [1.83, 2.34] | 1.90–2.29 |
| complex | SF30 | 4 | 2.87 [2.68, 3.06] | 2.72–3.01 |
| complex | SF100 | 4 | 3.57 [3.50, 3.65] | 3.53–3.64 |
| complex | SF300 | 4 | 3.32 [3.15, 3.49] | 3.22–3.47 |

### Interval tally

| Contrast | Suite | Scales with CI above 1 | Scales with CI below 1 | Scales with CI containing 1 |
|---|---|---|---|---|
| Polars CPU / Polars GPU | baseline | SF30, SF100, SF300 | SF1, SF10 | – |
| Polars CPU / Polars GPU | complex | SF30, SF100, SF300 | – | – |
| Spark CPU / Spark GPU | baseline | SF10, SF30, SF100, SF300 | SF1 | – |
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

96 Spark cells (1536 trials with a saved plan, 64 sessions) were checked; 2 have more than one final adaptive plan. Descriptive. The final adaptive plan of every Spark trial (plans/<query>-<phase>-<trial>.txt) is normalized (expression, plan, stage and lore ids removed) and hashed; a cell is listed when its trials do not all end in the same plan. Medians, estimates and intervals are unchanged by this section: every measured trial still enters its cell's median as designed.

**baseline SF10 q03 (Spark CPU)**: 2 final plans in 16 trials (12 measured, 4 warm-up); initial plan identical in every trial. The measured times of the plans overlap (median of the slowest over the fastest plan: 1.15).

- Plan 1a528022a1c0 against plan 17ec724f7006: exchanges `hashpartitioning(l_orderkey, o_orderdate, o_shippriority, 120)`: 0 to 1; exchanges `hashpartitioning(o_custkey, 120)`: 0 to 1; shuffle readers `coalesced`: 2 to 3; shuffle readers `local`: 1 to 2. Operator lines only in this plan: `*(7) BroadcastHashJoin [o_custkey], [c_custkey], Inner, BuildRight, false`, `*(7) HashAggregate(keys=[l_orderkey, o_orderdate, o_shippriority], functions=[partial_sum((l_extendedprice * (`, `*(7) Project [l_orderkey, l_extendedprice, l_discount, o_orderdate, o_shippriority]`, `*(8) HashAggregate(keys=[l_orderkey, o_orderdate, o_shippriority], functions=[sum((l_extendedprice * (1.0 - l_`. Per action (Spark event log, median): stages 5.00 to 7.00; tasks 118 to 144; shuffle bytes written 0.509 GiB to 0.537 GiB.

**complex SF30 cx_q09 (Spark GPU)**: 2 final plans in 16 trials (12 measured, 4 warm-up); initial plan identical in every trial. The measured times of the plans do not overlap (median of the slowest over the fastest plan: 2.03).

- Plan 5166c91b662d against plan a4362d8c1e3e: exchanges `gpuhashpartitioning(l_partkey, 120)`: 0 to 1; shuffle readers `local`: 0 to 1. Operator lines only in this plan: `GpuColumnarExchange gpuhashpartitioning(l_partkey, 120, Murmur3Mode), ENSURE_REQUIREMENTS, [plan_id=N]`, `GpuCustomShuffleReader local`, `GpuShuffleCoalesce 1073741824`, `ShuffleQueryStage N`. Per action (Spark event log, median): stages 12.0 to 13.0; tasks 216 to 336; shuffle bytes written 1.11 GiB to 5.78 GiB.

| Suite | Scale | Query | Engine | Plan | Measured trials (per round) | Warm-ups | Measured s (min / median / max) | Exchanges | Shuffle readers | Stages / tasks (median per action) | Shuffle written (GiB, median) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | SF10 | q03 | Spark CPU | 17ec724f7006 * | 10 (r1: 2; r2: 3; r3: 3; r4: 2) | 0 | 2.03/2.65/4.89 | 3 | coalesced 2, local 1 | 5.00 / 118 | 0.509 |
| baseline | SF10 | q03 | Spark CPU | 1a528022a1c0 | 2 (r1: 1; r4: 1) | 4 | 2.69/3.04/3.40 | 5 | coalesced 3, local 2 | 7.00 / 144 | 0.537 |
| complex | SF30 | cx_q09 | Spark GPU | a4362d8c1e3e * | 1 (r2: 1) | 4 | 3.14/3.14/3.14 | 7 | coalesced 6, plain 1 | 12.0 / 216 | 1.11 |
| complex | SF30 | cx_q09 | Spark GPU | 5166c91b662d | 11 (r1: 3; r2: 2; r3: 3; r4: 3) | 0 | 6.25/6.37/7.23 | 8 | coalesced 6, local 1, plain 1 | 13.0 / 336 | 5.78 |

* plan with the lowest median measured time in its cell (the reference for the listed changes).


## 7. Variability and order effects

| Engine | Within: median CV | Within: p90 | Within: max | Between: median CV | Between: p90 | Between: max |
|---|---|---|---|---|---|---|
| Polars CPU | 5.8% | 24.3% | 87.7% | 3.4% | 10.6% | 44.8% |
| Polars GPU | 2.3% | 6.9% | 22.0% | 1.8% | 4.7% | 11.6% |
| Spark CPU | 5.7% | 26.4% | 76.7% | 5.1% | 19.5% | 53.1% |
| Spark GPU | 3.0% | 7.0% | 35.0% | 2.0% | 5.3% | 10.2% |

Within-session CV uses the 3 measured trials of each cell and round; between-round CV uses the per-round medians of each cell.


| Position | Cells | Mean deviation [95% CI] |
|---|---|---|
| 1 | 186 | +1.0% [-0.2, +2.1] |
| 2 | 186 | -0.2% [-1.1, +0.7] |
| 3 | 186 | +0.2% [-4.0, +4.6] |
| 4 | 186 | -1.0% [-4.0, +2.1] |

Deviation of each cell's time from its own mean over rounds, averaged over cells, then a t-interval over rounds. Reported descriptively; the Williams design balances positions across engines.


| Predecessor | Cells | Mean deviation [95% CI] |
|---|---|---|
| none (first in block) | 186 | +1.0% [-0.2, +2.1] |
| Polars CPU | 138 | +1.1% [-5.0, +7.5] |
| Polars GPU | 144 | -1.6% [-5.8, +2.8] |
| Spark CPU | 138 | -0.9% [-4.2, +2.5] |
| Spark GPU | 138 | +0.2% [-2.4, +2.8] |


## 8. Legacy sum-of-medians totals (kept separate from the geometric means)

| Suite | Scale | Engine | Sum of medians (s) [min–max] |
|---|---|---|---|
| baseline | SF1 | Polars CPU | 0.227 [0.214–0.237] |
| baseline | SF1 | Polars GPU | 0.606 [0.602–0.612] |
| baseline | SF1 | Spark CPU | 2.69 [2.42–3.02] |
| baseline | SF1 | Spark GPU | 3.49 [3.46–3.53] |
| baseline | SF10 | Polars CPU | 1.39 [1.36–1.43] |
| baseline | SF10 | Polars GPU | 1.19 [1.17–1.21] |
| baseline | SF10 | Spark CPU | 9.15 [8.30–9.53] |
| baseline | SF10 | Spark GPU | 6.23 [6.09–6.39] |
| baseline | SF30 | Polars CPU | 4.41 [4.28–4.67] |
| baseline | SF30 | Polars GPU | 2.20 [2.14–2.24] |
| baseline | SF30 | Spark CPU | 20.9 [18.8–22.6] |
| baseline | SF30 | Spark GPU | 10.3 [10.2–10.4] |
| baseline | SF100 | Polars CPU | 14.4 [14.1–14.6] |
| baseline | SF100 | Polars GPU | 5.83 [5.77–5.90] |
| baseline | SF100 | Spark CPU | 52.7 [49.8–55.6] |
| baseline | SF100 | Spark GPU | 21.9 [21.4–23.3] |
| baseline | SF300 | Polars CPU | 48.1 [45.2–50.5] |
| baseline | SF300 | Polars GPU | 20.0 [19.8–20.2] |
| baseline | SF300 | Spark CPU | 134 [131–137] |
| baseline | SF300 | Spark GPU | 58.2 [56.2–60.3] |
| complex | SF30 | Polars CPU | 18.0 [17.9–18.1] |
| complex | SF30 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF30 | Spark CPU | 50.0 [48.3–50.7] |
| complex | SF30 | Spark GPU | 17.2 [16.7–18.1] |
| complex | SF100 | Polars CPU | 67.2 [67.1–67.4] |
| complex | SF100 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF100 | Spark CPU | 140 [139–140] |
| complex | SF100 | Spark GPU | 49.1 [48.8–49.3] |
| complex | SF300 | Polars CPU | 211 [210–211] |
| complex | SF300 | Polars GPU | n/a: cx_q13, cx_window |
| complex | SF300 | Spark CPU | 351 [345–356] |
| complex | SF300 | Spark GPU | 173 [171–175] |

Totals exist only when every query has a valid median in every round; never combined with the geometric means of t01.


| Suite | Scale | Contrast | Ratio of mean totals |
|---|---|---|---|
| baseline | SF1 | Polars CPU / Polars GPU | 0.374 |
| baseline | SF1 | Spark CPU / Spark GPU | 0.769 |
| baseline | SF1 | Spark GPU / Polars GPU | 5.76 |
| baseline | SF1 | Polars CPU / best GPU engine | 0.374 |
| baseline | SF1 | Polars CPU / best GPU (both GPU engines supported) | 0.374 |
| baseline | SF10 | Polars CPU / Polars GPU | 1.17 |
| baseline | SF10 | Spark CPU / Spark GPU | 1.47 |
| baseline | SF10 | Spark GPU / Polars GPU | 5.23 |
| baseline | SF10 | Polars CPU / best GPU engine | 1.17 |
| baseline | SF10 | Polars CPU / best GPU (both GPU engines supported) | 1.17 |
| baseline | SF30 | Polars CPU / Polars GPU | 2.00 |
| baseline | SF30 | Spark CPU / Spark GPU | 2.03 |
| baseline | SF30 | Spark GPU / Polars GPU | 4.68 |
| baseline | SF30 | Polars CPU / best GPU engine | 2.00 |
| baseline | SF30 | Polars CPU / best GPU (both GPU engines supported) | 2.00 |
| baseline | SF100 | Polars CPU / Polars GPU | 2.46 |
| baseline | SF100 | Spark CPU / Spark GPU | 2.40 |
| baseline | SF100 | Spark GPU / Polars GPU | 3.76 |
| baseline | SF100 | Polars CPU / best GPU engine | 2.46 |
| baseline | SF100 | Polars CPU / best GPU (both GPU engines supported) | 2.46 |
| baseline | SF300 | Polars CPU / Polars GPU | 2.41 |
| baseline | SF300 | Spark CPU / Spark GPU | 2.30 |
| baseline | SF300 | Spark GPU / Polars GPU | 2.92 |
| baseline | SF300 | Polars CPU / best GPU engine | 2.41 |
| baseline | SF300 | Polars CPU / best GPU (both GPU engines supported) | 2.41 |
| complex | SF30 | Polars CPU / Polars GPU | – |
| complex | SF30 | Spark CPU / Spark GPU | 2.90 |
| complex | SF30 | Spark GPU / Polars GPU | – |
| complex | SF30 | Polars CPU / best GPU engine | 1.04 |
| complex | SF30 | Polars CPU / best GPU (both GPU engines supported) | – |
| complex | SF100 | Polars CPU / Polars GPU | – |
| complex | SF100 | Spark CPU / Spark GPU | 2.84 |
| complex | SF100 | Spark GPU / Polars GPU | – |
| complex | SF100 | Polars CPU / best GPU engine | 1.37 |
| complex | SF100 | Polars CPU / best GPU (both GPU engines supported) | – |
| complex | SF300 | Polars CPU / Polars GPU | – |
| complex | SF300 | Spark CPU / Spark GPU | 2.03 |
| complex | SF300 | Spark GPU / Polars GPU | – |
| complex | SF300 | Polars CPU / best GPU engine | 1.22 |
| complex | SF300 | Polars CPU / best GPU (both GPU engines supported) | – |

Not comparable with the geometric means of t01: a sum is dominated by the longest queries.


## 9. Secondary metrics

### GPU memory per engine and scale (what is measured differs by engine: see the Measure column)

| Suite | Scale | Engine | Measure | Min–max over queries (GiB) | Median (GiB) | Max / device |
|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 2.0–2.0 | 2.0 | 5.0% |
| baseline | SF1 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| baseline | SF1 | Spark GPU | task-reported max (not a total) | 0.2–0.3 | 0.3 | 0.9% |
| baseline | SF10 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 8.5–8.5 | 8.5 | 21.3% |
| baseline | SF10 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| baseline | SF10 | Spark GPU | task-reported max (not a total) | 0.7–1.9 | 0.9 | 4.8% |
| baseline | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 9.7–9.7 | 9.7 | 24.1% |
| baseline | SF30 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| baseline | SF30 | Spark GPU | task-reported max (not a total) | 1.9–6.0 | 2.6 | 15.1% |
| baseline | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 17.6–17.8 | 17.7 | 44.5% |
| baseline | SF100 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| baseline | SF100 | Spark GPU | task-reported max (not a total) | 1.7–6.9 | 3.4 | 17.3% |
| baseline | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 22.7–22.7 | 22.7 | 56.6% |
| baseline | SF300 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| baseline | SF300 | Spark GPU | task-reported max (not a total) | 3.3–9.5 | 4.1 | 23.7% |
| complex | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 14.2–14.6 | 14.6 | 36.5% |
| complex | SF30 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| complex | SF30 | Spark GPU | task-reported max (not a total) | 0.8–5.5 | 2.5 | 13.7% |
| complex | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 20.0–20.0 | 20.0 | 49.9% |
| complex | SF100 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| complex | SF100 | Spark GPU | task-reported max (not a total) | 2.4–12.8 | 5.1 | 32.1% |
| complex | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 34.2–38.9 | 37.2 | 97.2% |
| complex | SF300 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| complex | SF300 | Spark GPU | task-reported max (not a total) | 2.3–31.4 | 5.9 | 78.4% |

max_bytes / median_bytes / min_bytes are the nvidia-smi samples of the worker process tree during the timed action (worker peak_gpu_memory_bytes); maximum over the 3 measured trials, then over rounds; a sampled peak can miss short transients. What the sample means differs by engine: see measure / measure_note, and the task_reported blocks for Spark GPU Polars GPU: nvidia-smi device memory of the worker process, sampled during the timed action. cudf-polars allocates through an RMM async pool that keeps freed blocks (release threshold 90% of the device), so the value is the pool's high-water mark in that session so far, not the working set of the query: it did not fall in 28 of 32 sessions (see high_water_check) but fell in the other 4 (by up to 18.4 GiB; their samples reach 97% of the device), so there it is the pool size at the time of the action, and an upper bound on what the query needs only while the pool keeps its blocks. Spark GPU: the largest device allocation a single task reported (RAPIDS task accumulator gpuMaxDeviceMemoryBytes, Spark event log) among the tasks launched inside the action; not a simultaneous total over concurrent tasks. The nvidia-smi samples (max_bytes, ...) are the RAPIDS pool reservation made at plugin start, not memory in use.

### Process wall time and startup (mean over rounds, seconds; overlapping scopes, do not add)

| Suite | Scale | Engine | Process wall | Launch → ready | Setup | Warmups | Measured |
|---|---|---|---|---|---|---|---|
| baseline | SF1 | Polars CPU | 1.47 | 0.187 | 0.104 | 0.291 | 0.677 |
| baseline | SF1 | Polars GPU | 6.39 | 1.75 | 1.67 | 1.06 | 1.84 |
| baseline | SF1 | Spark CPU | 27.8 | 5.71 | 4.11 | 10.1 | 8.83 |
| baseline | SF1 | Spark GPU | 43.2 | 20.0 | 18.1 | 7.40 | 10.6 |
| baseline | SF10 | Polars CPU | 6.95 | 0.186 | 0.103 | 1.63 | 4.10 |
| baseline | SF10 | Polars GPU | 8.79 | 1.69 | 1.61 | 1.55 | 3.62 |
| baseline | SF10 | Spark CPU | 55.1 | 5.56 | 4.05 | 16.5 | 27.9 |
| baseline | SF10 | Spark GPU | 54.3 | 19.6 | 17.9 | 10.6 | 18.8 |
| baseline | SF30 | Polars CPU | 19.9 | 0.193 | 0.105 | 4.97 | 13.3 |
| baseline | SF30 | Polars GPU | 12.6 | 1.67 | 1.58 | 2.52 | 6.60 |
| baseline | SF30 | Spark CPU | 105 | 5.57 | 4.05 | 29.0 | 62.8 |
| baseline | SF30 | Spark GPU | 73.9 | 19.6 | 17.9 | 15.5 | 31.0 |
| baseline | SF100 | Polars CPU | 60.4 | 0.186 | 0.103 | 14.9 | 44.4 |
| baseline | SF100 | Polars GPU | 27.3 | 1.72 | 1.63 | 6.16 | 17.5 |
| baseline | SF100 | Spark CPU | 246 | 5.59 | 4.07 | 64.5 | 160 |
| baseline | SF100 | Spark GPU | 128 | 19.6 | 17.9 | 28.0 | 66.3 |
| baseline | SF300 | Polars CPU | 208 | 0.195 | 0.111 | 53.1 | 154 |
| baseline | SF300 | Polars GPU | 84.2 | 1.79 | 1.71 | 20.6 | 59.8 |
| baseline | SF300 | Spark CPU | 593 | 5.61 | 4.09 | 148 | 405 |
| baseline | SF300 | Spark GPU | 290 | 19.8 | 18.0 | 61.4 | 176 |
| complex | SF30 | Polars CPU | 73.9 | 0.187 | 0.104 | 18.0 | 53.8 |
| complex | SF30 | Polars GPU | 12.2 | 1.71 | 1.63 | 2.30 | 5.83 |
| complex | SF30 | Spark CPU | 287 | 6.02 | 4.42 | 59.0 | 151 |
| complex | SF30 | Spark GPU | 165 | 20.1 | 18.2 | 18.8 | 51.2 |
| complex | SF100 | Polars CPU | 272 | 0.188 | 0.105 | 67.0 | 202 |
| complex | SF100 | Polars GPU | 28.5 | 1.68 | 1.60 | 6.42 | 18.0 |
| complex | SF100 | Spark CPU | 672 | 5.70 | 4.18 | 149 | 420 |
| complex | SF100 | Spark GPU | 329 | 19.8 | 18.1 | 58.5 | 147 |
| complex | SF300 | Polars CPU | 858 | 0.205 | 0.113 | 212 | 640 |
| complex | SF300 | Polars GPU | 110 | 1.88 | 1.79 | 27.1 | 79.1 |
| complex | SF300 | Spark CPU | 1590 | 6.70 | 4.90 | 363 | 1056 |
| complex | SF300 | Spark GPU | 900 | 21.1 | 19.1 | 191 | 517 |

### verified_gc cleanup cost (outside the query timers)

| Suite | Scale | Engine | Per cleanup (s) | Max (s) | Per session (s) | Share of wall |
|---|---|---|---|---|---|---|
| complex | SF30 | Polars CPU | 0.00758 | 0.0110 | 0.182 | 0.2% |
| complex | SF30 | Polars GPU | 0.0219 | 0.0290 | 0.351 | 2.9% |
| complex | SF30 | Spark CPU | 2.83 | 4.08 | 68.0 | 23.7% |
| complex | SF30 | Spark GPU | 2.87 | 4.15 | 68.9 | 41.7% |
| complex | SF100 | Polars CPU | 0.00798 | 0.0142 | 0.192 | 0.1% |
| complex | SF100 | Polars GPU | 0.0216 | 0.0311 | 0.346 | 1.2% |
| complex | SF100 | Spark CPU | 3.84 | 7.15 | 92.2 | 13.7% |
| complex | SF100 | Spark GPU | 4.04 | 8.23 | 97.1 | 29.5% |
| complex | SF300 | Polars CPU | 0.00795 | 0.0143 | 0.191 | 0.0% |
| complex | SF300 | Polars GPU | 0.0208 | 0.0303 | 0.333 | 0.3% |
| complex | SF300 | Spark CPU | 6.66 | 17.2 | 160 | 10.1% |
| complex | SF300 | Spark GPU | 6.77 | 18.4 | 162 | 18.0% |

### GPU board energy proxy per session (mean power.draw × process wall time)

| Suite | Scale | Engine | Mean power (W) | Wall (s) | Energy (Wh) |
|---|---|---|---|---|---|
| baseline | SF1 | Polars CPU | 52.6 | 1.47 | 0.0215 |
| baseline | SF1 | Polars GPU | 62.3 | 6.39 | 0.111 |
| baseline | SF1 | Spark CPU | 46.5 | 27.8 | 0.359 |
| baseline | SF1 | Spark GPU | 59.4 | 43.2 | 0.713 |
| baseline | SF10 | Polars CPU | 46.6 | 6.95 | 0.0900 |
| baseline | SF10 | Polars GPU | 82.5 | 8.79 | 0.202 |
| baseline | SF10 | Spark CPU | 46.1 | 55.1 | 0.706 |
| baseline | SF10 | Spark GPU | 64.9 | 54.3 | 0.979 |
| baseline | SF30 | Polars CPU | 46.1 | 19.9 | 0.254 |
| baseline | SF30 | Polars GPU | 102 | 12.6 | 0.357 |
| baseline | SF30 | Spark CPU | 46.0 | 105 | 1.34 |
| baseline | SF30 | Spark GPU | 72.2 | 73.9 | 1.48 |
| baseline | SF100 | Polars CPU | 46.0 | 60.4 | 0.772 |
| baseline | SF100 | Polars GPU | 133 | 27.3 | 1.01 |
| baseline | SF100 | Spark CPU | 45.9 | 246 | 3.13 |
| baseline | SF100 | Spark GPU | 82.7 | 128 | 2.93 |
| baseline | SF300 | Polars CPU | 45.7 | 208 | 2.64 |
| baseline | SF300 | Polars GPU | 150 | 84.2 | 3.52 |
| baseline | SF300 | Spark CPU | 45.9 | 593 | 7.56 |
| baseline | SF300 | Spark GPU | 90.1 | 290 | 7.27 |
| complex | SF30 | Polars CPU | 46.0 | 73.9 | 0.945 |
| complex | SF30 | Polars GPU | 110 | 12.2 | 0.373 |
| complex | SF30 | Spark CPU | 45.9 | 287 | 3.65 |
| complex | SF30 | Spark GPU | 66.2 | 165 | 3.04 |
| complex | SF100 | Polars CPU | 45.9 | 272 | 3.47 |
| complex | SF100 | Polars GPU | 144 | 28.5 | 1.14 |
| complex | SF100 | Spark CPU | 46.0 | 672 | 8.58 |
| complex | SF100 | Spark GPU | 73.7 | 329 | 6.74 |
| complex | SF300 | Polars CPU | 46.0 | 858 | 11.0 |
| complex | SF300 | Polars GPU | 148 | 110 | 4.55 |
| complex | SF300 | Spark CPU | 45.9 | 1590 | 20.3 |
| complex | SF300 | Spark GPU | 73.5 | 900 | 18.4 |

GPU board energy proxy = mean nvidia-smi power.draw over the session window x process wall time; CPU and host power are not included, and CPU-only engines show the idle GPU draw

### GPU telemetry inside measured actions (descriptive telemetry; not a pre-registered contrast; no causal claim)

For each GPU engine, suite and scale: the nvidia-smi samples (utilization.gpu, utilization.memory, power.draw, clocks.current.sm) that fall inside the measured actions of the accepted rounds, pooled over queries, trials and rounds. idle_fraction is the share of samples with utilization.gpu below 10% (equal to the share of action wall time at uniform sampling). utilization.gpu is nvidia-smi's share of the sample period in which at least one kernel was executing, not SM occupancy. Descriptive; not a pre-registered contrast; no causal claim. Nvidia-smi is sampled about every 200 ms, so an action of 1 s holds about 5 samples and the first and last sample of an action are partly outside it; actions shorter than 1 s (most SF1 actions) are left out of every statistic and counted in n_actions_excluded_short. Power limit 400 W (nvidia-smi -q, Current Power Limit of the first GPU); maximum SM clock 1410 MHz.


| Suite | Scale | Engine | Actions used / left out (< 1 s) | Samples | GPU util median / p90 (%) | GPU-idle fraction (util < 10%) | Power median W (fraction of limit) | SM clock median MHz (fraction of max) | Memory util median (%) |
|---|---|---|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF1 | Spark GPU | 12 / 60 | 74 | 38 / 89 | 12.2% | 69 W (17%) | 1410 (100%) | 0 |
| baseline | SF10 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF10 | Spark GPU | 26 / 46 | 241 | 45 / 99 | 7.1% | 75 W (19%) | 1410 (100%) | 1 |
| baseline | SF30 | Polars GPU | 0 / 72 | 0 | – | – | – | – | – |
| baseline | SF30 | Spark GPU | 51 / 21 | 557 | 71 / 98 | 12.0% | 78 W (20%) | 1410 (100%) | 1 |
| baseline | SF100 | Polars GPU | 36 / 36 | 229 | 94 / 99 | 0.0% | 160 W (40%) | 1410 (100%) | 22 |
| baseline | SF100 | Spark GPU | 72 / 0 | 1348 | 81 / 97 | 8.9% | 92 W (23%) | 1410 (100%) | 4 |
| baseline | SF300 | Polars GPU | 72 / 0 | 1216 | 89 / 99 | 0.2% | 155 W (39%) | 1410 (100%) | 20 |
| baseline | SF300 | Spark GPU | 72 / 0 | 3530 | 84 / 97 | 6.1% | 96 W (24%) | 1410 (100%) | 4 |
| complex | SF30 | Polars GPU | 0 / 48 | 0 | – | – | – | – | – |
| complex | SF30 | Spark GPU | 66 / 6 | 1026 | 45 / 95 | 23.4% | 76 W (19%) | 1410 (100%) | 2 |
| complex | SF100 | Polars GPU | 36 / 12 | 325 | 90 / 96 | 0.6% | 158 W (39%) | 1410 (100%) | 19 |
| complex | SF100 | Spark GPU | 72 / 0 | 2968 | 47 / 91 | 16.5% | 78 W (20%) | 1410 (100%) | 4 |
| complex | SF300 | Polars GPU | 48 / 0 | 1599 | 91 / 97 | 0.4% | 155 W (39%) | 1410 (100%) | 18 |
| complex | SF300 | Spark GPU | 72 / 0 | 10353 | 36 / 88 | 22.9% | 70 W (18%) | 1410 (100%) | 3 |

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
