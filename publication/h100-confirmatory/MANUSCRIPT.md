# CPU and GPU dataframe execution on one H100 host: a prospective blocked comparison of Polars and Spark

**Daniel Ejiofor**

**Technical draft — isolated recovery incomplete; separate contention audit
complete, 2026-10-28 UTC.** Four
cohorts have distinct roles: an exploratory pilot, the incomplete campaign on
a lost instance, an aborted default-profile replacement, and one fresh
recovery campaign with a fixed JVM profile. Their timings are never pooled.
A separately requested, descriptive same-host contention experiment completed
its planned waves. Independent terminal replay verifies its outputs, actual
action overlap, separation from the drained isolated attempt and the same
scheduler's resume. Its descriptive observations neither replace nor augment
the 400-session primary sample.
The [replacement addendum](../../docs/H100_REPLACEMENT_20261027.md) and
[prospective recovery protocol](../../docs/H100_GCLOCKER100_RECOVERY_PROTOCOL.md)
record the host, input reuse, incident, feasibility gate and profile choice.
The separate recovery feasibility diagnostic and repeated readiness have
passed, and the fresh campaign has started. Ten-block estimates, superiority
findings and sampled-scale conclusions remain pending. The separate contention
results below do not establish a slowdown relative to isolated execution.
This draft is not ready for external publication.

## Abstract

We compare Polars CPU, cuDF-Polars GPU, Spark CPU, and GPU-accelerated Spark on
one H100 host, including compatible Polars 1.44.2 paths and a same-Python
Polars 2.0 CPU control. Six selected queries derived from TPC-H run at five
scales; six complex queries run at three scales. A default-profile replacement
halted after 88 scientifically validated sessions when Spark CPU reported a
Java heap-space error during a first warmup. Its timings are excluded from the
fresh recovery sample. A separate feasibility diagnostic returned twelve
oracle-valid outputs, and repeated SF1 readiness compared 116 successful
outputs before one fresh 400-session recovery cohort began with a fixed Spark
JVM allocation-retry profile. The design retains ten paired
blocks, independent SQL correctness checks, separate action/common-host-result
endpoints, and 26 primary comparisons with Holm adjustment. Strict unsupported
expressions receive no imputed times. The profile is a post-incident
configuration choice, with no causal mitigation or reliability claim.
A separate staged experiment coordinates one Spark process and one Polars
process on this same H100. Its twenty completed waves yield 460 oracle-valid
outputs and 110 common measured action pairs with verified overlap. A terminal
audit also verifies separation from the drained isolated attempt and the same
scheduler's resume. These descriptive observations are excluded from the
primary family and isolated sample and do not estimate a causal contention
slowdown.
**Recovery performance estimates and conclusions await its completed ten-block
campaign.** These
selected research workloads are not complete or certified
TPC-H results.

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
Primary recovery conclusions will apply to the explicitly recorded JVM profile;
the aborted cohorts do not estimate its causal performance or reliability effect.
The user-requested contention extension asks a different question about two
coordinated workloads sharing the same host. It has separate sampling,
instrumentation and descriptive reporting, and supplies no extra primary blocks.

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

