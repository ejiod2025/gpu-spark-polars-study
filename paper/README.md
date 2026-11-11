# Paper: H100 four-engine study with a GPU-generation ladder (draft)

LaTeX source for the research paper. Written before any campaign produced
data, so every result is a placeholder macro. The methods section must match
`docs/H100_EXPERIMENT_DESIGN.md` (locked 2026-10-29; amendments A1–A3 committed
as `ee2e6a7`, file sha256 `6b7544ee…dcddff`; A4 committed as `afd07c0`, file sha256 `8650efdf…9776ef`); reflect any further amendment in
§3 of `main.tex`.

Hosts (amendments A2, A3): H100 (primary, unprefixed keys), A100, GH200 and B200, each
with its own complete campaign, never pooled. Host ids are `a100`, `h100`, `gh200`, `b200`.

## Files

| Path | Purpose |
|---|---|
| `main.tex` | Paper (article class, booktabs, natbib/plainnat) |
| `macros.tex` | `\stat`, `\ratioci`, `\ratiotext`, `\studyfig`, TPC disclaimer macros |
| `references.bib` | Verified references only (see header comment) |
| `CITATIONS_TODO.md` | Candidate references not yet verified, deliberately left out |
| `tools/statskeys.py` | Shared loading/formatting/key scanning (also used by `article/tools/fill_article.py`) |
| `tools/stats_to_tex.py` | `stats.json` → `generated/stats.tex`; fails on any missing key |
| `tools/sync_figures.py` | Copies F1–F10 from the plot output into `figures/`; fails on a missing figure |
| `generated/stats.tex` | Generated; do not edit or commit by hand |
| `figures/` | Generated copies of the campaign figures |

## Build

Requires `tectonic` (used for checking this draft) or `latexmk` with pdflatex and bibtex.

The `Makefile` defaults point at the committed final inputs
(`results/campaign-20261030/analysis/`), so `make stats figures pdf` rebuilds the final paper
reproducibly (see `FINAL_BUILD.md`). The general form, for other inputs:

```bash
cd paper
make pdf                       # draft: missing stats/figures show as boxed placeholders
# every additional host needs a stated outcome: its stats file, or a not-run reason
make stats STATS=../<h100-report>/stats.json \
           A100_STATS=../<a100-report>/stats.json B200_STATS=../<b200-report>/stats.json \
           GH200_NOT_RUN="no aarch64 build of the pinned plugin JAR" \
           XHOST=../<cross-host-output>      # reads xhost_<HOST>.json for each host that ran
make figures PLOTS=../<h100-plots> A100=ran GH200=not-run B200=ran XHOST_PLOTS=../<ladder-plots>
make pdf                       # final: any \stat key still missing is a LaTeX error
make keys                      # list every stats key the paper uses (the schema contract)
```

The final build refuses to proceed until the outcome of every additional host (A100, GH200,
B200) is stated: `--stats HOST=PATH` (ran) or `--not-run HOST=REASON` (not run), and
`sync_figures.py` likewise needs `--host HOST=ran|not-run` for all three. Giving both for one
host, or a cross-host file for a host whose campaign is not given, is an error. When at least one host ran, the title gains ", Replicated across
GPU Generations", and §4.7 shows the ladder tables and F11, with per-host paragraphs and an
appendix table for each host that ran; a host that did not run is listed with its reason
(§3.3, §4.7) and shows as "---" in the host tables. Title options for the author are in a
comment above `\title`.

Mechanism: `macros.tex` defines `\ifaonehundredran`, `\ifghtwohundredran`,
`\ifbtwohundredran` and `\ifladderran` (any of the three ran); `stats_to_tex.py` writes
`\<flag>ranfalse` for each host not run. Host text sits inside the conditional and between
`% BEGIN-RAN:<host>` / `% END-RAN:<host>` comment lines (`NOTRUN` for the else branch; `<host>`
is `a100`, `gh200`, `b200` or `any`; regions nest), so the key scanner knows which keys each
case needs. In tables, `\hoststat{host}{key}`, `\hostratioci{host}{prefix}` and
`\xhostratioci{host}{rest}` print the value if the host ran and "---" otherwise.
`stats_to_tex.py --list-keys --case a100=ran,gh200=not-run,b200=ran` lists one case.

