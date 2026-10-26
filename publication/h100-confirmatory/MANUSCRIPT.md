# CPU and GPU dataframe execution on one H100 host: a prospective blocked comparison of Polars and Spark

**Technical draft — confirmatory results pending, 2026-10-26.** The 400-session
campaign is in progress. Ten-block estimates, superiority findings, crossover
conclusions, authorship and publication metadata remain pending. This draft is
not ready for external publication.

## Abstract

We compare Polars CPU, cuDF-Polars GPU, Spark CPU, and GPU-accelerated Spark on
one H100 host, including compatible Polars 1.44.2 paths and a same-Python
Polars 2.0 CPU control. Six selected queries derived from TPC-H run at five
scales; six complex queries run at three scales. The prospective design
specifies ten paired blocks, 400 fresh sessions, independent SQL correctness
checks, and 26 primary comparisons with Holm adjustment. Primary action time
and secondary common-host-result time remain distinct. Pre-start validation
compared 116 successful outputs with SQL references; separate diagnostics
retain strict GPU coverage limitations. **Performance estimates and conclusions
await the completed ten-block campaign.** These selected research workloads
are not complete or certified TPC-H results.

## 1. Research question and scope

We ask where fixed configurations provide a supported GPU advantage on shared
physical inputs, which original expressions execute under strict Polars GPU
translation, and which operators appear in final Spark plans.

