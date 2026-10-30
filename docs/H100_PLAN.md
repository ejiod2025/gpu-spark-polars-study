# H100 four-engine study — plan and interface contract

Status: Phase 0 (local, no GPU) in progress. Branch `H100_Test`.

## Goal

Extend the TPC-H-derived study (DuckDB `tpch` dbgen data; selected adapted TPC-H queries plus
Q9/Q13-derived and custom complex queries; **not** a complete or certified TPC-H run) from three
to four engines on one Lambda `gpu_1x_h100_sxm5` host (H100 80 GB SXM5, 26 vCPU, 225 GiB RAM,
2,816 GiB disk):

| Engine id | Description |
|---|---|
| `polars` | Polars CPU, streaming engine (unchanged) |
| `polars_gpu` | Polars with the cuDF GPU engine (`cudf-polars`), `raise_on_fail=True` |
| `spark_cpu` | PySpark `local[N]` CPU (unchanged) |
| `spark_gpu` | PySpark + NVIDIA RAPIDS Accelerator 26.08.1 (CUDA 12) |

All four engines run on the same host, same files, same protocol (one warmup + 3 measured trials,
seeded shuffled order, preheat before each engine, randomized engine order per scale,
`verified_gc` for the complex suite). Within-H100 comparisons are the primary result.
A100-vs-H100 comparisons are secondary: CPU engines differ in vCPU count (30 vs 26) and are
labelled non-comparable; GPU-engine cross-hardware comparisons carry that caveat too.

## Non-negotiables

1. Existing engines' behaviour is unchanged: identical Spark confs and command lines under the
   default `a100-parity` profile, identical timing scope, identical record fields. New fields are
   additive only.
2. No silent GPU fallback: `polars_gpu` uses `raise_on_fail=True`; `spark_gpu` keeps its plan checks.
   A query that cannot run fully on the GPU is a recorded query error, never a CPU timing labelled GPU.
3. Analyzers given the saved three-engine A100 evidence must reproduce the committed reports
   (regression-tested against `git HEAD` analyzers).
4. Explicit schemas: Float64 normalization via `FLOAT_COLUMNS`, unchanged across engines.
5. New output directories only; runs are re-runnable without clobbering evidence.
6. Generated inputs are verified against `evidence/INPUT_MANIFESTS/` (row counts; file sha256 for
   single-process scales; order-independent DuckDB fingerprint for chunked SF100/SF300) before timing.

## Interface contract (worker `results.json`)

Additive fields; legacy fields keep their meaning.

Session metadata:
- `engine`: one of the four ids above.
- `polars_execution_engine`: `"streaming"` (polars), `"gpu"` (polars_gpu), `null` (spark).
- `polars_gpu`: object for polars_gpu only, else absent: `{"executor": str, "executor_options": dict,
  "raise_on_fail": true, "device": int, "cudf_polars_version": str, "memory_resource": str|null}`.
- `spark_gpu_profile`: `"a100-parity"` | `"h100-tuned"` for spark_gpu; absent otherwise.
- `gpu_inventory`: best-effort device name/driver/CUDA runtime for GPU engines; absent otherwise.

Per record:
- `gpu_execution_class`: legacy values for spark engines; `"gpu_engine_strict"` for polars_gpu
  (raise_on_fail guarantees no CPU fallback); `"cpu_engine"` for polars and spark_cpu.
- `peak_gpu_memory_bytes`: per-process-tree peak device memory sampled during the timed action for
  GPU engines (`null` if sampling unavailable); absent for CPU engines.

Timing scope for polars_gpu equals polars: query construction, optimization, Parquet read,
GPU execution and device-to-host result transfer (collect returns a host DataFrame), inside the
timer; `to_dicts()` outside.

Runner CLI additions (`run_experiment.py`):
- `--engines` accepts `polars_gpu`.
- `--polars-gpu-executor {in-memory,streaming}` (default `streaming`; recorded).
- `--spark-gpu-profile {a100-parity,h100-tuned}` (default `a100-parity`) and
  `--spark-gpu-conf-file PATH` (JSON key→value, required for `h100-tuned`; recorded verbatim).
- Missing `nvidia-smi` is tolerated only when no GPU engine is selected (allows Mac CPU smoke runs).

## Phases

| Phase | Where | Gate to proceed |
|---|---|---|
| 0 Build | local Mac (no GPU) | unit/regression tests pass; CPU smoke (`polars`, `spark_cpu`) at SF1 passes; GPU paths import-guarded |
| 1 Bring-up | H100 | `setup_h100.sh` idempotent success; `preflight.py` green: all queries × 4 engines at SF1, zero fallbacks, outputs agree |
| 2 Data | H100 | SF1/10/30/100/300 generated with recorded parameters; `verify_generated_inputs.py` matches A100 manifests |
| 3 Formal | H100 | baseline (SF1–SF300) and complex v2 (SF30–SF300), 4 engines, `--cpus 26`; `h100-tuned` in separate run roots |
| 4 Harvest | H100 → Mac | results rsynced + checksummed before termination; reports regenerated locally |

Optional rehearsal: `gpu_1x_a100_sxm4` (≈ original host shape) for Phase 1 at SF1 if H100 capacity
stays unavailable after Phase 0 — subject to explicit approval.

## Phase 0 ownership (no two owners edit the same file)

| Owner | Files |
|---|---|
| Runner/worker engineer | `worker.py`, `run_experiment.py`, `query_cleanup.py` |
| Analysis engineer | `analyze_results.py`, `analyze_crossover.py`, `analyze_complex.py`, `plot_results.py`, `plot_crossover.py`, `plot_complex.py`, `inspect_spark_metrics.py`, new `compare_hardware.py` |
| Data-quality engineer | new `preflight.py`, `setup_h100.sh`, `verify_generated_inputs.py`, `requirements-gpu.txt`, `tests/`, `docs/POLARS_GPU_COMPAT.md` |
| Lead | `docs/H100_PLAN.md`, `.gitignore`, `README.md` (later) |

## Known risks

- `cudf-polars` supports a narrow Polars range; if 2.0.0 is unsupported, both Polars engines move to
  a supported version on the H100 (recorded break from A100 Polars numbers).
- SF300 exceeds 80 GB VRAM for some queries; Polars GPU needs the streaming executor and may still
  fail; failures are recorded, not hidden.
- Window (`shift().over()`, `cum_count().over()`) and `n_unique` expressions may be unsupported on GPU.
- Spark shuffle disk at SF300 (prior v1 incident): data and `spark.local.dir` on the 2.8 TiB volume.
