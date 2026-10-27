# Claim and evidence matrix

Status: recovery collection in progress, 2026-10-27. The separate feasibility
diagnostic and repeated readiness pass; primary performance results remain pending.
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

## Historical and recovery artifact locations

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
