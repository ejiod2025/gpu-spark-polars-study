# Reproducing the study

This file covers the four-host campaign of 2026-10-30 (A100, H100, GH200, B200) and the three
papers built from it (`paper/`, `paper2/`, `paper-pvldb/`). The workload is TPC-H-derived and is
not a TPC-H benchmark result (see the scope notice in each paper). The pre-registered design is
`docs/H100_EXPERIMENT_DESIGN.md`.

There are three tiers. Each one starts from an earlier stage of the pipeline:

| Tier | Starts from | Needs | Time |
|---|---|---|---|
| (a) Rebuild the papers | committed analysis outputs (`results/campaign-20261030/`) | Python 3, Tectonic | minutes |
| (b) Re-run the analysis | archived raw campaign trees (Zenodo) | Python 3 (standard library only), about 16 GiB of disk | minutes |
| (c) Re-run the experiment | nothing (new data, new timings) | one Lambda GPU instance per host | about 7 to 12 hours per host |

All commands run from the repository root unless a step says `cd`. They were checked on
2026-11-10 as follows:
- Tier (a) was run end to end on a fresh clone.
- Tier (b) was run on the raw trees, which reproduced every committed value.
- For tier (c), the `--help` and `--dry-run` output was checked on macOS. The command lines are
  the ones recorded in the four campaigns' `invocations.jsonl` and report files.

---

## (a) Rebuild the papers from the committed results

