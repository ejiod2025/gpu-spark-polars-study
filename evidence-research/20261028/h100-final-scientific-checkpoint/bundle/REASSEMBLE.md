Completed research evidence

Verify every part with `sha256sum -c CHUNKS.sha256` (macOS: `shasum -a 256 -c CHUNKS.sha256`).

Reassemble in lexical part order: `cat evidence.tar.gz.part-* > evidence.tar.gz`.

The joined SHA256 must be `0334816271a422b2b95a71cb0a346f8f2da801a2c194d4ef7b71456459e45c0f`. Verify before extracting.

Extract into a new directory with `tar -xzf evidence.tar.gz`. Files are under `evidence/`; all original JSON paths and bytes are preserved. Use `evidence/campaign-results` as the replay artifact root.

Use the separate one-command replay to regenerate correctness, analysis and figures. A bundle PASS certifies archive integrity and its completion gates, not public-release clearance.
