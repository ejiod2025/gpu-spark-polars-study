# Polars GPU engine (cudf-polars) compatibility

Research date: 2026-10-26. Author: data-quality engineer, Phase 0. Sources are primary (PyPI metadata,
wheel contents, upstream source/commits, NVIDIA documentation); URLs are in the last section.
Static-analysis findings (section 5) were obtained by reading the cudf-polars wheel source; no GPU
was available. They are predictions that `preflight.py` must confirm on the H100 host.

## 1. Bottom line

1. **No released cudf-polars supports Polars 2.0.0**, the version in `evidence/requirements.lock.txt`.
   The newest release, `cudf-polars-cu12/-cu13 26.10.2` (2026-10-22), requires
   `polars>=1.35,<1.45` and Python >= 3.11. Polars 2.0.0 was released 2026-10-06 and 2.1.0 on
   2026-10-25.
2. Polars 2.0.x support exists only on the unreleased RAPIDS **26.12** line (`polars>=2.0,<2.1`,
   Python >= 3.12). It is available as nightly wheels only; 26.12 is scheduled for 2026-12-09/10.
3. **Recommendation (Option B): run both Polars engines on Polars 1.44.2** with
   `cudf-polars-cu12==26.10.2` on **Python 3.11**. At SF1 all 12 queries return the same schemas and
   tolerance-equal rows on Polars 1.44.2 and 2.0.0 (section 4). Costs: the Polars CPU version
   differs from the A100 record, and Python moves from 3.10.12 to 3.11.
4. Python 3.10 (the A100 interpreter) is not supported by any cudf-polars from 26.4.0 onward.
5. Expect `cx_window` to fail on `polars_gpu` under `raise_on_fail=True` (`cum_count().over(...)` is
   not implemented in cudf-polars 26.10.2 or the 26.12 nightly). `cx_q13` is at risk (`(?s)` inline
   regex flag). Decide whether these are accepted, documented gaps (recommended) or need a separate
   GPU-friendly query variant.

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
| **B** | **1.44.2** | **cu12 26.10.2** | **3.11** | **Recommended.** Released, exactly pinnable, newest Polars a release supports, 1.44.2 outputs verified equal to 2.0.0. |
| C | 2.0.0 | cu12 26.12.0aNNN nightly | 3.12 | Keeps Polars 2.0.0 but alpha, unreproducible (the nightly index keeps only the last few days; wheelhouse must be archived at install time), multi-key `over(order_by)` falls back to CPU. Resolves cleanly (checked). Only if Polars 2.0.0 is mandatory. |
| D | 2.0.x | cu12 26.12.0 release | 3.12 | Best end state; not available before 2026-12-09/10 (schedule subject to change). Re-evaluate then. |

Effect on A100 comparability under Option B: GPU-engine and Spark comparisons are unaffected by the
Polars version. The `polars` CPU engine runs 1.44.2 on H100 versus 2.0.0 on A100; the engine was
already labelled non-comparable across hosts (30 vs 26 vCPU), so this is one more caveat, not a new
class of problem. Performance difference 1.44.2 vs 2.0.0 for CPU streaming is **not measured**; a
CPU-only A/B on the H100 host (two venvs, SF10/SF30, `polars` engine) is cheap if the lead wants a
bridge factor. The A100 evidence itself is untouched (analyzers read saved results only).

## 4. Local parity evidence (Polars 1.44.2 vs 2.0.0, SF1, this Mac)

