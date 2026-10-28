# Recovery paper: result insertion and completion ledger

Status: isolated recovery incomplete; separate contention terminal replay and
final descriptive figure binding complete,
2026-10-28 UTC. This is an editorial workflow, not new
experimental source. It changes no C84 file, query, sample size, endpoint or
statistical rule. This ledger does not itself satisfy the final publication or
host-retirement gates.

The author is **Daniel Ejiofor**. Omit affiliation unless the author supplies
one. The paper and public webpage must describe four separate roles: exploratory
pilot; lost-host campaign; aborted default-profile replacement; and one
ongoing fixed-JVM-profile recovery. Primary results can come
only from the recovery's 400 fresh sessions. Never pool the lost cohort's 120
sessions/2,808 outputs, the default-profile cohort's 88 validated sessions/2,056
outputs, or the separate feasibility diagnostic into recovery inference.
The same-host contention extension is a separate descriptive experiment, not a
fifth primary cohort or an addition to the isolated 26-test family. Its staged
pilot and completed expansion remain separate samples. The original 400
sessions are not cancelled or replaced by this requested interlude.

## Separate contention binding and interlude status

Read [H100_CONTENTION_PROTOCOL.md](../../docs/H100_CONTENTION_PROTOCOL.md) and
[H100_CONTENTION_INTERLUDE_20261028.md](../../docs/H100_CONTENTION_INTERLUDE_20261028.md)
before inserting the extension. Its exact experiment ID is
`h100-contention-20261028`; canonical pre-start is
`evidence-research/20261028/h100-contention-v1`. Plan SHA256 is
`670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031` and
pre-start SHA256 is
`4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25`.
All 65 listed checksums match, for 66 canonical files including the manifest;
all 52 source identities match C84/the recovery reference. Private commit
`98c7ff4d6a9bbe82a021671c28fed28f71827b70` preceded execution. The plan separately
hashes the external runner, barrier worker and protocol, with the same reference
recovery campaign/profile, SF30/SF100 manifests and four oracle files. Its
immutable `FROZEN_NOT_RUN` pre-start status is not a live phase-status field.

The manager held only original scheduler PID 179144 at 04:44:23 UTC on October
28. The active isolated SF30 Spark CPU runner/workers were not signalled.
`final-handoff-v1/ORIGINAL_RUNNER_DRAINED.json` records runner exit zero at
04:45:43.946178 UTC before pilot start at 04:47:43 UTC. The same scheduler,
creation time 1793160829.75, resumed at 05:32:20.044724 UTC. Its final STATE is
`COMPLETE_RESUMED`, owned cleanup lists no live members, the before-resume
profile check is PASS, and `final-metadata-v1/PHASE_DRIVER.json` is
`COMPLETE_OFFHOST`. Interlude checks remain separate from the formal profile
log. The terminal audit now verifies all 34 actual held-attempt files from the
protected off-host block04 backup. The original result SHA256 is
`c33ddd3f46d02e4624083334490925170a20a44795a3f4d53a08556a72d8b4c9` and
schedule SHA256 is
`d22c6ad51044c77aeedcbcfe611c26604b1b8c0cfba46111e8cebbb472217659`,
matching the retained drain receipt. The last original host result is
04:45:38.505969 UTC, 141.188 s before the first contention action at
04:47:59.693693 UTC. The last contention host result is 05:28:37.930202 UTC,
before same-process resume. Preserve the outer wall of 3,043.015 s/end time
05:32:20.060856 UTC alongside worker process wall 240.581 s/end time
04:45:41.907405 UTC. The outer envelope includes the scheduler hold and is
neither query nor worker latency; do not rewrite the original timing values.

Both actual phases pass: four pilot and sixteen expansion waves, with both raw
phases verified off-host. The independent
`rerun/contention-20261028/offline-terminal-audit-v1/CONTENTION_REPLAY.json`, SHA256
`0cad03b7ab2edc7e0d62dbcc7b84f8fa0ea51325aba41eb354b5baae0c51a2d3`, verifies
460 SQL outputs (230 warmup/230 measured), ten expected strict rejections,
zero oracle failures/unexpected query errors, 110 intended common measured
intersections, 250 barrier releases and ten unpaired CPU measured diagnostics.
These are aggregate artifact-accounting counts, not pooled timing estimates.
The main raw snapshot has 1,561 payload files, 487,321,600 tar bytes and SHA256
`98be99cf74690d01053de9670669e629a166b02bd67fd51a994425006f792197`.

