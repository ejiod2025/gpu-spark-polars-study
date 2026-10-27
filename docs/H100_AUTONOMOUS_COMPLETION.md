# Authorized autonomous completion and instance retirement

On October 27, 2026, the owner authorized autonomous work while away and specified
that an acceptable final result includes the full research paper and webpage.
The owner separately authorized shutting down the H100 **once that work is done**.
The paper author is **Daniel Ejiofor**, with no institutional affiliation supplied.
These instructions add publication and conditional retirement to the existing
fixed research campaign; they do not reduce the experiment to a pilot or draft.

## Current execution and identities

- Private development repository: `ejiod2025/gpu-spark-polars-study`, branch
  `H100_main_dev`. Keep it private.
- Replacement SSH host: `ubuntu@68.209.73.247`.
- Lambda instance ID: `8809d4738226458a80a100bb4eb2f906`.
- Cloud instance type: `gpu_1x_h100_sxm5`; observed GPU description:
  `H100 (80 GB SXM5)`; region `us-southeast-1`.
- Physical GPU UUID: `GPU-aa9e9485-5551-d7be-dfc9-9e433a7284da`, from SSH inventory.
- Frozen experimental source:
  `c84ef6a07341f350dae6636ab709c1b65fd894e0` (52 files).
- New campaign ID: `h100-confirmatory-replacement-20261027`.
- Remote evidence: `/home/ubuntu/study-h100-research-evidence-20261027`.
- Local controller state/log/events:
  `rerun/replacement-20261027/raw-backups/` (ignored operational storage).
- Initial local controller PID: `85571`, creation time `1793114100.637303`.
  Verify command and process identity; do not infer liveness from a PID alone.
- Local caffeinate PID `20389` prevents idle system sleep while work continues.

The API key is outside Git in a user-only local credential file. Never print it,
copy it to the instance, include it in a command argument or environment variable,
or put it in a paper, webpage, prompt, archive, receipt or source repository.
For the final authorized call, read the local credential into the guard's stdin.
Operational credential location:
`/Users/danielejiofor/.codex/secrets/lambda-h100-20261027.key` (mode 0600).
The initial read-only API identity receipt is in ignored
`rerun/replacement-20261027/shutdown/target-identity.json`.

## Required completion gates

1. Finish all 400 fresh sessions in ten fixed paired blocks, without silently
   replacing attempts, changing source/settings or pooling the lost instance's
   three surviving blocks. Every block must have a verified off-host raw backup.
2. Verify all 9,360 successful outputs and 60 specific expected strict-GPU
   unsupported warmup outcomes, cleanup/configuration/coverage gates, and a full
   input byte gate chronologically after the final session. Preserve failures.
3. Harvest final and post-exit completion snapshots, verify their manifests and
   reconstruct a new local evidence tree with explicit overlap provenance.
   Preserve all original phase archives. Run real portable replay, complete
   audit and predefined analysis. Synthetic tests are not study results.
4. Produce the full research paper, final PDF and source, genuine tables/figures,
   citations, limitations and result-to-claim audit. Remove pending placeholders
   only when actual data support their replacement. Credit Daniel Ejiofor.
5. Publish the completed H100 webpage with the paper download, checked results,
   honest strict GPU coverage and reproducibility material. The existing public
   Site is `appgprj_6ade6dc9bae88191aebcee178edfef4e`, at
   `https://a100-spark-polars-study-oct2026.ejiod.chatgpt.site`.
   Preserve public access without sign-in and preserve historical A100 evidence.
   Its opened source is `.sites-checkout-h100-publication/`; the Site owner must
   use native Sites source/publishing tools and keep credentials in session memory.
6. Verify deployed page access and paper download, layout and interactions,
   all scientific claims and final source/evidence hashes. Push the publication
   code and completion receipts privately. Preserve large raw evidence off host
   with retrieval information and immutable checksum manifests.
7. Only then create the genuine completion manifest required by the retirement
   guard. Refresh SSH GPU identity/idle state and the exact Lambda instance
   ID/IP/type through read-only API calls. Terminate **only** the bound instance,
   then verify its final cloud state and save an off-host receipt.

Guest `shutdown`/`poweroff` does not end Lambda compute billing. The authorized
retirement uses the documented Lambda termination operation after the gates
above; it does not delete other instances or separately billed filesystems.
Delete the local shutdown credential after verified successful retirement.
Keep the research history and published page.

## Persistence and failure handling

Inspect current state rather than starting duplicate controllers or supervisors.
The controller and backup watcher already retry transport failures without
repeating engine sessions. A tool timeout is not a failed campaign. Scientific,
identity, integrity or publication failures require diagnosis with evidence
preserved; never erase a journal or fabricate a PASS gate to advance.

If the local continuation controller has stopped, inspect its exact phase/error,
remote markers and owned processes before an explicit controller-only resume.
No benchmark/supervisor restart or replacement attempt is automatic.

A chat heartbeat may continue this authorized work and stays quiet while the
state is unchanged. Notify only on meaningful progress, completion, failure or
required user action. After verified publication and instance retirement,
disable that heartbeat. Do not archive this chat unless requested.
