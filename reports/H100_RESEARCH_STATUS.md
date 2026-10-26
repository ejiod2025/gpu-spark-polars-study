# H100 research campaign status

Snapshot: **2026-10-26 18:28 UTC**. Data collection is in progress. No
confirmatory performance conclusion is available yet.

The first **5 of 400 sessions** have passed their scientific gates. They cover
all five configurations at baseline SF1, with **120 successful outputs**
independently compared. The next session is CPU Spark on complex SF300.
Both initial full-file integrity gates passed all **1,776 Parquet files**.
See the [machine-readable snapshot](h100-confirmatory-launch-status.json).

The fixed design is ten paired blocks across the original five baseline and
three complex scales. Four engine paths plus the CPU Polars version control
use the same CPython 3.11.13 executable/build. Source code, configuration,
oracles, inputs and execution order are frozen. Individual failed/unsupported
cases remain visible; a partial GPU suite never gets a complete-suite score.

Completed preparation:

- H100 Linux functional tests: **300 passed, one fixture-dependent skip,
  16 subtests passed**. Static reporting tests: **16 passed locally**.
- SF1 engine readiness: ten sessions and **116 agreeing output comparisons**.
- Hand-checked join diagnostics pass on all configurations, including a
  nonempty anti exclusion. The saved Spark GPU final plan has `LeftAnti` on a
  GPU node. This is a tiny semantic diagnostic, not an anti-join speed claim.
- Independent DuckDB reference outputs exist at all eight suite/scale cells.
- A later oracle audit of the exploratory pilot compares **936 saved outputs
  across 40 sessions**, with zero mismatches. Those timings remain exploratory.
- The complete schedule and 52-file source snapshot were committed before
  the first timed session, in commit `b7a5f99`.

The live remote driver is PID **64491** on the H100 host. It runs a fixed full
sample, halts on failures, retains attempts, and performs final input hashing,
independent validation and predefined analysis after all sessions finish.
The shell is detached from SSH; local caffeinate prevents idle sleep.

Remaining work is full data collection, final validation, ten-block uncertainty
and Holm-adjusted comparisons, reproducible figures, the completed manuscript,
and an artifact/claim audit. Repository visibility remains private.

[Prospective protocol](../docs/H100_CONFIRMATORY_PROTOCOL.md) ·
[runbook](../docs/H100_RESEARCH_RUNBOOK.md) ·
[pre-start archive](../evidence-research/20261026/h100-confirmatory-v1/README.md) ·
[semantic audit](../docs/H100_SEMANTIC_AUDIT.md) ·
[report renderer](../research_reporting/README.md)