Same code path as `worker.py` (`scan_parquet`, Float64 casts from `FLOAT_COLUMNS`,
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
  as a single partition **on the GPU** and emits a warning (can use a lot of memory). Recommendation for the
  runner: keep the defaults (do not tune), and capture those warnings into the results metadata so
  single-partition fallbacks are visible. Use `fallback_mode="raise"` only if the lead wants them to
  be hard failures.
- Default memory resource: `rmm.mr.CudaAsyncMemoryResource` with `release_threshold` = 90% of device
  memory (`MemoryResourceConfig.default()`); this is a suitable value for the contract's
  `memory_resource` string when none is passed.
- Eager and background collection never run on the GPU (Polars 2.0.0 `_post_opt_callback`); the benchmark
  uses lazy `collect`, so this does not matter.

### Expression coverage for our 12 queries (static read of the wheel; confirm in preflight)

| Query | Constructs | Predicted on 26.10.2 | Basis |
|---|---|---|---|
| q01 | filter, multi-key `group_by`, sum/mean/`pl.len`, sort | Supported | `Agg`, `Len` nodes |
| q03, q10, q12, q14 | inner joins, `is_between`, `is_in`, `when/then/otherwise`, `str.starts_with`, descending multi-key sort, `limit` | Supported | `Join`, `BooleanFunction.IsBetween/IsIn`, `StringFunction.StartsWith`, `Ternary`, `Slice` |
| q06 | filter + scalar sum | Supported | |
| cx_q09 | 6-table join incl. composite key, `str.contains(literal=True)`, `dt.year` | Supported | `libcudf strings.find.contains`, `TemporalFunction.Year` |
| cx_q13 | left join, nested `group_by`, `count`, `str.contains("(?s)special.*requests")` | **At risk** | regex built by `RegexProgram.create(pattern)` with default flags. libcudf documents inline `(?i)` as unsupported and lists no `(?s)`; "invalid regex patterns will result in undefined behavior". Failure mode is either a `NotImplementedError` (recorded query error) or, worst case, a misparse. Data check on SF1: no `o_comment` contains a newline, and the pattern with and without `(?s)` matches the same 15,702 orders, so DOTALL semantics cannot change results on this data; only the cross-engine output check can reveal a misparse of the inline flag itself. |
| cx_semi_anti | `how="semi"`, `how="anti"`, `is_in`, column-vs-column comparison | Supported | `Join` handles Inner/Left/Right/Full/Semi/Anti/Cross |
| cx_distinct | 4-table join, `drop_nulls().n_unique()` in `group_by` | Supported | fused to `nunique(null_handling=EXCLUDE)` in `decompose_single_agg` |
| cx_self_join | equi-join on `l_orderkey`, post-filter inequality, `group_by`/`len` | Supported (memory heavy: join fan-out) | |
| **cx_window** | `shift(1).over(key, order_by=[2 keys], nulls_last=True)` and **`fill_null(0).cum_count().over(key, order_by=[3 keys], nulls_last=True)`** | **Unsupported** (`cum_count().over`) | `GroupedWindow.__init__` only allows `Len`, `Agg`, rolling windows and unary functions `rank, fill_null_with_strategy, cum_sum, diff, shift, shift_and_fill`; anything else raises `NotImplementedError("Unsupported over(...) only expression: ...")`. Identical whitelist in the 26.12 nightly. `shift().over(multi-key order_by)` alone is supported in 26.10.2 but falls back to CPU on the 26.12/Polars 2.0 line. |

Also: `FLOAT_COLUMNS` casts read Parquet `DECIMAL(15,2)` and cast to Float64; `dtypes.can_cast` defers to
`plc.unary.is_supported_cast` for fixed-width types, which libcudf supports for decimal to double.

**Consequence for the contract.** Under `raise_on_fail=True`, `cx_window` on `polars_gpu` becomes a
recorded query error at every scale. That is consistent with non-negotiable 2 (a failure, never a CPU timing
labelled GPU) but means the complex suite has 5/6 queries on `polars_gpu`; analyzers must tolerate a
missing engine/query cell. If a GPU-capable comparison for that case is wanted, a **separate,
clearly labelled** variant using `rank("ordinal").over(...)` (a supported window function) would be
needed; it is not output-identical by construction and must not replace the parity query.
`preflight.py --expect-gpu-unsupported cx_window` reports such a failure as XFAIL instead of FAIL; the
default is zero expected failures.

## 6. CUDA, driver, hardware, Python

- RAPIDS (cudf-polars, `-cu12` wheels): compute capability >= 7.0 (Volta or newer, so Hopper sm_90
  qualifies); CUDA 12 needs driver >= 525.60.13; CUDA 13 needs >= 580.65.06; Linux with glibc >= 2.28.
  The A100 host reported 580.105.08, which satisfies either; Lambda H100 images are expected to be similar
  but `setup_h100.sh` and `preflight.py` record the real value. The pinned `-cu12` wheels bundle CUDA
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
  80 GB, 26 vCPU, 225 GiB RAM, 2.75 TiB root volume; persistent filesystems appear under
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

## 8. Open items to confirm on the H100 host

1. `preflight.py` outcome per polars_gpu query versus the table in section 5 (especially cx_window and cx_q13).
2. Warnings emitted by the streaming executor at SF1 (single-partition fallbacks).
3. SF100/SF300 behaviour (VRAM, streaming executor), recorded as query errors if they fail.
4. Optional bridge: Polars 1.44.2 vs 2.0.0 CPU streaming timings on the same host.

## 9. Environments built by `setup_h100.sh`

| Env | Path | Python | Polars | Purpose |
|---|---|---|---|---|
| `gpu` (default) | `.venv` | 3.11 | 1.44.2 (+ `requirements-gpu.txt`, cudf-polars-cu12 26.10.2) | all four engines; both Polars engines share this Polars |
| `a100compat` | `.venv-a100compat` | 3.10 | 2.0.0 (the lock verbatim) | optional CPU-Polars continuity run; Spark engines behave the same in either env |

`./setup_h100.sh --envs both` builds both; every pin file, interpreter version and path is a flag
(`--python`, `--venv`, `--a100compat-python`, `--a100compat-venv`, `--gpu-requirements`), so switching
to Option A, C or D means pointing `--gpu-requirements` at another pin file. PySpark 3.5.9 declares
Python 3.8 to 3.11 support (PyPI classifiers; verified importing on 3.11.13), not 3.12. A run must not
mix the two Polars versions between `polars` and `polars_gpu`: `preflight.py` fails if the two engines
report different Polars versions.

## 10. Input regeneration determinism (measured 2026-10-26)

Question: can regenerating data on the H100 reproduce the A100 files? Answer: content yes,
bytes no. DuckDB 1.5.6, `generate_data.py`, SF1, compared with `evidence/INPUT_MANIFESTS/production-20261025/sf1.json`
(`CALL dbgen(sf = 1.0)`, 30 threads, 57 Parquet files):

| Regeneration | Files with equal path and sha256 | Other findings |
|---|---|---|
| macOS arm64, threads 8 | 0/57 | 8 lineitem and 8 orders files (A100: 30 and 13) |
| macOS arm64, threads 30 | 0/57 | same 57 paths as the A100; sizes differ for every file (region 1077 vs 1071 bytes): Parquet bytes depend on the platform |
| macOS arm64, threads 30, second run | 9/57 vs the first run | per-thread output makes the row-to-file assignment depend on scheduling, even on one host |
| Linux x86_64 (amd64 container), threads 30 | 10/57 | table byte totals equal for 8/8 tables (total 325,716,220 = A100); file-hash multiset equal for 7/8 tables (all but lineitem), i.e. the same files under permuted `data_N` names |

All four datasets have the A100-host fingerprints (rows, schemas, key ranges, row/key hash sum and
xor for all 8 tables; `evidence/validation/large-generation-proof-linux.json`). So on the H100:
generate with `--threads 30` (the file layout follows the thread count), expect byte-level
differences, and gate on `verify_generated_inputs.py` (content fingerprint, rows, schema, settings,
disk self-consistency). Byte identity is reported but not a gate unless `--byte-identity require`.

## 11. Sources

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

## 12. GH200 / aarch64 (amendment A3)

Research date: 2026-10-29. Question: do the pinned versions exist as aarch64 builds, so that the
`gpu_1x_gh200` campaign can run with the same package versions (A3: if any pin has no aarch64 build the
GH200 campaign is not run and no version changes)? **Verdict: GO.** Every pin, the RAPIDS Spark jar, the
JDK and uv have aarch64 builds, no version changes are needed, and `setup_h100.sh` was run for real in a
`linux/arm64` Ubuntu 22.04 container (section 12.5).

### 12.1 Host (Lambda documentation, https://docs.lambda.ai/public-cloud/on-demand/)

`gpu_1x_gh200`: GH200, 1 GPU, 96 GB, 64 vCPU, 432 GiB RAM, 4 TiB root volume (the table is dated December
2025; the 500 GiB data-volume requirement of `setup_h100.sh` is met by the root volume). The table
footnote says the GH200 has an onboard ARM CPU and to "verify that your intended workloads run on ARM".
Instances run Ubuntu 22.04 LTS by default; the Lambda Stack images listed as supporting `x86-64, arm64`
are Lambda Stack 22.04 (system Python 3.10) and 24.04 (Python 3.12). Everything below assumes Ubuntu 22.04
(glibc 2.35, JDK package and artifact selection); the wheels need glibc >= 2.28 only.

### 12.2 Method

- PyPI: `https://pypi.org/pypi/<name>/<version>/json`, every file's wheel tags evaluated with
  `packaging.tags` for CPython 3.11 (gpu env) and 3.10 (a100compat env) on Linux aarch64, glibc 2.35
  (`manylinux_2_17` ... `manylinux_2_35`, `manylinux2014`, `py3-none-any`). Exactly one file per pin
  is installable in every case.
- Closure: `uv pip compile --python-platform aarch64-manylinux_2_35 --only-binary :all: --no-binary pyspark`
  resolves the same 42 packages and versions (gpu env, Python 3.11) and 7 (a100compat, Python 3.10) as
  `x86_64-manylinux_2_28`; the lists are identical, so there is no aarch64-only or x86-only package.
- Maven Central listing and checksum files for the jar; the jar was downloaded into a scratch directory
  (never executed on the Mac), hashed, integrity-tested with `unzip -t` and its PGP signature verified.
- Container rehearsal: `docker run --platform linux/arm64 ubuntu:22.04` on Docker Desktop (Apple silicon, native).

### 12.3 Availability per pin

All 37 pins of `requirements-gpu.txt` (35 closure pins plus `polars`/`polars-runtime-32` 1.44.2) have an
aarch64-installable file at the SAME version. The exact PyPI file names are below; their sha256 are in
`requirements-gpu-aarch64.txt`, which `setup_h100.sh` uses on aarch64 (hash mismatch aborts the install;
checked by tampering one hash). 30 are aarch64 platform wheels, 7 are architecture-independent wheels.

| Pin | Version | aarch64 artifact (PyPI) | Kind | MB |
|---|---|---|---|---|
| `cuda-bindings` | 12.9.10 | `cuda_bindings-12.9.10-cp311-cp311-manylinux_2_24_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 7.1 |
| `cuda-core` | 1.2.2 | `cuda_core-1.2.2-cp311-cp311-manylinux_2_24_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 6.2 |
| `cuda-pathfinder` | 1.8.4 | `cuda_pathfinder-1.8.4-py3-none-any.whl` | any | 0.1 |
| `cuda-toolkit` | 12.9.2.0 | `cuda_toolkit-12.9.2.0-py2.py3-none-any.whl` | any | 0.0 |
| `cudf-polars-cu12` | 26.10.2 | `cudf_polars_cu12-26.10.2-py3-none-any.whl` | any | 0.7 |
| `cudf-streaming-cu12` | 26.10.2 | `cudf_streaming_cu12-26.10.2-cp311-abi3-manylinux_2_24_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 0.9 |
| `cupy-cuda12x` | 14.2.0 | `cupy_cuda12x-14.2.0-cp311-cp311-manylinux2014_aarch64.whl` | aarch64 | 148.2 |
| `kvikio-cu12` | 26.10.0 | `kvikio_cu12-26.10.0-cp311-abi3-manylinux_2_24_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 0.6 |
| `libcudf-cu12` | 26.10.2 | `libcudf_cu12-26.10.2-py3-none-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 662.3 |
| `libcudf-streaming-cu12` | 26.10.2 | `libcudf_streaming_cu12-26.10.2-py3-none-manylinux_2_24_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 0.5 |
| `libkvikio-cu12` | 26.10.0 | `libkvikio_cu12-26.10.0-py3-none-manylinux_2_28_aarch64.whl` | aarch64 | 2.9 |
| `librapidsmpf-cu12` | 26.10.0 | `librapidsmpf_cu12-26.10.0-py3-none-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 1.9 |
| `librmm-cu12` | 26.10.0 | `librmm_cu12-26.10.0-py3-none-manylinux_2_24_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 4.7 |
| `libucx-cu12` | 1.21.0 | `libucx_cu12-1.21.0-py3-none-manylinux_2_24_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 39.8 |
| `libucxx-cu12` | 0.52.0 | `libucxx_cu12-0.52.0-py3-none-manylinux_2_24_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 4.7 |
| `numpy` | 2.4.6 | `numpy-2.4.6-cp311-cp311-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 16.0 |
| `nvidia-cublas-cu12` | 12.9.2.10 | `nvidia_cublas_cu12-12.9.2.10-py3-none-manylinux_2_27_aarch64.whl` | aarch64 | 575.0 |
| `nvidia-cuda-nvrtc-cu12` | 12.9.86 | `nvidia_cuda_nvrtc_cu12-12.9.86-py3-none-manylinux2014_aarch64.manylinux_2_17_aarch64.whl` | aarch64 | 44.5 |
| `nvidia-cuda-runtime-cu12` | 12.9.79 | `nvidia_cuda_runtime_cu12-12.9.79-py3-none-manylinux2014_aarch64.manylinux_2_17_aarch64.whl` | aarch64 | 3.5 |
| `nvidia-cufft-cu12` | 11.4.1.4 | `nvidia_cufft_cu12-11.4.1.4-py3-none-manylinux2014_aarch64.manylinux_2_17_aarch64.whl` | aarch64 | 200.8 |
| `nvidia-cufile-cu12` | 1.14.1.1 | `nvidia_cufile_cu12-1.14.1.1-py3-none-manylinux_2_27_aarch64.whl` | aarch64 | 1.1 |
| `nvidia-curand-cu12` | 10.3.10.19 | `nvidia_curand_cu12-10.3.10.19-py3-none-manylinux_2_27_aarch64.whl` | aarch64 | 68.3 |
| `nvidia-cusolver-cu12` | 11.7.5.82 | `nvidia_cusolver_cu12-11.7.5.82-py3-none-manylinux_2_27_aarch64.whl` | aarch64 | 337.9 |
| `nvidia-cusparse-cu12` | 12.5.10.65 | `nvidia_cusparse_cu12-12.5.10.65-py3-none-manylinux2014_aarch64.manylinux_2_17_aarch64.whl` | aarch64 | 366.4 |
| `nvidia-libnvcomp-cu12` | 5.3.0.16 | `nvidia_libnvcomp_cu12-5.3.0.16-py3-none-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 41.3 |
| `nvidia-ml-py` | 13.615.71 | `nvidia_ml_py-13.615.71-py3-none-any.whl` | any | 0.1 |
| `nvidia-nvjitlink-cu12` | 12.9.86 | `nvidia_nvjitlink_cu12-12.9.86-py3-none-manylinux2014_aarch64.manylinux_2_17_aarch64.whl` | aarch64 | 39.5 |
| `nvtx` | 0.2.16 | `nvtx-0.2.16-cp311-cp311-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 2.9 |
| `packaging` | 26.3 | `packaging-26.3-py3-none-any.whl` | any | 0.1 |
| `polars` | 1.44.2 | `polars-1.44.2-py3-none-any.whl` | any | 0.9 |
| `polars-runtime-32` | 1.44.2 | `polars_runtime_32-1.44.2-cp310-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl` | aarch64 | 46.6 |
| `pylibcudf-cu12` | 26.10.2 | `pylibcudf_cu12-26.10.2-cp311-abi3-manylinux_2_24_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 7.5 |
| `rapids-logger` | 0.3.0 | `rapids_logger-0.3.0-py3-none-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 0.3 |
| `rapidsmpf-cu12` | 26.10.0 | `rapidsmpf_cu12-26.10.0-cp311-abi3-manylinux_2_24_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 2.8 |
| `rmm-cu12` | 26.10.0 | `rmm_cu12-26.10.0-cp311-abi3-manylinux_2_24_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 0.9 |
| `typing-extensions` | 4.16.0 | `typing_extensions-4.16.0-py3-none-any.whl` | any | 0.0 |
| `ucxx-cu12` | 0.52.0 | `ucxx_cu12-0.52.0-cp311-abi3-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl` | aarch64 | 0.7 |

The remaining pins come from `evidence/requirements.lock.txt`. Sha256 of the exact aarch64 files
(PyPI `urls[].digests.sha256`); `pyspark` is a source distribution on every platform (pure Python, the same
file as on x86_64), `py4j` and `polars` are architecture-independent wheels. The 3.11 rows are the lock
without its Polars pins (Polars 1.44.2 is in `requirements-gpu-aarch64.txt`); the 3.10 rows are the whole
lock (the `a100compat` env, which on aarch64 installs the lock unhashed exactly as on x86_64).

| Pin | Python | aarch64 artifact | sha256 |
|---|---|---|---|
| `duckdb==1.5.6` | 3.11 | `duckdb-1.5.6-cp311-cp311-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl` | `56c0f71c6bee982e9c30568bb12371bf66b26bf129c75d8d7f60bc69d6590a2c` |
| `psutil==7.1.2` | 3.11 | `psutil-7.1.2-cp36-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl` | `e2aeb9b64f481b8eabfc633bd39e0016d4d8bbcd590d984af764d80bf0851b8a` |
| `py4j==0.10.9.9` | 3.11 | `py4j-0.10.9.9-py2.py3-none-any.whl` | `c7c26e4158defb37b0bb124933163641a2ff6e3a3913f7811b0ddbe07ed61533` |
| `pyarrow==22.0.0` | 3.11 | `pyarrow-22.0.0-cp311-cp311-manylinux_2_28_aarch64.whl` | `b41f37cabfe2463232684de44bad753d6be08a7a072f6a83447eeaf0e4d2a215` |
| `pyspark==3.5.9` | 3.11 | `pyspark-3.5.9.tar.gz` (sdist, pure Python) | `ea27adc39ddac9413b8951e45aa748cbed6c785971b81386efc41938f6243d93` |
| `duckdb==1.5.6` | 3.10 | `duckdb-1.5.6-cp310-cp310-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl` | `95a6b91bb9149950baeb5d02466c006550d0ea98b9d10f15f7d614a8eb32e174` |
| `polars==2.0.0` | 3.10 | `polars-2.0.0-py3-none-any.whl` | `35d62f3541b7a6d4c360a2e2f07fccc0c2bcbd33b0ea51c83a25417a47a3f3ad` |
| `polars-runtime-32==2.0.0` | 3.10 | `polars_runtime_32-2.0.0-cp310-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl` | `8b85bb42e6009acc9629afcc70a83473fd468694d6a30ffb0ab376c8dd1a0a17` |
| `psutil==7.1.2` | 3.10 | `psutil-7.1.2-cp36-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl` | `e2aeb9b64f481b8eabfc633bd39e0016d4d8bbcd590d984af764d80bf0851b8a` |
| `py4j==0.10.9.9` | 3.10 | `py4j-0.10.9.9-py2.py3-none-any.whl` | `c7c26e4158defb37b0bb124933163641a2ff6e3a3913f7811b0ddbe07ed61533` |
| `pyarrow==22.0.0` | 3.10 | `pyarrow-22.0.0-cp310-cp310-manylinux_2_28_aarch64.whl` | `b9d71701ce97c95480fecb0039ec5bb889e75f110da72005743451339262f4ce` |
| `pyspark==3.5.9` | 3.10 | `pyspark-3.5.9.tar.gz` (sdist, pure Python) | `ea27adc39ddac9413b8951e45aa748cbed6c785971b81386efc41938f6243d93` |

### 12.4 RAPIDS Spark jar, JDK, uv, Python

| Artifact | Identity | Evidence |
|---|---|---|
| RAPIDS Accelerator jar | `rapids-4-spark_2.12-26.08.1-cuda12-arm64.jar`, 979,914,456 bytes, sha256 `44ac76f748b2d1091dcb057eb8effa624967622cdf648ff8d0940f7f19dd4735` | https://repo.maven.apache.org/maven2/com/nvidia/rapids-4-spark_2.12/26.08.1/rapids-4-spark_2.12-26.08.1-cuda12-arm64.jar |
| same, other digests | sha1 `9cb2c720961e8514ab8599f4ca0d4eff40ee8ee6`, md5 `edc2ca22fe48e3c06ac18fa7c0650343` | equal to the `.sha1` / `.md5` files on Maven Central (Maven publishes no `.sha256`; the sha256 above was computed from the download) |
| OpenJDK 17 | apt `openjdk-17-jre-headless` 17.0.20.1+1-1~22.04, `arm64`, .deb sha256 `565b48dedfdf70fbd8f8c63a8407e63b659f4c97b4f97103599a645d6d606be1` | `apt-cache show` on Ubuntu 22.04 arm64 (ports.ubuntu.com); `JAVA_HOME=/usr/lib/jvm/java-17-openjdk-arm64` |
| uv | 0.8.13, `uv-0.8.13-py3-none-manylinux_2_28_aarch64.whl`, sha256 `4c2c5e5962239ecaff6444d5bc22422a9bd2da25a80adc6ab14cb42e4461b1cf` | PyPI; installed by `pip install --user uv==0.8.13` as on x86_64 |
| CPython | uv-managed `cpython-3.11.13-linux-aarch64-gnu` and 3.10.12 (python-build-standalone), interpreter sha256 recorded by the setup script | no PPA needed, as on x86_64 |

- The Maven listing offers four jars per release: unclassified and `-cuda12` (x86_64, identical bytes,
  998,327,432 bytes) and `-cuda12-arm64`, `-cuda13-arm64` (aarch64) plus `-cuda13`. `-cuda12-arm64` is the
  aarch64 counterpart of the jar used on x86_64 (the CUDA 12 build, matching the `-cu12` wheels).
- The jar contains `aarch64/Linux/libcudf.so`, `libcudfjni.so`, `libnvmljni.so` and `libjucx_aarch64.so`.
  Its PGP signature (`.asc`) verifies with "Good signature" against key
  `7A8A 3990 9B9B 2024 10C2 A26F 1D9E 1285 6543 92EF` ("NVIDIA Spark (For the signature of spark-rapids
  release jars)", fetched from keys.openpgp.org; the key is not in a web of trust, so this proves the file
  is what that key signed, not who holds the key).
- The jar's sha256 is in `setup_h100.sh` (`JAR_SHA_BUILTIN`). `evidence/DEPENDENCY_SHA256.txt` lists only the
  x86_64 jar; adding the line `44ac76f7...4735  jars/rapids-4-spark_2.12-26.08.1-cuda12-arm64.jar` there is
  honoured by the script and must equal the built-in value (otherwise the script stops).
- RAPIDS Accelerator 26.08.1 reports "using cudf 26.08.0" (the jar's bundled libcudf), a different cudf
  from the 26.10 Python wheels; that is the same on x86_64 and irrelevant to the comparison.

### 12.5 What `setup_h100.sh` does on aarch64, and the rehearsal

On `uname -m` = `aarch64` the script changes exactly these things; on `x86_64` the dry-run output of the
old and new script was byte-for-byte identical in a `linux/amd64` container (43 lines compared, two
runs):

- downloads the `-cuda12-arm64` jar and verifies the sha256 above (a file with a different sha256 is never
  overwritten or accepted);
- uses `JAVA_HOME=/usr/lib/jvm/java-17-openjdk-arm64`;
- accepts GPU names containing `GH200` (`--expect-gpu NAME` sets any other name; `--allow-any-gpu` still
  downgrades a mismatch to a warning; `nvidia-smi` is expected to report `NVIDIA GH200 480GB`, where 480 GB
  is the LPDDR5X pool and the HBM3 is 96 GB);
- layers `requirements-gpu-aarch64.txt` (same versions, per-file sha256) instead of `requirements-gpu.txt`;
- writes `rerun/setup-logs/platform-artifacts-<stamp>.txt`: kernel and glibc, the JDK package version and
  sha256 of `bin/java` and `libjvm.so`, the jar sha256, the uv version, each interpreter (path, version,
  sha256) and `uv pip freeze` of both envs;
- writes the same `rerun/h100-env.sh` variables (`JAVA_HOME`, `PATH`, `PYTHON_GPU_ENV`,
  `PYTHON_A100COMPAT_ENV`, `DATA_ROOT`, `RAPIDS_JAR`); only the values differ.

Rehearsal: Docker Desktop on Apple silicon, `ubuntu:22.04` `linux/arm64` (glibc 2.35, Python 3.10 system
interpreter, user `ubuntu` with sudo, a stand-in `nvidia-smi` printing `NVIDIA GH200 480GB, 580.105.08`).
`./setup_h100.sh --envs both` ran to "OK -- setup complete" (exit 0) and a second run was idempotent
(venvs, jar and JDK detected, all pins re-verified).

| Step | Result in the container |
|---|---|
| apt `openjdk-17-jre-headless` (arm64) | installed, 17.0.20.1, `/usr/lib/jvm/java-17-openjdk-arm64` exists |
| `pip install --user uv==0.8.13` | ok |
| `uv venv --python 3.11` / `3.10` | ok (uv downloads aarch64 CPython 3.11.13 / 3.10.12) |
| gpu env install (42 pins, 37 hash-verified) | ok; `uv pip check` clean; "all 42 pins match" |
| a100compat env install (7 pins) | ok; `uv pip check` clean; "all 7 pins match" |
| `import cudf_polars` | ok without GPU or driver (`26.10.02`); `rmm`, `pylibcudf`, `cupy`, `kvikio` also import |
| jar download and sha256 | ok (verified against the recorded value) |
| Polars 1.44.2 CPU streaming, DuckDB 1.5.6 `INSTALL tpch; CALL dbgen`, PyArrow 22, psutil | ok on aarch64 (SF0.01 Parquet written and queried) |
| PySpark 3.5.9 `local[2]` on the arm64 JDK 17 | ok (Parquet read, group-by) |
| Spark with the arm64 RAPIDS jar and `spark.plugins=com.nvidia.spark.SQLPlugin` | plugin and JNI libraries load (`RAPIDS Accelerator 26.08.1 using cudf 26.08.0`, `Cuda.getDeviceCount` reached); fails only at the CUDA driver, as expected without a GPU: `cudaErrorInsufficientDriver` |
| `LazyFrame.collect(engine=pl.GPUEngine(raise_on_fail=True))` | fails without a GPU: `ComputeError ... NVMLError_LibraryNotFound` (expected) |
| negative: one wrong wheel hash; corrupted jar; H100 name on aarch64 | each stops the real run (uv "Hash mismatch"; "has sha256 ..., expected ..."; "is not a GH200") |

Not shown by the rehearsal (needs the GH200 host): GPU execution of cudf-polars and of the Spark plugin,
NVML/driver version on that host (the script requires >= 525.60.13 as on x86_64), unified-memory behaviour
of the Grace-Hopper pair, and any difference between the Lambda Stack image and plain Ubuntu 22.04.

### 12.6 Items outside `setup_h100.sh` that the GH200 campaign needs (not changed here)

1. `preflight.py` hard-codes `JAR_RELATIVE = "jars/rapids-4-spark_2.12-26.08.1.jar"` and looks up the
   expected sha256 for that file name; given the arm64 jar (`--jar "$RAPIDS_JAR"`) its `env.jar` check
   reports a MISMATCH or "no checksum". It needs to look up the checksum by the jar's own file name (and
   the arm64 line must exist in `evidence/DEPENDENCY_SHA256.txt`).
2. `preflight.py` (env.gpu and the `gpu_inventory` check) and `run_campaign.py` require `H100` in the GPU
   name unless `--allow-any-gpu`; the GH200 campaign runs with `--allow-any-gpu` (as A2 hosts do).
3. Telemetry and any other code that assumes `/usr/lib/jvm/java-17-openjdk-amd64` should read `JAVA_HOME`.

## 13. Blackwell (B200) (amendment A2)

Research date: 2026-10-29. Question (A2 precondition): does the pinned stack (`requirements-gpu.txt`,
`cudf-polars-cu12 26.10.2` with its RAPIDS and CUDA 12.9 closure, and the unclassified = CUDA 12 jar
`rapids-4-spark_2.12-26.08.1`) run on `gpu_1x_b200_sxm6` (B200, compute capability 10.0 = sm_100, x86_64)
with no version change? Static analysis of the shipped binaries and vendor documentation; no B200 was
available, so nothing here is a measurement on the GPU. Downloaded artifacts were only unpacked and read
(never imported or executed).

### 13.1 Verdicts

| Component | Verdict | Basis |
|---|---|---|
| `libcudf-cu12` 26.10.2 (the only wheel with large GPU code) | **SUPPORTED** | native sm_100 cubin in all 463 kernel translation units; RAPIDS 26.10 platform page lists Blackwell (100, 120) for CUDA 12.2-12.9 |
| `libcudf-streaming-cu12`, `nvidia-libnvcomp-cu12 5.3.0.16` | **SUPPORTED** | native sm_100 cubins (2 and 36 translation units) |
| `nvidia-cuda-nvrtc-cu12`, `nvidia-nvjitlink-cu12` 12.9.86 | **SUPPORTED** | accept `sm_100/100a/100f`, `compute_100*`, `lto_100*` (only used by an off-by-default JIT path, 13.3) |
| `pylibcudf-cu12`, `rmm-cu12`, `librmm-cu12`, `kvikio-cu12`, `libkvikio-cu12`, `rapidsmpf-cu12`, `librapidsmpf-cu12`, `cudf-polars-cu12` | **SUPPORTED** (nothing to compile for) | of the 280 `.so` files other than libcudf, device code exists only in libcudf_streaming, libnvcomp and three cupy extensions; `cudf-polars` is pure Python and has no architecture gate (its only CUDA attribute query is managed-memory access) |
| `cupy-cuda12x` 14.2.0 | SUPPORTED, not on the query path | sm_100 cubin and compute_100 PTX (checked on `cub`); a declared dependency of `kvikio-cu12`, but `cudf_polars`, `pylibcudf` and `kvikio` import it only in an optional codec module and docstrings |
| RAPIDS Accelerator `rapids-4-spark_2.12-26.08.1.jar` (sha256 equals `evidence/DEPENDENCY_SHA256.txt`) | **SUPPORTED** | native sm_100 cubin in all 517 kernel translation units of its `libcudf.so`; NVIDIA docs list GB100 (Blackwell data-center) among the tested GPUs |
| Driver on the Lambda B200 image | **UNKNOWN (version)** | not documented by Lambda; any working B200 driver is >= 570.26 (Blackwell minimum), which runs the CUDA 12.9 user space (13.4) |
| Overall (A2 "pinned stack supports Blackwell") | **SUPPORTED at the binary and vendor-documentation level, with one open correctness risk (13.5)** | the risk is a CUDA 12.9 compiler defect reproduced on sm_120 only; it is not shown on sm_100 and not shown to affect these 12 queries |

### 13.2 What is compiled in (`cuobjdump 12.9.82 --list-elf --list-ptx`)

| Artifact | Translation units with GPU code | Cubins (SASS) per unit | PTX |
|---|---|---|---|
| `libcudf.so` (wheel 26.10.2) | 463 (+21 small containers of a kind `cuobjdump` does not list; probably the LTO-IR fragments for libcudf's nvJitLink path, not decoded) | sm_70, 75, 80, 86, **90a**, **100**, 120, 120a (one each) | compute_120 only |
| `libcudf_streaming.so` | 2 | same set as libcudf | compute_120 only |
| `libnvcomp.so.5` 5.3.0.16 | 36 | sm_70, 75, 80, 86, 89, 90, **100**, 101, 120 | compute_70 |
| `libcudf.so` inside the jar (`amd64/Linux/`, 1,491,824,968 bytes) | 517 (+21 containers of that same unlisted kind) | sm_70, 75, 80, 86, **100**, 120 in all 517; 90a and 120a in 503; 89, 90, 101 in the other 14 | compute_120 (503), compute_70 (14) |
| jar `libcufilejni.so` (cuFile JNI helper) | 1 | sm_60, 61, 62, 70, 72, 75, 80, 86 (no sm_100) | compute_80 |
| jar `libprofilerjni.so`, `libcudfjni.so`, `libnvmljni.so` | 0 | none (`cuobjdump`: "does not contain device code" for libprofilerjni) | none |

The sm_100 cubins were produced with `ptxas -arch sm_100f` (the option strings are embedded: 463 hits in the
wheel, CUDA 12.9 toolchain; the jar's `libcudf.so` carries `release 12.9, V12.9.86`). A family-specific
`f` target is compatible with compute capability 10.0, 10.3 and 10.7 (CUDA Programming Guide, compute
capabilities appendix, "Family-Specific Compatibility"), and the B200 is 10.0 (developer.nvidia.com/cuda-gpus).
The jar's own build manifests state `gpu_architectures=100;101;120;70;75;80;86;89;90` (spark-rapids-jni) and
`100;120;70;75;80;86;90` (cudf-java).

### 13.3 What happens at runtime on a B200

- **No PTX JIT for libcudf, libcudf_streaming, libnvcomp or the jar's libcudf.** Every kernel has an sm_100
  cubin, so the CUDA runtime picks it as is. The embedded PTX (compute_120 / compute_70) is irrelevant for
  sm_100: PTX only JIT-compiles forward to a >= compute capability, so compute_120 PTX could never run on 10.0.
  Had the sm_100 cubins been absent, the failure would be `cudaErrorNoKernelImageForDevice`, not a slow first
  call. The Hopper `sm_90a` cubins that H100 runs are architecture-specific and are not used on Blackwell
  (Blackwell Compatibility Guide, 1.2): H100 and B200 therefore execute different compiled code from the same
  source, which is a further reason cross-host ratios are descriptive only.
- First-use cost per kernel is cubin module loading, the same mechanism as on the H100; nothing Blackwell
  specific needs to be added to the warm-up note.
- The one place PTX can appear is jar `libcufilejni.so` (compute_80 PTX only, no sm_100 cubin): any device
  kernel launched from that cuFile helper would be driver-JIT-compiled once (NVIDIA: "the CUDA driver caches
  the cubins generated as a result of the PTX JIT"). It is the JNI wrapper for cuFile (GPUDirect Storage),
  which this study does not configure; I did not trace whether any kernel in it is ever launched. If a
  warm-up ever shows a first-call spike on the B200 only, look here first.
- libcudf also contains an NVRTC/nvJitLink runtime-compilation path (`compute_column_jit`, symbols
  `nvrtcGetLTOIR`, `nvJitLink GetLinkedPtx`, `-arch=sm_{}`). `cudf_polars` 26.10.2 never calls
  `compute_column_jit`; its only switch is the Parquet option `use_jit_filter` (default `False`,
  `utils/config.py`, `CUDF_POLARS__...USE_JIT_FILTER`). So `cudf_polars` itself requests no runtime compilation
  by default; I did not audit every libcudf-internal caller of NVRTC, so preflight's SF1 smoke is the
  confirmation. If the JIT path were used, NVRTC/nvJitLink 12.9.86 do know sm_100, but the PTX they generate (CUDA 12.9) needs a driver
  >= 575.51.03 (NVIDIA: "Applications that compile device code to PTX will not work on older drivers").

### 13.4 Driver

- Lambda's public docs list the 1x B200 instance (180 GB, 26 vCPU, 360 GiB, 2.75 TiB root volume), say each
  on-demand instance runs Ubuntu 22.04 with Lambda Stack by default (Lambda Stack 24.04 is selectable through the
  API), and say Lambda Stack is tested on "the latest NVIDIA HGX B200 and H200 SXM GPUs". They name no driver version. Lambda's published installer (`install-lambda-stack.sh`) sets
  `DRIVER_SERIES=570` and picks the `-server-open` driver for Turing and newer, so a 570 series driver is
  plausible; this study's A100 host reported 580.105.08 (section 6), so 580.x is equally plausible. Record the
  real value (`campaign.json`, `setup_h100.sh` and `preflight.py` already read it from `nvidia-smi`).
- Floors (CUDA 12.9 release notes, driver table): 12.8 GA >= 570.26 (first with Blackwell), 12.9 GA >= 575.51.03,
  12.9 Update 1 >= 575.57.08. Minor-version compatibility lets CUDA 12.9 user space run precompiled cubins on a
  570 driver (12.x needs >= 525), which covers everything in 13.3 except the JIT path. RAPIDS 26.10 lists
  "Driver 535+" for CUDA 12; an R535 driver predates Blackwell support (first in 570.26), so the B200 floor is the binding one.
- `MIN_DRIVER = "525.60.13"` in `setup_h100.sh` and `preflight.py` is correct for the CUDA 12 wheels but is
  not a Blackwell gate; the B200 itself enforces 570.26. Not changed here.

### 13.5 Known Blackwell defects relevant to this pin set (open risk, silently-wrong class)

1. **CUDA 12.9 `ptxas` miscompilation of `thrust::exclusive_scan_by_key` on Blackwell**
   (NVIDIA/cccl#11167, **open**; cudf workaround NVIDIA/cudf#23973 merged 2026-09-04 as `86b2a5b`).
   Silent wrong results from the second 3136-item tile onward (224 threads x 14 items); the CCCL report says the
   sm_100 and sm_120 tuning policies share that tile size. Reproduced on **sm_120** (RTX PRO 4500, RTX 5090) only;
   no sm_100 reproduction or denial exists in the issues I read. Forcing PTX instead of SASS still fails.
   Reported manifestations: Parquet reads of files with more than about 3,100 string pages (corrupt string
   offsets) and `contiguous_split` with more than about 3,100 buffers (validity mask turned NULL).
   - Wheel: `libcudf-cu12 26.10.2` (git `2cd6c6c`) is 76 commits after `86b2a5b` and 0 behind, so it **contains**
     the two workarounds. The compiler is still 12.9, so other manifestations cannot be excluded.
   - Jar: its cudf revision is `ff5b362` (REL v26.08.00, 2026-08-05), diverged and 412 commits behind; the jar was
     built 2026-08-25, before the fix existed, so it **lacks** them. At that revision the two call sites use
     different iterator types than the ones named in the reports (`page_offset_output_iter`,
     `dst_offset_output_iterator`, `thrust::make_transform_iterator`), so the published reproducers do not map onto it
     directly. That is not evidence that the jar is safe.
   - Effect on this study: not shown. The risk is greatest where a Parquet read or a Spark shuffle/split handles
     thousands of pages or buffers (SF100 and SF300 `lineitem`/`orders`; Spark GPU shuffles with many
     partitions x columns). SF1 preflight is unlikely to reach that size. The existing safeguard is the
     study's cross-engine output agreement check; for the B200 campaign a disagreement on a GPU engine at a large scale should be
     treated as a possible compiler defect, not only as a query difference.
2. DECIMAL128 group-by sums wrong on Blackwell, including B200 (NVIDIA/cudf#23150, fixed by #23229 on
   `release/26.08`, 2026-07-21): **CUDA 13.0 builds only** (`__CUDACC_VER_MAJOR__ == 13 && MINOR < 3`); the CUDA 12.9
   builds pinned here are outside the affected configuration, and the study casts decimals to Float64 before
   aggregating.
3. cudf-spark#15872 and #15897 (PIVOT, accelerated columnar-to-row, `get_json_object` corruption on Blackwell): on
   RTX PRO sm_120 hosts with CUDA 13 tooling. #15872 was later attributed to item 1; #15897 is a JNI
   `get_json_object` retry race (cudf-spark-jni#5079), and no study query uses JSON.
4. Evidence that libcudf is exercised on B200 with CUDA 12.9 by its developers: cudf#23514 (2026-08, closed
   2026-10-21) benchmarks TPC-H Q1 expressions on "CUDA 12.9 ... NVIDIA B200 (sm_100)" with
   `-DCMAKE_CUDA_ARCHITECTURES=100-real`; cudf#23150 reproduces on B200 as well. These are performance and
   CUDA 13 items, not correctness failures of the pinned configuration.
5. Not Blackwell specific: cudf#23829 (open, shared-memory group-by hang for unusual column counts) is
   predicted to apply to H100 and B200 alike; it would surface as a query timeout/error in preflight.

### 13.6 Recommendations for A2 (no version changes)

1. The B200 campaign may proceed under A2's precondition: the pinned stack supports Blackwell at the binary
   level and per NVIDIA's platform pages. The remaining checks only a B200 can give are preflight's SF1 smoke of
   all engines and the recorded driver version.
2. Record in `campaign.json` for the B200: driver, `nvidia-smi` CUDA version, GPU name and compute capability, and
   the cubin set actually used (the section 13.2 listing is the reference).
3. Optionally, before round 1 and not as timed data, run an extra output-agreement pass of `polars_gpu` and `spark_gpu` against the CPU engines at the largest
   scale on the B200 (the first place thousands of Parquet pages or split buffers occur). A mismatch there on
   the B200 only (H100 clean) is the signature of item 13.5.1.
4. Treat any `cudaErrorNoKernelImageForDevice` or `cudaErrorUnsupportedPtxVersion` as an environment failure (wrong
   wheel or driver below 575.51.03 on a JIT path), not a result.

### 13.7 Commands run

```
# metadata and artifacts (x86_64 wheels; sha256 compared with the PyPI JSON digests; unpacked with unzip only)
curl -s https://pypi.org/pypi/<package>/<version>/json -o <package>_<version>.json
curl -sSL -o <wheel> <url from the JSON> ; echo "<sha256>  <wheel>" | shasum -a 256 -c -
unzip -q -o <wheel> -d x/<package>
# wheels: libcudf-cu12 26.10.2 b4a23fb5bd25071b81984c7e4b1b1ecd48497919556384168db2ccb6cf1da251,
#   libcudf-streaming-cu12 26.10.2 b1eb054878b5181298f1c427b3ff01b8a74076ab5db1be5c3f6423b0d4c3a40a,
#   nvidia-libnvcomp-cu12 5.3.0.16 535944be2e37ead56ad1ef80e9dd58a3126fc2ccf238ddffc9d1cb7a48f5300a,
#   pylibcudf-cu12 26.10.2 b6738113b71ebc5579f4292f2518d23e03ef5c933613faf2ba66e6546467d057,
#   nvidia-cuda-nvrtc-cu12 12.9.86 210cf05005a447e29214e9ce50851e83fc5f4358df8b453155d5e1918094dcb4,
#   nvidia-nvjitlink-cu12 12.9.86 e3f1171dbdc83c5932a45f0f4c99180a70de9bd2718c1ab77d14104f6d7147f9 (plus the other pinned wheels)
curl -sSL -o rapids-4-spark_2.12-26.08.1.jar https://repo.maven.apache.org/maven2/com/nvidia/rapids-4-spark_2.12/26.08.1/rapids-4-spark_2.12-26.08.1.jar
shasum -a 256 rapids-4-spark_2.12-26.08.1.jar     # 773a2fe5a6eeaf61a8ce3ec66b55e0ac9424ed6152d356a2524498c15373dd68
unzip -q -o rapids-4-spark_2.12-26.08.1.jar 'amd64/*' spark-rapids-jni-version-info.properties cudf-java-version-info.properties

# NVIDIA's cuobjdump (CUDA 12.9.82 redistributable, sha256 ee0de40e8c18068bfcc53e73510e7e7a1a80555205347940df67fa525d24452f
# from https://developer.download.nvidia.com/compute/cuda/redist/redistrib_12.9.2.json), run on the Mac in an x86_64
# container with no network and read-only mounts, because the Mac has no CUDA tools:
docker run --rm --platform linux/amd64 --network none \
  -v <dir with cuobjdump>:/tool:ro -v <dir with the .so>:/in:ro ubuntu:22.04 \
  /tool/cuobjdump --list-elf --list-ptx /in/<file>.so \
  | sed -E 's/.*\.(sm_[0-9]+[a-z]?)\.(cubin|ptx)$/\2 \1/' | sort | uniq -c
# cross-check without CUDA tools: a small Python parser of the fatbin container headers (magic 0xBA55ED50, 64-byte
# entry headers) agreed with cuobjdump on every architecture count for libcudf (wheel and jar), libcudf_streaming,
# libnvcomp and libcufilejni; it also found the 21 containers of entry kind 8 (probably LTO-IR) that cuobjdump does not list, and
# 3 containers in libprofilerjni.so that cuobjdump reports as no device code (so the scan alone over-reports; the
# table uses cuobjdump). The scanner was a throwaway script and is not kept; cuobjdump is the reproducible method.
# The claim "no device code" for the remaining 280 .so files rests on the scan finding no fatbin container in them.
strings -a libcudf.so | grep -E "(-arch|--gpu-architecture|compute_[0-9]|sm_[0-9]|lto_[0-9])" | sort | uniq -c
strings -a libnvrtc.so.12 libnvJitLink.so.12 | grep -oE "(sm|compute|lto)_1[0-9]{2}[af]?" | sort -u
# DT_NEEDED of the native libraries read with a small ELF reader; cudf_polars grep for architecture gating and JIT use:
grep -rnE 'compute_column|jit|use_jit_filter' --include='*.py' cudf_polars/

# vendor documentation and upstream records (fetched 2026-10-29)
curl -sSL https://docs.rapids.ai/platform-support/ ; https://docs.nvidia.com/cudf-spark/latest/getting-started/overview.html
curl -sSL https://docs.lambda.ai/public-cloud/on-demand/ ; https://lambda.ai/install-lambda-stack.sh   # read, not run
gh api repos/NVIDIA/cudf/pulls/23973 ; gh api repos/NVIDIA/cccl/issues/11167 ; gh api repos/NVIDIA/cudf/issues/23150
gh api repos/NVIDIA/cudf/pulls/23229 ; gh api repos/NVIDIA/cudf-spark/issues/15872 ; gh api repos/NVIDIA/cudf-spark/pulls/15904
gh api repos/NVIDIA/cudf/compare/86b2a5bad9cfca6d72b233968d3e2338d6fad6f9...2cd6c6c087c6f4ec566e20269bc8e7d87c522ed9   # wheel: ahead_by 76, behind_by 0
gh api repos/NVIDIA/cudf/compare/86b2a5bad9cfca6d72b233968d3e2338d6fad6f9...ff5b362d7c06ae5837fd7a7337e2ae20895f324d   # jar:   diverged, behind_by 412
```

### 13.8 Sources (Blackwell)

- RAPIDS platform support (26.10: CUDA 12.2-12.9, driver 535+, compute capability 70 ... Blackwell 100/120): https://docs.rapids.ai/platform-support/
- Spark RAPIDS overview ("built against CUDA 12.9 or CUDA 13.1 ... tested on V100, T4, A10, A100, L4, H100 and GB100"): https://docs.nvidia.com/cudf-spark/latest/getting-started/overview.html
- Blackwell Compatibility Guide: https://docs.nvidia.com/cuda/blackwell-compatibility-guide/index.html ; CUDA minor version compatibility: https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html ; CUDA toolkit release notes (driver table): https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html ; compute capabilities appendix: https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/compute-capabilities.html ; B200 = 10.0: https://developer.nvidia.com/cuda-gpus
- Lambda: https://docs.lambda.ai/public-cloud/on-demand/ (B200 1x: 180 GB, 26 vCPU, 360 GiB, 2.75 TiB; images) ; https://lambda.ai/install-lambda-stack.sh
- Defects: https://github.com/NVIDIA/cccl/issues/11167 ; https://github.com/NVIDIA/cudf/pull/23973 ; https://github.com/NVIDIA/cudf/issues/23150 ; https://github.com/NVIDIA/cudf/pull/23229 ; https://github.com/NVIDIA/cudf-spark/issues/15872 ; https://github.com/NVIDIA/cudf-spark/pull/15904 ; https://github.com/NVIDIA/cudf/issues/23514 ; https://github.com/NVIDIA/cudf/issues/23829
