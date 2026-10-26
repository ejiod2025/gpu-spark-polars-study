# H100 evidence — October 26, 2026

Both formal suites, CPU software-stack controls, and post-run checks are complete.
This archive is separate from the frozen A100 evidence. Start with the
[results overview](../../reports/H100_STUDY.md).

- `results/baseline/`: 20 sessions at SF1/10/30/100/300; 480 agreeing outputs
  and 120 validated Spark GPU final plans.
- `results/complex/`: 12 sessions at SF30/100/300; 264 agreeing outputs, 264
  successful-action cleanup barriers, 72 Spark GPU final plans and 48 Polars GPU
  plan artifacts containing 12 cached per-query lowerings. Six GPU warmups reject the original Q13 regex/window
  expressions; their cleanup barriers also pass. There is no complete GPU six-query score.
- `results/polars2-baseline-control/` and `results/polars2-complex-control/`:
  eight CPU sessions, 192 agreeing outputs and 72 complex cleanup barriers.
  Frozen sources, files, threads and protocol match. Python and Polars change
  together, so this is a CPU software-stack control.
- `validation/`: initial content checks and the final five-scale check.
  `INPUT_VALIDATION_INDEX.json` binds each scale to its manifest SHA-256. All
  1,776 files (157,967,834,615 bytes) re-hash correctly after timing. SF30 physical
  layout differs from A100. Post-check missing-reference warnings at SF10/SF30
  are covered by earlier dbgen checks and unchanged file hashes.
- `preflight/`: fresh SF1 smoke: 74 PASS, zero FAIL/WARN, two unsupported XFAILs;
  92 successful outputs, 24 Spark GPU plans and 44 complex barriers validate.
  These one-trial smoke timings are not repeatability estimates.
- `source-snapshots/`: exact preflight, formal, post-check and reporting sources.
  Formal source is commit `c4c934cf732b4394e2bcb0547bb642ee2a1ae5d4`. Earlier
  development is in `artifacts/source-snapshots/h100-recovered-20261026/` at repo root.
- `operations/` and `incidents/`: sequencing scripts and the untimed post-check's
  first import failure. The missing copied dependency was added before the
  successful check. No timed run was repeated or altered.

There are 936 successful formal outputs and zero mismatches or integrity
violations. The separate smoke contributes 92 successful outputs. Three trials
and cross-engine agreement do not establish statistical significance or an
independent full-scale SQL oracle. Startup and explicit cleanup are outside
query scores. GPU nodes establish operator selection, not runtime fractions;
Spark optimizes away the correlated anti branch.

Both primary Polars modes use 1.44.2/Python 3.11. The CPU 2.0.0/Python 3.10 control
is necessary context: it wins every aggregate comparison against GPU Spark,
while Polars GPU retains its baseline advantage and four supported complex-query
wins. Primary charts explicitly label the matched 1.44.2 comparison.

From the repository root, verify the dated archive:

```bash
shasum -a 256 -c evidence-h100/20261026/SHA256SUMS
```

Checksum scope includes this archive, dated H100 reports and the recovered source
snapshot. SSH credentials, environments, bulk Parquet, dependency JARs and raw
Spark event logs are excluded. The [runbook](../../docs/H100_RUNBOOK.md) records
commands and scope.
