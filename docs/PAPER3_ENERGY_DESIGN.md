# Paper 3 — energy and cost per query, CPU versus GPU dataframe engines — pre-registered design

Status: **LOCKED 2026-11-16 (UTC)** — annotated tag `paper3-design-lock` on the lock commit; external timestamp: Zenodo
DOI [10.5281/zenodo.24284712](https://doi.org/10.5281/zenodo.24284712) (this file). The author approved the lock, including the
lead's pre-lock changes listed in §12, on 2026-11-16. One condition follows the lock, before the first session of its host:
B200 admission (§3.1). Drafted 2026-11-11 for Daniel Ejiofor (Boston University, ejiod@bu.edu), revised after probes P1-P4
(§7.2) and the pre-lock probes of 2026-11-11 to 2026-11-16 (§7.2; what changed: §12). From the lock on, sections 1-11 are
never edited; changes are dated, append-only amendments in §12 (rule of `docs/H100_EXPERIMENT_DESIGN.md`, extended in
§7.4). All author decisions are answered (§11).

Aim (author's words): rigorous, reproducible evaluation methods and tools that help U.S. organizations choose cost- and
energy-efficient CPU or GPU engines for analytical data processing. Paper 3 contributes a measurement protocol and
break-even scales, not a vendor ranking.

## 1. Research questions
Same TPC-H-derived workload, engines and integrity gates as Paper 1 (not a certified TPC-H run; §8.3).

| Id | Question | Type |
|---|---|---|
| RQ1 | Energy per query of a GPU engine versus its CPU counterpart (Polars GPU/CPU, Spark GPU/CPU): scope-tagged figures, plus a node-level direction under assumption N1 (§2.5); the GH200 module-scope comparison is RQ6 | confirmatory |
| RQ2 | Cost per query (on-demand price x time), same contrasts, three CPU baselines (§2.6) | confirmatory |
| RQ3 | Break-even scale: smallest scale where a GPU engine is cheaper than the best CPU engine, per suite (§5.3); for energy only a lower bound under N1 | confirmatory |
| RQ4 | GPU generation: GPU-board energy (S1) and cost per query of the GPU engines on A100, H100, GH200, B200 (host-confounded, as Paper 1 A2/A3); the GH200 counter is module scope (S1-module), so its energy is not a like-for-like board figure (§2.5); B200 is conditional (§3.1) | confirmatory estimates, descriptive |
| RQ5 | CPU platform and idle-GPU tax: time and cost of the CPU engines on the CPU-only host versus the GPU hosts; GPU-board energy burned idle during CPU-engine runs on the x86 GPU hosts (§2.6, X5/X6; on GH200 the idle GPU is inside the module counter, not separable) | confirmatory estimates |
| RQ6 | GH200 only: energy per query of a GPU engine versus its CPU counterpart on the same host at module scope (S1-module: Grace CPU + GPU + memory; whole-module energy, not node energy; §2.5, X7) | secondary, pre-specified estimates (not in the confirmatory set of §5.4) |
| EX1-3 | Exploratory: re-analysis of the archived campaign-20261030 GPU telemetry (EX1, §6); model-based host-CPU energy for GPU-engine runs where no counter exists, labelled MODEL (EX2, §2.4); cost from archived Paper 1 timings x prices, power versus utilisation, per-query heterogeneity (EX3, §6.2) | exploratory |

"Confirmatory" means contrast, metric, baseline, scale grid and decision rule are fixed here. It is not a hypothesis test
(§5.4).

## 2. Measurement definitions
### 2.1 Time and energy windows
- Timed region and `perf_counter` timer exactly as Paper 1. Warmup, preheat, query order, cleanup (`none` baseline,
  `verified_gc` complex) unchanged.
- Around each timed run the worker stores tight UTC and `CLOCK_MONOTONIC` stamps (`timed_start/end_*`, just outside the
  timer) and reads the NVML counter (§2.3) just outside them; read latency and overhang are stored per run and summarised in
  the QA table (not a gate). CPU energy (§2.4) comes from the runner's 200 ms counter series (`power.csv`, same monotonic
  clock), interpolated linearly at the stamps. No cross-process clock alignment is needed.
- **Window rule.** A measured trial is a window of back-to-back runs of one query; runs continue until the summed timed time
  of the window's runs reaches 10 s or 50 runs have been made, whichever comes first (`n` = runs made; `n = 1` when the
  first run takes >= 10 s). Window energy = sum of per-run counter differences (gaps, cleanup and plan-explain excluded);
  per-run energy and time = window value / `n`. 3 trials per query; the median is the round's value (as Paper 1). `n`
  depends only on elapsed time, never on energy, so the trials of one query may differ in `n`. The warm-up time `t_hat` is
  still recorded, for QA only; it no longer sets `n`. (Pre-lock correction 2026-11-15, §12: the first draft set `n =
  clamp(ceil(10 s / t_hat), 1, 50)` from the warm-up time, but the warm-up is the cold first run, so it overstates the steady
  per-run time and left windows short of 10 s.)
- **Resolution.** The NVML counter updates every ~100 ms (P1) and the RAPL series every 200 ms, so each end of a run is
  uncertain by up to one update period: a 10 s window bounds this at 2% (NVML) or 4% (RAPL) for one run, and for `n` short
  runs the errors are independent in expectation and add like sqrt(n), not n (P6, below: the H100's errors have mixed signs,
  consistent with this; the A100's are one-sided, all 30 windows positive at every length, about 4-12 J per run, so on A100
  they do not average out like sqrt(n)). P6 measured this before the lock on synthetic pulses of known length: the per-run
  counter error against the 20 ms integral of the instantaneous-power field that has the counter's scope
  (`power.draw.instant` on H100 and A100; `module.power.draw.instant` on GH200, §2.3), for one isolated pulse and for
  windows of `n = ceil(10 s / length)` runs of the known length, capped at 50 (summed over the runs, gaps excluded). For
  runs of fixed length that is what the window rule above gives, up to timing jitter at exact multiples of the length (the
  P6 windows of 0.1 s pulses stop at the cap of 50 runs, 5 s, as the rule does), so the P6-derived `t_min` applies to the
  rule as worded now.
- **`t_min` is per host** (author decision 2026-11-15, §11 item 8.1). Rule: the smallest per-run length whose median
  |error| is <= 2% and every longer length also is, taken from the windows-mode result (the mode the study uses), with the
  locked default of 1 s as a floor: `t_min(host) = max(1 s, measured)`. (The first draft worded the rule as "the shortest
  per-run time at which the median error is <= 2%"; the "every longer length also" clause and |error| are the tool's
  definition, `tools/paper3_probes/p3_gpu_probes.py`, and give the same `t_min` on every valid result below.) A cell whose
  median trial has a summed window below 10 s or a per-run time below its host's `t_min` is "below measurement resolution",
  never a number (a summed window below 10 s arises only when the cap of 50 runs is reached, an average run under 0.2 s,
  which is also below any `t_min` >= 1 s). The limit is not tuned to the observed values.

  | Host (reference field) | median absolute error in %, windows mode, per-run length 0.1 / 0.25 / 0.5 / 1 / 2 / 5 s | measured `t_min`, windows / isolated | `t_min` used |
  |---|---|---|---|
  | H100 SXM5 (`power.draw.instant`) | 9.3 / 5.5 / 1.1 / 0.78 / 0.56 / 0.57 | 0.5 s / 0.5 s | **1 s** (floor) |
  | A100 SXM4 (`power.draw.instant`) | 34 / 18 / 11 / 5.3 / 1.9 / 0.69 | 2.0 s / 5.0 s | **2 s** |
  | GH200, first run (`power.draw.instant`, GPU-only: wrong scope, invalid) | 46 / 26 / 19 / 16 / 15 / 14.7 | none / none | superseded |
  | GH200, rerun 2026-11-15 (`module.power.draw.instant`) | 11.0 / 5.95 / 2.93 / 0.41 / 0.45 / 0.47 | 1.0 s / 1.0 s | **1 s** (equals the floor) |
  | B200 (`power.draw.instant`) | P6 lost 2026-11-16: connection lost, only the 20 ms stream was collected | none | **pending** (valid P6 rerun, before any B200 session) |
  | C-GCP, P6-CPU 2026-11-16 (S2-pinned; direct `energy_uj` reads as reference) | 21 / 9.8 / 4.9 / 2.6 / 1.15 / 0.40 | 2.0 s / 2.0 s | **2 s** |

  Evidence: `docs/paper3-evidence/probes/lambda-h100-20261112T035018Z-p6.json`, `lambda-a100-20261112T035051Z-p6.json`,
  `lambda-gh200-20261115T171353Z-p6.json` (first GH200 run) and `lambda-gh200-20261115T213713Z-p6.json` (GH200 rerun); 5
  windows per length and 8 isolated pulses per length. The H100, A100 and GH200-rerun errors were recomputed independently
  from the raw counter reads and the 20 ms CSV and match the recorded values exactly; the first GH200 run's medians were
  recomputed from its records. The A100's 1.91% at 2 s is close to the 2% limit and its 1.0 s windows give 5.3%; the H100's
  isolated 0.5 s and 1 s values are 1.97% and 1.91%. All are recorded as measured; no limit is moved.
- **GH200 P6: first run invalid, rerun valid.** The first GH200 P6 was scored against GPU-only `power.draw.instant`, which
  does not have the counter's scope (§2.3): the gap never fell below 14.7% at any length (14.7% for 5 s windows, 14.8% for
  10 s isolated pulses), a scope offset and not a resolution effect, so it yields no `t_min` and is superseded. The rerun
  (2026-11-15 21:43-21:59 UTC, a second GH200 instance, driver 570.148.08, reference `module.power.draw.instant` via
  `--p6-field` in `tools/paper3_probes/p3_gpu_probes.py`) gives windows-mode median absolute errors of 11.0 / 5.95 / 2.93 /
  0.41 / 0.45 / 0.47% at 0.1 / 0.25 / 0.5 / 1 / 2 / 5 s and isolated errors of 31.5 / 20.2 / 5.5 / 1.4 / 1.1 / 0.35 / 0.17%
  at 0.1 / 0.25 / 0.5 / 1 / 2 / 5 / 10 s. The measured `t_min` is 1.0 s in both modes, so `t_min(GH200) = max(1 s, 1.0 s) =
  1 s`. Windows-mode errors are positive in all 5 windows at 0.1, 0.25 and 0.5 s (one-sided below 1 s) and of mixed sign
  from 1 s (3, 4 and 3 of 5 positive at 1, 2 and 5 s). Provenance: the `power_telemetry.py` copied to the host had
  uncommitted edits against HEAD 0cbb2a7, whose exact diff is `lambda-gh200-20261115T213713Z-power_telemetry-vs-HEAD.diff`:
  a negative-window-energy guard in `energy_between_uj`, a new S2-all zone helper (`all_package_dram_zones`) and a docstring
  change. The GPU steps do not call `energy_between_uj` (not called by `p3_gpu_probes.py` or `probe_power.py`; the counter
  is read through `GpuEnergyReader`).
