# Local study preview

This directory is a private review draft. It does not register, synchronize, or
publish a Site. An owner-private Sites review destination is being prepared by
the calling task; public release, notices, and any required permission remain pending. The
preview keeps that status visible; an attached evidence ZIP remains an uncleared
local draft.

Build from the workspace root with Python's standard library:

```bash
python3 benchmarks/spark_gpu/publication/site-preview/build_preview.py \
  --evidence-bundle benchmarks/spark_gpu/publication/dist/cleanup-protocol-draft-v4.zip
```

The default baseline is the verified five-scale report at
`benchmarks/spark_gpu/reports/crossover-20261025`. Without `--complex-report`, the
complex tab displays query designs and a pending state, with no invented timings.
To add a verified formal complex report later:

```bash
python3 benchmarks/spark_gpu/publication/site-preview/build_preview.py \
  --complex-report benchmarks/spark_gpu/reports/COMPLEX_REPORT_DIRECTORY \
  --pending-complex-scales 300 \
  --evidence-bundle benchmarks/spark_gpu/publication/dist/FINAL_DRAFT_BUNDLE.zip
```

Use `--pending-complex-scales 300` while SF30/SF100 are validated and SF300 is
still pending. This keeps the status partial/progress and names SF300 visibly,
even when every case in the supplied report is valid. Remove that option once
the final report includes SF300; a supplied scale cannot also be declared
pending. Reports with more than twelve cases use per-scale figures: the largest
available scale is shown with an explicit scope caption, and all copied
per-scale PNGs are linked in the evidence section.

The builder checks correctness/plan-coverage gates, case coverage, numeric ranges,
ratio arithmetic, and saved-check counts. Formal complex cases must all use the
revised `verified_gc` protocol. Legacy or mixed-protocol formal reports are
rejected, so the earlier v1 attempt cannot enter a v2 timing curve. Only
three-trial formal complex cases enter the page. One-trial smoke cases show a functional-check notice with no
performance values. Incomplete cases stay excluded, and an aggregate is shown
only where all six cases have a validated complete-suite score. A partial report
remains visibly partial. The script verifies both an attached ZIP's checksum
sidecar and its internal content-checksum manifest before copying it.

For a formal v2 report, every included output must have one matching passed
cleanup record. The builder checks observed scratch counts, zero remaining Spark
shuffle files and bytes, per-case cleanup counts and sums, session policy and
session totals. Explicit cleanup happens after a query returns and outside its
timer. The page reports barrier counts and cleanup seconds separately from query
medians, with process wall time in a separate column. Session cleanup totals
include warmups, measured trials, and any saved barriers belonging to cases
excluded from a partial comparison. They cannot be added to a sum of query
medians to construct an end-to-end score. Explicit GC can change the next query's
heap state; all displayed v2 scales and engines must share that protocol.

An attached exporter-v4 ZIP may also contain `INCIDENTS.json`. The builder derives
a portable nonperformance incident summary from that index, preserving the
original raw evidence in its separate bundle namespace. No incident trial times
or errors enter the page's curves. The attached-bundle scope resource lists only
the runs and sessions actually present, including partial/incident roots; it
does not imply that every page observation is in a provisional ZIP. A final ZIP
can contain all baseline, smoke, v1 incident and v2 roots together without mixing
their performance results: the displayed complex measurements come only from
the explicitly supplied validated v2 report.

Generated files are in `dist/`: `index.html`, `styles.css`, `app.js`, portable
`data.json`, the real scientific chart, and any enabled evidence/download files.
The builder regenerates only directories inside `site-preview` that it owns.
No source-machine paths, host identifiers, raw inventories, or credentials are
needed in the page payload. Disabled resources are marked pending rather than
linked to missing files.

The calling/root task owns preview serving and browser QA. A loopback-only static
server can be started there, for example:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 \
  --directory benchmarks/spark_gpu/publication/site-preview/dist
```

Open `http://127.0.0.1:8765/` through that task's supported preview surface. A plain
`file://` page cannot reliably fetch its sibling JSON. No server was started by
the preview builder or its implementation task.

The HTML/CSS/JS source lives in `template/`. It uses no framework, external font,
analytics, or remote application requests. JavaScript fetches only `data.json`;
official documentation links open only when a reader follows them.

Run the stdlib synthetic protocol gate tests with:

```bash
python3 benchmarks/spark_gpu/publication/site-preview/test_build_preview.py -v
```

These fixtures exercise publication gates only and are not benchmark evidence.
Checks performed: Python compilation; JavaScript syntax; actual baseline and
one-trial smoke build gates; synthetic verified-GC complete/partial coverage,
legacy/mixed-protocol rejection, missing/duplicate/failed cleanup rejection,
scratch/count/sum tampering, process-wall checks and incident exclusion;
portable payload and enabled-resource existence checks;
offline DOM tests of tabs, query selection, pending/smoke suppression, partial
coverage, keyboard controls, and unsafe-link rejection. Visual browser QA remains
with the calling/root task.
