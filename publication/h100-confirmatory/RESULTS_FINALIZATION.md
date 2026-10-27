# Replacement paper: result insertion and completion ledger

Status: preparation only, 2026-10-27. This is an editorial workflow, not new
experimental source. It changes no C84 file, query, sample size, endpoint or
statistical rule. It does not authorize publication or host shutdown.

The author is **Daniel Ejiofor**. Omit affiliation unless the author supplies
one. The paper and webpage must both describe the completed October 27
replacement cohort; neither may pool the lost cohort's 120 surviving sessions
or its 2,808 output comparisons into the replacement's 400 sessions.

## 1. Bind the actual replacement before inserting a result

Record the following exact values from files, never from a running status
message. The October 26 campaign SHA beginning `69669dfb` is historical and
cannot be used for the replacement paper.

| Binding | Source | Required check |
| --- | --- | --- |
| Replacement campaign ID and SHA256 | New `campaign.json` and `PRESTART_MANIFEST.json` | Registered new campaign/pre-start pair; unchanged C84 source identity |
| Pre-start manifest SHA256 | SHA256 of the archived `PRESTART_MANIFEST.json` bytes | Matches the committed campaign identity registry |
| 52-file source and protocol | `campaign.json.source/`, pre-start inventory | C84 commit `c84ef6a07341f350dae6636ab709c1b65fd894e0`; complete archive verification |
| New physical inventory | Five replacement dataset manifests and plan `.datasets` | Five nominal scales; actual compressed bytes, file count and full hashes; no old byte totals |
| Host and runtime | Replacement readiness inventory and formal-session audit | H100 80 GB HBM3; 26 vCPUs; 237,490,843,648 RAM bytes; driver 580.105.08; kernel 6.8.0-1046-nvidia; exact remaining versions/JAR hash |
| Fresh readiness, fixtures and oracles | New `readiness/`, `INPUT_DOMAIN.json`, all eight `oracles/*.json` | Complete pre-start PASS files, exact plan-declared hashes and tested input domain |
| Raw evidence root | New plan `.output_root`, final journal and harvested tree | Exact bytes preserved; paths remapped externally, never rewritten inside JSON |
| Final journal and backup receipts | Final raw tree; all ten full block manifests/receipts | Every 40-session block preserved off-host; actual collection interval and pacing |

## 2. Run the existing complete evidence workflow locally

Use an analysis environment outside the H100 measurement process. Set the
paths below to actual immutable directories. All output directories must be
new. The replay does not need a GPU, JVM or generated Parquet files. Do not
pass `--allow-synthetic` for study evidence.

```sh
python research_reporting/replay.py \
  --archive /absolute/replacement-prestart-archive \
  --artifacts-root /absolute/replacement-harvest/campaign-results \
  --output /absolute/replacement-replay
```

Before proceeding, require real `REPLAY_MANIFEST.json` with `status: PASS`,
`phase: complete`, `synthetic: false`, the exact replacement campaign/pre-start
hashes and the unchanged C84 source. Preserve `validation.raw.json`, derived
`validation.json`, `PATH_NORMALIZATION.json` and all subprocess logs. The
adapter changes only validated Spark plan source paths in derived validation.

Check `complete-audit/AUDIT_COMPLETE.json` for `status: PASS`, no findings,
matching campaign/journal identity, and the full action/configuration/cache/
integrity accounting. Its full-byte gates must precede the first session and
follow the last session before final completion. Expected aggregate counts
under the frozen coverage pattern are 400 sessions, 9,360 successful outputs,
60 expected unsupported warmups, 3,360 successful complex cleanup records and
60 failed-action cleanup records. Report actual checked outcomes; expectations
alone are not evidence.

Package and verify the complete private raw tree after collection ends, using
the same actual complete audit. Preserve original remote final validation and
analysis in that raw tree; portable replay outputs remain separate.

```sh
python research_reporting/package_evidence.py \
  --evidence /absolute/replacement-harvest \
  --audit-complete /absolute/replacement-replay/complete-audit/AUDIT_COMPLETE.json \
  --output /absolute/replacement-raw-bundle
```

Keep all parts, manifests and extraction/replay receipts off-host. A successful
bundle is an integrity result, not an interpretation or publication decision.

## 3. Insert the full paper results, with these exact contracts

`P` below means the actual new successful replay directory. All tables use the
same replacement campaign hash. Values are copied from the predefined analysis;
the paper workflow must not recompute a different statistic.

