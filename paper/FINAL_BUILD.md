# Final build record: Paper 1 and the companion article

Built 2026-10-30 from the five final analysis directories. Repository HEAD at build time:
`afd07c0` on `H100_Test`; the paper and article sources were uncommitted working-tree files.
Tools: Tectonic 0.17.0, Python 3.10.18.

## Inputs

All under `rerun/analysis/`. Superseded and not used: every `*-main` other than `a100-main` and
`b200-main`, every `*-r1`, and `xhost-partial`.

| Role | Path | sha256 |
|---|---|---|
| H100 (primary; includes the 2026-10-25 A100 study, the 4-round bridge, E1/E4) | `h100-final/stats.json` | `cc44d5a5b03b63efc69ef0cabe5372538a89878938e5c7d3b3b291ad421cc0d5` |
| A100 campaign (4 main rounds) | `a100-main/stats.json` | `96c149788f725839d712a81b764b471a6560233271106579d2f7bc8042b710db` |
| GH200 campaign (bridge, E1/E4) | `gh200-final/stats.json` | `0b950658135edd09abfc195e04057bdfe18ec9f22cb3fc38856c271d4b554eec` |
| B200 campaign (4 main rounds) | `b200-main/stats.json` | `64f8c01a26d70b8b27268724e782b8db1252a129c284ddba730e0146029edb36` |
| Cross-host A100/H100 | `xhost-final/xhost_A100.json` | `1ee58766cf0a23c819f748f47b48073eb1096a343cc6f59c0cae40827a829747` |
| Cross-host GH200/H100 | `xhost-final/xhost_GH200.json` | `a84c698d677ee8c6baa9b45129d8353777e78a8ff7910f9197f5b95c7e59c454` |
| Cross-host B200/H100 | `xhost-final/xhost_B200.json` | `6be27d8bd0c125f5d28b8e661353b7e70eac2ea67efae5d5bd15e08f68ad0ae1` |

The same paths and hashes are written as comments at the top of `generated/stats.tex` by
`stats_to_tex.py`. None of the files is marked synthetic, and none carries `_display` overrides.
So the PDF has no MANUAL VALUES or SYNTHETIC watermark.

## Figures

- F1–F10 and F2x (`f01_forest_sf{1,10,30,100,300}`, `f02_suite_speedup_vs_scale`,
  `f02x_c1_e1_exploratory`, `f03_heatmap_{baseline,complex}`, `f04_gpu_coverage`, `f05_variability`,
  `f06_order_effects`, `f07_gpu_memory`, `f08_polars_version_bridge`, `f09_a100_vs_h100`,
  `f10_headline_times`): `rerun/analysis/h100-final/figs/`.
- F11 (`f11_gpu_ladder`) and F12 (`f12_gpu_idle_fraction`): `rerun/analysis/xhost-final/figs/`.
- Copied as .pdf and .png (36 files). No fallback stem was used.

## Build commands

From the repository root (`R=rerun/analysis`):

```bash
cd paper
make stats STATS=../$R/h100-final/stats.json A100_STATS=../$R/a100-main/stats.json \
     GH200_STATS=../$R/gh200-final/stats.json B200_STATS=../$R/b200-main/stats.json \
     XHOST=../$R/xhost-final
make figures PLOTS=../$R/h100-final/figs XHOST_PLOTS=../$R/xhost-final/figs A100=ran GH200=ran B200=ran
make pdf
cd ..
python3 article/tools/fill_article.py --stats $R/h100-final/stats.json --stats a100=$R/a100-main/stats.json \
    --stats gh200=$R/gh200-final/stats.json --stats b200=$R/b200-main/stats.json \
    --stats xhost_a100=$R/xhost-final/xhost_A100.json --stats xhost_gh200=$R/xhost-final/xhost_GH200.json \
    --stats xhost_b200=$R/xhost-final/xhost_B200.json --output article/ARTICLE.filled.md
```

The reviewed build was made the same way in a clean copy of `paper/` (`/tmp/ladder-build/final/`), so
no generated files were written into the repository. Equivalent direct commands:
`tools/stats_to_tex.py --stats ... --stats xhost_<host>=...`,
`tools/sync_figures.py --plots ... --cross-host-plots ... --host a100=ran --host gh200=ran --host b200=ran`,
and `tectonic -X compile main.tex`.

## Result

- `stats_to_tex.py`: all three hosts marked as ran; 57,333 statistics loaded; 999 keys required by
  the paper, all present. Final mode (`\statsfinaltrue`).
- Only warning: 3 required keys are null and print as n/a: `legacy.sum_medians.polars_gpu.complex.{sf30,sf100,sf300}`.
  This is expected, because the two unsupported queries leave no complete sum.
- Tectonic: exit 0, 38 pages, no undefined references or citations, no overfull boxes.
- Typeset check: every one of the 999 required keys was typeset, and no other key.
- PDF: `/tmp/ladder-build/final/main.pdf`, sha256
  `29dc4f9c68cf35ce66671ce9d1716b10c3b0c3335dcea5bcfd7034357bd2ac5a`.
- Last text fixes before this build: generated scale lists that are identical on both suites or on
  every host collapse into one phrase (derived keys `<prefix>.both.*_text`,
  `ladder.<c>.<suite>.<kind>_text`, `<host>.crossover.c4.text`, `c12.complex_larger_text`), and round
  counts read "N excluded, M repeated" (`meta.rounds_excluded_count`, `meta.rounds_repeated`). A scan
  of the rendered text found no repeated scale list.
- Article: `/tmp/ladder-build/final/ARTICLE.filled.md`, with no missing placeholders.

## Remaining TODOs before submission

Author details:
- Affiliation and email (title block, `\todoplaceholder`), ORCID, target venue (arXiv category,
  e.g. cs.DB) and acknowledgements (funding, compute provider).
- Final title: options are in a comment above `\title`. The current title is option (a).
- Repository URL and archive DOI (§7 artifact paragraph; article "Read more" links) and the
  article's publication date.
- TPC written-permission status for benchmark-name use, the final name of the derived workload
  (`\workloadname`), and the licence and notice material (see `README.md`, open decisions).
- Optional related work the author knows of (§2 comment).

Citations still unverified, deliberately not cited (`CITATIONS_TODO.md`):
- A citable cuDF / RAPIDS paper or release DOI, and a peer-reviewed description of the Spark GPU plugin.
- The NVIDIA H100 whitepaper, the GH200 whitepaper (the product page is cited) and the Blackwell
  architecture documentation.
- Independent (non-vendor) Polars GPU evaluations. The §2 claim is worded "to our knowledge" and
  must be re-checked.
- Further GPU join and aggregation papers.
- The earlier A100 study itself (no public URL yet).
- Lambda instance documentation. It was checked on 2026-10-30 but is not cited: it now lists the A100
  instance with 220 GiB RAM, against the 200 GiB quoted in amendment A3. The paper compares the
  measured values with the design's quoted figures.
