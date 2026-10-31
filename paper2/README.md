# Paper 2: what is the GPU speedup measured against? (draft)

A secondary analysis of the pre-registered four-host study. Paper 1 (`../paper/`) is the
primary report and is cited as the companion paper. This paper adds no measurements. It
re-examines the CPU side of the speedups:

1. **Version lock-in.** cudf-polars 26.10.2 requires Polars <1.45, so C1/C4 use Polars 1.44.2
   on the CPU. The pre-registered RQ5 bridge (1.44.2 vs 2.0.0) and the exploratory E1/E4
   re-baselining show how much this matters.
2. **Equal threads, unequal cores.** `--cpus 26` puts 26 threads on 13 physical cores on the
   H100 and B200 (SMT 2), and on 26 cores on the A100 and GH200. Cross-host time ratios and an
   exact factorization `C_host/C_H100 = X_cpu/X_gpu` show how much of each host's speedup
   difference comes from the CPU side.
3. **The earlier A100 study** as a case of how baseline choices carry into later comparisons.
4. **A reporting checklist.**

Pre-registered and post-hoc analyses are separated in Table 1 (`tab:status`). Every post-hoc
number is labelled "exploratory" in its sentence, caption or table header.

## Files

| Path | Purpose |
|---|---|
| `main.tex` | Paper (article class, natbib/plainnat) |
| `macros.tex` | Paper 1's `\stat`/`\ratioci`/`\ratiotext`, watermark and TPC macros, plus Paper 2's condition flags and `\studyfigalt`/`\iffigure` |
| `references.bib` | Verified references only (see header comments) |
| `CITATIONS_TODO.md` | Candidate references not yet verified |
| `tools/stats_to_tex.py` | Host `stats.json` files + cross-host files (+ optional Paper 2 file) → `generated/stats.tex` and `generated/conditions.json`. Imports `../paper/tools/statskeys.py` unchanged |
| `tools/sync_figures.py` | Copies the figures listed below into `figures/`, using `generated/conditions.json` |
| `generated/`, `figures/` | Build products; do not edit |

## Build

```bash
cd paper2
make final     # stats + figures + pdf (tectonic, or latexmk)
make inputs    # show which analysis directories are used
make keys      # every stats key the paper uses (all conditional cases)
make pdf       # without generated/stats.tex: a draft with boxed placeholders
```

Inputs default to `../rerun/analysis`:

- `h100-final`, `gh200-final` and `xhost-final`.
- For the A100 and B200: `a100-final` / `b200-final` when present, which have the side-study
  bridge; otherwise `a100-main` / `b200-main`, which have none.
- `paper2-final/paper2.json` plus `figs/`, if present.

Override any of them with `H100_DIR=`, `A100_DIR=`, `GH200_DIR=`, `B200_DIR=`, `XHOST_DIR=` or
`EXTRA_DIR=`.

The final build fails in three cases:

- a key the text uses is missing (LaTeX error in final mode);
- a required figure is missing;
- a **claim check** fails.

Claim checks are statements the text makes in words, such as "every bridge interval below 1"
or "the GPU-side ratio is further from 1 in every cell". They are re-checked against the
inputs on every build and marked in `main.tex` with the invisible `\claim{<id>}` (an id without a check is an error). The list is in `claims()` in
`tools/stats_to_tex.py`. If new inputs break a claim, rewrite the sentence; don't relax the
check.

## Conditions (inputs that may or may not exist)

| Condition | True when | Effect |
|---|---|---|
| `bridge_<host>` (a100, h100, gh200, b200) | `<host>.side_study.meta.available` and `exploratory.e1.meta.available` are true and the bridge has an SF300 estimate | Bridge rows (Table 2), E1/E4 rows (Table 3), crossover cells (Table 4), F8 subfigure, gap sentences |
| `bridge_extra` | A100 or B200 bridge present | Second row of the F8 figure |
| `pcheck` | `protocol_check.*` keys in the Paper 2 file | §4.1 session check; threats paragraph |
| `decomp` | `decomp.*` keys in the Paper 2 file | F21 figure; C1-complex factorization and its claim |

In the source, conditional text sits between `% BEGIN-IF:<cond>` / `% END-IF:<cond>` lines (or
`IFNOT`), inside the matching `\if<flag>` (`\ifbridgeaonehundred`, `\ifpcheck`, ...). This lets
the key scanner require only the keys that are typeset.

## Derived keys (`p2.*`)

These are computed in `tools/stats_to_tex.py` from loaded keys only, never typed in:

- `p2.<host>.cores_used`, `p2.<host>.layout` and `p2.<host>.hypervisor_text`: from `host.*`.
- `p2.<host>.{c1,c4,e1,e4}.<suite>.first_above` and `.sustained`: the first scale whose 95%
  interval lies entirely above 1. This is the pre-registered RQ3 rule, applied descriptively.
