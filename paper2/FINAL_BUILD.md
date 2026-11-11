# Final build record: Paper 2

Paper 2 sources: `paper2/`, not yet committed. Inputs: the committed analysis copies under
`results/campaign-20261030/`. Tools: Tectonic 0.17.0, Python 3.10.18. Paper 1's
`paper/tools/statskeys.py` is imported unchanged.

## Inputs

| Role | Path (under `results/campaign-20261030/`) | sha256 |
|---|---|---|
| H100 (primary; bridge, E1/E4, the 2026-10-25 A100 study) | `analysis/h100-final/stats.json` | `f8f0488b7254f8e315683d7773807b6158333894537039376aacc4f447ad9e71` |
| GH200 (bridge, E1/E4) | `analysis/gh200-final/stats.json` | `dd2762798859a84741569eae677d2d3681c1b9daac9f060b0e719b8bb62a81d4` |
| A100 (main rounds + side study, E1/E4) | `analysis-paper2/a100-final/stats.json` | `6c577951f22382dd9017bfcc4728d6b5d21c8ed33086b9a3b90a486e1febcf04` |
| B200 (main rounds + side study, E1/E4) | `analysis-paper2/b200-final/stats.json` | `20af271822e7fc055e6f4a7ca86e843d352cecdf0faa47ff12168dbe9cb0e59a` |
| Cross-host A100/H100 | `analysis-paper2/xhost-final-v2/xhost_A100.json` | `1ee58766cf0a23c819f748f47b48073eb1096a343cc6f59c0cae40827a829747` |
| Cross-host GH200/H100 | `analysis-paper2/xhost-final-v2/xhost_GH200.json` | `a84c698d677ee8c6baa9b45129d8353777e78a8ff7910f9197f5b95c7e59c454` |
| Cross-host B200/H100 | `analysis-paper2/xhost-final-v2/xhost_B200.json` | `6be27d8bd0c125f5d28b8e661353b7e70eac2ea67efae5d5bd15e08f68ad0ae1` |
| Paper 2 analysis (K1 protocol check, K2 factorization, K4 session-adjusted E1/E4, gap keys, F20 data) | `analysis-paper2/paper2-final/paper2.json` | `b2e8f268666204e928d318cb19bf788287a702672a767194325984bcca0c61f8` |

- **Hash records.** The same paths and hashes are written at the top of `generated/stats.tex`.
- **Match with Paper 1.** The H100 and GH200 files are the same files Paper 1 uses (same hashes as in
  `paper/FINAL_BUILD.md`).
- **Cross-host files.** The three cross-host JSON files are byte-identical to Paper 1's
  `analysis/xhost-final/` copies. `xhost-final-v2` differs from the Paper 1 record only in
  provenance keys of `cross_host.json`, which Paper 2 does not load.
- **Pre-commit copies.** Every input above is byte-identical to the working copy under
  `rerun/analysis/` that the reviewed builds used.
- **No synthetic or manual values.** No file is marked synthetic and none carries `_display`
  overrides, so the PDF carries no MANUAL VALUES or SYNTHETIC watermark.

## Figures

The build copies 11 figures (.pdf and .png) into `figures/`; none is drawn by Paper 2:

| Stems | Source |
|---|---|
| `f08_bridge_{a100,h100,gh200,b200}` | each host's `figs/f08_polars_version_bridge` |
| `f02x_{a100,h100,gh200,b200}` | each host's `figs/f02x_c1_e1_exploratory`. These are copied, but the paper uses F20 instead; they are the fallback only. |
| `f11_gpu_ladder` | `analysis-paper2/xhost-final-v2/figs/` |
| `f20_baseline_sensitivity`, `f21_speedup_decomposition` | `analysis-paper2/paper2-final/figs/` |

## Build

From `paper2/`, with the Makefile defaults pointing at the committed inputs:

```bash
make final        # = make stats figures pdf
make inputs       # print the input directories
```

- **Outputs.** The build writes `generated/stats.tex`, `generated/conditions.json`, `figures/` and
  `main.pdf`. All are git-ignored (`paper2/.gitignore`).
