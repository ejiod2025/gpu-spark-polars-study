# Independent artifact audit during collection

Historical review date: 2026-10-26. That confirmatory campaign was still collecting data.
This review read local source and archived preparation artifacts. It did not
access the H100, alter frozen sources, run workloads, or draw performance
conclusions from partial sessions.

The local validator, analyzer, worker, runner, and orchestrator match their
frozen campaign source hashes. The campaign SHA256 is
`69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0`.

**Later-cohort applicability, 2026-10-27:** the reviewed host was subsequently
lost. Its 120 surviving sessions and 2,808 successful output comparisons do not
constitute the completed study and cannot be pooled with the replacement.
The replacement VM uses driver 580.105.08 and kernel 6.8.0-1046-nvidia. Its
default-profile campaign was then aborted, creating a fourth, conditionally
planned recovery role after the pilot, lost-host campaign and default-profile
replacement. The recovery reuses the same 1,654 replacement files/oracle bytes
and C84 source but changes the fixed Spark driver startup profile. Its own
genuine pre-start/profile/readiness evidence and 400 fresh sessions must pass
the full replay and extended audit. Old PASS labels and incomplete timings are
not recovery evidence. The [result-insertion ledger](RESULTS_FINALIZATION.md)
defines the remaining full-paper and public-webpage gates.

## Observed default-profile incident and evidence preservation

Campaign `h100-confirmatory-replacement-20261027`, SHA256
`0dabe0d40c5adda0a087508e3f95ceb0636815f5133a3b401071612e3f24c145`, stopped
at 20:27:48 UTC with 88 scientifically validated sessions, 2,056 successful
output comparisons, one failed session and 311 unstarted sessions. The failed
Spark CPU complex SF100 session failed in its first `cx_q09` warmup before any
successful output or measured observation. Exit 52, `Java heap space` and
preceding GCLocker retry warnings are retained facts. The BlockManager
maximum-on-heap value of 61,658,785,382 bytes corroborates the configured
96 GiB heap; nearest host observations show 211,723,608,064 available RAM bytes,
zero swap and 2,713,801,965,568 free disk bytes. No failure-time heap/RSS or
GC-recovery data establish the actual cause or a false-OOM diagnosis.

The full 3,349-file abort was preserved off-host in 220,656,964 gzip bytes,
SHA256 `79c99819d272cc1af947d8e200976a940cd63a96be580112e4a6c5900adee6ff`.
Original journal/attempt bytes remain unchanged, and the two completed blocks
also have separate verified raw backups. No successful session is rerun within
that campaign. This retained failure is scientifically reportable; it cannot
supply the complete primary analysis or be silently relabeled as recovery.

## Additional planned recovery proof

The [prospective recovery protocol](../../docs/H100_GCLOCKER100_RECOVERY_PROTOCOL.md)
permits one proposed cohort, `h100-confirmatory-gclocker100-20261027`, only
after one separate full-complex-SF100 Spark CPU feasibility diagnostic and
repeated five-configuration SF1 readiness pass. No recovery readiness,
collection or performance finding is asserted here.

The sealed `EXECUTION_PROFILE.json` must bind driver `SPARK_SUBMIT_OPTS`
`-XX:+UnlockDiagnosticVMOptions -XX:GCLockerRetryAllocationCount=100 -XX:+PrintFlagsFinal`,
unchanged Java 17.0.20.1/G1/96 GiB heap, Java runtime/launcher hashes, startup
environment, all five Python configurations and reused input/oracle identities.
Competing Java-option variables must be absent. The command-builder environment
must remain distinct; `JAVA_TOOL_OPTIONS` is not an accepted injection path.

Canonical pre-start diagnostic proof is `diagnostic-sf100-driver_log.log` and
`diagnostic-sf100-validation.json`, bound by the sealed profile. The validation
uses schema `h100-gclocker100-feasibility-validation-v1`, `status: PASS`, C84
source, `engine: spark_cpu`, `suite: complex`, `scale: "100"`, `runs: 1`, seed
838471203, matching manifest/oracle/results hashes, six queries, twelve passing
warmup/measured records and twelve checked cleanup records. Every record and
its comparison must pass; a count alone is insufficient. Its actual driver
flags and successful exit prove only diagnostic feasibility. Those timings
never enter the primary sample.

The extended final audit must verify the four repeated SF1 Spark readiness
driver logs, all 160 formal Spark driver logs and 24 idle profile receipts in
`EXECUTION_PROFILE_GATES.jsonl`: readiness before/after, ten block before/after
pairs and final verification before/after. Receipts must bind unchanged Java/
Python environments and bracket actual session bounds and final completion.
All actual Spark drivers must prove retry count 100, unlocked diagnostics, G1,
the intended flag recording and maximum heap 103,079,215,104 bytes. Expected
complete-audit profile proof is `{path: "EXECUTION_PROFILE.json", sha256: exact
registered profile hash, spark_driver_logs: 160, profile_gates: 24, status: "PASS"}`.
Final paper/page provenance must carry the same `execution_profile_sha256`.

