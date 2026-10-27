# Completed block 03: core evidence checkpoint

This is a verified copy of **one of ten planned blocks**, containing 40 fresh
sessions and 936 successful warmup/measured outputs. It is not a complete study,
statistical analysis, or substitute for the final post-run integrity gate.

`CORE_VERIFICATION.json` records every copied/derived file hash and the frozen
source/oracle identities. The frozen per-session validator rechecked all 40
sessions locally against the eight independent oracles. Every derived audit
matches its VM-created scientific gate after 192 explicitly recorded plan-source
path adaptations. Original JSON and plan bytes are retained.

`CHECKPOINT.json` declares the partial scope and original core-file inventory.
`JOURNAL_SNAPSHOT.json` captures the wider campaign at collection time; later
sessions remain outside this checkpoint. `CORE_AUDITS_RAW.json`,
`CORE_AUDITS_CANONICAL.json` and `PLAN_SOURCE_MAPPING.json` preserve the local
verification and location-only mapping. These files cannot supply the complete
400-session analysis gate.

This checkpoint includes result rows, plans, manifests, environment/configuration
records, schedules, session identities and scientific gates. Raw event logs,
telemetry and full process logs must still be harvested after collection ends.
No source Parquet, dependency binary or private key is included.

Reproduce the copy verification with the immutable pre-start source/oracle
archive and the original core tar:

```sh
python operations/verify_core_checkpoint.py \
  --archive evidence-research/20261026/h100-confirmatory-v1 \
  --checkpoint-tar /path/to/block03-core-checkpoint.tar \
  --output /path/to/new-core-checkpoint
```

The collector is `operations/collect_core_checkpoint.py`. It reads completed
metadata and plans only, without executing queries, tests or source-data hashes.
The complete raw bundle and reproducible publication figures remain pending.
