# Completed raw-evidence bundles

`package_evidence.py` is an offline, postcollection operation. It must not run
while a performance session is active. Keep the repository and all artifacts
private unless the owner separately changes the release decision.

First harvest the complete evidence tree without changing original JSON bytes.
The confirmation driver must have exited zero, written its end record and
`ANALYSIS_READY`, and completed all 400 original sessions. Run the separate
[portable replay](README.md) and [complete-evidence audit](COMPLETE_AUDIT.md).
The auditor must PASS against the same campaign, final journal and actual
read-file hashes. A synthetic replay is not study evidence.

Package the complete raw tree into a new directory outside that tree:

```sh
python research_reporting/package_evidence.py \
  --evidence /absolute/harvested-evidence \
  --audit-complete /absolute/replay/complete-audit/AUDIT_COMPLETE.json \
  --output /absolute/new-bundle
```

Use the actual audit output path from replay. The real bundle is anchored to
the frozen H100 v1 campaign SHA256; this command does not silently generalize
to a different study or accept an incomplete/replacement sample. It requires
the original final `validation.json` and `analysis/analysis.json` inside the
evidence tree. Preserve replay outputs and figures alongside the raw bundle as
separate derived artifacts; the raw tree is never rewritten to contain them.

The packager retains every regular raw file, including logs, event data,
plans, telemetry, result rows, oracles and frozen source. It rejects symlinks,
special files, accidentally included generated Parquet/dependency binaries or
key files, and self-including output paths. Nothing is filtered to improve a
result. Existing output directories are never reused or overwritten.

Compression uses sorted PAX tar records, normalized archive ownership/modes,
zero archive timestamps, and gzip without a filename/date header. The gzip
stream is deterministic on the same compression stack, independent of output
path or chunk size. Python and zlib versions are recorded. Original file
contents remain byte-identical; archive metadata normalization does not rewrite
result timestamps or paths inside those contents.

The default chunk size is one GiB, bounding individual transfers without
creating a second giant temporary archive. `--chunk-bytes` changes that bound.
Each part, the joined archive, and every original file have SHA256 records.
The packager hashes the exact bytes passed to tar and checks the complete input
inventory again afterward, rejecting concurrent changes. `BUNDLE_MANIFEST.json`
is published last. Failures retain partial parts and `BUNDLE_FAILED.json`,
without a successful manifest. The audit report is copied verbatim beside the
parts, with its hash bound in the manifest.

Inside the bundle directory, `REASSEMBLE.md` and `CHUNKS.sha256` explain transfer verification, lexical
reassembly and extraction into a new directory. The tar root is `evidence/`.
Use extracted `evidence/campaign-results` as the replay artifact root; retain
the separately archived pre-start identity bundle and the versioned repository
for the replay/reporting code. Store large parts outside Git objects, retaining
their manifests, hashes and access instructions in the private repository.

Marked synthetic mechanism fixtures require `--allow-synthetic`; their bundle
status is `PASS_SYNTHETIC` and the reassembly document prominently identifies
them as tests. They must never be uploaded or cited as measured study results.

A bundle PASS establishes its archive integrity and completion gates. It does
not establish publication or redistribution clearance, statistical assumptions,
or performance portability beyond the study's stated scope.