- `p2.<host>.gap.<suite>.<scale|min|max>` and `p2.gap.max`: 100(1 − E1/C1), from point
  estimates.
- `p2.<host>.e1_below_c1.<suite>`, `p2.<host>.reversal.<suite>` and `p2.reversal.list`. A
  reversal is a scale where C1's CI is above 1 and E1's is below 1.
- `p2.bridge.<suite>.{min,max}`, `p2.bridge.cx_window.{min,max}` and `p2.bridge.hosts_list`.
- `p2.<host>.bridge_gpuq.<suite>.<scale>` and `p2.bridge_gpuq.<suite>.{min,max}`: the bridge
  over E1's queries, as the geometric mean of per-query bridge estimates (point estimates only).
- `p2.<host>.{c1,c2}.<suite>.<scale>.speedup_ratio` and `.identity`: C_host/C_H100, and whether
  the factorization is exact (the query sets match).
- `p2.pcheck.*`: a summary of the protocol check, when present.
- `p2.<host>.pcheck.{min,max,max_pct,n_excluding_1,excluding_1_list}`: K1 per host.
- `p2.<host>.e1adj.<suite>.<scale>`, `.first_above_point`, `p2.<host>.e1adj_gap.*`,
  `p2.e1adj_gap.max`: the session-adjusted E1 = E1/K1 (point estimates).
- `p2.<host>.e1adj_ci.<suite>.first_above`: the same, from K4 intervals
  (`exploratory_adj.e1.*`, condition `e1adj`).
- `p2.verdict.{moves_list,unchanged_hosts,n_moves}`, `p2.verdict_adj.{moves_list,at_first_list}`,
  `p2.verdict_adjci.moves_list` and `p2.reversal.detail`: the generated text of the "where the
  verdict changes" paragraph. It lists every host with a bridge, so it extends to the A100 and
  B200 without edits.
- Paper 1-style `<host>.bridge|exploratory.e1|exploratory.e4.<suite>.summary.*` for the
  non-primary hosts.

## Figures

| Target in `figures/` | Source | Used |
|---|---|---|
| `f08_bridge_<host>` | `<host>/figs/f08_polars_version_bridge` | Fig. 1, one panel per host with a bridge |
| `f02x_<host>` | `<host>/figs/f02x_c1_e1_exploratory` | Fig. 2 fallback, when F20 is absent |
| `f11_gpu_ladder` | `xhost-final/figs/f11_gpu_ladder` | Fig. 3 |
| `f20_baseline_sensitivity` | `paper2-final/figs/` (requested from analysis-engineer) | Fig. 2, when present |
| `f21_speedup_decomposition` | `paper2-final/figs/` (requested) | Fig. 4, when `decomp` |

## Paper 2 analysis file

`rerun/analysis/paper2-final/` was delivered by analysis-engineer: `paper2.json` (schema in
`PAPER2_SCHEMA.md`), plus F20 and F21 in `figs/`. It contains:

- `protocol_check.<host>.<suite>.<scale>.*`: side-study Polars 1.44.2 / main-round Polars 1.44.2
  (Welch-type).
- `decomp.<host>.<c>.<suite>.<scale>.{speedup_ratio,cpu_side,gpu_side}.*` on matched query sets.
- `sensitivity.*`: the data behind F20.

It is now regenerated from all four `-final` campaigns. The cross-host file is
`xhost-final-v2`, which is identical to the committed `xhost-final` apart from three provenance
keys; the Makefile prefers it when it exists. With all four bridges present, every condition is
true. Statements that held only on the H100/GH200 are now generated exception phrases:
`p2.bridge.below_phrase`, `p2.e1.below_phrase`, `p2.e4.below_phrase`, `p2.k1cmp.bridge_phrase`,
`p2.gap.by_host` and `p2.pcheck.by_host`. Claims check them for consistency (for example,
every cell where E1 is not below C1 has a bridge interval that includes 1).

## Open decisions for the author

1. Acknowledgements and target venue (e.g. arXiv cs.DB or cs.PF, or a benchmarking workshop such
   as DBTest or TPCTC). Author details are set: Independent Researcher, daniel@siriusmed.io; no ORCID.
2. Title: the options are in a comment above `\title`.
3. Same TPC questions as Paper 1 (`../paper/README.md` item 2): benchmark-name permission and
   the `\workloadname`.
4. Citing Paper 1: `ejiofor2026companion` is "in preparation"; replace it with the arXiv id
   once posted, and align the title if Paper 1's changes.
5. The description of the earlier A100 study (§4.3) says it ran "before the design was locked"
   and "was not pre-registered". Please confirm the second statement.