- **Fixed date.** `make pdf` fixes `SOURCE_DATE_EPOCH=1793361600` (2026-10-30 12:00 UTC; it also
  sets the printed date).
- **Reproducibility.** The PDF is byte-reproducible. Two consecutive `make pdf` runs gave the same
  hash.
- **Don't call Tectonic directly.** Running `tectonic` without the fixed epoch embeds the build
  time and changes the hash.

## Result

- **`stats_to_tex.py`.**
  - All four hosts and all cross-host files were loaded: 73,850 statistics.
  - 932 keys are required by the text in this case, and all are present. Final mode
    (`\statsfinaltrue`).
  - No warnings, and no required key is null.
- **Conditions.** All are true: `bridge_a100`, `bridge_h100`, `bridge_gh200`, `bridge_b200`,
  `bridge_extra`, `pcheck`, `decomp` and `e1adj`.
- **Claim checks.** 29 are defined and all 29 hold against these inputs. The 29 ids cited in
  `main.tex` with `\claim{}` all exist, and the build would fail on an unknown id.
  - **Four-host change.** When the four-host inputs arrived, the old "bridge below 1 everywhere",
    "E1 below C1 everywhere" and "E4 below C4 everywhere" checks failed, because of B200 baseline
    SF1: bridge 1.03 [0.98, 1.08].
  - **Replacements.** Those checks were replaced by `bridge-never-slower`,
    `e1-exceptions-explained`, `e4-exceptions-explained`, `e1-e4-same-exceptions` and
    `e1-exceptions-k1`.
  - **Text.** The exception is stated once, in §4.1, and referred back to elsewhere.
- **Analysis cross-checks.** These claims assert that the analysis' own derived keys equal the
  converter's derivation:
  - `k1cmp-agrees`: the gap keys, to 1e-9.
  - `e1adj-matches`: K4 equals E1/K1, to 1e-6.
  - `speedup-identity`: the factorization, to 1e-9.
  - `decomp-identity`: the C1 complex decomposition.
- **Tectonic.** Exit 0, 21 pages, no undefined references or citations, no overfull boxes.
- **Abstract.** 243 words in the rendered PDF.
- **`main.pdf` sha256** (fixed `SOURCE_DATE_EPOCH`):
  `100a6e31df066cbfd0bc6b8d580997527782441f579b2a142ccb8a111aa08e0d`. This is the build with author details;
  two consecutive `make pdf` runs gave this hash. The approved review build, without author details,
  was `80ae130a…f1e42ba`.

## Remaining TODOs before submission

Author details:
- Author details are set from the author: "Boston University, Boston, MA, USA", `ejiod@bu.edu` (updated 2026-11-10), no ORCID. Current reproducible PDF sha256: afbbdce09964bc1af48ecb94c2596d9d65dcf8808060aac3c432a71c6841987d.
  The ORCID placeholder is removed.
- Still open: acknowledgements (funding, compute provider; `\todoplaceholder` in the
  acknowledgements section) and the target venue.
- Final title: the options are in a comment above `\title`; the current title is option (a).
- Archive DOI for the repository snapshot (§7, `% TODO(author)`).
- TPC questions shared with Paper 1:
  - written-permission status for benchmark-name use;
  - the name of the derived workload (`\workloadname`, currently Paper 1's "H100 study workload").
  The scope notice uses Paper 1's wording via the same macros.
- Citation of Paper 1: `ejiofor2026companion` is "Companion manuscript, in preparation", with Paper
  1's current title. Replace it with the arXiv id or venue once one exists.

Citations still unverified and deliberately not cited: see `CITATIONS_TODO.md`. The list:
- the cudf-polars Polars-2.0 upstream commit and the RAPIDS 26.12 schedule;
- provider documentation on what a vCPU is;
- empirical SMT studies for analytical engines;
- Bailey 1991;
- independent Polars GPU evaluations;
- the earlier A100 study;
- CPU product specifications.

One editorial item is left as a `% TODO(results)` comment in `main.tex`. It affects no number: an
optional descriptive sentence in §4.4 on Table 8 (the earlier A100 study against the A100
campaign).
