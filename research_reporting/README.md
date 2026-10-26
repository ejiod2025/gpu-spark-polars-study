# Artifact replay and research figures

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

## One-command portable artifact replay

Replay a completed harvest using the genuine C84 prestart archive and its frozen
validator/analyzer. No GPU, JVM, source Parquet files, SSH, or new benchmark runs
are needed. Keep the archive and harvested JSON bytes unchanged, including their
original VM paths. Use a Linux or macOS Python environment: the frozen helper
imports POSIX `fcntl`. Install dependencies in a separate analysis environment:

```sh
python -m pip install -r /path/to/prestart-archive/campaign.json.source/requirements-research-analysis.txt
python -m pip install matplotlib==3.10.8
python research_reporting/replay.py \
  --archive /path/to/prestart-archive \
  --artifacts-root /path/to/harvest/campaign-results \
  --output /path/to/new-replay
```

The archive contains `PRESTART_MANIFEST.json`, `campaign.json`, its `.sha256`
sidecar, the complete 52-file `campaign.json.source/`, and `oracles/`, together
with every file listed in the prestart checksum inventory. Normal replay anchors
the exact real H100 v1 prestart/campaign checksums, C84 commit and source-tree
identity. Every archived checksum, source file, protocol and oracle is checked
before any archived code runs. A future campaign needs an explicitly reviewed
identity update; this command cannot silently substitute another experiment.

The harvested root contains `journal.json` and all session attempt directories,
results, schedules, plans and scientific gates. A harvest parent containing one
`campaign-results/` is also accepted. Missing, incomplete, retried, drifted or
oracle-invalid evidence stops replay. Existing output directories are refused.

The new output contains `validation.raw.json`, `validation.json`, `analysis/`,
`complete-audit/`, `figures/`, subprocess logs, `PATH_NORMALIZATION.json` and
`REPLAY_MANIFEST.json`. The separate complete-evidence auditor must report PASS
before figures render; its descriptive host-result sensitivity tables are kept
with its audit outputs.
The manifest records input/source/export hashes, the exact commands/interpreter,
dependency versions, and completion/failure. Failures retain their logs and
artifacts with a `FAIL` manifest; partial output is never labeled successful.

The frozen validator records inspected Spark plan paths using the local harvest
prefix, while VM-created scientific gates retain the original VM prefix. The
frozen analyzer compares these audit payloads exactly. The documented
`frozen-plan-source-path-v1` adapter preserves the raw validation and changes only
`sessions[].plans[].source` in derived `validation.json`. Each changed path must
be contained in the local harvest, match its precise query/phase/trial's recorded
plan, and match that plan's SHA256. All scientific values, other audit fields,
top-level oracle paths and validation timestamps remain unchanged. The full
before/after mapping and adapter source hash are recorded. No journal, session,
result, gate, plan, campaign, or archived source file is rewritten.

Full-workflow tests create explicitly synthetic raw artifacts, then run the
genuine frozen validator and analyzer through actual subprocesses. Synthetic
replay requires `--allow-synthetic` and produces watermarked figures. Its exact
frozen analyzer output is preserved; a separate `SYNTHETIC-render-input/` adds
only the synthetic marker required by the renderer. This test mode is never a
substitute for real campaign results.

After the complete study and audit pass, the [raw-evidence packager](PACKAGING.md)
creates bounded archive parts, a full file index and transfer/reassembly hashes.
Run packaging after collection has ended, outside the timed workloads. Large
raw parts and their manifests remain private alongside the versioned repository.
