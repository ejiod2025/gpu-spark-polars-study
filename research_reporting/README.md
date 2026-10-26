# Static research figures

This reporting layer consumes a completed `research_analysis.py` output directory.
It does not run experiments or modify the frozen analysis. Install its local
dependencies in an analysis environment, then render all export formats:

```sh
python -m pip install -r research_reporting/requirements-local.txt
python research_reporting/render.py --analysis /path/to/analysis --output /path/to/figures
```

The output directory must be new. PNG, SVG and PDF exports include the baseline
suite comparisons, complex complete-suite comparisons with Polars GPU N/A,
descriptive query heatmaps, and a complete-case score/coverage table. The complete
26-row primary table is `primary_contrasts.csv`; `complete_case_scores.csv` keeps
all 40 configuration/condition cells. `CAPTIONS.md` provides standalone captions.
PNG output uses 300 dpi; SVG and PDF retain vector text and plot elements.
Use `--formats png` to export a subset.

Every plotted primary point is checked against the 400 session summaries and
the 260 primary paired-block CSV observations. Estimates, pointwise intervals
and the 26-test Holm adjustment are checked for consistency. No failed or
unsupported time is imputed, and no partial GPU complex suite sum is accepted.
Query heatmaps carry no significance claims. Scale factors are discrete measured
conditions; figures do not interpolate or extrapolate a crossover.

The fixed H100 label describes the study context; input analysis does not contain
a hardware inventory. The renderer records this distinction, input hashes,
package versions, source hash and every export hash in `RENDER_MANIFEST.json`.
SVG IDs use a fixed hash salt. SVG dates and PDF creation/modification dates are
omitted for deterministic output on the same rendering stack.

Run the tests separately from the frozen root test tree:

```sh
python -m pytest -q research_reporting/tests
```

Test inputs are explicitly labeled `SYNTHETIC`, created only in temporary
directories, and require `--allow-synthetic`. Their figures have a prominent
watermark and must never be used as study results.
