# Claim and evidence matrix

Status: recovery incomplete; separate contention terminal audit and final
descriptive figures complete, 2026-10-28 UTC. Primary performance interpretation
and full-paper completion remain pending.
All paths below are repository-relative unless explicitly
marked remote. JSON selectors are machine-readable `jq` expressions.

`E` denotes the **lost October 26 cohort only**:
`evidence-research/20261026/h100-confirmatory-v1`. Existing C01–C15 evidence below
establishes that cohort's design, preparation and historical pilot context. It
does not establish replacement runtime, physical inputs, readiness or results.
Its surviving three core checkpoints (120 sessions; 2,808 successful output
comparisons) are incomplete and never pooled into later primary inference.

`V` denotes the **aborted default-profile October 27 replacement**:
`evidence-research/20261027/h100-confirmatory-replacement-v1`. Its campaign SHA256
is `0dabe0d40c5adda0a087508e3f95ceb0636815f5133a3b401071612e3f24c145` and
pre-start manifest SHA256 is
`a758ceea7d16a6c726221db5db2cb99502ac4f640131341b456e9b63ed459b2a`.
Its preparation artifacts establish the reused host/input/oracle inventory;
its 88 scientifically validated sessions and one failure do not complete a
primary family and are excluded from recovery timing inference.

`R` is the **registered recovery** archive directory
`evidence-research/20261027/h100-confirmatory-gclocker100-v1`, ID
`h100-confirmatory-gclocker100-20261027`. Its campaign SHA256 is
`3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641`,
pre-start manifest SHA256 is
`ea57fa1221b905e8ee287fe7860285c7a37535b084f13ce3dbbbca5ac4e29ace`, and
execution-profile SHA256 is
`a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69`.
All 345 listed pre-start checksums match, for 346 canonical files including
the manifest. Private commit `6dbca2fbd4de1be8aa4408a639954bc9076bfa38`
preceded the first recovery session. Feasibility and repeated readiness are
completed pre-start observations, excluded from primary inference.
`P` denotes the future genuine completed recovery replay, which remains pending. `B` is its
eventual final paper build output. Neither prior campaign can satisfy the
recovery primary-results gate. Resolve the final raw/incident extraction paths
and these output bindings to literal paths before final reporting.
Expand aliases before resolving files. Do not rewrite paths inside hashed JSON.

`T` denotes the separate, immutable contention pre-start archive
`evidence-research/20261028/h100-contention-v1`, experiment
`h100-contention-20261028`. Its plan SHA256 is
`670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031` and
pre-start SHA256 is
`4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25`.
All 65 listed checksums match, for 66 canonical files including the manifest.
Private pre-start commit `98c7ff4d6a9bbe82a021671c28fed28f71827b70`
preceded execution. `U_phase` denotes each extracted phase snapshot directory
listed in `Q.snapshots[phase].directory`; phase files are under
`U_phase/<phase>/`. `H` is `rerun/contention-20261028/final-handoff-v1/`;
`D` is `rerun/contention-20261028/final-metadata-v1/`. `Q` is the independent
`rerun/contention-20261028/offline-terminal-audit-v1/CONTENTION_REPLAY.json`, SHA256
`0cad03b7ab2edc7e0d62dbcc7b84f8fa0ea51325aba41eb354b5baae0c51a2d3`.
`Q` verifies the complete contention workload, all 34 actual original block04
attempt files, action separation and same-process resume. It completed at
07:15:58.632271 UTC with status COMPLETE, integrity PASS, publication_ready true,
no findings and no pending handoff reasons. `Q0` denotes the unchanged historical
`rerun/contention-20261028/offline-full-audit-v3/CONTENTION_REPLAY.json`, SHA256
`a34a8de887225edf58f9211a2e1f184feaa0eac5165e0dd0d03697f517e30ee5`, which
remains PARTIAL/publication_ready false because original attempt bytes were
not yet supplied to that audit. `F` denotes
`rerun/contention-20261028/figures-final-v1/`, whose final figure-manifest SHA256
is `5751b86ee46989fdfc7b0d25b1e2eac3a475a09c872143026cedfbd1e06000bd`.
None of these artifacts supplies an isolated primary observation or replaces
`R`, the formal profile log or the original 400-session completion gates.

