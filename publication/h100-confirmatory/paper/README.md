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

For final builds, supply a completed genuine replacement replay and a separate
editorial review JSON bound to the actual manuscript and references. The exact
fields and finalization procedure are in the ledger. The builder rejects the
lost campaign, synthetic/incomplete/modified replay outputs, missing complete
tables, mismatched editorial review and pending/draft markers in the paper.
It consumes the existing statistics and never recomputes a new analysis.

```sh
python publication/h100-confirmatory/paper/build_paper.py \
  --replay /absolute/replacement-replay \
  --final-review /absolute/FINAL_PAPER_REVIEW.json \
  --output /absolute/new-final-paper-build
```

Insert verified figures into the manuscript using their actual replay paths:

```markdown
![Full standalone caption from the real CAPTIONS.md.](replay/figures/suite_ratios_baseline.png)
```

Figures must occupy their own Markdown paragraph and use verified PNG/JPEG
exports. The builder copies their exact bytes and records hashes. PDF figures
receive a separate landscape page for legibility; surrounding prose remains
portrait. Local repository documentation links are rendered as their labels in
the standalone paper; external primary references remain linked. No absolute
home-directory or private host path is needed in the manuscript.

`BUILD_MANIFEST.json` uses schema `h100-paper-build-v1`. A successful final
build has `status: PASS`, `final: true`, `synthetic: false`, `author`,
`campaign_id`, `campaign_sha256`, `replay_manifest_sha256`, `paper_sha256`,
`paper_html_sha256`, `manuscript_sha256`, `references_sha256`,
`editorial_review_sha256`, `embedded_figures_sha256`, and `output_files_sha256`.
This is the integration contract for the separate H100 webpage builder.
`public_sanitization.status: PASS` records a bounded scan of extracted PDF
text/HTML for private-key, token, credential-assignment, home-path and IPv4
patterns. It is not publication or redistribution authorization.

Inspect every PDF page after each meaningful edit. For example:

```sh
pdfinfo /absolute/paper-build/h100-paper.pdf
pdftoppm -scale-to 1600 -png /absolute/paper-build/h100-paper.pdf /absolute/qa/page
```

Review all pages for clipped tables, missing glyphs, orphan headings, figure
legibility and reference wrapping. After final assembly, compare paper and
webpage findings against the same replay, preserve their manifests and the
full off-host evidence, and follow the parent task's completion/shutdown gate.
