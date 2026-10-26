# Polars GPU engine (cudf-polars) compatibility

Research and H100 bring-up date: 2026-10-26. Branch: `H100_main_dev`. Sources include PyPI metadata,
wheel contents, upstream source/commits, NVIDIA documentation and recovered H100 SF1 smoke
evidence; URLs are in the last section. The original Phase 0 source assessment is retained below
and distinguished from the observed SF1 outcome. A fresh preflight of the updated code is
pending; formal multi-scale coverage and performance are not yet established.

## 1. Selected stack

1. **No released cudf-polars supports Polars 2.0.0**, the version in `evidence/requirements.lock.txt`.
   The newest release, `cudf-polars-cu12/-cu13 26.10.2` (2026-10-22), requires
   `polars>=1.35,<1.45` and Python >= 3.11. Polars 2.0.0 was released 2026-10-06 and 2.1.0 on
   2026-10-25.
2. Polars 2.0.x support exists only on the unreleased RAPIDS **26.12** line (`polars>=2.0,<2.1`,
   Python >= 3.12). It is available as nightly wheels only; 26.12 is scheduled for 2026-12-09/10.
3. **Selected (Option B): both H100 Polars engines use Polars 1.44.2** with
   `cudf-polars-cu12==26.10.2` on **Python 3.11**. The recovered Phase 0 CPU parity notes report
   equal schemas and tolerance-equal rows for all 12 queries on Polars 1.44.2 and 2.0.0 at SF1
   (section 4). Costs: the Polars CPU version
   differs from the A100 record, and Python moves from 3.10.12 to 3.11.
4. Python 3.10 (the A100 interpreter) is not supported by any cudf-polars from 26.4.0 onward.
5. The recovered H100 SF1 smoke confirmed **both `cx_q13` and `cx_window` were unsupported**
   on Polars GPU under `raise_on_fail=True`: the former rejected `(?s)` in its regex; the latter
   rejected a grouped unary/window expression. Polars GPU completed six of six baseline queries
   and four of six complex queries. Successful saved outputs agreed with the CPU reference.
   These two strict rejections are explicit limitations; they do not establish the outcome at
   every larger scale, and no GPU-friendly replacement is substituted into the original suite.

## 2. Release matrix (PyPI `requires_dist` / `requires_python`, per version)

| cudf-polars (cu12 and cu13 identical) | Date | Polars range | Python |
|---|---|---|---|
| 25.12.0 | 2025-12-11 | >=1.29, <1.35 | >=3.10 |
| 26.2.0 / 26.2.1 | 2026-02-05 / 02-09 | >=1.30, <1.36 | >=3.10 |
| 26.4.0 | 2026-04-09 | >=1.30, <1.39 | >=3.11 |
| 26.6.0 | 2026-06-09 | >=1.35, <1.40 | >=3.11 |
| 26.8.0 / 26.8.1 | 2026-08-06 / 08-26 | >=1.35, <1.43 | >=3.11 |
| 26.10.0 / **26.10.2** | 2026-10-08 / **10-22** | **>=1.35, <1.45** | >=3.11 |
| 26.12.0a568 nightly (cu12/cu13) | 2026-10-26 | **>=2.0, <2.1** | **>=3.12** |

Polars 1.x releases that matter: 1.44.1 (2026-08-26), **1.44.2 (2026-09-09, last 1.x)**, 2.0.0 (2026-10-06),
2.1.0 (2026-10-25, requires Python >= 3.11). Yanked: 1.35.0, 1.36.0, 1.38.0, 1.39.2, 1.41.0, 1.43.0,
1.43.1, 1.44.0 (use none of these).