The terminal receipt completed at 07:15:58.632271 UTC with `status: COMPLETE`,
`integrity_status: PASS`, `publication_ready: true`, no findings and handoff
PASS. It verifies `original_payload_verified`, `no_action_overlap` and
`same_scheduler_resumed` as true, with no pending reasons. The 110 positive
within-contention intersections are distinct from verified separation from
isolated work. The older unchanged
`offline-full-audit-v3/CONTENTION_REPLAY.json`, SHA256
`a34a8de887225edf58f9211a2e1f184feaa0eac5165e0dd0d03697f517e30ee5`, remains
historical PARTIAL/publication_ready false because those original bytes were
not then supplied. Its false separation boolean denoted pending proof, not
an observed overlap violation. Do not rewrite that historical receipt or use
it as the terminal binding.

The genuine final figure directory is
`rerun/contention-20261028/figures-final-v1/`, with `FIGURE_MANIFEST.json`
SHA256 `5751b86ee46989fdfc7b0d25b1e2eac3a475a09c872143026cedfbd1e06000bd`.
It binds six stems (pilot/main × pair A actions, pair B actions and actual
timeline), each exported as PNG/SVG/PDF, plus `CAPTIONS.md`. It has
`final: true`, `draft: false`, `synthetic: false`, complete terminal identity,
full input/export hashes and no pending reasons. Pilot measured rows are 44
paired participant actions plus two unpaired CPU diagnostics; expansion rows
are 176 paired participant actions plus eight unpaired CPU diagnostics.

Use the terminal audit's `QUERY_ACTIONS_DESCRIPTIVE.csv` (460 rows; SHA256
`b5fc666176782517020fa2dbf90dff6bdfa47c9b2c4ace8aff872a6ab0869335`)
and `COMMON_QUERY_OVERLAP.csv` (110 rows; SHA256
`00a5812bf431defee06d1fe5b44c4eaa6794d0efff671847e9001a3330d3f3be`)
for §5.7. That section reports expansion within-pair shorter-action counts,
query-specific examples and observed overlap ranges without pooling pilot
timings, creating suite scores or adding inference. Different competitors
prevent a ranking between GPU engines. A contention slowdown relative to
isolated execution still requires an appropriate matched analysis; temporal
separation does not establish unchanged host/cache conditions. All primary
performance interpretation and full-paper completion await the original 400.

The actual paper gate was separately exercised with this terminal directory,
final figures and explicitly selected `/tmp/h100-analysis-audit-venv/bin/python`.
Its genuine `h100-paper-contention-verification-v1` proof is preserved at
`rerun/contention-20261028/paper-terminal-verification-v1/CONTENTION_VERIFICATION.json`,
SHA256 `a2f2401acf21b3ea7f3832568a0eeb98b5a8efe1bb359cd350d9ac53d7568099`.
The selected-runtime subprocess freshly reproduces the current-source audit,
binding 2,062 raw audit inputs and 29 verifier/figure inputs to the exact terminal
review object and figure manifest above. This is a separate-role gate check,
not a final paper build, editorial attestation or original400 completion.

## 1. Bind the actual recovery before inserting a primary result

Record the following exact values from files, never from a running status
message. The October 26 SHA beginning `69669dfb` and default-profile October 27
SHA beginning `0dabe0d4` are historical incomplete identities. Neither may
satisfy the final recovery paper gate. The registered identity is
`h100-confirmatory-gclocker100-20261027`, with canonical archive directory
`evidence-research/20261027/h100-confirmatory-gclocker100-v1`.
Its exact registered hashes are:

| Artifact | SHA256 |
| --- | --- |
| Recovery campaign | `3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641` |
| Pre-start manifest | `ea57fa1221b905e8ee287fe7860285c7a37535b084f13ce3dbbbca5ac4e29ace` |
| Execution profile | `a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69` |

