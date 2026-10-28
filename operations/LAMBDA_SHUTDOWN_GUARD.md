# Conditional termination of the replacement H100

The user authorized termination **after** the full paper and public shareable
webpage are complete and all results/raw backups are safely offhost. There is
no second approval prompt. `lambda_shutdown_guard.py` enforces those conditions;
creating or committing this tool does not authorize terminating an incomplete run.
No completion manifest is supplied with this tool. Completion is limited to
`h100-confirmatory-gclocker100-20261027`, registered only after its actual new
campaign, prestart and execution profile exist. Historical archive verification
remains available, but the lost original cohort and aborted replacement v1
cohort cannot permit termination, even when presented with a forged PASS.

The owner subsequently authorized the separate contention experiment. A
400-session-only completion manifest is now insufficient. Both the unchanged
isolated-study gates and the mandatory terminal contention evidence below must
pass. There is no skip flag or absent-role interpretation that ignores this
extension. This source change does not establish actual contention completion.

The fixed target is Lambda instance `8809d4738226458a80a100bb4eb2f906`, IP
`68.209.73.247`, type `gpu_1x_h100_sxm5`, physical GPU
`GPU-aa9e9485-5551-d7be-dfc9-9e433a7284da`, name `NVIDIA H100 80GB HBM3`, driver
`580.105.08`. Supply every expected identity field explicitly. Use the exact
`instance_type.gpu_description` from the authenticated read-only API, which is
separate from `instance_type.description`. The guard has no target override.

## Official Lambda semantics verified 2026-10-27

