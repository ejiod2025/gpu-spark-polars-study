# Independent artifact audit during collection

Historical review date: 2026-10-26. That confirmatory campaign was still collecting data.
This review read local source and archived preparation artifacts. It did not
access the H100, alter frozen sources, run workloads, or draw performance
conclusions from partial sessions.

The local validator, analyzer, worker, runner, and orchestrator match their
frozen campaign source hashes. The campaign SHA256 is
`69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0`.

**Replacement applicability, 2026-10-27:** the reviewed host was subsequently
lost. Its 120 surviving sessions and 2,808 successful output comparisons do not
constitute the completed study and cannot be pooled with the replacement.
The new host uses driver 580.105.08, kernel 6.8.0-1046-nvidia and newly generated
physical input files. Its own pre-start/runtime/input/oracle archive and
completed 400-session raw evidence must pass the same tooling and separate
complete-evidence audit. Tool behavior reviewed here is reusable; old PASS
labels are not replacement evidence. The [result-insertion ledger](RESULTS_FINALIZATION.md)
defines the remaining full-paper evidence and presentation gates.

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

## Tooling resolution, pending real-data execution

The separate [portable replay](../../research_reporting/replay.py) now records
the path-only adapter and preserves both validation outputs. The
[complete-evidence auditor](../../research_reporting/audit_complete.py) checks
the chronological/configuration/coverage requirements above and exports the
secondary sensitivity tables. The [raw-evidence packager](../../research_reporting/package_evidence.py)
provides immutable chunked archives with exact-byte hashes.

The full relocated synthetic workflow passes with 400 marked sessions and
9,360 outputs; archive mechanism and negative-case tests also pass. These are
tooling verification results. The actual campaign, final post-run byte gate,
real-data audit, replay and publication figures remain incomplete until the
fixed sample finishes and is harvested. No frozen timing source was changed.