The 346-file canonical snapshot passes all 345 listed file checksums. Pre-start
private commit `6dbca2fbd4de1be8aa4408a639954bc9076bfa38` preceded the first
recovery session, recorded at 21:47:44 UTC after the initial full 1,654-file byte
gate at 21:47:43.892782 UTC. The separate twelve-output diagnostic and repeated
116-output readiness both pass; all five semantic fixture configurations and
four actual Spark readiness JVM flag logs are checked. The canonical pre-start
contains two passing readiness-boundary profile receipts. Completion still
requires the full 24-receipt/160-formal-Spark-log proof, all 400 sessions,
post-final byte gates and genuine replay. No partial performance estimate is
authorized by this collection milestone.

| Binding | Source | Required check |
| --- | --- | --- |
| Recovery campaign ID and SHA256 | New `campaign.json` and `PRESTART_MANIFEST.json` | Exact registered recovery campaign/pre-start/profile tuple; unchanged C84 source identity |
| Pre-start manifest SHA256 | SHA256 of the archived `PRESTART_MANIFEST.json` bytes | Matches the committed campaign identity registry |
| Sealed execution profile | `EXECUTION_PROFILE.json`, its SHA256 and registry entry | Exact `SPARK_SUBMIT_OPTS` with unlocked diagnostic options, retry count 100 and `PrintFlagsFinal`; absent competing Java-option variables; Java/runtime/launcher/environment hashes bound before timing |
| 52-file source and protocol | `campaign.json.source/`, pre-start inventory | C84 commit `c84ef6a07341f350dae6636ab709c1b65fd894e0`; complete archive verification |
| Reused physical inventory | Same five replacement manifests, plan `.datasets` and profile-bound identities | Same 1,654 physical files and existing eight oracle bytes; no recovery regeneration/mutation; preserve the actual v1 physical dataset table |
| Host and runtime | Repeated readiness, sealed profile and formal-session audit | Same replacement H100 VM; 26 vCPUs; 237,490,843,648 RAM bytes; driver 580.105.08; kernel 6.8.0-1046-nvidia; Java 17.0.20.1, 96 GiB heap/G1; unchanged five Python configurations and JAR |
| Separate feasibility diagnostic | Canonical pre-start `diagnostic-sf100-driver_log.log` and `diagnostic-sf100-validation.json`; exact profile-bound paths/hashes | One full original complex SF100 Spark CPU suite, seed 838471203, one warmup/measurement per query; all 12 oracle-valid outputs, normal cleanup, exit success and actual driver proof; timings excluded |
| Repeated readiness, fixtures and oracle identities | New `readiness/`, input-domain record, eight reused `oracles/*.json`, sealed diagnostic/readiness proof | All five configurations pass repeated SF1 readiness before new collection; earlier readiness alone does not establish recovery feasibility |
| Raw evidence root | New plan `.output_root`, final journal and harvested tree | Exact bytes preserved; paths remapped externally, never rewritten inside JSON |
| Final journal and backup receipts | Final raw tree; all ten full block manifests/receipts | Every 40-session block preserved off-host; actual collection interval and pacing |
| Effective-profile chronology | Root `EXECUTION_PROFILE_GATES.jsonl` and all 160 Spark driver logs | Exactly 24 idle receipts: readiness before/after, ten block pairs, final before/after; same sealed Java/Python environment; receipts bracket actual sessions and completion |

The completed diagnostic validation schema is
`h100-gclocker100-feasibility-validation-v1`. Require `status: PASS`, source C84,
`engine: spark_cpu`, `suite: complex`, `scale: "100"`, `runs: 1`,
`query_seed: 838471203`, exact `manifest_sha256`/`oracle_sha256`/`results_sha256`,
`successful_outputs_compared: 12`, six queries, twelve records with both
`passed: true` and `comparison.passed: true`, twelve checked cleanup records,
and the five effective `driver_flags`. The profile's diagnostic receipt binds
its status/completion time plus driver-log and validation paths/hashes.
Independently check the four repeated SF1 Spark readiness logs as well as the
160 formal Spark logs. No count-only diagnostic PASS or historical readiness
label substitutes for those actual proofs. Do not repeat/escalate a failed
diagnostic until clean; preserve it for a new explicit scientific decision.

