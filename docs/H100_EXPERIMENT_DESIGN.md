# H100 four-engine experiment — locked design (pre-registration)

Status: **LOCKED 2026-10-29, before any timed H100 data exists.** Changes after this point are
amendments: append them to §11 with date and reason; never edit the sections above silently.
This document supersedes earlier exploratory campaign designs (none of their data is used).

## 1. Research questions

On one NVIDIA H100 80 GB SXM5 host, with identical TPC-H-derived inputs (DuckDB 1.5.6 `dbgen`;
selected adapted TPC-H queries plus Q9/Q13-derived and custom queries; **not** a complete or certified
TPC-H run):

- **RQ1** How much does the GPU accelerate each framework? (Polars GPU vs Polars CPU; Spark GPU vs Spark CPU)
- **RQ2** Which engine is fastest per query, suite and scale; does any GPU engine beat Polars CPU?
- **RQ3** At which scale (SF1→SF300), if any, does a GPU engine overtake the best CPU engine?
- **RQ4** Which queries cannot run on each GPU engine (coverage), and why?
- **RQ5 (side study)** How much does the Polars version (1.44.2 vs 2.0.0, CPU) change Polars CPU timings,
  to bridge to the earlier A100 study (Polars 2.0.0)?

## 2. Arms (engines)

| Id | Engine | Version / settings |
|---|---|---|
| `polars` | Polars CPU, streaming | Polars 1.44.2, Python 3.11, 26 threads |
| `polars_gpu` | Polars + cuDF GPU engine | cudf-polars-cu12 26.10.2, `raise_on_fail=True`, executor `streaming` |
| `spark_cpu` | PySpark `local[26]` | Spark 3.5.9, Java 17, 96 GiB driver, 120 shuffle partitions, AQE on |
| `spark_gpu` | PySpark + RAPIDS Accelerator | rapids-4-spark_2.12 26.08.1 (sha256 recorded), profile `a100-parity` |

Spark confs are byte-identical to the A100 study (`a100-parity`). No arm is tuned during the campaign.

## 3. Workload and inputs

- Baseline suite: q01, q03, q06, q10, q12, q14 at SF1, 10, 30, 100, 300; query cleanup `none` (A100 lifecycle).
- Complex suite (v2): cx_q09, cx_q13, cx_semi_anti, cx_window, cx_distinct, cx_self_join at SF30, 100, 300;
  query cleanup `verified_gc` after every action, outside timers.
- Inputs regenerated on the host with the A100 generation parameters (SF1–30: `generate_data.py --threads 30
  --memory-limit 64GB`; SF100: `generate_large_data.py` 20 children/12 workers; SF300: 60 children/16 workers).
- **Input gate (before round 1):** `verify_generated_inputs.py` on SF1–SF300 must report `RESULT: OK`
  (row counts, schemas, parameters, order-independent content fingerprints vs A100). Byte identity is not
  required (shown to be scheduling-dependent).
- Float64 normalization of money/quantity columns (`FLOAT_COLUMNS`) in every engine.

## 4. Experimental unit and structure

- **Session** = one engine process for one (suite, scale): preheat input files (untimed) → one warmup per
  query (untimed) → `R=3` measured trials per query in seeded shuffled order. Timer covers query construction,
  optimization, Parquet read, execution and result collection (device→host included for GPU engines).
- **Round** = every engine × every (suite, scale) once: 4 × 8 = 32 sessions.
- **Campaign** = 4 rounds. Round = block for analysis.

## 5. Order design (balanced Latin square, Williams, n = 4)

Seed **20261029**. `random.Random(20261029).shuffle(["polars","polars_gpu","spark_cpu","spark_gpu"])`
assigns letters: **A = spark_cpu, B = polars_gpu, C = polars, D = spark_gpu**.

| Round | Sequence | Engine order within every (suite, scale) |
|---|---|---|
| 1 | A B D C | spark_cpu → polars_gpu → spark_gpu → polars |
| 2 | B C A D | polars_gpu → polars → spark_cpu → spark_gpu |
| 3 | C D B A | polars → spark_gpu → polars_gpu → spark_cpu |
| 4 | D A C B | spark_gpu → spark_cpu → polars → polars_gpu |

Each engine occupies each position once and each ordered pair of engines is adjacent exactly once
(first-order carryover balanced). Suite order alternates: rounds 1 and 3 run baseline first; rounds 2 and 4
run complex first. Scales run in ascending order within a suite. Within-session query order uses the
worker's seed (unchanged).

## 6. Controls

- No other workload on the host during rounds (data generation finished and verified beforehand).
- Before each session: GPU has no compute processes; files preheated; previous session's processes exited.
- Telemetry at 200 ms: GPU utilization, memory, power, temperature and SM/memory clocks; host CPU, memory,
  swap and disk at 1 s. Clocks are not locked; they are recorded.
- `--cpus 26` for all engines; same data root and manifest for all.
- After every round: results copied off-host (rsync to the analysis workstation) and checksummed.

## 7. Integrity gates (pass/fail; never based on timings)

