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
- Current recovery campaign ID: `h100-confirmatory-gclocker100-20261027`.
- Current remote evidence:
  `/home/ubuntu/study-h100-research-evidence-20261027-gclocker100`.
- Current canonical prestart:
  `evidence-research/20261027/h100-confirmatory-gclocker100-v1`.
- Local controller state/log/events:
  `rerun/gclocker100-20261027/raw-backups/` (ignored operational storage).
- Current local controller PID: `76437`, creation time `1793137268.510692`.
  Verify command and process identity; do not infer liveness from a PID alone.
- Local caffeinate PID `19471`, creation time `1793161696.288356`, prevents
  idle system sleep. The previous PID 20389 had exited and was replaced after
  the owner's renewed request. Ownership receipt:
  `rerun/contention-20261028/caffeinate-owner.json`.

## User-requested concurrent contention extension

The owner explicitly selected concurrent sessions on this same H100 as a
separate contention experiment. Follow
[the prospective interlude record](H100_CONTENTION_INTERLUDE_20261028.md)
and `docs/H100_CONTENTION_PROTOCOL.md`. Do not mix these measurements into the
original 400-session isolated estimates or silently cancel that cohort.

Contention staging is `/home/ubuntu/study-h100-contention-staging-20261028`,
evidence is `/home/ubuntu/study-h100-contention-evidence-20261028`, and the
separate handoff root is
`/home/ubuntu/study-h100-contention-handoff-20261028`. Local operational copies
are under ignored `rerun/contention-20261028/`. Read actual plan, state and
receipts; these path declarations do not establish that a run has begun.

The genuine contention prestart is now frozen and privately preserved at
`evidence-research/20261028/h100-contention-v1` (66 files). Its plan SHA256 is
`670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031` and
prestart manifest SHA256 is
`4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25`.
Private commit `98c7ff4d6a9bbe82a021671c28fed28f71827b70` preceded all
contention queries. The managed handoff launched at 2026-10-28 04:44:23 UTC,
manager PID `188803`, creation time `1793162662.9`, holding scheduler PID
`179144`, creation time `1793160829.75`. It initially drained the original
`block-04-complex-sf30-spark_cpu` runner without signaling it. Initial receipts
are in `evidence-research/20261028/h100-contention-controller` and ignored
`rerun/contention-20261028/handoff-launch/`; they are not live completion proof.
The separate local off-host watcher is PID `66482`, creation time
`1793162631.560101`, with receipts/logs in
`rerun/contention-20261028/raw-backups/`. Verify identities and actual state
before acting; never launch a duplicate manager or watcher.

The contention pilot actually passed all four waves: eight fresh processes,
92 successful SQL comparisons, two expected strict unsupported warmups and
22 common measured query pairs with positive action overlap. The 367-file raw
pilot payload is verified off-host in
`rerun/contention-20261028/raw-backups/pilot/`; its 75,530,240-byte tar has SHA256
`87c508ae916d2c161c99a86766af97eb4a16071cb045b36e879c7ed6b78bc55a`.
Private metadata is in
`evidence-research/20261028/h100-contention-pilot-checkpoint`. The gated
16-wave expansion has begun. A genuine offline pilot replay is PARTIAL until
the full separate disposition and actual handoff/resume proof exist; no pilot
milestone establishes full publication or retirement readiness.

The exact original journal and parent session record during the hold are
preserved verbatim in
`evidence-research/20261028/h100-contention-held-original`. Their running
statuses are intentional and must not be repaired. After actual completion,
capture final `PHASE_DRIVER.json`, remote `backup-receipts/*.json` and the full
handoff directory separately: the main archive predates its own ACK and the
later same-scheduler `RESUMED.json` proof.

The full contention workload has now finished: pilot 4/4 and expansion 16/16
waves PASS. Both raw snapshots are verified off-host; the main archive is
487,321,600 bytes, SHA256
`98be99cf74690d01053de9670669e629a166b02bd67fd51a994425006f792197`,
with 1,561 payload files. The phase watcher exited normally after its verified
main ACK; do not restart that completed watcher. The manager and guardian also
finished: `STATE.json` is `COMPLETE_RESUMED`, and the same scheduler PID 179144
resumed at 2026-10-28 05:32:20.044724 UTC. The original isolated campaign is
actively collecting again. These historical PID values are not authorization
to signal a later reused PID.

