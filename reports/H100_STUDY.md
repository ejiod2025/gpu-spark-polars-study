# H100 study results — October 26, 2026

Polars GPU has the lowest complete baseline score from SF10 through SF300. At SF300,
its score is **2.47× faster than the CPU Polars 2.0 control**. GPU Spark wins selected
queries, but has no aggregate win over that CPU control in either suite. Polars GPU
cannot claim a complete complex-suite score: two original query expressions are
unsupported in strict GPU mode.

The primary comparison matches Polars CPU and GPU at **1.44.2** with cuDF-Polars
**26.10.2**, on Python **3.11.13**. A separate CPU software-stack control uses
Polars **2.0.0** and Python **3.10.12**, matching the earlier A100 software record.
Python and Polars change together, so this control does not isolate a Polars-version effect.

All runs use one H100 80 GB HBM3, 26 Xeon Platinum 8480+ vCPUs, approximately 221 GiB
RAM, and the same H100 Parquet files. Spark is 3.5.9 with `local[26]`, a 96 GiB
driver heap, and the original configuration; GPU Spark uses plugin 26.08.1.
The frozen engine source is commit `c4c934cf732b4394e2bcb0547bb642ee2a1ae5d4`.

## Largest measured scale: SF300

Scores are seconds, **summing six per-query medians** from three measured trials
after one warmup. They exclude startup and are not measured once-through suite runtimes.

| Engine / software stack | Baseline score | Complex score |
|---|---:|---:|
| Polars CPU 1.44.2, primary matched stack | 44.512 | 184.446 |
| Polars CPU 2.0.0, separate CPU control | 31.540 | 70.389 |
| Polars GPU 1.44.2 | **12.774** | n/a — 4/6 supported cases |
| CPU Spark | 146.428 | 391.143 |
| GPU Spark | 45.528 | 143.039 |

The CPU control materially changes the complex-suite interpretation. GPU Spark
beats the compatible CPU 1.44.2 score by 1.29×, largely because of the window query.
GPU Spark's complex score is **2.03× the CPU 2.0 control score**.
Do not present the primary comparison as GPU Spark beating CPU Polars 2.0 overall.

## Polars GPU baseline scaling

| Scale | CPU Polars 2.0 control (s) | Polars GPU 1.44.2 (s) | CPU control / GPU |
|---|---:|---:|---:|
| SF1 | 0.171 | 0.450 | 0.38× |
| SF10 | 1.109 | 0.845 | 1.31× |
| SF30 | 3.209 | 1.585 | 2.02× |
| SF100 | 10.230 | 4.064 | 2.52× |
| SF300 | 31.540 | 12.774 | 2.47× |

The observed aggregate winner changes between SF1 and SF10. This is a bracket
between measured observations, not a fitted threshold. At SF300, Polars GPU wins
all six individual baseline queries against both CPU stacks.

## Where GPU Spark beats the CPU 2.0 control

These are the only such wins at SF300 among the twelve selected queries.

| Query | CPU Polars 2.0 (s) | GPU Spark (s) | Speedup |
|---|---:|---:|---:|
| Baseline Q1 aggregation | 10.016 | 4.451 | **2.25×** |
| Complex Q13 outer join and nested aggregation | 9.172 | 5.206 | **1.76×** |
| Complex windows | 17.258 | 3.614 | **4.78×** |

Polars GPU is faster on all four supported SF300 complex cases: the six-table
join, correlated semi/anti query, exact distinct counts, and fact self-join. Its
original Q13 regex `(?s)special.*requests` and grouped window expression are
rejected during GPU translation. CPU fallback is disabled and tested; no CPU
timing is labelled GPU. This does not establish that every regex, outer join,
or window function is unsupported. No partial four-query sum is used as a six-query score.

## Validation and limits

- **936 successful formal outputs** agree within the original relative `1e-8` /
  absolute `1e-6` tolerances, with exact integer pairs: 480 baseline, 264 complex,
  120 CPU baseline-control, and 72 CPU complex-control outputs. Warmups are included.
- Formal evidence includes 192 Spark GPU final plans, 168 Polars GPU plan
  artifacts (42 per-query lowerings reused across session actions), and 336
  successful complex cleanup barriers. The separate SF1 smoke has
  92 agreeing outputs. The six formal unsupported GPU warmups remain explicit.
- All **1,776 Parquet files, 157,967,834,615 bytes** across five scales passed
  post-run re-hashing against the same prevalidated manifests. SF30 has 63
  lineitem files versus A100's 60; logical-content fingerprints match, physical
  layout differs. The post-check's missing small-scale external-reference warnings
  are covered by the earlier dbgen checks and unchanged file hashes.
- **200 tests pass; one local fixture-dependent test is skipped.** Fresh H100
  smoke runs supply actual engine bring-up evidence. The untimed post-check's
  first packaging attempt lacked a copied module; its traceback is retained,
  the dependency was added, and the full check then passed.

Complex actions use verified GC outside query timers; cleanup remains in process
wall time. Filesystem preheating is attempted, without guaranteed residency, and
application DataFrame caching is disabled. GPU transfer inside Polars `collect()`
is timed; conversion to Python dictionaries is outside. Polars GPU lowerings are
explained once per query/session outside the timer and cached in action artifacts;
they are not per-action kernel traces. Plan nodes establish operator selection,
not runtime fractions. Spark optimizes away the correlated
query's anti branch, so this is not a demonstrated nonempty GPU anti-join workload.

These are selected TPC-H-derived and custom queries, not complete or certified
TPC-H results. Formal correctness is cross-engine agreement; no independent
full-scale SQL oracle is claimed. Three trials are descriptive, and one host does
not establish distributed scaling, equal-cost performance, or statistical significance.
A100/H100 host, CPU, software, and file-layout differences prevent attributing
cross-host changes solely to the GPU model.

## Reports and evidence

- [Four-engine baseline](h100-baseline-20261026/CROSSOVER_REPORT.md) ·
  [chart](h100-baseline-20261026/crossover.png)
- [Four-engine complex suite](h100-complex-20261026/COMPLEX_REPORT.md) ·
  [SF300 chart](h100-complex-20261026/complex-sf300.png)
- [CPU baseline control](h100-polars2-baseline-control-20261026/CPU_STACK_CONTROL.md)
- [CPU complex control](h100-polars2-complex-control-20261026/CPU_STACK_CONTROL.md)
- [Curated evidence](../evidence-h100/20261026/README.md) ·
  [runbook](../docs/H100_RUNBOOK.md) · [compatibility](../docs/POLARS_GPU_COMPAT.md)
