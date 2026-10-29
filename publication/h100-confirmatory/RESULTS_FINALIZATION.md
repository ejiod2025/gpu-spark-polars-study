# Complete recovery results and publication ledger

Scientific status: the genuine ten-block recovery and portable replay are
complete. The manuscript has been populated from those actual outputs. This
ledger does not create an editorial attestation, PDF build, publication receipt
or retirement approval; those separate checks remain with the root operator.
Author: **Daniel Ejiofor**, with no institutional affiliation supplied.

## 1. Exact registered recovery and harvest identity

| Binding | Actual value |
| --- | --- |
| Campaign ID | `h100-confirmatory-gclocker100-20261027` |
| Canonical pre-start directory | `evidence-research/20261027/h100-confirmatory-gclocker100-v1` |
| Campaign SHA256 | `3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641` |
| Pre-start SHA256 | `ea57fa1221b905e8ee287fe7860285c7a37535b084f13ce3dbbbca5ac4e29ace` |
| Execution-profile SHA256 | `a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69` |
| Frozen source commit | `c84ef6a07341f350dae6636ab709c1b65fd894e0` - exact 52-file map |
| Canonical private pre-start commit | `6dbca2fbd4de1be8aa4408a639954bc9076bfa38` |
| Reconstruction | `rerun/gclocker100-20261027/reconstruction-v1` |
| Reconstruction-manifest SHA256 | `6ef171eaaebbda72bfd928b2b12c468a19529e15ec86224d3921c1fcad281b2d` |
| Reconstructed raw harvest | `rerun/gclocker100-20261027/reconstruction-v1/harvest` |
| Genuine complete replay | `rerun/gclocker100-20261027/replay-v1` |
| Final journal SHA256 | `3648cfe093d239af6b6ffd9e14bb6e41926a57f8058604225dc47bf055a27d67` |

The canonical archive has 346 files/345 listed checksum entries. All thirteen
original transport snapshots (prestart, ten blocks, final and completion) were
verified and reconciled without rewriting scientific JSON. The harvest and
lineage are separate from the canonical seal and original snapshots. All
1,654 replacement physical inputs and existing oracle bytes were reused.
Neither the lost October 26 cohort nor aborted default-profile replacement
contributes a primary observation.

The actual attempt interval is October 27 21:47:44.042050 UTC through October 28
23:26:10.952925 UTC. The full final input-byte gate completed at
23:33:18.564788 UTC; journal completion is 23:33:18.565015 UTC. Original
supervisor exit is zero and its end marker is 23:42:11 UTC. All 400 sessions
have exactly one successful attempt. No session, block or inner action is
silently replaced, excluded or pooled with a different role.

## 2. Actual complete scientific accounting

The replay ended at October 28 23:50:00.745311 UTC, with validation, frozen
analysis, complete audit and rendering all successful. It is genuine and
nonsynthetic. The complete audit has no findings and proves:

| Observed item | Count |
| --- | ---: |
| Fresh sessions | 400 |
| Successful oracle comparisons | 9,360 |
| Successful warmups | 2,340 |
| Successful measured outputs | 7,020 |
| Specific strict translation rejections | 60 |
| Successful complex cleanup records | 3,360 |
| Failed-action complex cleanup records | 60 |
| Actual formal Spark driver logs | 160 |
| Ordered idle-boundary profile receipts | 24 |

All formal Spark logs prove retry count 100, unlocked diagnostic options,
PrintFlagsFinal, G1 and maximum heap 103,079,215,104 bytes. Profile receipts
retain the sealed readiness prefix and match Java/runtime/environment hashes,
with bracketing chronology. This complete execution under the selected profile
is not a causal mitigation/reliability comparison with the aborted cohort.
The separately preserved twelve-output diagnostic and 116-output readiness are
excluded from performance inference.

## 3. Exact result sources and insertion accounting

`P` below is the literal genuine replay directory above. Values are taken from
the frozen analysis and current verified exports, without changing the method.

