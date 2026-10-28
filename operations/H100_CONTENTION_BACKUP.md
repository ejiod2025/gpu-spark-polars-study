# Separate contention raw backups

`contention_backup.py` preserves raw evidence at an idle driver boundary. It
never invokes, retries or restarts an engine, changes original result bytes,
or asserts that the isolated 400-session study has finished. Its schema is
`h100-contention-offhost-backup-v1`, with the separate contention plan SHA256.

Deploy both `contention_backup.py` and the unchanged `research_backup.py` beside
it in the new contention staging directory. The latter supplies existing
safe-path and bounded, owned-process transport primitives. Keep source Parquet,
JARs, `.env`, private keys, virtual environments, symlinks and special files out
of the evidence root. Run the new driver/workers with
`PYTHONDONTWRITEBYTECODE=1` so their archived source trees do not accumulate
runtime `__pycache__` files. The helper refuses excluded files; it never silently
filters original evidence to improve a result.

After a phase has exited and all workers have drained, the driver writes the
plain phase name (`pilot`, `main` or `failed`) to
`EVIDENCE/WAITING_FOR_OFFHOST_BACKUP`, then invokes:

```sh
python -B STAGING/contention_backup.py snapshot \
  --evidence EVIDENCE --snapshot-root STAGING/backups --phase pilot
```

The snapshot requires no benchmark workers or GPU compute clients. Successful
pilot/main boundaries also require the actual matching terminal `PHASE.json`
with status PASS. `failed` is for preserved diagnostic outcomes and makes no
scientific PASS assertion, even if a crashed phase still says RUNNING. The
snapshot includes every regular original file, records all hashes/sizes, hashes
the exact bytes passed to tar, and checks the complete inventory again. It
writes immutable `pilot.tar` and `pilot.json` outside the evidence tree. A
failure retains partial bytes and never publishes a successful control. Existing
snapshot files are never overwritten.

Start the local watcher before releasing the new contention driver:

```sh
/tmp/h100-analysis-audit-venv/bin/python -B operations/contention_backup.py watch \
  --host ubuntu@68.209.73.247 \
  --identity-file /Users/danielejiofor/.ssh/lambda_key22 \
  --evidence /home/ubuntu/study-h100-contention-evidence-20261028 \
  --snapshot-root /home/ubuntu/study-h100-contention-staging-20261028/backups \
  --destination rerun/contention-20261028/raw-backups \
  --plan-sha256 ACTUAL_FROZEN_CONTENTION_PLAN_SHA256 --interval-seconds 30
```

A waiting marker with no published control is **pending**; it is not a failed
experiment. Network subprocesses have bounded timeouts and only their own
transport process groups may be reaped. Resumable rsync transfers use a partial
pathname; the helper independently checks the complete tar's SHA256 and size
before promoting it. Duplicate/traversing archive names, nonregular members,
wrong manifests and changed extracted bytes are rejected. All original archive,
control and extracted files remain off-host.

Only after full verification does the watcher atomically create
`EVIDENCE/backup-receipts/PHASE.json`, with:

- `schema_version: h100-contention-offhost-backup-v1`;
- exact `phase`, `plan_sha256`, `tar_sha256`, `tar_bytes`, `manifest_sha256`, `files`;
- `status: OFFHOST_SHA256_VERIFIED` and actual `verified_at_utc`.

The driver must compare these fields to its actual control before advancing.
The watcher will not acknowledge a different plan, altered local receipt, changed
remote control or an unverified tree. It exits after main or failed ACK; after
pilot ACK it keeps watching. An integrity exception stops for diagnosis; it does
not restart a measurement or fabricate a receipt. A second watcher cannot hold
the same local destination lock.

Individual `pull`, `ack`, and offline `verify` subcommands expose the same checks.
Use `--help` for their exact arguments. These receipts are transport provenance,
not a replacement for independent SQL, overlap, configuration, cleanup,
statistical, paper/publication or exact-instance retirement gates.