`L` denotes the separate local operational observation directory
`evidence-research/20261028/h100-local-power-gap-checkpoint/`. Its selected
macOS power/watcher records, unchanged block06 session/control/receipt and
captured profile observation disclose local-power and backup pacing. These are
bounded records, not complete system logs, primary timing observations or final
400-session proof. Stable source hashes are listed in
[RESULTS_FINALIZATION.md](RESULTS_FINALIZATION.md); no future checkpoint
README/manifest hash is assumed.

| ID | Permitted statement | Evidence file and selector | Scope and remaining gate |
| --- | --- | --- | --- |
| C01 | The prospective design specifies ten blocks, 400 sessions and 26 primary contrasts. | `E/campaign.json`: `.blocks \| length`, `.sessions \| length`, `.primary_contrasts \| length`; expected `10`, `400`, `26`. | Planned sample. Completion must come from the final journal and validation, not these counts. |
| C02 | The protocol/source/schedule were archived before confirmation. | `E/PRESTART_MANIFEST.json`: `.phase`, `.archived_at_utc`, `.campaign_sha256`, `.source_commit`; `E/campaign.json.sha256`; `E/campaign.json`: `.protocol_sha256`, `.source.files_sha256`. | Pre-start identity, not proof of a completed experiment. Campaign SHA256 `69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0`; source `c84ef6a07341f350dae6636ab709c1b65fd894e0`. |
| C03 | Five configurations use CPython 3.11.13 with a common base build, including a separate Polars 2.0 control. | `E/campaign.json`: `.configs[] \| {id,engine,runtime}`; `.configs[].runtime.base_executable`, `.configs[].runtime.python_build`, `.configs[].runtime.distributions`. | GPU dependency inventories differ; this is not isolation of every library-version effect. Historical Python 3.10 controls remain separate. |
| C04 | The earlier recorded host exposes H100 80 GB HBM3, 26 vCPUs and approximately 221 GiB host RAM. | `E/readiness/baseline-spark_gpu/environment.json`: `.inventory[] \| {command,returncode,output}`; corresponding `sf1/spark_gpu/results.json`: `.gpu_inventory`. | Historical observed VM inventory only. The replacement's driver 580.105.08 and kernel 6.8.0-1046-nvidia require its own archived inventory; see R02. |
| C05 | Spark uses `local[26]`, a 96 GiB heap and fixed parity settings. | `E/campaign.json`: `.settings`; `E/readiness/baseline-spark_gpu/sf1/spark_gpu/results.json`: `.spark_conf`, `.spark_gpu_profile`. | Same host does not mean equal memory budgets: Polars has no equivalent host-memory cap. Check all formal effective settings before final reporting. |
| C06 | Configurations share physical Parquet input identities; nominal SF is distinguished from bytes. | `E/campaign.json`: `.datasets`; `E/oracles/baseline-sf{1,10,30,100,300}.json`: `.manifest_sha256`, `.input_files`. | Oracle input SHA fields are manifest declarations; campaign pre/post byte gates must verify actual bytes. File size does not establish query working-set size or out-of-core execution. |
| C07 | Readiness compared 116 successful SF1 outputs with independent SQL references. | `E/readiness/READINESS.json`: `.status`, `.successful_outputs_compared`, `.sessions`; expected `"PASS"`, `116`. | Ten smoke sessions with one warmup and one measured action per supported query. Excluded from prospective performance sample. |
| C08 | Polars GPU passes three of five adversarial diagnostics and rejects rank/window and DOTALL-regex cases in strict mode. | `E/readiness/fixtures/polars_gpu/FIXTURES.json`: `.queries`, `.supported_queries`, `.unsupported_queries`, `.polars_gpu.session_guard`. | Tiny diagnostic coverage only. Original Q13/window workload coverage is separately recorded in `E/readiness/complex-polars_gpu/sf1/polars_gpu/results.json`; each formal session must retain its own outcomes. No partial complex suite score. |
| C09 | A nonempty tiny-fixture anti exclusion returns IDs 1 and 3, and Spark's final GPU plan contains `LeftAnti`. | `E/readiness/fixtures/spark_gpu/FIXTURES.json`: `.nonempty_anti_semantics_passed`, `.gpu_left_anti_plan_observed`, `.queries.fx_semi_anti`; `E/readiness/fixtures/spark_gpu/plans/fx_semi_anti.txt`. | Observed tiny-input semantics and plan selection. The original correlated query's optimized-away anti branch supplies no nonempty anti-join performance result. No kernel-time or runtime-fraction claim. |
| C10 | The original Polars 1.44.2 window port has a null-ordering limitation outside the generated input domain. | `docs/H100_SEMANTIC_AUDIT.md`; `tests/test_research_oracle.py`: `test_original_window_port_null_ordering_limitation_is_preserved_and_explicit`; `E/INPUT_DOMAIN.json`: `.status`, `.scales[] \| {scale,null_prices,null_dates,null_order_keys,null_customer_keys}`. | Hand-checked regression preserves the mismatch. All listed generated-input null counts are zero; no claim of general SQL equivalence follows. |
| C11 | All 936 saved successful pilot outputs in 40 sessions match later independent SQL execution under the declared tolerances. | `E/PILOT_ORACLE_SUPPLEMENT.json`: `.status`, `.successful_outputs_compared`, `.sessions_compared`, `.mismatches`, `.comparisons`, `.sessions`; expected `"PASS"`, `936`, `40`, `0`. | Post-hoc correctness supplement only. Unsupported outputs are not successful observations; historical timings do not enter prospective inference. |
| C12 | Pilot SF300 scores and selected query wins are descriptive historical observations. | `reports/H100_STUDY.md`; `reports/h100-baseline-20261026/summary.csv`; `reports/h100-complex-20261026/summary.csv`; `reports/h100-polars2-baseline-control-20261026/suite_scores.csv`; `reports/h100-polars2-complex-control-20261026/suite_scores.csv`. | One session per configuration/condition, nested repeats, historical CPU control on Python 3.10. No confirmatory significance or same-Python result can be inferred. |
| C13 | Primary estimates use session query medians, complete-suite sums, paired log ratios, t intervals and one Holm family. | `E/campaign.json`: `.analysis_predeclarations`, `.primary_contrasts`; frozen `E/campaign.json.source/docs/H100_CONFIRMATORY_PROTOCOL.md`; frozen `E/campaign.json.source/research_analysis.py`. | Prespecified method. `N=10` refers to paired blocks. Pointwise intervals are not simultaneous; model assumptions and insufficient precision remain reportable. |
| C14 | The action endpoint and common-host-result endpoint have different conversion boundaries. | Frozen `E/campaign.json.source/worker.py`; each formal `results.json`: `.timing_scope`, `.host_result_timing_scope`, `.records[].seconds`, `.records[].host_result_seconds`. | A sum of medians is not once-through suite latency. Final analysis must expose direction changes between endpoints. |
| C15 | Preheat/residency, memory and I/O are observations with stated limits. | Each formal `schedule.json`: `.[] \| .cache_observations`; each `results.json`: `.records`; environment/host/GPU logs. | Snapshot residency does not guarantee in-query residency. Process-population limits, sampled peaks and mixed CPU/GPU plans must remain explicit. No bandwidth, transfer, spilling, or energy explanation without separate evidence. |
| C16 | A supported primary GPU advantage exists at a sampled recovery condition. | **Pending:** `P/validation.json` and `P/analysis/analysis.json`: `.status == "PASS"` on validation; `.primary[] \| {contrast_id,n_blocks,geometric_mean_speedup,ci95_pointwise,p_holm,supported_gpu_advantage}` on analysis. | Do not assert now. Requires all 400 fresh recovery sessions, exact frozen reused oracle hashes, sealed execution profile, valid full outputs, ten valid paired observations for that contrast, ratio above one and Holm-adjusted `p < 0.05`. |
| C17 | A recovery scale crossover is supported. | **Pending:** all same-family primary rows in `P/analysis/analysis.json`; `P/analysis/block_ratios.csv`. | Apply protocol bracket rule under the recorded recovery JVM profile; no exact unmeasured threshold. Lack of significance is not equivalence or a reversal. |
| C18 | The recovery study is a complete paper with reproducible evidence. | **Pending:** `P/REPLAY_MANIFEST.json`; `P/validation.json`; `P/complete-audit/AUDIT_COMPLETE.json`; `P/analysis/`; final raw `journal.json`; `P/figures/RENDER_MANIFEST.json`; paper build manifest and final manuscript review. | Audit all 26 primary rows, 40 coverage cells, 32 secondary pairs, deviations, 160 Spark driver logs, 24 profile receipts and matching profile hashes. Disclose all four cohort roles and post-incident selection; private source/evidence preservation remains in force. |