Exact final driver/remote ACK metadata is at ignored
`rerun/contention-20261028/final-metadata-v1`; the complete final handoff is
`rerun/contention-20261028/final-handoff-v1`. They are preserved privately in
`evidence-research/20261028/h100-contention-final-checkpoint`. Genuine offline
replay v3 at `rerun/contention-20261028/offline-full-audit-v3` verifies all 460
outputs, ten strict rejections, 110 measured overlap pairs, 250 barrier releases
and the actual driver/resume metadata. It remains PARTIAL/publication false:
only the original paused attempt's actual payload/worker endpoints from the
forthcoming block-04 backup remain for independent separation proof. An unset
`no_action_overlap` at this stage is pending proof, not an observed violation.
Once that genuine backup exists, run the terminal command in
`research_reporting/CONTENTION_REPLAY.md` into a new output directory. Never
promote the partial receipt by editing its status. The isolated 400-session
study and final paper/web/private retrieval/retirement gates remain unfinished.

The handoff may hold only the exact owned C84 scheduler PID, letting its
already launched original runner/worker finish unpaused. The unchanged journal
can retain a running attempt while that scheduler is deliberately stopped.
Its outer orchestrator wall time includes the hold; original worker action
endpoints must finish before any contention begins. Never resume the isolated
scheduler into active contention workers, signal a reused PID, restart a live
engine or edit the old attempt/journal to disguise the interlude. Inspect the
handoff manager, detached guardian, `STATE.json` and `RESUMED.json` before any
manual intervention. Interlude profile/data checks stay separate from the
formal 24-record log. Preserve genuine overlap, correctness, failures and both
study identities off-host, and include the separate contention disposition
and limitations in the final paper and public webpage before retirement.

The API key is outside Git in a user-only local credential file. Never print it,
copy it to the instance, include it in a command argument or environment variable,
or put it in a paper, webpage, prompt, archive, receipt or source repository.
For the final authorized call, read the local credential into the guard's stdin.
Operational credential location:
`/Users/danielejiofor/.codex/secrets/lambda-h100-20261027.key` (mode 0600).
The initial read-only API identity receipt is in ignored
`rerun/replacement-20261027/shutdown/target-identity.json`.

## Preserved default-profile failure and fixed recovery

The original replacement campaign `h100-confirmatory-replacement-20261027`
halted at 88 validated sessions plus one Spark CPU warmup JVM allocation failure.
Its 3,349-file raw evidence, including the original journal and failed attempt,
is verified off-host under ignored
`rerun/replacement-20261027/incidents/aborted-20261027T202748Z/`.
Do not resume its supervisor or rewrite its failed journal. The original local
controller PID 85571 exited as designed; it is not the current controller.

The [prospective recovery protocol](H100_GCLOCKER100_RECOVERY_PROTOCOL.md)
specifies exactly one separately named SF100 feasibility diagnostic and one new
fixed 400-session cohort if readiness passes. The diagnostic completed with 12
oracle-valid outputs, verified cleanup and actual driver flags. The captured
execution-profile SHA256 is
`a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69`.
Preparation PID 87058 completed all five SF1 readiness configurations and froze
fresh genuine campaign/prestart identities before any recovery timing.

Recovery readiness is now PASS: 116 successful output comparisons, all five
semantic fixture configurations and four actual Spark readiness JVM flag
checks. The registered recovery campaign SHA256 is
`3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641`;
the canonical 346-file prestart manifest SHA256 is
`ea57fa1221b905e8ee287fe7860285c7a37535b084f13ce3dbbbca5ac4e29ace`.
Private prestart commit `6dbca2fbd4de1be8aa4408a639954bc9076bfa38` preceded
the first recovery session. Supervisor PID 92406 launched block 01; the first
1,654-file byte gate passed at 2026-10-27 21:47:43 UTC. Do not restart it.

