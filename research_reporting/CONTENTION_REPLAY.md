# Offline replay of the separate contention role

`contention_replay.py` reads saved evidence only. It never connects to the H100,
starts a worker/JVM, reads source Parquet, changes raw JSON, publishes a page or
terminates an instance. Its outcomes remain separate from the isolated
400-session study and its 26 primary comparisons.

The only accepted genuine contention identity is the actual 66-file prestart at
`evidence-research/20261028/h100-contention-v1`, with plan SHA256
`670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031`
and prestart manifest SHA256
`4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25`.
Unknown identities and synthetic replacements have no acceptance override.

## Phase-boundary checks while collection continues

Use the existing local analysis environment and a new output directory:

```sh
cd /Users/danielejiofor/AIRFLOW/gpu-spark-polars-study
/tmp/h100-analysis-audit-venv/bin/python -B research_reporting/contention_replay.py \
  --archive evidence-research/20261028/h100-contention-v1 \
  --raw-backups rerun/contention-20261028/raw-backups \
  --output rerun/contention-20261028/new-contention-replay
```

For every available `pilot`, `main` or `failed` snapshot, retain its original
`.tar`, `.json`, `.verified.json` and extracted directory. The tool verifies the
tar bytes/size, safe extracted inventory, every original file and all 66 frozen
prestart bytes through the contention transport helper. Historical verification
receipts bind their original absolute off-host locations. Do not edit them to
relocate a copy; preserve originals and create a separately verified retrieval
record when moving evidence.

The latest full snapshot already contains earlier phases. Completed `pilot/`
or `main/` bytes must remain identical across later snapshots. The canonical
prestart remains separate. The auditor's output must be outside every input
tree and must not exist already. Failed reports and their original inputs stay
intact; a reporting repair uses a new output directory and no engine rerun.

After verifying source bytes, the auditor invokes the archived contention
validator with an explicit **in-memory** source/oracle lookup adapter. It
rechecks all available SQL outputs, runtime/configuration identities, actual
Spark driver flags, strict unsupported outcomes, plans and cleanup. It also
independently verifies the two worker claims, setup/action readiness files,
shared release records, per-participant event coverage and original action
timer boundaries. Common measured overlap is recomputed on both the same-host
monotonic and UTC clocks. A launched pair or overlapping process lifetimes is
not sufficient evidence. OOM/CUDA failures cannot count as expected strict
translation rejection.

All original action rows remain available in the descriptive export. The two
CPU-only measured `cx_q13`/`cx_window` diagnostics in pair-B complex waves are
marked `paired_condition:false`. They must not be summed into a uniformly
contended six-query score. Pilot and expansion rows are not pooled for a new
confirmatory claim; there are no new p-values, winner decisions or thresholds.

## Actual terminal disposition and original-study separation

A successful main phase alone is insufficient for a terminal publication-ready
audit. Preserve these actual completion inputs after their real creation:

- A final metadata directory containing the actual `PHASE_DRIVER.json` and all
  actual remote `backup-receipts/{pilot,main,failed}.json`. The source snapshot
  predates its own ACK and the driver's later terminal update. Preserve those
  archived bytes; do not replace them with the final metadata.
- The complete final handoff directory, including `CONFIG.json`, `STATE.json`,
  `RESUMED.json`, both profile checks, original-runner exit/result/schedule proof,
  original attempt inventory, owned-group and residual-process checks, driver
  spec and logs. A valid same-scheduler resume is mandatory.
- The exact held original journal and session record already preserved at
  `evidence-research/20261028/h100-contention-held-original`. These bytes must
  match the handoff's frozen hashes and original planned first attempt.
- The original paused attempt from the later immutable block-04 raw backup.
  Every original worker payload file must retain its pre-contention hash. Only
  the original C84 parent's normal `session.json` finalization and a newly saved
  `SCIENTIFIC_VALIDATION.json` are allowed. This adapter never edits either.

Set the metadata paths below to their actual off-host copies:

```sh
/tmp/h100-analysis-audit-venv/bin/python -B research_reporting/contention_replay.py \
  --archive evidence-research/20261028/h100-contention-v1 \
  --raw-backups rerun/contention-20261028/raw-backups \
  --final-metadata /absolute/actual-final-driver-metadata \
  --handoff /absolute/actual-final-handoff \
  --held-original evidence-research/20261028/h100-contention-held-original \
  --original-attempt rerun/gclocker100-20261027/raw-backups/block-04/campaign-results/block-04-complex-sf30-spark_cpu/attempt-001 \
  --output rerun/contention-20261028/new-terminal-contention-replay
```

The auditor verifies the recorded pre/post source/runtime/idle checks and all
five original input-byte gates totaling 1,654 files. It does not independently
rehash missing source Parquet off-host: these are hash-bound VM observations,
plus the unchanged manifest/profile identities. Actual original worker action
and host-result endpoints must finish before any contention action; contention
must finish before the verified profile check and actual same-PID resume.
The original parent's outer orchestrator envelope includes the scheduling hold,
and is never rewritten or interpreted as a query latency.

`COMPLETE` requires all four pilot and sixteen expansion waves, their genuine
460 oracle-valid outputs and ten strict rejections, both off-host phase ACKs,
terminal driver metadata and the verified handoff. `STOPPED_WITH_FAILURE`
requires an actual terminal phase failure, its raw failed snapshot, driver
failure and the same separation/resume proofs. All attempted partial outputs
are checked; failed or unattempted actions receive no imputed times. A driver
operational failure without a terminal scientific phase disposition stays
nonterminal pending explicit review. Missing final driver, handoff, held bytes
or original attempt proof can never become a terminal result.

## Receipt contract

`CONTENTION_REPLAY.json` has schema `h100-contention-replay-v1` and records:

- Exact experiment/plan/prestart/reference campaign/profile identities,
  canonical directory and `{path,sha256}` canonical plan/prestart references.
- `status: PARTIAL | COMPLETE | STOPPED_WITH_FAILURE | FAIL`, independent
  `integrity_status`, `publication_ready`, findings, source hash and local
  analysis runtime. Terminal status requires the actual handoff proof.
- `input_files_sha256` for every actual scientific/transport/tool input read,
  immutable snapshot refs and selected phase dispositions. A partial snapshot's
  absent future phases are labeled unobserved; terminal stop explicitly lists
  their unattempted fixed-prefix wave IDs.
- Actual wave revalidation, all available output comparisons, strict rejection
  versus unexpected error accounting, barrier/action overlap and handoff refs.
  Handoff refs include the held journal/session, original inventory/results/
  schedule, before/after profile, same scheduler resume and residual scans.
- Terminal driver and actual remote ACK byte references, with no fabricated
  acknowledgement for an unattempted phase.

`PATH_ADAPTER.json` lists only derived source/oracle lookup changes;
`QUERY_ACTIONS_DESCRIPTIVE.csv` retains query endpoints and paired/unpaired flags;
`COMMON_QUERY_OVERLAP.csv` retains both-clock overlap and actual start skew.
`AUDITOR_SOURCE.py` preserves the exact auditor source used, and output hashes
exclude the receipt itself to avoid a hash cycle. These private audit outputs
are not a deployment, paper review or original-400 completion permit. The
retirement guard requires this role **in addition to** the original final
study, publication, private preservation and exact-instance identity gates.

Focused tests use visible fabricated lifecycle/barrier boundaries, and the
committed genuine prestart solely for read-only source seal checks:

```sh
/tmp/h100-analysis-audit-venv/bin/python -B -m pytest -q \
  research_reporting/tests/test_contention_replay.py
```