Allocation-retry/GC stalls occurring inside action timers remain measured;
none is trimmed as an outlier. A passing diagnostic or completed cohort cannot
establish a causal cure or reliability improvement from the before/after
cohorts. Another failure does not permit automatic escalation, invisible retry
or additional cohorts until a clean result appears. Actual recovery execution
and these real-evidence proofs remain pending.

## What the combined gates establish

The analyzer requires all 400 planned session identities, exactly one accepted
attempt each, complete per-session scientific gates, and a complete matching
final validation. Every supported query must retain its warmup and three
measured actions; all successful actions must have passing oracle comparisons.
The two original Polars GPU complex rejections remain explicit, with no
complete-suite score. Consequently the fixed successful-output total is
**9,360: 2,340 warmups and 7,020 measured actions**, plus 60 expected unsupported
warmup outcomes. No silent exclusion of a trial or partial complex-suite sum
was found in this path.

The renderer checks all 400 session summaries, the 260 primary paired-block
CSV observations, the 26 primary estimates and Holm adjustment, and every
query cell it draws. Its captions distinguish query diagnostics from primary
inference, pointwise from simultaneous intervals, and sum-of-medians scores
from observed suite latency. The manuscript keeps confirmatory results pending.

## Findings to resolve before final publication

1. **Regenerated validation is path dependent.**
   `research_validate.audit_session` includes the actual inspection path in
   Spark GPU `plans[].source`. After relocation, a fresh local validation uses
   local paths while the immutable scientific gate retains VM paths.
   `research_analysis.load_evidence` compares these payloads exactly and rejects
   the difference. Replaying an already generated remote final validation is
   different from regenerating it locally. A separate portability adapter may
   inspect mapped bytes, verify their SHA256 and containment, and canonicalize
   only this source field to the original frozen identity. Preserve the raw
   relocated validation, the derived validation, and adapter provenance. No
   timing record or original scientific gate needs modification.

2. **A count of integrity gates does not prove temporal bracketing.**
   The frozen final validator requires a campaign completion marker and at
   least two passing integrity entries, but does not compare gate timestamps
   with the first and last sessions. Multiple entries already exist while the
   campaign is incomplete. The frozen orchestrator does perform full byte
   hashing before recording final completion. The final artifact audit should
   additionally verify a gate before collection and a gate after the final
   session, all five scales and expected file counts, and reconcile the frozen
   manifests. Do not infer the post-run check from `len(gates) >= 2` alone.

3. **Scientific PASS is not the entire effective-configuration audit.**
   The session validator checks identities, engine versions, strict Polars GPU
   guards, oracle agreement and GPU Spark plans. It does not independently
   compare every formal session's effective thread allocation, CPU streaming
   selection, Spark master/heap/shuffle/parity settings, CPU Spark absence of
   GPU operators, or cache-observation completeness with the protocol. These
   fields are retained in the raw artifacts, and execution is controlled by
   frozen code. Audit them across all sessions before making the corresponding
   material/configuration claims. Any actual discrepancy requires explicit
   deviation assessment; a PASS label alone is insufficient.
   Also inspect all 60 unsupported diagnostics: the frozen validator accepts
   broad `unsupported`/`not supported` message tokens for the two allowed query
   IDs. An unrelated I/O or runtime error containing those words could receive
   the same coverage label. The retained errors should establish the expected
   translation rejection, rather than relying on that substring test alone.

4. **Complete the secondary-endpoint presentation.**
   The frozen analysis retains common-host-result estimates and direction
   changes. The current renderer exports action-endpoint figures and tables;
   it does not export the complete secondary sensitivity table. The final
   manuscript or supplement should expose those paired secondary results and
   identify any changed conclusions, as prescribed, without introducing new
   primary tests.

These are postcollection replay, audit and presentation requirements. They do
not by themselves invalidate the collected action timings or require changing
the frozen schedule. Final publication still requires the completed sample,
post-run integrity evidence, full validation and harvested-archive replay.

## Historical tooling resolution, recovery execution pending

The separate [portable replay](../../research_reporting/replay.py) now records
the path-only adapter and preserves both validation outputs. The
[complete-evidence auditor](../../research_reporting/audit_complete.py) checks
the chronological/configuration/coverage requirements above and exports the
secondary sensitivity tables. The [raw-evidence packager](../../research_reporting/package_evidence.py)
provides immutable chunked archives with exact-byte hashes.

The full relocated synthetic workflow passes with 400 marked sessions and
9,360 outputs; archive mechanism and negative-case tests also pass. These are
tooling verification results for that reviewed workflow. They do not establish
the recovery's new profile/diagnostic/receipt gates or completed study results.
The recovery's final post-run byte gate, profile audit, real-data replay and
publication figures remain incomplete until the fresh fixed sample finishes
and is harvested. No frozen timing source was changed.
