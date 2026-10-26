# Prospective campaign pre-start archive

This archive was prepared before any confirmatory performance session. The
immutable campaign contains **10 blocks, 400 fresh sessions and 26 primary
contrasts**. It binds code commit `c84ef6a07341f350dae6636ab709c1b65fd894e0`,
the prospective protocol, exact runtimes, input manifests, independent oracles,
and readiness diagnostics. It is not a completed performance result.

- `campaign.json`, `.sha256`, `.source/`: complete schedule, hash and frozen
  52-file executable/test/protocol/dependency snapshot.
- `readiness/`: all ten suite/configuration SF1 smoke sessions, **116 successful
  output comparisons** against independent SQL; five adversarial fixture runs.
  All core inner/outer/semi/anti cases pass, including an observed final GPU
  `LeftAnti` node in Spark. Polars GPU supports three of five diagnostics;
  rank/window and DOTALL-regex cases remain explicit strict rejections.
- `linux-tests.log`: **300 passed, one unavailable local fixture skipped,
  16 subtests passed** on the H100 Python 3.11 test environment.
- `oracles/`: every complete baseline/complex suite at all eight measured
  conditions, full ordered rows, SQL/manifest/source hashes, and retained
  progress files/logs. These are untimed correctness references.
- `INPUT_DOMAIN.json`: exact SQL counts find no null price/date/order/customer
  keys in `orders` at all five scales. This supports the tested-domain limit
  of the preserved original window port; the adversarial null-order mismatch
  remains documented in the semantic audit and regression tests.
- `PILOT_ORACLE_SUPPLEMENT.json`: later independent comparison of **all 936
  saved pilot outputs in 40 sessions**, with **zero mismatches**. This is a
  post-hoc correctness supplement. No pilot timing enters prospective inference.
- `operations/`: exact preparation and supplemental audit drivers.
- `PRESTART_MANIFEST.json`: pre-start file hashes and verified identities.

Original paths inside JSON are retained. For analysis after copying prospective
results, use `--artifacts-root` instead of rewriting hashed evidence. Raw smoke
events/logs are retained here; no private SSH key, authentication token, generated
Parquet dataset, installed virtual environment or plugin binary is included.

The source protocol defines action timing, the secondary common host-result
endpoint, the fixed stopping rule, paired-block t intervals, the 26-test Holm
family and honest unsupported-query coverage. Future performance results and
publication reporting are separate artifacts. Development remains private.
