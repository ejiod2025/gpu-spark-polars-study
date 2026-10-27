# Conditional termination of the replacement H100

The user authorized termination **after** the full paper and public shareable
webpage are complete and all results/raw backups are safely offhost. There is
no second approval prompt. `lambda_shutdown_guard.py` enforces those conditions;
creating or committing this tool does not authorize terminating an incomplete run.
No completion manifest is supplied with this tool.

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
| `campaign_sha256` | Actual campaign-file SHA256, already registered with its exact prestart SHA in the committed campaign registry |
| `artifacts` | File references for every role below |
| `raw_backups` | Entries `final` and `completion`, each with `archive`, `control`, `receipt` file references and `directory` pointing to its verified extracted local tree |

`artifacts` roles:

| Role | Required evidence |
|---|---|
| `campaign` | Exact genuine registered campaign JSON with all 400 sessions |
| `journal` | Matching final journal: all 400 succeeded exactly once, return code 0, completed time, full passing input-byte gate strictly after the last action |
| `complete_audit` | Actual `AUDIT_COMPLETE.json`: genuine `synthetic:false`, PASS/no findings, matching campaign/journal, 400 sessions, 9360 successful outputs, 2340 warmups, 7020 measured outputs, 60 translation rejections, 3360 successful and 60 failed complex cleanups; every auditor input hash is checked again |
| `replay_manifest` | Actual portable `REPLAY_MANIFEST.json`: PASS, phase complete, synthetic false, same audit hash; validation/analysis/complete-audit/rendering commands all exited 0; original archive, harvested raw and rendered output inventories are rehashed |
| `paper_pdf` | Complete research paper PDF, preserved offhost; header and exact hash checked |
| `paper_source` | Complete paper source file, or an archive containing all source/assets for a multifile paper; exact hash checked |
| `publication_receipt` | Actual final paper/claim review receipt described below |
| `web_receipt` | Actual successful **public deployed version**, never local preview or owner-private deployment |
| `private_git_receipt` | Actual private-repository push/privacy verification plus exact commit file inventory |
| `identity_idle_receipt` | Fresh offhost captured identity and empty job/process checks, at most 300 seconds old at the end of local verification |

Final and completion backups use the existing `h100-offhost-backup-v1` control
and `OFFHOST_SHA256_VERIFIED` receipt formats. The guard hashes each tar again,
checks its size, verifies every extracted file through `research_backup.py`,
and checks the full replay raw inventory against the final raw snapshot.
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
`paper_pdf_sha256`, `version_url`, and `page_sha256`. Use a direct immutable
HTTPS version URL without a query or fragment. The mutation path fetches it
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