Retain the observed default-profile incident in the final paper: halt at
20:27:48 UTC; failed first `cx_q09` warmup in
`block-03-complex-sf100-spark_cpu` before any successful output or measured
record; exit 52/Java heap-space error and preceding GCLocker warnings. Preserve
the 3,349-file, 220,656,964-byte incident gzip with SHA256
`79c99819d272cc1af947d8e200976a940cd63a96be580112e4a6c5900adee6ff`, unchanged
journal/attempts and two separate complete-block backups. Failure-time heap/RSS
and GC-recovery data are unavailable. Do not call this a false OOM, a diagnosed
GCLocker root cause or a demonstrated cure.

## 2. Run the existing complete evidence workflow locally

Use an analysis environment outside the H100 measurement process. Set the
paths below to actual immutable directories. All output directories must be
new. The replay does not need a GPU, JVM or generated Parquet files. Do not
pass `--allow-synthetic` for study evidence.

```sh
python research_reporting/replay.py \
  --archive /absolute/recovery-prestart-archive \
  --artifacts-root /absolute/recovery-harvest/campaign-results \
  --output /absolute/recovery-replay
```

Before proceeding, require real `REPLAY_MANIFEST.json` with `status: PASS`,
`phase: complete`, `synthetic: false`, the exact recovery campaign/pre-start/
profile hashes and unchanged C84 source. Preserve `validation.raw.json`, derived
`validation.json`, `PATH_NORMALIZATION.json` and all subprocess logs. The
adapter changes only validated Spark plan source paths in derived validation.

Check `complete-audit/AUDIT_COMPLETE.json` for `status: PASS`, no findings,
matching campaign/journal identity, and the full action/configuration/cache/
integrity accounting. Its full-byte gates must precede the first session and
follow the last session before final completion. Expected aggregate counts
under the frozen coverage pattern are 400 sessions, 9,360 successful outputs,
60 expected unsupported warmups, 3,360 successful complex cleanup records and
60 failed-action cleanup records. The complete audit must also contain an
`execution_profile` proof with `path: EXECUTION_PROFILE.json`, exact registered
`sha256`, `spark_driver_logs: 160`, `profile_gates: 24` and `status: PASS`.
Each Spark log must prove retry count 100, unlocked diagnostics, G1 and maximum
heap 103,079,215,104 bytes. Report actual checked outcomes; expectations alone
are not evidence.

Package and verify the complete private raw tree after collection ends, using
the same actual complete audit. Preserve original remote final validation and
analysis in that raw tree; portable replay outputs remain separate.

```sh
python research_reporting/package_evidence.py \
  --evidence /absolute/recovery-harvest \
  --prestart-archive /absolute/recovery-prestart-archive \
  --audit-complete /absolute/recovery-replay/complete-audit/AUDIT_COMPLETE.json \
  --output /absolute/recovery-raw-bundle
```

The canonical pre-start seal remains separate from the reconstructed harvest;
do not copy it into a tree with advanced operational logs. Keep all parts,
manifests and extraction/replay receipts off-host. A successful
bundle is an integrity result, not an interpretation or publication decision.

## 3. Insert the full paper results, with these exact contracts

`P` below means the actual new successful replay directory. All tables use the
same recovery campaign and profile hashes. Values are copied from the predefined analysis;
the paper workflow must not recompute a different statistic.