Keys written by `stats_to_tex.py` itself from the command line (not by the analysis):
`hosts.<host>.status` ("ran" / "not run"), `<host>.not_run_reason`, and once all hosts are
decided `hosts.ran_list` ("A100, H100 and B200"), `hosts.other_ran_list`, `hosts.not_run_list`
("none" if empty) and `hosts.n_ran` (H100 included). It also compares each host's measured RAM,
GPU memory and vCPUs (`host.host_ram_gib`, `host.gpu_memory_gib`, `host.vcpus`) with the
provider listing quoted in A2/A3 (`statskeys.LISTING`; tolerance 1 GiB for memory, the listed GB
figure compared as GiB; vCPUs exact) and writes `hosts.<host>.<spec>.matches_listing` ("yes" /
"no") and `hosts.listing_note`, the hosts-table sentence naming any spec that differs. The table
prints measured values; a host that did not run shows the listing, marked `*`.

Prose summary keys (all computed from loaded keys, never typed): `<prefix>.verdict` ("above 1" /
"below 1" / "includes 1") for every interval; `<family>.<suite>.summary.{min,max,above,below,
includes,pattern,trend}` for the contrasts on every host, the bridge, E1/E4 and the cross-host
ratios; `fastest.<engine>.<suite>.won_text`; `ladder.*` (C3 range, GPU cross-host ranges,
integrity and coverage totals); `<host>.<c>.<suite>.sf300.vs_h100`; `bridge_summary.*`;
`gpu_mem_note.*`; `plan_variability.cells_text`; `cv.between_exceeds_within`; `order.intervals_*`.
The results prose uses these so that its wording follows the data.

For arXiv, upload `main.tex`, `macros.tex`, `references.bib` (or the generated
`main.bbl`), `generated/stats.tex` and `figures/`.

## How numbers get into the paper

- Write `\stat{c1.baseline.sf300.estimate}` for one value, `\ratioci{c1.baseline.sf300}` for
  "estimate [low, high]" in tables, and `\ratiotext{c1.baseline.sf300}` for running text.
- `stats.json` may be flat (dotted keys) or nested; nested objects are joined with `.`.
  Reserved top-level keys: `_display` (key → verbatim text), `_format` (key or glob → Python
  format spec such as `"{:.3f}"`); other `_`-prefixed keys are ignored.
- **No hand-filled values in real builds.** `_display` entries are manual values. They are
  accepted only from files marked synthetic (top-level `"synthetic": true`, or `_meta.synthetic`
  in `xhost_<HOST>.json`), in a draft with missing keys (`--allow-missing`), or with
  `--allow-manual-overrides`. Any manual value from a non-synthetic file watermarks every PDF page
  and the title block "MANUAL VALUES", and synthetic stats watermark "SYNTHETIC -- NOT RESULTS".
  `fill_article.py` applies the same rule and puts a banner at the top. Every number in a real
  build must come from the analysis output itself, including the `meta.*` integrity keys
  (contract agreed with the analysis engineer: `meta.input_gate`, `meta.amendments`,
  `meta.sessions_completed`, `meta.measured_trials`, `meta.outputs_compared`,
  `meta.outputs_failed`, `meta.max_rel_diff`, `meta.spark_gpu_plans_checked`,
  `meta.cleanup_barriers_passed`, `meta.campaign_start_utc`, `meta.campaign_end_utc`, one set per
  host stats.json).
- Default formats: contrast/bridge/A100 ratios 2 decimals; medians and legacy sums 3 decimals
  (seconds); CVs and order effects 1 decimal (percent); GPU memory 1 decimal (GiB); startup
  2 decimals; integers with thousands separators; `max_rel_diff` in scientific notation; strings
  verbatim (LaTeX-escaped); `null` prints as `n/a` with a warning.
- `analyze_campaign.py` writes its own detailed schema (`STATS_SCHEMA.md` in its output).
  Contrast keys (`c1`–`c4`, plus `c4_strict`) are identical in both. `statskeys.derive_paper_keys`
  maps the rest onto the paper's names (for example `side_study.ratio.*` → `bridge.*`,
  `medians.{suite}.{scale}.{query}.{engine}.geomean` → `median.{engine}.{suite}.{scale}.{query}`,
  winner and coverage counts, CV fraction → percent, GPU bytes → GiB, and the `host` identity
  block → `meta.gpu_name`, `meta.driver_version`, `meta.cpu_model`). A key given directly in
  stats.json overrides a derived one. The other `meta.*` integrity keys (outputs compared, plans
  checked, barriers, input gate, campaign window, amendments) are not derived; they must be
  present in each host's stats.json (see the manual-values rule above).