Requirements:
- [Tectonic](https://tectonic-typesetting.github.io/) 0.17.0, or `latexmk` with pdflatex and bibtex.
- `python3`. The paper tools use only the standard library; they were checked with Python 3.10
  and 3.14.

```bash
make -C paper final          # Paper 1 (38 pages): stats + figures + pdf
make -C paper2 final         # Paper 2 (21 pages)
make -C paper-pvldb final    # PVLDB version of Paper 1 (12 pages); also prints the page report
```

Each `final` target has three steps:
1. Convert the committed `stats.json` files into `generated/stats.tex`.
2. Copy the committed figures into `figures/`.
3. Compile `main.pdf`.

Every number in the papers is a macro filled from those files. A missing key is a hard error, so a
successful build means every number came from the committed analysis.

The Makefiles fix `SOURCE_DATE_EPOCH=1793361600`, so the PDFs are byte-reproducible with
Tectonic 0.17.0. These are the expected sha256 values (recorded in each folder's
`FINAL_BUILD.md`; other TeX versions may produce different bytes):

| PDF | sha256 |
|---|---|
| `paper/main.pdf` | `9e65bd98d79296770a60f05d7c0e83ee2069a308162085a2f060578e50929971` |
| `paper2/main.pdf` | `afbbdce09964bc1af48ecb94c2596d9d65dcf8808060aac3c432a71c6841987d` |
| `paper-pvldb/main.pdf` | `42dadfa997bb93d6ca251350703d7c181dd287d72f569759dc03141788d2d26b` |

```bash
shasum -a 256 paper/main.pdf paper2/main.pdf paper-pvldb/main.pdf   # sha256sum on Linux
```

Note: run `make ... final`, not plain `make`. In a fresh clone `generated/` does not exist yet,
and plain `make` (the `pdf` target) builds a draft with boxed placeholders instead of numbers.

### Redraw the figures (optional)

The committed figures can be redrawn from the committed statistics. This needs matplotlib
3.10.8 (`requirements-reporting.txt`). The PNG and PDF bytes need not match the committed files,
because matplotlib embeds metadata.

```bash
C=results/campaign-20261030/analysis
.venv-test/bin/python plot_campaign.py --stats $C/h100-final --cross-host $C/xhost-final --output rerun/figs-h100 --formats pdf png
.venv-test/bin/python plot_campaign.py --paper2 results/campaign-20261030/analysis-paper2/paper2-final --output rerun/figs-paper2
```

`.venv-test` is the environment from the section "Test suite" below.

---

## (b) Re-run the analysis from the archived raw data

The raw campaign trees are not in git; there are about 4 GiB per host. They hold:
- every trial time and every materialized output;
- Spark final plans and the Polars GPU session-guard records;
- telemetry and logs;
- the input-verification and preflight reports.

They are archived as one file per host:

- Archive: Zenodo dataset, DOI [`10.5281/zenodo.24125306`](https://doi.org/10.5281/zenodo.24125306). `SHA256SUMS` in the record covers the four archives.
- Host identifiers: the archives are not redacted, so they stay byte-identical to the host-produced
  manifests. They contain the instances' hostnames (on Lambda, the public IP of a since-terminated rental)
  and GPU UUIDs/serials from `nvidia-smi -q`. No personal data, credentials or keys are included.
- Files: `campaign-20261030-a100-raw.tar.zst`, `campaign-20261030-h100-raw.tar.zst`,
  `campaign-20261030-gh200-raw.tar.zst` and `campaign-20261030-b200-raw.tar.zst`.
- Each archive holds the host's `rerun/` directory as it was copied off the host. Its paths are
  exactly those listed in `results/campaign-20261030/raw-manifests/FINAL-<host>.SHA256SUMS`, for
  example `campaign-h100/campaign.json`.
- Maintainer note: each archive was built from the local tree so that it has this
  layout, for example
  `COPYFILE_DISABLE=1 tar -C rerun/hosts/h100/final -cf - . | zstd -19 -T0 -o campaign-20261030-h100-raw.tar.zst`.
  `COPYFILE_DISABLE=1` keeps macOS `._*` files out. Building the H100 archive this way and
  extracting it with the steps below verified every file.

### 1. Extract and verify

This needs `zstd`.

```bash
for h in a100 h100 gh200 b200; do
  mkdir -p rerun/hosts/$h/final
  zstd -dc campaign-20261030-$h-raw.tar.zst | tar -xf - -C rerun/hosts/$h/final
  (cd rerun/hosts/$h/final && shasum -a 256 -c ../../../../results/campaign-20261030/raw-manifests/FINAL-$h.SHA256SUMS --quiet) \
    && echo "$h: all files verified"
done
```

`--quiet` prints only mismatches. Use `sha256sum -c` instead of `shasum -a 256 -c` on Linux.

### 2. Recreate the Paper 1 layout for A100 and B200

Paper 1's A100 and B200 records (`analysis/a100-main`, `analysis/b200-main`) were analyzed from a
copy of the campaign root placed one level up, without the side study. The campaign files are
identical. Recreate that layout with links:

```bash
(cd rerun/hosts/a100 && ln -s final/campaign-a100 campaign-a100 && ln -s final/preflight-host-2 preflight-host-2 \
   && cp final/verify-sf1-30-ref.json final/verify-sf100-300.json .)
(cd rerun/hosts/b200 && ln -s final/campaign-b200 campaign-b200 && ln -s final/preflight-host preflight-host \
   && cp final/verify-sf1-30-ref.json final/verify-sf100-300.json .)
```

### 3. Check equality with the committed record (recommended)

`tests/test_analysis_campaign_frozen.py` re-analyzes every raw tree and checks the result against
the committed outputs. It requires that:
- every key in each committed `stats.json` and `stats_flat.json` has the same value (only
  `generated_at_utc` may differ);
- every CSV table is reproduced;
- the cross-host files and `paper2.json` are rebuilt value for value.

```bash
.venv-test/bin/python -m pytest -q tests/test_analysis_campaign_frozen.py     # expect: 16 passed (about 1.5 minutes)
```

When a raw tree is missing, its tests are reported as skipped, not passed. `.venv-test` is the
environment from the section "Test suite" below.

### 4. Or run the analysis by hand

Write the outputs into new directories; this does not overwrite the committed record.
`analyze_campaign.py` uses only the Python standard library.

```bash
O=rerun/reanalysis
# Paper 1 record (results/campaign-20261030/analysis/); --a100 names the earlier A100 study's saved runs (in git)
python3 analyze_campaign.py --root rerun/hosts/h100/final/campaign-h100 --output $O/h100-final --label H100 \
    --a100 evidence/results/production-20261025 evidence/results/large-sf100-20261025 \
           evidence/results/large-sf300-20261025 evidence/results/complex-formal-v2-20261025
python3 analyze_campaign.py --root rerun/hosts/gh200/final/campaign-gh200 --output $O/gh200-final --label GH200
python3 analyze_campaign.py --root rerun/hosts/a100/campaign-a100         --output $O/a100-main   --label A100 --no-side-study
python3 analyze_campaign.py --root rerun/hosts/b200/campaign-b200         --output $O/b200-main   --label B200 --no-side-study
python3 analyze_campaign.py --host A100=$O/a100-main/stats.json --host H100=$O/h100-final/stats.json \
    --host GH200=$O/gh200-final/stats.json --host B200=$O/b200-main/stats.json --output $O/xhost-final
# Paper 2 record (results/campaign-20261030/analysis-paper2/)
python3 analyze_campaign.py --root rerun/hosts/a100/final/campaign-a100   --output $O/a100-final  --label A100
python3 analyze_campaign.py --root rerun/hosts/b200/final/campaign-b200   --output $O/b200-final  --label B200
python3 analyze_campaign.py --host A100=$O/a100-final/stats.json --host H100=$O/h100-final/stats.json \
    --host GH200=$O/gh200-final/stats.json --host B200=$O/b200-final/stats.json --output $O/xhost-final-v2
python3 analyze_campaign.py --paper2 --reference H100 --host H100=$O/h100-final/stats.json \
    --host GH200=$O/gh200-final/stats.json --host A100=$O/a100-final/stats.json \
    --host B200=$O/b200-final/stats.json --output $O/paper2-final
```

Each per-host run writes the following, with the schema in `STATS_SCHEMA.md`:
- `stats.json` and `stats_flat.json`;
- `STATS_REPORT.md`, `INTEGRITY.md` and `integrity.json`;
- `tables/`.

To build Paper 1 or its PVLDB version from these outputs, draw the figures first. The
`figures` step copies them from `$(CAMPAIGN)/h100-final/figs` and `$(CAMPAIGN)/xhost-final/figs`.
This needs matplotlib, for example from the test environment below:

```bash
.venv-test/bin/python plot_campaign.py --stats $O/h100-final --cross-host $O/xhost-final --output $O/h100-final/figs
.venv-test/bin/python plot_campaign.py --cross-host $O/xhost-final --output $O/xhost-final/figs
make -C paper final CAMPAIGN=../rerun/reanalysis
make -C paper-pvldb final CAMPAIGN=../rerun/reanalysis
```

In the 2026-11-10 check, this chain (raw trees, then analysis, then figures, then papers) gave
`paper/main.pdf` and `paper-pvldb/main.pdf` byte-identical to the tier (a) builds.

---

## (c) Re-run the full experiment on GPU hosts

Each host runs its **own complete campaign** under the locked design: 4 main rounds in the
Williams square plus the 4-round Polars version side study, 192 sessions in total. Campaigns are
never pooled. Follow the steps below on each host.

### Hardware and OS (as run on 2026-10-30)

All four hosts were Lambda on-demand single-GPU instances:

| Host | Instance type | GPU | vCPUs | RAM (measured) | OS image | Driver |
|---|---|---|---|---|---|---|
| A100 | `gpu_1x_a100_sxm4` | A100-SXM4-40GB | 30 | 216 GiB | Ubuntu 22.04.5 | 570.148.08 |
| H100 (primary) | `gpu_1x_h100_sxm5` | H100 80GB HBM3 | 26 | 221 GiB | Ubuntu 24.04.4 | 580.105.08 |
| GH200 (aarch64) | `gpu_1x_gh200` | GH200 (96 GB HBM) | 64 | 526 GiB | Ubuntu 22.04.5 | 570.148.08 |
| B200 | `gpu_1x_b200_sxm6` | B200 | 26 | 354 GiB | Ubuntu 22.04.5 | 570.148.08 |

What each host needs:
- **GPU and driver:** one NVIDIA GPU with a driver of at least 525.60.13 (`setup_h100.sh` checks
  this).
- **Architecture:** x86_64, or aarch64 for the GH200. Both are supported.
- **Disk:** at least 500 GiB free on the data volume. This is the default threshold of
  `setup_h100.sh` and `preflight.py`. The Parquet inputs take about 158 GB (SF300 alone is about
  108 GB), plus the Spark shuffle and about 4 GiB of results.
- **RAM:** the Spark driver heap is 96 GiB. Every host had at least 216 GiB, and smaller hosts
  were not tested.
- **Threads:** every engine runs with 26 threads (`--cpus 26`), whatever the vCPU count.

### 1. Clone and set up

On the GPU host:

```bash
git clone https://github.com/ejiod2025/gpu-spark-polars-study.git study && cd study
git checkout H100_Test                     # TODO(zenodo-doi): replace with the archived release tag
./setup_h100.sh --dry-run --envs both      # read-only plan and checks
./setup_h100.sh --envs both                # on a non-H100 host add: --allow-any-gpu
source rerun/h100-env.sh                   # JAVA_HOME, PATH (.venv first), DATA_ROOT, RAPIDS_JAR, PYTHON_A100COMPAT_ENV
```

`setup_h100.sh` is idempotent. It installs OpenJDK 17 (only if missing) and uv 0.8.13, and builds
two virtual environments:
- `.venv` (Python 3.11): `evidence/requirements.lock.txt` minus its Polars pins, plus
  `requirements-gpu.txt`, or `requirements-gpu-aarch64.txt` on aarch64. It runs all four
  engines.
- `.venv-a100compat` (Python 3.10): `evidence/requirements.lock.txt` exactly. It runs the Polars
  2.0.0 arm of the side study.

After `source rerun/h100-env.sh`, `python` in the steps below is the `.venv` interpreter.
Re-source the file in every new shell.

`setup_h100.sh` also downloads the RAPIDS Accelerator JAR, checks its sha256 against
`evidence/DEPENDENCY_SHA256.txt` (the aarch64 JAR's sha256 is recorded inside the script), and
picks the data volume. Run `./setup_h100.sh --help` for all options.

### 2. Generate the inputs

Use the earlier A100 study's parameters (design section 3), with the `.venv` Python (DuckDB 1.5.6):

```bash
python generate_data.py --root "$DATA_ROOT" --scales 1 10 30 --threads 30 --memory-limit 64GB
python generate_large_data.py --root "$DATA_ROOT" --scales 100 --workers 12     # 20 dbgen children (chunk scale 5)
python generate_large_data.py --root "$DATA_ROOT" --scales 300 --workers 16     # 60 dbgen children
```

This took 10 to 15 minutes per host. The SF100 and SF300 steps are resumable.

### 3. Verify the inputs (input gate; required before round 1)

The gate checks row counts, schemas, generation parameters and order-independent content
fingerprints against the A100 manifests in `evidence/INPUT_MANIFESTS/`. These are the two calls
every campaign used, with the report paths it recorded:

```bash
python verify_generated_inputs.py --data-root "$DATA_ROOT" --scales 1 10 30 --reference-dbgen --report rerun/verify-sf1-30-ref.json
python verify_generated_inputs.py --data-root "$DATA_ROOT" --scales 100 300 --report rerun/verify-sf100-300.json
```

Each call must end with the line `RESULT: OK` and exit 0. This took 1 to 3 minutes per host.
`--reference-dbgen` is needed for SF10 and SF30: their A100 manifests hold no content
fingerprint, so a run without it reports `RESULT: FAILED`.

`--formal` (one call, all five scales, stricter protocol checks) is a stricter alternative; see
`python verify_generated_inputs.py --help`. Byte identity with the A100 files is not expected,
because DuckDB's per-thread output depends on scheduling.

### 4. Preflight (an SF1 smoke run of every engine, with the gates)

```bash
python preflight.py --data-root "$DATA_ROOT" --output rerun/preflight-host --cpus 26 --driver-memory 96g \
    --jar "$RAPIDS_JAR" --skip-inputs --expect-gpu-unsupported cx_q13 cx_window
# non-H100 host: add --allow-any-gpu
```

It must print `VERDICT: pass` and exit 0. This took about 3 minutes. `--output` must be a new
directory, so use `rerun/preflight-host-2` for a second attempt.

### 5. Run the campaign

Run it inside `tmux` or with `nohup`, because it takes hours. Use the `.venv` Python: the runner
launches every session with its own interpreter.

```bash
python run_campaign.py --dry-run --root rerun/campaign-h100 --data-root "$DATA_ROOT" --jar "$RAPIDS_JAR"   # print the schedule
python run_campaign.py --root rerun/campaign-h100 --host-label gpu_1x_h100_sxm5 \
    --data-root "$DATA_ROOT" --jar "$RAPIDS_JAR" --a100compat-env "$PYTHON_A100COMPAT_ENV" \
    --inputs-report rerun/verify-sf1-30-ref.json rerun/verify-sf100-300.json \
    --engine-timeout-seconds 7200 2>&1 | tee -a rerun/campaign-h100.log
```

Adjust the command to the host:
- On the other hosts, change the root and log name (`campaign-a100`, `campaign-gh200`,
  `campaign-b200`).
- Set `--host-label` to the instance type: `gpu_1x_a100_sxm4`, `gpu_1x_gh200` or
  `gpu_1x_b200_sxm6`.
- Add `--allow-any-gpu` on any host that is not an H100.
- The 2026-10-30 campaigns also passed `--stop-after-round 1` the first time. That pauses after
  round 1 for the integrity review; running the same command again without it resumes.
- `--engine-timeout-seconds 7200` was passed on every host.

The runner is idempotent and resumable: after an interruption, run the same command again. It
does not change the design. Flags that would change it (`--rounds`, `--scales-override`, and
others) mark the campaign `formal: false`.

**Expected duration**, measured from the campaign logs and excluding the 20 to 30 minute
bring-up:

| Host | Main rounds (4) | Side study (4) | Total |
|---|---|---|---|
| A100 | 8 h 50 min | 2 h 33 min | 11 h 24 min |
| H100 | 7 h 10 min | 2 h 05 min | 9 h 15 min |
| GH200 | 5 h 18 min | 1 h 28 min | 6 h 47 min |
| B200 | 7 h 04 min | 2 h 02 min | 9 h 07 min |

**Expected outputs** in `rerun/campaign-<host>/`:
- `campaign.json`: the resolved design, the design-document sha256, source and input hashes, and
  the host identity.
- `invocations.jsonl` and `status.json`, which ends with `"state": "complete"` and 192 of 192
  sessions.
- `round-1/` to `round-4/`, each with `baseline/` and `complex/` run roots, `gates.json`
  (verdict `pass`) and `preflight/preflight-report.json`.
- `side-study/round-1/` to `side-study/round-4/`.

A round that fails a gate is repeated once into `round-<k>-repeat-1/`. A second failure stops the
campaign (design section 7, amendment A1).

### 6. Copy off the host and analyze

Copy the host's `rerun/` directory to the analysis machine as `rerun/hosts/<host>/final/`, for
example `rsync -a host:study/rerun/ rerun/hosts/h100/final/`. Then continue with tier (b), steps 2
to 4. New timings will differ from the archived ones, so compare the conclusions and intervals,
not the bytes. The frozen-record tests apply only to the archived trees.

---

## Software versions and pins

| Component | Version | Pinned in |
|---|---|---|
| Polars (both Polars arms) | 1.44.2 | `requirements-gpu.txt` |
| cudf-polars-cu12 / libcudf-cu12 | 26.10.2 | `requirements-gpu.txt` (with its full resolved closure) |
| CUDA user-space runtime | 12.9 (`cuda-toolkit` 12.9.2.0 wheels) | `requirements-gpu.txt` |
| PySpark / py4j | 3.5.9 / 0.10.9.9 | `evidence/requirements.lock.txt` |
| RAPIDS Accelerator JAR | `rapids-4-spark_2.12` 26.08.1; aarch64: `-cuda12-arm64` classifier | sha256 in `evidence/DEPENDENCY_SHA256.txt` / `setup_h100.sh` |
| DuckDB (data generation, verification) | 1.5.6 | `evidence/requirements.lock.txt` |
| PyArrow, psutil | 22.0.0, 7.1.2 | `evidence/requirements.lock.txt` |
| Polars for the side study | 2.0.0 (Python 3.10) | `evidence/requirements.lock.txt` |
| Python | 3.11 (`.venv`), 3.10 (`.venv-a100compat`) | `setup_h100.sh` |
| Java | OpenJDK 17 (distribution package) | `setup_h100.sh` |
| uv | 0.8.13 | `setup_h100.sh` |
| Plotting | matplotlib 3.10.8 | `requirements-reporting.txt` |

The engine settings are fixed by the design: 26 threads, a 96 GiB Spark driver heap, 120 shuffle
partitions, AQE on, and the Spark GPU profile `a100-parity`. The Polars GPU engine uses
`raise_on_fail=True` and the streaming executor. `run_campaign.py --dry-run` prints them.

## Known caveats

- **CUDA minor-version compatibility.** The pinned CUDA 12.9 runtime ran on the A100, GH200 and
  B200 provider drivers (570.148.08, which supports CUDA 12.8) through CUDA minor-version
  compatibility. The H100's driver 580.105.08 supports CUDA 13.0. Do not change the pinned stack
  to match a driver; the design forbids version changes.
- **Disk thresholds.** `setup_h100.sh` and `preflight.py` require 500 GiB free by default. The A100
  instance's volume had less: 463 GiB before generation and 316 GiB after it. So that host ran
  `setup_h100.sh --min-free-gib 450`, and its first preflight failed the disk check. The second
  preflight passed with `--min-free-gib 250`. Lowering the threshold is an operational setting,
  not a design change, and it is recorded in the preflight report.
- **Blackwell (B200).** There is a known CUDA 12.9 compiler defect on Blackwell (amendment A4,
  NVIDIA/cccl#11167). The guard is the output-agreement gate; a disagreement fails the round and
  is not re-classified.
- **Expected coverage gaps.** The Polars GPU engine rejects `cx_q13` and `cx_window` (pre-declared,
  design section 8). These are coverage outcomes, not failures.
- **Instance types differ** in more than the GPU: CPU model, ISA, vCPUs, RAM and driver. Equal
  threads (26) are not equal physical cores on hosts with SMT. See the papers' threats sections.

## Test suite

Set up a test environment once. The tests were run with Python 3.10.18 and pytest 9.1.1; Java is
not needed:

```bash
uv venv --python 3.10 .venv-test
uv pip install --python .venv-test/bin/python -r evidence/requirements.lock.txt -r requirements-reporting.txt pytest==9.1.1
.venv-test/bin/python -m pytest -q tests
```

The suite covers:
- the design (Latin square, schedule) and the gates (preflight validation, error classification);
- input verification;
- the analyzer, including synthetic campaigns with injected ratios, and the plots;
- the frozen-record tests above.

Tests that need the raw trees (tier b) are skipped when the trees are absent. Results of the
2026-11-10 runs on macOS:
- a fresh clone without the raw trees: `742 passed, 31 skipped`, about 4.5 minutes;
- the working tree with all raw trees extracted: `773 passed`, about 8 minutes.
