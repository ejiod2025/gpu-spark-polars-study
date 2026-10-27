# H100 research campaign status

Update: **2026-10-27**. The owner reported that the previous H100 instance is
gone. A replacement instance is reachable and a fresh campaign is being prepared.
No confirmatory performance conclusion is available yet. See the
[prospective replacement addendum](../docs/H100_REPLACEMENT_20261027.md).

Replacement preparation snapshot at **2026-10-27 15:05 UTC**: all ten SF1
readiness sessions passed with **116 output comparisons** and all five
configurations completed semantic diagnostics. Linux checks passed **300 tests
and 16 subtests**, with one fixture-dependent skip. SF1/SF10/SF30 are rebuilt;
SF100 is generating. No confirmatory timing has started. See the
[replacement preparation snapshot](h100-replacement-preparation-status.json).

The durable surviving timed evidence is blocks 01–03: **120 sessions and 2,808
successful outputs** with locally reverified core evidence. Their raw telemetry,
events and complete logs were not in these core backups. No final input hash or
complete ten-block result exists for the lost campaign. Its last readable live
observation reached 132 scientific PASS sessions; artifacts beyond the three
backed-up blocks have not been recovered. The earlier
[machine-readable snapshot](h100-confirmatory-launch-status.json) is retained as
historical evidence rather than overwritten as a new live status.

The replacement starts all **400 sessions** afresh. It will not combine old and
new host timings. A new prestart archive must be preserved before measurement,
and each complete block will be copied off host with all raw artifacts while
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
- The complete schedule and 52-file source snapshot were committed before
  the first timed session, in commit `b7a5f99`.

The previous remote driver PID **64491** belongs to the lost instance and must
not be used to infer replacement liveness. Replacement preparation is detached
from SSH, records its own PID, phase, logs and exit status, and stops before the
first confirmatory timing for an off-host prestart archive gate. Local
caffeinate remains active.

Remaining work is full data collection, final validation, ten-block uncertainty
and Holm-adjusted comparisons, reproducible figures, the completed manuscript,
and an artifact/claim audit. Repository visibility remains private.

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