## Observed default-profile disposition and recovery pre-start evidence

| ID | Permitted statement | Evidence binding | Scope and remaining gate |
| --- | --- | --- | --- |
| D01 | Default-profile replacement stopped at 20:27:48 UTC with 88 scientifically validated sessions, 2,056 successful output comparisons, one failed and 311 unstarted sessions. | [Recovery protocol](../../docs/H100_GCLOCKER100_RECOVERY_PROTOCOL.md); unchanged incident journal/attempts in preserved off-host archive; `V/campaign.json`. | Observed incomplete cohort only; no default-profile timing enters recovery inference. |
| D02 | `block-03-complex-sf100-spark_cpu` failed in first `cx_q09` warmup before any successful output or measured observation, with exit 52, Java heap-space error and preceding GCLocker warnings. | Retained failed-session results/stdout/stderr/event log and scientific gate in incident archive; protocol incident record. | Report actual error; no failure-time heap/RSS or GC-recovery data establish cause or a false-OOM diagnosis. |
| D03 | Configured 96 GiB heap and nearest host resource sample are recorded. | Incident event-log BlockManager maximum on-heap 61,658,785,382 bytes; nearest host sample: available RAM 211,723,608,064 bytes, swap zero, free disk 2,713,801,965,568 bytes. | These corroborate the configured cap and observed host sample; host availability does not establish failure-time JVM occupancy or allocation feasibility. |
| D04 | All 3,349 original incident files survive off-host without journal/attempt edits. | Incident gzip bytes 220,656,964; SHA256 `79c99819d272cc1af947d8e200976a940cd63a96be580112e4a6c5900adee6ff`; extraction/transfer receipt and separate block 1/2 backups. | Retain actual private location/receipt in final binding. Archive integrity and a reported failure do not complete a primary study. |
| D05 | The separate full-complex-SF100 Spark CPU feasibility diagnostic passes. | `R/diagnostic-sf100-validation.json`: `.status`, `.query_seed`, `.successful_outputs_compared`, `.cleanup_records_checked`, `.records`, `.driver_flags`; `R/EXECUTION_PROFILE.json`: `.diagnostic`; retained driver log/results hashes. | Observed PASS at 21:36:41.579958 UTC: six queries, twelve passing warmup/measured outputs and cleanup records, seed 838471203, exit success and five actual driver flags. Diagnostic timings are excluded; feasibility does not establish causal mitigation or robust future execution. |
| D06 | Recovery repeated readiness passes before collection. | `R/readiness/READINESS.json`: `.status`, `.successful_outputs_compared`, `.sessions`, `.semantic_fixtures`, `.errors`; four actual Spark readiness logs; two pre-start `R/EXECUTION_PROFILE_GATES.jsonl` receipts. | PASS at 21:44:41.187289 UTC: ten smoke sessions, 116 successful output comparisons, five fixture configurations, no errors. Four Spark logs prove retry 100, G1, unlocked diagnostics, PrintFlagsFinal and 96 GiB maximum heap. This is preparation, not 400-session performance evidence. |