| Paper item | Exact artifact and fields | Completeness/cross-check | Placement and interpretation |
| --- | --- | --- | --- |
| Campaign disposition and integrity table | Historical incident archive; recovery final `journal.json`; `P/validation.json`; complete audit; ten raw block backups and 24 profile receipts | Four unpooled roles; observed abort; one recovery collecting after passed diagnostic/readiness; final 400 fresh identities, all attempts/deviations and bracketing gates | §5.1; report failure and post-incident profile/pacing choice explicitly |
| Reused input table | Same v1 manifests, new plan `.datasets`, profile-bound identity and full byte gates | Five scales and same 1,654 physical files; preserve exact actual v1 byte totals and oracle identities | §3.2; no regeneration; stored bytes do not establish scan volume, working set or out-of-core capacity |
| Runtime/configuration and JVM profile | `.configs[].runtime`, `EXECUTION_PROFILE.json`, complete audit, 24 receipts and 160 actual Spark driver logs | All five Python configurations, exact JVM options, Java/runtime/launcher hashes, G1/96 GiB/retry 100 and unchanged settings | §3.1/§4; post-incident configuration choice, not a causal mitigation comparison |
| Feasibility, readiness and semantic coverage | Separately preserved 12-slot diagnostic proof; repeated `readiness/READINESS.json`, fixtures and input-domain record; reused oracle bytes | Actual recovery pre-start PASS artifacts only; preserve original window-domain limitation and strict failures | §5.1–5.2; diagnostic timings excluded; feasibility is not a cure or guarantee |
| Complete-suite score and coverage table | `P/figures/complete_case_scores.csv`: `suite`, `scale`, `config_id`, `complete_blocks`, `query_coverage`, `geometric_mean_score_seconds` | Exactly 40 cells; 37 complete score cells and three unavailable complex Polars GPU cells under expected coverage | §5.2–5.3; descriptive geometric means of session sums, not once-through latency |
| All primary contrasts | `P/analysis/analysis.json`: `.primary[]`; `P/figures/primary_contrasts.csv` | Exactly 26 rows, each ten paired blocks; all four families and all prescribed conditions; raw/adjusted p values preserved | §5.3 and complete supplement; show estimate, pointwise CI, `p_raw`, `p_holm` and supported decision |
| Baseline primary figure | `P/figures/suite_ratios_baseline.*`; caption from `P/figures/CAPTIONS.md` | Renderer binding to analysis and 260 primary block ratios; exact export hash checked in manifest | §5.3; ten block points, pointwise t intervals, full Holm family in caption |
| Complex primary figure | `P/figures/suite_ratios_complex.*`; same caption source | Exact export hash checked; no complex Polars GPU complete score | §5.3; preserve unavailable path rather than four-query surrogate |
| Query heterogeneity figure/table | `P/figures/query_ratios_action.*`; `.exploratory_queries[]` in analysis | Every supported/unsupported original query retained; ten block query observations where available | §5.4; descriptive, with no query-level superiority claim |
| Block/order diagnostics | `P/analysis/block_ratios.csv` filtered by `family == primary_suite`, `endpoint == seconds`; `.primary[].sd_log_ratio` | Exactly 260 primary block rows; ten per contrast; retain chronology and configuration positions from session medians | §5.6/supplement; show temporal spread; no post-hoc exclusion or new significance claim |
| Endpoint sensitivity | `P/complete-audit/HOST_RESULT_SENSITIVITY.csv`; `.secondary_direction_changes` | Exactly 32 pairs; six complex Polars GPU N/A pairs; direction-change flags agree | §5.5/full supplement; secondary ratios and pointwise CIs are descriptive |
| Residency and host state | Every session schedule's three cache observations; retained environment/host/GPU telemetry; complete audit | All 400 session inventories; note available sampling/counter/process-population limits | §5.6; snapshots and sampled peaks are not guarantees or causal explanations |
| Figure/paper/page provenance | Figure and replay manifests; final paper `BUILD_MANIFEST.json`; public webpage provenance | Real-data `synthetic: false`; same recovery campaign, `execution_profile_sha256` and source/input/export hashes | §8/supplement; figures copied verbatim; final paper/page accept only the exact sealed recovery profile |
| Raw archive availability | `BUNDLE_MANIFEST.json`, `CHUNKS.sha256`, `REASSEMBLE.md`, verified off-host receipt | Every raw file and part bound; extraction/replay verified | §8; state actual private access and any authorized release terms accurately |

### Separate contention and managed-interlude insertion contract

