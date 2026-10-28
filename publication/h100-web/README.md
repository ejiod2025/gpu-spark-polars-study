# H100 publication renderer

This directory owns the new H100 research page. It never changes the historical
A100 page, launches a benchmark, changes frozen source, or deploys a Site.
Author: **Daniel Ejiofor**, with no affiliation.

The page follows the existing Systems / field notes visual language with cream,
navy, serif headings, explicit numerical definitions and accessible native
controls. It contains a primary log-ratio explorer, descriptive query cards,
complete-case coverage tables, publication figure downloads, CSVs and the full
paper. No aggregate headline is invented from partial observations.

The separate contention section shows actual per-repetition query durations and
same-host monotonic/UTC intersections. It retains the pilot and expansion as
separate descriptive samples, with no new inferential family or GPU-engine
ranking. The isolated 400-session study remains the sole confirmatory evidence.

## Local methods-only preview

```sh
python3 publication/h100-web/build.py preview
python3 -m http.server 8767 --bind 127.0.0.1 --directory publication/h100-web/preview
```

The preview is clearly marked unpublished, carries `noindex,nofollow`, contains
no `data.json`, disables the paper link, and emits `PREVIEW_ONLY.json` with
`status: UNPUBLISHED` and `final: false`. It cannot be written inside the Site
checkout. It is for layout review only and must not be published.

## Final build from real evidence

Use the Python environment containing the pinned reporting dependencies in
`research_reporting/requirements-local.txt`. The builder imports the existing
reporting validator to recompute the primary estimates and Holm adjustment from
all 400 sessions and 260 raw primary block pairs.

```sh
python publication/h100-web/build.py publish \
  --replay /absolute/path/to/complete-real-replay \
  --contention-replay /absolute/path/to/genuine-terminal-contention-replay \
  --paper /absolute/path/to/final-paper/h100-paper.pdf \
  --paper-manifest /absolute/path/to/final-paper/BUILD_MANIFEST.json \
  --output /Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/.sites-checkout-h100-publication/dist/h100
```

`publish` means build release-ready static files; **it does not deploy**. The
output path must not already exist. Build to a new staging directory for later
revisions and replace only the H100 directory after review. The Site owner adds
the historical homepage link and handles packaging and deployment.

All of these gates must pass before any output file is created:

- Exact recovery campaign ID `h100-confirmatory-gclocker100-20261027`;
  `synthetic: false`; complete genuine replay PASS.
- Registered campaign/prestart identity and exact `EXECUTION_PROFILE.json` hash
  from the committed registry; unchanged C84 source commit and exact 52-file
  source-map checksum. Registration occurs only after the actual artifacts exist.
- Complete recovery profile audit proving all 160 actual Spark driver logs and
  24 profile receipts (readiness before/after, ten block pairs, final pair). The
  driver logs prove diagnostic unlocking, GCLocker retry count 100, a
  103,079,215,104-byte maximum heap, and G1; profile receipts prove the Java
  executable/runtime hashes outside timed queries.
- Current replay, complete-audit and renderer source hashes; every replay output
  checksum; all validation, analysis, audit and render commands successful.
- Final validation PASS for all 400 sessions and 9,360 successful outputs;
  complete audit PASS with all expected warmup, measured, rejection and cleanup
  counts; zero findings.
- Reporting validation of all ten blocks, 26 primary contrasts and Holm values,
  three nested measured actions, six-query scores and unsupported N/A cells;
  all PNG/SVG/PDF figure identities.
- Final paper manifest `h100-paper-build-v1`, PASS, `final: true`, author Daniel
  Ejiofor, no affiliation, sanitization PASS, and exact campaign, replay, PDF,
  execution-profile, paper-builder, manuscript and reference hashes. Draft paper
  builds are rejected.
- Mandatory separate `--contention-replay`, with exact experiment
  `h100-contention-20261028`, plan
  `670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031`
  and prestart
  `4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25`.
  Its actual disposition must be `COMPLETE` or `STOPPED_WITH_FAILURE`, with
  integrity PASS, no findings and `publication_ready: true`. The existing
  original-400 and final-paper gates remain mandatory for either disposition.
- Current contention auditor source, preserved source copy, canonical prestart,
  actual inputs and both original CSV hashes. The current
  `research_reporting/contention_figures.py` loader reproduces the offline audit
  from the real saved bytes and compares every scientific receipt field,
  including terminal driver/ACKs, original worker payload, action separation and
  same-scheduler resume. Updating receipt labels or their checksum maps cannot
  replace missing evidence. The loader imports plotting libraries lazily.
- The final paper must bind the exact same `contention_review` object emitted
  in the public provenance and publication manifest:

  ```json
  {
    "status": "COMPLETE",
    "experiment_id": "h100-contention-20261028",
    "plan_sha256": "670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031",
    "prestart_sha256": "4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25",
    "replay_sha256": "actual terminal CONTENTION_REPLAY.json SHA256",
    "disposition": "COMPLETE or STOPPED_WITH_FAILURE"
  }
  ```

  Here review `status: COMPLETE` identifies the completed review; `disposition`
  records the actual experiment outcome. The Site owner must retain this same
  exact object in the actual public deployment receipt required by
  `operations/LAMBDA_SHUTDOWN_GUARD.md`. A static build is not that receipt.

