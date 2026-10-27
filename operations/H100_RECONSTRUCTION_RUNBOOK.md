# Offline replacement evidence reconstruction

Run only after the existing controller has preserved all 400 recovery
sessions and the actual supervisor has exited zero. This operation never
contacts the VM, reads source Parquet, executes a query or changes old archives.
The October 26 lost-instance campaign and the aborted October 27 v1 campaign
remain separately preserved; neither contributes observations to recovery. An absent final or
completion backup is a hard stop, not permission to manufacture a PASS.

The controller's local backup directory must contain each of `prestart`,
`block-01` through `block-10`, `final` and `completion`, with its `.tar`, `.json`,
`.verified.json` and extracted directory. Keep every original snapshot. The
completion snapshot contains the remote prestart/block/final acknowledgements;
it predates its own acknowledgement, which the watcher need not save locally.

Use the existing analysis interpreter and a new output directory:

```sh
cd /Users/danielejiofor/AIRFLOW/gpu-spark-polars-study
RECON_PYTHON=/tmp/h100-analysis-audit-venv/bin/python
RECON_CANONICAL="$PWD/evidence-research/20261027/h100-confirmatory-gclocker100-v1"
RECON_RAW="$PWD/rerun/gclocker100-20261027/raw-backups"
RECON_OUTPUT="$PWD/rerun/gclocker100-20261027/reconstruction-v1"
"$RECON_PYTHON" -B operations/reconstruct_h100_replacement.py \
  --cohort gclocker100 --archive "$RECON_CANONICAL" --raw-backups "$RECON_RAW" \
  --output "$RECON_OUTPUT"
```

The exact canonical campaign/prestart identity must already be registered.
Reconstruction checks the acknowledged Git commit contains both canonical
bytes and the registered seal, with the prestart receipt preceding timing.
Every actual tar is safely extracted into temporary storage and verified with
the current backup helper; existing extracted trees and reports are independently
checked. The current reporting verifier checks the canonical prestart archive.
Allow temporary disk space for one additional final snapshot plus the harvest.

`harvest/` combines canonical prestart, final and completion bytes. All ten
block session inventories must exactly match final. Overlapping immutable files
must match. Only `confirm-driver.log` and `EXECUTION_PROFILE_GATES.jsonl` (append preserving
their prefixes), plus `confirm-driver.phase`, may advance. Recovery requires the
registered `EXECUTION_PROFILE.json` sealed in prestart, the 24 ordered idle-host
Java/environment receipts bracketing readiness, every block and final completion,
and all 160 actual Spark driver logs with the fixed effective GCLocker100, 96g
heap and G1 settings. The Java executable, libjvm, module image and release
hashes in each receipt must match the profile. Missing or changed profile
evidence stops reconstruction. Preparation logs and all scientific data
remain immutable. The post-final journal must match completion, all 400 original
sessions must have one successful attempt, and the byte gates must bracket
timing. Completion must contain `confirm-driver.exit=0`, its end marker and
`ANALYSIS_READY`. Shell date markers retain their recorded one-second precision;
fractional Python timestamps are compared against that precision interval.

`lineage/` retains original transport manifests, controls, verification reports,
phase journals and the canonical seal outside the merged payload. The harvest
contains neither `BACKUP_MANIFEST.json` nor `PRESTART_MANIFEST.json`: their old
inventories would misdescribe advanced supervisor logs. The sibling
`RECONSTRUCTION_MANIFEST.json` records source hashes, exact per-file phase/hash
provenance, overlap decisions, tool/registry hashes and the new payload inventory.
Its status is `RECONSTRUCTED_TRANSPORT_VERIFIED`, which makes no scientific PASS
claim. Original absolute paths inside JSON are never rewritten.

Next, run the genuine offline replay into another new directory:

```sh
RECON_REPLAY="$PWD/rerun/gclocker100-20261027/replay-v1"
"$RECON_PYTHON" -B research_reporting/replay.py \
  --archive "$RECON_CANONICAL" \
  --artifacts-root "$RECON_OUTPUT/harvest/campaign-results" \
  --output "$RECON_REPLAY"
```

Replay runs the unchanged frozen validator/analyzer and the separate
[complete audit](../research_reporting/COMPLETE_AUDIT.md). Require actual PASS
manifests for this exact campaign and final journal. Preserve failed reports;
never rerun a measured engine session to repair a reporting failure. Replay's
path adapter only changes derived validation provenance; raw JSON stays intact.

After replay and its complete audit PASS, package the raw harvest with an
explicit reference to the separate canonical prestart:

```sh
RECON_BUNDLE="$PWD/rerun/gclocker100-20261027/bundle-v1"
"$RECON_PYTHON" -B research_reporting/package_evidence.py \
  --evidence "$RECON_OUTPUT/harvest" \
  --prestart-archive "$RECON_CANONICAL" \
  --audit-complete "$RECON_REPLAY/complete-audit/AUDIT_COMPLETE.json" \
  --output "$RECON_BUNDLE"
```

Follow [packaging verification](../research_reporting/PACKAGING.md): verify every
part, joined archive and extracted file in a fresh location; retain canonical
prestart, reconstruction lineage/manifest, replay, audit, figures and bundle
records as separate linked evidence. Large tar parts stay outside Git. Inspect
the final paper credited to Daniel Ejiofor and public webpage against those
actual outputs before release. Only after all required evidence, derived
artifacts, paper and page are preserved and their backups verified should the
existing exact-instance shutdown guard be considered. This tool neither
publishes nor shuts down the H100.

The bounded `--cohort replacement` selector permits historical v1 transport
inspection only. The default is `gclocker100`; arbitrary campaign IDs are refused.
Historical registration cannot satisfy the final recovery paper/page gates, and
the aborted v1 evidence cannot satisfy the 400 single-attempt requirement.

Focused tests use visibly fabricated snapshots and a temporary mock registry;
they cannot establish that the real replacement campaign has completed:

```sh
"$RECON_PYTHON" -B -m pytest -q operations/tests/test_reconstruct_h100_replacement.py
```