- Additional campaigns are namespaced: `--stats a100=PATH`, `gh200=PATH`, `b200=PATH` put that
  host's campaign keys (same schema, same derivation) under `<host>.`; each host file needs the
  same `meta.*` keys as the H100 file (`meta.gpu_name`, `meta.driver_version`, `meta.cpu_model`,
  `meta.input_gate`, ...). The cross-host comparison is `analyze_campaign.py`'s per-host
  `xhost_<HOST>.json` (keys `<engine>.<suite>.<scale>.{estimate,ci_low,ci_high,df,n_queries}`),
  loaded with `--stats xhost_<host>=PATH` as `xhost.<host>.<engine>.<suite>.<scale>.*` (host `a100`,
  `gh200` or `b200`; suite geometric mean of t_host/t_H100, below 1 = faster than the H100;
  Welch-type interval on the log scale). Files must not already carry their namespace prefix.
- The converter also fails if any `ci_low <= estimate <= ci_high` triple is out of order.
- Never type a result into `main.tex`. Prose that interprets results is marked `% TODO(results)`
  and `\pending{...}`; remove both when rewriting.

## Figures

Stems follow `plot_campaign.py` (copy with `tools/sync_figures.py`).

| Id | File stem(s) | Content |
|---|---|---|
| F1 | `f01_forest_sf300` (main text); `f01_forest_sf1`…`sf100` (appendix) | Per-query speedups with CIs, by contrast and suite |
| F2 | `f02_suite_speedup_vs_scale` | Suite geometric-mean speedup vs scale with CI bands |
| F3 | `f03_heatmap_baseline`, `f03_heatmap_complex` | Heatmaps of median times |
| F4 | `f04_gpu_coverage` | GPU coverage matrix (A1 outcome classes) |
| F5 | `f05_variability` | Within-session and between-round CV |
| F6 | `f06_order_effects` | Position and predecessor effects |
| F7 | `f07_gpu_memory` | Peak GPU memory |
| F8 | `f08_polars_version_bridge` | Polars 1.44.2 vs 2.0.0 bridge |
| F9 | `f09_a100_vs_h100` | A100 vs H100 GPU engines |
| F10 | `f10_headline_times` | Per-query absolute times (headline) |
| F11 | `f11_gpu_ladder` | GPU-generation ladder: t_host/t_H100 per engine, suite and scale (only if an additional host ran) |

`sync_figures.py --map <stem>=<other_stem>` handles a different plotter naming.

## Open decisions for the author

1. Author: Daniel Ejiofor (sole author), Boston University (Master's student), ejiod@bu.edu; no ORCID. Still pending: acknowledgements, target venue (arXiv category, e.g. cs.DB).
2. **TPC benchmark-name use.** Policies v6.20 §8.1.4 requires the TPC's written permission for
   derived work using the benchmark name, and "Derived from" wording; see
   `publication/NOTICE_RESEARCH.md`. The title and text say "TPC-H-derived". Confirm the
   permission status before public release.
3. Name of the derived workload in the disclaimer (`\workloadname` in `macros.tex`, currently
   "H100 study workload").
4. Licence of original contributions and the notice material that must accompany the artifact
   (unresolved; see `NOTICES.md`).
5. Repository URL and archive DOI for §7.
6. Pre-registration evidence: cited in §3.1 as commit `ee2e6a7` (pushed 2026-10-30, branch
   `H100_Test`, design file sha256 `6b7544ee23b4ad40d66c8ba0866772ce0fec0ca6a6db92b6435f064979dcddff`).
   The lock and A1–A3 first appear together in that commit, so they are not timestamped
   separately. A4 (known CUDA 12.9 Blackwell compiler defect, NVIDIA/cccl#11167 and the cudf
   workaround NVIDIA/cudf#23973, both verified) is cited by its own commit `afd07c0` (design file
   sha256 `8650efdfa0d282614430c5b0a8d895cf69af0a5f25506e4af5316e20529776ef`). Still open: confirm every host's round 1 started after the commit, cite any later
   amendment by its own commit, and consider an external archive (e.g. Zenodo) of the commit.
7. Title: choose among the options commented above `\title` once the ladder outcome is known.
