# H100 experiment runbook

The October 26, 2026 campaign uses frozen engine source commit
`c4c934cf732b4394e2bcb0547bb642ee2a1ae5d4` on branch `H100_main_dev`.
Reporting fixes can be committed separately: every run records the hashes of the source it
actually loaded. Query definitions are unchanged from the A100 experiment.

## Recorded environment and gates

One H100 80 GB HBM3, 26 Xeon Platinum 8480+ vCPUs, about 221 GiB RAM, driver 570.148.08;
Python 3.11.13, Polars 1.44.2 for both Polars modes, cuDF Polars 26.10.2/CUDA 12,
PySpark 3.5.9, RAPIDS Spark 26.08.1, Java 17, DuckDB 1.5.6. Spark uses `local[26]`,
96 GiB driver memory, and the original `a100-parity` configuration. No tuned profile is mixed in.

Local validation passed 169 tests with one skipped test requiring an absent local CPU-smoke
directory. Three regression tests compare analyzer output byte for byte with source commit
`06850ed` on the saved A100 evidence. The fresh H100 preflight passed 74 checks, zero failures,
zero warnings, four informational checks, and two expected unsupported-query rejections.
The analyzers checked 92 successful smoke outputs, 24 Spark GPU plans, and 44 complex cleanup
barriers with zero output mismatches or integrity violations. Smoke timings have one measured
trial and are not formal performance estimates.

Polars GPU rejects the original `cx_q13` inline regular expression and `cx_window` grouped
window expression. Keep these errors visible; do not replace them with CPU timings or silently
rewrite one engine's query. Expected-failure recognition requires a diagnostic-specific
unsupported translation failure at warmup with no successful records. OOM, I/O errors,
measured failures, and mismatches remain failures.

`raise_on_fail=True` disables CPU translation fallback. The worker verifies the effective
configuration, a successful tiny GPU query, and rejection of an unsupported Python UDF.
Streaming `fallback_mode="warn"` permits a single GPU partition where needed and retains
warnings; this is GPU execution, distinct from CPU fallback.

## Reproduce on a provisioned Linux H100

Use a fresh checkout/output directory. Setup records inventory, validates the existing driver,
installs pinned dependencies, and downloads the checksummed Spark plugin. It does not create a
cloud instance. Dataset generation and validation must finish before timed runs.

```bash
./setup_h100.sh --envs gpu --data-root /path/to/data
source rerun/h100-env.sh
```

The generators and historical methods describe creating SF1/10/30 and chunked SF100/SF300.
For the recorded campaign those datasets were already present. Validate the small scales
against independently generated DuckDB content, and the large scales against stored A100
fingerprints; generating a second full SF300 in memory is unnecessary:

```bash
"$PYTHON_GPU_ENV" verify_generated_inputs.py --data-root "$DATA_ROOT" \
  --scales 1 10 30 --reference-dbgen --threads 26 --workers 8 \
  --memory-limit 64GB --report rerun/input-small-new.json
"$PYTHON_GPU_ENV" verify_generated_inputs.py --data-root "$DATA_ROOT" \
  --scales 100 300 --threads 26 --workers 8 \
  --memory-limit 64GB --report rerun/input-large-new.json

"$PYTHON_GPU_ENV" preflight.py --data-root "$DATA_ROOT" \
  --output rerun/preflight-new --scales 1 --runs 1 --cpus 26 --driver-memory 96g \
  --engines polars polars_gpu spark_cpu spark_gpu --jar "$RAPIDS_JAR" \
  --skip-inputs --strict --expect-gpu-unsupported cx_q13 cx_window
```

The recorded SF30 layout has 63 lineitem files versus 60 on A100. All five scales passed
external content-fingerprint comparisons, and every H100 engine reads the same H100 files.
Large-scale unique-key enumeration is skipped above the verifier's automatic row threshold;
row/key hash sums and XORs, row counts, schemas, and file SHA-256 checks remain active.

Run engines sequentially and keep baseline/complex outputs separate:

```bash
"$PYTHON_GPU_ENV" run_experiment.py --data-root "$DATA_ROOT" \
  --output rerun/h100-baseline-new --scales 1 10 30 100 300 --runs 3 \
  --cpus 26 --driver-memory 96g --engines polars polars_gpu spark_cpu spark_gpu \
  --jar "$RAPIDS_JAR" --keep-going --engine-timeout-seconds 5400

"$PYTHON_GPU_ENV" run_experiment.py --suite complex --query-cleanup verified_gc \
  --cleanup-timeout-seconds 60 --data-root "$DATA_ROOT" \
  --output rerun/h100-complex-new --scales 30 100 300 --runs 3 \
  --cpus 26 --driver-memory 96g --engines polars polars_gpu spark_cpu spark_gpu \
  --jar "$RAPIDS_JAR" --keep-going --engine-timeout-seconds 5400

python analyze_crossover.py --results rerun/h100-baseline-new \
  --output rerun/h100-baseline-new-report
python analyze_complex.py --results rerun/h100-complex-new \
  --output rerun/h100-complex-new-report
```

Each case uses one recorded warmup and three measured trials. Query/engine orders are seeded,
filesystem preheating precedes each engine, and application DataFrame caching is disabled.
Complex actions use verified GC outside query timers; cleanup and process wall time remain
separate. GPU transfer inside `collect()` is timed; Polars conversion to dictionaries is outside.
Suite scores sum per-query medians. Missing cells cannot create a six-query score.

Within-H100 comparisons are primary. A100 differs in CPU model/count, Polars/Python versions,
GPU memory, driver, and physical file layout. Cross-host ratios cannot isolate GPU hardware.

## Recorded locations

The active campaign was launched from `/home/ubuntu/study-h100-c4c934c`, using the existing
environment `/home/ubuntu/study/.venv` and datasets `/home/ubuntu/study/data`.
Its run roots are `rerun/h100-baseline-formal-20261026` and
`rerun/h100-complex-formal-20261026`; adjacent `.log` and `.exit` files record the shell launches.
The fresh SF1 preflight is in `/home/ubuntu/study-h100-main-dev/rerun/h100-preflight-20261026`.
Local curated validation/preflight/source evidence is in `evidence-h100/20261026/`.
The baseline is complete and validated (480 outputs, 120 Spark GPU plans). Its report and plots
are in `reports/h100-baseline-20261026/`; complex results are pending. A separate
CPU software-stack control of both suites using Polars 2.0.0/Python 3.10 is planned after the main campaign.
It will reuse the frozen source, files, thread allocation, and protocol and compare every saved
output with the primary CPU reference. Both Python and Polars versions differ; this is not an
isolated Polars-version experiment. Use `analyze_polars_control.py --suite baseline` or
`--suite complex`, with `--primary-root`, `--control-root`, a new `--output`, and optional
`--include-polars-gpu` to revalidate and compare those saved results. SSH credentials, environments, bulk
Parquet data, and raw Spark event logs are not repository inputs.
