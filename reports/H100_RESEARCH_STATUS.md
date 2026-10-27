# H100 research campaign status

Update: **2026-10-27**. The owner reported that the previous H100 instance is
gone. The replacement instance is collecting a fresh confirmatory campaign.
No confirmatory performance conclusion is available yet. See the
[prospective replacement addendum](../docs/H100_REPLACEMENT_20261027.md).

Launch snapshot at **2026-10-27 16:17 UTC**: **5 of 400 sessions** have passed
their scientific gates, with **120 successful outputs** compared. One session
is running and no failed session is recorded. The first complete byte gate
passed all **1,654 replacement Parquet files**. See the
[replacement launch snapshot](h100-replacement-launch-status.json).

All five dataset scales and eight independent oracle cells are complete. The
358-file prestart archive, including the exact 52-file experimental source,
was verified off-host and pushed privately in commit
`b94372deebcf3503c115fd9cc396dec3666ce85b` before timing. Its
[canonical manifest](../evidence-research/20261027/h100-confirmatory-replacement-v1/PRESTART_MANIFEST.json)
binds the registered replacement campaign and all original bytes.

The historical preparation snapshot at **2026-10-27 15:05 UTC** recorded ten SF1
readiness sessions passed with **116 output comparisons** and all five
configurations completed semantic diagnostics. Linux checks passed **300 tests
and 16 subtests**, with one fixture-dependent skip. SF1/SF10/SF30 are rebuilt;
SF100 was then generating and confirmation had not started. See the
[replacement preparation snapshot](h100-replacement-preparation-status.json).

Local continuation controller PID **85571** completed the preparation/archive
gates and is watching block backups. Remote supervisor PID **21385** runs the
fixed campaign and waits between blocks for verified off-host raw receipts. Its current
state/events/log live under ignored `rerun/replacement-20261027/raw-backups/`.
See the [controller](../operations/continue_h100_replacement.py) and
[backup runbook](../operations/H100_BACKUP_RUNBOOK.md). Do not restart the remote
supervisor on an SSH timeout; inspect persistent markers and controller state.

The durable surviving timed evidence is blocks 01–03: **120 sessions and 2,808
successful outputs** with locally reverified core evidence. Their raw telemetry,
events and complete logs were not in these core backups. No final input hash or
complete ten-block result exists for the lost campaign. Its last readable live
observation reached 132 scientific PASS sessions; artifacts beyond the three
backed-up blocks have not been recovered. The earlier
[machine-readable snapshot](h100-confirmatory-launch-status.json) is retained as
historical evidence rather than overwritten as a new live status.

The replacement collects all **400 sessions** afresh. It does not combine old and
new host timings. Its new prestart archive is preserved before measurement,
and each complete block is copied off host with all raw artifacts while
the supervisor pauses between blocks. The fixed inference sample remains ten
paired blocks.

The fixed design is ten paired blocks across the original five baseline and
three complex scales. Four engine paths plus the CPU Polars version control
use the same CPython 3.11.13 executable/build. Source code, configuration,
oracles, inputs and execution order are frozen. Individual failed/unsupported
cases remain visible; a partial GPU suite never gets a complete-suite score.

Completed preparation:

- H100 Linux functional tests: **300 passed, one fixture-dependent skip,
  16 subtests passed**. Offline replay, audit, rendering and packaging tests:
  **70 passed locally**.
- SF1 engine readiness: ten sessions and **116 agreeing output comparisons**.
- Hand-checked join diagnostics pass on all configurations, including a
  nonempty anti exclusion. The saved Spark GPU final plan has `LeftAnti` on a
  GPU node. This is a tiny semantic diagnostic, not an anti-join speed claim.
- Independent DuckDB reference outputs exist at all eight suite/scale cells.
- A later oracle audit of the exploratory pilot compares **936 saved outputs
  across 40 sessions**, with zero mismatches. Those timings remain exploratory.
- The original cohort's schedule/source archive was committed in `b7a5f99`;
  the replacement's complete prestart archive was committed in `b94372d`.

The previous remote driver PID **64491** belongs to the lost instance and must
not be used to infer replacement liveness. The replacement's detached
supervisor records its own PID, phase, logs and exit status. Local caffeinate
remains active.

Remaining work is full data collection, final validation, ten-block uncertainty
and Holm-adjusted comparisons, reproducible figures, the completed manuscript,
and an artifact/claim audit. The owner requires the full research paper credited
to Daniel Ejiofor and a public shareable webpage before authorized instance
retirement. The quiet autonomous follow-up is active. See the
[completion instructions](../docs/H100_AUTONOMOUS_COMPLETION.md). Repository
visibility remains private.

Portable replay, a stronger postcollection audit and raw-evidence bundling are
implemented separately from the frozen experiment. The replay retains raw
validation and records its path-only adapter. Final configuration/chronology
checks, secondary-endpoint tables, figures and bundle hashes must still be
produced from the completed real harvest; synthetic tooling tests do not satisfy
those study gates.

[Prospective protocol](../docs/H100_CONFIRMATORY_PROTOCOL.md) ·
[runbook](../docs/H100_RESEARCH_RUNBOOK.md) ·
[pre-start archive](../evidence-research/20261026/h100-confirmatory-v1/README.md) ·
[semantic audit](../docs/H100_SEMANTIC_AUDIT.md) ·
[report renderer](../research_reporting/README.md)

[Completed block 01 core checkpoint](../evidence-research/20261026/block01-core-checkpoint/README.md)

[Completed block 02 core checkpoint](../evidence-research/20261026/block02-core-checkpoint/README.md)

[Completed block 03 core checkpoint](../evidence-research/20261026/block03-core-checkpoint/README.md)