The first recovery block finished all 40 sessions at 2026-10-27 23:54:27 UTC.
Its 1,041,694,720-byte raw tar (1,355 payload files) is verified off-host with
SHA256 `a661e7066c8ceeadb6a10b6f88653417b8a17f32120950cc6c754711ec88dd64`.
Independent local frozen validation reports PARTIAL: 40 sessions, 936 successful
outputs, six expected strict translation rejections, no findings and 360 pending
sessions in that block-end snapshot. All 16 formal Spark JVMs and four profile
receipts through block 01 match the sealed settings. The private checkpoint is
`evidence-research/20261028/gclocker100-block01-raw-checkpoint`; its separately
captured five-record profile observation includes a later block-02-before
receipt. This milestone is not final analysis or publication readiness. The
existing supervisor and watcher have continued into block 02.

Block 02 also finished its 40 sessions at 2026-10-28 02:06:16 UTC. Its verified
off-host tar is 1,043,906,560 bytes, SHA256
`3bc4e319aa273100895e2cfe95c87ff8e8ceb888d166cab5dd45925e86abe64a`.
The private checkpoint is
`evidence-research/20261028/gclocker100-block02-raw-checkpoint`. Independent
checks call the unchanged C84 `audit_session` function on those 40 selected
sessions: 936 outputs, six expected rejections, cleanup and all 16 actual Spark
JVMs match, with no findings. The original journal records 80 successes; this
isolated block-02 payload does not claim a full 80-session `--partial` CLI run.
Six separately captured profile receipts match and bracket both blocks; the
seventh is later block-03-before evidence. The supervisor continues block 03.

Block 03 is independently checked and privately checkpointed at
`evidence-research/20261028/gclocker100-block03-raw-checkpoint`: 40 selected
sessions, 936 oracle outputs, six exact strict rejections, cleanup, all 16 JVMs
and all 40 runtime/configuration inventories match with no findings. Its
1,042,534,400-byte raw tar SHA256 is
`b4f68a4ad9085a470f8214d59915bba3a3cfeb90e46a11a67213e579ee13d90c`.
The first eight separately observed formal profile receipts bracket the three
completed blocks; the ninth is later block-04-before evidence. This is still
PARTIAL_BLOCK_ONLY, not a final 400-session analysis. The checkpoint preserves
an initial local missing-psutil import failure separately from scientific
outcomes; the successful audit uses the pinned local analysis interpreter.

The frozen C84 expressions, heap96g, 26 CPUs, shuffle120, AQE, timing boundaries,
same 1,654 input files, oracles and Python environments remain unchanged.
Both Spark paths use fixed `SPARK_SUBMIT_OPTS` with unlocked diagnostic options,
`GCLockerRetryAllocationCount=100` and startup `PrintFlagsFinal`. Verify all 24
idle profile receipts, 160 actual formal Spark JVM logs and four readiness logs.
This profile choice is disclosed, and no causal cure or speed benefit is claimed.
No prior timing is pooled and no further clean restart is automatic if recovery
fails. The final paper/web builders accept only this registered recovery cohort.

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
   Independently replay the separate contention evidence as well. Its terminal
   disposition must bind the actual same-scheduler resume, unchanged original
   attempt/journal and full interlude input/profile checks, and prove that its
   actions did not overlap isolated worker actions. Preserve and describe any
   failure and any unattempted expansion waves without imputed results.
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
   with retrieval information and immutable checksum manifests. Follow the
   [private archive storage runbook](../operations/H100_PRIVATE_ARCHIVE_STORAGE.md)
   for independent private GitHub retrieval checks; an upload alone is not
   verified preservation.
7. Only then create the genuine completion manifest required by the retirement
   guard. Refresh SSH GPU identity/idle state and the exact Lambda instance
   ID/IP/type through read-only API calls. Terminate **only** the bound instance,
   then verify its final cloud state and save an off-host receipt.

The owner's expanded contention request also requires a matching retirement
guard binding for that separate genuine disposition and handoff proof, plus
paper/web review of both roles. The earlier 400-only retirement checks do not
by themselves establish completion of the expanded request. Integrate and
test the separate contention replay receipt before preparing termination.

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

The active native follow-up ID is `finish-h100-study-and-retire-instance`,
attached to this chat. It was created after the owner explicitly requested
autonomous work while away. Use the native automation tool to inspect, update
or disable it; do not edit scheduler files or create duplicate follow-ups.
