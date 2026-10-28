# Local publication tooling readiness, 2026-10-28

This is a local tooling check, not a completed-study finding, editorial review,
publication receipt or final paper. Original400 collection remains incomplete.
No new paper PDF, benchmark, SSH request, source-Parquet read or publication was
performed. Manuscript and claim prose were not changed.

## Verified runtime

`load_workspace_dependencies` resolved bundle `26.1017.22136`.

| Purpose | Verified executable/package |
| --- | --- |
| PDF authoring Python | `/Users/danielejiofor/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`, Python 3.12.14 |
| PDF libraries in that Python | ReportLab 4.4.9, pypdf 6.10.0, pdfplumber 0.11.9, Pillow 12.3.0 |
| Pandoc | `/opt/homebrew/bin/pandoc`, 3.7.0.2 |
| PDF rasterizer | `/Users/danielejiofor/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/pdftoppm`, Poppler 26.05.0 |
| PDF inspection | `/Users/danielejiofor/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/pdfinfo`, Poppler 26.05.0 |
| Explicit scientific Python | `/tmp/h100-analysis-audit-venv/bin/python`, resolving to `/private/tmp/h100-analysis-audit-venv/bin/python`, Python 3.12.12 |
| Scientific dependencies | NumPy 2.5.4, SciPy 1.17.1, psutil 7.1.2, Matplotlib 3.10.8, matching the declared local requirements |

`pdftotext` is absent from the current PATH. The builder's supported pypdf
layout-extraction fallback is installed; this is not a blocker. All five
required Unicode font roles register successfully from ReportLab's bundled
Vera family. Pandoc and both Poppler tools resolve on the current PATH.
No dependency installation or builder code fix was necessary.

The current paper suite passes all 19 focused tests, including the pypdf-based
temporary layout checks, final gate refusals, explicit-runtime selection,
source-origin checks and lazy common imports. Those visibly synthetic fixtures
are not research artifacts. The current paper and webpage CLI help confirms
the argument contracts below.

## Real separate-contention binding

The existing terminal audit remains COMPLETE/integrity PASS/publication_ready
true, with no findings. Its 34-entry original-attempt binding, verified action
separation and same-scheduler resume remain intact. The current auditor and
figure-helper hashes match the receipt/manifests. All nine final-figure input
hashes and all nineteen output hashes were rechecked locally.

| Actual artifact | SHA256 |
| --- | --- |
| `rerun/contention-20261028/offline-terminal-audit-v1/CONTENTION_REPLAY.json` | `0cad03b7ab2edc7e0d62dbcc7b84f8fa0ea51325aba41eb354b5baae0c51a2d3` |
| `rerun/contention-20261028/figures-final-v1/FIGURE_MANIFEST.json` | `5751b86ee46989fdfc7b0d25b1e2eac3a475a09c872143026cedfbd1e06000bd` |
| Existing selected-runtime paper verification proof | `a2f2401acf21b3ea7f3832568a0eeb98b5a8efe1bb359cd350d9ac53d7568099` |

The existing genuine proof is at
`rerun/contention-20261028/paper-terminal-verification-v1/CONTENTION_VERIFICATION.json`.
It is separate-role verification only. A final paper invokes fresh scientific
verification again in the explicitly selected analysis subprocess. The common
paper/web `contention_review` must use the terminal receipt hash above and
actual disposition COMPLETE; the historical v3 PARTIAL receipt stays unchanged.

## Remaining sequence, only after genuine collection completion

The material blocker is actual completion and replay of the isolated400,
followed by complete result prose and real editorial/visual review. No tooling
check removes those gates. Require all ten raw block backups, final and
post-exit completion backups, original supervisor exit zero, unchanged single
attempts, chronological full input-byte gates, 24 profile receipts and 160 Spark
driver logs. The final audit must account for 9,360 successful outputs
(2,340 warmup/7,020 measured), 60 expected strict rejections, 3,360 successful
and 60 failed complex cleanups, with no unexplained findings.

Run from the repository root. These are future commands, not records of
execution. Every output directory must be new; choose another version if it
already exists. Keep the canonical seal and original backups unchanged.