Keep the complete contention raw tree and its backup/verification schemas
separate from the primary reconstruction/replay. A contention wave or phase
PASS never satisfies a missing isolated session. Preserve the genuine pre-start
seal separately from the later phase files and final handoff controls.

| Paper item | Exact separate evidence | Completeness/cross-check | Interpretation |
| --- | --- | --- | --- |
| Extension identity/source/instrumentation | Canonical `CONTENTION_PLAN.json`, `.sha256`, `CONTENTION_PRESTART_MANIFEST.json`, 52-file `FROZEN_SOURCE/`, three declared tool/protocol hashes and reference plan/profile | Exact hashes above, 65 matching listed checksums, `synthetic: false`, same C84/reference identities | §4.5; external instrumentation is separately versioned, with no original expression/timer edits |
| Staged matrix and resource table | Plan `.pilot`, `.main`, `.main_gate`, `.settings`, `.configs`, `.spark_submit_opts`; phase runtime/configuration gates | Four pilot waves/eight processes at SF30; only after all pilot gates, sixteen expansion waves/thirty-two processes at SF30/SF100 with two repetitions | One Spark + one Polars process, one GPU path; both request 26 CPU threads on the same 26-vCPU host, one 96 GiB Spark heap; deliberate shared resources |
| Actual phase/wave disposition | Terminal-v1 `CONTENTION_REPLAY.json`, both `<phase>/PHASE.json`, final wave/member artifacts and off-host phase ACKs | Actual pilot PASS 4/4 and expansion PASS 16/16; all 460 SQL outputs pass, ten expected strict rejections, zero unexpected errors; terminal COMPLETE/integrity PASS/publication_ready true | §5.7; separate phase samples and no imputed times; the completed extension does not complete original400 |
| Common measured overlap/action table | `<wave>/VALIDATION.json`: `.measured_action_overlap[]`; exact matching participant action UTC/monotonic bounds and both endpoints | Per common query: start skew, left/right durations, monotonic intersection and UTC intersection, positive on both clocks; six pairs normally, four for pair B complex | Coordinated same-query contention; overlapping process lifetimes/barrier release do not prove overlapping action timers |
| Descriptive expansion action findings | Terminal `QUERY_ACTIONS_DESCRIPTIVE.csv`, filter wave prefix `main-`, phase `measured`, pairing flag true; match both participants by wave/query/trial | Eight pair/suite/scale rows; twelve common pairs per cell except eight for pair B complex; two waves per condition; retain individual query values and both endpoints | §5.7 counts shorter recorded actions, not independent replicates, significance or suite scores; no cross-pair GPU ranking or isolated slowdown estimate |
| Final extension figures/captions | `figures-final-v1/FIGURE_MANIFEST.json`, exact terminal audit and current renderer hashes, every PNG/SVG/PDF and `CAPTIONS.md` | Six verified figure stems; pilot separated from expansion; point plots show common measured actions; timelines retain hatched unpaired CPU diagnostics and actual gaps without widening fast bars | §5.7/full supplement; individual observations, no averages/CIs/tests, zero at each wave's first measured action rather than process start |
| Pair B complex unpaired diagnostics | Strict Polars GPU `cx_q13`/`cx_window` warmup rejections, barrier bypass events and CPU measured records | Mark these two CPU measurements unpaired in each affected wave; retain original oracle agreement and no imputed GPU time | A six-query CPU sum mixes paired/unpaired conditions and is not a uniformly contended suite score; only four common measured pairs supply query-level contention |
| Shared preheat, walls and resource observations | `WAVE.json` preheat/cache/member/start/end fields; participant process walls/counters; `barriers/events-*.jsonl`, `host-processes.jsonl` and `gpu.csv` | Keep shared preheat separate; startup/rendezvous waits remain visible; host/process-tree 0.5 s and GPU 200 ms sample scope disclosed | Counters and sampled RSS/GPU memory may include untimed rendezvous waits; sampled peaks/board power are not isolated per-query resource costs |
| Managed hold, drain and return to isolated work | `final-handoff-v1/` actual CONFIG/STATE/EVENTS/drain/cleanup/profile/RESUMED receipts; final metadata; protected block04 actual attempt; terminal `.handoff` | All 34 original files verified; actual endpoint separation and original result/schedule hash agreement; same PID 179144/create 1793160829.75 resumed 05:32:20.044724 UTC; cleanup has no live members | §4.6/§5.7; preserve outer-wall inflation and actual worker wall/endpoints; separation does not establish unchanged host/cache conditions |
| Separate preservation and final metadata | Verified contention phase snapshots/raw manifests/off-host acknowledgements, then final driver/guardian/handoff metadata after phase ACK | Exact raw bytes and extraction/transfer hashes; later resume/exit receipts preserved even when not in the main phase archive | §8; keep interlude identity checks outside the formal 24-record profile log; both full isolated delivery and separate contention disposition remain required before retirement |