An exploratory H100 pilot motivated the comparisons and current CPU control.
Its within-process repeats do not establish process-level replication. The
prospective campaign retains the workload and collects fresh sessions in paired
blocks, addressing repetition levels, uncertainty and setup bias.
[Kalibera and Jones](https://kar.kent.ac.uk/33611/45/p63-kaliber.pdf),
[Mytkowicz et al.](https://research.ibm.com/publications/producing-wrong-data-without-doing-anything-obviously-wrong)

The scope excludes distributed scaling, equal-cost performance, energy,
controlled cold-cache latency and optimal per-engine tuning. Historical
A100/H100 comparisons also change CPU allocation, software and file layout.

## 2. Materials and implementations

### 2.1 Host and software

The Linux KVM host exposes one H100 80 GB HBM3 GPU, 26 Xeon Platinum 8480+
vCPUs and 237,490,950,144 RAM bytes, approximately 221 GiB. Inventory records
driver 570.148.08, OpenJDK 17.0.20.1, CUDA driver API 12.8 and runtime 12.9.

| Configuration ID | Execution path | Principal version |
| --- | --- | --- |
| `polars_cpu_144` | Polars CPU streaming | Polars 1.44.2 |
| `polars_cpu_200` | Polars CPU streaming | Polars 2.0.0 |
| `polars_gpu` | cuDF-Polars streaming, CPU fallback rejected | Polars 1.44.2; cuDF-Polars 26.10.2 |
| `spark_cpu` | Spark CPU, `local[26]` | Spark 3.5.9 |
| `spark_gpu` | Spark with RAPIDS plugin, `local[26]` | Spark 3.5.9; plugin 26.08.1 |

All use the same CPython 3.11.13 base executable/build; auxiliary non-GPU
package pins match. GPU dependencies differ because the selected
cuDF-Polars release requires older Polars. The historical CPU control used
Python 3.10.12 and remains separate.

Spark retains the `a100-parity` settings, including a 96 GiB driver heap,
120 shuffle partitions, adaptive query execution, and UTC session time zone.
GPU Spark uses two concurrent GPU tasks and a 2 GiB pinned-memory pool. Effective
settings and the plugin JAR hash are archived. Polars uses the
allocated 26-thread setting without an equivalent fixed host-memory limit.
Consequently, common hardware does not imply equal memory budgets. Spark AQE
can change joins and partitioning using runtime statistics; final plans are
retained for interpretation. [Spark 3.5.9 documentation](https://spark.apache.org/docs/3.5.9/sql-performance-tuning.html#adaptive-query-execution)

Polars GPU sets `raise_on_fail=True`, verifies the effective configuration, runs
a positive GPU canary, and requires an unsupported CPU-only canary to raise.
The streaming policy `fallback_mode=warn` concerns a possible fallback to a
single GPU partition in the installed release; it does not authorize CPU
translation fallback. Spark GPU may retain CPU operators. Installed source
and guards define behavior; rolling documentation is contextual.
[Polars GPU support](https://docs.pola.rs/user-guide/gpu-support/),
[pinned Spark plugin compatibility](https://github.com/NVIDIA/cudf-spark/blob/v26.08.1/docs/compatibility.md)

### 2.2 Data and workload definitions

DuckDB 1.5.6's `tpch` extension generated the inputs, exported as Snappy Parquet
under the archived recipe. Configurations share files and fixed manifests;
full SHA256 checks run before/after the campaign outside measurements. Nominal
scale factors are generator settings. Physical compressed bytes below describe
all tables, not scan volume or query working set.

| Nominal scale factor | Physical Parquet bytes | Performance suites |
| ---: | ---: | --- |
| 1 | 325,716,220 | Baseline |
| 10 | 3,442,803,045 | Baseline |
| 30 | 10,568,906,365 | Baseline and complex |
| 100 | 35,652,242,574 | Baseline and complex |
| 300 | 107,978,166,411 | Baseline and complex |

The baseline contains Q01, Q03, Q06, Q10, Q12, and Q14 adaptations. The
`complex-v1` suite contains a six-table Q9-derived join, Q13-derived outer join
with nested aggregation, correlated semi/anti predicates, two window
orderings, exact distinct counts, and a fact-table self-join with an inequality.
Query parameters, deterministic output ordering, and SQL/native definitions
are frozen. Selected monetary and quantity columns are converted to Float64
or DOUBLE, rather than preserving decimal financial semantics.

Fixed parameters, custom queries, omitted standard queries/refresh tests, and
experimental timing distinguish this workload from the standard. DuckDB also
documents generated-data differences. No QphH, official TPC metric or
certification claim is made. [DuckDB 1.5 extension documentation](https://duckdb.org/docs/1.5/core_extensions/tpch),
[TPC-H specification 3.0.1](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC-H_v3.0.1.pdf)

Disk bytes exceeding GPU memory do not establish out-of-core capacity:
projection, filtering, compression and intermediates determine working sets.
Sampled peaks are not exact allocator maxima.

## 3. Prospective experimental design

### 3.1 Frozen allocation and replication

The pre-start archive binds code commit
`c84ef6a07341f350dae6636ab709c1b65fd894e0`, a 52-file source snapshot, the protocol,
exact runtime inventories, eight independent suite/scale oracles, and readiness
evidence. The campaign was created at 2026-10-26 18:11:05 UTC and archived before
the first confirmatory performance session. Its SHA256 is
`69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0`.

The design is ten blocks by eight suite/scale conditions by five
configurations, yielding 400 fresh sessions. Each supported query has one
recorded warmup, excluded from performance estimates, and three measured actions
in a session. A fresh Python process
or Spark JVM prevents reuse of the previous configuration's engine session,
application table cache, or allocator. The three actions define a session
median; they do not increase the independent inferential sample size beyond
ten paired blocks.

For each condition, the ten sequences of an odd-five Williams design balance
each configuration across positions and each ordered predecessor pair. The
base sequence is `[0,1,4,2,3]`; cyclic relabelings and reversals supply the ten
orders. Seed 20261026 fixes relabeling and sequence allocation. Condition order
is randomized separately in each block. The five configurations within a
block/condition share a recorded measured-query shuffle seed; warmup query
order is fixed. Blocking and randomization reduce nuisance effects but do not
prove independence or remove all temporal drift. [NIST randomized-block guidance](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm)

Ten blocks is a fixed allocation, not a power guarantee. All ten are required,
without significance-based stopping, trimming, selective repetition or tuning.
Unexpected errors halt execution; retained attempts and partial outputs support
explicit recovery and deviation assessment.

### 3.2 Timing boundaries and host state

The primary `seconds` endpoint starts before query construction and ends after
materialized `collect()`. It includes optimization, Parquet reading, execution,
and result collection, while excluding process/session setup, table setup,
plan serialization, sampler shutdown, and cleanup. Polars returns a host
DataFrame; Spark returns Python Rows. The endpoint retains this API conversion
asymmetry for continuity with the pilot.

Secondary `host_result_seconds` continues from the same start through Python
dictionary conversion in both paths, excluding normalization/serialization.
Endpoint direction changes will be reported. Startup, process wall time and
action bounds are separate. A sum of six query medians is a suite score, not
observed once-through latency.

All Parquet files at the selected scale are read before each session.
Linux `mincore` snapshots are recorded immediately before/after this preheat
and after the worker exits, together with memory, swap, faults, and disk
counters. A residency snapshot cannot guarantee that pages remain resident
during execution. The policy is attempted filesystem preheating with observed
residency, not a controlled cold-cache or guaranteed in-memory benchmark.
Application DataFrame caching is disabled.

Baseline has no explicit cleanup barrier; complex uses verified Python/JVM GC
and active-job/shuffle checks outside action timers. Cleanup remains in process
wall time. Sequential execution, exclusivity checks and process-group cleanup
support control without establishing dedicated hardware or constant clocks.
Plans show operator selection, not kernel/transfer times or runtime fractions.
Additional profiling must remain outside primary timings.

### 3.3 Correctness and coverage

Before confirmation, DuckDB 1.5.6 executes all original SQL definitions for all
eight measured conditions using the same Float64 normalization. Each oracle
contains complete ordered rows, SQL/source/manifest hashes, and effective
resource settings. Its execution is outside competitor performance scores.
This provides an independent execution engine for the specified SQL, not an
independent proof that the SQL specification is correct.

Every successful warmup and measured result must match its frozen oracle:
column sets, row counts, and ordered rows; exact nulls, strings, dates, and
integer pairs; finite floating values within relative tolerance `1e-8` or
absolute tolerance `1e-6`. The audit retains numeric discrepancies. Parallel
floating reductions may differ in operation order, so tolerance-based agreement
does not imply bitwise identity or exact decimal arithmetic.

Strict Polars GPU rejection of the original Q13 DOTALL-regex expression and
grouped-window expression remains explicit. Rejections have no imputed time.
A complete suite score requires all six queries and all three measured actions
to succeed and pass the oracle. Four supported complex queries cannot supply
a six-query Polars GPU score. Coverage is part of the result.

Five separate hand-checked semantic cases cover duplicate/null join keys,
outer counts, a nonempty semi/anti exclusion, rank ties/nulls, and multiline
regex/distinct behavior. Their corrected diagnostic plans do not replace any
performance query. A regression preserves a null-ordering mismatch observed in
the original Polars 1.44.2 window port: with prices 100, 100, null, 50, its
top-three keys are 3, 1, 2 instead of SQL's 1, 2, 4. Full-scale input-domain
checks find no null price/date/order/customer keys in the generated `orders`
table at all five scales. Original-port claims are bounded to that tested
domain; the adversarial limitation remains disclosed.

### 3.4 Estimands, uncertainty, and multiplicity

For block `b`, configuration `e`, and query `q`, let `m[b,e,q]` be the median
of the three measured primary action times. A complete-suite score is
`S[b,e] = sum_q m[b,e,q]`. For a prespecified reference/GPU comparison,
`L[b] = log(S[b,reference] / S[b,GPU])`. The reported speedup estimate is
`exp(mean_b L[b])`. Thus it is the geometric mean of paired block score
ratios, rather than a ratio of pooled times or an arithmetic mean speedup.
Ratios above one favor the denominator GPU configuration.

Two-sided pointwise 95% intervals are obtained by exponentiating
`mean(L) ± t(0.975,9) × sd(L)/sqrt(10)`. The corresponding two-sided tests
evaluate mean log ratio zero. The model assumes independent, approximately
normal block log ratios. Raw ratios, order and spread support assessment.
Ten blocks may leave wide uncertainty; zero observed variance cannot prove
reproducibility. Inner actions or resamples add no independent observations;
small-sample bootstrapping is not an automatic remedy.
[Hesterberg](https://doi.org/10.1080/00031305.2015.1089789)

| Primary comparison, numerator / denominator | Conditions | Tests |
| --- | --- | ---: |
| CPU Polars 2.0 / GPU Spark | All eight suite/scale conditions | 8 |
| CPU Spark / GPU Spark | All eight suite/scale conditions | 8 |
| CPU Polars 1.44.2 / Polars GPU | Five baseline scales | 5 |
| CPU Polars 2.0 / Polars GPU | Five baseline scales | 5 |

The 26-test family uses Holm adjustment at familywise alpha 0.05.
A supported GPU advantage requires an estimated ratio above one and adjusted
`p < 0.05`, conditional on the model assumptions. Pointwise intervals are not
simultaneous intervals. Lack of a supported difference does not establish
equivalence. Failed/incomplete comparisons remain explicit and do not silently
shrink the family. Query-level and host-result estimates are descriptive
secondary results, with pointwise intervals and no added primary claims.
[Holm](https://www.jstor.org/stable/4615733)

Crossover statements will identify only sampled scale points. The protocol's
supported-bracket rule requires a supported advantage at the upper measured
point without a contradictory larger sampled point. A lower point lacking
support is not evidence of reversal. No exact byte threshold or interpolation
through unmeasured scales will be asserted.

## 4. Available evidence and pending results

### 4.1 Pre-start correctness evidence

Readiness passes with 116 successful comparisons across ten SF1 smoke sessions.
All configurations pass core inner/outer/semi-anti cases. CPU configurations
and GPU Spark pass all five diagnostics; Polars GPU passes three and rejects
rank/window and DOTALL-regex cases. The tiny Spark GPU final plan contains
`LeftAnti`. This establishes small-input semantics/selection, not anti-join
performance or a nonempty anti branch in the original correlated workload.

Eight complete independent oracles are archived for the measured conditions.
The separate post-hoc pilot supplement compares all 936 saved successful
outputs in 40 historical sessions to those SQL references, with zero
mismatches under the stated tolerance rule. This strengthens the pilot's
correctness record; it does not retroactively preregister its timings or add
independent timing repetitions.

### 4.2 Exploratory pilot context

At SF300, the pilot's baseline scores were 12.774 s for Polars GPU, 31.540 s
for its historical Polars 2.0/Python 3.10 CPU control, and 45.528 s for GPU
Spark. Its complex scores were 70.389 s for that CPU control and 143.039 s for
GPU Spark; Polars GPU had no complete complex score. These are sums of six
per-query medians from one session per configuration/condition. They are
descriptive historical observations without process-level confidence intervals.

The new same-Python CPU control requires fresh estimation; historical
Python 3.10 timings cannot populate its prospective results.

### 4.3 Confirmatory performance results — pending

No ten-block estimates are available in this draft. Operational progress and
partial correctness passes do not satisfy the final analysis gate.

| Planned result | Required completed evidence | Current manuscript status |
| --- | --- | --- |
| All 26 primary speedup estimates, intervals, adjusted p values | 400 sessions; final `PASS` validation; predefined analysis | Pending |
| Forty configuration/condition scores and coverage outcomes | Complete saved session summaries | Pending |
| Query-level contrasts and scale patterns | Oracle-valid paired block observations | Pending, descriptive |
| Action versus common-host-result sensitivity | Both endpoints for the same validated actions | Pending, descriptive |
| Cache/host-state and deviation assessment | Final journal, residency/telemetry, full pre/post byte gates | Pending |

Final tables must retain all 26 comparisons and coverage outcomes. Figures
must show measured points, pointwise uncertainty and block ratios. Conclusions
require completed archived analysis.

## 5. Validity and interpretation limits

One virtual host, possible temporal dependence, VM interference and ten blocks
limit generalization and t-model precision. Williams balance does not eliminate
all carryover. Fixed settings, unequal memory policies and differing dependency
inventories constrain causal software comparisons. Selected queries, Float64
semantics and the preserved window-domain limitation constrain workload and
semantic generalization. Unsupported cases have no imputed speed. Mechanistic
explanations involving transfers, compute, spilling or bandwidth require
separate runtime evidence.

## 6. Reproducibility and release status

The [claim/evidence matrix](claim-evidence-matrix.md) links the private pre-start
archive and pending raw sessions, journal, integrity gates, deviations, plans,
telemetry, validation, tables and figure manifests.

`research_validate.py` gates `research_analysis.py`; the separate
`research_reporting/render.py` produces PNG/SVG/PDF figures from complete real
analysis. Synthetic test exports are excluded. Copied artifacts preserve
hashed JSON bytes and use path remapping. Final harvested-archive replay is
required.

Development remains private. Authorship, availability, attribution and unresolved
[notice/provenance items](../NOTICE_RESEARCH.md) require the separate release
process. This draft supplies no publication clearance or legal conclusion.

## 7. Conclusion — pending

The ten-block experiment is underway. Pre-start correctness and bounded
coverage are documented; supported performance advantages and sampled-scale
crossover conclusions await final validation and analysis.

Primary literature and official implementation references are collected in
[references.md](references.md).
