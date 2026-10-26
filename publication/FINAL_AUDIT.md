# Completed owner-private publication audit

The completed experiment and owner-private publication are verified. Public release is a separate, unresolved audience/licensing decision; this completion does not assert public release clearance.

- [x] Original suite: five scales, 30 cases, 360 saved-output comparisons and 120 GPU plans. Its results remain separate from the complex suite.
- [x] Complex v2: three scales, 18 cases, nine completed sessions, 216 saved-output comparisons, 216 passed cleanup barriers and 72 final GPU plans. No cases excluded.
- [x] Every complex session uses verified_gc. Cleanup is outside query medians, inside process wall, and all matching Spark barriers observed zero remaining owned shuffle files/bytes.
- [x] Post-run inputs: all five scales, 1,773 files, 157,967,833,132 bytes; all original checksums match, no missing or extra Parquet files, measured manifests unchanged. The planned generator revalidation was replaced by a direct read-only call to the same generate_data.validate_existing validator, expanded to all five scales.
- [x] Seven-root evidence bundle: full v2 nine-session scope, no pending scales; v1 incident remains excluded from performance summaries. Exact runtime snapshots and curated oracle/lifecycle/integrity proofs are retained with numeric values and hashes preserved.
- [x] Final article includes all 18 case values, three totals and nine session-cost rows; its baseline results remain unchanged. Narrative link-context limits are explicit in the evidence bundle.
- [x] Final page has 18 complex rows, three totals, no pending scale, all three scale figures and the 1,773-file proof. Every displayed median/range matches the final CSV exactly.
- [x] Desktop interaction and mobile layout checked locally. The largest-scale figure is explicitly scoped. The final evidence ZIP bytes inside the deployment archive match the audited ZIP.
- [x] Clean deployment archive has no AppleDouble metadata. Its data and hosting manifest match the selected Site and source. Git is clean at the exact pushed commit below.
- [x] Native owner-private deployment succeeded at the study URL. Browser handoff reached its expected sign-in gate; authenticated live content was not read. Local final static content was checked, and native success verifies deployment of that exact archive/version under the Sites tool contract. No owner credentials were entered.
- [x] Temporary local preview server stopped after the hosted handoff.

Canonical machine-readable evidence: ../artifacts/complex/FINAL_COMPLETION.json and FINAL_DEPLOYMENT.json. The earlier provisional audit is retained as FINAL_AUDIT_PROVISIONAL.md.

Private study: https://a100-spark-polars-study-oct2026.ejiod.chatgpt.site

Source commit: 18f824e64f7504b59778491cd098d3d101362129

Evidence ZIP SHA-256: 60d9c9cab914300e47c99303cc24874f50826febd3a23ac3b7c5ba91a85f084b

Deployment archive SHA-256: 7a505ecaf5365bf0536a53b7c79da3f1f28c6292bb207aeb913a81240363696c

Native deployment: appgdep_6ade93b6021081919ee5411119a6ce79
