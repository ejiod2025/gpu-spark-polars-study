# Prospective JVM-profile recovery cohort, 2026-10-27

## Incident and retained observations

The default-profile replacement campaign
`h100-confirmatory-replacement-20261027` (SHA256
`0dabe0d40c5adda0a087508e3f95ceb0636815f5133a3b401071612e3f24c145`)
halted on October 27 at 20:27:48 UTC. It had 88 scientifically validated
sessions, 2,056 successful output comparisons and one failed session;
311 sessions had not started. The failed session was
`block-03-complex-sf100-spark_cpu`, during the first warmup `cx_q09`, before
any successful output or measured observation in that session.

The JVM exited 52 and reported `java.lang.OutOfMemoryError: Java heap space`.
Its preceding warnings identified the GCLocker allocation-retry limit. The
event log corroborates the 96 GiB heap cap through BlockManager maximum on-heap
memory of 61,658,785,382 bytes. The nearest host sample recorded
211,723,608,064 available RAM bytes, no swap use and 2,713,801,965,568 free disk
bytes. These facts do not establish failure-time heap occupancy, distinguish
premature allocation failure from genuine heap exhaustion, or prove a root cause.
Failure-time JVM heap/RSS and GC-recovery data are unavailable.

The complete aborted evidence is preserved without modifying the original
journal or attempts. Its incident archive has 3,349 original files; no successful
session will be rerun within that campaign and no failed attempt will disappear.
The two complete blocks also retain their separate verified off-host raw backups.
This cohort supplies an explicitly reported failure observation, not primary
recovery timing estimates.

## Fixed diagnostic and execution profile

OpenJDK documents GCLocker allocation starvation and discusses increasing its
retry count as a possible workaround, with a risk of longer stalls. That is a
plausible mechanism, not a diagnosis of this incident. JEP 423 addresses G1 JNI
critical regions in JDK 22; this study does not upgrade its JDK. The Spark PR
that proposed retry count 100 was closed unmerged and is not official Spark
3.5.9 tuning guidance.

- [OpenJDK JDK-8192647](https://bugs.openjdk.org/browse/JDK-8192647)
- [OpenJDK JEP 423](https://openjdk.org/jeps/423)
- [Spark PR 51920](https://github.com/apache/spark/pull/51920)

Before any recovery observations, run exactly one separately named diagnostic
session for the entire original complex SF100 Spark CPU suite, using query seed
838471203, one warmup and one measured action per query. Its timings are
diagnostic and excluded from primary inference. Preserve every outcome.
Require all 12 returned outputs to match the existing independent SQL oracle,
normal cleanup checks, successful process exit, and effective JVM evidence.
Do not escalate the proposed retry count or repeat the diagnostic until it passes.
Failure requires preserved diagnosis and a new explicit scientific decision.

The proposed fixed driver startup profile is:

```text
SPARK_SUBMIT_OPTS=-XX:+UnlockDiagnosticVMOptions -XX:GCLockerRetryAllocationCount=100 -XX:+PrintFlagsFinal
```

Use this profile for both Spark CPU and Spark GPU at every suite/scale.
`PrintFlagsFinal` records the actual driver values before application/query
timing. It must not be supplied through `JAVA_TOOL_OPTIONS`, because that would
also alter Spark's command-builder JVM stdout protocol. The Spark 3.5.9 launcher
uses `SPARK_LAUNCHER_OPTS` for the command builder and `SPARK_SUBMIT_OPTS` for
the final driver. Competing `JAVA_TOOL_OPTIONS`, `_JAVA_OPTIONS`,
`JDK_JAVA_OPTIONS` and `SPARK_LAUNCHER_OPTS` must be absent.

Keep Java 17.0.20.1, 96 GiB driver heap, G1, 26 CPUs, 120 shuffle partitions,
AQE, UTC, original expressions, strict GPU guards, tolerances, endpoints and
cleanup settings unchanged. Hash the Java executable, `libjvm.so`, module image,
release metadata, actual Spark launcher files and environment sources.
Freeze the complete actual profile, diagnostic evidence and input/oracle
identities in `EXECUTION_PROFILE.json` before the recovery campaign begins.
Check that profile at idle block boundaries. Independently verify all 160
actual Spark session logs for retry count 100, unlocked diagnostic options,
G1 and maximum heap 103,079,215,104 bytes.

Allocation-retry and GC stalls remain inside normal measured endpoints when
they occur during an action. Do not remove such observations as outliers.
A passing diagnostic establishes feasibility, not a causal mitigation effect
or a guarantee of robustness.

## One fresh fixed sample

If the diagnostic and repeated five-configuration SF1 readiness pass, collect
one new campaign, `h100-confirmatory-gclocker100-20261027`. Use the same H100,
same 1,654 physical Parquet files and independent oracle bytes, matching Python
environments, and unchanged C84 experimental source:
`c84ef6a07341f350dae6636ab709c1b65fd894e0` (52 files).
Do not regenerate or mutate the inputs.

Freeze and privately archive the genuine new plan, exact profile, source,
readiness and prestart checksum before timing. Preserve the fixed seed
20261026, Williams allocation, all eight conditions, five configurations,
ten fresh paired blocks and 400 fresh sessions. Retain the original 26-test
family, three nested measured actions, complete-case scores and expected
strict unsupported outcomes. No old timing enters this new sample.

This is a post-incident execution-profile choice, not an unchanged continuation
of the original confirmation. The paper must disclose the aborted campaign,
diagnostic and new configuration. It cannot estimate a causal speed or
reliability effect of the JVM options from these before/after cohorts.
Primary conclusions apply to the recorded recovery profile.

No automatic new cohort, parameter escalation or invisible retry is permitted
if this recovery cohort fails. Preserve that outcome and diagnose it. This is
one explicitly planned recovery cohort, not repeated collection until a clean
or favorable result appears.

All original final verification, genuine replay, full paper, public webpage,
private source/evidence preservation and conditional exact-instance retirement
requirements remain in force. No shutdown is authorized while those conditions
are incomplete.
