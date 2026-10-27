# Recovery pre-start evidence checked, 2026-10-27

This updates the paper's preparation milestone only. The recovery has passed
its separate feasibility diagnostic and repeated readiness, and collection has
started. All full-campaign performance results, final profile/integrity gates,
analysis, paper/page completion and retirement remain pending. No partial
timings or new effect estimates were inspected or inserted for this update.

The canonical evidence prefix is
`evidence-research/20261027/h100-confirmatory-gclocker100-v1/`.
The exact registered recovery ID is
`h100-confirmatory-gclocker100-20261027`.

| Identity | Actual SHA256 |
| --- | --- |
| `campaign.json` | `3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641` |
| `PRESTART_MANIFEST.json` | `ea57fa1221b905e8ee287fe7860285c7a37535b084f13ce3dbbbca5ac4e29ace` |
| `EXECUTION_PROFILE.json` | `a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69` |
| `diagnostic-sf100-validation.json` | `8402a8c70306b624fcd4847a19faed445162f06ce09a1c97e3f7d5888566f0fc` |
| `readiness/READINESS.json` | `58bcb61e70f9fc18c5d75bf7893a03fd36420149d8b22505d68c6c295faff1fa` |

The pre-start manifest reports `status: PASS`, phase
`before-first-confirmatory-session`, 400 planned sessions, 26 primary contrasts,
C84 source and archive time 21:45:24.052335 UTC. All 345 listed file checksums
were independently checked locally and match, for 346 canonical files including
the manifest. The exact tuple also matches
`research_reporting/campaign_identities.json`. Private pre-start commit
`6dbca2fbd4de1be8aa4408a639954bc9076bfa38` precedes the first recovery session.

Directly checked diagnostic evidence:

- `diagnostic-sf100-validation.json`: PASS at 21:36:41.579958 UTC; C84 source;
  Spark CPU, complex, SF100; seed 838471203; one warmup and one measured action
  per each of six queries; twelve passing records and comparisons, twelve
  cleanup records checked, and twelve successful output comparisons.
- `diagnostic-sf100-results.json` and `diagnostic-sf100-driver_log.log`: exact
  byte hashes match the validation/profile bindings. Five effective flags are
  retry count 100, maximum heap 103,079,215,104 bytes, and true values for
  `PrintFlagsFinal`, `UnlockDiagnosticVMOptions` and `UseG1GC`.
- The sealed execution profile's diagnostic receipt identifies PASS and binds
  completion time and the driver/result/validation byte identities. Diagnostic
  timings are excluded from primary inference.

Directly checked recovery readiness evidence:

- `readiness/READINESS.json`: PASS at 21:44:41.187289 UTC; ten smoke sessions,
  116 successful output comparisons, five semantic fixture configurations and
  no errors. Strict Polars GPU retains the two diagnostic translation rejections.
- The actual `spark_cpu.log` and `spark_gpu.log` files in both
  `readiness/baseline-*` and `readiness/complex-*`: all four show the five exact
  flags above.
- Canonical `EXECUTION_PROFILE_GATES.jsonl`: two passing readiness receipts,
  `readiness-before` at 21:41:19.583719 UTC and `readiness-after` at
  21:44:41.539957 UTC, both bound to the registered execution-profile hash.
  These two receipts do not establish the final required 24-receipt proof.
- All five `manifests/sf*.json` and all eight complete `oracles/*.json` are
  byte-identical to the aborted default-profile replacement's corresponding
  files. The manuscript's actual physical dataset table remains unchanged.

The current operational record in `docs/H100_AUTONOMOUS_COMPLETION.md` and the
verified collection-start report record the initial full 1,654-file byte gate
at 21:47:43.892782 UTC and first session at 21:47:44 UTC. The local controller
state identifies the same campaign as running after pre-start acknowledgement.
These operational boundaries are a collection milestone, not a completed
sample. The pre-start snapshot does not substitute for the eventual final
journal, post-session byte gate, all ten raw block backups, 24 profile receipts,
160 formal Spark driver logs, real replay or full result-to-claim review.

Checks for this prose-only update are local JSON/hash inspections, Markdown
parsing and `git diff --check`. No VM access, benchmark/test jobs, API calls,
publication, PDF regeneration or frozen source changes were performed.