## Recovery binding and reporting gates

| ID | Recovery statement or output | Exact recovery evidence | Remaining gate |
| --- | --- | --- | --- |
| R01 | New cohort identity with unchanged C84 source and sealed profile. | `R/PRESTART_MANIFEST.json`: `.campaign_sha256`, `.source_commit`, `.files_sha256`; actual SHA256 of that file; `R/campaign.json`; `R/EXECUTION_PROFILE.json`; committed identity registry and pre-start private commit. | Exact recovery tuple above is registered; 346 canonical files and listed checksums checked. Neither incomplete earlier pair is eligible for final recovery results. All final performance gates remain pending. |
| R02 | Same replacement VM and verified recovery runtime. | `V/` archived inventory; repeated `R/readiness/baseline-spark_gpu/environment.json`; `R/campaign.json`: `.configs[].runtime`; sealed profile and formal-session audit. | Confirm H100 80 GB HBM3, 26 vCPUs, RAM, driver 580.105.08, kernel 6.8.0-1046-nvidia, unchanged Java 17.0.20.1/Python environments, and effective new Spark driver profile. |
| R03 | Same 1,654 physical replacement files and existing oracle bytes reused. | `V/manifests/`, `V/oracles/`; `R/campaign.json`: `.datasets`; `R/oracles/*.json`; profile-bound identities, raw journal byte gates and complete audit. | No recovery regeneration/mutation; preserve the actual v1 physical table. New recovery pre/post full SHA256 gates still required; old-host October 26 bytes remain distinct. |
| R04 | Repeated recovery readiness and unchanged independent references. | `R/readiness/READINESS.json`; fixtures; eight reused `R/oracles/*.json`; input-domain record; sealed diagnostic/readiness proof. | Recovery repeat and the twelve-output diagnostic pass; all five manifests and eight complete oracle files are byte-identical to `V`. Full-campaign correctness, post-final byte gates and all formal-session profile checks remain pending. |
| R05 | Full completed recovery campaign. | Raw final `journal.json`, block receipts and `EXECUTION_PROFILE_GATES.jsonl`; `P/complete-audit/AUDIT_COMPLETE.json`; `P/validation.json`. | 400 fresh sessions, 9,360 expected successful outputs, 60 expected translation rejections; all attempts/deviations, bracketing byte gates, 24 profile receipts and 160 effective Spark driver proofs. |
| R06 | Complete results tables. | `P/figures/primary_contrasts.csv` (26 rows), `P/figures/complete_case_scores.csv` (40 rows), `P/analysis/analysis.json`, `P/analysis/block_ratios.csv`. | Exact cross-checks to the full primary family and raw block observations; unsupported cells retain N/A and zero complete suite scores. |
| R07 | Secondary sensitivity and raw uncertainty. | `P/complete-audit/HOST_RESULT_SENSITIVITY.csv` (32 rows); `P/analysis/analysis.json`: `.secondary_direction_changes`; `P/analysis/session_query_medians.csv`; `P/analysis/block_ratios.csv`. | Report all available pairs, six unavailable complex Polars GPU pairs, direction changes and pointwise/model scope; no added primary claim. |
| R08 | Auditable publication figures. | `P/figures/CAPTIONS.md`; `P/figures/RENDER_MANIFEST.json`: `.synthetic == false`, `.campaign_sha256`, `.input_files_sha256`, `.output_files_sha256`; `P/REPLAY_MANIFEST.json`. | PASS real-data replay; exact estimate/caption agreement. Synthetic or partial renderings cannot enter paper or webpage. |
| R09 | Complete private raw evidence survives host shutdown. | Postcollection `BUNDLE_MANIFEST.json`, `CHUNKS.sha256`, `REASSEMBLE.md`, receipt, verified extracted raw files and replay manifests. | Final raw tree/off-host copies and reassembly/byte verification; bundle alone does not establish scientific conclusions. |
| R10 | Final paper and webpage report the same completed findings. | `B/BUILD_MANIFEST.json` for final build; exact manuscript/table/figure hashes; webpage snapshot and source mapping. | Apply [RESULTS_FINALIZATION.md](RESULTS_FINALIZATION.md), inspect every PDF page and webpage, confirm Daniel Ejiofor author metadata and actual availability terms. |
| R11 | The measured recovery uses exactly the sealed JVM profile. | `R/EXECUTION_PROFILE.json`; two canonical readiness receipts; final raw `EXECUTION_PROFILE_GATES.jsonl` with 24 required receipts; all 160 required formal Spark logs; complete audit `.execution_profile`; final paper/page profile SHA. | Two readiness receipts and four readiness driver logs are verified. The full final proof remains pending: `{path: "EXECUTION_PROFILE.json", sha256: registry hash, spark_driver_logs: 160, profile_gates: 24, status: "PASS"}`; match Java/runtime/environment hashes, chronology, max heap 103,079,215,104 bytes, retry 100, unlocked diagnostics and G1. No causal mitigation/reliability inference follows. |
| R12 | Recovery collection has started after its initial full-input gate. | [Current execution record](../../docs/H100_AUTONOMOUS_COMPLETION.md), registered pre-start archive, retained operational start/journal evidence; first session recorded at 21:47:44 UTC after full 1,654-file gate at 21:47:43.892782 UTC. | Collection milestone only. No partial-session timing, effect estimate or completed primary comparison is asserted. Final journal and post-session byte gate remain required. |
| R13 | A local low-power observation gap delayed the block06 off-host handoff while remote collection completed that block. | `L/POWER_EVENTS_SELECTED.txt` (SHA `7584152f1ab5d49fda74f58e89d16d991a508043f704334f04ee8be722393895`); `L/WATCHER_EVENTS_SELECTED.jsonl` (SHA `226018cb159052df874b55328b76235afd921be15183c4572ba8c6441ddc7ad2`); unchanged `L/LAST_BLOCK06_SESSION.json`, `L/block-06/CONTROL.json` and `L/block-06/OFFHOST_VERIFIED.json`; process/battery observations. | Low Power Sleep at 09:13:38 UTC/1% battery, wake on AC at 14:11:23 UTC; watcher last 09:13:25.168866, next 14:11:52.581715 UTC. Remote final block06 session succeeded 11:15:26.689677 UTC, backup created 11:15:30.821446 UTC, off-host ACK 14:12:51.939753 UTC, 10,641.118307 s later. Existing controller/sleep-prevention process survived and supervisor continued block07 without restart/replacement. This is distinct from the contention hold or a GPU/JVM failure diagnosis; no saved timing correction or performance inference. |
| R14 | The local power checkpoint observes six completed blocks without completing the study. | `L/WATCHER_EVENTS_SELECTED.jsonl`: preserved STATUS/ACK events; `L/EXECUTION_PROFILE_GATES_OBSERVED.jsonl`; immutable block06 raw journal and receipt. | At the checkpoint, 240 sessions succeeded and six blocks were preserved off-host; the 15 captured profile receipts end in block-07-before PASS. Original400, final 24 receipts and all primary findings remain pending. Added between-block temporal/cache history stays disclosed, with unchanged design, endpoints, order and 26-test family. |

