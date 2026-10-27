# Claim and evidence matrix

Status: replacement manuscript preparation, 2026-10-27. Replacement performance
results remain pending. All paths below are repository-relative unless explicitly
marked remote. JSON selectors are machine-readable `jq` expressions.

`E` denotes the **lost October 26 cohort only**:
`evidence-research/20261026/h100-confirmatory-v1`. Existing C01–C15 evidence below
establishes that cohort's design, preparation and historical pilot context. It
does not establish replacement runtime, physical inputs, readiness or results.
Its surviving three core checkpoints (120 sessions; 2,808 successful output
comparisons) are incomplete and never pooled into replacement inference.

`R` is the exact October 27 replacement pre-start archive directory, **pending
its actual private archive**. `P` is the new successful portable replay output
for that replacement, also pending. `B` is the eventual final paper build output
directory. Resolve these to literal paths in the final paper binding; none is
an existing completed evidence file claimed by this draft.
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
| C16 | A supported primary GPU advantage exists at a sampled replacement condition. | **Pending:** `P/validation.json` and `P/analysis/analysis.json`: `.status == "PASS"` on validation; `.primary[] \| {contrast_id,n_blocks,geometric_mean_speedup,ci95_pointwise,p_holm,supported_gpu_advantage}` on analysis. | Do not assert now. Requires all 400 replacement sessions, exact frozen replacement oracle hashes, valid full outputs, ten valid paired observations for that contrast, ratio above one and Holm-adjusted `p < 0.05`. |
| C17 | A replacement scale crossover is supported. | **Pending:** all same-family primary rows in `P/analysis/analysis.json`; `P/analysis/block_ratios.csv`. | Apply protocol bracket rule; no exact unmeasured threshold. Lack of significance is not equivalence or a reversal. |
| C18 | The replacement study is a complete paper with reproducible evidence. | **Pending:** `P/REPLAY_MANIFEST.json`; `P/validation.json`; `P/complete-audit/AUDIT_COMPLETE.json`; `P/analysis/`; final raw `journal.json`; `P/figures/RENDER_MANIFEST.json`; paper build manifest and final manuscript review. | Audit all 26 primary rows, all 40 coverage cells, failures/deviations, 32-row endpoint sensitivity and captions. Completion and publication/release authorization are separate decisions; private development remains in force. |

## Replacement binding and reporting gates