| Artifact | Repository-relative path under P | Actual accounting | SHA256 |
| --- | --- | --- | --- |
| Complete replay | `REPLAY_MANIFEST.json` | PASS, complete, synthetic false | `b59450d0731180b5b9cc642c5174f872ab460ab585445b87230175a07c48655c` |
| Complete audit | `complete-audit/AUDIT_COMPLETE.json` | PASS, no findings, 400 sessions | `cb03c639c0b428f50f07a142d1201e8a3c39ad82ea21a2192db95d4ebfc5585d` |
| Analysis | `analysis/analysis.json` | 26 complete primary rows; 32 secondary pairs; descriptive query results | `3086aa09e795169308fb36615948c0fd752b48ea1a3b7cbcff075a11254a7b24` |
| Raw ratios | `analysis/block_ratios.csv` | 260 primary_suite / seconds rows, ten per contrast | `696799dd2c71d31afb701c9eae9d5a38e4b26fa6a3bddec5dd43cb340ef85ed5` |
| Primary CSV | `figures/primary_contrasts.csv` | 26 rows | `94f9fc15c800772d86d6da7cce96fbb1eaf6e1c0212e96f34e0c245a45269993` |
| Complete-score CSV | `figures/complete_case_scores.csv` | 40 cells, 37 numeric and 3 N/A | `62fae39d082c1361e591a8fa7dca2c0473a7b661e7a41f4e1b8a0955a13efa52` |
| Secondary sensitivity CSV | `complete-audit/HOST_RESULT_SENSITIVITY.csv` | 32 rows, 26 available and 6 N/A | `88a00052cb5f4196b32372c26af93dca734b907067e59ef997fb41bf5ce4d6a5` |
| Primary captions | `figures/CAPTIONS.md` | Four exact standalone captions | `239e95077842565208d25441d2f9543bad2fef37f39722a3f5f3f87ae9d19919` |
| Primary render manifest | `figures/RENDER_MANIFEST.json` | Four figures, each in PNG/SVG/PDF, source/input/export bindings | `78a4443ba2ba1fbde695172ce994e9adae9f5096398c122912ebf9f73fd1fe9f` |

The manuscript contains every required result: all 26 primary rows in Tables 2a-2d,
all 40 score/coverage cells in Table 1, all 32 secondary pairs in Appendix B and
all 260 rounded primary block ratios in Appendix C. Full precision remains in
the bound JSON/CSVs. Each available primary/secondary estimate uses ten paired
blocks; inner actions define session medians and add no independent sample.
The score/coverage table has 37 numeric cells (10/10 complete, 6/6 queries) and
three Polars GPU complex N/A cells (0/10 complete, 4/6 queries).

All 26 primary Holm tests reject: 15 favor the denominator GPU path and 11 the
numerator reference. GPU Spark does not beat CPU Polars 2.0 on any sampled
complete-suite score. It beats CPU Spark at baseline SF10-300 and all three
complex scales; CPU Spark is supported at baseline SF1. Polars GPU beats both CPU
versions at baseline SF10-300 and loses at SF1. Baseline sampled brackets are SF1
to SF10 for Spark CPU/GPU and both CPU Polars/Polars GPU comparisons; there is no
exact unmeasured threshold. All available secondary direction-change flags are
false, with six complex Polars GPU pairs unavailable. No primary row has zero
observed log-ratio variance.

The query discussion retains the descriptive Spark GPU exceptions relative to
CPU Polars 2.0: baseline Q1 at SF30/100/300, complex window at all three scales and
complex Q13 at SF100/300. It does not extend a suite result to every query or add
query hypothesis tests. Table 3 gives all twelve SF300 expressions, including
strict N/A cells; the heatmap retains every sampled scale.

| Manuscript location | Actual integrated input |
| --- | --- |
| Abstract, §5.1, §6, §9 | Complete registered replay/audit/journal and all 26 decisions; four unpooled cohort roles |
| §5.2 Table 1 | All 40 score/coverage cells; `complete_case_scores.png` with exact caption |
| §5.3 Tables 2a-2d | All 26 primary rows, both p values and supported directions; baseline/complex ratio PNGs with exact captions |
| §5.4 Table 3 | Actual descriptive queries; `query_ratios_action.png` with exact caption |
| §5.5, Appendix B | All 32 action/host-result comparisons and intervals; six unavailable cells and zero direction changes |
| §5.6, Appendix C | All 260 primary ratios in block order, sample log spread and actual pacing limits |
| §5.7-5.8 | Separate completed contention results and six exact-caption final figures |
| §8 | Exact reconstruction/replay/audit identities and truthful private raw-code/evidence availability |

All ten Markdown images occupy standalone paragraphs. Four use
`replay/figures/<stem>.png`; six use `contention/figures/<stem>.png`. Their
captions are copied from the actual primary and contention CAPTIONS.md files.
The final paper build must verify and copy those exact PNG bytes. PDF visual
review must check image/caption fit as well as table/number consistency.

## 4. Separate contention proof and its historical audit

The exact separate ID is `h100-contention-20261028`, plan SHA256
`670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031`,
pre-start SHA256
`4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25`.
The immutable canonical archive has 66 files/65 checksum entries and binds the
same 52 C84 source identities plus separately versioned instrumentation.

`rerun/contention-20261028/offline-terminal-audit-v1/CONTENTION_REPLAY.json`
has SHA256 `0cad03b7ab2edc7e0d62dbcc7b84f8fa0ea51325aba41eb354b5baae0c51a2d3`.
It is COMPLETE/integrity PASS/publication_ready true/no findings, with pilot 4/4
and expansion 16/16 passing waves, 460 successful outputs (230 warm / 230 measured),
ten specific strict rejections, 110 common measured pairs with positive
intersections on both clocks, 250 barrier releases and ten unpaired CPU measured
diagnostics. Pair B complex has four common measured expressions; Q13/window
CPU measurements remain unpaired. No uniform six-query contended CPU score,
complete complex GPU score, new inference or cross-pair GPU ranking is claimed.
Counters and memory samples can include untimed waits.