Optional `--contention-figures /absolute/final-export-directory` adds up to six
verified contention figures in SVG/PNG/PDF, covering only phases with observed
waves. A terminal pilot failure does not require invented expansion figures.
The figure manifest must be
`h100-contention-figures-v1`, `draft: false`, `final: true`, publication-ready,
and bind the same receipt, CSVs, read-input map and current renderer/auditor.
Every output checksum is checked. Draft figures cannot enter the release.
Omitting this option retains the accessible per-repetition numerical tables.

The historical `offline-full-audit-v3` contention receipt remains unchanged at
`PARTIAL` / `publication_ready: false`; it lacked the original block-04 payload.
The later genuine `offline-terminal-audit-v1/CONTENTION_REPLAY.json` is
`COMPLETE`, integrity PASS, `publication_ready: true`, with no findings and SHA256
`0cad03b7ab2edc7e0d62dbcc7b84f8fa0ea51325aba41eb354b5baae0c51a2d3`.
It verifies all 20 waves, 460 oracle-valid outputs, ten strict rejections and
110 common measured pairs, together with the actual original payload, action
separation and same-scheduler resume. The webpage's current-source fresh replay
and typed public-export checks pass for this terminal receipt. The paper and
webpage produce the same exact six-field `contention_review` contract.
The optional figure gate also passes for `figures-final-v1`, including all six
figure stems, 19 output hashes and SVG sanitization.

This closes the separate contention evidence gate only. Complete original-400
replay, final paper/editorial review and actual deployment remain independently
required. No release page, final paper review or publication receipt is inferred
from this smoke check. Timings and private draft figure exports remain absent
from the methods-only preview.

There is no `--allow-synthetic`, `--skip-audit` or incomplete-data override.
The lost cohort's surviving 120 sessions / 2,808 core comparisons, earlier
readiness checks, and synthetic fixtures can never satisfy this release gate.
The aborted replacement v1 cohort (88 passed sessions before the SF100 Spark CPU
warmup failure) remains historical evidence. Neither its timings nor the separate
SF100 recovery diagnostic enter recovery estimates, and a forged PASS or changed
campaign label cannot promote it through the registry/profile gate.

## Public data boundary

`data.json` has schema `h100-publication-data-v1`. It contains only fixed labels,
exact campaign/prestart/profile hashes, counts, allowlisted primary estimates, paired block ratios,
descriptive CPU Polars 2.0 query ratios and complete-case scores. Unsupported
Polars GPU complex-suite scores remain null/N/A, never partial sums.

The `contention` object adds only typed, plan-bound labels, counts, actual
terminal phase dispositions, per-repetition action endpoints and measured
overlap. Pair A is GPU Spark + CPU Polars 2.0.0; pair B is CPU Spark + strict
Polars GPU. Both processes request 26 CPU threads on the same 26-vCPU host;
Spark retains a 96 GiB heap and there is one GPU path per pair. Pair B complex
Q13/window CPU-only actions retain `paired_condition: false`. They cannot become
a uniformly contended six-query score. Pilot and expansion are never pooled.

`downloads/` contains numerical CSVs, the checked final paper and a narrow
`provenance.json`. `assets/` contains the four verified publication figures in
PNG, SVG and PDF. Raw replay manifests, audit manifests, logs, plans, environment
inventories, source code, SSH material, private addresses and credential files
are not copied. Text/SVG downloads receive an additional private-path,
address and credential-pattern scan.

The contention downloads are rebuilt as typed allowlisted
`contention_query_actions.csv` and `contention_common_overlap.csv`; raw audit
CSVs and manifests are never copied. Provenance records exact original CSV hashes
and separate public CSV hashes. No raw errors, PIDs, host paths, addresses or
private URLs cross this boundary. Optional contention assets use fixed stems
for actual observed phases; the private figure manifest and captions source
are not copied.

`PUBLICATION_MANIFEST.json` has schema `h100-publication-web-v1`, PASS,
`final: true`, `synthetic: false`, campaign ID/hash, execution-profile hash, author, replay hash, paper
hash, builder hash and a checksum map whose keys are only relative public files.
It proves a local build, not an actual deployment. The separate native Site
deployment receipt must establish public hosting before the shutdown guard runs.

## Verification

With the pinned reporting environment active:

```sh
python -B -m unittest discover -s publication/h100-web/tests -v
node --check publication/h100-web/templates/app.js
node --test publication/h100-web/tests/test_app.cjs
```

The gate tests deliberately label fabricated/partial inputs as test inputs and
assert rejection; they never produce publication data. Final browser QA must
also inspect the real data's full ratio range, controls, query N/A states, paper,
and working downloads at desktop and narrow widths.
The UI tests exercise both individual expansion repetitions, pilot separation,
CPU-only diagnostic labels, honest failure disposition and empty unavailable
cells. They use fabricated display rows only and never build a release.