- [Lambda Cloud API](https://docs.lambda.ai/api/cloud) uses base
  `https://cloud.lambda.ai/api/v1`. Bearer API-key authentication is recommended;
  Basic remains compatible. `GET /instances` lists running instances;
  `GET /instances/{id}` retrieves exact details including non-running instances.
  `POST /instance-operations/terminate` accepts `{"instance_ids":["id"]}` and
  returns `data.terminated_instances`. The published lifecycle endpoints offer
  restart and terminate; no stop/suspend endpoint is documented.
- [Console termination instructions](https://docs.lambda.ai/public-cloud/console/#terminate-instances)
  warn that guest `shutdown`/`poweroff` puts an instance in Alert and billing
  continues. Use the control-plane terminate operation to release the instance.
- [ODC billing](https://docs.lambda.ai/public-cloud/billing/#on-demand-cloud-odc-instances)
  uses one-minute increments and ends at termination. Existing filesystems have
  separate charges while they exist. This tool never deletes filesystems.
- API requests are generally limited to one per second. The guard spaces its
  exact GET and POST and never automatically retries the destructive POST.

The API exposes GPU type/count but does not expose the physical GPU UUID. The
root operator must capture recent, read-only guest identity/idle evidence and
bring that log offhost. This guard never connects to SSH, inspects the guest,
signals jobs or calls guest shutdown. It compares the captured GPU identity and
a fresh exact cloud ID/IP/type/GPU-description lookup.

## Commands

`prepare` performs local verification without accepting a key or using a network.
`status` performs one authenticated GET and prints only ID/IP/status/type/GPU
information. `terminate` repeats local verification, matches the current cloud
identity, GETs the exact public deployed version and checks its body SHA256,
then submits at most one POST for the fixed instance. A successful `prepare`
is not a cached permit; the mutation command rechecks its inputs.

All credentials enter through a pipe on stdin. There is no key argument,
environment-variable lookup or credential-file reader. Terminal and regular-file
stdin are rejected. The parent/root operator owns the secure producer. Do not
include a key in command text, process arguments, a manifest or Git.

```sh
GUARD_EXPECTED=(
  --expected-instance-id 8809d4738226458a80a100bb4eb2f906
  --expected-ip 68.209.73.247
  --expected-gpu-uuid GPU-aa9e9485-5551-d7be-dfc9-9e433a7284da
  --expected-gpu-name 'NVIDIA H100 80GB HBM3'
  --expected-driver-version 580.105.08
  --expected-api-instance-type gpu_1x_h100_sxm5
  --expected-api-gpu-description '<exact observed instance_type.gpu_description>'
)
python3 operations/lambda_shutdown_guard.py prepare \
  --manifest /absolute/offhost-evidence-base/COMPLETION.json "${GUARD_EXPECTED[@]}"
# Run the following pipeline only with the real secure producer configured.
secure-secret-producer | python3 operations/lambda_shutdown_guard.py status "${GUARD_EXPECTED[@]}"
secure-secret-producer | python3 operations/lambda_shutdown_guard.py terminate \
  --manifest /absolute/offhost-evidence-base/COMPLETION.json "${GUARD_EXPECTED[@]}"
```

After a POST, retain its sanitized JSON receipt and independently use `status`
until it reports `terminated`. `terminating` is an accepted request, not proof
that release has finished. If a POST has a timeout, an unexpected response or an
ambiguous result, use `status`; do not blindly retry. An exact ID reporting
`terminated` is reportable even if Lambda has removed its IP from the response.

## Actual completion manifest and evidence

The root operator creates `COMPLETION.json` only from actual final artifacts.
Put it at the common parent of all local evidence, replay, paper and Git paths.
Every file reference is `{"path":"relative/path","sha256":"64 lowercase hex"}`;
absolute paths are accepted only within that same parent. Symlinks, parent
traversal, duplicate JSON keys, missing files and changed bytes are rejected.
No fixture or preview may be promoted by changing a status string.

Top-level schema:

| Field | Required value |
|---|---|
| `schema_version` | `h100-conditional-termination-v1` |
| `status` | `COMPLETE`, only after every gate below actually passes |
| `offhost` | `true`; root verifies these are durable local copies outside the H100 |
| `instance` | Exact keys `instance_id`, `ip`, `gpu_uuid`, `gpu_name`, `driver_version`, `api_instance_type`, `api_gpu_description`; values equal explicit CLI identity |
| `campaign_sha256` | Actual campaign-file SHA256, already registered with its exact prestart and execution-profile SHA in the committed campaign registry |
| `artifacts` | File references for every role below |
| `raw_backups` | Entries `final` and `completion`, each with `archive`, `control`, `receipt` file references and `directory` pointing to its verified extracted local tree |

`artifacts` roles:

| Role | Required evidence |
|---|---|
| `campaign` | Exact genuine registered recovery campaign JSON with all 400 sessions and campaign ID `h100-confirmatory-gclocker100-20261027` |
| `journal` | Matching final journal: all 400 succeeded exactly once, return code 0, completed time, full passing input-byte gate strictly after the last action |
| `complete_audit` | Actual `AUDIT_COMPLETE.json`: genuine `synthetic:false`, PASS/no findings, matching campaign/journal, 400 sessions, 9360 successful outputs, 2340 warmups, 7020 measured outputs, 60 translation rejections, 3360 successful and 60 failed complex cleanups; every auditor input hash is checked again; current auditor source hash and exact registered recovery identity/profile summary (160 Spark drivers, 24 profile gates) are required |
| `replay_manifest` | Actual portable `REPLAY_MANIFEST.json`: PASS, phase complete, synthetic false, same audit hash; validation/analysis/complete-audit/rendering commands all exited 0; original archive, harvested raw and rendered output inventories are rehashed |
| `paper_pdf` | Complete research paper PDF, preserved offhost; header and exact hash checked |
| `paper_source` | Complete paper source file, or an archive containing all source/assets for a multifile paper; exact hash checked |
| `publication_receipt` | Actual final paper/claim review receipt described below |
| `web_receipt` | Actual successful **public deployed version**, never local preview or owner-private deployment |
| `private_git_receipt` | Actual private-repository push/privacy verification plus exact commit file inventory |
| `identity_idle_receipt` | Fresh offhost captured identity and empty job/process checks, at most 300 seconds old at the end of local verification |
| `contention_replay` | Actual `CONTENTION_REPLAY.json`: registered separate plan/prestart, current auditor, terminal disposition, integrity PASS, actual same-scheduler resume and complete raw/input bindings |
| `contention_preservation` | Actual private retrieval receipt binding that contention audit and every existing raw phase to independently downloaded bytes and stable private release/asset locators |

Final and completion backups use the existing `h100-offhost-backup-v1` control
and `OFFHOST_SHA256_VERIFIED` receipt formats. The guard hashes each tar again,
checks its size, verifies every extracted file through `research_backup.py`,
and checks the full replay raw inventory against the final raw snapshot.
The archive's `PRESTART_MANIFEST.json` must have the registered prestart hash
and seal the registered `EXECUTION_PROFILE.json` plus the readiness prefix of
`EXECUTION_PROFILE_GATES.jsonl`. The profile must identify the fixed recovery
cohort and unchanged C84 source. The full 24-record log must retain the exact
sealed readiness prefix, then ten ordered block pairs and the final pair; every
record must report PASS for the same profile hash. The sealed profile,
prestart manifest, archived readiness prefix and full receipt log must each be
explicit exact-path inputs of the matching complete audit. The raw final and
completion roots must retain byte-identical profile/full receipt logs. Copied
raw backup paths need not be separate audit inputs; their bytes must match the
actual full log that the auditor read.

The completion snapshot must contain the identical final journal,
`confirm-driver.exit` equal to `0`, and `confirm-driver.ended-utc`. A final raw
tar alone does not establish that the real supervisor exited successfully.

The final paper review receipt has schema `h100-final-publication-v1`,
`status:PASS`, `full_paper:true`, `final_claim_audit:PASS`, `campaign_sha256`,
`paper_pdf_sha256` and `paper_source_sha256`. The operator's actual scientific,
claim and visual publication reviews establish those facts. The guard checks
the recorded decisions and exact files; it does not assess paper quality.

The deployed web receipt has schema `h100-public-web-deployment-v1`,
`status:succeeded`, `audience:public`, nonempty `version`, `campaign_sha256`,
`paper_pdf_sha256`, `version_url`, and `page_sha256`. Use the native deployment's
direct public HTTPS URL without a query or fragment, with its deployed version
ID recorded separately. A stable production URL is acceptable when Sites does
not provide an immutable version URL. The mutation path fetches it
without credentials or redirects, requires HTTP 200, and compares its actual
body SHA256. The page must be built from the final paper and genuine replay.
The webpage builder's local `PUBLICATION_MANIFEST.json` is useful provenance
but is not a substitute for this actual deployment receipt.

The private Git receipt has schema `h100-private-git-evidence-v1`,
`status:PRIVATE_PUSH_VERIFIED`, `private:true`, `campaign_sha256`,
`repository_url`, `commit` (full SHA), equal `remote_commit`, `local_repository`
(directory beneath the evidence base), and `files_sha256` (repository-relative
file names to SHA256). Derive visibility and remote commit from an authenticated
read-only repository check after the actual push. The guard verifies the
recorded visibility/push decisions and runs read-only `git show` to verify
preserved commit bytes; it does not authenticate to GitHub. At minimum the exact
campaign, journal, complete audit, real replay manifest, paper PDF and paper
source hashes must all be represented in that committed file inventory.

## Mandatory separate contention disposition

The contention role is `h100-contention-20261028`. The only genuine anchors are
the 66-file canonical prestart at
`evidence-research/20261028/h100-contention-v1`, plan SHA256
`670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031`
and prestart manifest SHA256
`4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25`.
`research_reporting/contention_replay.py` provides the bounded identity check;
the guard has no arbitrary campaign override. The separate plan must reference
the same final isolated campaign and registered execution profile.

The actual replay receipt uses schema `h100-contention-replay-v1`,
`synthetic:false`, `integrity_status:PASS`, `publication_ready:true`, no findings
and the current auditor source hash. Its terminal `status` must be `COMPLETE`
or `STOPPED_WITH_FAILURE`. `PARTIAL`, `FAIL`, active work, missing resume proof
and failed integrity cannot permit retirement. A stopped scientific phase can
retain zero attempted waves if its genuine runtime/input/prelaunch gate failed;
the failed phase, actual error and all unattempted wave IDs must remain visible.
For a terminal stop, an untouched future phase may use
`NOT_ATTEMPTED_AFTER_STOP`; `NOT_OBSERVED` remains accepted with zero attempts.
The closed-stop label is accepted only with the full `STOPPED_WITH_FAILURE`
evidence and cannot stand in for a completed phase.
An operational interruption without a terminal failed scientific phase remains
nonterminal and requires preserved diagnosis.

The receipt preserves the original four-pilot/sixteen-main wave order. A
complete extension has all 20 waves, 460 successful output comparisons and ten
specific strict translation rejections. A stopped extension retains its actual
failed phase and fixed unattempted suffix; it cannot invent a passing main
phase after a pilot failure, drop a failed participant or retry until clean.
Only actually created `pilot`, `main` or `failed` raw snapshots are required.
Each snapshot's control, original offhost receipt, tar hash/byte count and full
extracted inventory are rechecked with `contention_backup.py`. Every snapshot
must retain the byte-identical canonical prestart. Terminal driver metadata and
the actual offhost ACK bytes must bind each raw snapshot. No synthetic or
fabricated terminal receipt is supplied by this runbook.

A late verified ACK after a stopped failure is retained as separate actual
remote-receipt metadata; the original driver's checkpoint list stays unchanged.
Complete runs require the driver-observed pilot and main ACKs. Every actual raw
phase still needs its verified remote receipt, local transport proof and private
retrieval, including a failed snapshot.

Every exact-path entry in `input_files_sha256` is rehashed locally, including
canonical files, original held journal/session, raw payloads and operational
metadata. The receipt's `handoff` must verify actual `RESUMED.json` with the same
held scheduler PID, creation time and identity, source/runtime/profile checks
and all 1,654 input files before contention and before resume. The five expected
scale counts are 57, 123, 185, 371 and 918. Owned groups must be empty and both
residual scans clear. Held journal/attempt bindings and original results and
schedule bytes must match the final isolated evidence. Original host-result
endpoints precede contention actions, and all contention actions end before
the resume profile check and actual resume. Outer scheduler wall time includes
the hold and is disclosed; action timings from the two designs stay separate.
The actual manager failure path can leave `STATE.phase:HANDOFF_ERROR` with its
error while `RESUMED.json` proves the verified return. A detached guardian can
also leave the prior state phase. These cases require the actual matching
resume actor/identity and all cleanup/profile/byte/timeline evidence; there is
no assumed `FAILED_RESUMED` state that the executed source never writes.

The final paper review and actual public deployment receipt must each contain
the same exact `contention_review` object:

```json
{
  "status": "COMPLETE",
  "experiment_id": "h100-contention-20261028",
  "plan_sha256": "actual separately registered plan SHA256",
  "prestart_sha256": "actual separately registered prestart SHA256",
  "replay_sha256": "actual terminal CONTENTION_REPLAY.json SHA256",
  "disposition": "COMPLETE or STOPPED_WITH_FAILURE"
}
```

Here `status:COMPLETE` means the actual editorial/deployment review is complete;
`disposition` records whether the experiment completed or honestly stopped with
failure. The real reviews must cover the contention instrumentation, resource
sharing, failures/unattempted waves, overlap and limitations. No partial findings
or inferred causal benefit may be promoted by changing this object. Existing
stable production Site URLs remain supported; immutable version URLs are not
required.

The `contention_preservation` file uses schema
`h100-contention-private-preservation-v1`, `status:PRIVATE_DOWNLOAD_VERIFIED`,
`private:true`, `experiment_id`, `plan_sha256`, `prestart_sha256`,
`replay_sha256`, and the same `repository_url` as the actual private Git receipt.
Its `raw_backups` keys must exactly equal the replay's actual snapshot phases.
Each entry has `archive_sha256`, `archive_bytes`, `retrieval_verified:true`, a
`retrieved_archive` file reference and `storage` with exactly these fields:

- `repository_url`: the same private repository identity;
- `release_tag`: the actual stable release tag;
- `asset_ids`: the actual positive integer GitHub asset IDs;
- `asset_names`: the corresponding flat asset names in the same order.

The independently retrieved or reassembled tar must be a different local file
from the original snapshot, with identical actual SHA256 and byte count. Another
hard link to the original inode does not prove retrieval. For
split assets, retain all ordered IDs/names and verify the rejoined original.
Signed redirect URLs, query credentials and temporary download links do not
belong in this schema. The guard checks the recorded private/download decisions
and actual downloaded bytes; the root operator performs the authenticated
privacy and independent retrieval checks described in
`H100_PRIVATE_ARCHIVE_STORAGE.md`.

The private Git commit inventory must additionally preserve the exact terminal
contention audit, preservation receipt, all 66 canonical prestart files,
auditor/handoff/backup source, raw controls/manifests/original verification
receipts, terminal driver/ACK metadata and all referenced handoff/held-original
metadata. Large raw tar bytes remain private release assets. Preserve failed
snapshots and all original reports. The final identity/idle receipt must remain
fresh after actual same-scheduler resume and isolated campaign completion.

The identity/idle receipt has schema `h100-offhost-identity-idle-v1`, `instance`
(equal to the explicit identity), `campaign_sha256`, `captured_at_utc`,
`driver_exit:0`, `driver_alive:false`, `benchmark_processes:[]`,
`gpu_compute_processes:[]`, `capture_log_path`, `capture_log_sha256`. The captured
log itself must be offhost and hash-bound. Capture after campaign completion
and shortly before termination. If any job remains active, leave it running
and do not manufacture an empty process list. Refresh read-only evidence after
long paper/replay/deployment work; the guard never kills a job to make this pass.

## Local verification

```sh
uv run --python python3.12 --with pytest python -m pytest -q \
  operations/tests/test_lambda_shutdown_guard.py
```

Boundary fixtures are explicitly fabricated, registry acceptance is mocked only
inside tests, and every cloud/page mutation test uses fake transport. Tests do
not query the real API, use a real key, access a VM or terminate an instance.