The handoff binds all 34 original attempt inventory entries, byte-verifying 33
immutable payload files and allowing only documented parent finalization of
session metadata. Original result SHA256
`c33ddd3f46d02e4624083334490925170a20a44795a3f4d53a08556a72d8b4c9`
and schedule SHA256
`d22c6ad51044c77aeedcbcfe611c26604b1b8c0cfba46111e8cebbb472217659`
match the drain receipt. Last isolated host result 04:45:38.505969 UTC precedes
first contention action 04:47:59.693693 UTC by 141.188 s. Last contention host result
05:28:37.930202 UTC precedes same PID 179144 / creation time 1793160829.75 resume at
05:32:20.044724 UTC. STATE is COMPLETE_RESUMED and driver COMPLETE_OFFHOST.
Preserved outer attempt wall 3,043.015 s/end 05:32:20.060856 UTC includes the hold;
worker process wall 240.581 s/end 04:45:41.907405 UTC is distinct. No timestamp
is corrected. Temporal separation does not establish unchanged cache/host state.

Historical `offline-full-audit-v3/CONTENTION_REPLAY.json`, SHA256
`a34a8de887225edf58f9211a2e1f184feaa0eac5165e0dd0d03697f517e30ee5`,
remains PARTIAL/publication_ready false because original payload bytes were not
then supplied. Its false separation boolean meant unavailable proof, not an
observed overlap violation. It is never rewritten or used as terminal evidence.

The six final figure stems are pilot/main × pair_A_common_actions,
pair_B_common_actions and actual_action_timeline. Their genuine final manifest
SHA256 is `5751b86ee46989fdfc7b0d25b1e2eac3a475a09c872143026cedfbd1e06000bd`.
All 19 output hashes are bound. Existing selected-analysis-runtime verification
proof at `rerun/contention-20261028/paper-terminal-verification-v1/CONTENTION_VERIFICATION.json`
has SHA256 `a2f2401acf21b3ea7f3832568a0eeb98b5a8efe1bb359cd350d9ac53d7568099`.
The final builder freshly verifies the same actual evidence again.

## 5. Distinct local power gap and operational pacing

`evidence-research/20261028/h100-local-power-gap-checkpoint/` retains selected
power/watcher observations and unchanged block 06 session/control/receipt.
Low Power Sleep was 09:13:38 UTC on 1% battery; wake on AC was 14:11:23 UTC.
Watcher observations bracket 09:13:25.168866-14:11:52.581715 UTC. Remote block 06
last session succeeded 11:15:26.689677 UTC, backup control was created
11:15:30.821446 UTC and off-host ACK was 14:12:51.939753 UTC, 10,641.118307 s later.
The existing controller and owned sleep-prevention process survived; the same
remote supervisor continued block 07 without restart or replacement. This local
control/backup delay is distinct from the managed contention hold and does not
diagnose a GPU/JVM failure or establish a performance effect.

Stable power record SHA256 is
`7584152f1ab5d49fda74f58e89d16d991a508043f704334f04ee8be722393895`;
watcher selection SHA256 is
`226018cb159052df874b55328b76235afd921be15183c4572ba8c6441ddc7ad2`.
Keep original worker/action/outer timestamps unchanged and disclose possible
host/cache carryover. These observations add no sample, contrast or profile
receipt. The final 24-receipt profile proof comes from the complete audit.

## 6. Remaining editorial, PDF, public-page and preservation work

Scientific replay and manuscript insertion do not create a finished PDF or a
public deployment. The root operator must substantively review every table,
claim, deviation and caption, including the separate contention limits. Only
then may the genuine editorial JSON bind exact manuscript/reference hashes,
registered campaign and exact terminal contention object, with all six review
booleans required by the builder. This ledger creates no such JSON or claim
of completed editorial/visual review.

Use the verified runtimes and exact command sequence in
`paper/LOCAL_READINESS_20261028.md`, with the actual replay-v1 directory and
`--analysis-python /tmp/h100-analysis-audit-venv/bin/python`. The final paper
must bind the same profile/replay/contention identities, author Daniel Ejiofor,
no affiliation, exact embedded PNG bytes and full output inventory. Inspect
all PDF pages at readable resolution; captions must fit with figures and no
numeric table may clip. Keep QA images outside the hashed build directory.

The exact common `contention_review` object uses status COMPLETE, experiment
`h100-contention-20261028`, the plan/pre-start hashes above, terminal replay hash
`0cad03b7ab2edc7e0d62dbcc7b84f8fa0ea51325aba41eb354b5baae0c51a2d3`
and disposition COMPLETE. Paper, webpage and actual final publication receipts
must agree. A status marker cannot substitute for the fresh scientific check.

After final PDF review, build and inspect the genuine public webpage, verify
actual deployment and paper download, and complete private evidence packaging/
independent retrieval and exact source hashes under the operational runbooks.
The full request remains subject to those root-owned delivery/preservation
checks before exact-instance retirement. This ledger performs none of them.
