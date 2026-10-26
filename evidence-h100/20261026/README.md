# H100 evidence — October 26, 2026

This directory preserves the H100 extension separately from the frozen A100 evidence.
The five-scale baseline campaign is complete and validated. The complex campaign is running.

- `validation/`: five-scale checks of actual Parquet files against their manifests and
  external logical-content references. `INPUT_VALIDATION_INDEX.json` links each scale to
  its manifest SHA-256 and validation report. SF30 physical file layout differs from A100.
- `preflight/`: SF1 baseline/complex outputs, plans, manifests, launch schedules,
  host/GPU telemetry, and the preflight report. Raw Spark event logs and operational logs
  are excluded; materialized results and structured errors are retained.
- `source-snapshots/`: exact preflight source and frozen formal source, with their
  provenance. The preflight runner preceded an additive installed-package inventory
  capture; all executed query/worker source matches the formal source.
- `results/baseline/`: all twenty completed engine sessions at SF1/10/30/100/300,
  each with one warmup and three measured trials. All 480 saved outputs agree and
  all 120 Spark GPU plans validate; there are no engine failures or integrity violations.

The baseline report and plots are in `reports/h100-baseline-20261026/` in the repository.
At SF300 the six-query median sums are 44.512 s Polars CPU, 12.774 s Polars GPU,
146.428 s CPU Spark, and 45.528 s GPU Spark. Polars GPU wins every individual baseline
query there; GPU Spark has no observed aggregate win through SF300. These are sums
of query medians, excluding startup, and three trials do not establish statistical significance.

Fresh preflight: 74 PASS, 0 FAIL, 0 WARN, 4 INFO, 2 XFAIL. Successful smoke outputs:
48 baseline and 44 complex, all matching within the original tolerances, with 24 validated
Spark GPU plans and 44 verified complex cleanup barriers. Polars GPU has 20 successful
query actions with captured cuDF physical plans. Its unsupported Q13 regex and window
expression produce explicit warmup errors and no timings. These are smoke observations
with one measured trial, not estimates of repeatability.

Formal engine source: `c4c934cf732b4394e2bcb0547bb642ee2a1ae5d4`. Both H100 Polars modes
use 1.44.2/Python 3.11, unlike A100's Polars 2.0.0/Python 3.10. Within-H100 comparisons
are primary; host and software differences prevent attributing cross-host ratios solely
to the GPU model. See `docs/H100_RUNBOOK.md` in the repository root for the protocol.

No SSH credentials, virtual environments, Parquet data, or dependency JARs are included.
