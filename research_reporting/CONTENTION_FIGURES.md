# Separate contention figures

`contention_figures.py` produces standard Matplotlib SVG, PNG and PDF artifacts
from the separate H100 contention audit. It reads local saved evidence only and
does not start a worker, contact a host/API, read source Parquet, modify the
original 400-session renderer/auditor or write paper/web publication paths.

Final export requires a genuine `COMPLETE` or `STOPPED_WITH_FAILURE` receipt,
`integrity_status:PASS`, `publication_ready:true`, no findings, the exact genuine
plan/prestart identities, and the current auditor source. It verifies every
audit-output hash, including both CSVs, then reproduces the entire offline audit
in a temporary directory and compares every substantive receipt field and output
hash. Editing a receipt to say `COMPLETE` cannot satisfy this boundary. All raw,
transport, driver and original-study separation inputs must still be available
at their preserved receipt locations. A stale auditor requires a new honest
audit in a new directory, never editing a receipt or its source snapshot.

The current full audit at `rerun/contention-20261028/offline-full-audit-v3` is
`PARTIAL`, integrity `PASS`, with all four pilot and sixteen expansion waves,
460 SQL outputs, ten expected strict rejections and 110 common measured pairs.
Its original block-04 attempt payload is still needed for the final separation
proof. Only an explicit draft export is currently permitted:

```sh
cd /Users/danielejiofor/AIRFLOW/gpu-spark-polars-study
/tmp/h100-analysis-audit-venv/bin/python -B research_reporting/contention_figures.py \
  --audit rerun/contention-20261028/offline-full-audit-v3 \
  --output rerun/contention-20261028/new-figures-DRAFT --draft
```

Every draft figure has a prominent status label and watermark; captions and
manifest also identify it as draft. Manifest `final` and `publication_ready`
remain false even if `--draft` is used with a terminal audit. There is no
synthetic-input acceptance flag and no status override. Output directories must
be new and outside all immutable input trees. Render with the existing analysis
environment, which pins Matplotlib 3.10.8.

After the actual original attempt arrives, first create a new terminal audit
using the complete replay command in `CONTENTION_REPLAY.md`. Then use that new
audit without `--draft`, still in a new private output directory. Rendering does
not publish these artifacts or authorize instance retirement.

## Figure choices and limits

For each phase, the two `*_pair_{A,B}_common_actions` figures show exact common
query action durations, one point per recorded action. Both expansion
repetitions are explicit (filled circle and open square), without averaging,
confidence intervals, p-values or superiority claims. Pilot and expansion have
separate artifacts. Pair A is Spark GPU plus CPU Polars 2.0.0; pair B is CPU
Spark plus Polars GPU. Their competitors differ; the figures do not rank the
two GPU engines across pairs. The horizontal duration axis is logarithmic.

The `*_actual_action_timeline` figures retain every attempted phase wave in
planned order, with CPU/GPU lanes. Actual measured UTC action start/end
intervals, including gaps, use the same linear seconds axis across panels.
Bars have their true width with no minimum-width enlargement, edge stroke or
endpoint marker. All measured CPU-only pair-B complex Q13/window diagnostics
remain visible with hatching; they never enter the common-query duration
panels or a six-query contended score. Warmups are deliberately excluded from
these measured-action figures. Both-clock overlap validation stays in the
independently reproduced audit. Neither process lifetimes nor whole-wave wall
durations substitute for actual action overlap. Failed/unattempted actions get
no imputed latency. No resource causal interpretation is added.

An honest terminal scientific failure is eligible only after the auditor has
verified its actual failed snapshot, explicit fixed-prefix disposition and full
separation/resume proofs. Figures retain all observed attempted waves and label
the terminal status; absent phases produce no figure.

## Manifest and checks

`FIGURE_MANIFEST.json` uses `h100-contention-figures-v1`. It records exact
receipt/audit-output/current-source/canonical-input hashes in
`input_files_sha256`, the reproduced auditor's full original input map in
`reproduced_audit_input_files_sha256`, source identities, audit counts, remaining
handoff reasons, phase-specific paired/diagnostic action counts and hashes for
every output. The manifest excludes its own hash to avoid a cycle. Input CSVs
stay immutable in their audit directory; no derived suite score is exported.

```sh
/tmp/h100-analysis-audit-venv/bin/python -B -m pytest -q \
  research_reporting/tests/test_contention_figures.py
```

Tests exercise visible lifecycle-only fixtures and actual locally preserved
audit bytes, never fabricated measured study findings. Genuine-evidence tests
skip when that ignored private evidence is unavailable. They cover draft/final
boundaries, both CSV hashes, current-source matching, a forged terminal receipt,
true timeline widths and all output formats. Inspect rendered PNGs before using
an export. SVG/PDF dates are omitted and SVG identifiers use a stable salt.
