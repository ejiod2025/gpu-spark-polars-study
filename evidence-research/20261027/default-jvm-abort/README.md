# Preserved default-JVM allocation failure

The replacement campaign stopped with 88 scientifically validated sessions (2,056 successful outputs), one failed Spark CPU session and 311 unstarted sessions. The first cx_q09 warmup at complex SF100 reported a Java heap-space allocation failure after GCLocker retry warnings; no successful output or measured record was returned by that session. The warnings identify an allocation path, not a proven root cause or actual failure-time heap occupancy.

All 3,349 original evidence files are preserved in the verified off-host raw archive referenced by TRANSFER.json and OFFHOST_VERIFIED.json. This Git checkpoint includes the original failed session/results/log, full aborted journal and every archived file hash. The complete raw archive remains in ignored local incident storage. No original attempt, journal or timing was erased or retried.

A post-abort full byte gate passed all 1,654 unchanged input files; source and Python runtimes also matched their frozen identities. This does not make the aborted campaign complete. The separately predeclared GCLocker100 feasibility diagnostic and fresh recovery cohort have distinct identities, and no default-profile timing is pooled into recovery inference. See docs/H100_GCLOCKER100_RECOVERY_PROTOCOL.md.