Require both final phase and final wave cleanup evidence, not only a wave
`VALIDATION.json` PASS written before final cleanup. Reconcile every referenced
wave/result/validation hash. Preserve blocked or unrun expansion as such if its
gate fails. No p values, superiority decisions, new primary tests or exact
crossover threshold are specified for the two-repetition descriptive extension.
Any later comparison against isolated results must disclose different sampling,
barrier, preheat and metric boundaries without pooling either design.

The same original scheduler has resumed; do not repair or replace its journal
to conceal the interlude. Terminal-v1 now binds the original attempt bytes and
worker action endpoints and verifies separation. Preserve v3 as historical
PARTIAL rather than editing or promoting it. Interlude runtime/
input/profile checks are separate files, not extra entries in the formal
24-receipt log. Temporal pacing and possible cache/host-state carryover remain
in the validity discussion. Supplying the existing protected original payload
to the audit adds provenance, not a rerun or new experimental observation.

The figure stems above are defined by the current renderer and must also appear
in the actual real-data `RENDER_MANIFEST.json` before insertion. Their presence
in this ledger is not a claim that the completed result files already exist.

For the primary table, retain the complete `analysis.json` decision fields
`supported_gpu_advantage`, `supported_reference_advantage`,
`holm_reject_two_sided_005` and `zero_observed_log_variance`. The renderer's CSV
contains numeric estimates and p values; it is not the sole source of the
decision labels. Identify any zero observed log-ratio variance explicitly.

## 4. Write the finding-bearing prose only after insertion

Replace the technical-draft status, abstract, §5 placeholders, relevant §6
discussion and §9 conclusion together. Use each exact contrast ID to support
every number and conclusion in the abstract and webpage. Summarize effect
sizes and uncertainty as well as decisions. Retain comparisons without support
and any supported reference advantage. Do not state that one engine wins
universally when results vary by suite, scale, expression coverage or endpoint.

For each proposed sampled-scale bracket, list all measured points of the same
contrast, identify the supported upper point, and check all larger measured
points for contradiction. Do not infer a lower CPU advantage from lack of GPU
support, and do not report an exact unmeasured byte threshold.

Move planning instructions out of the final paper after their results are
written; preserve this ledger in the artifact. Keep historical pilot values
confined to their labeled appendix, and retain both incomplete-campaign
dispositions and the separate diagnostic in methods/results. Disclose that the
fixed profile was selected after the observed failure and that the old cohorts
cannot estimate its causal speed or reliability effect. Allocation-retry/GC
stalls occurring inside an action stay inside normal endpoints without trimming.
No automatic escalation, invisible retry or new cohort follows another failure.
Add no transfer, spill, compute or energy mechanism without
separate measured evidence. Resolve actual notice/attribution requirements
without inventing an affiliation, public dataset URL or publication venue.
The terminal original-payload binding now supports the separate descriptive
contention/interlude text in §4.6/§5.7. Preserve the historical v3 PARTIAL
qualification as history, not current status. Final primary finding-bearing
prose and paper completion still require the original 400-session study.
Keep every finding's sample and
metric scope explicit, including unpaired CPU complex actions and wait-inclusive
resource observations. The managed pause may alter later host/cache conditions;
do not claim a causal contention slowdown without an appropriate matched
isolated analysis. Completed extension provenance alone does not finalize the
full paper.

