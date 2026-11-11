# Final build record: Paper 1, PVLDB Vol. 20 version

Built on 2026-11-10 with Tectonic 0.17.0 and Python 3.14.4. Paper 1 was recorded with Python
3.10.18; the converter produced the same statistic count, and the input hashes it reports match
Paper 1's record.

## Inputs

The inputs are the same committed files as Paper 1 (`../paper/FINAL_BUILD.md`), all under
`../results/campaign-20261030/analysis/`. These are the `Makefile` defaults:

| Role | Path | sha256 |
|---|---|---|
| H100 (primary) | `h100-final/stats.json` | `f8f0488b7254f8e315683d7773807b6158333894537039376aacc4f447ad9e71` |
| A100 | `a100-main/stats.json` | `96c149788f725839d712a81b764b471a6560233271106579d2f7bc8042b710db` |
| GH200 | `gh200-final/stats.json` | `dd2762798859a84741569eae677d2d3681c1b9daac9f060b0e719b8bb62a81d4` |
| B200 | `b200-main/stats.json` | `64f8c01a26d70b8b27268724e782b8db1252a129c284ddba730e0146029edb36` |
| Cross-host A100/H100 | `xhost-final/xhost_A100.json` | `1ee58766cf0a23c819f748f47b48073eb1096a343cc6f59c0cae40827a829747` |
| Cross-host GH200/H100 | `xhost-final/xhost_GH200.json` | `a84c698d677ee8c6baa9b45129d8353777e78a8ff7910f9197f5b95c7e59c454` |
| Cross-host B200/H100 | `xhost-final/xhost_B200.json` | `6be27d8bd0c125f5d28b8e661353b7e70eac2ea67efae5d5bd15e08f68ad0ae1` |

- Figures come from `h100-final/figs/` and `xhost-final/figs/`. `sync_figures.py` copies the full
  Paper 1 set; this paper uses five of them.
- The same paths and hashes appear as comments at the top of `generated/stats.tex`.
- No file is synthetic and none has `_display` overrides, so the PDF carries no watermark.

Template files (official, unmodified; https://github.com/vldbproceedings/VLDB-Template at commit
`39c95f5c6fcbe652a83be24e4eff8f2134cd3fbc`):

| File | Version | sha256 |
|---|---|---|
| `acmart.cls` | v2.19 (2026/06/27) | `2f949e6e3f2a79f2cdc218b9dcdbaa7dd451adb4ee0be1af6dc7ebe00b318ea7` |
| `pvldb.sty` | v1.0 (2026/07/06) | `52ad3948f3231b31d2ac90f470c1759e7f96add7b577ec4e267dfbdc87fc4eb2` |
| `ACM-Reference-Format.bst` | 2.1 | `0590db3b8d255a3af91982ab710f3ace5fcaa52434020c9af8a4f141b00b68f3` |

## Build

```bash
cd paper-pvldb
make final      # = make check stats figures pdf pages
```

- `make check` confirms that `macros.tex` is byte-identical to `../paper/macros.tex`.
- `make stats` runs `../paper/tools/stats_to_tex.py ... --tex main.tex --output generated/stats.tex`.
  `make figures` runs `../paper/tools/sync_figures.py ... --output figures`. The tools in
  `../paper/` are used unchanged.
- `SOURCE_DATE_EPOCH=1793361600` (2026-10-30 12:00 UTC) is fixed in the Makefile.
- acmart loads `hyperxmp`. It writes the wall-clock time and a random instance UUID into the XMP
  metadata and ignores `SOURCE_DATE_EPOCH`. So `main.tex` fixes these values with `\hypersetup`
  (`pdfdate`, `pdfcreationdate`, `pdfmoddate`, `pdfmetadate`, `pdfdocumentid`,
  `pdfinstanceid`). Without that, every compile gives a different hash.

## Result

- `stats_to_tex.py`: all three additional hosts ran. It loaded 57,333 statistics; the paper needs
  371 keys, and all are present. Final mode (`\statsfinaltrue`). There is no null-key warning,
  because the legacy sums are not used.
- Tectonic exit 0. The log has no undefined references or citations and no overfull boxes. The
  remaining warnings are underfull boxes and font-size substitutions.
- **12 PDF pages.** The body runs from page 1 to the left column of page 11: 10 full pages plus
  part of one column (conclusion and acknowledgements). REFERENCES starts in the left column of
  page 11. The PVLDB limit is 12 pages excluding references. `make pages` prints the total page
  count and the page where the references start.
- Size: about 9,400 words before the references, counted with `pdftotext` and including captions,
  table cells and the title block. The running prose is about 8,000 words.
- `main.pdf` sha256: `42dadfa997bb93d6ca251350703d7c181dd287d72f569759dc03141788d2d26b`.
  These builds all gave this same hash:
  - from a clean folder (`generated/` and `figures/` removed);
  - a repeated `make pdf`;
  - a fresh clone of the repository;
  - the full chain from the raw trees (re-analysis and re-plotting, then
    `make final CAMPAIGN=<re-analysis>`; see `../REPRODUCE.md` tier (b)).

## Floats (10)

| # | Content | Source |
|---|---|---|
| Table 1 | The four arms | text |
| Table 2 | Hosts (`table*`; measured GPU memory, vCPU layout, RAM, driver, CPU) | stats |
| Table 3 | Queries | text |
| Table 4 | H100 contrasts C1–C4 by suite and scale, plus the Polars version-bridge column (`table*`) | stats |
| Table 5 | Ladder: SF300 contrasts and crossover per host (`table*`) | stats |
| Figure 1 | `f10_headline_times`: per-query SF300 times (`figure*`) | F10 |
| Figure 2 | `f01_forest_sf300`: per-query SF300 speedups with CIs (`figure*`) | F1 |
| Figure 3 | `f02_suite_speedup_vs_scale`: suite speedup vs scale (`figure*`) | F2 |
| Figure 4 | `f04_gpu_coverage`: GPU coverage matrix (`figure*`) | F4 |
| Figure 5 | `f11_gpu_ladder`: cross-host time ratios (`figure*`) | F11 |

## Open items (author)

See the checklist in `README.md`. In short:
- the Zenodo DOI (`TODO(zenodo-doi)`) and a pinned artifact tag;
- the root `README.md` should point to `REPRODUCE.md`, and its "private repository" wording should go;
- the raw-data archives on Zenodo, which `REPRODUCE.md` tier (b) refers to (`TODO(zenodo-doi)`);
- the CMT title tag, abstract registration by 2027-01-25, reviewer nomination and conflicts;
- Paper 2's "under submission" status and the arXiv identifier for the extended version;
- acknowledgements, and the TPC permission and `\workloadname` decisions inherited from Paper 1.