Upstream history (`python/cudf_polars/pyproject.toml`): commit `1340933` (2026-09-11) "Pin Polars<1.45"
(added 1.43/1.44, "support Polars 2 in the next release"); commit `8654d64` (2026-10-12) drops Python
3.11; commit `e0fb724` (2026-10-20) "Pin Polars to 2.0 and drop Polars 1.x support in cudf-polars"
(removes 167 version guards; multi-key `over(order_by=...)` now falls back to CPU; `LazyFrame.profile()`
support removed). `cudf_polars/utils/versions.py` on main: `POLARS_LOWER_BOUND = parse("2.0")`.

The Polars 2.0.0 `GPUEngine` (`polars/lazyframe/engine.py` in the installed wheel) calls
`cudf_polars.execute_with_cudf(..., config=self)` as a post-optimization callback, so forcing the
26.10.2 wheel onto Polars 2.0.0 (`--no-deps`) is an unsupported hack against an IR whose guards were
rewritten between 1.44 and 2.0; not recommended.

## 3. Options

| | Polars (both engines) | cudf-polars | Python | Verdict |
|---|---|---|---|---|
| A | 1.35.2 | cu12 26.2.1 | 3.10 | Only route that keeps Python 3.10. Eight months old, older window/streaming support, Polars far from 2.0.0. Resolves cleanly (checked) but not recommended. |
| **B** | **1.44.2** | **cu12 26.10.2** | **3.11** | **Selected on H100.** Released, exactly pinnable, matched CPU/GPU Polars version; recovered SF1 CPU parity notes report agreement with 2.0.0. |
| C | 2.0.0 | cu12 26.12.0aNNN nightly | 3.12 | Keeps Polars 2.0.0 but alpha, unreproducible (the nightly index keeps only the last few days; wheelhouse must be archived at install time), multi-key `over(order_by)` falls back to CPU. Resolves cleanly (checked). Only if Polars 2.0.0 is mandatory. |
| D | 2.0.x | cu12 26.12.0 release | 3.12 | Best end state; not available before 2026-12-09/10 (schedule subject to change). Re-evaluate then. |

Effect on A100 comparability under Option B: both H100 Polars engines use 1.44.2, while A100 CPU
Polars used 2.0.0. The hosts also differ in CPU model and count (30 vs 26 vCPUs), and Python changes
from 3.10.12 to 3.11. Within-H100 engine comparisons use one host and a matched Polars version;
cross-host differences cannot be attributed solely to the GPU. The Polars version does not
change Spark's implementation, but the host and Python differences still apply to its comparison.
The CPU streaming performance difference between Polars 1.44.2 and 2.0.0 is **not measured** here.
An optional same-host CPU version bridge would need its own evidence. The A100 evidence itself
is untouched (analyzers read saved results only).

## 4. Recovered local parity notes (Polars 1.44.2 vs 2.0.0, SF1, Mac)

The recovered Phase 0 notes describe the same code path as `worker.py` (`scan_parquet`, Float64 casts from `FLOAT_COLUMNS`,
`collect(engine="streaming")`), data/sf1, both suites (6 baseline + 6 complex queries). 1.44.2 ran in
a throwaway venv under `/tmp` (not the project venv).