| Paper item | Exact artifact and fields | Completeness/cross-check | Placement and interpretation |
| --- | --- | --- | --- |
| Campaign disposition and integrity table | Raw final `journal.json`; `P/validation.json`; `P/complete-audit/AUDIT_COMPLETE.json`; ten block manifests/receipts | 400 unique planned identities; all attempts and deviations; chronological byte gates; counts and hashes agree | §5.1; report lost campaign and pause/receipt amendment explicitly |
| Replacement input table | New plan `.datasets` and full input manifests | Five scales, exact physical Parquet byte totals and manifest/file hashes | §3.2; bytes describe stored files, not scan volume, working set or out-of-core capacity |
| Runtime/configuration table | New `.configs[].runtime` plus complete audit and actual session settings | All five configurations; effective common Python/build, Spark/JAR and GPU pins | §3.1; distinct dependencies and unequal memory policies remain disclosed |
| Readiness and semantic coverage | New `readiness/READINESS.json`, fixtures, `INPUT_DOMAIN.json`; formal audit | Replacement facts only; preserve original window-domain limitation and strict failures | §5.2; small diagnostics do not measure performance or general equivalence |
| Complete-suite score and coverage table | `P/figures/complete_case_scores.csv`: `suite`, `scale`, `config_id`, `complete_blocks`, `query_coverage`, `geometric_mean_score_seconds` | Exactly 40 cells; 37 complete score cells and three unavailable complex Polars GPU cells under expected coverage | §5.2–5.3; descriptive geometric means of session sums, not once-through latency |
| All primary contrasts | `P/analysis/analysis.json`: `.primary[]`; `P/figures/primary_contrasts.csv` | Exactly 26 rows, each ten paired blocks; all four families and all prescribed conditions; raw/adjusted p values preserved | §5.3 and complete supplement; show estimate, pointwise CI, `p_raw`, `p_holm` and supported decision |
| Baseline primary figure | `P/figures/suite_ratios_baseline.*`; caption from `P/figures/CAPTIONS.md` | Renderer binding to analysis and 260 primary block ratios; exact export hash checked in manifest | §5.3; ten block points, pointwise t intervals, full Holm family in caption |
| Complex primary figure | `P/figures/suite_ratios_complex.*`; same caption source | Exact export hash checked; no complex Polars GPU complete score | §5.3; preserve unavailable path rather than four-query surrogate |
| Query heterogeneity figure/table | `P/figures/query_ratios_action.*`; `.exploratory_queries[]` in analysis | Every supported/unsupported original query retained; ten block query observations where available | §5.4; descriptive, with no query-level superiority claim |
| Block/order diagnostics | `P/analysis/block_ratios.csv` filtered by `family == primary_suite`, `endpoint == seconds`; `.primary[].sd_log_ratio` | Exactly 260 primary block rows; ten per contrast; retain chronology and configuration positions from session medians | §5.6/supplement; show temporal spread; no post-hoc exclusion or new significance claim |
| Endpoint sensitivity | `P/complete-audit/HOST_RESULT_SENSITIVITY.csv`; `.secondary_direction_changes` | Exactly 32 pairs; six complex Polars GPU N/A pairs; direction-change flags agree | §5.5/full supplement; secondary ratios and pointwise CIs are descriptive |
| Residency and host state | Every session schedule's three cache observations; retained environment/host/GPU telemetry; complete audit | All 400 session inventories; note available sampling/counter/process-population limits | §5.6; snapshots and sampled peaks are not guarantees or causal explanations |
| Figure provenance | `P/figures/RENDER_MANIFEST.json`; `P/REPLAY_MANIFEST.json` | Real-data `synthetic: false`; exact source/input/export hashes and same campaign | §8/supplement; figures copied verbatim, including caption scope |
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
confined to their labeled appendix, and keep the lost-campaign disposition in
methods/results. Add no transfer, spill, compute or energy mechanism without
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
attestation, not a substitute for the scientific replay. The builder checks it
against the actual source and the same genuine replacement replay.

```sh
python publication/h100-confirmatory/paper/build_paper.py \
  --replay /absolute/replacement-replay \
  --final-review /absolute/FINAL_PAPER_REVIEW.json \
  --output /absolute/new-final-paper-build
```

Inspect every rendered PDF page at readable resolution, including tables,
captions, long identifiers and references. Verify PDF author/title, no clipped
content or draft markers in the final, correct local HTML figures/links, and
page-by-page text consistency. Preserve `BUILD_MANIFEST.json` beside the PDF.
Compare the webpage's numbers, uncertainty, coverage and claims to the paper,
and retain the actual webpage snapshot/source hash. Only the completed full
paper, completed webpage and safely harvested evidence satisfy the intended
deliverables; this ledger and a draft preview do not.
