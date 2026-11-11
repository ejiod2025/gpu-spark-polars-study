# Licensing of this repository

Copyright (c) 2026 Daniel Ejiofor. Different parts carry different terms.

| Material | License |
|---|---|
| Author's original source code (runner, worker, analysis, plotting, gates, setup scripts, tests) | **MIT** — see [`LICENSE`](LICENSE) |
| Author's papers (`paper/`, `paper2/`), article (`article/`), documentation (`docs/`), and the author's results and figures (`results/`, `reports/`, `evidence/` analysis outputs) | **CC BY 4.0** — <https://creativecommons.org/licenses/by/4.0/> |
| Query definitions and workload text derived from TPC-H sources (e.g. `workloads.py`, the Q9/Q13-derived parts of `complex_joins.py` / `complex_analytics.py`) and any TPC-origin material | **Not relicensed.** Subject to the TPC terms (TPC EULA 2.2.0 and TPC Policies) and the DuckDB notices — see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) |
| Third-party software and documentation referenced or quoted | Their own licenses |

Where a file mixes original code with TPC-derived query text, the MIT grant covers only the original
portions. Nothing here grants rights in TPC material, TPC benchmark names, or third-party trademarks.

## TPC benchmark name

This study's workload is **derived from the TPC-H Benchmark** and its results are **not comparable to
published TPC-H Benchmark results**; they do not comply with the TPC-H Benchmark Specification. TPC
Policies require written permission for using the benchmark name to describe derived work; that
permission status is tracked in `NOTICES.md`.