## 5. Build and inspect the paper and compare the webpage

`paper/build_paper.py` creates a local PDF/HTML from the manuscript plus its
references, records hashes and preserves the generated source. Its default is
an explicit draft. It never launches measurements or publishes a page.

```sh
python publication/h100-confirmatory/paper/build_paper.py \
  --output /absolute/new-draft-paper-build
```

For a final build, first complete the paper text and create a separate review
JSON with `status: complete`, exact `campaign_sha256`, `manuscript_sha256` and
`references_sha256`, author `Daniel Ejiofor`, and all six review booleans true:
`all_26_primary_rows`, `all_40_coverage_cells`, `all_32_secondary_pairs`,
`deviations_and_limits`, `results_and_captions`, and `contention_results_and_limits`.
It must also contain the exact verified `contention_review` object described
below. This is an explicit editorial
attestation, not a substitute for scientific replay. The builder checks it
against the actual source and genuine recovery replay. The final
`BUILD_MANIFEST.json.execution_profile_sha256` and public webpage provenance
must match the registered sealed profile and complete audit. Earlier campaign
hashes cannot satisfy this gate. The editorial review JSON's exact campaign/
source hashes bind it to that verified replay; it has no separate required
profile-hash field. Profile identity is explicit in the build/audit/page
manifests.
Final builds require `--contention-replay` with the genuine terminal separate
role and `--analysis-python` with an explicitly selected analysis environment.
The builder invokes the shared fresh current-source offline audit in that
subprocess; it never chooses a receipt-provided interpreter or assumes that
the PDF/ReportLab runtime has scientific dependencies. Common gate imports
remain independent of Matplotlib. PARTIAL/publication_ready false is refused;
methods-only drafts may omit those arguments and remain unpublished.

The exact final `BUILD_MANIFEST.json.contention_review`, and the same object
inside the editorial JSON, is `{status: "COMPLETE", experiment_id:
"h100-contention-20261028", plan_sha256:
"670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031",
prestart_sha256: "4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25",
replay_sha256: "0cad03b7ab2edc7e0d62dbcc7b84f8fa0ea51325aba41eb354b5baae0c51a2d3",
disposition: "COMPLETE"}` for the actual completed extension. The gate also
supports an honestly stopped terminal experiment, but that is not this observed
disposition. Do not use the historical PARTIAL v3 receipt hash as a terminal
binding. The existing deviation/caption reviews and new explicit
`contention_results_and_limits` review cover actual phase/failure disposition,
unpaired CPU diagnostics, wait-inclusive metrics, scope and scheduler interlude.
An editorial marker cannot substitute for freshly verified raw evidence.

```sh
python publication/h100-confirmatory/paper/build_paper.py \
  --replay /absolute/recovery-replay \
  --contention-replay rerun/contention-20261028/offline-terminal-audit-v1 \
  --analysis-python /tmp/h100-analysis-audit-venv/bin/python \
  --contention-figures rerun/contention-20261028/figures-final-v1 \
  --final-review /absolute/FINAL_PAPER_REVIEW.json \
  --output /absolute/new-final-paper-build
```

Optional final contention figures use `--contention-figures` with the separate
genuine final manifest bound to that same terminal receipt/current renderer and
complete input/export hashes. Markdown embeds use
`contention/figures/<actual-final-export>.png`; primary figures continue using
`replay/figures/`. Draft/static preparation exports remain private and cannot
enter a final paper binding. `CONTENTION_VERIFICATION.json` preserves the
selected analysis runtime and exact verification inputs/provenance.

Inspect every rendered PDF page at readable resolution, including tables,
captions, long identifiers and references. Verify PDF author/title, no clipped
content or draft markers in the final, correct local HTML figures/links, and
page-by-page text consistency. Preserve `BUILD_MANIFEST.json` beside the PDF.
Compare the webpage's numbers, uncertainty, coverage and claims to the paper,
and retain the actual webpage snapshot/source hash. Only the completed full
paper, completed public webpage and safely harvested private source/evidence satisfy the intended
deliverables; this ledger and a draft preview do not.