- **`t_min(C-GCP)` comes from a RAPL probe, P6-CPU** (lead's decision 2026-11-15; the author may object before the lock).
  The P6 above is an NVML test and says nothing about the RAPL path. On C-GCP `t_min` is fixed by a RAPL resolution probe:
  per-run S2-pinned energy from the campaign's own path (the 200 ms `power.csv` series interpolated linearly at the run's
  stamps) against direct `energy_uj` reads at the same stamps, which are the reference, on synthetic CPU pulses of known
  length on the 26-vCPU pin, isolated and in windows built by the window rule, with the same 2% rule as above on the
  windows-mode result: `t_min(C-GCP) = max(1 s, measured)`. **Measured before the lock, 2026-11-16**
  (`docs/paper3-evidence/probes/gcp-c3-highmem-192-metal-20261116T000855Z-p6-cpu.json` with `-power.csv`,
  `-power-sources.json`, `.log`, `-run.log` and `-SHA256SUMS.txt`): `c3-highmem-192-metal` in us-east4-c (the shape of the
  design's us-east4-a, where the create was refused), code = repository HEAD 82d189f via `git archive`; pin CPUs 0-12,96-108
  (socket 0, NUMA node 0, `MPOL_BIND` verified), zones package-0 and package-0/dram; the 200 ms series had a median spacing
  of 200.0 ms and a maximum of 211 ms, 4812 polls per series, no overrun, sampler overhead 0.21% of one core; one counter
  wrap on package-0 was unwrapped and no window was invalid. Windows-mode median absolute error 20.7 / 9.8 / 4.9 / 2.6 /
  1.15 / 0.40% at 0.1 / 0.25 / 0.5 / 1 / 2 / 5 s; isolated 23.0 / 6.9 / 5.0 / 2.1 / 1.2 / 0.33 / 0.30% at 0.1 / 0.25 / 0.5 /
  1 / 2 / 5 / 10 s (the reference and the estimate were recomputed independently from the stored `energy_uj` reads and
  `power.csv` and match the recorded values exactly). The measured `t_min` is 2.0 s in both modes, so `t_min(C-GCP) = max(1
  s, 2.0 s) = 2 s`, the value for `paper3_hosts.json`. The errors are one-sided: negative in every window and every pulse at
  every length, i.e. the 200 ms-interpolated estimate is below the direct reads, by a median of 7.0-8.9 J per run in windows
  mode (spin load of about 335-345 W for package-0 plus its DRAM), so they do not average out like sqrt(n), as on the A100
  (there with the opposite sign). The team lead reads this as the 200 ms sampling smearing the power step at each run edge;
  that was not tested here. The earlier change of this probe into a pre-first-session condition (§12) is superseded:
  capacity appeared and it ran before the lock.
- **The isolated (single-run) `t_min` is never binding.** A window of one run (`n = 1`) arises only when the first run takes
  >= 10 s (otherwise the window continues), and a summed window below 10 s arises only when the cap of 50 runs is reached;
  so every valid one-run window has a per-run time >= 10 s, where the isolated P6 error is 0.34% (H100) and 0.22% (A100).
  The A100's isolated 5 s is thus never reached by a valid cell; the windows-mode value above is the one used.

### 2.2 Idle baseline, both ways
Each session is bracketed by two 30 s idle windows (engine ready, no query running), each preceded by >= 60 s without load
(also a thermal reset). `P_idle` per scope = mean of the two windows' mean power. Every energy value is reported both ways:
**total** `E` = counter difference over the timed runs (**primary**: what the machine draws); **above-idle** `E_above = E -
P_idle * t`, negative values reported as they are (not clamped). For a CPU engine on an x86 GPU host (S1 idle GPU)
above-idle is zero by construction and not reported; on GH200 the counter is module scope and includes the Grace CPU
(§2.3), so above-idle is reported there for CPU engines too. The runner's own pre-session bare idle baseline
(`run_idle_baseline`, phase `idle-baseline`) is off in Paper 3: `P_idle` comes only from the two engine-ready windows above,
and no time is budgeted for it.

**Measured idle floors (P5, pre-lock, synthetic load; QA values, not outcomes).** The primary definition above is
unchanged. Counter mean power over 60 s windows, two per state; "bare" = no CUDA context (nothing has touched the GPU),
"context open" = a CUDA context held by the probe with no kernel running (what a GPU engine leaves while "ready"); the
`nvidia-smi` poller is the 200 ms `gpu.csv` stream. Evidence: `docs/paper3-evidence/probes/lambda-<class>-<stamp>-p5-gpu.json`
and `gcp-c3-highmem-192-metal-20261111T203231Z-idle{1,2}-*`.

| Host (counter scope) | bare idle W | context open W | poller minus bare W | pair difference, bare / poller | 26-process CPU burn, context open W |
|---|---|---|---|---|---|
| H100 (board) | 76.3, 75.5 | 142.4, 145.9 | +0.72 | 1.1% / 2.2% | 149.1 (poller), 151.2 (bare) |
| A100 (board) | 69.8, 69.6 | 75.3, 75.4 | +0.004 | 0.23% / 0.03% | 75.3, 75.3 |
| GH200 (module) | 162.0, 162.0 | 209.4, 209.7 | +0.03 | 0.011% / 0.079% | 256.9 (poller), 257.3 (bare) |
| B200 (board; 2026-11-16) | 143.0, 143.1 | 191.9, 192.2 | +0.010 | 0.066% / 0.013% | 191.9 (poller), 192.3 (bare) |

- C-GCP (four RAPL zones, two 60 s idle windows): 429.02 W and 429.05 W (0.0065% apart; each zone within 0.2%); sampler
  overhead 0.39-0.48% of one core, 302 of 302 polls on schedule, no overrun, all four series advancing; under a 26-vCPU
  burn pinned to socket 0, package-0 rose 68.7% and package-1 changed by -0.12%; DRAM did not respond to the CPU-only burn.
- Context-open idle is far above bare idle on H100 (+68 W; SM clock 1980 MHz against 345 MHz), GH200 (+47 W) and B200 (+49
  W; SM clock 1965 MHz against 120 MHz) and small on A100 (+5.6 W; 1095 against 210 MHz). The state a session's idle windows
  measure follows from its engine being ready (a GPU engine is expected to hold a CUDA context, a CPU engine not; to be read
  off the session's `gpu.csv` clocks, not assumed), so a GPU-engine session's `P_idle` and a CPU-engine session's idle-GPU
  draw are different quantities.
- The H100 poller-minus-bare difference (0.72 W) is smaller than the difference between its two poller windows (77.5 against
  75.8 W), so it is not attributable to the poller; the A100, GH200 and B200 differences are 0.004 W, 0.03 W and 0.010 W.
  All pair differences are far inside G10's 10%.
- A 26-process CPU burn changed the A100 and B200 counters by nothing (B200: 192.1 W with the context open, 191.9 and 192.3
  W under the burn), raised the H100 board counter by 3-5% with its GPU temperature (62-64 to 66-67 C) and `power.draw`
  rising in step (a GPU-side effect, not CPU energy), and raised the GH200 module counter by 47-48 W while its GPU-only
  `power.draw` moved by 1.5 W (135.4 to 136.9 W): CPU activity is in the GH200 counter (§2.3). On H100, host CPU load can
  therefore move the "idle GPU" energy of a CPU-engine run (X6).

### 2.3 GPU energy (scope S1: GPU board; S1-module on GH200)
- **Primary:** NVML `nvmlDeviceGetTotalEnergyConsumption` (cumulative mJ), read through ctypes (`nvidia-smi --query-gpu`
  cannot return it) and differenced per run. P1 (H100): ~100 ms update, non-decreasing, within 0.17% of the 20 ms
  `power.draw.instant` integral over a pulsed synthetic load; P4: all 8 GPUs of a Lambda 8x A100. P7 (1x hosts, driver
  570.148.08 on all three, synthetic load; `docs/paper3-evidence/probes/lambda-<class>-<stamp>-semantics.json`): the counter
  advances and agrees with the 20 ms integral of the field of its scope, counter / integral = 0.998 (10 s idle) and 1.0002
  (20 s steady load) on H100, 1.001 and 1.003 on A100, and on GH200 0.9985 and 0.9982 against `module.power.draw.instant`
  (but 1.55 and 1.14 against GPU-only `power.draw.instant`). B200 (2026-11-16): 0.9986 and 1.0049 against
  `power.draw.instant` (board scope).
- **Cross-check (diagnostic, not a gate):** trapezoid integral of the 200 ms `nvidia-smi` stream (`gpu.csv`, as Paper 1; now
  also `power.draw.average` and `.instant` where offered, and on GH200 `module.power.draw.average` and `.instant`, with the
  driver version) over the same UTC window, as a relative difference to the counter, on windows >= 10 s only; on GH200 the
  comparison uses the `module.*` field, because GPU-only `power.draw` differs from the counter by 14-55% there (scope).
  Yang, Adamek and Armour (arXiv:2312.02741; SC24 per its DOI) report that `nvidia-smi` samples only 25% of runtime on A100
  and H100 (abstract checked 2026-11-11; applicability to our drivers not checked; the primary source is the counter, not
  sampling). If the counter is unavailable, the trapezoid value is labelled **S1-sampled**, never pooled (on GH200 the
  trapezoid of `module.power.draw`).
- **`power.draw` semantics:** H100 a trailing 1 s average (P2); A100 documented instantaneous (Average Power Draw N/A in P4
  and the archive), so archived cross-host `power.draw` is not like-for-like (Paper 1 carries the caveat). P7, measured (50
  ms stream under pulsed load): H100 `power.draw` equals `power.draw.average` in every sample (fraction 1.0; correlation
  with `.instant` 0.03) at driver 570.148.08 as at 580.105.08 (P2); A100 equals `power.draw.instant` in every sample
  (`.average` is N/A); GH200 equals `power.draw.average` in every sample (correlation with `.instant` 0.06). The averaging
  window length was not measured in P7. B200 (driver 570.148.08) equals `power.draw.average` in every sample (399 of 399;
  correlation with `.instant` -0.01).
- Scope: whole GPU board (GPU, HBM, VRM), device 0, one GPU per host; the host is excluded. P7 result: on H100 and A100 (no
  `module.*` fields, `Module Power Readings` N/A) the counter is the board. **On GH200 the counter covers the whole
  Grace-Hopper module** (Grace CPU, GPU and memory): counter / `module.power.draw.instant` integral 0.998 against 1.14 to
  1.55 for GPU-only `power.draw.instant` (above), and a 26-process CPU-only burn raised the counter from ~209 W to ~257 W
  (§2.2). Every GH200 counter value, GPU engines included, is therefore tagged **S1-module** (whole-module energy), and
  GH200 `power.draw` is GPU-only (88 W against the 162 W counter at bare idle). B200: board scope (no `module.*` fields,
  counter / integral 0.9986 and 1.0049), tag S1-counter. Also logged: temperature, SM/memory clocks, throttle reasons, power
  limit. Nothing is tuned: no clock locking, power capping or governor change.
- **GH200 power limits, recorded; the enforced limit is 700 W.** In
  `docs/paper3-evidence/probes/lambda-gh200-20261115T171353Z-semantics.json` (2026-11-15 17:30:19 UTC) the `nvidia-smi
  --query-gpu` row in `meta` reports `power.limit` 900.00 W and `power.default_limit` 900.00 W, while the `nvidia-smi -q -d
  POWER` text in the same file, parsed, gives GPU Current and Requested Power Limit 700.00 W, GPU Default and Max 900.00 W
  (Min 100.00 W) and Module Power Readings limits of 1000.00 W (Min 200.00 W). The source check on a second GH200 instance
  (`lambda-gh200-20261115T213713Z-sources.json`, 21:43 UTC) settles which value is enforced: `--query-gpu
  enforced.power.limit` reads 700.00 W (with `power.limit`, `power.default_limit` and `power.max_limit` at 900.00 W and
  `power.min_limit` at 100.00 W), NVML reports a management limit of 900 W and an enforced limit of 700 W, and the `-q -d
  POWER` text again shows Current Power Limit 700.00 W. So the GPU's enforced limit on the GH200 is 700 W, equal to the `-q`
  Current Power Limit; `--query-gpu power.limit` (900 W) is the default/max view and is not the enforced limit. On the H100
  (700 and 700 W) and the A100 (400 and 400 W; enforced 400 W in the 1x A100 source check) the sources agree; on the B200
  `power.limit`, `enforced.power.limit` and the `-q` Current Power Limit are all 1000 W. G11 therefore records the enforced
  limit (`enforced.power.limit`) as the power limit, with `power.limit` and the `-q -d POWER` text, logged at session start
  and end, beside it; the QA table shows them.

### 2.4 CPU energy (scopes S2, S3)
Source order, decided per host class by the probe report (fixed at the lock, never at analysis time):
1. **S2: RAPL** via powercap (`/sys/class/powercap/intel-rapl*/energy_uj`), the primary source is powercap per package
   and `dram` zone; `perf stat -e power/energy-pkg/,power/energy-ram/` (with `--per-socket`, verified first) or `turbostat`
   is a cross-check only. On the CPU-only host (P3: 2 sockets, `package-0`, `package-1` and one `dram` zone each, all
   advancing; perf `--per-socket` agreed with powercap per socket within 0.05%, idle and under a socket-0 burn (P5); bare
   `perf stat -a` gives Joules equal to the two-package powercap sum within 0.3% but a `runtime` summed over the sockets
   (40.0 s for a 20 s window), so power computed from its runtime column is half the whole-host figure: any perf cross-check
   uses `--per-socket` and Joules) **S2-pinned** (primary) = package + DRAM of the socket hosting the engine threads,
   package and DRAM also shown separately; **S2-all** = all four zones, the idle socket reported separately, not attributed.
   Root only (CVE-2020-8694): the sampler runs as root, or `setup_h100.sh --enable-rapl-read` is applied on this
   single-tenant host only; the choice is recorded. P5 (2026-11-11) ran the second path on the real host: `setup_h100.sh
   --power-only --enable-rapl-read --install-power-tools` succeeded, all four `energy_uj` files were then readable by the
   plain user, `power_telemetry.py --check-cpu-energy require` passed and the sampler ran as the plain user
   (`docs/paper3-evidence/probes/gcp-c3-highmem-192-metal-20261111-summary.md`). The sampler unwraps the counter with
   `max_energy_range_uj`; windows containing a sample flagged `decrease` (counter went backwards) or `gap` (> 30 s since the
   previous sample) are invalid.
2. **S3: whole node** via IPMI/DCMI (`ipmitool dcmi power reading`, ~1 s), only if a BMC is reachable (none: P1, P3, P4).
3. **None.** The cell reports time and cost and the text "CPU energy not measured on this host". **No model value is ever
   placed in a confirmatory table.** Every energy value carries a tag `S1-counter | S1-sampled | S1-module | S2-pinned |
   S2-all | S3 | none`, and the analysis code raises an error for an untagged value. Model values (EX2: host-CPU power as a
   function of CPU utilisation, fitted on the CPU-only host's RAPL and applied to a GPU host's `host.csv`) appear only in
   files and figures labelled MODEL.
- **Probe results.** P1 (H100) and P4 (8x A100): Lambda KVM guests, no powercap, MSR or IPMI, `energy-psys` reads 0 J:
  separable CPU energy is not measurable on those classes. **1x A100: not measurable, now shown** (CPU-energy source check
  of 2026-11-15, `docs/paper3-evidence/probes/lambda-a100-20261115T181846Z-sources.json`, `probe_power.py --no-load`, valid
  lease): a virtualized guest (hypervisor flag, `systemd-detect-virt` kvm, QEMU Q35 / SeaBIOS DMI), AMD EPYC 7J13 reported
  as 30 sockets of 1 core, SMT off; powercap has no zones (the `intel_rapl_*` modules are loaded but list none), no hwmon
  energy or power device, no perf power PMU, no `/dev/cpu/*/msr`, no BMC; `best_source` is `none`, nothing readable as user
  or root; the NVML GPU energy counter is present (power limit 400 W, enforced 400 W). **GH200: no separable CPU energy**
  (CPU-energy source check of 2026-11-15, `docs/paper3-evidence/probes/lambda-gh200-20261115T213713Z-sources.json`,
  `probe_power.py --no-load`, valid lease, a second GH200 instance): a virtualized guest (`systemd-detect-virt` kvm, QEMU
  KVM Virtual Machine DMI, EDK II firmware), Neoverse-V2 (aarch64) with 64 vCPUs shown as 1 socket of 64 cores, SMT off;
  powercap sysfs present with no zones, no hwmon energy or power device, no perf power PMU, no MSR device, no BMC;
  `best_source` is `none`. CPU energy on the GH200 exists only inside the module counter (S1-module, §2.3), so CPU-engine
  energy there is measured at module scope (§2.5, X7); Grace has no x86 RAPL, and no author decision is triggered. **B200:
  no CPU-energy source** (source check of 2026-11-16,
  `docs/paper3-evidence/probes/lambda-b200-20261116T052750Z-sources.json`, `probe_power.py --no-load`; the lease had expired
  by then): a virtualized guest (`systemd-detect-virt` kvm, QEMU Q35 DMI, EDK II firmware), Xeon Platinum 8592+ with 26
  vCPUs as 13 cores x 2 threads, 354 GiB; powercap sysfs present and the `intel_rapl_*` modules loaded but no zones, no
  hwmon energy or power device, no `/dev/cpu/*/msr`, no BMC; the power PMU exposes `energy-psys`, which counted nothing;
  `best_source` is `none`; the NVML counter is present, enforced power limit 1000 W. P3, P5: RAPL works on the metal host,
  no BMC.

### 2.5 Scope matrix and what may be compared
| Host class | Engines | Energy reported | Cost |
|---|---|---|---|
| Lambda x86 GPU host (A100, H100, B200 if admitted; H100, 1x A100, 8x A100 and B200 verified KVM guests) | GPU engines | S1-counter, GPU-board-only (CPU energy not measurable: P1, P4, 1x A100 source check) | host price |
| same host | CPU engines | S1 *idle GPU* (the GPU paid for while idle), GPU-board-only | host price |
| Lambda GH200 (Arm; verified KVM guest) | GPU engines | **S1-module**: Grace CPU + GPU + memory, whole-module energy (P7, §2.3) | host price |
| same host | CPU engines | **S1-module**: the CPU engine's draw on Grace plus the idle GPU's (whole-module energy; secondary comparison X7, below) | host price |
| CPU-only bare metal (GCP `c3-highmem-192-metal`; fallbacks §3.1) | CPU engines | S2-pinned, S2-all | right-sized VM B1 (primary), as rented B2 (§2.6) |

- **Within-class** energy contrasts (GPU vs GPU across hosts; CPU vs CPU) use one scope. **No common scope exists across
  classes:** no GPU-engine run is measured at node level (P1, P4; the bare-metal GPU host option was dropped by the author),
  so cross-class contrasts compare `E_G` (S1-counter, GPU board) with `E_C` (S2-pinned, package + DRAM), each a node-energy
  lower bound. On GH200 `E_G` is S1-module (it already contains the Grace CPU), so `U_G` below is only the platform outside
  the module; X4 energy contrasts that set the GH200 (S1-module) against an S1-counter host mix scopes: they are shown with
  both tags, not claimed as a like-for-like ratio (time and cost are unaffected).
- **Assumption N1** (an assumption, not a fact; not measured, not tested). Per query, node energy = measured + unmeasured.
  GPU node: `E_G` plus `U_G` (host CPU, DRAM, platform). CPU node: `E_C` plus `U_C` (platform; the other socket belongs to a
  larger machine than the right-sized B1 node and is shown as S2-all). **N1: `U_G >= U_C`.** Plausible because the GPU node
  adds a host CPU and DRAM running the same engine process to the same kind of platform, but it may fail. Every cross-class
  energy statement is worded "under N1", with the assumption-free scope-tagged figures (`E_G`, `E_C`, S2-all, `rho = E_C /
  E_G`) always shown beside it. Under N1:
  - **Survive:** (i) the direction "the GPU engine uses *more* node energy", claimed when the cell is "CPU better" (§5.3);
    (ii) a **lower bound on the energy break-even scale** (the break-even on the measured scopes, §5.3, because node-level
    "GPU better" implies measured-scope "GPU better"); (iii) all time and cost claims; (iv) scope-labelled within-class
    claims, never worded as node energy; (v) the idle-GPU energy a CPU engine burns on an x86 Lambda host.
  - **Do not survive:** "the GPU engine uses *less* or equal energy" at any scale (§11; the one exception is X7 below, at
    module scope on GH200); an energy break-even scale (only its lower bound); the magnitude of any cross-class ratio; "no
    difference" across classes. Spark GPU versus Polars GPU is GPU-board only; their host-CPU draw differs and is
    unmeasured. A modelled host-CPU term (EX2) may appear only as exploratory MODEL. If N1 is not accepted, only (iii)-(v)
    survive. Cost is fully measured either way.
- **Tipping point (pre-registered, every cross-class energy cell).** Node energies are equal iff `U_G - U_C = Delta*`, where
  `Delta* = E_C - E_G`. `Delta* < 0` ("CPU better" at the measured scope): N1 excludes it, so the GPU engine uses more node
  energy under N1. `Delta* > 0`: the GPU node uses less only if its unmeasured excess is below `Delta*`; the claim is not
  made, and `Delta*` says how large that excess would have to be to erase the measured advantage. `Delta*` comes from
  per-round suite totals (queries both classes ran), reported as mean and §5.2 t-interval over rounds, in Wh per suite run
  and in W (over the GPU suite time), beside the measured S2 idle power of one socket and the S1 idle GPU power for scale.
- **GH200 module-scope comparison (X7; secondary; author decision 2026-11-15, §11 item 8.3).** The GH200 counter covers the
  whole Grace-Hopper module, so on that host alone the CPU engines' energy is measured too: `E_CPU` (module energy of a
  CPU-engine run: the engine on Grace plus the idle GPU) against `E_GPU` (module energy of a GPU-engine run: the GPU plus
  Grace in support), same host, same counter, same scope tag **S1-module**. Planned: X1, X2 and X3 (§5.1) for energy at
  S1-module, the CPU engines being those run on the same GH200 (X7), with the §5.2 estimators, the §5.3 cell classification
  (+-10% equivalence margin) and break-even rule, the rounds, the window rule and `t_min(GH200)` (§2.1) and the gates of §4,
  all unchanged. Labelling: always "whole-module energy", never "GPU energy", "CPU energy" or "node energy"; the CPU
  engine's figure includes the idle GPU's draw and the GPU engine's includes the Grace CPU's (at bare idle the counter reads
  162.0 W of which `power.draw` is 88.4 W, §2.2); the module still excludes the rest of the node (fans, PSU, network,
  storage), so the claim is limited to the module. No N1 is involved (one host, one scope). It does not generalise to x86
  hosts, where N1 and the standing limitation of §11 are unchanged. Classified secondary (§5.4): the confirmatory set X1-X3
  is defined with the cross-class energy reference of §5.1, and this contrast is a different one (same-host, module scope).

### 2.6 Cost
- `cost_run = (p / 3600) * t_run`; `p` = on-demand list USD/hour of the instance rented; `t_run` = timed seconds. Per-second
  accrual (billing minimums ignored). Energy, carbon, storage and egress are not in per-query cost.
- **Primary price table P-NEW, frozen at the lock.** Lambda: per-instance-hour prices at https://lambda.com/pricing
  effective 2026-11-16 00:00 UTC, fetched 2026-11-11 (H100 SXM5 4.89, GH200 2.59, B200 7.99, A100 SXM4 40 GB 1.99). GCP
  (Google's official pages fetched 2026-11-11 16:21-16:24 UTC, evidence in
  `docs/paper3-evidence/prices-20261111/gcp-official/`; us-east4 and us-central1 identical, no metal premium):
  `c3-highmem-192-metal` 12.701568, `c3-standard-192-metal` 9.677184, `c3-highcpu-192-metal` 8.669056, `c3-highmem-44` (B1)
  2.910776; Hyperdisk Balanced (800 GiB, 16k IOPS, 1,000 MBps) ~$0.224/h. This is the P-NEW GCP table, snapshot 2026-11-11;
  a zone outside those two regions needs its price added before the lock. AWS (fallback only): Price List API, us-east-2,
  Linux, fetched 2026-11-11 (m7i.metal-24xl 4.8384, r7i.8xlarge 2.1168). Every session is costed with P-NEW whatever was
  billed, so cost ratios depend only on measured time and a table fixed before any outcome exists. P-NEW stays the frozen
  primary table whenever the campaign runs, list prices whatever credits fund the bill; P-OLD is the sensitivity and
  P-BILLED captures the price actually charged.
- **P-OLD (sensitivity):** Lambda prices before 2026-11-16, per the API on 2026-11-11: H100 SXM5 4.29, GH200 2.29, B200
  6.99, A100 SXM4 1.99. **P-BILLED (third sensitivity):** `campaign.json` records per instance the provider's price at
  launch, instance id, launch and terminate UTC times and whether billing was credit-funded; the invoice is reconciled
  against hours x price and a gap above 2% is reported. Cost results are reported under P-NEW (primary), P-OLD and P-BILLED;
  which is primary is not revisited after data exist.
- **Snapshots** at the lock and at the start and end of each host's campaign: raw provider responses (Lambda API
  `price_cents_per_hour` and a pricing-page capture, since they differed on 2026-11-11; AWS Price List API; GCP official
  pricing pages) with UTC time, region, OS and sha256. Third-party price sites are secondary only. **Factorised reporting:**
  time ratio and price ratio are shown separately; the GPU host is cheaper iff `p_gpu / p_cpu < t_cpu / t_gpu`.
