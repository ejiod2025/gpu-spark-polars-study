# Four-engine GPU-ladder campaign, 2026-10-30 — final analysis outputs

TPC-H-derived workload (DuckDB 1.5.6 `dbgen`); **not** a complete or certified TPC-H run.
Design: `docs/H100_EXPERIMENT_DESIGN.md` (pre-registered; commits `ee2e6a7`, amendments to `afd07c0`).

## Contents

| Path | What it is |
|---|---|
| `analysis/h100-final/` | Primary H100 campaign: 4 main + 4 side-study rounds (bridge, exploratory E1/E4, earlier-A100-study comparison) |
| `analysis/gh200-final/` | GH200 campaign: 4 main + 4 side rounds |
| `analysis/a100-main/`, `analysis/b200-main/` | A100 / B200 campaigns: 4 main rounds (side studies analysed separately when archived) |
| `analysis/xhost-final/` | Cross-host comparison of all four hosts (ladder, F11, telemetry F12) |
| `raw-manifests/` | SHA-256 manifests of the raw campaign trees (results, plans, event logs, telemetry) |

Each analysis directory holds `stats.json` (all numbers; schema in `STATS_SCHEMA.md`), `STATS_REPORT.md`,
`INTEGRITY.md`, `tables/` (CSV/Markdown/LaTeX) and `figs/` (PDF + PNG; SVG omitted from git).

## Integrity summary
4 hosts × 4 main rounds = 512 sessions, 0 failed; 8,832 cross-engine output comparisons, 0 outside tolerance
(rel 1e-8 / abs 1e-6); only the pre-declared Polars GPU coverage gaps (cx_q13, cx_window).

## Raw data
The raw campaign trees (~16 GB) are not in git. They are held off-host with verified checksums; the
manifests here identify every file. Regenerate analyses with `analyze_campaign.py --root <raw tree>`.