- Row counts equal for all 12; output schemas identical for all 12.
- All 12 equal within rel 1e-8 / abs 1e-6 (the study's own agreement rule). 8/12 are bit-identical;
  q01, q10, q14 and cx_q09 differ only in the last float bits (parallel summation order).
- `LazyFrame.explain(engine="streaming")`, `pl.thread_pool_size()` and the `GPUEngine.__init__` signature
  (`device, memory_resource, raise_on_fail, monitoring, **kwargs`) are identical in both versions.

## 5. cudf-polars 26.10.2: executors, options, and expression support

### GPUEngine surface (from `cudf_polars/utils/config.py` in the 26.10.2 wheel)

- Top-level kwargs accepted by `pl.GPUEngine(...)`: `device`, `memory_resource` (Polars-level),
  `raise_on_fail`, `executor`, `executor_options`, `parquet_options`, `memory_resource_config`,
  `hardware_binding`. Anything else raises `TypeError`. `monitoring=True` raises `NotImplementedError` (checked on Polars 1.44.2 and 2.0.0).
- Executors: `"streaming"` (default; also chosen by env `CUDF_POLARS__EXECUTOR`) and `"in-memory"`
  (single GPU, whole dataset must fit; takes no options). The docs state the streaming executor
  does not support `.profile()`.
- Streaming `executor_options` (defaults): `cluster` (single-GPU `default_singleton`),
  `fallback_mode` (`"warn"`; also `"silent"`, `"raise"`), `max_rows_per_partition` (1_000_000),
  `target_partition_size` (auto: min(1.5 GB, 2.5% of device)), `broadcast_limit` (auto: min(16 GB, 15%
  of device)), `client_device_threshold` (0.5), `sink_to_directory`, `dynamic_planning`,
  `join_filter_pushdown` (off), `max_concurrent_io_tasks` (2 local / 8 remote), `num_py_executors` (8),
  `kvikio_*` tuning knobs. Env overrides use the `CUDF_POLARS__EXECUTOR__*` prefix. **The option set is
  version specific** (26.2.1 had `runtime`, `scheduler`, `shuffle_method`, `unique_fraction`); record
  `cudf_polars_version` next to `executor_options` as the contract already does.
- `raise_on_fail=True` makes a translation failure (unsupported node/expression) raise instead of
  running on the Polars CPU engine. It does **not** govern the streaming executor's multi-partition
  fallback: with `fallback_mode="warn"` an operation lacking a multi-partition implementation runs
  as a single partition **on the GPU** and emits a warning (can use a lot of memory). The runner
  retains the recorded default and captures these warnings and the effective configuration.
  GPU single-partition fallback is distinct from forbidden CPU fallback. Any run using
  `fallback_mode="raise"` would be a separately recorded configuration, not an unreported change
  to the selected protocol.
- Default memory resource: `rmm.mr.CudaAsyncMemoryResource` with `release_threshold` = 90% of device
  memory (`MemoryResourceConfig.default()`); this is a suitable value for the contract's
  `memory_resource` string when none is passed.
- Eager and background collection never run on the GPU (Polars 2.0.0 `_post_opt_callback`); the benchmark
  uses lazy `collect`, so this does not matter.

### Expression coverage for our 12 queries

The table combines the original wheel-source assessment with the recovered H100 SF1 observation.
All six baseline cases and `cx_q09`, `cx_semi_anti`, `cx_distinct`, `cx_self_join` completed on
Polars GPU in that smoke. `cx_q13` and `cx_window` raised the strict translation errors described
below. This is SF1 support evidence; a fresh updated-code preflight and formal larger-scale runs
remain separate gates.

| Query | Constructs | 26.10.2 assessment / SF1 outcome | Basis |
|---|---|---|---|
| q01 | filter, multi-key `group_by`, sum/mean/`pl.len`, sort | Supported | `Agg`, `Len` nodes |
| q03, q10, q12, q14 | inner joins, `is_between`, `is_in`, `when/then/otherwise`, `str.starts_with`, descending multi-key sort, `limit` | Supported | `Join`, `BooleanFunction.IsBetween/IsIn`, `StringFunction.StartsWith`, `Ternary`, `Slice` |
| q06 | filter + scalar sum | Supported | |
| cx_q09 | 6-table join incl. composite key, `str.contains(literal=True)`, `dt.year` | Supported | `libcudf strings.find.contains`, `TemporalFunction.Year` |
| cx_q13 | left join, nested `group_by`, `count`, `str.contains("(?s)special.*requests")` | **Unsupported in recovered H100 SF1 smoke** | `ComputeError` wraps `NotImplementedError: Unsupported regex (?s)special.*requests for GPU engine.` The original source assessment identified the inline DOTALL flag as a risk. The original expression is retained; removing the flag would be a distinct query variant requiring validation. |
| cx_semi_anti | `how="semi"`, `how="anti"`, `is_in`, column-vs-column comparison | Supported | `Join` handles Inner/Left/Right/Full/Semi/Anti/Cross |
| cx_distinct | 4-table join, `drop_nulls().n_unique()` in `group_by` | Supported | fused to `nunique(null_handling=EXCLUDE)` in `decompose_single_agg` |
| cx_self_join | equi-join on `l_orderkey`, post-filter inequality, `group_by`/`len` | Supported (memory heavy: join fan-out) | |
| **cx_window** | `shift(1).over(key, order_by=[2 keys], nulls_last=True)` and **`fill_null(0).cum_count().over(key, order_by=[3 keys], nulls_last=True)`** | **Unsupported in recovered H100 SF1 smoke** | `ComputeError` wraps `NotImplementedError: No support for ... UnaryFunction ... in groupby/rolling`. The wheel-source assessment also found `cum_count().over(...)` outside the grouped-window unary-function whitelist. The original expression is retained. |

Also: `FLOAT_COLUMNS` casts read Parquet `DECIMAL(15,2)` and cast to Float64; `dtypes.can_cast` defers to
`plc.unary.is_supported_cast` for fixed-width types, which libcudf supports for decimal to double.

**Consequence for the contract.** The recovered SF1 complex smoke has **4/6 successful Polars GPU
queries**, with recorded strict translation errors for `cx_q13` and `cx_window`. Analyzers must
preserve those missing cells and must not present a complete six-query Polars GPU aggregate from
the other four. No result at SF100/SF300 is implied by this smoke. The original query ports stay
intact; any future GPU-friendly variants require separate names and independent validation.
`preflight.py --expect-gpu-unsupported cx_q13 cx_window` reports only diagnostic-specific strict
translation rejections at warmup, with no successful records, as XFAIL. Runtime/OOM/I/O errors or
partially successful queries still fail, even when their query name is listed. The default is
zero expected failures.

## 6. CUDA, driver, hardware, Python

- RAPIDS (cudf-polars, `-cu12` wheels): compute capability >= 7.0 (Volta or newer, so Hopper sm_90
  qualifies); CUDA 12 needs driver >= 525.60.13; CUDA 13 needs >= 580.65.06; Linux with glibc >= 2.28.
  The A100 host reported 580.105.08. The connected H100 reports **570.148.08**, with CUDA driver API
  **12.8** in `nvidia-smi`; this satisfies the stated CUDA 12 minimum and does not satisfy the
  CUDA 13 minimum. The pinned `-cu12` wheels bundle CUDA
  12.9 libraries (`cuda-toolkit==12.9.2.0`, `nvidia-libnvcomp-cu12==5.3.0.16`); no system CUDA toolkit
  is needed.
- RAPIDS Accelerator for Spark 26.08.1 (`rapids-4-spark_2.12`): NVIDIA's 26.08 release page lists
  "tested on V100, T4, A10, A100, L4, H100 and GB100 GPUs", "NVIDIA Driver: R525+", packages "built
  against CUDA 12.9 or CUDA 13.1", Scala 2.12 for Spark 3.3.0 to 3.5.9, any Linux with glibc >= 2.28. Maven
  Central has `rapids-4-spark_2.12-26.08.1.jar` and `-cuda12.jar` with the same SHA-1
  (`00cc9744dfdc07243bdb66b73a840243826f4d48`, 998,327,432 bytes) plus a distinct `-cuda13.jar`, so the
  unclassified jar in `evidence/DEPENDENCY_SHA256.txt` is the CUDA 12 build. Hopper is covered.
- Python: Option B needs >= 3.11 (cudf-polars 26.10.2, pylibcudf/rmm/kvikio 26.10 wheels are
  `abi3` >= 3.11). PySpark 3.5.9, DuckDB 1.5.6, PyArrow 22.0.0, psutil 7.1.2 all run on 3.11.
- Lambda Stack 22.04 image: Ubuntu 22.04, system Python 3.10 (24.04 images ship 3.12); 1x H100 SXM:
  the connected machine reports **H100 80 GB HBM3, 26 vCPUs, approximately 221 GiB RAM and a 2.7 TiB
  root filesystem**. Published instance sizing and OS-visible memory need not match exactly.
  Persistent filesystems appear under
  `/lambda/nfs/<name>` only when attached. `setup_h100.sh` uses `uv` to obtain Python 3.11 so no PPA
  is needed.

## 7. Resolved pin set

`requirements-gpu.txt` holds the exact transitive closure for Option B (37 pins, resolved with
`uv pip compile --python-version 3.11 --python-platform x86_64-manylinux_2_28`; the union with the
lock minus Polars resolves with zero drift). Install size is several GB (libcudf-cu12 alone is
667 MB). Alternatives were also resolved on 2026-10-26: Option A (`polars==1.35.2`,
`cudf-polars-cu12==26.2.1`, Python 3.10) and Option C (`polars==2.0.0`, nightly
`cudf-polars-cu12==26.12.0a568.post261026052714` with `--extra-index-url
https://pypi.anaconda.org/rapidsai-wheels-nightly/simple --prerelease allow`, Python 3.12).

## 8. Remaining H100 validation

1. Complete a fresh preflight of the updated code. The recovered SF1 smoke already establishes
   the two strict unsupported cases above; local revalidation passes with those explicit XFAILs.
2. Retain warning/configuration evidence at each scale. No cudf-polars warnings were recorded
   on the successful queries in the recovered SF1 smoke; that does not rule them out at scale.
3. Validate all generated scales and measure SF100/SF300 behaviour (VRAM, streaming executor),
   recording unsupported/runtime errors rather than filling in missing timings.
4. Optional bridge: Polars 1.44.2 vs 2.0.0 CPU streaming timings on the same host.

## 9. Sources

- PyPI JSON: https://pypi.org/pypi/cudf-polars-cu12/json and `/<version>/json` (same for `-cu13`,
  `pylibcudf-cu12`, `libcudf-cu12`, `cudf-streaming-cu12`); https://pypi.org/pypi/polars/json
