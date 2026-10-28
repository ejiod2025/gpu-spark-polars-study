# Prospective shared-host contention experiment, 2026-10-28 UTC

Author: **Daniel Ejiofor**. The owner explicitly requested concurrent sessions
on the existing H100 as a contention experiment. This is a separate descriptive
extension to the isolated 400-session recovery study. Its rows do not enter that
study's primary family, replace an isolated session, or establish standalone
engine speed. No performance finding is asserted by this protocol.

## Host, source and resource scope

Use the same replacement H100 and unchanged C84 source commit
`c84ef6a07341f350dae6636ab709c1b65fd894e0`, with its exact 52-file map. Reuse
the same physical SF30/SF100 Parquet and independent DuckDB oracle bytes. Bind
the reference campaign SHA256
`3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641`
and JVM profile SHA256
`a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69`.
The new plan contains copies and hashes of source, instrumentation, protocol,
reference plan/profile, manifests and four selected oracle files before timing.
The owner/root operator preserves this genuine plan off-host before execution.

The isolated scheduler must be parked separately, allowing its current worker
and cleanup to finish. Do not signal or change an original measured worker,
modify its journal, or overlap a contention action with an isolated action.
The contention runner itself does not control the isolated campaign. See the
separate interlude record for the root operator's hold/resume evidence.

Each pair consists of one Spark process and one Polars process. Both request
their full original **26 CPU threads** on the same 26-vCPU host; they are not
allocated disjoint CPUs or cgroups. Spark retains a **96 GiB heap**, G1,
shuffle120, AQE, UTC, 128 MiB file partition limit and 1 GiB driver result cap.
Both Spark paths retain the diagnostic retry-100 startup profile. Verify the
actual PrintFlagsFinal table, actual Spark configuration, Java-file hashes and
Python runtime inventories. The Spark GPU path retains the parity profile,
two concurrent GPU tasks, 2 GiB pinned pool and floating aggregation setting.
Polars CPU uses 2.0.0; strict Polars GPU uses 1.44.2/cuDF-Polars 26.10.2 streaming.

CPU, RAM, filesystem cache, disk and GPU interactions are the experimental
condition. No equal-resource or isolated-runtime interpretation is allowed.
The initial design excludes two concurrent Spark heaps and simultaneous Spark
GPU/Polars GPU default pools. Such a both-GPU condition would require a separate
prospective resource/memory decision, not an invisible tuning retry.

## Fixed pair matrix and staged sample

| Pair | Concurrent participants |
| --- | --- |
| A | Spark GPU and Polars CPU 2.0 |
| B | Spark CPU and strict Polars GPU |

The pilot is **four waves/eight fresh processes**: both pairs, each for the
original six-query baseline suite and six-query complex suite at SF30. Each
query has one recorded warmup and **one measured action**. The pilot's matrix
is shuffled by seed 20261028; its query seed is 838471203 plus the fixed wave
number. Pilot timings are descriptive feasibility observations and are not
pooled into the expansion.

Only if all four pilot waves pass correctness, cleanup, configuration and actual
measured-overlap gates, run the prospective expansion: both pairs × both suites
× SF30/SF100 × two repetitions = **16 waves/32 fresh processes**. Expansion
order is shuffled by seed 20261029; query seeds are 838472203 plus wave number.
There are no additional hidden attempts, adaptive sample increases or scale
changes after observing a failure. A failed wave stops that phase and preserves
both participants, completed actions, errors and telemetry. Do not retry until
clean. Root may report a failure or make a new explicit scientific decision.

Expected coverage, conditional on the known strict limitations, is 92 successful
output comparisons and two unsupported warmups in the pilot; 368 comparisons and
eight unsupported warmups in expansion. These are expectations, not results.
Strict Polars GPU rejects `cx_q13` and `cx_window` during warmup. Every other
selected warmup/measured output must match its original SQL oracle under the
unchanged relative 1e-8/absolute 1e-6 comparison. An OOM, process crash or unrelated
translation error is a failure; it is never converted into an expected rejection
or a timing. Complete six-query Polars GPU complex scores remain unavailable.

## Concurrent action instrumentation and endpoints

Each wave reads all selected-scale files once before either participant starts,
and records mincore observations before/after this shared preheat and after both
workers. Preheat is outside action timers and is not a residency guarantee.
Full file-size/SHA gates bracket each phase; manifests are preserved separately.

`contention_worker.py` imports the byte-identical frozen `worker.py` and invokes
its original main function. External hooks add an initial engine-ready barrier
and a barrier before each common query/phase/trial. They do not edit query
expressions, collectors, timer statements or saved action interval code. Both
workers publish readiness and use the same future monotonic release timestamp.
The per-action rendezvous occurs in the first existing `process_counters` call,
after its sample and before the original timer starts. The after-conversion
counter call is not gated. Each event is recorded, with UTC and monotonic clocks.

