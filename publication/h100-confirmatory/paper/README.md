# Paper source and build

The editable paper is [`../MANUSCRIPT.md`](../MANUSCRIPT.md), authored by
**Daniel Ejiofor** with no institutional affiliation supplied. Its annotated
primary references live in [`../references.md`](../references.md). The complete
[result insertion ledger](../RESULTS_FINALIZATION.md) defines how the actual
replacement campaign enters the finished paper.

`build_paper.py` parses Markdown through Pandoc and renders a Unicode PDF with
ReportLab. It also emits standalone HTML, composed Markdown, the parsed source,
PDF-extracted text, CSS and a hash manifest. It starts no measurements and
publishes nothing. It requires Python with ReportLab, Pandoc and Poppler's
`pdftotext` or Python `pypdf` for text inspection. Prefer the bundled Codex Python/runtime exposed by
`load_workspace_dependencies`.

```sh
python publication/h100-confirmatory/paper/build_paper.py \
  --output /absolute/new-draft-paper-build
```

Draft builds have `status: PASS_DRAFT`, `final: false`, and a visible
`DRAFT · RESULTS PENDING` on every PDF page. A draft preview is a formatting
artifact and cannot satisfy the finished paper deliverable or final webpage
paper gate.

For final builds, supply the completed genuine recovery replay for exactly
`h100-confirmatory-gclocker100-20261027`, a genuine terminal separate-contention
audit, an explicitly selected analysis Python and a separate editorial review
JSON bound to the actual manuscript, references and terminal contention receipt. The exact
fields and finalization procedure are in the ledger. The builder rejects the
lost campaign, aborted replacement v1 cohort, arbitrary campaign IDs,
synthetic/incomplete/modified replay outputs, missing complete tables,
mismatched editorial review and pending/draft markers in the paper. The actual
campaign/prestart/profile hashes must be registered before use; the original
historical identity anchors remain valid for archive verification only.

The final gate verifies the exact 52-file C84 source map and current reporting
code, all four successful replay commands, the bound complete audit, and exact
counts for 400 sessions, 9,360 successful outputs and 60 translation rejections.
The complete audit must prove the registered recovery profile in all 160 actual
Spark driver logs and all 24 profile receipts (readiness before/after, ten block
pairs, final pair). Separate diagnostics and aborted-campaign observations are
excluded from the recovery analysis.
Primary statistics are consumed unchanged. The separate contention gate
reproduces the current-source offline audit from saved bytes; it never runs a
benchmark, starts a JVM/GPU worker, or reads source Parquet.

`--contention-replay` is mandatory for a final paper and must be the exact
`h100-contention-20261028` role. `COMPLETE` and honest `STOPPED_WITH_FAILURE`
dispositions are eligible only with integrity PASS, publication_ready true,
no findings, actual original payload/action-separation proof and same-scheduler
resume. The shared `research_reporting/contention_figures.py.load_audit(...,
draft=False)` gate validates current auditor/source/canonical/CSV hashes and
freshly reproduces the full offline audit. A changed status marker cannot
promote a PARTIAL/publication_ready false receipt. The historical v3 receipt
remains PARTIAL; the later genuine terminal-v1 audit is COMPLETE. Its exact
current binding and the resolved local authoring/analysis runtimes are recorded
in [the dated local readiness check](LOCAL_READINESS_20261028.md).

The PDF authoring environment may be the bundled ReportLab Python. Scientific
verification runs in a subprocess selected explicitly with `--analysis-python`
(for example, the existing `/tmp/h100-analysis-audit-venv/bin/python`). The builder
never chooses an interpreter from the supplied receipt or assumes that its
ReportLab Python has `psutil` and the analysis dependencies. A virtual environment
invocation path is retained. The current helper/auditor load by exact source
paths with explicit module-origin checks. The common gate imports no Matplotlib
and invokes no plotting functions. Exact verification inputs, current helper/auditor hashes
and selected runtime are recorded in `CONTENTION_VERIFICATION.json`.

