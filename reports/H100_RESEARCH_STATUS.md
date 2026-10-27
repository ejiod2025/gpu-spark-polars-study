# H100 research campaign status

Snapshot: **2026-10-27 21:47 UTC**. The new, separately frozen GCLocker100
recovery cohort is collecting its fixed 400 sessions. Its first session is
running; no recovery session had completed at this snapshot. No final
performance conclusion, paper or new H100 webpage is available yet.

The registered recovery campaign is
`3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641`.
Its 346-file prestart archive was independently reverified and privately pushed
in commit `6dbca2fbd4de1be8aa4408a639954bc9076bfa38` before timing.
Its [canonical manifest](../evidence-research/20261027/h100-confirmatory-gclocker100-v1/PRESTART_MANIFEST.json)
binds the unchanged 52-file C84 query/runner source, same 1,654 physical inputs,
all oracles and exact execution profile. The first full input-byte gate passed.

New readiness passed all ten SF1 sessions: **116 output comparisons**, all five
semantic diagnostic configurations and four actual Spark child JVM flag checks.
The separate full complex SF100 CPU Spark diagnostic passed **12 output
comparisons**, verified cleanup and effective JVM settings. These observations
are outside primary performance inference.

Both Spark paths now use fixed startup retry count 100, unlocked diagnostic
options and PrintFlagsFinal, retaining Java 17.0.20.1, G1, 96 GiB heap, 26 CPUs,
shuffle 120, AQE, all expressions and timing rules. The exact profile SHA256 is
`a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69`.
This is a disclosed execution-profile choice after an earlier failure, not a
causal test or proof of a JVM cure. See the
[prospective recovery protocol](../docs/H100_GCLOCKER100_RECOVERY_PROTOCOL.md).

The default-profile replacement campaign stopped at **88 validated sessions**,
**2,056 successful outputs** and one CPU Spark warmup Java allocation failure.
Its original journal, failed attempt and all **3,349 raw evidence files** are
verified off-host; see the [incident checkpoint](../evidence-research/20261027/default-jvm-abort/README.md).
A post-abort byte gate confirmed unchanged source, Python runtimes and all 1,654
inputs. No original attempt was erased or retried, and none of its timings is
pooled into recovery inference. The earlier lost instance's 120 surviving core
sessions likewise remain a separate historical cohort.

Current local continuation controller: PID **76437**, using
`rerun/gclocker100-20261027/raw-backups/`. Current remote supervisor: PID
**92406**, using `/home/ubuntu/study-h100-research-evidence-20261027-gclocker100`.
It stops on unexpected failure and pauses between complete blocks until their
full raw backups are verified off-host. The original default-profile controller
85571 and supervisor 21385 exited; do not restart them. Local caffeinate remains
active. A tool/SSH timeout does not authorize restarting any experiment.

Final reporting requires ten complete paired blocks, all 400 fresh sessions,
9,360 successful output comparisons, 60 specific strict-GPU rejections, final
input hashing, genuine replay/complete audit, all 24 idle JVM profile receipts,
160 formal and four readiness Spark log checks, all original primary/secondary
tables, real figures and the completed claim audit. Existing targeted tooling
and layout tests do not satisfy those study gates.

The owner requires the full research paper credited to **Daniel Ejiofor** and a
public shareable webpage before exact-instance retirement. Repository visibility
remains private, and no incomplete findings are published. The autonomous
follow-up is active. See the [completion instructions](../docs/H100_AUTONOMOUS_COMPLETION.md),
[reconstruction runbook](../operations/H100_RECONSTRUCTION_RUNBOOK.md),
[reporting replay](../research_reporting/README.md), and
[conditional retirement guard](../operations/LAMBDA_SHUTDOWN_GUARD.md).
