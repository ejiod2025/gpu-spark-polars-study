# Recovery paper: result insertion and completion ledger

Status: recovery collection in progress, 2026-10-27. This is an editorial workflow, not new
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
  --audit-complete /absolute/recovery-replay/complete-audit/AUDIT_COMPLETE.json \
  --output /absolute/recovery-raw-bundle
```

Keep all parts, manifests and extraction/replay receipts off-host. A successful
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
`references_sha256`, author `Daniel Ejiofor`, and all five review booleans true:
`all_26_primary_rows`, `all_40_coverage_cells`, `all_32_secondary_pairs`,
`deviations_and_limits`, and `results_and_captions`. This is an explicit editorial
attestation, not a substitute for scientific replay. The builder checks it
against the actual source and genuine recovery replay. The final
`BUILD_MANIFEST.json.execution_profile_sha256` and public webpage provenance
must match the registered sealed profile and complete audit. Earlier campaign
hashes cannot satisfy this gate. The editorial review JSON's exact campaign/
source hashes bind it to that verified replay; it has no separate required
profile-hash field. Profile identity is explicit in the build/audit/page
manifests.

```sh
python publication/h100-confirmatory/paper/build_paper.py \
  --replay /absolute/recovery-replay \
  --final-review /absolute/FINAL_PAPER_REVIEW.json \
  --output /absolute/new-final-paper-build
```

Inspect every rendered PDF page at readable resolution, including tables,
captions, long identifiers and references. Verify PDF author/title, no clipped
content or draft markers in the final, correct local HTML figures/links, and
page-by-page text consistency. Preserve `BUILD_MANIFEST.json` beside the PDF.
Compare the webpage's numbers, uncertainty, coverage and claims to the paper,
and retain the actual webpage snapshot/source hash. Only the completed full
paper, completed public webpage and safely harvested private source/evidence satisfy the intended
deliverables; this ledger and a draft preview do not.
