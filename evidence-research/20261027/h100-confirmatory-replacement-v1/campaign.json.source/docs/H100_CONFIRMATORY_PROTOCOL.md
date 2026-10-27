# H100 confirmatory protocol, version 1

Status: prospective protocol. This protocol and its complete randomized
schedule must be committed and hashed before the first confirmatory session.
The October 26 H100 runs at source `c4c934cf732b4394e2bcb0547bb642ee2a1ae5d4`
are exploratory pilots and will not enter the confirmatory sample.

## Question and scope

On the same single H100 host and physical Parquet files, how do the four engine
paths compare under a fixed configuration and preheated-filesystem policy?
Where do the measured scale points support GPU advantages over CPU Polars 2.0?
The study is scoped to these versions, queries, machine and settings. It does
not estimate distributed scaling, cold-start latency, energy, cost, or the best
achievable performance after tuning every engine.

Preserve the entire pilot workload: baseline Q01/Q03/Q06/Q10/Q12/Q14 at
SF1/10/30/100/300 and the six versioned complex queries at SF30/100/300. Monetary
and quantity columns use the existing Float64 semantics. These are selected
TPC-H-derived and custom workloads, not a complete or certified TPC-H run.
Existing attribution and [notice research](../publication/NOTICE_RESEARCH.md)
remain part of the artifact; no TPC metric or compliance claim is made.

## Configurations

| Configuration ID | Engine | Runtime |
|---|---|---|
| `polars_cpu_144` | Polars CPU streaming | Polars 1.44.2 |
| `polars_cpu_200` | Polars CPU streaming | Polars 2.0.0 |
| `polars_gpu` | strict cuDF-Polars streaming | Polars 1.44.2; cuDF-Polars 26.10.2 |
| `spark_cpu` | Spark CPU | Spark 3.5.9 |
| `spark_gpu` | Spark with RAPIDS plugin | Spark 3.5.9; plugin 26.08.1 |

All configurations use the same CPython **3.11.13 executable and build**. The
CPU 2.0 virtual environment matches the common non-GPU package versions; its
GPU-only dependencies differ because cuDF-Polars requires Polars <1.45. The
earlier Python 3.10 CPU control remains a separate historical bridge. Freeze
and archive actual distribution inventories and the Java/JAR hashes.

Use all 26 allocated vCPUs, the existing Spark `local[26]`, 96 GiB driver heap,
and `a100-parity` configuration. Keep GPU strict-failure guards, the existing
streaming executor options, and the suite-specific cleanup policy: `none` for
baseline, `verified_gc` outside timers for complex. No configuration tuning is
allowed after confirmatory timings begin.

## Experimental units, schedule and stopping

Collect **10 blocks × 8 suite/scale conditions × 5 configurations = 400 fresh
engine sessions**. Each session has one warmup per query and three measured
actions per supported query. A session starts a fresh Python engine or Spark
JVM; no session, table cache or allocator persists between configurations.
Three inner actions estimate a session median; they are not three independent
replications. Paired block observations are the units used for inference.

For each condition, use the ten sequences of the odd-five Williams design:
base `[0,1,4,2,3]`, its five cyclic label shifts and their reversals. Seeded
random relabeling and sequence allocation give each configuration each position
twice and each ordered predecessor pair twice within a condition. Randomize
the eight condition orders separately in every block. Use one recorded query
shuffle seed for all five configurations in a block/condition. Warmup query
order remains fixed, matching the pilot. Archive the entire schedule before
execution; no adaptive reordering based on observed speed is permitted.

Ten blocks is a fixed, balanced replication allocation, **not a power
guarantee**. Run all ten regardless of the emerging results. Do not stop when
a comparison becomes significant or add selected repetitions to narrow a
favorable interval. Temporal drift and dependence remain possible on one
host; show block-level observations and report these limitations.

## Timing and host state

Primary endpoint: existing action wall time, from query construction through
materialized `collect()`, excluding session setup, plan serialization and
cleanup. It includes optimization, file reads and execution. Polars returns
a host DataFrame; Spark returns Python Rows. This endpoint retains that API
conversion asymmetry for continuity.

Secondary endpoint: the same start through a list of Python dictionaries for
both engines (`host_result_seconds`), excluding normalization, plan capture,
sampler shutdown and cleanup. Report this endpoint alongside the primary
results and flag conclusions that change. Record tight action start/end bounds,
startup and full process wall time separately. A suite score sums six query
medians; it is not an observed once-through suite latency.

Read all Parquet files for the selected scale before each session. Record
Linux `mincore` residency immediately before and after preheating and after
the engine exits; also record available/cached memory, swap, VM faults and
disk counters. Residency is a snapshot, not a guarantee that pages remain
resident during a query. Do not describe this as controlled cold-cache or
guaranteed in-memory performance. Save per-process cumulative I/O and page
fault snapshots around actions, with their process-population limits.

Keep existing host/GPU telemetry, CPU affinity/frequency-policy inventory,
temperature/clocks/throttling information where exposed, and process names.
Sampled memory is an observed peak, not an exact allocator peak. Kernel traces
or profiling, if added, run separately and never contribute primary timings.
Plans establish selected operators, not GPU runtime fractions.

## Correctness and coverage gates