| ID | Replacement statement or output | Exact replacement evidence | Remaining gate |
| --- | --- | --- | --- |
| R01 | New cohort identity with unchanged C84 source. | `R/PRESTART_MANIFEST.json`: `.campaign_sha256`, `.source_commit`; actual SHA256 of that file; `R/campaign.json`: `.campaign_id`, `.source.files_sha256`; committed `research_reporting/campaign_identities.json`. | Exact new campaign/pre-start pair registered and 52-file source verified before analysis; the old pair is ineligible for a complete replacement paper. |
| R02 | New host driver/kernel and runtime inventory. | `R/readiness/baseline-spark_gpu/environment.json`: `.inventory[] \| {command,returncode,output}`; `R/campaign.json`: `.configs[].runtime`; every formal session inventory in final complete audit. | Confirm H100 80 GB HBM3, 26 vCPUs, RAM bytes, driver 580.105.08, kernel 6.8.0-1046-nvidia and full software/JAR inventory from actual replacement archive. |
| R03 | Regenerated physical files shared within cohort. | `R/campaign.json`: `.datasets`; `R/oracles/*.json`: `.manifest_sha256`, `.input_files`; raw journal integrity gates; `P/complete-audit/AUDIT_COMPLETE.json`. | Five scales, new physical-byte totals and actual pre/post full SHA256 gates. Never copy October 26 byte totals into the replacement inventory. |
| R04 | Replacement readiness and independent references. | `R/readiness/READINESS.json`: `.status`, `.successful_outputs_compared`, `.sessions`; `R/readiness/fixtures/*/FIXTURES.json`; eight `R/oracles/*.json`; `R/INPUT_DOMAIN.json`. | New pre-start archive must contain complete PASS outputs and the tested replacement domain. Expected 116 readiness outputs is not an observed claim until checked. |
| R05 | Full completed replacement campaign. | Raw final `journal.json` plus block manifests/off-host receipts; `P/complete-audit/AUDIT_COMPLETE.json`: `.status`, `.counts`, `.findings`; `P/validation.json`. | 400 sessions, 9,360 successful outputs, 60 specific translation rejections under expected coverage; every attempt/deviation retained and byte gates temporally bracket sessions. |
| R06 | Complete results tables. | `P/figures/primary_contrasts.csv` (26 rows), `P/figures/complete_case_scores.csv` (40 rows), `P/analysis/analysis.json`, `P/analysis/block_ratios.csv`. | Exact cross-checks to the full primary family and raw block observations; unsupported cells retain N/A and zero complete suite scores. |
| R07 | Secondary sensitivity and raw uncertainty. | `P/complete-audit/HOST_RESULT_SENSITIVITY.csv` (32 rows); `P/analysis/analysis.json`: `.secondary_direction_changes`; `P/analysis/session_query_medians.csv`; `P/analysis/block_ratios.csv`. | Report all available pairs, six unavailable complex Polars GPU pairs, direction changes and pointwise/model scope; no added primary claim. |
| R08 | Auditable publication figures. | `P/figures/CAPTIONS.md`; `P/figures/RENDER_MANIFEST.json`: `.synthetic == false`, `.campaign_sha256`, `.input_files_sha256`, `.output_files_sha256`; `P/REPLAY_MANIFEST.json`. | PASS real-data replay; exact estimate/caption agreement. Synthetic or partial renderings cannot enter paper or webpage. |
| R09 | Complete private raw evidence survives host shutdown. | Postcollection `BUNDLE_MANIFEST.json`, `CHUNKS.sha256`, `REASSEMBLE.md`, receipt, verified extracted raw files and replay manifests. | Final raw tree/off-host copies and reassembly/byte verification; bundle alone does not establish scientific conclusions. |
| R10 | Final paper and webpage report the same completed findings. | `B/BUILD_MANIFEST.json` for final build; exact manuscript/table/figure hashes; webpage snapshot and source mapping. | Apply [RESULTS_FINALIZATION.md](RESULTS_FINALIZATION.md), inspect every PDF page and webpage, confirm Daniel Ejiofor author metadata and actual availability terms. |

## Historical and replacement artifact locations

The **October 26** immutable plan stores the historical remote result root as
`/home/ubuntu/study-h100-research-evidence-20261026/campaign-results`.
Final `validation.json` and `analysis/` are expected under the same remote
evidence parent. These are expected output locations, not files claimed to be
complete or locally harvested in this draft. These locations must not be
reused as the replacement evidence identity.

For the replacement, read its new `campaign.json` and `PRESTART_MANIFEST.json`
to resolve archive and raw-result roots. The reporting paths are precisely the
relative `P/` outputs above. [RESULTS_FINALIZATION.md](RESULTS_FINALIZATION.md)
provides the ordered finalization procedure and insertion ledger.

Use `campaign.json` `.sessions[].output_root` and journal attempt records to
resolve each `session.json`, `schedule.json`, `results.json`, plan and log.
For local replay, copy the result tree without editing hashed JSON and supply
`--artifacts-root` to the validator/analyzer. The renderer documents its own
input/export hashes and produces `primary_contrasts.csv`,
`complete_case_scores.csv`, `CAPTIONS.md`, and `RENDER_MANIFEST.json`.

## Completion conditions for updating the manuscript

1. Final journal covers all 400 planned sessions with explicit attempts and
   deviations, no missing/duplicate actions, and successful pre/post input-byte
   gates. Retain unsupported-query evidence.
2. Exact frozen-oracle validation passes for every successful warmup and
   measured output; no replacement attempt enters analysis invisibly.
3. Frozen analysis completes the full 26-comparison family and exports raw
   block ratios, session medians, coverage, uncertainty and endpoint sensitivity.
4. Tables/figures replay from the harvested real archive; every plotted primary
   estimate agrees with its machine-readable contrast and block observations.
5. Replace pending abstract/results/conclusion text only after the above gates;
   complete the separate notice, attribution and public-release
   process before presenting the draft as publication ready.