Per round, all must hold or the round is repeated once (and the repeat is reported):
1. Every session exits; no engine timeout.
2. Output agreement with `polars` per query: rel 1e-8, abs 1e-6, integers exact.
3. `polars_gpu`: strict GPU session guard passed; only the pre-declared unsupported queries fail (§8).
4. `spark_gpu`: final plans contain GPU scan + GPU compute operators and no CPU core operators
   (complex rule) / GPU scan + aggregate (baseline rule).
5. Complex suite: every `verified_gc` cleanup barrier passed.
A post-round-1 review checks integrity only. No stopping or modification based on observed speeds.

## 8. Pre-declared GPU coverage gaps

`polars_gpu` is expected to reject **cx_q13** (inline regex flag `(?s)` unsupported) and **cx_window**
(`cum_count().over(...)` unsupported) under `raise_on_fail=True` (observed in preflight 2026-10-26).
They are reported as "not supported on GPU" (RQ4) and excluded from ratios involving `polars_gpu`.
Queries are never rewritten for the GPU inside this campaign.

## 9. Statistical analysis plan

- Per (round, suite, scale, query, engine): **median of the 3 measured trials** → `t`.
- **Primary contrasts** (ratio = slower-is-numerator speedup, `t_ref / t_engine`):
  - C1 `polars` / `polars_gpu`  (RQ1, Polars GPU speedup)
  - C2 `spark_cpu` / `spark_gpu` (RQ1, Spark GPU speedup)
  - C3 `spark_gpu` / `polars_gpu` (RQ2, GPU engine head-to-head)
  - C4 `polars` / best GPU engine per query (RQ2/RQ3, does GPU beat the best CPU engine)
- Per query: log-ratio per round; estimate = geometric mean over the 4 rounds; **95% CI = t-interval on
  mean log-ratio, df = 3**, back-transformed; also report min–max across rounds.
- Per (suite, scale): geometric mean of per-query ratios over queries supported by both engines, computed
  per round, then the same t-interval across rounds (round is the block). Legacy sum-of-medians totals are
  reported alongside for comparability with the A100 study, never mixed with geometric means.
- Variability: coefficient of variation within session (trials) and between rounds, per engine.
- Order/carryover check: mean log-time by position (1–4) and by predecessor engine; reported descriptively.
- RQ3 crossover: per suite, the scale at which C4's CI lies entirely above 1 (GPU faster), if any.
- Multiplicity: four primary contrasts × suites × scales are reported as estimates with CIs; no hypothesis
  tests or p-values are used for claims. With 4 rounds, intervals are expected to be wide; this is stated.
- Secondary (descriptive): peak GPU memory, process wall time and startup, cleanup cost, energy proxy
  (mean power × time), and an A100→H100 comparison for GPU engines (labelled non-equivalent hosts; CPU
  engines not compared across hosts: 30 vs 26 vCPU).

## 10. Side study (RQ5): Polars version bridge

After the 4 main rounds, on the same host: `polars` 1.44.2 (main env) vs `polars` 2.0.0 (`.venv-a100compat`,
Python 3.10), both suites, all scales, 4 rounds with orders AB, BA, BA, AB. Same session protocol.
Estimate: geometric-mean ratio 2.0.0 / 1.44.2 per suite and scale with t-interval (df = 3).

## 11. Amendments

### A1 — 2026-10-29, before any timed H100 data (gate vs coverage outcomes)

Reason: as written, §7 gate 3 would fail a round on a deterministic GPU resource exhaustion (e.g. SF300
out of device memory), stopping the campaign after a repeat, although RQ4 treats such failures as results.

Change: query and session failures are classified, and only **integrity failures** fail a round.
- **Coverage outcome (reported under RQ4, not a gate failure):** a GPU engine (`polars_gpu`, `spark_gpu`)
  rejects or cannot complete a query/session because of (a) an unsupported operation (e.g. cudf-polars
  `NotImplementedError` under `raise_on_fail=True`), or (b) resource exhaustion (device/host out-of-memory,
  RMM allocation failure, Spark GPU OOM/retry exhaustion). The failure, its error class and the scale are
  recorded; the affected cells are excluded from contrasts involving that engine and shown in the coverage
  matrix (F4). The same rule applies to a CPU engine failing for host resource exhaustion.
- **Integrity failure (fails the round, §7 repeat-once-then-stop rule unchanged):** output disagreement,
  silent CPU fallback or CPU core operators in a GPU plan, a failed `verified_gc` barrier, an engine timeout,
  an unclassified error, or a crash not attributable to (a)/(b).
- **Consistency:** a coverage outcome observed in some rounds but not others for the same (engine, suite,
  scale, query) is reported as *unstable coverage*; those cells are excluded from contrasts in all rounds.
- §8's pre-declared gaps (cx_q13, cx_window) are coverage outcomes of type (a).
- Error classification rules (message/type patterns) are implemented in `preflight.py` and listed in its
  gate report; any error not matching a rule is an integrity failure.

### A2 — 2026-10-29, before any timed data (additional host: B200)

Reason: extend to a Blackwell-generation GPU. GB300/H200 are not offered by the provider (Lambda);
`gpu_1x_b200_sxm6` (B200 180 GB, 26 vCPU x86_64, 360 GiB RAM) is the nearest available and shares the
H100 host's vCPU count.

