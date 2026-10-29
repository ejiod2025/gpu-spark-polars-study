# Claim and evidence matrix

Status: complete genuine recovery replay and manuscript result insertion,
2026-10-28 UTC. Separate final editorial/PDF/web publication and preservation
checks are not asserted by this matrix.
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
`P` is the actual complete nonsynthetic replay directory
`rerun/gclocker100-20261027/replay-v1`. Its manifest SHA256 is
`b59450d0731180b5b9cc642c5174f872ab460ab585445b87230175a07c48655c`.
The complete audit SHA256 is
`cb03c639c0b428f50f07a142d1201e8a3c39ad82ea21a2192db95d4ebfc5585d`.
`H_R` is `rerun/gclocker100-20261027/reconstruction-v1/harvest`; the separate
reconstruction manifest SHA256 is
`6ef171eaaebbda72bfd928b2b12c468a19529e15ec86224d3921c1fcad281b2d`.
`B` denotes the subsequent genuine final paper build, which this matrix does
not create or attest. Neither earlier campaign can satisfy the recovery primary gate.
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
`Q` verifies the complete contention workload, all 34 actual original block 04
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
macOS power/watcher records, unchanged block 06 session/control/receipt and
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
| C16 | All 26 primary tests reject after Holm adjustment: fifteen GPU-favoring and eleven reference-favoring. | `P/analysis/analysis.json`: `.primary[]` complete ten-block estimates, pointwise intervals, raw/Holm p values and both supported-direction fields; `P/figures/primary_contrasts.csv`; Tables 2a-2d. | All 400 fresh sessions and exact frozen references/profile audited; maximum adjusted p=5.544658625465578e-6; no zero observed log-ratio variance. Pointwise intervals are not simultaneous. No query or extra-family claim follows. |
| C17 | Baseline sampled-scale brackets are SF1 to SF10 for Spark CPU/GPU and both CPU Polars/Polars GPU comparisons. | All same-family rows in `P/analysis/analysis.json`; `P/analysis/block_ratios.csv`; §5.4. | CPU supported at SF1, GPU at SF10 and all larger sampled points. GPU Spark has no complete-suite advantage over CPU Polars 2.0 at any sampled condition. Complex Spark CPU/GPU already favors GPU at lowest sampled SF30. No exact unmeasured threshold or general hardware effect. |
| C18 | The manuscript now reports the complete genuine recovery results with traceable evidence. | `P/REPLAY_MANIFEST.json`; `P/validation.json`; `P/complete-audit/AUDIT_COMPLETE.json`; `P/analysis/`; `H_R/campaign-results/journal.json`; `P/figures/RENDER_MANIFEST.json`; full manuscript tables/appendices. | All 26 primary rows, 40 score/coverage cells, 32 secondary pairs, 260 primary ratios, 160 Spark logs and 24 profile receipts are represented. Final PDF/editorial/visual review, public deployment and independent private preservation remain separate delivery gates; no final build/review receipt is created here. |

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
| R01 | Registered complete cohort with unchanged C84 source and sealed profile. | `R/PRESTART_MANIFEST.json`, `R/campaign.json`, `R/EXECUTION_PROFILE.json`; actual P registered identity/source map and complete audit. | Exact registered tuple above; 346 canonical files/345 listed checksums, 52 C84 files and all complete replay outputs verified. Earlier incomplete cohorts remain excluded. |
| R02 | Recorded replacement runtime and fixed profile are verified for the recovery. | `V/` inventory; repeated `R/readiness/baseline-spark_gpu/environment.json`; `R/campaign.json` runtime pins; `P/complete-audit/AUDIT_COMPLETE.json` configuration/profile proofs. | H100 80 GB HBM3,26 vCPUs,RAM237490843648 bytes,driver 580.105.08,kernel 6.8.0-1046-nvidia,Java 17.0.20.1 and sealed effective Spark profile. This is configuration evidence, not isolation of hardware effects. |
| R03 | The same 1,654 replacement physical inputs and existing oracle bytes were reused unchanged. | `V/manifests/`, `V/oracles/`; R dataset/profile identities; `H_R/campaign-results/journal.json.input_integrity_gates`; complete audit. | No regeneration or mutation. Actual initial full-byte gate 21:47:43.892782 Oct 27 and final gate 23:33:18.564788 Oct 28 bracket collection; preserve actual physical table. No lost-host byte-identity or working-set claim. |
| R04 | Repeated readiness and all full-campaign successful outputs pass their independent references. | `R/readiness/READINESS.json`, fixtures and reused oracles; `P/validation.json`; complete audit; formal original records/diagnostics. | 116 readiness and 12 diagnostic outputs remain excluded from inference. Actual recovery 9,360 successes(2,340 warm/7,020 measured) and 60 specific strict rejections are independently audited. |
| R05 | Recovery completed all 400 fresh single-attempt sessions. | `H_R/campaign-results/journal.json`; thirteen verified transport snapshots/reconstruction lineage; `P/complete-audit/AUDIT_COMPLETE.json`; `P/validation.json`. | Ten blocks, all 400 succeeded once/returncode 0; 9,360 outputs, 60 expected rejections, 3,360 successful+60 failed complex cleanups, 24 profile receipts and 160 Spark flag proofs; final post-action byte gate and supervisor exit 0. |
| R06 | The paper includes all complete-suite scores and the full primary family. | `P/figures/primary_contrasts.csv`26 rows; `P/figures/complete_case_scores.csv`40 cells; analysis decisions; Table 1/Tables 2a-2d and AppendixC. | 37 numeric score cells and 3 unavailable Polars GPU complex cells; N/A has 0/10 complete blocks and 4/6 coverage. All 260 primary block ratios retained, ten per contrast; no partial suite substitution. |
| R07 | All available secondary endpoint pairs preserve point-estimate direction. | `P/complete-audit/HOST_RESULT_SENSITIVITY.csv`32 rows; analysis `.secondary_direction_changes == []`; AppendixB. | 26 complete ten-block pairs and 6 unavailable complex Polars GPU pairs. Both endpoint estimates/pointwise intervals retained. No added tests or familywise secondary claims. |
| R08 | Four primary publication figures derive from complete genuine analysis. | `P/figures/CAPTIONS.md`, `P/figures/RENDER_MANIFEST.json`, all PNG/SVG/PDF hashes; matching `P/REPLAY_MANIFEST.json`; manuscript four image references. | Exact source/input/export identities, synthetic false and genuine complete replay PASS. Estimate/caption equality is required in the separate final visual review; no synthetic exports enter the manuscript. |
| R09 | The complete raw harvest and original transport evidence are preserved off-host. | Thirteen original transport controls/tars/extracted trees/verification receipts, reconstruction lineage/manifest and `H_R`; final/completion exit/journal evidence. | Actual transport/raw equivalence verified. Durable private packaging/catalog/retrieval and final publication preservation still require their root-owned receipts; this scientific result does not assert those later steps. |
| R10 | The final paper/page must bind the same completed findings and source. | Subsequent actual `B/BUILD_MANIFEST.json`, final manuscript/reference/table/figure hashes, editorial review and public-page deployment/source checks. | Manuscript insertion is complete; final PDF, all-page visual review, webpage QA/deployment and genuine editorial/publication receipts are not created by this matrix. |
| R11 | The completed recovery uses the sealed JVM profile in all formal sessions. | `R/EXECUTION_PROFILE.json`; pre-start receipts; `H_R/EXECUTION_PROFILE_GATES.jsonl`; all formal logs; P complete audit `.execution_profile`. | Actual proof PASS: path EXECUTION_PROFILE.json, registered SHA, spark_driver_logs 160, profile_gates 24. Identical Java/runtime/environment identities and chronology; max heap 103079215104, retry 100, unlocked diagnostics, PrintFlagsFinal and G1. No causal mitigation/reliability inference. |
| R12 | The complete collection interval and post-final chronology are recorded. | Final journal and original session attempts; `H_R/confirm-driver.exit` and `.ended-utc`; P complete audit. | Attempt bounds Oct 27 21:47:44.042050 to Oct 28 23:26:10.952925 UTC; full final byte gate 23:33:18.564788, journal completion 23:33:18.565015, supervisor exit 0/end 23:42:11. Saved timings include disclosed pacing; none corrected. |
| R13 | A local low-power observation gap delayed the block 06 off-host handoff while remote collection completed that block. | `L/POWER_EVENTS_SELECTED.txt` (SHA `7584152f1ab5d49fda74f58e89d16d991a508043f704334f04ee8be722393895`); `L/WATCHER_EVENTS_SELECTED.jsonl` (SHA `226018cb159052df874b55328b76235afd921be15183c4572ba8c6441ddc7ad2`); unchanged `L/LAST_BLOCK06_SESSION.json`, `L/block-06/CONTROL.json` and `L/block-06/OFFHOST_VERIFIED.json`; process/battery observations. | Low Power Sleep at 09:13:38 UTC/1% battery, wake on AC at 14:11:23 UTC; watcher last 09:13:25.168866, next 14:11:52.581715 UTC. Remote final block 06 session succeeded 11:15:26.689677 UTC, backup created 11:15:30.821446 UTC, off-host ACK 14:12:51.939753 UTC, 10,641.118307 s later. Existing controller/sleep-prevention process survived and supervisor continued block 07 without restart/replacement. This is distinct from the contention hold or a GPU/JVM failure diagnosis; no saved timing correction or performance inference. |
| R14 | The local power checkpoint is a bounded historical progress/pacing observation. | `L/WATCHER_EVENTS_SELECTED.jsonl`; `L/EXECUTION_PROFILE_GATES_OBSERVED.jsonl`; original block 06 raw journal/receipt. | It observed 240 successful sessions/six blocks and 15 profile receipts ending block-07-before PASS. Those historical counts are not final; P independently proves 400 sessions/24 receipts. Added temporal/cache history stays disclosed, with unchanged endpoints/order/family. |

