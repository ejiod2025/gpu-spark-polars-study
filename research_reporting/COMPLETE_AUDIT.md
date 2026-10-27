# Postcollection completeness proof

`audit_complete.py` supplements the immutable C84 validator and analyzer. It
does not alter the 52-file snapshot, add observations, change the statistics,
exclude slow cases, tune configurations, or read source Parquet data. The
portable replayer runs this gate after frozen validation and analysis, before
creating real publication figures.

For a harvested archive, the standalone invocation is:

```sh
python research_reporting/audit_complete.py \
  --campaign /path/to/prestart/campaign.json \
  --evidence-root /path/to/prestart \
  --artifacts-root /path/to/campaign-results \
  --validation /path/to/replay/validation.json \
  --analysis /path/to/replay/analysis/analysis.json \
  --output /path/to/new-complete-audit
```

The validation argument is the portability adapter's canonical validation;
the raw relocated validation and adapter manifest remain separate evidence.
The output directory must be new. An ordinary audit failure produces a
reviewable `AUDIT_COMPLETE.json` and a nonzero exit status.

Real campaigns must match an exact campaign/prestart checksum pair in the
committed `campaign_identities.json` registry and the unchanged C84 source.
The report binds the registry and identity-helper hashes among its actual read
inputs. Unknown campaigns are refused; synthetic test opt-in remains separate. The
original and aborted v1 pairs remain fixed historical identities. The one allowed
recovery identity, `h100-confirmatory-gclocker100-20261027`, additionally registers
its actual `EXECUTION_PROFILE.json` SHA256. No placeholder hash is accepted.

The gate reconciles 400 unique sessions with one successful attempt each,
9,360 successful outputs (2,340 warmups and 7,020 measured actions), 60 specific
translation rejections, 3,360 successful complex cleanup records and 60 failed
action cleanup records. It checks full file inventories in all three cache
observations, effective engine/thread/Spark/Polars GPU settings, captured
Python/distribution/source inventories, materialized-output and scientific-gate
hashes, and every action's oracle-audit coverage. Residency remains an
observation: a low fraction is retained without a new exclusion threshold.

Full-byte integrity evidence must cover each scale and file count, with one
gate preceding the first session and another following the last session before
recorded campaign completion. The auditor checks these recorded execution
proofs against frozen manifests and oracle declarations; it does not claim to
rehash omitted Parquet files or the installed plugin binary locally.

`AUDIT_COMPLETE.json` uses schema `research-complete-audit-v1` and includes:

- `status`, `campaign_sha256`, `journal_sha256`, and exact aggregate `counts`;
- explicit failure `findings` and a status for every examined session;
- `input_files_sha256`, mapping each actual absolute local input path read by
  the audit to its SHA256, with mutation during the audit rejected;
- `source_sha256`, the auditor code SHA256, plus artifact root mappings.

`HOST_RESULT_SENSITIVITY.csv` and `.md` retain all 32 descriptive host-result
configuration/condition pairs, including the six unavailable Polars GPU complex
suite pairs. They show the corresponding primary action estimate where defined,
pointwise intervals and direction changes. They add no secondary hypothesis
tests or Holm-adjusted superiority claims.

Synthetic replay tests require an explicitly tagged campaign and
`--allow-synthetic`; their report identifies the synthetic scope, their CSV
contains a synthetic flag, and their Markdown carries a visible warning. Such
outputs are test artifacts and must never be presented as experimental results.

The recovery audit also requires a sealed successful separate SF100 Spark CPU
diagnostic and the unchanged 1,654 physical file identities, manifests and
independent oracle JSON/log bytes. The diagnostic validation must retain all six
complex queries and all 12 oracle comparisons (one warmup and one measured
action per query), 12 verified cleanup records, the original SF100 manifest and
oracle hashes, and the actual driver flags. These observations never enter the
primary cohort. Its harvested parent directory must contain
`EXECUTION_PROFILE_GATES.jsonl`: exactly 24 ordered idle-host receipts preserving
the prestart readiness prefix. The readiness pair precedes timing, each block
pair brackets all 40 sessions, and the final pair brackets journal completion.
Each receipt records the actual Java binary/libjvm/module-image/release hashes,
version and inherited launcher environment, matching the frozen profile. Global
Java-option variables must be absent; the fixed flags use `SPARK_SUBMIT_OPTS`.
These probes run outside queries.

Every one of the 160 Spark CPU/GPU session logs must contain one actual driver
`PrintFlagsFinal` table showing `UnlockDiagnosticVMOptions=true`,
`GCLockerRetryAllocationCount=100`, `MaxHeapSize=103079215104`, `UseG1GC=true` and
`PrintFlagsFinal=true`. A block receipt cannot substitute for a missing child
driver table. The audit hashes every inspected profile, receipt and driver log
and records `execution_profile` with the registered SHA256, 160 driver logs, 24
gates and PASS only when the complete audit passes. Old failed, diagnostic and
recovery observations are never pooled. Historical archive verification does
not authorize a final recovery paper or webpage.
