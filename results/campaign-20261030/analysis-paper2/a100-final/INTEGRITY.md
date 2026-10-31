# Integrity counts

integrity counts only: no timings, no ratios; a failed query is a coverage outcome (A1) unless its class is error/timeout/session.

| Round | Included | Gate verdict | Sessions found / expected | Readable | Non-zero return | Queries | OK | Failed (class: n) | Incomplete | Session-level | Invalid |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round-1 | yes | pass | 32 / 32 | 32 | 0 | 192 | 186 | unsupported_operation: 6 | 0 | 0 | 0 |
| round-2 | yes | pass | 32 / 32 | 32 | 0 | 192 | 186 | unsupported_operation: 6 | 0 | 0 | 0 |
| round-3 | yes | pass | 32 / 32 | 32 | 0 | 192 | 186 | unsupported_operation: 6 | 0 | 0 | 0 |
| round-4 | yes | pass | 32 / 32 | 32 | 0 | 192 | 186 | unsupported_operation: 6 | 0 | 0 | 0 |

| Engine | Sessions | Queries | OK | Failed (class: n) | Incomplete | Session-level | Invalid |
|---|---|---|---|---|---|---|---|
| Polars CPU | 32 | 192 | 192 | – | 0 | 0 | 0 |
| Polars GPU | 32 | 192 | 168 | unsupported_operation: 24 | 0 | 0 | 0 |
| Spark CPU | 32 | 192 | 192 | – | 0 | 0 | 0 |
| Spark GPU | 32 | 192 | 192 | – | 0 | 0 | 0 |

Measured trials per OK cell: [3]. Cleanup barriers not passed: 0.

## Operational parameters recorded by preflight (not design parameters)

- preflight preflight-host-2 (host rehearsal) ran with --min-free-gib 250: a recorded operational threshold for the host's free-disk check, not a design parameter

## Sources, gaps and disagreements of the meta values

- meta.run_commit is null: run_campaign.py recorded no git commit (no git checkout on the host); the design commit was resolved from the design document's sha256