- Wheel contents read: `cudf_polars_cu12-26.10.2` (sha256 `e24bef1b4b0488249097fe18194792609bbe669b5fd76b89c1fb8cec37cfe318`,
  git `2cd6c6c0`), `cudf_polars_cu12-26.2.1`, nightly `26.12.0a568.post261026052714` (git `01a78716`) from
  https://pypi.anaconda.org/rapidsai-wheels-nightly/simple/cudf-polars-cu12/
- Upstream source: https://github.com/rapidsai/cudf (`python/cudf_polars/pyproject.toml`,
  `cudf_polars/utils/versions.py`, commits `1340933`, `8654d64`, `e0fb724` via the GitHub commits API)
- libcudf regex support: https://github.com/rapidsai/cudf/blob/main/cpp/doxygen/regex.md
- RAPIDS install requirements: https://docs.nvidia.com/datascience/install/
- RAPIDS release schedule: https://docs.nvidia.com/datascience/maintainers/ (26.12 release Wed Dec 9 to Thu Dec 10, 2026)
- Polars GPUEngine reference: https://docs.pola.rs/api/python/stable/reference/lazyframe/api/polars.lazyframe.engine_config.GPUEngine.html
- Spark RAPIDS 26.08 requirements: https://docs.nvidia.com/cudf-spark/latest/getting-started/overview.html ;
  on-premise guide https://docs.nvidia.com/spark-rapids/user-guide/latest/getting-started/on-premise.html ;
  Maven listing https://repo.maven.apache.org/maven2/com/nvidia/rapids-4-spark_2.12/26.08.1/
- Lambda: https://docs.lambda.ai/public-cloud/on-demand/ ; https://lambda.ai/lambda-stack-deep-learning-software
