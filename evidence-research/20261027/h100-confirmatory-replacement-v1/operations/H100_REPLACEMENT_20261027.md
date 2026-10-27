# Replacement H100 campaign: prospective operational addendum

The owner reported that the October 26 instance was lost and authorized a new
instance on October 27. The earlier campaign is incomplete. Its immutable
prestart archive and three complete core checkpoints survive in the private
repository: 120 sessions and 2,808 successful output comparisons. Later live
observations are not equivalent to surviving raw artifacts. No final input hash
or complete raw telemetry archive survives for that campaign. Keep its evidence
and identity unchanged; do not pool its timings into the replacement campaign.

The replacement is a fresh 400-session campaign under a new campaign ID, evidence
directory, input manifests, runtime inventory, readiness reports and prestart
hash. It uses the same frozen experimental source at commit
`c84ef6a07341f350dae6636ab709c1b65fd894e0`, the same ten-block design and seed
20261026, the same endpoints, queries, settings, three inner measurements and
26-comparison primary family. There is no data-dependent sample-size decision.

Initial replacement inventory: NVIDIA H100 80GB HBM3, 26 Xeon Platinum 8480+
vCPUs, 237,490,843,648 bytes of RAM, driver 580.105.08 and Linux
6.8.0-1046-nvidia. The lost instance had driver 570.148.08 and a different kernel.
This is a separate host cohort. Regenerated Parquet files receive new physical
file hashes and all engines share those same new files. The previous file layout
and host environment are not assumed identical.

All five configurations repeat SF1 readiness and adversarial semantic fixtures.
All five scales are regenerated with the pinned DuckDB recipe, then all eight
independent SQL oracle outputs are produced before planning. The new complete
plan, exact source snapshot, readiness, manifests and oracles must be copied off
the instance and committed privately before the first confirmatory session.

To prevent another instance loss from erasing all raw evidence, the operational
supervisor pauses after each full 40-session block. While no engine session is
running it snapshots every raw file for that block, including logs, telemetry,
events, plans, outputs and scientific validation. The next block requires an
off-host receipt tied to the exact campaign and snapshot hashes. Transfers do
not overlap measured queries. Backup pauses and receipt times remain recorded.
This changes operational pacing and is disclosed; it does not change the order,
sample size, expressions or timing boundaries of the frozen experiment.

The frozen executor performs full byte verification on each resume. After the
final block a no-pending-session invocation must produce a true post-final hash
and campaign completion marker before final validation and analysis. Additional
integrity gates are retained rather than mistaken for new observations.

No successful session is rerun. A failed or interrupted attempt stops collection
and remains preserved for explicit deviation handling. The final paper must
describe the lost campaign, the fresh replacement, the pacing amendment and
remaining limits. Development and all evidence remain private.