```sh
python publication/h100-confirmatory/paper/build_paper.py \
  --replay /absolute/gclocker100-recovery-replay \
  --contention-replay /absolute/new-terminal-contention-replay \
  --analysis-python /tmp/h100-analysis-audit-venv/bin/python \
  --final-review /absolute/FINAL_PAPER_REVIEW.json \
  --output /absolute/new-final-paper-build
```

Insert verified figures into the manuscript using their actual replay paths:

```markdown
![Full standalone caption from the real CAPTIONS.md.](replay/figures/suite_ratios_baseline.png)
```

To embed final separate-contention figures, additionally supply
`--contention-figures /absolute/final-contention-figures` and use, for example:

```markdown
![Exact descriptive caption from the final contention CAPTIONS.md.](contention/figures/main_pair_A_common_actions.png)
```

The optional figure manifest must be final, genuine and tied to the same freshly
verified terminal receipt, counts, current renderer and full input map. Every
declared export hash is checked; only actually attempted phases are required,
including an honest pilot-only stopped-with-failure disposition. Draft exports
cannot enter this binding. Figure bytes remain separate from the isolated
replay and are copied unchanged for paper embedding.

Figures must occupy their own Markdown paragraph and use verified PNG/JPEG
exports. The builder copies their exact bytes and records hashes. PDF figures
receive a separate landscape page for legibility; surrounding prose remains
portrait. The builder measures the complete caption at the landscape frame's
actual width and reserves its height, spacing and a small layout tolerance
before scaling the image. Image and caption stay together on that one page;
an unfit caption is refused. The copied image bytes are unchanged. Final visual
review must still assess small labels and dense timelines after scaling.
Pandoc's implicit `Figure` wrapper is supported for a single standalone image
whose caption matches its description; it uses the same verified embedding path.
Local repository documentation links are rendered as their labels in
the standalone paper; external primary references remain linked. No absolute
home-directory or private host path is needed in the manuscript.

`BUILD_MANIFEST.json` uses schema `h100-paper-build-v1`. A successful final
build has `status: PASS`, `final: true`, `synthetic: false`, `author`,
`campaign_id`, `campaign_sha256`, `execution_profile_sha256`,
`replay_manifest_sha256`, `paper_sha256`,
`paper_html_sha256`, `manuscript_sha256`, `references_sha256`,
`editorial_review_sha256`, `embedded_figures_sha256`, and `output_files_sha256`.
It also contains the exact common paper/web `contention_review` object:

```text
status: COMPLETE
experiment_id: h100-contention-20261028
plan_sha256: 670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031
prestart_sha256: 4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25
replay_sha256: exact actual terminal CONTENTION_REPLAY.json SHA256
disposition: COMPLETE or STOPPED_WITH_FAILURE
```

The final editorial JSON must contain that exact `contention_review` object and
explicit `contention_results_and_limits: true`, alongside the five existing
review booleans and exact primary/source hashes. This records review of actual
contention results, unpaired diagnostics, wait-inclusive metrics, limitations
and managed-interlude disclosure; it cannot replace scientific verification.
Methods-only drafts may omit contention/analysis arguments and remain visibly
`PASS_DRAFT`/unpublished. No final or partial-status override is provided.
This is the integration contract for the separate H100 webpage builder.
`public_sanitization.status: PASS` records a bounded scan of extracted PDF
text/HTML for private-key, token, credential-assignment, home-path and IPv4
patterns. It is not publication or redistribution authorization.
The exact explicitly labeled NIST section 5.3.3.2 and pinned Java/OpenJDK
17.0.20.1 are recognized as public identifiers; unlabeled addresses remain
subject to the IPv4 check.

Inspect every PDF page after each meaningful edit. For example:

```sh
pdfinfo /absolute/paper-build/h100-paper.pdf
pdftoppm -scale-to 1600 -png /absolute/paper-build/h100-paper.pdf /absolute/qa/page
```

Review all pages for clipped tables, missing glyphs, orphan headings, figure
legibility and reference wrapping. After final assembly, compare paper and
webpage findings against the same replay, preserve their manifests and the
full off-host evidence, and follow the parent task's completion/shutdown gate.

Focused gate and synthetic layout tests (no benchmark or real final-paper generation):

```sh
python -B -m unittest discover -s publication/h100-confirmatory/paper -p 'test_*.py' -v
```
