# CPU and GPU dataframe execution on one H100 host: a prospective blocked comparison of Polars and Spark

**Daniel Ejiofor**

**Technical draft — replacement campaign preparing, 2026-10-27.** The original
instance was lost before completion; its three surviving blocks are not pooled
with the fresh replacement. The
[prospective operational addendum](../../docs/H100_REPLACEMENT_20261027.md)
records the new host, regenerated files and off-host backup pauses. Ten-block
estimates, superiority findings, crossover
conclusions and publication metadata remain pending. This draft is
not ready for external publication.

## Abstract

We compare Polars CPU, cuDF-Polars GPU, Spark CPU, and GPU-accelerated Spark on
one H100 host, including compatible Polars 1.44.2 paths and a same-Python
Polars 2.0 CPU control. Six selected queries derived from TPC-H run at five
scales; six complex queries run at three scales. The prospective design
specifies ten paired blocks, 400 fresh sessions, independent SQL correctness
checks, and 26 primary comparisons with Holm adjustment. Primary action time
and secondary common-host-result time remain distinct. The earlier cohort's
pre-start validation compared 116 successful outputs with SQL references;
replacement readiness and oracles require their own archived evidence.
Separate diagnostics retain strict GPU coverage limitations. **Performance estimates and conclusions
await the completed ten-block campaign.** These selected research workloads
are not complete or certified TPC-H results.

## 1. Introduction

Dataframe users choosing between a CPU path and a GPU path face two connected
questions: whether the selected expressions execute with the intended
semantics, and whether a complete supported workload becomes faster at the
data sizes they use. A fast subset cannot answer the complete-workload
question when the remaining expressions are unsupported. Comparisons also
depend on the timing boundary: process startup, materialization and conversion
to a common host representation are distinct costs.

We study these questions for Polars CPU, strict cuDF-Polars GPU, Spark CPU and
Spark with the RAPIDS plugin on one H100 host. The design compares five fixed
software configurations, including two CPU Polars versions, on shared physical
inputs. We ask where the prespecified contrasts support a GPU advantage,
which unchanged expressions strict Polars GPU can translate, and which
operators appear in final Spark plans. These are configuration comparisons;
the same host does not isolate a causal effect of GPU hardware alone.