- **CPU baselines** (Paper 2's fair-baseline lesson: do not charge the CPU engine for an idle GPU):
  - **B1 (primary):** CPU-only *right-sized* VM = the smallest GCP C3 VM with >= 26 vCPU and >= 200 GiB RAM, price
    2.910776/h, applied to times measured on the metal host pinned to 26 vCPUs. This is `c3-highmem-44` (44 vCPU, 352 GiB),
    the smallest fit. RAM floor: 200 GiB is the least RAM of any Paper 1 host (A100), where the feasibility audit found no
    unexpected query error or non-zero exit at any scale; `c3-standard-44` (176 GiB) is below it. The C-VM run (§3.1) checks
    B1: pass if the geometric-mean time ratio VM/metal is in [0.90, 1.10] and no CPU-engine query fails for host resource
    exhaustion (A1) at SF300; otherwise B1 uses the C-VM times where available and the gap is reported. If the metal host
    falls back to AWS, B1 is AWS `r7i.8xlarge` (32 vCPU, 256 GiB).
  - **B2 (sensitivity):** CPU-only host *as rented* (an inflated upper bound). **B3 (sensitivity):** the same CPU engine on
    the same Lambda GPU host (the idle-GPU tax).
- **Time scopes.** Query energy and cost cover the timed runs only (primary). **Session energy and cost** (secondary) cover
  worker launch to process exit, less the §2.2 idle windows; the runner records launch, `completed_at_utc` and
  **process-exit** stamps with counter readings (the archive has no exit time; teardown reached 38 s there). Cold-job cost:
  `(p/3600) * (launch_to_engine_ready + t_run)`. One query at a time on a whole instance; sharing idle vCPUs or the GPU
  between queries is not modelled.

## 3. Hosts, arms, data, order
### 3.1 Hosts
| Id | Instance | Role | USD/h P-NEW (P-OLD) |
|---|---|---|---|
| G-A100 | Lambda `gpu_1x_a100_sxm4` (A100 40 GB, 30 vCPU, 200 GiB) | 4 engines | 1.99 (1.99) |
| G-H100 | Lambda `gpu_1x_h100_sxm5` (H100 80 GB, 26 vCPU, 225 GiB) | 4 engines, **reference** | 4.89 (4.29) |
| G-GH200 | Lambda `gpu_1x_gh200` (GH200 96 GB, 64 vCPU Arm, 432 GiB) | 4 engines; energy at S1-module (§2.3); module-scope CPU-vs-GPU comparison X7 (§2.5) | 2.59 (2.29) |
| G-B200 | Lambda `gpu_1x_b200_sxm6` (B200 180 GB, 26 vCPU, 360 GiB) | 4 engines; **conditional host** (below) | 7.99 (6.99) |
| C-GCP | GCP `c3-highmem-192-metal`, us-east4-a (2x Xeon Platinum 8481C, 96 cores / 192 threads, 1,536 GB), engines pinned to 26 vCPUs on socket 0 | 2 CPU engines, RAPL (P3) | 12.701568 (same) |
| C-VM | smallest GCP C3 VM with >= 26 vCPU and >= 200 GiB (§2.6; `c3-highmem-44`) | time check for B1: baseline SF10, SF30, SF300, 2 rounds, time only; protocol below | 2.910776 (same) |

- The four GPU hosts are Paper 1's, so energy and cost can be set beside its timings. Only instance types returned by the
  Lambda API at the lock (response archived) are used (it lists no H200, GB200 or B300); a type missing at the lock is
  dropped by amendment, not substituted (B200: next bullet). Hosts are never pooled (Paper 1 A2). Stack and engine settings
  are Paper 1's (A2-A4: versions, `a100-parity`, 26 threads, strict GPU guard), recorded in `campaign.json` with instance
  type, CPU model, hypervisor, kernel, driver, RAM, storage type, probe-declared energy sources and the design commit (§7).
- **B200 is a conditional host** (author decision 2026-11-15, §11 item 8.2). Lambda had no 1x B200 capacity from 2026-11-11
  until a first battery could be run before the lock (2026-11-16, below). Its sessions may run only after its semantics step
  (counter, `power.draw` semantics, counter scope), P5, P6 and G8 have been run on a B200 instance and pass under the same
  rules as the other hosts: the counter advances and the integral of one instantaneous-power field (`power.draw.instant`, or
  `module.power.draw.instant` where offered) matches it in scope, meaning counter / integral lies within 0.98-1.02 on both
  the semantics step's 10 s idle window and its 20 s steady-load window (the same 2% as the `t_min` rule, fixed here before
  any B200 data and not tuned to observed values; the matching field fixes the scope tag and is the P6 reference), idle pair
  differences within G10's 10%, P6 yields a `t_min` on the tested grid with `t_min(B200) = max(1 s, windows-mode measured
  value)` against the reference field of the counter's scope, and G8 passes at the §4 thresholds. **Status after the
  pre-lock battery of 2026-11-16** (`docs/paper3-evidence/probes/lambda-b200-20261116T052750Z-*`; Lambda `gpu_1x_b200_sxm6`
  in australia-east-1, driver 570.148.08). Met: counter / `power.draw.instant` integral 0.9986 (10 s idle) and 1.0049 (20 s
  load), inside 0.98-1.02, so the counter is board scope with tag S1-counter (no `module.*` fields); idle pair differences
  0.066% (bare) and 0.013% (poller); G8 passes (median lag 0.195 s, p90 0.305, max 0.334, drift -0.039 s, 20 pulses, no
  onset missed). Not met: P6. Four of the five remote SSH steps returned 255 (connection lost: the driving machine slept for
  the whole battery, so its SSH sessions to the host were lost; the team lead's check of the local power log, which is not
  archived in the repository), `p6.json` was not collected and only its 20 ms stream was (06:30:20-06:46:36 UTC, 48,451
  rows, median spacing 20.0 ms, largest gap 22 ms), which holds no counter reads or pulse stamps and so cannot be scored;
  `t_min(B200)` is unknown. The conditions met are taken as met and may be reused at admission (lead's decision 2026-11-16);
  admission needs a valid P6 rerun on a B200 instance (windows mode against `power.draw.instant`), giving `t_min(B200) =
  max(1 s, measured)`. That P6 result and `t_min(B200)` are recorded in an append-only §12 amendment (outcome data seen:
  none beyond synthetic probe load) before the first B200 session. The CPU-energy source check ran in the same battery and
  found no source (§2.4); it is not an admission condition. If B200 never passes, or no capacity appears before the campaign
  ends, it is reported as **not measured**: no substitution by an 8-GPU AWS or GCP B200 machine, which is a different host
  class (CPU, interconnect, tenancy, price basis). Rounds, squares, the seed rule (the lock date), gates and analysis are
  those of the other GPU hosts; RQ4 then covers three GPU generations.
- **CPU-only host choice.** At P3 (~15:00 UTC) `c3-highmem-192-metal` had capacity in us-east4-a only; the `c3-standard-`
  and `c3-highcpu-192-metal` shapes were stocked out in every US zone. Fallback order, first obtainable: (1)
  `c3-highmem-192-metal` in another US zone; (2) `c3-standard-192-metal` (768 GB, 9.677184); (3) `c3-highcpu-192-metal` (512
  GB, 8.669056); (4) AWS `m7i.metal-24xl` (96 vCPU, 384 GiB; last resort, probed first as P8). The choice is fixed before
  round 1 and recorded in `campaign.json` (provider, shape, zone, RAPL zones); shapes differ in populated DRAM, so data from
  different shapes are never pooled. Host loss mid-campaign counts as "one square only" (§3.2). No AMD platform.
- **Pinning.** Process CPU affinity (`os.sched_setaffinity`, set by the runner before any child starts) to 13 physical cores
  plus SMT siblings (26 vCPUs, as the H100 VM), all on socket 0, plus NUMA memory binding to the pinned socket (`numactl` or
  libnuma; recorded; fail-fast if unavailable); socket 1 stays idle and its package + DRAM is the measured idle floor (P5
  QA, 60 s windows: the four RAPL zones sum to 429.02 and 429.05 W idle, CPU and DRAM only, so a lower bound on wall power;
  packages 178.7-179.3 W, DRAM 35.5-35.6 W per socket; P3's 3 s window gave ~175 W and ~38 W). **Verified by P5**
  (`docs/paper3-evidence/probes/gcp-c3-highmem-192-metal-20261111T203231Z-{topology,membind-socket0,numa-bind}.json`):
  socket 0 = NUMA nodes {0,1} (CPUs 0-47, 96-143), socket 1 = {2,3}, sub-NUMA clustering on; the 26-vCPU pin is CPUs
  0-12,96-108, all on node 0, and the runner's memory binding for that pin is node 0 only, while socket 0's `dram` zone
  covers nodes 0 and 1. `--probe-membind` exits 0 (libnuma `set_mempolicy`, `MPOL_BIND`). Binding wins over first-touch
  locality (8 GiB allocator, `numa_maps`): 99.96% of pages on the bound node under the real pin and under `--socket 0`;
  99.96% on socket 0's node for a process running on socket 1's CPUs but bound to socket 0's nodes; the unbound control on
  socket 1 puts 99.91% on its own node. DRAM attribution to the pinned socket is therefore sound. Placement and cross-socket
  traffic are recorded, not attributed. C-GCP has network storage (Hyperdisk only), not Lambda's local SSD, so Spark spill
  I/O may differ (recorded).
- **C-VM protocol** (lead's decision 2026-11-15; the draft stated no pinning for the VM, only "26 threads", §2.6). So that
  the B1 time check is like-for-like with the metal host: the engines are pinned to 26 vCPUs made of 13 physical cores plus
  their SMT siblings, taken from the VM's reported topology and recorded (if the VM reports no SMT siblings, 26 vCPUs,
  recorded, and the caveat is stated), with NUMA binding only if the VM reports more than one node. The window rule (10 s) of
  §2.1 applies as on the metal host, because the metal times come from windows of back-to-back warm runs. Time only: no
  energy, no `t_min`, no cell is "below resolution". The two engines take the letters of §3.2 with orders `AB` and `BA`. The
  C-VM has no §2.2 idle brackets, so the >= 60 s cool-down between sessions of §3.2 applies literally: the harness runs it
  with a 60 s `--session-cooldown-seconds` (the campaign hosts use 0, §3.2).

### 3.2 Workload, rounds, controls
- **Workload.** Baseline suite q01, q03, q06, q10, q12, q14 at SF1, SF10, SF30, SF100, SF300; complex suite (v2) at SF30,
  SF100, SF300. Inputs, generation parameters and the `verify_generated_inputs.py` gate as Paper 1 §3; Float64
  normalisation. Queries are never rewritten. Pre-declared Polars GPU gaps (cx_q13, cx_window) and the A1
  coverage-versus-integrity classification carry over. The scale grid is Paper 1's (SF1 is the small-data end where fixed
  GPU/JVM start-up should matter), so Paper 3 can be read against Paper 1 timings; SF1 energy may be "below resolution". The
  grid is not refined after seeing data (§5.3).
- **GPU hosts:** R = 8 rounds = the Paper 1 Williams square (n = 4) twice, the second time in reverse row order. Seed
  rule: the integer `YYYYMMDD` of the lock date (§7.1: the UTC date of the annotated `paper3-design-lock` tag);
  `random.Random(seed).shuffle(["polars","polars_gpu","spark_cpu", "spark_gpu"])` gives letters A-D; rounds 1-4 use `A B D C
  / B C A D / C D B A / D A C B`, rounds 5-8 use the same rows in reverse order (4, 3, 2, 1). Suite order alternates (odd
  rounds baseline first); scales ascend. **CPU-only host:** 2 engines, orders `AB BA BA AB BA AB AB BA`. The two CPU engines
  (`polars`, `spark_cpu`) take the letters A and B in the order in which their letters appear in the GPU-host assignment
  from the same seed (no operator choice); for example, if the shuffle gave `[spark_gpu, polars, polars_gpu, spark_cpu]`
  (letters A-D), `polars` (B) comes before `spark_cpu` (D) and so is A on the CPU-only host. C-VM uses the same letters,
  orders `AB` and `BA`.
- The two squares run on **separate instance launches** where capacity allows, so between-instance variation enters the
  across-round interval; instance is reported as a sensitivity factor. A square is rounds 1-4 or rounds 5-8 (on C-GCP
  likewise the first or the last four orders); it is complete when all four of its rounds pass the gates (§4). Confirmatory
  estimates use complete squares only: two complete squares give R = 8; one complete square gives R = 4 (df = 3) from it,
  stated; the rounds of an incomplete square enter no confirmatory estimate and appear only as a sensitivity; a host with no
  complete square is reported descriptively, outside the confirmatory set. No round is added or dropped for what its values
  look like. Sessions: 32 per GPU-host round, 16 per CPU-only round.
- **Controls.** Paper 1 §6 (no other workload, no foreign GPU processes, preheated files, off-host rsync + checksums), plus:
  >= 60 s cool-down between sessions, met by the §2.2 brackets (>= 150 s without query load between the last query of one
  session and the first of the next; the harness flag `--session-cooldown-seconds` is 0 on the campaign hosts and 60 s on
  the C-VM, which has no brackets, §3.1); C-GCP is single-tenant bare metal (network storage is shared); Lambda hosts are
  KVM VMs where checked (H100, 1x A100, 8x A100, GH200, B200; noise visible via `host.csv`, clock and throttle logs).
  Measurement processes (`nvidia-smi` with `TZ=UTC`, RAPL sampler) run in every session including idle windows, so their
  draw is in `P_idle` and `E`; P5 quantified their overhead (§2.2: 0.004-0.72 W for the `nvidia-smi` poller on the 1x GPU
  hosts, the H100 figure within its window-to-window variation; 0.39-0.48% of one core for the RAPL sampler).

## 4. Integrity and telemetry gates (pass/fail; never based on engine comparisons)
Paper 1 §7 and A1 apply unchanged: G1 every session exits, no timeout; G2 output agreement with `polars` (rel 1e-8, abs
1e-6, integers exact); G3 strict GPU guard, only classified coverage outcomes fail; G4 Spark GPU plan check; G5
`verified_gc` barriers; input gate before round 1. New:

| Gate | Rule | On failure |
|---|---|---|
| G6 source conformance | each session's counters match the probe-declared sources; none missing or non-monotone mid-session; every sampler series has liveness verdict "advancing", judged per session on the samples inside that session's stamps; launch, completed and process-exit stamps with counter readings present | session energy invalid; round fails, repeated once; no substitution |
| G7 sample coverage | trapezoid windows have >= 80% of expected 200 ms samples, no gap > 1 s | cross-check flagged; counter value unaffected |
| G8 clock alignment | activity-onset correlation, **onset times only** (no magnitudes): for GPU-engine windows >= 5 s, the first `gpu.csv` sample with `utilization.gpu` >= 10% must follow the worker's window start with median lag within +-0.4 s and first-third versus last-third drift <= 0.2 s; sessions without such windows use a 3 s load pulse (separate process) at start and end; `chronyc tracking` offset logged | `gpu.csv` values disabled for the session |
| G9 resolution | window rule and the host's `t_min` (§2.1: 1 s H100, 1 s GH200, 2 s A100, 2 s C-GCP; B200 pending) met | cell "below resolution" |
| G10 idle stability | pre/post idle mean power within 10% per scope | above-idle flagged unreliable; total unaffected |
| G11 thermal/power state | throttle reasons, power limit (the enforced limit, `enforced.power.limit`, with the `--query-gpu` fields and the `nvidia-smi -q -d POWER` text at session start and end beside it, §2.3), temperature logged | flagged, not excluded |
| G12 exclusive use | no foreign GPU compute process; nothing else scheduled on C-GCP | integrity failure |

**G8 pre-lock probe, thresholds unchanged** (20 synthetic 3 s pulses per host against a 200 ms `nvidia-smi` stream with
`TZ=UTC`; `docs/paper3-evidence/probes/lambda-<class>-<stamp>-g8.json`). Median lag / p90 / max / first-third minus
last-third drift, in seconds: H100 0.241 / 0.313 / 0.327 / -0.072, pass; A100 0.389 / 0.522 / 0.592 / +0.007, pass but
**marginal** (the median is 0.011 s inside the 0.4 s limit and 8 of its 20 lags exceed 0.4 s; its smallest lag is 0.276 s
against 0.140 s on H100); GH200 0.226 / 0.297 / 0.331 / -0.005, pass; B200 0.195 / 0.305 / 0.334 / -0.039, pass (smallest
lag 0.077 s). No onset was missed on any host. A session that fails G8 is handled by the rule in the table (its `gpu.csv`
values are disabled; the counter energy is not affected); the thresholds are not moved.

Rounds fail on G1-G6 and G12 (repeat once, then stop, as Paper 1); G7-G11 only set eligibility flags, counted in a QA table.
Allowed QA views before the campaign ends: gate outcomes, sample counts, idle stability, coverage, read latencies and
overhangs. **Not allowed:** any engine-versus-engine or host-versus-host energy, power or cost aggregate (§7.3).

## 5. Statistical analysis plan
### 5.1 Quantities and contrasts
Per (host, round, suite, scale, query, engine): `m` = median over the 3 trial windows of per-run time `T`, energy `E`
(total; above-idle alongside) and cost (baseline B1/B2/B3). `rho = m_ref / m_engine`, so `rho > 1` means the engine uses
less (time, energy, cost). Per host, suite, scale and metric with a valid scope or price:
- **X1** `polars` / `polars_gpu`. **X2** `spark_cpu` / `spark_gpu`.
- **X3** best CPU engine / best GPU engine: per query and round, the lower of the two CPU values over the lower of the two
  GPU values among engines that ran the query (strict variant: queries both GPU engines ran, as Paper 1 C4). CPU reference:
  B1 for cost; S2-pinned on C-GCP for energy (cross-class, so under N1 with `Delta*` of §2.5).
- **X4** GPU generation: per GPU engine, host `h` / G-H100, Welch-type interval on the log scale across rounds (Paper 1 A2);
  descriptive; confounded with CPU, RAM, link and driver. **X5** CPU platform (CPU engines): G-* / C-GCP for time and cost
  only (no common energy scope, §2.5). **X6** idle-GPU tax: B3 / B1 as price ratio x time ratio, plus the S1 idle-GPU energy
  of CPU-engine runs in Wh per suite run (not set against a CPU-node figure; x86 GPU hosts only, on GH200 the idle GPU is
  inside the module counter).
- **X7 (secondary; GH200 only; author decision 2026-11-15).** X1, X2 and X3 for energy (total, and above-idle, which is
  reported for CPU engines at module scope, §2.2) at S1-module, with the CPU engines taken from the same GH200 host instead
  of C-GCP (§2.5). Same estimators (§5.2), classification and break-even rule (§5.3), rounds, window rule, gates and
  margin as X1-X3; labelled whole-module energy; it is not set against any S2 figure.

**5.2 Estimation.** Per query: log-ratio per round; estimate = geometric mean over rounds; **95% CI = t-interval on the mean
log-ratio, df = R - 1** (R = 8: t = 2.365; R = 4: t = 3.182), back-transformed; across-round min-max also reported. Per
(suite, scale): mean of per-query log-ratios over queries supported by both engines in that round, same t-interval across
rounds (round = block). Suite totals (sum of per-query medians, Wh and USD per suite run) are reported beside geometric
means, never mixed with them. Also: within-session and between-round CV; order and carryover by position and predecessor
(descriptive). Thermal carryover is likely for energy; the Latin square and cool-down limit it, not remove it.

### 5.3 Cell classification and break-even rule (fixed in advance)
Each (contrast, host, suite, scale) cell is classified from its 95% CI of `rho`:
- **GPU better**: CI entirely > 1. **CPU better**: CI entirely < 1.
- **No difference**: CI entirely inside [1/1.10, 1.10] (+-10%, approved by the author 2026-11-11; a practical-significance
  choice, not derived from data). A CI that merely contains 1 is **inconclusive**, never "no difference".

**Break-even scale** for X3 (and X1, X2, and X7 at module scope), per suite, host and metric (energy total, energy
above-idle, cost B1, B2, B3, each under P-NEW, P-OLD and P-BILLED): the smallest grid scale `s` such that every grid scale
`>= s` is "GPU better". It is reported as the bracket (previous grid scale, `s]`; "<= SF1" and "> SF300" are censored
outcomes and reported as such. For cross-class energy it is computed on the measured scopes (S1 vs S2-pinned) and labelled
"lower bound on the node-level break-even, under N1" (§2.5); for X7 it is the module-scope break-even on GH200, labelled
"whole-module energy", with no N1. A log-linear interpolation of the mean log-ratio against log SF is descriptive only. No
scales are added to refine a break-even inside this design.

**5.4 Multiplicity, precision, missing data, code.**
- No tests or p-values. Confirmatory set: X1-X3 for {energy, cost} x suite x scale x GPU host; X4-X6 are estimates; X7
  (GH200 module scope, RQ6) is secondary: fully specified here and reported with the same intervals, but outside the
  confirmatory set.
  Intervals are marginal; no multiplicity correction. No power calculation: between-round variance of energy is unknown
  before data; interval width is a result, and R is set by time and instance availability.
- Ineligible cells (coverage outcome, below resolution, scope absent, invalid session) go to a coverage matrix and are
  excluded from the affected contrast; no imputation. Rounds of an incomplete Williams square are excluded from every
  confirmatory estimate (§3.2).
- Analysis code is committed before the lock and run only on a synthetic campaign with injected known effects (as
  `artifacts/campaign-synthetic`) to show it recovers them; its sha256 is recorded at the lock.

## 6. Archived-data analysis (secondary, exploratory)
### 6.1 What already exists (the lock text must repeat this)
- The archived campaign-20261030 data (Zenodo DOI 10.5281/zenodo.24125306) hold a 200 ms `gpu.csv` (incl. `power.draw`) per
  host, per-session UTC start/end, per-trial durations and per-second `host.csv` CPU utilisation. **No CPU power.**
- **Per-engine GPU power aggregates already exist in the repository and in Paper 1:** each host's committed analysis has
  `t16_energy_proxy.csv` (session mean power x process wall time) and `t24_gpu_telemetry.csv` (median `power.draw`, fraction
  of power limit), per suite, scale and engine; `stats.json` carries the same keys; Paper 1 and the PVLDB version report
  median board power as a share of the power limit at SF300 per host and engine, and the PVLDB text interprets it; Paper 1's
  locked design (§9) lists an "energy proxy" as a secondary analysis. The AI assistants that drafted this section and the
  feasibility audit report that they checked file names, column headers and key names only and did not open the values (the
  finalising drafter did not either). What the author has seen: §7.2.
- Consequences: (i) EX1 is not blind to GPU power magnitudes and **every analysis of archived data is exploratory only**;
  (ii) the pre-registration protects the new campaign's analysis choices and all CPU energy (no data exist), not the
  archived GPU power; (iii) the new campaign, not EX1, carries the confirmatory claims.
- Per p3-feasibility (timing only): all 18,048 archived trial records carry `started_at_utc`/`ended_at_utc`, exceeding the
  timed `seconds` by a median 12 ms (p99 106 ms); `gpu.csv` stamps map to UTC with offset 0 s (+-0.4 s); teardown (up to 38
  s) is not recorded. `power.draw` semantics differ by GPU (§2.3), so archived cross-host comparisons carry a caveat.

**6.2 Plan (computed only after the lock; the only permitted use of archived data).**
- **EX1a (per trial):** trapezoid of `power.draw` over `[started_at_utc, ended_at_utc]`, GPU engines only, trials of
  duration >= 1 s (the repository's telemetry rule) holding >= 5 samples, so mainly large scales; UTC offset 0 s, with +-0.4
  s shifts as a sensitivity. **EX1b (per session):** mean `power.draw` x process wall time (the existing proxy, all scales;
  its aggregates already exist, §6.1). Per host and engine, geometric means of ratios with the §5.2 round t-interval,
  labelled "exploratory, GPU board only, no CPU energy, `power.draw` semantics differ across GPUs". They validate the proxy
  against the new counter-based GPU energy and never fill a gap in the new data.
- **EX3a:** cost from archived Paper 1 timings x the price snapshot (Lambda hosts only); exploratory, because the author has
  seen the timings. No archived value enters a confirmatory table.

## 7. Pre-registration procedure
### 7.1 Lock
A **git commit and push** of this file with the status line changed to `LOCKED <UTC time>. At the lock no timed Paper 3
study session has run and no Paper 3 energy or cost analysis has been computed; everything seen earlier is listed in §6.1
and §7.2.` Then an annotated tag `paper3-design-lock`. It must precede (a) any timed Paper 3 session and (b) the computation
of any Paper 3 energy, power or cost aggregate from any data, archived data included. The commit hash and push time are
recorded here, in every host's `campaign.json` and in the paper. The lock wording must not say "before any data existed".
The external timestamp is a Zenodo deposit of the locked design file made at the lock, its DOI recorded here, in every
`campaign.json` and in the paper (commit dates are author-controlled). **Lock date** (the seed rule of §3.2): the UTC date of
the annotated tag `paper3-design-lock` (its tagger date); the Zenodo deposit is made the same UTC day.

**Timing (author decision 2026-11-15, §11 item 8.2).** The lock is made once the GH200 P6 rerun against
`module.power.draw.instant` is in and `t_min(GH200)` has been written into §2.1 by the rule stated there: both are met
(rerun of 2026-11-15, `t_min(GH200)` = 1 s). The C-GCP P6-CPU probe, which the lead first made a condition of the lock and
then of the first C-GCP session, also ran before the lock (2026-11-16, `t_min(C-GCP)` = 2 s, §2.1). One condition follows
the lock: B200 admission (§3.1; after the partial battery of 2026-11-16 only a valid P6 rerun is missing), recorded in an
append-only §12 amendment before its first session (outcome data seen: none beyond synthetic probe load). A locked design
without B200 data is complete.

### 7.2 What may precede the lock (all disclosed in a dated probe report committed before the lock)
- Drafting and review of this design, reading the repository, papers and published Paper 1/2 results, and harness and
  analysis development on synthetic data and loads.
- **Probe runs of power interfaces, no study workloads.** Each lists host, time, command and raw output (sha256); the
  evidence is in `docs/paper3-evidence/probes/`. Their idle and synthetic-load numbers are QA values, not outcomes.
  - **Done, 2026-11-11 (no study queries run).** **P1** ~14:07 UTC, Lambda `gpu_1x_h100_sxm5` (KVM VM, Xeon Platinum 8480+,
    13 cores / 26 threads, driver 580.105.08; updated probe re-run 14:44): NVML energy counter with ~100 ms update, within
    0.17% of the 20 ms `power.draw.instant` integral; no RAPL, MSR or IPMI; `energy-psys` reads 0 J. **P2** ~14:17, same
    host: under a pulsed synthetic CUDA spin kernel `power.draw` equalled `power.draw.average` in 228/228 samples and
    `.instant` in 1/228, i.e. a trailing 1 s average. **P3** ~15:05, GCP `c3-highmem-192-metal` us-east4-a: RAPL works
    (§2.4), no BMC, availability as in §3.1. **P4** ~15:20, Lambda `gpu_8x_a100` europe-central-1 (KVM VM; the 8x type, not
    the study's 1x type): no CPU energy source, NVML counter on all 8 GPUs. **Instrumentation smoke test** ~14:18-14:21 on
    the H100: `power_telemetry.py` and the worker's per-run counter reads, synthetic CUDA pulse load only.
  - **Done, 2026-11-11 to 2026-11-16 (no study queries run; synthetic CUDA spin kernel, `nvidia-smi` pollers and CPU burners
    only).** **P5 on C-GCP**, `c3-highmem-192-metal` us-east4-a, launched 2026-11-11 20:32:31 UTC, deleted 20:44:14:
    topology, the `--probe-membind` check, the four-scenario NUMA-binding check (`numa_maps` must show the engine's pages on
    the bound nodes: it does, §3.1), perf versus powercap, sampler overhead, two idle and one busy 60 s window
    (`gcp-c3-highmem-192-metal-20261111-summary.md` and `gcp-c3-highmem-192-metal-20261111T203231Z-*`; results §2.4, §3.1).
    **P5, P7 (counter, `power.draw` semantics, counter scope), P6 and G8 on three Lambda 1x classes**, driver 570.148.08:
    H100 SXM5 (us-south-2, launched 2026-11-12 03:50 UTC, stamp `20261112T035018Z`), A100 SXM4 40 GB (us-east-1, launched
    03:50 UTC, `20261112T035051Z`) and GH200 (us-east-3, launched 2026-11-15 17:13 UTC, `20261115T171353Z`); files
    `lambda-<class>-<stamp>-*` (results §2.1-§2.3, §4). A first H100/A100 attempt left no kept data (team lead's report, not
    archived). The GH200 P6 in these files is invalid (§2.1; rerun below). **1x A100 CPU-energy source check**
    (`probe_power.py --no-load`, valid lease): Lambda `gpu_1x_a100_sxm4` us-east-1, launched 2026-11-15 18:18:48 UTC, probe
    18:23:02, termination confirmed 18:24:40; files `lambda-a100-20261115T181846Z-sources.json` and `-run.log` (result
    §2.4).
  - **GH200 P6 rerun and CPU-energy source check** (2026-11-15; no study queries, synthetic load only): Lambda
    `gpu_1x_gh200` us-east-3, a second instance, launched 21:37:15 UTC; `probe_power.py --no-load` at 21:43:11; P6 against
    `module.power.draw.instant` 21:43:26-21:59:35; termination confirmed 22:00:43; files `lambda-gh200-20261115T213713Z-*`
    (results §2.1, §2.3, §2.4; the copied `power_telemetry.py` differed from HEAD, see §2.1).
  - **C-GCP P6-CPU probe** (2026-11-16; no study queries, synthetic pinned CPU spin loops only): `c3-highmem-192-metal`,
    create refused in us-east4-a and made in us-east4-c at 00:09:34 UTC; repository archive of HEAD 82d189f copied; probe
    00:12:00-00:27:53; instance deleted and confirmed gone 00:29:35 (about 20 minutes); files
    `gcp-c3-highmem-192-metal-20261116T000855Z-p6-cpu*` (result §2.1).
  - **B200 battery, partial** (2026-11-16 05:27-08:38 UTC; no study queries, synthetic load only): Lambda `gpu_1x_b200_sxm6`,
    australia-east-1, driver 570.148.08, launched 05:27:52 UTC, SSH ready 05:39:35. Remote steps (run log): p5-gpu 255 (the
    step had finished on the host at 05:50:42 UTC, per its JSON; files collected), semantics 0, p6 255 (only the 20 ms
    stream was collected, no `p6.json`), g8 255 (`g8.json` collected; run on the host at about 07:33 UTC), sources 255
    (`sources.json` collected; probe at 08:20:15 UTC); a 255 is a lost SSH connection (the driving machine slept throughout,
    per the team lead's check of the local power log, not archived) and the files collected afterwards are the only evidence
    of what ran. Termination was requested at 08:37:49 UTC and the run log could not confirm it by 08:55:53 (manual check
    required); the instance outlived its lease (expiry 07:27 UTC) and the 5400 s watchdog backstop armed at launch (the
    sources probe ran on it at 08:20 UTC). Files `lambda-b200-20261116T052750Z-*` (results §2.2-§2.4, §3.1, §4; spend §9).
    The P6 is missing (§3.1).
  - **Pending.** *Before the lock:* nothing; the GH200 P6 rerun and source check and the C-GCP P6-CPU probe are in. *After
    the lock, before its first session:* if capacity appears, a valid P6 on a B200 instance (§3.1); the rest of the B200
    battery (semantics, P5, G8, source check) ran on 2026-11-16 and is reused. *Not run:* P8 RAPL, topology and idle floor
    of a fallback host if one is used.
  - **Harness readiness (before round 1; not a lock item).** The campaign needs the harness to implement the window rule
    (§2.1), the `gpu.csv` power fields (`power.draw.average` and `.instant`, and `module.power.draw.average` and `.instant`
    on GH200), the scope tags (§2.4) and a scope-aware counter description (module, not "whole-board", on GH200), and to log
    the `nvidia-smi -q -d POWER` text at session start and end (§2.3, G11). These are being implemented as opt-in options so
    that Paper 1's default command lines and outputs are unaffected; this document has not verified them. A short synthetic
    smoke test on one GPU host (synthetic load, no study queries) checks them before round 1 and is recorded in the probe
    report if it precedes the lock, else in a §12 amendment (outcome data seen: none beyond synthetic load).
- **Feasibility audit blinding note** (structure-only audit of the archive): before switching to timestamp-only readers the
  auditor printed raw rows of the h100 round-1 baseline suite (the first and last two `gpu.csv` rows, idle and outside any
  session; the first and last two `host.csv` rows, with `cpu_percent` and memory of one `spark_cpu` sf1 and one `polars`
  sf300 execute sample; the head of `schedule.json`). No aggregate by engine, host or scale was computed from them; `t16`,
  `t24` and Paper 1's Power column were not opened. The auditor rated it minor.
- **Seen by the author before the lock:** the author approved and published Paper 1, which reports per-engine GPU power
  (median share of the board power limit at SF300) and describes the energy proxy; the author is therefore treated as having
  seen those aggregates. The committed `t16`/`t24` tables were not opened by the assistants. Archived-data analyses are
  exploratory only.
- **No pilot of study queries on instrumented hosts.** If the author wants one, it is listed in the lock text (dates, hosts,
  what was inspected) and its data are excluded. This repeats the correction of Paper 1 amendment A5 (an undisclosed
  exploratory pilot preceded a lock worded as "before any data existed").

**7.3 After the lock.** No look at engine-versus-engine or host-versus-host energy, power or cost aggregates until all
rounds have completed or been stopped by the rules above. Allowed: the §4 QA views. Stopping, repeating or modifying a round
is never based on observed speed, energy or cost. Post-hoc analyses are labelled exploratory with date and what was seen.

**7.4 Amendments.** Append-only in §12 with date, reason, change **and the outcome data seen at that time** ("none", "QA
only", "aggregates of host X"). An amendment made after any aggregate was seen cannot make the changed element confirmatory.

## 8. Disclosures
- **AI tools.** This design was drafted by an AI assistant (Anthropic's Claude via Claude Code) at the author's direction;
  commits carry a `Co-Authored-By` trailer. The author reviews and approves it and is responsible for it. AI assistants may
  also help write harness and analysis code and manuscript text; the paper will list all uses. Earlier project designs were
  drafted with OpenAI Codex (Paper 1, amendment A5).
- **Funding and conflict of interest.** Lambda provided $10,000 and AWS $6,000 in compute credits for Paper 3 (they did not
  cover the Paper 1/2 runs); neither sponsor had any role in design, analysis, interpretation, review or the decision to
  publish. The study reports Lambda prices and compares Lambda GPU hosts with GCP CPU hosts, so the sponsor relationship is
  disclosed in every Paper 3 output. Cost uses list prices (P-NEW) regardless of credits; P-BILLED records that billing was
  credit-funded.
- **Relation to Papers 1 and 2.** Paper 1 (`docs/H100_EXPERIMENT_DESIGN.md`, locked 2026-10-29, amendments A1-A5) reports
  timing on four Lambda hosts; A5 discloses the exploratory pilot that preceded its lock; it already published per-engine
  GPU power summaries (§6.1). Paper 2 re-examines the CPU baseline and gives the reporting checklist behind B1-B3. Paper 3
  reuses the workload, engines, gates and Williams design, collects **new** data with new instrumentation, and does not pool
  it with Paper 1/2 data; archived data enter only as labelled in §6.
- **Workload and TPC notice.** Copied from `paper/macros.tex` (`\tpcdisclaimer`) with the author's workload name
  (2026-11-11), "the TPC-H-derived workload", in place of `\workloadname` ("H100 study workload"):

  > The TPC-H-derived workload is derived from the TPC-H Benchmark and as such is not comparable to published
  > TPC-H Benchmark results, as the TPC-H-derived workload results do not comply with the TPC-H Benchmark
  > Specification.

  Deviations as in Paper 1 (selected adapted queries, Float64, Parquet inputs, no refresh, power or throughput tests). The
  energy figures here are **not** TPC-Energy metrics and not comparable to them. TPC written-permission status for the
  benchmark name: the request (`docs/TPC_PERMISSION_REQUEST.md`) has not been sent yet (the author will send it later).

## 9. Budget (estimates; prices and hours are inputs, not results)
**Prices.** Lambda rows: P-NEW (§2.6; no spot price is listed). GCP rows: the P-NEW GCP table. AWS fallback: Price List API,
us-east-2. Prices are re-checked on the provider pages before the lock.

**Hours.** Paper 1 wall times (A100 11h24m, H100 9h15m, GH200 6h47m, B200 9h07m: 4 main rounds plus a 64-session side study
that Paper 3 omits). GPU host = (2 x that time + 3 h set-up for two launches) x 1.2 (one repeated round or failed launch).
C-GCP = (2 x 9.25 h x 0.75 (CPU engines assumed <= 75% of a four-engine round) + 2 h set-up + 0.5 h boot) x 1.2 = 20 h.
**Instrumentation overhead** (P5 confirms): the §2.2 brackets cost 180 s per session (256 sessions per GPU host, 128 on
C-GCP) and the window rule at most 180 s more per session where queries run under 10 s (half the sessions), x 1.2: 23.0 h
per GPU host, 11.5 h on C-GCP. Checked against the window rule as reworded on 2026-11-15 (§2.1): the 180 s is 6 queries x 3
trials x 10 s (both suites have 6 queries), and for runs of equal length T the time a window adds over a single run is
(n-1)T, which is under 10 s, so the estimate, which already assumed full 10 s windows, is unchanged.

| Item | Hours | USD/h | USD (est.) |
|---|---|---|---|
| G-A100 / G-H100 / G-GH200 / G-B200 | 31.0 / 25.8 / 19.9 / 25.5 | 1.99 / 4.89 / 2.59 / 7.99 | 62 / 126 / 51 / 204 |
| Instrumentation overhead, 4 GPU hosts (23.0 h each) | 92.2 | 17.46 summed | 402 |
| Remaining probes and validation P5-P7: 1.5 h x 4 GPU host classes | 6.0 | 17.46 summed | 26 |
| **Lambda subtotal (P-NEW)** | **200.4** | | **871** |
| C-GCP base run (20 h); overhead and probes P5, P6 (13.5 h incl. boot) | 33.5 | 12.701568 | 425 |
| C-VM time check (baseline SF10, SF30, SF300, 2 rounds; incl. set-up and SF300 generation) | 6 | 2.910776 | 17 |
| **GCP subtotal** | **39.5** | | **443** |
| Storage, snapshots, egress (Hyperdisk computed at ~9 for 39.5 h; the rest assumed) | | | 50 |
| **Core total** | | | **~1,364** |

At P-OLD the Lambda subtotal is about $778 and the core about $1,271. Fallback hosts: `c3-standard-` or
`c3-highcpu-192-metal` give a GCP subtotal of about $342 or $308 (core $1,263 or $1,229); AWS (`m7i.metal-24xl`,
`r7i.8xlarge`) about $175 (core $1,096). The core is about 14% of the ~$10k budget (as relayed; confirmed by the author
2026-11-11) and 43-54 h of wall time per GPU host over two launches. A +-30% price change moves it by about +-$410.
Availability and calendar time bind, not cost.

**Clarification, 2026-11-15: no separate between-session cool-down.** The >= 60 s cool-down between sessions (§3.2) is met
by the §2.2 brackets already costed above: between the last query of one session and the first query of the next there are
always >= 150 s without query load (that session's 60 s settle and 30 s closing idle window, then the next session's 60 s
settle before its opening idle window; this lower bound does not count the next engine's start-up or the opening window
itself). The harness has a `--session-cooldown-seconds` flag; the campaign hosts set it to 0. The estimate above is
therefore unchanged (core total ~$1,364). The C-VM sessions have no brackets and use a 60 s cool-down (§3.1): 12 sessions (2
engines x 3 scales x 2 rounds), 11 x 60 s = 0.2 h inside the 6 h C-VM row, also unchanged.

**Probe spend so far (approximate; not part of the core total above, which is not re-estimated here).** GCP P5, 703 s of
`c3-highmem-192-metal` at list price: $2.48. Lambda, billed at the API price (for the runs up to 2026-11-15 the 2026-11-16
prices were not yet in force): the first lost H100/A100 attempt about $5.76 (team lead's figure, no logs kept), the H100 and
A100 rerun about $8 (the launch-to-terminate times in the run logs times the API prices give about $7.1-7.4), GH200 about
$1.40 (35 minutes at $2.29/h, about $1.34-1.38 from the logs), the 1x A100 source check of 2026-11-15 about $0.15-0.20
(4.3-5.9 minutes at $1.99/h from the run log), and the GH200 P6 rerun with source check of 2026-11-15 about $0.90 (about 23
minutes at $2.29/h; 22.4-23.5 minutes from the run log, $0.86-0.90). The C-GCP P6-CPU probe of 2026-11-16 adds about $4.24
(20.0 minutes, 00:09:34 to 00:29:35 UTC, at $12.701568/h, list price, disk negligible). The partial B200 battery of
2026-11-16 ran 3.17 h from launch (05:27:52 UTC) to the termination request (08:37:49) and 3.47 h to the end of the run
log's unconfirmed wait (08:55:53), against the 1.5 h per class of the table row "Remaining probes and validation P5-P7": by
date the P-NEW price of $7.99/h (in force from 2026-11-16 00:00 UTC) applies, giving about $25-28; at the pre-change $6.99/h
it would be about $22-24. The run log does not record the price charged, and the end of the billing depends on the manual
termination check. Total about $48-51 at $7.99/h ($45-47 at $6.99/h). The table rows "Remaining probes and validation P5-P7"
and "overhead and probes P5, P6" overlap with this spend and were not reduced; on C-GCP, P5 and P6-CPU together used about
0.5 h of the row's roughly 2 h probe allowance. The B200 rows (base run, its share of the overhead and probe rows) are spent
only if B200 is admitted (§3.1).

## 10. Threats to validity
- **Virtualised counters.** Lambda H100 and 8x A100 guests are KVM VMs with a working NVML counter and no CPU energy (P1,
  P4), so x86 GPU-host CPU/DRAM energy is unmeasured (§2.5); the GH200's counter includes the Grace CPU (module scope); the
  1x A100 (EPYC 7J13) and the GH200 (Neoverse-V2) are virtualized guests with no separable CPU-energy source (2026-11-15; on
  the GH200 CPU energy is only inside the module counter), and the B200 (Xeon 8592+) is a virtualized guest with no
  CPU-energy source either (2026-11-16, §2.4). RAPL covers package and DRAM only (no PSU, fans, storage, network), and no
  external wall-power reference exists in a public cloud. P3's virtualisation verdict was "ambiguous" (no hypervisor flag,
  but DMI says Google); its counters advance and agree between powercap and perf, and G6 re-checks them every session.
- **Scope asymmetry and N1.** S1 and S2 are different slices of a node; §2.5 limits the claims, and N1 is untested: the
  tipping points show its weight without testing it. The GH200's S1-module is a third scope (Grace CPU + GPU + memory): its
  energy is not like-for-like with the board-only S1 of the other GPU hosts, and X7 is whole-module energy, not node energy.
- **Over-provisioned CPU-only host.** 96 cores and 1.5 TB DRAM: the pinned socket's package and DRAM idle high (§3.1)
  against light work, and DRAM includes idle capacity a right-sized node lacks. This inflates `E_C`, a bias against the CPU
  engines: cross-class "CPU better" findings are conservative, while "GPU better at the measured scope" may partly reflect
  host size. Package and DRAM are reported separately and above-idle beside total for exactly this reason; B1 prices a
  right-sized VM while the energy comes from the large host.
- **Sensors and idle draw.** `power.draw` is a trailing average on H100, GH200 and B200 and instantaneous on A100; the
  counter avoids this, but its ~100 ms update still limits short runs (§2.1). On A100 the short-run error is one-sided
  (about 4-12 J per run) and was measured on a synthetic pulse at one power level; if the per-run excess does not depend on
  power, a run drawing less power has a larger relative error than the P6 figure (not tested). Back-to-back window runs are
  warmer than Paper 1's interleaved trials. Idle power is large relative to light work, so total and above-idle can give
  different break-evens (both fixed up front); it depends on temperature, persistence mode and C-states (recorded, not
  controlled) and, measured in P5, on whether a CUDA context is open (+68 W on H100, +47 W on GH200, +5.6 W on A100, §2.2);
  host CPU load moved the H100 board counter by 3-5%.
- **Tenancy and capacity.** Lambda hosts and network storage are shared; noise is bounded only by rounds and two launches;
  bare metal removes CPU co-tenants, not storage effects. Metal capacity was thin at P3; the fallback changes DRAM and
  price, hence the shape is fixed before round 1.
- **Price volatility.** Lambda raised three of four relevant prices (+13% to +14%, A100 unchanged) on 2026-11-16, and its
  API and page disagreed on 2026-11-11. Cost is a snapshot: P-NEW primary, P-OLD and the billed price sensitivities; the
  factorised price/time reporting is the durable result. GCP prices are official list prices as of 2026-11-11 (§2.6). Spot,
  reserved and committed-use prices are not measured; billing minimums are ignored.
- **Baseline construction and utilisation.** B1 prices a 44 vCPU VM for 26 threads and times a pinned metal host; C-VM
  bounds the time assumption, not the vCPU surplus. The metal Xeon 8481C, the H100 host's 8480+ and the B200 host's 8592+
  are one family, not one SKU; the A100 hosts have AMD EPYC, confounding A100 CPU-engine comparisons; the Arm GH200 has no
  CPU-only Arm baseline. One query at a time on a whole instance: real fleets share, queue and idle, and the 26 thread cap
  leaves vCPUs idle on A100, GH200 and metal (their energy is on the machine, not attributed).
- **Not blind; scope.** The author knows Paper 1 timings (Lambda cost ratios are largely predictable) and per-engine GPU
  power aggregates were already published (§6.1); the pre-registration protects choices, not ignorance. Four engines, one
  TPC-H-derived workload, five scales, one provider per host class: no claim for other engines, formats, clusters, CPU
  vendors or query mixes. Not TPC results.

## 11. Author decisions (answered 2026-11-11; item 8 added 2026-11-15)
**Standing limitation.** In this study "a GPU engine uses less energy than its CPU counterpart" **cannot be claimed at any
scale on the x86 hosts (H100, A100, B200)**, with or without N1. Node energy of a GPU-engine run needs the host CPU, DRAM
and platform energy of the GPU host, and P1 (Lambda H100 SXM5) and P4 (Lambda 8x A100) found Lambda GPU hosts to be KVM
guests with no RAPL, MSR, IPMI or working psys counter; the one way to measure it, a bare-metal GPU host, was dropped by the
author. GPU-board energy is a lower bound on the node's, so "the CPU engine wins" can survive under N1 and "the GPU engine
wins" cannot. **Exception, GH200 only (author decision 2026-11-15, item 8.3):** the GH200 counter covers the whole
Grace-Hopper module (P7, §2.3), so a CPU engine's and a GPU engine's energy are measured on one scope, S1-module, and "the
GPU engine uses less (equal, more) whole-module energy than its CPU counterpart on the GH200" may be reported for that scope
(X7, secondary), worded as whole-module energy, not node energy, and not generalised to x86 hosts. The 1x A100 and the GH200
expose no separable CPU-energy source (source checks of 2026-11-15, §2.4; on the GH200 CPU energy is only inside the module
counter), so no author decision is triggered; the B200 source check (2026-11-16) finds no CPU-energy source either, so none
is triggered there.

All answered by the author on 2026-11-11: items 1-6 are the approved defaults ("approve defaults"), item 7 the final
answers.
1. R = 8 rounds on two launches (R = 4 only if one square completes, §3.2).
2. Primary idle rule *total*, above-idle always shown; primary CPU cost baseline B1 = `c3-highmem-44` with the 200 GiB RAM
   floor; primary price table P-NEW with P-OLD as sensitivity (§2.6).
3. Equivalence margin +-10%; keep SF1 in the grid; 10 s window and `t_min` = 1 s (§2.1, confirmed by P6 before the lock;
   refined to a per-host value by item 8.1); G8 thresholds (§4): median onset lag within +-0.4 s, drift <= 0.2 s, onset at
   `utilization.gpu` >= 10%.
4. Idle brackets (§2.2) per session as written (12.8 h per GPU host, ~15 h with the 1.2 margin, ~$270 across the four).
5. Probes P5-P8 authorised, run by the team lead's agents under the lease rules.
6. N1 accepted as a labelled assumption; the wording of §2.5, the tipping points (`Delta*`) and the ~$10k cap (§9)
   confirmed.
7. Funding and sponsor role (§8), workload name and TPC status (§8.3), and exposure to archived power aggregates (§7.2).
   Note, not a decision: whether arXiv:2312.02741 applies to our drivers (§2.3) does not matter for the primary counter.
8. **Decisions of 2026-11-15, after the pre-lock probes** (author Daniel Ejiofor; recorded in substance):
   1. **`t_min` per host.** `t_min` is set per host by the pre-stated §2.1 rule (the smallest per-run length whose median
      |error| <= 2% and every longer length also is), applied to the windows-mode P6 result, with the locked 1 s default as
      a floor: `t_min(host) = max(1 s, measured)`. So H100 1 s, A100 2 s, GH200 pending its P6 rerun [drafter's update:
      rerun in, 1 s, §2.1], B200 from its own P6 before any B200 session. The A100's isolated (single-run) value of 5 s is
      never binding, because a single-run window (`n = 1`) only occurs when the per-run time is >= 10 s; checked against the
      window rule (as reworded 2026-11-15: `n = 1` only when the first run takes >= 10 s) and confirmed in §2.1.
   2. **Lock timing and B200.** The lock happens once the GH200 P6 rerun is in [drafter's update: met 2026-11-15]. B200 is a
      conditional host: its sessions may run only after its semantics, P5, P6 and G8 pass under the same rules; if it never
      passes or never gets capacity before the campaign ends, B200 is reported as not measured, with no substitution by an
      8-GPU AWS or GCP B200 machine (different host classes). Mechanics: §3.1, §7.1. The numeric admission threshold in §3.1
      (counter / integral within 0.98-1.02) is the drafter's specification of "the same rules", fixed 2026-11-15 before any
      B200 data; the author confirms it at the lock.
   3. **GH200 CPU-versus-GPU energy at module scope.** A planned comparison of CPU-engine versus GPU-engine energy per query
      on the GH200 at module scope (scope tag S1-module), clearly labelled whole-module energy: a CPU engine's number
      includes the idle GPU's draw and a GPU engine's number includes the Grace CPU's. Same estimators, rounds, window rule,
      gates and equivalence margin as the existing comparisons. It does not generalise to x86 hosts. Its classification as
      secondary (X7, RQ6) is the drafter's reading of §5.4, not an author decision: the confirmatory set X1-X3 is defined
      with the cross-class energy reference, and this contrast is a different one; the author may reclassify it before the
      lock.

## 12. Amendments
None.

### Pre-lock changes, 2026-11-15 (not amendments: the design is not locked, §7)
Made after the pre-lock probes of 2026-11-11 to 2026-11-16 (§7.2) and the author decisions of §11 item 8. Outcome data seen:
synthetic-load probe results only (power-interface behaviour); no study query was run and no engine-versus-engine energy,
power or cost aggregate exists. Gate thresholds (G8, G10), the equivalence margin, the estimators and the primary idle
definition are unchanged; `t_min` is set per host by the rule stated before the probes (author decision, item 8.1). What
changed:
- **Status line, RQ table:** the status line first said the lock waits for the GH200 P6 rerun, then (lead) also for the
  C-GCP P6-CPU probe, and now reads "ready to lock; awaiting the author's go-ahead" (see the last bullets of this list);
  B200 conditional; RQ1 pointer, RQ4/RQ5 wording for GH200 and B200, new RQ6 (GH200 module scope, secondary).
- **Campaign-driver decisions (lead's decisions 2026-11-15, all pre-lock; the author may object before the lock):**
  - **§3.2 CPU-only letters:** the two CPU engines take A and B in the order their letters appear in the GPU-host assignment
    from the same seed (no operator choice); C-VM uses the same letters with orders `AB` and `BA`.
  - **§3.1 C-VM protocol:** the draft stated no pinning for the VM; now 26 vCPUs = 13 physical cores plus SMT siblings as on
    the metal host, the 10 s window rule, time only, and a 60 s cool-down between its sessions (no §2.2 brackets there, so
    §3.2 applies literally).
  - **§2.2:** the runner's pre-session bare idle baseline is off in Paper 3; `P_idle` comes only from the engine-ready
    windows; nothing budgeted.
  - **§2.1 `t_min(C-GCP)`:** set by a RAPL resolution probe (P6-CPU) of the campaign's 200 ms path against direct
    `energy_uj` reads on synthetic CPU pulses, same 2% rule, `max(1 s, measured)`. First worded as a lock condition, then as
    a pre-first-session condition (last bullets of this list); the probe finally ran before the lock, 2026-11-16:
    `t_min(C-GCP)` = 2 s.
  - **§7.1, §3.2 lock date:** the UTC date of the annotated `paper3-design-lock` tag (tagger date); the Zenodo deposit is
    made the same UTC day.
  - **§3.2, §5.4 partial squares:** confirmatory estimates use complete squares only; one complete square gives R = 4 from
    it; rounds of an incomplete square are a sensitivity only; a host with no complete square is reported descriptively.
  - **§4 G6:** sampler liveness is judged per session, on the samples inside the session's stamps.
- **§3.2, §9 (cool-down clarification; lead's decision 2026-11-15):** the >= 60 s between-session cool-down of §3.2 is met
  by the §2.2 brackets (>= 150 s without query load between sessions), so no separate sleep is run
  (`--session-cooldown-seconds` is 0 in the campaign) and the §9 estimate is unchanged (core ~$1,364). A separate 60 s sleep
  before every session but the first was costed (about 5.1 h per GPU host and 2.5 h on C-GCP with the 1.2 margin, about $121
  at P-NEW) and rejected: dropping it avoids that cost with no measurement benefit. The author may object before the lock.
- **§2.3 (GH200 power limits):** the discrepancy bullet was made factual with the parsed `nvidia-smi -q -d POWER` values
  (GPU Current 700 W, GPU Default and Max 900 W, Module current 1000 W; `--query-gpu power.limit` of 900 W equal to the
  default and max limit), and then settled by the GH200 source check of 2026-11-15 (last bullets of this list).
- **§2.1 (window rule; technical correction by the team lead, 2026-11-15):** `n` is no longer set in advance from the
  warm-up time (`n = clamp(ceil(10 s / t_hat), 1, 50)`); runs continue until the window's summed timed time reaches 10 s or
  50 runs have been made. Reason: the warm-up is the cold first run (JIT, plan compilation, caches), so `t_hat` overstates
  the steady per-run time and windows fell short of 10 s, which would have lost those cells as "below measurement
  resolution" (found in code review; a synthetic local run gave a 0.056 s window against a 3 s target). No study data are
  involved. The 10 s target, the cap of 50, the resolution rule and G9 are kept; `n` depends only on elapsed time and may
  differ between trials; `t_hat` is recorded for QA only; the P6 `t_min` applies unchanged (§2.1), and the §9 time estimate
  is unchanged. The author may object before the lock.
- **§2.1:** `t_min` is per host, `max(1 s, measured windows-mode value)`; P6 results per host with the invalid GH200 run
  identified and `t_min(GH200)` pending; reference field matches the counter's scope; the isolated single-run `t_min` shown
  never to bind; the rule's "every longer length" and |error| wording made explicit (same values as the first wording);
  A100 error noted as one-sided.
- **§2.2:** measured bare and context-open idle floors per host (P5), sampler overhead and idle stability; above-idle is
  reported for CPU engines on GH200.
- **§2.3:** counter validation per host (P7), `power.draw` semantics per host, GH200 counter scope = module (S1-module for
  all GH200 counter values), cross-check on GH200 uses `module.*` fields.
- **§2.4:** perf cross-check wording corrected (Joules versus runtime column), RAPL-read path verified on the real host,
  CPU-energy probe results restated per host class (the "not measurable on GH200, B200, 1x A100" sentence removed).
- **§2.5:** scope matrix split into x86 GPU hosts and GH200; mixed-scope note for X4; new GH200 module-scope comparison (X7).
- **§3.1, §3.2:** G-GH200 and G-B200 rows; B200 conditional-host rules; C-GCP idle floor and NUMA topology and binding
  evidence; controls bullet states the P5 overhead and that KVM is verified only on H100 and 8x A100.
- **§4:** G9 names the per-host `t_min`; G8 probe results per host (A100 marginal), thresholds unchanged.
- **§5.1, §5.3, §5.4:** X7 (secondary) defined; break-even rule extended to it at module scope; confirmatory set unchanged.
- **§7.1, §7.2:** lock timing and B200 handling; probe list updated from "planned" to done and pending.
- **§9:** probe spend to date (approximate) and the conditional B200 rows; the cost estimate itself is not revised.
- **§10:** threat bullets updated (module scope, power.draw semantics, A100 one-sided error, idle floor and CUDA context).
- **§2.3, §4 (G11):** the GH200 power-limit discrepancy recorded; the `nvidia-smi -q -d POWER` text is logged at session start
  and end.
- **§3.1, §11 item 8.2:** the B200 admission rule gets a numeric threshold (counter / integral within 0.98-1.02 on the 10 s
  idle and 20 s load windows), fixed before any B200 data.
- **§2.4, §2.5, §3.1, §3.2, §7.2, §9, §10, §11:** the 1x A100 CPU-energy source check (2026-11-15) is recorded: a
  virtualized EPYC 7J13 guest with no CPU-energy source, so "not measurable" is now shown for it. Wording "no source check
  run" for the 1x A100 removed; the 1x A100 is added to the verified KVM guests.
- **§7.2:** harness readiness (window rule, `gpu.csv` power fields, scope tags, scope-aware description, synthetic smoke
  test).
- **§11:** standing limitation gains the GH200 exception; item 3 points to item 8; new item 8 (the three 2026-11-15
  decisions).
- **GH200 P6 rerun and source check folded in (2026-11-15; §2.1, §2.3, §2.4, §2.5, §3.2, §4 G9 and G11, §7.2, §9, §10,
  §11):** the rerun against `module.power.draw.instant` gives a measured `t_min` of 1.0 s in both modes, so `t_min(GH200)` =
  1 s (the first GH200 P6, scored against the wrong field, is superseded); the CPU-energy source check finds a virtualized
  Neoverse-V2 guest with no separable CPU-energy source (CPU energy only inside the module counter), so no author decision
  is triggered; the enforced GPU power limit is 700 W (`enforced.power.limit`, = the `-q` Current Power Limit), and G11
  records it. The copied `power_telemetry.py` differed from HEAD 0cbb2a7 (diff archived; stated in §2.1). Probe spend about
  $0.90. Outcome data seen: synthetic-load probe results only.
- **Lock condition changed (lead's decision 2026-11-15; the author may object before the lock):** with the GH200 P6 rerun
  in, the lock waits for nothing else. `t_min(C-GCP)` became a pre-first-session condition like B200's (to be run on a C-GCP
  instance, possibly the campaign's own first launch, with its results, `t_min(C-GCP)` and the `paper3_hosts.json` value
  recorded in an append-only §12 amendment before that session), because `c3-highmem-192-metal` was stocked out in every US
  zone (team lead's report, not archived). The status line then read "ready to lock; awaiting the author's go-ahead".
  **Superseded 2026-11-16:** capacity appeared in us-east4-c and the P6-CPU probe ran before the lock (next bullet), so that
  condition is satisfied; only B200 admission remains a post-lock condition.
- **C-GCP P6-CPU result folded in (2026-11-16; §2.1, §4 G9, §7.1, §7.2, §9; lead's request, last edit before the lock):**
  `t_min(C-GCP)` = 2 s (windows-mode median absolute error 20.7 / 9.8 / 4.9 / 2.6 / 1.15 / 0.40% at 0.1 / 0.25 / 0.5 / 1 / 2
  / 5 s; measured `t_min` 2.0 s in both modes), on `c3-highmem-192-metal` in us-east4-c, the shape of the design's
  us-east4-a. The errors are one-sided (negative in every window), as the A100's are (positive); this is recorded, not
  corrected, and no threshold is moved. Probe spend about $4.24 (total about $23). The status line stays "ready to lock;
  awaiting the author's go-ahead" and now names B200 admission as the only post-lock condition. Outcome data seen:
  synthetic-load probe results only.
- **Partial B200 battery folded in (2026-11-16; lead's request, final edit before the lock; §2.1, §2.2, §2.3, §2.4, §2.5,
  §3.1, §3.2, §4 G8, §7.1, §7.2, §9, §10, §11):** a first B200 battery ran before the lock (Lambda `gpu_1x_b200_sxm6`,
  australia-east-1, 05:27-08:38 UTC, driver 570.148.08). Four of five remote SSH steps returned 255 (connection lost: the
  driving machine slept throughout, per the team lead's check of the local power log, not archived); `p6.json` was not
  collected, only its 20 ms stream, so `t_min(B200)` is unknown (§2.1 table: pending). Completed: semantics (`power.draw` =
  trailing average; counter / `power.draw.instant` integral 0.9986 idle and 1.0049 load, inside the 0.98-1.02 admission
  threshold, so board scope, S1-counter), P5 (bare idle about 143 W, context open about 192 W, poller overhead 0.010 W), G8
  (pass, median 0.195 s, p90 0.305, drift -0.039), and the CPU-energy source check (KVM guest, Xeon 8592+, no source);
  enforced power limit 1000 W. B200 stays a conditional host: admission needs a valid P6 rerun, and the other admission
  conditions are met by this run and may be reused (lead's decision 2026-11-16; the author may object before the lock).
  Probe spend about $22-28 (3.17-3.47 h; price charged not recorded), total about $48-51 at $7.99/h ($45-47 at $6.99/h). The run log could not confirm
  termination by 08:55:53 UTC. Thresholds, gates and the status line are unchanged. Outcome data seen: synthetic-load probe
  results only.
