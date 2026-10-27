# H100 publication renderer

This directory owns the new H100 research page. It never changes the historical
A100 page, launches a benchmark, changes frozen source, or deploys a Site.
Author: **Daniel Ejiofor**, with no affiliation.

The page follows the existing Systems / field notes visual language with cream,
navy, serif headings, explicit numerical definitions and accessible native
controls. It contains a primary log-ratio explorer, descriptive query cards,
complete-case coverage tables, publication figure downloads, CSVs and the full
paper. No aggregate headline is invented from partial observations.

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
  --paper /absolute/path/to/final-paper/h100-paper.pdf \
  --paper-manifest /absolute/path/to/final-paper/BUILD_MANIFEST.json \
  --output /Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/.sites-checkout-h100-publication/dist/h100
```

`publish` means build release-ready static files; **it does not deploy**. The
output path must not already exist. Build to a new staging directory for later
revisions and replace only the H100 directory after review. The Site owner adds
the historical homepage link and handles packaging and deployment.

All of these gates must pass before any output file is created:

- Exact replacement campaign ID `h100-confirmatory-replacement-20261027`;
  `synthetic: false`; complete genuine replay PASS.
- Registered campaign/prestart identity from the committed registry; unchanged
  C84 source commit and exact 52-file source-map checksum.
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
  paper-builder, manuscript and reference hashes. Draft paper builds are rejected.

There is no `--allow-synthetic`, `--skip-audit` or incomplete-data override.
The lost cohort's surviving 120 sessions / 2,808 core comparisons, earlier
readiness checks, and synthetic fixtures can never satisfy this release gate.

## Public data boundary

`data.json` has schema `h100-publication-data-v1`. It contains only fixed labels,
exact hashes, counts, allowlisted primary estimates, paired block ratios,
descriptive CPU Polars 2.0 query ratios and complete-case scores. Unsupported
Polars GPU complex-suite scores remain null/N/A, never partial sums.

`downloads/` contains numerical CSVs, the checked final paper and a narrow
`provenance.json`. `assets/` contains the four verified publication figures in
PNG, SVG and PDF. Raw replay manifests, audit manifests, logs, plans, environment
inventories, source code, SSH material, private addresses and credential files
are not copied. Text/SVG downloads receive an additional private-path,
address and credential-pattern scan.

`PUBLICATION_MANIFEST.json` has schema `h100-publication-web-v1`, PASS,
`final: true`, `synthetic: false`, campaign ID/hash, author, replay hash, paper
hash, builder hash and a checksum map whose keys are only relative public files.
It proves a local build, not an actual deployment. The separate native Site
deployment receipt must establish public hosting before the shutdown guard runs.

## Verification

```sh
python3 -m unittest discover -s publication/h100-web/tests -v
node --check publication/h100-web/templates/app.js
```

The gate tests deliberately label fabricated/partial inputs as test inputs and
assert rejection; they never produce publication data. Final browser QA must
also inspect the real data's full ratio range, controls, query N/A states, paper,
and working downloads at desktop and narrow widths.