Before confirmatory sessions, execute independent DuckDB SQL against every
suite/scale input, outside performance measurement. Archive SQL hashes,
runtime, manifest hashes and normalized results. Compare every successful
warmup and measured output to this independent result: identical column sets,
row counts and ordered rows; exact nulls, strings, dates and integer pairs;
finite floating values with relative tolerance `1e-8` and absolute tolerance
`1e-6`. These tolerances preserve the pilot rule and accommodate parallel
Float64 reductions; include absolute/relative discrepancies in the audit.
They are not a substitute for decimal financial semantics.

Add hand-checked, small semantic fixtures for duplicate and null join keys,
outer-count semantics, nonempty semi/anti branches and rank ties. Report their
coverage separately from the unchanged primary query scores. The original
correlated query's anti branch may be optimized away; make no nonempty
anti-join performance claim from that query.

Keep the original strict GPU regex and grouped-window expressions. Record
their rejection at every planned session. Never substitute a rewritten query,
enable CPU fallback, impute a time, or compare a four-query GPU sum with a
six-query suite. Any new failure is retained as a coverage outcome and gates
the affected comparison. No engine may obtain a complete-suite score unless
all six queries have all three successful, oracle-valid measurements.

## Analysis and predefined comparisons

For each session/query use the median of its three measured action times.
For a complete session/suite sum its six medians. In each paired block compute
the ratio **CPU or reference score / GPU score**; a ratio above one favors the
GPU. Analyze log ratios across the ten blocks. Report geometric mean ratios
and two-sided Student-t 95% confidence intervals, with the approximate
normality/independence assumption explicit. Show raw block ratios, log-ratio
spread and block order; ten observations may leave considerable uncertainty.
Do not treat percentile bootstrap resampling or inner trials as extra data.

The single primary family contains **26 two-sided tests** of mean log ratio
zero, adjusted together by Holm's procedure at familywise alpha 0.05:

1. CPU Polars 2.0 / GPU Spark: all eight conditions.
2. CPU Spark / GPU Spark: all eight conditions.
3. CPU Polars 1.44.2 / Polars GPU: five baseline scales.
4. CPU Polars 2.0 / Polars GPU: five baseline scales.

Pointwise 95% intervals are not simultaneous intervals. A primary supported
advantage requires a ratio above one and Holm-adjusted p <0.05; lack of support
is not equivalence. Preserve every failed, incomplete and unsupported outcome;
never reduce the tested family by silently dropping a failed comparison.
Query-level ratios and secondary host-result endpoints are descriptive, with
pointwise intervals explicitly labeled exploratory; no additional primary
significance claims will be selected from them.

For a scale crossover, report only sampled points. A supported bracket requires
a supported advantage at the upper sampled point and no contradictory larger
sampled point. Do not infer an exact byte threshold, interpolate confidence
through unmeasured scales, or claim a universal crossover. Distinguish an
unsupported comparison from evidence of a reversal.

## Failures, integrity and deviations

No automatic time-based exclusions, outlier trimming or selective retries.
Unexpected query failures remain in the session. A process failure, timeout,
interruption, changed source/dependency/input hash, failed cache observation,
or nonempty orphan process group halts the campaign for diagnosis; preserve
the attempt and its partial outputs. Resume only through an explicit recorded
recovery operation after confirming no owned worker remains alive. A replacement
attempt is a documented protocol deviation, not an invisible clean run. The
analysis must identify attempts/deviations and refuse to silently accept missing
blocks, duplicate sessions or invalid outputs.

Use the same validated data manifests as the pilot; record full file hashes
before and after the campaign. No input regeneration or mutation during runs.
Before starting, require sufficient disk space, idle GPU with no unowned
compute client, no unowned benchmark JVM/Python worker, and working strict GPU
guards. Retain environmental warnings and measurements, with no post-hoc
numeric exclusion thresholds. Heavy oracle checks, hashing and tests run
outside timed work. Do not run concurrent benchmark sessions.

Archive the frozen code commit, protocol SHA, immutable schedule SHA, package
inventories, raw results, failures, plans, telemetry and oracle evidence. A
manuscript and reproducible analysis must distinguish these prospective results
from the exploratory A100/H100 pilots. Keep development private.

## Method sources

- Kalibera and Jones, [Rigorous benchmarking in reasonable time (2013)](https://kar.kent.ac.uk/33611/45/p63-kaliber.pdf): repetition levels and uncertainty.
- NIST, [Randomized block designs](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm): blocking and randomized allocation.
- Mytkowicz et al., [Producing wrong data without doing anything obviously wrong (2009)](https://research.ibm.com/publications/producing-wrong-data-without-doing-anything-obviously-wrong): sensitivity to experimental layout.
- Hesterberg, [What teachers should know about the bootstrap (2015)](https://arxiv.org/pdf/1411.5279): limits of small-sample bootstrap inference.
- [TPC-H specification 3.0.1](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC-H_v3.0.1.pdf): the complete benchmark differs from this selected workload study.
- [Polars GPU support](https://docs.pola.rs/user-guide/gpu-support/) and [RAPIDS Spark 26.08.1 compatibility](https://github.com/NVIDIA/cudf-spark/blob/v26.08.1/docs/compatibility.md): strict execution and semantic differences. Installed source/pins take precedence over rolling examples.
