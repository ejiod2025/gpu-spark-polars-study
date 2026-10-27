# Replacement H100 evidence backups

These operational tools preserve the replacement campaign separately from the
lost October 26 instance. They use the unchanged C84 benchmark source, all 400
fresh sessions and the frozen analysis. The supervisor runs 40 new sessions at
a time, then copies raw evidence while every benchmark engine is stopped.
The next block waits for an exact, locally verified backup receipt. The frozen
executor rehashes inputs on each invocation, outside engine measurements.
Record this block-boundary backup procedure in the replacement addendum before
timing. It changes neither query expressions nor the fixed sample.

Preparation must finish with `prepare.exit=0` and `READY_TO_ARCHIVE_PLAN`.
`run_h100_replacement.sh` records its PID/start/phase/exit markers and refuses
an automatic restart of an already started supervisor. Run it detached using
the existing launch procedure; never restart it merely because SSH times out.
It waits for prestart acknowledgement before creating the campaign journal.

Default remote paths:

- Source: `/home/ubuntu/study-h100-research-c84ef6a`
- Evidence: `/home/ubuntu/study-h100-research-evidence-20261027`
- Snapshots: `/home/ubuntu/study-h100-research-staging-20261027/backups`
- Tool: the deployed `research_backup.py` beside the supervisor script

`RESEARCH_SOURCE`, `RESEARCH_EVIDENCE`, `RESEARCH_BACKUP_ROOT`,
`RESEARCH_BACKUP_TOOL`, `RESEARCH_ENV` and `RESEARCH_ANALYSIS_PYTHON` can override
those paths. Do not override an existing campaign's bound input/runtime paths.

## Prestart archive and manual acknowledgement

Use these local values with the actual newly frozen campaign hash:

```bash
BACKUP_REPO=/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study
BACKUP_PYTHON=/tmp/h100-analysis-audit-venv/bin/python
BACKUP_HOST=ubuntu@68.209.73.247
BACKUP_KEY=/Users/danielejiofor/.ssh/lambda_key22
BACKUP_REMOTE=/home/ubuntu/study-h100-research-staging-20261027/backups
BACKUP_EVIDENCE=/home/ubuntu/study-h100-research-evidence-20261027
BACKUP_RAW="$BACKUP_REPO/rerun/replacement-20261027/raw-backups"
BACKUP_CANONICAL="$BACKUP_REPO/evidence-research/20261027/h100-confirmatory-replacement-v1"
BACKUP_CAMPAIGN_SHA=REPLACE_WITH_ACTUAL_64_CHARACTER_CAMPAIGN_SHA
```

The supervisor creates `prestart.tar` and `prestart.json` outside evidence,
then waits. Download and verify them:

```bash
"$BACKUP_PYTHON" "$BACKUP_REPO/operations/research_backup.py" pull \
  --host "$BACKUP_HOST" --identity "$BACKUP_KEY" \
  --remote-snapshot-root "$BACKUP_REMOTE" --phase prestart \
  --destination "$BACKUP_RAW" --campaign-sha256 "$BACKUP_CAMPAIGN_SHA"
```

Keep tar files outside Git. Copy the verified `prestart/` directory once to the
new canonical archive location, then seal it:

```bash
cp -R "$BACKUP_RAW/prestart" "$BACKUP_CANONICAL"
"$BACKUP_PYTHON" "$BACKUP_REPO/operations/research_backup.py" seal-prestart \
  --archive-directory "$BACKUP_CANONICAL" --control "$BACKUP_RAW/prestart.json"
```

Create the canonical parent directory first if absent; refuse an existing
canonical archive rather than copying into it. The transfer's
`BACKUP_MANIFEST.json` hashes the original files. The local
`PRESTART_MANIFEST.json` hashes every other canonical file, including the
transfer manifest. The transfer manifest never hashes the later seal, avoiding
a checksum cycle. No extra files may be added after sealing.

Register the actual campaign/PRESTART manifest SHA pair in
`research_reporting/campaign_identities.json` using the reporting audit's
instructions. Verify the archive with the reporting tools. Force-add the
complete canonical archive so ignored readiness logs are preserved; commit
the registry and archive and push the private branch before acknowledgement.
Capture that full commit SHA as `BACKUP_PRESTART_COMMIT`.