Both workers use the same query seed. For pair B complex measured `cx_q13` and
`cx_window`, the CPU action explicitly bypasses rendezvous because the GPU has
no measured counterpart. Both warmup attempts still rendezvous. Remaining
common query actions are synchronized independently. Every new wave/barrier
directory is exclusive; an old barrier cannot be reused as a retry.
The two unpaired CPU measurements are diagnostics. A full six-query CPU sum in
this pair mixes paired and unpaired conditions and must not be presented as a
uniformly contended suite score. Only the four common measured pairs establish
query-level contention for pair B complex.

This is **coordinated same-query contention**, not arbitrary independent user
arrival traffic. The action endpoint retains the frozen build/optimize/read/
collect timer. The secondary common-host-result endpoint retains dictionary
conversion. Wave/process wall time includes initialization and rendezvous waits;
shared preheat is reported separately. Process counter deltas and sampled RSS/
GPU memory can include the untimed rendezvous wait because their probes surround
the action; they are not isolated action-only resource costs.

Genuine action overlap must be established from the saved action UTC **and**
same-host monotonic intervals, never inferred from two process lifetimes. Report
the start skew, both action durations and intersection for every common measured
query. Pilot expansion requires positive intersection on both clocks for every
common measured query: six per baseline wave, six for pair A complex and four
for pair B complex. If a barrier launches processes but measured actions do not
overlap, preserve and report that fact instead of claiming concurrent execution.

One host/process-tree sample is recorded every 0.5 s and one GPU sample every 200 ms.
Samples can miss transient peaks; GPU board utilization/power cannot be attributed
causally to one query or process. Per-process memory sample scope is retained from
the frozen worker. The complex cleanup barrier inspects only its Spark session's
jobs and owned scratch directory; unique Spark local/event directories are used.

## Analysis and reporting

Report actual coverage, failures, paired action intersections, action endpoints,
whole-wave wall durations, resource samples and the small descriptive sample.
No p-values, superiority decisions, primary-family additions or exact crossover
thresholds are predeclared here. Two expansion repetitions do not support a
new confirmatory performance claim. Later comparisons to the completed isolated
study must be labeled descriptive, disclose different repetition/timing context
and the barrier/warm-cache policy, and never pool the two designs.

The paper and webpage must identify this extension and its disposition separately.
No partial/synthetic findings are published. Keep development and full raw
evidence private. The original full paper/page/evidence and exact-instance
retirement gates remain required; contention completion alone cannot retire the
H100. Once all contention workers and descendants are gone, root records fresh
source/runtime/input identities and resumes the same isolated scheduler PID.

## Operator commands (templates, not evidence of execution)

The root operator deploys these instruments to
`/home/ubuntu/study-h100-contention-staging-20261028`. With the isolated scheduler
parked and its current worker gone, freeze a new evidence directory:

```sh
/home/ubuntu/study/.venv/bin/python -B /home/ubuntu/study-h100-contention-staging-20261028/contention_runner.py freeze \
  --reference-campaign /home/ubuntu/study-h100-research-evidence-20261027-gclocker100/campaign.json \
  --reference-profile /home/ubuntu/study-h100-research-evidence-20261027-gclocker100/EXECUTION_PROFILE.json \
  --source /home/ubuntu/study-h100-research-c84ef6a \
  --protocol /home/ubuntu/study-h100-contention-staging-20261028/H100_CONTENTION_PROTOCOL.md \
  --output-root /home/ubuntu/study-h100-contention-evidence-20261028
```

`freeze` executes no benchmark or runtime probe. It writes
`CONTENTION_PLAN.json`, `CONTENTION_PLAN.sha256` and the complete
`CONTENTION_PRESTART_MANIFEST.json`. The root interlude wrapper owns the explicit
off-host-plan acknowledgement; the runner has no acknowledgement shortcut.
Verify and preserve that exact plan, all declared source/tool hashes and reference
files off-host before invoking the archived runner:

```sh
/home/ubuntu/study/.venv/bin/python -B /home/ubuntu/study-h100-contention-evidence-20261028/tools/contention_runner.py run \
  --plan /home/ubuntu/study-h100-contention-evidence-20261028/CONTENTION_PLAN.json --phase pilot
```

The same command with `--phase main` independently verifies the exact pilot
matrix, wave/validation/result hashes, original output comparisons and actual
overlap before expanding. It has no incomplete-data override. Both commands
refuse an existing phase directory. Preserve all phase data and failed attempts.
No CLI command in this tool pauses or resumes the isolated campaign, accesses a
cloud credential, sends an external message, publishes a Site or shuts down a VM.