An exploratory H100 pilot motivated the comparisons and current CPU control.
Its within-process repeats do not establish process-level replication. The
prospective campaign retains the workload and collects fresh sessions in paired
blocks, addressing repetition levels, uncertainty and setup bias.
[Kalibera and Jones](https://kar.kent.ac.uk/33611/45/p63-kaliber.pdf),
[Mytkowicz et al.](https://research.ibm.com/publications/producing-wrong-data-without-doing-anything-obviously-wrong)

The intended contribution is an auditable empirical comparison with four
linked properties: fresh process-level replication in a balanced prospective
schedule; independent SQL checks of every successful action; explicit
expression coverage without imputed unsupported times; and separate primary
action and descriptive common-host-result endpoints. The archive binds each
claim to the exact source, inputs, schedule and raw artifacts that produced it.
No novelty claim about GPU database architecture is needed for this bounded
comparison to be useful.

The scope excludes distributed scaling, equal-cost performance, energy,
controlled cold-cache latency and optimal per-engine tuning. Historical
A100/H100 comparisons also change CPU allocation, software and file layout.

## 2. Related work and methodological position

Systems benchmark repetition occurs at several levels. Kalibera and Jones
distinguish variation between builds, executions and inner iterations, and
argue that replication should address the level contributing uncertainty.
Our inner actions summarize one fresh session; ten paired blocks supply the
observations used for inference. We do not treat thirty inner actions as
thirty independent sessions or claim that ten blocks guarantee a desired
precision. [Kalibera and Jones](https://kar.kent.ac.uk/33611/)

Experimental layout can change measured conclusions even when the setup
appears innocuous. Mytkowicz and colleagues demonstrate this problem and
motivate setup randomization. Blocking provides a complementary way to
organize comparisons around nuisance variation. Here, shared block/condition
identities, randomized condition order and balanced configuration orders
address allocation; they do not demonstrate constant host conditions or
independence. [Mytkowicz et al.](https://research.ibm.com/publications/producing-wrong-data-without-doing-anything-obviously-wrong),
[NIST](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm)

The study fixes one family of 26 primary comparisons before observing their
results and applies Holm adjustment. Query-level and endpoint sensitivity
analyses have a different, descriptive role. The t intervals on block log
ratios remain model dependent; resampling the same ten observations would not
create further independent evidence. Hesterberg's discussion of small-sample
bootstrap undercoverage is a reason to state these limits, rather than to
present bootstrap computation as a replacement for replication.
[Holm](https://www.jstor.org/stable/4615733),
[Hesterberg](https://arxiv.org/abs/1411.5279)

Implementation documentation supplies a different kind of evidence. Polars
GPU support, Spark AQE and the pinned RAPIDS compatibility notes explain why
strict guards, final plans and explicit semantic rules are required. They
cannot establish that a particular measured action used the intended path or
returned a valid result; the saved session artifacts provide those checks.
Likewise, TPC-H supplies workload provenance, while this selected-query,
fixed-parameter Float64 experiment has a narrower identity than the complete
standard benchmark. [Polars](https://docs.pola.rs/user-guide/gpu-support/),
[Spark 3.5.9](https://spark.apache.org/docs/3.5.9/sql-performance-tuning.html),
[RAPIDS 26.08.1](https://github.com/NVIDIA/cudf-spark/blob/v26.08.1/docs/compatibility.md),
[TPC-H 3.0.1](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC-H_v3.0.1.pdf)

## 3. Materials and implementations

### 3.1 Host and software

The replacement Linux KVM host exposes one H100 80 GB HBM3 GPU, 26 Xeon Platinum
8480+ vCPUs and 237,490,843,648 RAM bytes, approximately 221 GiB. Its driver is
580.105.08 and kernel is 6.8.0-1046-nvidia. Archived readiness inventory records
OpenJDK 17.0.20.1, CUDA driver API 13.0 and CUDA runtime 12.9. The CUDA 12
package closure is pinned; the driver's advertised compatibility level does
not change the runtime version used by these environments.
The lost host used driver 570.148.08 and a different kernel; its incomplete
campaign cannot establish the replacement host's performance.

| Configuration ID | Execution path | Principal version |
| --- | --- | --- |
| `polars_cpu_144` | Polars CPU streaming | Polars 1.44.2 |
| `polars_cpu_200` | Polars CPU streaming | Polars 2.0.0 |
| `polars_gpu` | cuDF-Polars streaming, CPU fallback rejected | Polars 1.44.2; cuDF-Polars 26.10.2 |
| `spark_cpu` | Spark CPU, `local[26]` | Spark 3.5.9 |
| `spark_gpu` | Spark with RAPIDS plugin, `local[26]` | Spark 3.5.9; plugin 26.08.1 |

The frozen replacement inventories verify the same CPython 3.11.13 base
executable/build (Clang 20.1.4) and matching common non-GPU package pins in
both environments. GPU dependencies differ because the selected
cuDF-Polars release requires older Polars. The historical CPU control used
Python 3.10.12 and remains separate.

Spark retains the `a100-parity` settings, including a 96 GiB driver heap,
120 shuffle partitions, adaptive query execution, and UTC session time zone.
GPU Spark uses two concurrent GPU tasks and a 2 GiB pinned-memory pool. The plan
archives these settings and the plugin JAR SHA256; the complete audit must still
check their effective values in every final session. Polars uses the
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

### 3.2 Data and workload definitions

DuckDB 1.5.6's `tpch` extension generated the inputs, exported as Snappy Parquet
under the archived recipe. Configurations share files and fixed manifests;
full SHA256 checks run before/after the campaign outside measurements. Nominal
scale factors are generator settings. Physical compressed bytes below describe
all tables, not scan volume or query working set.

The replacement inputs are freshly generated using the pinned recipe. Their
new manifest and file identities must be used throughout the replacement
oracles, plan and byte gates. No claim of physical-byte identity with the lost
instance is made. The archived replacement inventory covers 1,654 Parquet files.
Generation uses 30 export threads, a 64 GB memory setting, Snappy compression,
122,880-row groups, per-thread output and a 128 MB target file size. Generation
and export are outside competitor timings; timed configurations use 26 vCPUs.

| SF | Line-item rows | Parquet files | Stored bytes, all tables | Suites |
| ---: | ---: | ---: | ---: | --- |
| 1 | 6,001,215 | 57 | 325,716,220 | Baseline |
| 10 | 59,986,052 | 123 | 3,442,803,044 | Baseline |
| 30 | 179,998,372 | 185 | 10,568,904,874 | Baseline, complex |
| 100 | 600,037,902 | 371 | 35,650,816,651 | Baseline, complex |
| 300 | 1,799,989,091 | 918 | 107,973,376,582 | Baseline, complex |

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

## 4. Prospective experimental design

### 4.1 Campaign identity, allocation and replication

The frozen experimental source is code commit
`c84ef6a07341f350dae6636ab709c1b65fd894e0`, comprising a 52-file source snapshot.
Three cohorts have distinct evidential roles. The exploratory pilot supplies
motivation and historical observations. The October 26 prospective campaign
was created at 2026-10-26 18:11:05 UTC with campaign SHA256
`69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0`;
its host was lost before completion. Only three complete core checkpoints,
120 sessions and 2,808 successful output comparisons survive. It lacks final
input-byte verification and a complete raw telemetry archive, and contributes
no timings to the replacement sample.

The October 27 replacement uses the same frozen source, schedule design, seed,
queries, settings and inferential method. Its campaign ID is
`h100-confirmatory-replacement-20261027`, with campaign SHA256
`0dabe0d40c5adda0a087508e3f95ceb0636815f5133a3b401071612e3f24c145` and
pre-start manifest SHA256
`a758ceea7d16a6c726221db5db2cb99502ac4f640131341b456e9b63ed459b2a`.
All 358 files in the pre-start archive were preserved off-host and verified;
private Git commit `b94372deebcf3503c115fd9cc396dec3666ce85b` preceded the
first confirmatory action. This archive includes fresh runtime inventories,
all five input manifests, ten SF1 readiness sessions with 116 successful SQL
output comparisons, five semantic fixture runs, and all eight independent
full-scale oracle outputs. Readiness and oracle execution are outside the
confirmatory performance sample.

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

### 4.2 Timing boundaries and host state

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

Read-only status probes remain part of the instrumented virtual host. The
replacement operational supervisor pauses after each complete 40-session block
and snapshots all block raw files, including logs, telemetry, events, plans,
outputs and scientific gates. The next block requires an off-host receipt tied
to the exact campaign and snapshot hashes. Transfers occur while no engine
session is running. Backup pauses and receipt times are retained as an
operational pacing amendment; they add no sessions and change no expressions,
action boundaries or scheduled order. Full input-byte hashing, oracle execution
and tests on the benchmark host remain outside engine measurements.

### 4.3 Correctness and coverage

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
top-three keys are 3, 1, 2 instead of SQL's 1, 2, 4. The earlier cohort's full-scale
input-domain checks find no null price/date/order/customer keys in the generated
`orders` table at all five scales. The replacement repeated these SQL counts
against all five fresh inputs before confirmation and likewise found zero
nulls in those fields. Original-port claims are bounded
to the archived tested domain; the adversarial limitation remains disclosed.

### 4.4 Estimands, uncertainty, and multiplicity

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

## 5. Results — replacement campaign pending

The replacement results are unavailable at the fixed-sample analysis gate in
this draft. No partial-block observation enters the tables, abstract or
conclusion. The subsections below specify the complete results presentation;
their factual values must come from a successful replay of all 400 replacement
sessions. The historical evidence in Appendix A has a separate cohort identity.

### 5.1 Campaign disposition, integrity and deviations — pending

Report the replacement campaign/pre-start identities, collection interval,
all 400 planned session dispositions, attempts and deviations, final completion
marker and temporally bracketing input-byte checks. Account for every complete
40-session block snapshot and off-host receipt. State whether the final
validation, complete-evidence audit, portable replay and raw bundle each pass.
These are necessary evidence-disposition results; a planned session count or
successful tooling test cannot substitute for them.

### 5.2 Correctness and expression coverage — pending

Report all successful action/oracle comparisons, expected translation
rejections and any unexpected errors, retaining the warmup/measured distinction.
The unchanged supported-query pattern implies 9,360 successful comparisons:
2,340 warmups and 7,020 measured actions, together with 60 unsupported warmup
outcomes. These are expected counts, not observed replacement results in this
draft. Every rejection must be the intended expression-translation limitation,
verified from the retained diagnostic; no unrelated error may be counted as
expected coverage. Report replacement adversarial-fixture outcomes separately.

The coverage table must retain all 40 configuration/condition cells, including
the three complex Polars GPU cells with four of six supported expressions and
no complete six-query score. Any departure from that expected coverage requires
explicit review of the retained attempts, rather than a replacement query or
an imputed time.

### 5.3 Complete-suite scores and all primary contrasts — pending

Present descriptive complete-suite scores for every configuration/condition
cell and the entire 26-row primary comparison table. Each primary row gives
its contrast ID, configuration pair, suite, nominal scale, ten-block geometric
mean ratio, pointwise 95% interval, raw p value, Holm-adjusted p value and
supported-advantage outcome. Report estimated reference advantages and
comparisons without supported differences as well as supported GPU advantages.
Do not reduce the table to favorable rows or describe an unadjusted interval
as a familywise superiority decision.

Baseline and complex figures show the ten measured block ratios at each
sampled scale, with the prespecified estimate and interval. They use discrete
scale points and identify the unsupported complex Polars GPU comparisons.
Full primary values remain available in machine-readable form even when the
printed figure separates contrast families for readability.

### 5.4 Scale patterns and descriptive query results — pending

Evaluate sampled-scale brackets only within each declared contrast family and
suite. For every proposed bracket, inspect all larger measured points and
state the supported upper point and lower observed condition explicitly.
An inconclusive lower point cannot establish a CPU-favored region. Report
nonmonotone observations directly; do not fit an unregistered exact crossover.

The query heatmap and query-level table describe heterogeneity around the
suite scores, including unsupported original expressions. Individual query
ratios do not add primary significance claims. A selected physical operator
may be documented from a final plan, but cannot establish its runtime share
or explain a speed difference without separate profiling.

### 5.5 Common-host-result endpoint sensitivity — pending

Present all 32 descriptive configuration/condition pairs for the secondary
endpoint, retaining the six unavailable Polars GPU complex pairs. Place each
available action ratio beside the common-host-result ratio and its pointwise
interval. Enumerate every point-estimate direction change and relate it to
the declared conversion boundaries. Do not apply primary superiority language
or add hypothesis tests to this secondary table.

### 5.6 Block order, cache observations and host state — pending

Show or tabulate block log-ratio spread and chronological order alongside
preheat/post-session residency, host-memory pressure and recorded environmental
warnings. Keep snapshots and sampled peaks distinct from continuous guarantees.
Describe any conspicuous drift or uncertainty without silently trimming a
block, changing the fixed family or asserting that balanced order removes all
interference. Deviation and pacing disclosures belong beside these observations.

## 6. Discussion — interpretation prepared, findings pending

The primary practical interpretation will concern supported complete
workloads under the five fixed configurations. A useful GPU path must satisfy
the study's expression and correctness contract before its speed ratio is
interpreted. An unsupported complete suite remains unavailable even when its
supported constituent queries are fast. Conversely, lack of a supported
difference does not establish equivalence or rule out a useful effect that
this fixed allocation measures imprecisely.

The two CPU Polars controls serve different comparisons. Polars 1.44.2 aligns
the frontend version with the selected GPU integration; Polars 2.0.0 supplies
a newer same-Python CPU control. Differences among them include software
versions and dependencies, while Spark and Polars also use different memory
policies and materialization APIs. Ratios therefore describe deployable fixed
paths on this host, rather than an isolated GPU-hardware effect or an estimate
of the best tuning each engine could achieve.

Endpoint sensitivity is substantive: the primary boundary preserves the
pilot's action API, while the secondary boundary ends at the same Python
dictionary representation. Any changed direction will narrow the practical
claim to the relevant boundary. Neither endpoint includes process startup;
suite scores are sums of per-query medians and must not be presented as the
latency of a once-through application workload.

The lost instance and replacement also delimit the comparison. A driver/kernel
change and regenerated physical input files prevent treating surviving old
timings as interchangeable repetitions. The completed replacement will support
its own cohort-specific estimates. The earlier pilot and lost checkpoints
remain transparent historical records rather than extra independent blocks.

The final discussion must connect each observed result to these questions,
address conflicting scale/query/endpoint patterns, and distinguish statistical
support from effect magnitude and practical relevance. Claims about bandwidth,
data transfer, GPU kernels, spilling, cost or energy require evidence beyond
the planned action timings and saved physical plans.

## 7. Validity and interpretation limits

One virtual host, possible temporal dependence, VM interference and ten blocks
limit generalization and t-model precision. Williams balance does not eliminate
all carryover. Fixed settings, unequal memory policies and differing dependency
inventories constrain causal software comparisons. Selected queries, Float64
semantics and the preserved window-domain limitation constrain workload and
semantic generalization. Unsupported cases have no imputed speed. Mechanistic
explanations involving transfers, compute, spilling or bandwidth require
separate runtime evidence.

## 8. Reproducibility and release status

The [claim/evidence matrix](claim-evidence-matrix.md) links the private pre-start
archive and pending raw sessions, journal, integrity gates, deviations, plans,
telemetry, validation, tables and figure manifests.

`research_validate.py` gates `research_analysis.py`; the separate
`research_reporting/render.py` produces PNG/SVG/PDF figures from complete real
analysis. Synthetic test exports are excluded. Copied artifacts preserve
hashed JSON bytes and use path remapping. Final harvested-archive replay is
required.

The one-command replay retains the frozen validator's raw relocated output and
records a path-only adapter for Spark GPU plan-source identities before running
the frozen analyzer. It changes no original result or scientific gate. A
separate complete-evidence audit checks chronological pre/post byte gates,
all effective configurations, cache inventories, strict rejection diagnostics,
and exact action/cleanup totals. It also exports descriptive host-result
sensitivity tables. Raw evidence is packaged with per-file, per-part and joined
archive hashes after collection ends. These tools have passed synthetic
workflow tests; their final execution on the harvested study remains pending.

Development remains private. Availability, attribution and unresolved
[notice/provenance items](../NOTICE_RESEARCH.md) require the separate release
process. This draft supplies no publication clearance or legal conclusion.

The paper's source, build instructions and result-insertion ledger reside
under `publication/h100-confirmatory/`. The ledger specifies exact inputs and
required row counts for every final table, figure and summary claim. The
author is Daniel Ejiofor; no institutional affiliation is asserted. A final
data/code availability statement must describe the actual private archive
access and any separately authorized release, without implying public access.

## 9. Conclusion — pending

The replacement ten-block experiment is being prepared under the fixed
protocol and operational addendum. This draft documents the complete
experimental and reporting method. Its supported performance advantages,
endpoint-sensitive interpretation and sampled-scale conclusions await the
completed replacement archive, final audit and replay.

## Appendix A. Historical evidence excluded from replacement inference

### A.1 Earlier pre-start correctness evidence

The lost October 26 campaign's archived readiness passes with 116 successful
comparisons across ten SF1 smoke sessions. All configurations pass core
inner/outer/semi-anti cases. CPU configurations and GPU Spark pass all five
diagnostics; Polars GPU passes three and rejects rank/window and DOTALL-regex
cases. The tiny Spark GPU final plan contains `LeftAnti`. This establishes
small-input semantics/selection, not anti-join performance or a nonempty anti
branch in the original correlated workload. These facts cannot establish
replacement readiness until repeated and archived for that cohort.

Eight complete independent oracles are archived for the earlier measured
conditions. Its separate post-hoc pilot supplement compares all 936 saved
successful outputs in 40 historical sessions to those SQL references, with
zero mismatches under the stated tolerance rule. This strengthens the pilot's
correctness record; it does not retroactively preregister its timings or add
independent timing repetitions to the replacement.

### A.2 Exploratory pilot performance context

At SF300, the pilot's baseline scores were 12.774 s for Polars GPU, 31.540 s
for its historical Polars 2.0/Python 3.10 CPU control, and 45.528 s for GPU
Spark. Its complex scores were 70.389 s for that CPU control and 143.039 s for
GPU Spark; Polars GPU had no complete complex score. These are sums of six
per-query medians from one session per configuration/condition. They are
descriptive historical observations without process-level confidence intervals.
Historical Python 3.10 timings do not populate the replacement same-Python
CPU control or its prospective results.

Primary literature and official implementation references are collected in
[references.md](references.md).