## Separate contention and interlude evidence gates

| ID | Permitted statement or planned output | Evidence and selector | Scope and remaining gate |
| --- | --- | --- | --- |
| T01 | A genuine separate contention plan/source/instrumentation archive preceded execution. | `T/CONTENTION_PLAN.json`: `.experiment_id`, `.source_commit`, `.source_files_sha256`, `.tools_sha256`, `.reference_campaign_sha256`, `.reference_execution_profile_sha256`, `.synthetic`; `T/CONTENTION_PRESTART_MANIFEST.json`: `.files_sha256`, `.plan_sha256`; pre-start commit. | All 52 source identities match C84/reference; all 65 listed files match; `synthetic: false`. Its immutable `FROZEN_NOT_RUN` marker describes pre-start creation, not current execution or phase completion. |
| T02 | Fixed staged pairs and resources are declared. | `T/CONTENTION_PLAN.json`: `.pilot`, `.main`, `.main_gate`, `.settings`, `.configs`, `.spark_submit_opts`; [contention protocol](../../docs/H100_CONTENTION_PROTOCOL.md). | Pair A Spark GPU + Polars CPU 2.0; pair B Spark CPU + strict Polars GPU. Each requests 26 CPU threads on the same 26-vCPU host; one 96 GiB Spark JVM and one GPU path per pair. Four SF30 pilot waves; sixteen SF30/SF100 expansion waves with two repetitions only if all pilot gates pass. No equal-resource or isolated-runtime claim. |
| T03 | Within-contention common action intersections are verified. | `Q.counts.common_measured_pairs` and `Q.waves[].overlap`; `U_phase/<phase>/<wave>/VALIDATION.json` and participant UTC/monotonic action intervals; terminal `COMMON_QUERY_OVERLAP.csv`. | All 110 common measured pairs have positive intersection on both clocks: 22 pilot and 88 expansion. Pilot observed intersection range 0.208–2.361 s/max start skew 0.647 ms; expansion 0.228–8.664 s/max skew 2.340 ms. These ranges span different queries, not uncertainty intervals. Intended participant intersections are distinct from isolated-versus-contention separation. No new tests or primary-family additions. |
| T04 | Pair B complex has four common measured pairs and two unpaired CPU diagnostics. | `T/tools/contention_worker.py` measured-query bypass; phase/wave records, strict GPU warmup diagnostics and participant outputs. | CPU measured `cx_q13`/`cx_window` bypass rendezvous because strict Polars GPU has no measured counterpart. No complete GPU complex score; a six-query CPU sum mixes paired/unpaired conditions and cannot represent uniformly contended execution. |
| T05 | Timer boundaries are preserved while resource observations can include waits. | Frozen C84 `worker.py` identity; `T/tools/contention_worker.py`; `U_phase/<phase>/<wave>/barriers/events-*.jsonl`; `Q.waves[].barriers`; participant endpoints/counters, wave walls and telemetry. | All 250 barrier releases are verified. Rendezvous precedes the original timer; counter/memory samples may include untimed waits. Shared residency and 0.5 s/200 ms samples are observations, not guarantees or per-query causal power attribution. No isolated action-only resource cost is asserted. |
| T06 | Actual same-process scheduler resume and original wall accounting are verified. | `H/RESUMED.json`, `H/STATE.json`, `H/ORIGINAL_RUNNER_DRAINED.json`, cleanup/profile receipts; `D/PHASE_DRIVER.json`; `Q.handoff`; original protected attempt `session.json`/`run/schedule.json`/results. | PID 179144, creation time 1793160829.75, resumed at 05:32:20.044724 UTC; STATE COMPLETE_RESUMED, driver COMPLETE_OFFHOST. Preserved outer attempt wall 3,043.015 s/end 05:32:20.060856 UTC includes the hold; worker process wall 240.581 s/end 04:45:41.907405 UTC is distinct. Neither is query latency. |
| T07 | The planned contention workload and terminal provenance completed. | `Q.status`, `.integrity_status`, `.publication_ready`, `.findings`, `.disposition`, `.waves`, `.snapshots`, `.terminal_driver`; phase/final member artifacts and off-host ACKs. | Terminal COMPLETE/integrity PASS/publication_ready true/findings empty. Pilot PASS 4/4, main PASS 16/16; 460 passing SQL outputs (230 warmup/230 measured), ten expected strict rejections, zero unexpected errors, ten unpaired CPU diagnostics. Both raw phases OFFHOST_SHA256_VERIFIED. This completes the extension's provenance, not isolated 400 or the full paper. |
| T08 | Paper/page disclose the separate interlude and extension without changing primary sampling. | §4.5-4.6/§5.7-5.8; Q terminal disposition; original journal/attempt plus hold/drain/end/resume/idle/identity receipts; complete primary P. | Action separation and same-process resume verified; primary 400 separately complete. Interlude checks stay outside formal 24 profile log. Possible host/cache carryover remains; no pooling, primary replacement or contention-only retirement. |
| T09 | Actual original isolated actions finished before contention actions began. | `Q.handoff`: status PASS, original_payload_verified true, original_attempt_files_expected/verified both 34, no_action_overlap true, pending_reasons empty; original result SHA `c33ddd3f46d02e4624083334490925170a20a44795a3f4d53a08556a72d8b4c9`, schedule SHA `d22c6ad51044c77aeedcbcfe611c26604b1b8c0cfba46111e8cebbb472217659`. | Last original host result 04:45:38.505969 UTC precedes first contention action 04:47:59.693693 UTC by 141.188 s. Last contention host result 05:28:37.930202 UTC precedes resume. Q0's historical false boolean meant then-unavailable proof, not a violation; preserve Q0 unchanged. Temporal separation does not prove unchanged host/cache conditions. |
| T10 | The expansion raw archive is preserved with a specific identity. | `Q.snapshots.main`; verified main receipt and `D/backup-receipts/main.json`: 1,561 files, 487,321,600 tar bytes, SHA256 `98be99cf74690d01053de9670669e629a166b02bd67fd51a994425006f792197`. | Actual off-host archive identity; preserve pilot separately. Archive identity alone is not a performance finding or retirement eligibility. |
| T11 | Final paper/page bind the same genuinely terminal contention role. | `B/BUILD_MANIFEST.json.contention_review`; final editorial JSON's same exact object and `contention_results_and_limits: true`; `CONTENTION_VERIFICATION.json` from explicit analysis-Python shared current-source replay. | Bind Q SHA above and actual disposition COMPLETE, fixed experiment/plan/pre-start, integrity PASS/publication_ready true and original payload/separation/resume. Never use historical Q0 as terminal. All original 400/editorial gates remain; methods-only drafts stay unpublished. |
| T12 | Expansion within-pair shorter-action counts and query examples are descriptive observations. | Terminal `QUERY_ACTIONS_DESCRIPTIVE.csv`, SHA `b5fc666176782517020fa2dbf90dff6bdfa47c9b2c4ace8aff872a6ab0869335`; select `main-` waves, phase measured, paired_condition true; match wave/query/trial and compare seconds. | Pair A Polars CPU is shorter in all 24 baseline pairs and 21/24 complex pairs; the three Spark GPU exceptions are window (one SF30, both SF100). Pair B Polars GPU is shorter in all 40 supported common pairs; Q13/window remain unavailable. Report eight separate condition rows/two waves per condition, not independent query replicates, suite scores or primary inference. Different competitors prohibit a GPU-engine ranking; no causal isolated slowdown is estimated. |
| T13 | Final extension figures preserve individual actions and actual timelines. | `F/FIGURE_MANIFEST.json`, synthetic false/final true/publication_ready true, exact Q/source/input/export hashes; all six PNG/SVG/PDF stems and CAPTIONS.md; manuscript six image references. | Pilot/main separate; no averaging/CI/tests. Timelines retain actual UTC gaps and hatched unpaired CPU diagnostics. Final PDF/page still require independent editorial and visual review. |

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

## Completed scientific checks and remaining publication work

The genuine P replay now proves all 400 fresh sessions, exact frozen-oracle
validation, the complete 26-test family, all raw ratios and all required
profile/byte/cleanup evidence. The manuscript has actual full tables and ten
manifest-bound image references, with all cohorts, post-incident profile choice,
strict N/A cases, contention separation and operational pacing disclosed.

This is not a final editorial/PDF/deployment attestation. Independent numeric
and claim review, exact caption/image/table visual inspection, final source
hashes, public webpage comparison/deployment and private preservation/retrieval
remain their own explicit checks. Only genuine completed receipts may satisfy
the root-owned publication and retirement workflow. No scientific artifact,
query, method, sample size or primary-family rule changes in this matrix.
