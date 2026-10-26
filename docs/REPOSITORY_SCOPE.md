# Repository scope and provenance

This is the private development archive for the [public study](https://a100-spark-polars-study-oct2026.ejiod.chatgpt.site). It preserves author-created source and curated reproducibility evidence. The repository's private visibility does not change the separate website's public audience, and the website's public visibility does not grant a blanket license to repository contents.

## Included material

The snapshot includes benchmark Python modules and query definitions; generation, running, comparison, plotting, oracle and resource-cleanup tools; publication/export and website source; final reports; exact historical author-source snapshots; and the expanded final evidence bundle. `docs/experiment-methods.md` preserves the original working README as historical context. Its initial three-scale scope and older complex command are superseded by the current root README's five-scale baseline and explicit `verified_gc` formal instructions.

The curated `evidence/results/` tree contains seven distinct run roots:

| Run | Role |
|---|---|
| `production-20261025` | Original suite at SF1, SF10 and SF30 |
| `large-sf100-20261025` | Original suite at SF100 |
| `large-sf300-20261025` | Original suite at SF300 |
| `complex-smoke-sf1-20261025` | Initial small-scale functional check |
| `complex-cleanup-smoke-sf1-20261025` | Small-scale revised-cleanup check |
| `complex-formal-20261025` | Preserved legacy incident support; excluded from complete performance summaries |
| `complex-formal-v2-20261025` | Formal `verified_gc` suite at SF30, SF100 and SF300 |

The two smoke roots do not supply formal performance observations. The v1 incident retains actual saved outputs and failures in its own namespace, without converting failure into a latency bound or GPU win. Its timings remain outside the final v2 comparison. Formal baseline and complex scores stay separate; each sums six per-query medians. The study concerns selected TPC-H-derived/custom queries and makes no complete or certified benchmark claim.

## What is reproducible from saved evidence

The root README provides commands against `evidence/results/` that revalidate output agreement, final-plan coverage and explicit cleanup records, then recalculate timing summaries from saved trials. Curated records retain materialized results, timing fields, configuration, manifest identity and portable plan references. Final reports cover five original scales and three complex scales. Full complex v2 coverage is 18 cases, nine sessions, 216 saved outputs/comparisons, 216 passed cleanup barriers and 72 inspected GPU plans.

`evidence/BUNDLE.json`, `TRIALS.csv`, `SUMMARY.csv`, `INCIDENTS.json` and input manifests describe scope rather than relying on directory names alone. `evidence/SHA256SUMS` covers the exported payload. `PROVENANCE.json` identifies original/exported hashes and normalization. Curated JSON and plans can have host identifiers and private paths normalized; their exported bytes do not claim original-byte identity. Source snapshots marked exact preserve the recorded source bytes. `artifacts/source-snapshots/` and the bundle's `source-snapshots/` serve historical-code inspection, while root modules are the current reproduction tools.

Small-scale oracle/generation proofs and post-run-integrity evidence retain their stated scope. Matching checksums establish file identity, not a global independent SQL oracle. Final-plan GPU operators establish operator selection, not the fraction of runtime spent on GPU. In particular, correlated `EXISTS`/`NOT EXISTS` input SQL is not proof of nonempty anti-join execution; actual saved final plans are authoritative.

## Excluded bulk and machine state

Bulk generated Parquet, generation databases, virtual environments, installed packages, NVIDIA JARs and other dependency binaries, large raw Spark event logs/telemetry archives, transient scratch/shuffle output, SSH keys and credentials are not versioned. Neither are nested hosted-checkout `.git` history or credential-bearing hosting metadata. The static hosted copy preserves the page and its selected assets, rather than the native hosting control plane. Dependencies are retrieved separately from their official sources; the recorded JAR hash and Python/reporting pins are in the evidence bundle.

Excluding raw event logs means historical stage/task attribution and some operational resource audits cannot be independently repeated from the curated tree alone. Published audit notes preserve their conclusions and scope, but rerunning them requires the corresponding original host logs/metadata. Deployment, audience revisions and source-upload operations likewise require native service records and the original deployment archive/commit metadata. A static copy or ordinary Git commit does not recreate those service events. Saved result validation is a separate, supported operation.

## Rights and attribution

No blanket MIT license or repository-wide license grant is asserted. Author-created source, third-party query derivations, specifications, datasets/generation components, dependency binaries and product documentation can have different rights and notices. Preserve the included `NOTICES.md`, publication research notes and provenance when inspecting or sharing material. Repository access, sanitized files and checksums do not establish third-party permission or endorsement. This document records scope; it does not expand redistribution rights.

## Import-specific details

`SOURCE_MANIFEST.json` covers all available current and archived source files. The remote-status utility now accepts host/workspace/run/PID arguments rather than retaining the original VM target. Historical metadata paths are tokenized; experimental Python source snapshot bytes are unchanged. The cleanup-smoke revisions formerly available only inside the release bundle are also retained under `artifacts/source-snapshots/complex-cleanup-smoke/`. Two unselected early pilot smoke source revisions could not be recovered; those runs are not among the seven curated evidence roots.

The hosted website directory is a source/static snapshot, not its managed Git history. Native Site identity is omitted; `hosting.example.json` shows the configuration shape. Open or create a Site through the supported Sites workflow before deploying, and retain its returned project identity. Benchmark code, the public website and this private development repository have separate publication/access controls.
