# H100 four-engine study — plan and interface contract

Status (2026-10-26): H100 development recovered and under validation on branch `H100_main_dev`.
The existing SF1 smoke evidence has been audited locally; a fresh preflight of the updated code
is pending. Formal multi-scale measurements and their reports are not yet complete.

## Goal

Extend the TPC-H-derived study (DuckDB `tpch` dbgen data; selected adapted TPC-H queries plus
Q9/Q13-derived and custom complex queries; **not** a complete or certified TPC-H run) from three
to four engines on one Lambda H100 host. The connected machine reports an NVIDIA H100 80 GB HBM3,
26 Intel Xeon Platinum 8480+ vCPUs, approximately 221 GiB RAM, a 2.7 TiB root filesystem, and
NVIDIA driver 570.148.08 (`nvidia-smi` reports CUDA driver API 12.8).

| Engine id | Description |
|---|---|
| `polars` | Polars 1.44.2 CPU, streaming engine |
| `polars_gpu` | Polars 1.44.2 with `cudf-polars-cu12` 26.10.2, streaming executor, `raise_on_fail=True` |
| `spark_cpu` | PySpark `local[N]` CPU (unchanged) |
| `spark_gpu` | PySpark + NVIDIA RAPIDS Accelerator 26.08.1 (CUDA 12) |

All four engines run on the same host, same files, same protocol (one warmup + 3 measured trials,
seeded shuffled order, preheat before each engine, randomized engine order per scale,
`verified_gc` for the complex suite). Within-H100 comparisons are the primary result.
A100-vs-H100 comparisons are secondary: CPU engines differ in vCPU count (30 vs 26) and are
labelled non-comparable; GPU-engine cross-hardware comparisons carry that caveat too. Both H100
Polars engines use the same supported Polars 1.44.2 version on Python 3.11; the A100 record used
Polars 2.0.0 on Python 3.10.12. These software and host differences prevent attribution of a
cross-host timing change solely to the GPU model.

The recovered SF1 smoke completed all six baseline queries on all four engines. In the complex
suite, Polars GPU completed four of six queries and rejected `cx_q13` (unsupported inline regex)
and `cx_window` (unsupported grouped unary/window expression) during strict GPU translation.
The other three engines completed all six complex queries. Local revalidation of the saved
outputs found agreement for the successful records and accepted those two diagnostic-specific
unsupported cases as XFAIL. This is bring-up evidence, not formal performance evidence, and does
not establish coverage or behaviour at larger scales.

## Non-negotiables

1. Preserve existing query definitions, timing scope and record meanings. The default
   `a100-parity` profile preserves the recorded Spark configuration apart from the H100 host's
   explicit `local[26]` CPU allocation. New fields are additive; the matched Polars version
   change is recorded rather than presented as unchanged A100 software.
2. No silent GPU fallback: `polars_gpu` uses `raise_on_fail=True`; `spark_gpu` keeps its plan checks.
   An unsupported Polars GPU translation is a recorded query error, never a CPU timing labelled
   GPU. Its streaming executor may fall back to a single partition **on the GPU**; capture the
   warning and effective configuration. That differs from forbidden silent CPU fallback.
   Spark GPU plans retain both GPU and CPU operator evidence and their execution classification.
3. Analyzers given the saved three-engine A100 evidence must reproduce the committed reports
   (regression-tested against `git HEAD` analyzers).
4. Explicit schemas: Float64 normalization via `FLOAT_COLUMNS`, unchanged across engines.
5. New output directories only; runs are re-runnable without clobbering evidence.
6. Verify generated inputs before timing: file SHA-256 against the new local manifest, then
   generation metadata, row counts and schemas against `evidence/INPUT_MANIFESTS/`, with
   order-independent DuckDB content fingerprints against external references where available.
   Report physical-layout differences and missing external content references explicitly;
   regenerated Parquet files need not have the A100 files' byte hashes.

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
| 1 Bring-up | H100 | Fresh updated-code `preflight.py` validates all engines at SF1, agreement on successful queries, strict rejection of CPU fallback, and explicit diagnostic-specific XFAILs for unsupported queries |
| 2 Data | H100 | SF1/10/30/100/300 generated with recorded parameters; `verify_generated_inputs.py` matches A100 manifests |
| 3 Formal | H100 | baseline (SF1–SF300) and complex v2 (SF30–SF300), 4 engines attempted, `--cpus 26`; preserve unsupported/error cells and restrict aggregate scores to complete coverage; `h100-tuned` in separate run roots |
| 4 Harvest | H100 → Mac | results rsynced + checksummed before termination; reports regenerated locally |

Datasets SF1/SF10/SF30/SF100/SF300 are present on the H100. Presence alone does not satisfy the
Phase 2 verification gate; retain the validation reports before formal timing.

## Development ownership (no two owners edit the same file)

| Owner | Files |
|---|---|
| Runner/worker engineer | `worker.py`, `run_experiment.py`, `query_cleanup.py` |
| Analysis engineer | `analyze_results.py`, `analyze_crossover.py`, `analyze_complex.py`, `plot_results.py`, `plot_crossover.py`, `plot_complex.py`, `inspect_spark_metrics.py`, new `compare_hardware.py` |
| Data-quality engineer | new `preflight.py`, `setup_h100.sh`, `verify_generated_inputs.py`, `requirements-gpu.txt`, `tests/`, `docs/POLARS_GPU_COMPAT.md` |
| Lead | `docs/H100_PLAN.md`, `.gitignore`, `README.md` (later) |

## Known risks

- `cudf-polars` 26.10.2 supports Polars `>=1.35,<1.45`, so both H100 Polars engines use 1.44.2;
  their CPU timings cannot be treated as a matched-version comparison with A100 Polars 2.0.0.
- SF300 query working sets may exceed 80 GB VRAM. The streaming executor may still fail;
  record failures and GPU single-partition warnings rather than hiding them.
- The original `cx_q13` and `cx_window` Polars ports were unsupported on GPU in the recovered SF1
  smoke. Larger-scale coverage remains to be measured. The original query definitions remain
  intact; successful cases cannot substitute for a complete six-query Polars GPU score.
- Spark shuffle disk at SF300 (prior v1 incident): data and `spark.local.dir` on the 2.8 TiB volume.