The recovery profile has a limited technical rationale. The OpenJDK allocation-
starvation issue discusses GCLocker retries and possible longer stalls; it
does not diagnose this study's failure. JEP 423 changes G1 JNI critical-region
handling in JDK 22, while this experiment retains Java 17.0.20.1. The Spark
proposal that used retry count 100 was closed unmerged, so it is not official
Spark 3.5.9 tuning guidance. These sources motivate a separately gated
feasibility diagnostic, not a claimed cure or measured causal benefit.
[OpenJDK JDK-8192647](https://bugs.openjdk.org/browse/JDK-8192647),
[JEP 423](https://openjdk.org/jeps/423),
[Spark PR 51920](https://github.com/apache/spark/pull/51920)

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

The recovery applies the following sealed driver startup profile to both
Spark configurations at every suite/scale:

```text
SPARK_SUBMIT_OPTS=-XX:+UnlockDiagnosticVMOptions -XX:GCLockerRetryAllocationCount=100 -XX:+PrintFlagsFinal
```

Java 17.0.20.1, G1, the 96 GiB maximum heap and all listed query settings remain
fixed. `PrintFlagsFinal` records the effective driver options before query
timing. Competing Java-option variables must be absent; the profile must not
be injected through `JAVA_TOOL_OPTIONS`, which also affects Spark's command
builder. `EXECUTION_PROFILE.json` binds the exact startup environment,
Java executable/runtime files, launcher sources, Python environments and
diagnostic/input/oracle evidence before recovery timing. Its exact SHA256 and
pre-start identity are recorded in §4.1. The four repeated Spark readiness
driver logs verify the profile; proof for all formal sessions remains a final
audit requirement. Allocation-retry and GC stalls within an action remain inside
its normal endpoints; no such observation is excluded as an outlier.

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

The default-profile replacement inputs were freshly generated using the pinned
recipe. Their manifest and physical file identities remain the inputs for the
recovery, which reuses the same 1,654 files and existing independent
oracle bytes on the same VM. No recovery input regeneration or mutation is
allowed. No claim of physical-byte identity with the lost instance is made.
The archived replacement inventory is therefore also the recovery
inventory below; its actual pre/post byte gates must still verify those bytes.
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
Four cohorts have distinct evidential roles. The exploratory pilot supplies
motivation and historical observations. The October 26 prospective campaign
was created at 2026-10-26 18:11:05 UTC with campaign SHA256
`69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0`;
its host was lost before completion. Only three complete core checkpoints,
120 sessions and 2,808 successful output comparisons survive. It lacks final
input-byte verification and a complete raw telemetry archive, and contributes
no timings to either later primary sample.

The October 27 default-profile replacement used the same frozen source, design, seed,
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
confirmatory performance sample. That cohort subsequently halted at 20:27:48 UTC
with 88 scientifically validated sessions, 2,056 successful output comparisons
and one failed session. Its remaining 311 sessions had not started. The complete
abort and evidence disposition are reported in §5.1. No default-profile timing
is carried into recovery inference, and its journal/attempts remain unchanged.

The fourth cohort, `h100-confirmatory-gclocker100-20261027`, began collection
on October 27 at 21:47:44 UTC. Its canonical archive is
`evidence-research/20261027/h100-confirmatory-gclocker100-v1`. The registered
campaign SHA256 is
`3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641`;
the pre-start manifest SHA256 is
`ea57fa1221b905e8ee287fe7860285c7a37535b084f13ce3dbbbca5ac4e29ace`;
the sealed execution-profile SHA256 is
`a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69`.
The canonical pre-start snapshot contains 346 files, including its manifest;
all 345 listed file checksums match. Private commit
`6dbca2fbd4de1be8aa4408a639954bc9076bfa38` preceded the first session. The
recorded full 1,654-file initial byte gate passed at 21:47:43.892782 UTC.
The final post-session byte gate remains required.

Exactly one separately named full-complex-SF100 Spark CPU feasibility session
completed before recovery preparation. It used seed 838471203, one warmup and
one measured action per query. Its validation completed at
21:36:41.579958 UTC with all twelve oracle-valid records, twelve checked
cleanup records, successful process exit and the five effective driver flags.
The archive preserves `diagnostic-sf100-driver_log.log`,
`diagnostic-sf100-results.json` and `diagnostic-sf100-validation.json`, with
hashes bound by the sealed profile. Its timings are excluded from primary
inference. This passed feasibility test does not establish mitigation efficacy
or guaranteed robustness.

All five configurations then repeated SF1 readiness. The readiness report
completed at 21:44:41.187289 UTC with ten smoke sessions, 116 successful SQL
output comparisons, five semantic fixture configurations and no errors. All
four Spark readiness driver logs show retry count 100, G1, unlocked diagnostics,
`PrintFlagsFinal` and maximum heap 103,079,215,104 bytes. Passing before/after
readiness profile receipts bind the same sealed profile. These pre-start
observations are excluded from the confirmatory performance sample.

The recovery retains the same VM, 1,654 physical files, existing oracle bytes,
five configurations and unchanged 52-file C84 source. The fixed Spark JVM
profile is an explicitly disclosed post-incident change. Recovery collection
is ongoing; no completed-sample performance result is asserted in this draft.

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

The recovery requires all ten fresh blocks and all 400 fresh sessions. Ten
blocks is a fixed allocation, not a power guarantee. No earlier timing enters
that allocation, and there is no significance-based stopping, trimming,
selective repetition or tuning.
Unexpected errors halt execution; retained attempts and partial outputs support
explicit deviation assessment. A recovery failure does not authorize automatic
parameter escalation, restart or another cohort until a clean result appears.

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
The recovery additionally requires 24 idle-boundary execution-profile receipts:
before/after repeated readiness, before/after each of ten blocks, and before/
after final verification. The final complete audit must bind those receipts
to the sealed profile, unchanged Java/Python environments, session bounds and
campaign completion. These new receipts are configuration checks, not extra
timing observations.

### 4.3 Correctness and coverage

Before default-profile replacement timing, DuckDB 1.5.6 executed the original
SQL definitions for all eight measured conditions using the same Float64
normalization. The recovery reuses those frozen complete oracle bytes. Each oracle
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
`orders` table at all five scales. The default-profile replacement repeated these SQL counts
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

### 4.5 Separate coordinated same-host contention experiment

The owner subsequently requested concurrent sessions on the existing H100.
The separate [contention protocol](../../docs/H100_CONTENTION_PROTOCOL.md)
defines `h100-contention-20261028`, with canonical pre-start archive
`evidence-research/20261028/h100-contention-v1`. Its plan SHA256 is
`670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031` and
pre-start manifest SHA256 is
`4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25`.
All 65 listed file checksums match, for 66 canonical files including the
manifest. Private pre-start commit
`98c7ff4d6a9bbe82a021671c28fed28f71827b70` preceded execution. The plan binds
the recovery campaign/profile identities above, the same 52-file C84 source,
SF30/SF100 manifests, four existing independent oracles and separately versioned
external instrumentation. No contention observation enters the isolated
400-session sample, its 26-test family or the recovery feasibility diagnostic.

| Pair | Concurrent participants | GPU path |
| --- | --- | --- |
| A | Spark GPU and Polars CPU 2.0.0 | Spark GPU |
| B | Spark CPU and strict Polars GPU 1.44.2 | Polars GPU |

Each pair has one Spark JVM with its original 96 GiB heap and one GPU execution
path. Each participant requests its full original 26 CPU threads on the same
26-vCPU host; CPUs are not partitioned into disjoint allocations or cgroups.
Both Spark paths retain the sealed retry-100 JVM profile and fixed SQL settings.
Host memory, CPU, cache, disk and GPU-related host work are deliberately shared.
The design excludes two concurrent Spark heaps and simultaneous Spark GPU/
Polars GPU default pools. These observations cannot describe equal-resource
competition or standalone isolated execution.

The staged pilot contains both pairs × baseline/complex at SF30: four waves
and eight fresh processes, with one warmup and one measured action per query.
Seed 20261028 fixes its wave order. Only if all four pilot waves pass output,
cleanup, configuration and actual measured-overlap gates may the planned
expansion run: both pairs × both suites × SF30/SF100 × two repetitions,
sixteen waves and thirty-two fresh processes, with order seed 20261029.
Pilot observations are not pooled into the expansion. Expected successful
output comparisons are 92 for the pilot and 368 for the expansion, with two
and eight expected strict unsupported warmups respectively. These are the
prespecified coverage counts; actual audit accounting is reported in §5.7.
Any failed wave stops its phase
and preserves both participants and partial evidence. The design permits no
hidden retry, adaptive sample increase or new scale; a failure requires a new
explicit scientific decision.

The separately hashed `contention_worker.py` imports the byte-identical frozen
worker. External hooks add an engine-ready barrier and a rendezvous before
each common query/phase/trial. The action rendezvous follows the first existing
process-counter sample and precedes the original timer start; both participants
use the same future monotonic release timestamp. Query expressions,
materialization/conversion code and timer statements remain unchanged.
This is coordinated same-query contention, rather than independent arrival
traffic. Each wave shares one full selected-scale preheat before either process
starts, with residency observations before/after preheat and after both workers.
Selected input-byte gates bracket each phase; these observations do not
guarantee in-query residency.

In pair B complex, strict Polars GPU rejects `cx_q13` and `cx_window` during
warmup. Both warmup attempts rendezvous, but their measured CPU counterparts
explicitly bypass the barrier because no measured GPU counterpart exists.
Those two CPU measurements are unpaired diagnostics. A six-query CPU sum would
mix paired and unpaired conditions and must not be presented as uniformly
contended execution; a complete six-query Polars GPU score remains unavailable.
Only the four common measured query pairs establish query-level contention for
this condition. Other waves require six common measured query pairs.

Saved action UTC and same-host monotonic intervals, rather than overlapping
process lifetimes, must establish actual action overlap. Report both action
durations, start skew and interval intersection for every common measured query;
the pilot gate requires positive intersection on both clocks. The action and
common-host-result endpoints retain their frozen boundaries. Wave/process wall
time includes initialization and rendezvous waits, with shared preheat reported
separately. Process-counter deltas and sampled RSS/GPU-memory observations can
include untimed rendezvous waits and are not isolated action-only resource costs.
Separate wave telemetry samples host/process trees every 0.5 s and the GPU
board every 200 ms; the worker samplers retain their separately recorded
intervals. Transient peaks may be missed, and board utilization/power cannot be
attributed causally to a single query or process.

No contention p values, superiority decisions, additions to the primary family
or exact crossover thresholds are declared. Two expansion repetitions support
descriptive reporting only. Any later comparison with the completed isolated
study must disclose the different replication, barrier and preheat context and
keep both designs' observations separate.

### 4.6 Managed interlude in isolated collection

The [interlude record](../../docs/H100_CONTENTION_INTERLUDE_20261028.md)
discloses a requested operational amendment after isolated collection began.
The manager held only the exact isolated scheduler, PID 179144, at
2026-10-28 04:44:23 UTC, preserving its process, locks and journal. The already
launched isolated SF30 Spark CPU runner and its workers continued unpaused.
`ORIGINAL_RUNNER_DRAINED.json` records runner exit zero and schedule/result
hashes at 04:45:43.946178 UTC, before the pilot start at 04:47:43 UTC.
The frozen original action/host-result timer code is unchanged. The terminal
offline audit now binds all 34 original
`block-04-complex-sf30-spark_cpu/attempt-001` files from the protected off-host
block04 backup. Its original result and schedule hashes match the drain
receipt. The last original host-result endpoint is 04:45:38.505969 UTC;
the first contention action starts at 04:47:59.693693 UTC, 141.188 s later.
Saved action endpoints therefore establish temporal separation independently
of the operational drain receipt.

Because the scheduler is held while its existing child drains, the journal
may continue to mark the attempt as running after its worker has finished.
The scheduler's `ended_at_utc` and `orchestrator_wall_seconds` include the hold.
The retained attempt reports an outer wall of 3,043.015 s and an end timestamp
of 05:32:20.060856 UTC, while its Spark worker process wall is 240.581 s,
ending at 04:45:41.907405 UTC. The outer envelope includes the scheduler hold
and must not be interpreted as worker or query latency. These original values
are preserved. The hold does not create a replacement attempt or cancel the
remaining 400-session allocation.

The saved handoff records the same scheduler PID 179144, creation time
1793160829.75, resumed at 05:32:20.044724 UTC. `STATE.json` is
`COMPLETE_RESUMED`; the phase driver is `COMPLETE_OFFHOST`, both phase raw
backups are verified off-host, and the owned-group cleanup receipt lists no
live members. `PROFILE_BEFORE_RESUME.json` records PASS at 05:32:19.993450 UTC.
These interlude checks are separate receipts and do not replace or augment the
formal 24-record profile log. The original unchanged schedule continues.
The last contention host-result endpoint is 05:28:37.930202 UTC, before this
resume. The terminal handoff audit reports PASS, verifies the original payload
and action separation, and records no pending reasons. Temporal separation
does not remove the interlude's change to pacing or demonstrate unchanged
host/cache conditions; possible carryover remains a limitation.

## 5. Results — recovery primary results pending

The default-profile abort and passed recovery feasibility/readiness are observed
outcomes; recovery primary results remain pending. No partial-block timing from
either incomplete cohort enters the primary estimates, tables or conclusion.
The completed primary presentation must come from a genuine replay of all 400
fresh recovery sessions under its sealed profile. Historical evidence has
separate cohort identities.

### 5.1 Observed default-profile abort and ongoing recovery disposition

The default-profile replacement halted at 20:27:48 UTC on October 27. Its failed
session, `block-03-complex-sf100-spark_cpu`, failed in `cx_q09`'s first warmup,
before any successful output or measured observation in that session. The JVM
exited 52 and reported `java.lang.OutOfMemoryError: Java heap space`, preceded
by GCLocker allocation-retry warnings. The event-log BlockManager maximum
on-heap value, 61,658,785,382 bytes, corroborates the configured 96 GiB heap cap.
The nearest host sample recorded 211,723,608,064 available RAM bytes, zero
swap use and 2,713,801,965,568 free disk bytes.

These observations do not reveal failure-time heap occupancy or RSS and do
not distinguish genuine heap exhaustion from premature allocation failure.
No failure-time heap/RSS or GC-recovery evidence was retained, so a root cause
and a causal effect of increasing the retry count remain undetermined. Host
available memory alone cannot establish whether this JVM could satisfy its
particular allocation.

The aborted cohort retains 88 scientifically validated sessions and 2,056
successful output comparisons, one failed session and 311 unstarted sessions.
Its complete 3,349-file incident evidence was preserved off-host as a
220,656,964-byte gzip archive with SHA256
`79c99819d272cc1af947d8e200976a940cd63a96be580112e4a6c5900adee6ff`.
Both complete blocks also retain separate verified off-host raw backups.
The original journal and attempts were not edited; no successful session was
rerun within that cohort, and its failed attempt remains visible. This is a
reported failure observation, not a complete primary comparison.

The passing pre-start diagnostic/readiness evidence and exact recovery
identities are reported in §4.1. For the ongoing collection, the final report
must still establish its complete interval, all 400 session dispositions,
attempts, deviations and final completion. Account for all ten
40-session raw backups, 24 idle profile receipts and temporally bracketing input-
byte gates. Independently check the four repeated SF1 Spark readiness driver
logs. Require all 160 formal Spark driver logs to prove retry count 100,
unlocked diagnostic options, G1 and maximum heap 103,079,215,104 bytes. Final
validation, complete audit, replay and raw bundle remain pending; planned counts
or successful tooling tests cannot establish their completion.

### 5.2 Correctness and expression coverage — pending

Report all successful action/oracle comparisons, expected translation
rejections and any unexpected errors, retaining the warmup/measured distinction.
The unchanged supported-query pattern implies 9,360 successful comparisons:
2,340 warmups and 7,020 measured actions, together with 60 unsupported warmup
outcomes. These are expected counts, not observed recovery results in this
draft. Every rejection must be the intended expression-translation limitation,
verified from the retained diagnostic; no unrelated error may be counted as
expected coverage. Pre-start recovery readiness already passes all five semantic
fixture configurations: the three CPU configurations and GPU Spark pass all
five cases, while strict Polars GPU passes three and retains the rank/window
and DOTALL-regex rejections. These are diagnostic coverage outcomes, separate
from the earlier default-profile preparation and from full-campaign correctness.

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

### 5.7 Separate descriptive contention results

Both planned contention phases completed their workload and phase validations:

| Separate phase | Passing/planned waves | Successful outputs, warmup/measured | Common measured pairs | Unpaired CPU measured diagnostics |
| --- | ---: | ---: | ---: | ---: |
| SF30 pilot | 4/4 | 46/46 | 22 | 2 |
| SF30/SF100 expansion | 16/16 | 184/184 | 88 | 8 |

The independent terminal offline audit at 07:15:58.632271 UTC verifies all 460 SQL output
comparisons: 230 warmups and 230 measured outputs, ten expected strict
unsupported warmups, zero oracle failures and zero unexpected query errors.
It also verifies 110 common measured action pairs with positive within-pair
intersection on both clocks, 250 barrier releases and ten unpaired CPU measured
diagnostics. These aggregate counts account for preserved artifacts; they do
not pool pilot/expansion timing estimates or add primary observations. Pair B
complex still has four common measured pairs and two unpaired CPU diagnostics
per affected wave, with no uniformly contended six-query CPU sum.

Pilot and expansion raw archives are verified off-host, and the phase driver
reports `COMPLETE_OFFHOST`. The expansion archive contains 1,561 payload files
in 487,321,600 tar bytes, SHA256
`98be99cf74690d01053de9670669e629a166b02bd67fd51a994425006f792197`.
The final handoff independently binds the actual original 34-file attempt and
same-process scheduler resume described in §4.6. The terminal receipt has
`status: COMPLETE`, `integrity_status: PASS`, `publication_ready: true`, no
findings and no pending handoff reasons. This completes the separate extension's
provenance gate; the original 400-session study remains incomplete.

The terminal `CONTENTION_REPLAY.json` SHA256 is
`0cad03b7ab2edc7e0d62dbcc7b84f8fa0ea51325aba41eb354b5baae0c51a2d3`.
The earlier 06:17 v3 receipt remains an unchanged historical PARTIAL audit:
its then-missing original payload is now supplied to this new terminal audit.
Its former `no_action_overlap: false` denoted pending proof, not an observed
violation. The terminal handoff verifies `original_payload_verified: true`
and `no_action_overlap: true`; the latter concerns separation from isolated
work, distinct from intended overlap within each contention pair.

Each common measured pair contains one action from each concurrent participant.
The following expansion counts describe which recorded action was shorter;
they are not independent replications, hypothesis tests or suite scores.
Each condition has only two fresh concurrent waves. No pilot timing is pooled
into this table.

| Pair | Suite | SF | Common measured pairs | Shorter Polars action | Shorter Spark action |
| --- | --- | ---: | ---: | ---: | ---: |
| A: Spark GPU + Polars CPU 2.0 | Baseline | 30 | 12 | 12 | 0 |
| A: Spark GPU + Polars CPU 2.0 | Baseline | 100 | 12 | 12 | 0 |
| A: Spark GPU + Polars CPU 2.0 | Complex | 30 | 12 | 11 | 1 |
| A: Spark GPU + Polars CPU 2.0 | Complex | 100 | 12 | 10 | 2 |
| B: Spark CPU + Polars GPU | Baseline | 30 | 12 | 12 | 0 |
| B: Spark CPU + Polars GPU | Baseline | 100 | 12 | 12 | 0 |
| B: Spark CPU + Polars GPU | Complex | 30 | 8 | 8 | 0 |
| B: Spark CPU + Polars GPU | Complex | 100 | 8 | 8 | 0 |

The pair A complex exceptions are the window query: Spark GPU has the shorter
action in one SF30 repetition and both SF100 repetitions. For example, its
SF100 window actions are 3.176 and 3.243 s, alongside Polars CPU actions of
4.909 and 4.913 s. In the same condition, the Q09 actions are 24.869 and
23.979 s for Spark GPU versus 5.999 and 5.706 s for Polars CPU. The observations
therefore vary by query even within one paired condition. In pair B, Polars
GPU's shorter recorded actions apply only to supported common expressions;
its two complex rejections remain unavailable, with no six-query GPU score.
The two pairings have different competitors and cannot rank the GPU engines.

`QUERY_ACTIONS_DESCRIPTIVE.csv` retains every warmup/measured action, both
endpoints and pairing flags; `COMMON_QUERY_OVERLAP.csv` retains all 110 common
measured intersections. Pilot intersections range from 0.208 to 2.361 s with
a maximum start skew of 0.647 ms. Expansion intersections range from 0.228 to
8.664 s with a maximum start skew of 2.340 ms. These are observed ranges across
different queries, not uncertainty intervals or estimates of synchronization
quality beyond these waves.

The six final figures preserve individual actions and actual timelines,
separating pilot from expansion. Their manifest SHA256 is
`5751b86ee46989fdfc7b0d25b1e2eac3a475a09c872143026cedfbd1e06000bd`.
Action plots omit unpaired CPU diagnostics; timelines retain them with explicit
hatching and use actual UTC intervals and gaps without widening fast actions.
No action-time ratio here estimates a contention penalty relative to isolated
execution. Such a claim needs a matched isolated analysis with the different
replication, preheat, barriers and host history disclosed. Resource counters
and sampled memory can include untimed waits and are not action-only costs.
No new confidence interval, p value, superiority decision or primary result
is inferred from this extension.

## 6. Discussion — primary interpretation pending

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

The four cohorts delimit the comparison. A driver/kernel change and regenerated
files separate the lost host from the replacement VM. The default-profile
replacement then halted, motivating the fixed recovery JVM profile on the same
VM and input bytes. This post-incident choice is not an unchanged continuation
and cannot support a causal before/after estimate of speed or reliability.
Primary estimates, if recovery completes, apply to that recorded profile.
The pilot, lost checkpoints, aborted replacement and separate feasibility
diagnostic contribute no extra independent primary blocks. The passed
feasibility diagnostic does not prove that the failure was a false OOM, that the
profile cured its cause, or that future runs are robust. Any allocation-retry
overhead during a measured action remains part of the reported action.

The contention extension has a distinct interpretation. Both processes request
the original full CPU budget while sharing one host, so observed action times
describe that coordinated competing workload. Barrier synchronization does not
make resources equal or reproduce arbitrary user arrivals. Pair B complex
further separates common contended queries from two unpaired CPU diagnostics.
Any isolated/contention comparison will be descriptive and will preserve the
different replication and cache/barrier conditions. The scheduler hold affects
the isolated outer orchestration envelope and temporal history, even though
its already launched worker and query timers were not paused. Terminal replay
verifies action separation, not unchanged host/cache state. The recorded
within-pair ordering in §5.7 is descriptive; a causal slowdown or resource-cost
claim would require an appropriate matched isolated comparison.

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
The recovery profile was chosen after an observed failure, limiting inference
to that post-incident configuration. A fresh complete sample avoids pooling
incompatible timings; it does not erase the configuration-selection history
or establish a causal mitigation effect. The fixed single-recovery decision
must remain visible, including any later collection failure.
The same-host contention extension is small and synchronized, with only two
planned expansion repetitions. Its process-counter and memory observations may
span untimed waits; GPU board samples are shared and can miss peaks. The
requested interlude changes isolated temporal pacing and may influence later
host/cache state. Recorded idle/identity checks and continued preheat policy
support interpretation without proving absence of carryover.

## 8. Reproducibility and release status

The [claim/evidence matrix](claim-evidence-matrix.md) links the private pre-start
archive and pending raw sessions, journal, integrity gates, deviations, plans,
telemetry, validation, tables and figure manifests.
It also separates the lost and aborted evidence from the recovery and binds
`EXECUTION_PROFILE.json`, its actual SHA256, diagnostic/repeated-readiness
proof, all 24 profile receipts and all 160 effective Spark driver logs. The
final complete audit and paper build must identify the same recovery profile;
neither earlier campaign identity can satisfy this primary-results gate.
The separate contention plan/pre-start/instrumentation hashes, phase and wave
validations, participant outputs, barrier/overlap events, telemetry and managed
handoff receipts must also be preserved. They retain separate schemas and
cannot satisfy the isolated campaign's final journal, 24 profile receipts or
400-session analysis. The immutable contention pre-start status
`FROZEN_NOT_RUN` describes its creation point, not live execution or completion.

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

One fresh ten-block recovery experiment is collecting after a passed separate
feasibility diagnostic and repeated readiness. Its post-incident
fixed JVM profile, reused input/oracle bytes and unpooled cohort history are
explicit. Supported performance advantages, endpoint-sensitive interpretation
and sampled-scale conclusions await all 400 recovery sessions, final integrity/
profile audit and genuine replay. The pre-start feasibility/readiness milestone
does not establish a causal mitigation finding or a primary performance result.

The separately requested contention workload completed its twenty waves, with
460 oracle-valid outputs, 110 common measured pairs and verified terminal
action separation and same-scheduler resume. Its per-query observations apply
to the two recorded competing pairs; they establish no causal slowdown relative
to isolated execution or ranking between GPU engines. These observations do
not replace the original 400 sessions, supply primary superiority conclusions
or complete the paper/publication/preservation requirements by themselves.

## Appendix A. Historical evidence excluded from recovery inference

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