## Separate contention and interlude evidence gates

| ID | Permitted statement or planned output | Evidence and selector | Scope and remaining gate |
| --- | --- | --- | --- |
| T01 | A genuine separate contention plan/source/instrumentation archive preceded execution. | `T/CONTENTION_PLAN.json`: `.experiment_id`, `.source_commit`, `.source_files_sha256`, `.tools_sha256`, `.reference_campaign_sha256`, `.reference_execution_profile_sha256`, `.synthetic`; `T/CONTENTION_PRESTART_MANIFEST.json`: `.files_sha256`, `.plan_sha256`; pre-start commit. | All 52 source identities match C84/reference; all 65 listed files match; `synthetic: false`. Its immutable `FROZEN_NOT_RUN` marker describes pre-start creation, not current execution or phase completion. |
| T02 | Fixed staged pairs and resources are declared. | `T/CONTENTION_PLAN.json`: `.pilot`, `.main`, `.main_gate`, `.settings`, `.configs`, `.spark_submit_opts`; [contention protocol](../../docs/H100_CONTENTION_PROTOCOL.md). | Pair A Spark GPU + Polars CPU 2.0; pair B Spark CPU + strict Polars GPU. Each requests 26 CPU threads on the same 26-vCPU host; one 96 GiB Spark JVM and one GPU path per pair. Four SF30 pilot waves; sixteen SF30/SF100 expansion waves with two repetitions only if all pilot gates pass. No equal-resource or isolated-runtime claim. |
| T03 | Within-contention common action intersections are verified. | `Q.counts.common_measured_pairs` and `Q.waves[].overlap`; `U_phase/<phase>/<wave>/VALIDATION.json` and participant UTC/monotonic action intervals; terminal `COMMON_QUERY_OVERLAP.csv`. | All 110 common measured pairs have positive intersection on both clocks: 22 pilot and 88 expansion. Pilot observed intersection range 0.208–2.361 s/max start skew 0.647 ms; expansion 0.228–8.664 s/max skew 2.340 ms. These ranges span different queries, not uncertainty intervals. Intended participant intersections are distinct from isolated-versus-contention separation. No new tests or primary-family additions. |
| T04 | Pair B complex has four common measured pairs and two unpaired CPU diagnostics. | `T/tools/contention_worker.py` measured-query bypass; phase/wave records, strict GPU warmup diagnostics and participant outputs. | CPU measured `cx_q13`/`cx_window` bypass rendezvous because strict Polars GPU has no measured counterpart. No complete GPU complex score; a six-query CPU sum mixes paired/unpaired conditions and cannot represent uniformly contended execution. |
| T05 | Timer boundaries are preserved while resource observations can include waits. | Frozen C84 `worker.py` identity; `T/tools/contention_worker.py`; `U_phase/<phase>/<wave>/barriers/events-*.jsonl`; `Q.waves[].barriers`; participant endpoints/counters, wave walls and telemetry. | All 250 barrier releases are verified. Rendezvous precedes the original timer; counter/memory samples may include untimed waits. Shared residency and 0.5 s/200 ms samples are observations, not guarantees or per-query causal power attribution. No isolated action-only resource cost is asserted. |
| T06 | Actual same-process scheduler resume and original wall accounting are verified. | `H/RESUMED.json`, `H/STATE.json`, `H/ORIGINAL_RUNNER_DRAINED.json`, cleanup/profile receipts; `D/PHASE_DRIVER.json`; `Q.handoff`; original protected attempt `session.json`/`run/schedule.json`/results. | PID 179144, creation time 1793160829.75, resumed at 05:32:20.044724 UTC; STATE COMPLETE_RESUMED, driver COMPLETE_OFFHOST. Preserved outer attempt wall 3,043.015 s/end 05:32:20.060856 UTC includes the hold; worker process wall 240.581 s/end 04:45:41.907405 UTC is distinct. Neither is query latency. |
| T07 | The planned contention workload and terminal provenance completed. | `Q.status`, `.integrity_status`, `.publication_ready`, `.findings`, `.disposition`, `.waves`, `.snapshots`, `.terminal_driver`; phase/final member artifacts and off-host ACKs. | Terminal COMPLETE/integrity PASS/publication_ready true/findings empty. Pilot PASS 4/4, main PASS 16/16; 460 passing SQL outputs (230 warmup/230 measured), ten expected strict rejections, zero unexpected errors, ten unpaired CPU diagnostics. Both raw phases OFFHOST_SHA256_VERIFIED. This completes the extension's provenance, not isolated400 or the full paper. |
| T08 | Final paper/page disclose the interlude and separate extension accurately. | Final §4.5–4.6/§5.7 tables; Q terminal disposition; actual original journal/attempt payload plus managed hold/drain/end/resume/idle/identity receipts. | Original action separation and same-process resume now verified; the original 400-session study remains pending. Interlude checks stay outside the formal 24-record profile log. A managed pause may alter host/cache conditions despite verified separation; no pooling, primary replacement or contention-only retirement. |
| T09 | Actual original isolated actions finished before contention actions began. | `Q.handoff`: status PASS, original_payload_verified true, original_attempt_files_expected/verified both 34, no_action_overlap true, pending_reasons empty; original result SHA `c33ddd3f46d02e4624083334490925170a20a44795a3f4d53a08556a72d8b4c9`, schedule SHA `d22c6ad51044c77aeedcbcfe611c26604b1b8c0cfba46111e8cebbb472217659`. | Last original host result 04:45:38.505969 UTC precedes first contention action 04:47:59.693693 UTC by 141.188 s. Last contention host result 05:28:37.930202 UTC precedes resume. Q0's historical false boolean meant then-unavailable proof, not a violation; preserve Q0 unchanged. Temporal separation does not prove unchanged host/cache conditions. |
| T10 | The expansion raw archive is preserved with a specific identity. | `Q.snapshots.main`; verified main receipt and `D/backup-receipts/main.json`: 1,561 files, 487,321,600 tar bytes, SHA256 `98be99cf74690d01053de9670669e629a166b02bd67fd51a994425006f792197`. | Actual off-host archive identity; preserve pilot separately. Archive identity alone is not a performance finding or retirement eligibility. |
| T11 | Final paper/page bind the same genuinely terminal contention role. | `B/BUILD_MANIFEST.json.contention_review`; final editorial JSON's same exact object and `contention_results_and_limits: true`; `CONTENTION_VERIFICATION.json` from explicit analysis-Python shared current-source replay. | Bind Q SHA above and actual disposition COMPLETE, fixed experiment/plan/pre-start, integrity PASS/publication_ready true and original payload/separation/resume. Never use historical Q0 as terminal. All original400/editorial gates remain; methods-only drafts stay unpublished. |
| T12 | Expansion within-pair shorter-action counts and query examples are descriptive observations. | Terminal `QUERY_ACTIONS_DESCRIPTIVE.csv`, SHA `b5fc666176782517020fa2dbf90dff6bdfa47c9b2c4ace8aff872a6ab0869335`; select `main-` waves, phase measured, paired_condition true; match wave/query/trial and compare seconds. | Pair A Polars CPU is shorter in all 24 baseline pairs and 21/24 complex pairs; the three Spark GPU exceptions are window (one SF30, both SF100). Pair B Polars GPU is shorter in all 40 supported common pairs; Q13/window remain unavailable. Report eight separate condition rows/two waves per condition, not independent query replicates, suite scores or primary inference. Different competitors prohibit a GPU-engine ranking; no causal isolated slowdown is estimated. |
| T13 | Final extension figures preserve individual measured observations and actual action timelines. | `F/FIGURE_MANIFEST.json`: final true, draft/synthetic false, publication_ready true, exact Q/input/renderer/output hashes; all six PNG/SVG/PDF stems and `F/CAPTIONS.md`. | Pilot/main kept separate; point plots contain common measured actions without averaging/CI/tests. Timelines retain actual UTC gaps and hatched unpaired CPU diagnostics; zero is first measured action per wave, not process startup. Final PDF/page still require full original400 and editorial/render review. |