```sh
cd /Users/danielejiofor/AIRFLOW/gpu-spark-polars-study
H100_ANALYSIS_PYTHON=/tmp/h100-analysis-audit-venv/bin/python
H100_PDF_PYTHON=/Users/danielejiofor/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
H100_CANONICAL="$PWD/evidence-research/20261027/h100-confirmatory-gclocker100-v1"
H100_RECONSTRUCTION="$PWD/rerun/gclocker100-20261027/reconstruction-v1"
H100_REPLAY="$PWD/rerun/gclocker100-20261027/replay-v1"
H100_CONTENTION="$PWD/rerun/contention-20261028/offline-terminal-audit-v1"
H100_CONTENTION_FIGURES="$PWD/rerun/contention-20261028/figures-final-v1"
H100_PAPER="$PWD/rerun/gclocker100-20261027/paper-final-v1"
H100_WEB="$PWD/rerun/gclocker100-20261027/web-final-v1"

"$H100_ANALYSIS_PYTHON" -B operations/reconstruct_h100_replacement.py \
  --cohort gclocker100 --archive "$H100_CANONICAL" \
  --raw-backups "$PWD/rerun/gclocker100-20261027/raw-backups" \
  --output "$H100_RECONSTRUCTION"

"$H100_ANALYSIS_PYTHON" -B research_reporting/replay.py \
  --archive "$H100_CANONICAL" \
  --artifacts-root "$H100_RECONSTRUCTION/harvest/campaign-results" \
  --output "$H100_REPLAY"
```

Require genuine validation, frozen analysis, complete audit and rendering PASS
from that same replay. Cross-check all 26 primary rows/260 block ratios, 40
coverage/score cells and 32 secondary pairs. Populate the manuscript, full
tables and exact real-figure captions from those artifacts; preserve unsupported
N/A cells, four unpooled cohort roles, post-incident profile selection,
contention limits, managed hold and separate local-power/backup pacing.

Only after that substantive review, prepare a genuine `FINAL_PAPER_REVIEW.json`
at the path below. Do not create it while results are pending. It must bind the
actual manuscript/reference hashes, campaign, exact terminal contention object
and all six review booleans described in `../RESULTS_FINALIZATION.md`.

```sh
"$H100_PDF_PYTHON" -B publication/h100-confirmatory/paper/build_paper.py \
  --replay "$H100_REPLAY" \
  --contention-replay "$H100_CONTENTION" \
  --analysis-python /tmp/h100-analysis-audit-venv/bin/python \
  --contention-figures "$H100_CONTENTION_FIGURES" \
  --final-review "$PWD/rerun/gclocker100-20261027/FINAL_PAPER_REVIEW.json" \
  --output "$H100_PAPER"
```

Inspect every resulting PDF page at readable resolution with the bundled
Poppler tools, check extracted text/metadata and verify all embedded figure
bytes, tables, captions and links. The PDF skill requires the authoring-operation
marker before the first actual authoring command; this readiness check did not
author a new PDF. Require genuine final BUILD_MANIFEST PASS, sanitization PASS,
Daniel Ejiofor/no affiliation, current source hashes and exact replay/profile/
contention bindings before building the webpage.

```sh
H100_PAPER_QA="$PWD/rerun/gclocker100-20261027/paper-final-v1-qa"
mkdir "$H100_PAPER_QA"
/Users/danielejiofor/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/pdfinfo \
  "$H100_PAPER/h100-paper.pdf"
/Users/danielejiofor/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/pdftoppm \
  -scale-to 1600 -png "$H100_PAPER/h100-paper.pdf" "$H100_PAPER_QA/page"
```

Keep these QA images outside the hashed final paper output directory.

```sh
"$H100_ANALYSIS_PYTHON" -B publication/h100-web/build.py publish \
  --replay "$H100_REPLAY" \
  --contention-replay "$H100_CONTENTION" \
  --contention-figures "$H100_CONTENTION_FIGURES" \
  --paper "$H100_PAPER/h100-paper.pdf" \
  --paper-manifest "$H100_PAPER/BUILD_MANIFEST.json" \
  --output "$H100_WEB"
```

This `publish` subcommand builds local static files; it does not deploy. Review
the genuine result page and downloads at desktop/narrow widths before the Site
owner stages it in the existing native Site checkout. Actual public deployment,
access/download checks, private independent retrieval, final claim review and
retirement evidence remain separate root-owned operations. Neither this note
nor a local build satisfies them. See the reconstruction, private-archive and
shutdown runbooks for those complete evidence contracts.