```bash
"$BACKUP_PYTHON" "$BACKUP_REPO/operations/research_backup.py" ack \
  --host "$BACKUP_HOST" --identity "$BACKUP_KEY" \
  --remote-snapshot-root "$BACKUP_REMOTE" --evidence "$BACKUP_EVIDENCE" \
  --phase prestart --destination "$BACKUP_RAW" \
  --campaign-sha256 "$BACKUP_CAMPAIGN_SHA" \
  --archive-directory "$BACKUP_CANONICAL" \
  --git-repository "$BACKUP_REPO" --git-commit "$BACKUP_PRESTART_COMMIT"
```

Acknowledgement rehashes all copied files and proves their exact Git blobs
exist in that commit. The remote receipt binds the campaign, tar, transfer
manifest, canonical prestart seal and commit. It authorizes collection only
after that evidence exists off the GPU instance. The automatic watcher never
acknowledges prestart.

## Automatic block backup watcher

After the manual prestart acknowledgement, run the watcher as a persistent
local process with its own PID/log. Keep the Mac awake. It does not start,
restart, kill or rerun a remote benchmark:

```bash
"$BACKUP_PYTHON" "$BACKUP_REPO/operations/research_backup.py" watch \
  --host "$BACKUP_HOST" --identity-file "$BACKUP_KEY" \
  --remote-snapshot-root "$BACKUP_REMOTE" --evidence "$BACKUP_EVIDENCE" \
  --destination "$BACKUP_RAW" --campaign-sha256 "$BACKUP_CAMPAIGN_SHA" \
  --remote-tool /home/ubuntu/study-h100-research-staging-20261027/research_backup.py \
  --interval-seconds 60
```

The watcher locks its local destination against duplicate watchers, records
`watcher.jsonl`, and checks campaign identity before transfer. Each block's tar
contains all 40 sessions, including raw orchestrator logs, event logs, telemetry,
plans, session metadata, result rows, schedules, environments and scientific
gates. It also contains the original journal snapshot and frozen plan.
Snapshot collection rejects live sessions, retries, missing gates, symlinks,
Parquet, jars, virtual environments and credential files. Scientific
completeness still requires the independent validator and complete audit.

Files per phase are `PHASE.tar`, `PHASE.json`, `PHASE.verified.json`, and an
extracted `PHASE/` directory. All are under ignored `rerun/` storage locally.
Only verified receipts advance the remote supervisor. Keep control/receipt
manifests in the private repository as checkpoints; large raw tars belong in
private durable artifact storage rather than Git history.

SSH control calls have 30-second deadlines. Data transfer uses resumable rsync
partials, a 30-second I/O idle timeout and a 600-second attempt wall limit.
Owned network subprocess groups are terminated/reaped on timeout. Connection
failures are logged and retried at the next poll; scientific/identity/hash
failures stop the watcher for investigation. Interrupted extraction uses a
temporary directory; only a completely verified tree is atomically published.
An altered already published copy is rejected, never silently replaced.

## Final integrity and completion metadata

The supervisor calls the frozen executor once more after block 10 with no new
sessions pending. This is necessary because `--max-sessions 40` breaks before
the executor's loop-else post-run hashing. It creates the true post-final-action
integrity gate, then independent validation and predefined analysis run.

`final.tar` includes all 400 raw sessions, final journal/validation/analysis,
and then-available root metadata. It precedes the supervisor's EXIT trap, so
it does **not** by itself establish the actual successful supervisor exit.
After acknowledging it, the watcher waits for `confirm-driver.exit=0` and the
supervisor process to disappear. It then requests a separate `completion`
snapshot, downloads/verifies it, acknowledges it and exits successfully.
That snapshot preserves actual exit/end markers, closed supervisor logs,
receipts, final journal and analysis. A nonzero exit stops the watcher.

For final replay/packaging, reconstruct a new evidence directory using the
prestart files, final raw-session snapshot, and completion metadata. Verify
every source snapshot first. Overlapping immutable files must match exactly.
Operational root logs/phase/exit files may advance only from final to completion;
record each replacement's old/new hash and source snapshot explicitly. Never
overwrite original phase archives. Omit transport-only `BACKUP_MANIFEST.json`
from the reconstructed evidence root; retain every manifest beside its source
tar. This reconstruction remains subject to real portable replay, the complete
audit, final claim audit and final evidence packaging. Backup receipts alone
make no publication-readiness claim.