Change:
- Each host runs its **own complete campaign** under §2–§10 unchanged (same seed, Latin square, gates,
  side study), in its own campaign root, with host identity (instance type, GPU, driver, CPU model,
  RAM) recorded in `campaign.json`. Data are never pooled across hosts; every primary estimate (§9) is
  per host.
- The H100 campaign remains the primary analysis. The B200 campaign is a replication on newer hardware.
- Cross-host comparison (new §9 secondary analysis): per (suite, scale, query, engine) ratio of per-host
  geometric-mean round medians, B200/H100, with intervals from the per-host round distributions
  (Welch-type on log scale); descriptive only. CPU engines are compared across hosts only with the caveat
  that CPU model and RAM may differ (recorded); GPU-engine differences confound GPU, CPU, memory and driver.
- Preconditions: the B200 software stack must pass the same preflight (with `--allow-any-gpu`) and input
  verification before its round 1. If the pinned stack does not support Blackwell, the B200 campaign is
  not run (no version changes inside this design); that outcome is reported.
- An ARM host (GH200) is out of scope for now; adding it would need a further amendment.

### A3 — 2026-10-30, before any timed data (GPU-generation ladder: A100 and GH200 hosts)

Reason: place the H100 result on a hardware ladder (Ampere → Hopper → Grace-Hopper → Blackwell) and supply
the four-engine result missing from the earlier A100 study.

Change:
- Two further hosts, each running its **own complete campaign** exactly as A2 specifies (same seed, Latin
  square, gates, side study; never pooled): `gpu_1x_a100_sxm4` (A100 40 GB SXM4, 30 vCPU x86_64, 200 GiB)
  and `gpu_1x_gh200` (GH200 96 GB, 64 vCPU Arm Neoverse/Grace, 432 GiB).
- All hosts use `--cpus 26` (§2/§6 unchanged), so engine thread counts are identical across the ladder;
  hosts with more vCPUs leave them idle. This favours cross-host comparability over per-host peak CPU
  performance, and is stated as a limitation.
- GH200 (aarch64): the same package *versions* are used where aarch64 builds exist; the platform-specific
  artifacts (wheels, RAPIDS jar classifier, JDK build) are recorded with their sha256. If any pinned
  version has no aarch64 build, the GH200 campaign is not run and this is reported (no version changes).
- Expected coverage outcomes (A1) are more likely on the A100 (40 GB) at SF300; they are results, not
  failures.
- The cross-host secondary analysis (A2) extends to all hosts; the H100 remains primary.

### A4 — 2026-10-30, before any B200 data (known Blackwell compiler defect)

Reason: the B200 precondition check (docs/POLARS_GPU_COMPAT.md §13) found the pinned stack carries native
sm_100 code (precondition met), but identified an open CUDA 12.9 `ptxas` defect on Blackwell
(NVIDIA/cccl#11167: `thrust::exclusive_scan_by_key` corrupts results beyond the first tile; reproduced on
sm_120, not established either way on sm_100). libcudf 26.10.2 carries cudf's workaround (NVIDIA/cudf#23973);
the RAPIDS Accelerator 26.08.1 jar predates it.

Change (no change to gates or analysis):
- On the B200, any GPU-engine output disagreement remains an integrity failure (A1, §7) and is not
  re-classified. Its diagnosis records whether it is consistent with this defect (engine, operator,
  scale); the defect is named as a candidate cause only if the evidence fits, never as an excuse.
- If a B200 round fails integrity twice (campaign stops), the B200 replication is reported as not completed,
  with the evidence; no version change is made within this design.
- The real B200 driver version is recorded in `campaign.json` (Lambda does not document it).

### A5 — 2026-11-11, clarification (no change to the design, gates or analysis)

Correction of wording, appended under the lock rule instead of editing the locked text above.

- The status line ("LOCKED 2026-10-29, before any timed H100 data exists"), the headings of A1–A3
  ("before any timed H100 data", "before any timed data") and the paper text derived from them were
  too absolute. They are true only of the campaigns run under this design (campaign 2026-10-30 on the
  H100, GH200, A100 and B200 hosts).
- Before the lock, exploratory H100 campaigns ran from 2026-10-26 to 2026-10-28 under an earlier design
  that was not pre-registered (history on branch `H100_main_dev`, including a completed run, an aborted
  cohort, a recovery run and an unpublished manuscript). Their results were seen before the lock and
  informed this design; for example, the two pre-declared Polars GPU coverage gaps (§8) were found in
  the 2026-10-26 preflight. The note "none of their data is used" above remains true: every reported
  number comes from the campaigns run under this design.
- Consequence for interpretation: the pre-registration guards against choosing the analysis after
  seeing these campaigns' data, not against knowledge of the pilot.
- AI tools: this document and its amendments were drafted with an AI assistant (Anthropic's Claude via
  Claude Code; commits carry a `Co-Authored-By` trailer). Earlier exploratory designs were drafted with
  OpenAI Codex. The author reviewed and approved the design and is responsible for it.
