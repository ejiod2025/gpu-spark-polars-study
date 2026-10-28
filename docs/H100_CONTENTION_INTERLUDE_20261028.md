# Authorized concurrent-workload interlude

Prepared prospectively on 2026-10-28 UTC, before any scheduler hold or concurrent
workload. The owner explicitly selected: “Run concurrent sessions on this H100
as a contention experiment.” This adds a separate experiment to the ongoing
isolated study; it does not cancel its 400 sessions or pool their observations.

## Execution and separation

Use the existing replacement H100 and the same unchanged C84 source, installed
environments, JVM profile, Parquet files and SQL references. No additional cloud
instances are requested. Concurrent pairs deliberately share CPU, host memory,
disk/cache and GPU-related host work. They estimate behavior under that shared
load, not isolated engine latency or a replacement primary contrast family.

The separate contention protocol and immutable pilot plan must be preserved
before running a pair. The initial four waves pair Spark GPU with CPU Polars
2.0, and Spark CPU with strict Polars GPU, for baseline and complex SF30 suites.
Each process retains its declared full CPU budget. Only one Spark JVM and one
GPU execution path run in each pair. Any later expansion is separately planned
and gated on the pilot's actual correctness, cleanup and observed overlap.

The isolated campaign must not execute a measured worker concurrently with
these pairs. A dedicated handoff helper may suspend **only** its exact owned
`research_campaign.py` scheduler, preserving its process, locks and journal.
An already launched `run_experiment.py` and all its workers continue normally.
Before starting contention, require that the original runner finished with
successful schedule/results, its worker groups are gone and the GPU has no
compute clients. A stopped scheduler is not a failed experiment or permission
to restart an engine. Do not suspend the runner, worker, process group or
supervisor.

This fast handoff can leave the original journal's current attempt marked
running until its original scheduler resumes. Its original worker action and
host-result endpoints finish before the interlude; they are never rewritten.
The scheduler's later `ended_at_utc` and `orchestrator_wall_seconds` include
the hold. Preserve and disclose that outer-envelope inflation explicitly.

## Evidence and return to isolated collection

Record exact scheduler PID, creation time, command, working directory, parent,
campaign hash, journal hash and original child identity before a signal. Save
intent, actual hold, original-child completion, contention execution and resume
receipts. The helper owns any new contention processes and must not resume
isolated scheduling while a contention worker remains active. A bounded
guardian must distinguish a live handoff from an abandoned one; uncertainty
requires preserved diagnosis, never an unverified signal to a reused PID.

After contention, verify no concurrent worker remains, allow an explicit idle
interval, and recheck source, runtimes, profile and all input bytes outside
measurement. Keep these interlude checks in separate files. Do not append to
or relabel the formal 24-record execution-profile log. Resume the **same**
owned scheduler PID; do not create a new campaign, replace an attempt or change
the frozen schedule. Preserve all original result/journal bytes and all failed
contention outcomes.

Actual per-action timestamps establish overlap and start skew; a launched pair
alone does not prove overlapping query execution. Any rendezvous waits stay
outside the original action timers and remain visible in wall-time and sampled
resource evidence. Strict unsupported expressions have no imputed timings.
Contention estimates and isolated estimates remain separately labeled, with no
post-hoc significance or causal claim from this initial experiment.

The full research paper and public webpage must disclose the requested
interlude and report the actual separate contention disposition, including
failures, instrumentation, resource settings and limitations. Existing final
400-session replay/publication/identity/retirement gates remain required. The
H100 is retired only after both authorized deliverables and all preservation
and publication checks are complete.