## Historical, recovery and contention artifact locations

The **October 26** immutable plan stores the historical remote result root as
`/home/ubuntu/study-h100-research-evidence-20261026/campaign-results`.
Final `validation.json` and `analysis/` are expected under the same remote
evidence parent. These are expected output locations, not files claimed to be
complete or locally harvested in this draft. These locations must not be
reused as a later evidence identity.

For the aborted default-profile replacement, use `V` and the separately preserved
incident archive. For recovery, require its genuine new `campaign.json`,
`PRESTART_MANIFEST.json` and `EXECUTION_PROFILE.json` before resolving results.
The reporting paths are precisely the relative `P/` outputs above.
[RESULTS_FINALIZATION.md](RESULTS_FINALIZATION.md)
provides the ordered finalization procedure and insertion ledger.

Use `campaign.json` `.sessions[].output_root` and journal attempt records to
resolve each `session.json`, `schedule.json`, `results.json`, plan and log.
For local replay, copy the result tree without editing hashed JSON and supply
`--artifacts-root` to the validator/analyzer. The renderer documents its own
input/export hashes and produces `primary_contrasts.csv`,
`complete_case_scores.csv`, `CAPTIONS.md`, and `RENDER_MANIFEST.json`.

## Completion conditions for updating the manuscript

1. Recovery final journal covers all 400 fresh planned sessions with explicit attempts and
   deviations, no missing/duplicate actions, and successful pre/post input-byte
   gates. Retain unsupported-query evidence.
   The sealed execution profile, all 24 receipts and 160 Spark driver logs must
   match the registered recovery identity; earlier incomplete cohorts are excluded.
2. Exact frozen-oracle validation passes for every successful warmup and
   measured output; no replacement attempt enters analysis invisibly.
3. Frozen analysis completes the full 26-comparison family and exports raw
   block ratios, session medians, coverage, uncertainty and endpoint sensitivity.
4. Tables/figures replay from the harvested real archive; every plotted primary
   estimate agrees with its machine-readable contrast and block observations.
5. Replace pending abstract/results/conclusion text only after the above gates;
   complete the separate notice, attribution and public-release
   process before presenting the draft as publication ready.
6. Preserve and report the separate contention pilot/any gated expansion and
   actual managed interlude disposition. Verify action overlap from both clocks,
   identify unpaired CPU complex diagnostics, retain wait-inclusive metric scope,
   and account for the same-scheduler resume and outer-wall inflation. These
   observations never replace or enter the isolated 26-test family.
   Terminal Q now binds all 34 original block04 files/endpoints and verifies
   separation and same-process resume. Preserve historical Q0 PARTIAL unchanged;
   its no_action_overlap false denoted pending proof, not a violation. Final
   paper/page must bind Q and F exactly, with no causal slowdown or cross-pair
   GPU ranking inferred from the descriptive extension.
