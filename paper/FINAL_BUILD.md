# Final build record: Paper 1 and the companion article

Paper sources committed as `c9e9014`; inputs committed as `99e2eba`
(`results/campaign-20261030/analysis/`). Tools: Tectonic 0.17.0, Python 3.10.18.

## Inputs

All under `results/campaign-20261030/analysis/`. These are the committed copies of the final
analysis outputs. SVG figures are omitted; the paper uses only .pdf and .png.

| Role | Path | sha256 |
|---|---|---|
| H100 (primary; includes the 2026-10-25 A100 study, the 4-round bridge, E1/E4) | `h100-final/stats.json` | `f8f0488b7254f8e315683d7773807b6158333894537039376aacc4f447ad9e71` |
| A100 campaign (4 main rounds) | `a100-main/stats.json` | `96c149788f725839d712a81b764b471a6560233271106579d2f7bc8042b710db` |
| GH200 campaign (bridge, E1/E4) | `gh200-final/stats.json` | `dd2762798859a84741569eae677d2d3681c1b9daac9f060b0e719b8bb62a81d4` |
| B200 campaign (4 main rounds) | `b200-main/stats.json` | `64f8c01a26d70b8b27268724e782b8db1252a129c284ddba730e0146029edb36` |
| Cross-host A100/H100 | `xhost-final/xhost_A100.json` | `1ee58766cf0a23c819f748f47b48073eb1096a343cc6f59c0cae40827a829747` |
| Cross-host GH200/H100 | `xhost-final/xhost_GH200.json` | `a84c698d677ee8c6baa9b45129d8353777e78a8ff7910f9197f5b95c7e59c454` |
| Cross-host B200/H100 | `xhost-final/xhost_B200.json` | `6be27d8bd0c125f5d28b8e661353b7e70eac2ea67efae5d5bd15e08f68ad0ae1` |

The same paths and hashes are written as comments at the top of `generated/stats.tex`. None of the
files is marked synthetic, and none carries `_display` overrides. So the PDF has no MANUAL VALUES or
SYNTHETIC watermark.

**Provenance check (2026-10-30).**
- The A100, B200 and three cross-host files are byte-identical to the files the reviewed build used.
- The H100 and GH200 files are later regenerations (`generated_at_utc` 14:52 UTC). The reviewed build
  recorded different hashes for them (`cc44d5a5…`, `0b950658…`).
- Every statistic the paper loads (45,955 printable values, including all 999 required keys) is
  identical between the two versions, and so is every figure file.
- Where the old file could be compared directly (the A100 and B200 regenerations), the only
  differing field was `generated_at_utc`.

## Figures

- F1–F10 and F2x: `h100-final/figs/`. These are `f01_forest_sf{1,10,30,100,300}`,
  `f02_suite_speedup_vs_scale`, `f02x_c1_e1_exploratory`, `f03_heatmap_{baseline,complex}`,
  `f04_gpu_coverage`, `f05_variability`, `f06_order_effects`, `f07_gpu_memory`,
  `f08_polars_version_bridge`, `f09_a100_vs_h100` and `f10_headline_times`.
- F11 (`f11_gpu_ladder`) and F12 (`f12_gpu_idle_fraction`): `xhost-final/figs/`.
- Copied as .pdf and .png (36 files). No fallback stem was used.

## Build

The `Makefile` defaults point at the committed inputs, so from `paper/`:

```bash
make stats figures pdf
```

This writes `generated/stats.tex`, `figures/` and `main.pdf`, all git-ignored. `make pdf` fixes
`SOURCE_DATE_EPOCH=1793361600` (2026-10-30 12:00 UTC; it also sets the printed date), so the PDF is
byte-reproducible. Without it, Tectonic embeds the build time and every compile gets a different hash.

Companion article, from the repository root (writes the git-ignored `article/ARTICLE.filled.md`):

```bash
C=results/campaign-20261030/analysis
python3 article/tools/fill_article.py --stats $C/h100-final/stats.json --stats a100=$C/a100-main/stats.json \
    --stats gh200=$C/gh200-final/stats.json --stats b200=$C/b200-main/stats.json \
    --stats xhost_a100=$C/xhost-final/xhost_A100.json --stats xhost_gh200=$C/xhost-final/xhost_GH200.json \
    --stats xhost_b200=$C/xhost-final/xhost_B200.json --output article/ARTICLE.filled.md
```

## Result

- `stats_to_tex.py`: all three hosts marked as ran; 57,333 statistics loaded; 999 keys required by
  the paper, all present. Final mode (`\statsfinaltrue`).
- Only warning: 3 required keys are null and print as n/a: `legacy.sum_medians.polars_gpu.complex.{sf30,sf100,sf300}`.
  This is expected, because the two unsupported queries leave no complete sum.
- Tectonic: exit 0, 38 pages, no undefined references or citations, no overfull boxes.
- Typeset check: every one of the 999 required keys was typeset, and no other key.
- `main.pdf` sha256 (with the fixed `SOURCE_DATE_EPOCH`):
  `1e7af9512cabad65841a1119eec740a07bf727d53309cbfb8626e4b63010c191`. Two `make pdf` runs gave this
  hash, and so did compiling the reviewed build (built from the earlier input files) with the same
  `SOURCE_DATE_EPOCH`. The PDF is therefore identical to the reviewed one apart from the build
  timestamp. The reviewed copy itself, compiled without a fixed date, had sha256 `29dc4f9c…bd2ac5a`.
- The filled article is byte-identical to the reviewed fill, with no missing placeholders.

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
