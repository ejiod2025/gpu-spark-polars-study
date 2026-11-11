# Paper 3 — energy and cost per query, CPU versus GPU dataframe engines — pre-registered design

Status: **DRAFT — not locked; requires author approval.** Drafted 2026-11-11 for Daniel Ejiofor (Boston University,
ejiod@bu.edu), revised after probes P1-P4 (§7.2). Until the lock (§7) this text may be edited freely. After it, sections
1-11 are never edited; changes are dated, append-only amendments in §12 (rule of `docs/H100_EXPERIMENT_DESIGN.md`, extended
in §7.4). `[AUTHOR: ...]` marks open decisions (list in §11).

Aim (author's words): rigorous, reproducible evaluation methods and tools that help U.S. organizations choose cost- and
energy-efficient CPU or GPU engines for analytical data processing. Paper 3 contributes a measurement protocol and
break-even scales, not a vendor ranking.

## 1. Research questions
Same TPC-H-derived workload, engines and integrity gates as Paper 1 (not a certified TPC-H run; §8.3).

| Id | Question | Type |
|---|---|---|
| RQ1 | Energy per query of a GPU engine versus its CPU counterpart (Polars GPU/CPU, Spark GPU/CPU): scope-tagged figures, plus a node-level direction under assumption N1 (§2.5) | confirmatory |
| RQ2 | Cost per query (on-demand price x time), same contrasts, three CPU baselines (§2.6) | confirmatory |
| RQ3 | Break-even scale: smallest scale where a GPU engine is cheaper than the best CPU engine, per suite (§5.3); for energy only a lower bound under N1 | confirmatory |
| RQ4 | GPU generation: GPU-board energy (S1) and cost per query of the GPU engines on A100, H100, GH200, B200 (host-confounded, as Paper 1 A2/A3) | confirmatory estimates, descriptive |
| RQ5 | CPU platform and idle-GPU tax: time and cost of the CPU engines on the CPU-only host versus the GPU hosts; GPU-board energy burned idle during CPU-engine runs (§2.6, X5/X6) | confirmatory estimates |
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
- **Window rule.** A measured trial is a window of `n` back-to-back runs of one query, `n = clamp(ceil(10 s / t_hat), 1,
  50)`, `t_hat` = that query's warmup time in the same session (`n = 1` when `t_hat >= 10 s`). Window energy = sum of
  per-run counter differences (gaps, cleanup and plan-explain excluded); per-run energy and time = window value / `n`. 3
  trials per query; the median is the round's value (as Paper 1).
- **Resolution.** The NVML counter updates every ~100 ms (P1) and the RAPL series every 200 ms, so each end of a run is
  uncertain by up to one update period: a 10 s window bounds this at 2% (NVML) or 4% (RAPL) for one run, and for `n` short
  runs the errors are independent in expectation and add like sqrt(n), not n. P6 measures this on pulses of known length and
  fixes before the lock `t_min`, the shortest per-run time at which the summed per-run counter error against a 20 ms
  `power.draw.instant` integral is <= 2% (median) (approved default: 1 s). A cell whose median trial has a summed window
  below 10 s or a per-run time below `t_min` is "below measurement resolution", never a number.

### 2.2 Idle baseline, both ways
Each session is bracketed by two 30 s idle windows (engine ready, no query running), each preceded by >= 60 s without load
(also a thermal reset). `P_idle` per scope = mean of the two windows' mean power. Every energy value is reported both ways:
**total** `E` = counter difference over the timed runs (**primary**: what the machine draws); **above-idle** `E_above = E -
P_idle * t`, negative values reported as they are (not clamped). For a CPU engine on a GPU host (S1 idle GPU) above-idle is
zero by construction and not reported.

### 2.3 GPU energy (scope S1: GPU board)
- **Primary:** NVML `nvmlDeviceGetTotalEnergyConsumption` (cumulative mJ), read through ctypes (`nvidia-smi --query-gpu`
  cannot return it) and differenced per run. P1 (H100): ~100 ms update, non-decreasing, within 0.17% of the 20 ms
  `power.draw.instant` integral over a pulsed synthetic load; P4: all 8 GPUs of a Lambda 8x A100. Other classes: P7.
- **Cross-check (diagnostic, not a gate):** trapezoid integral of the 200 ms `nvidia-smi` stream (`gpu.csv`, as Paper 1; now
  also `power.draw.average` and `.instant` where offered, with the driver version) over the same UTC window, as a relative
  difference to the counter, on windows >= 10 s only. Yang, Adamek and Armour (arXiv:2312.02741; SC24 per its DOI) report
  that `nvidia-smi` samples only 25% of runtime on A100 and H100 (abstract checked 2026-11-11; applicability to our drivers
  not checked; the primary source is the counter, not sampling). If the counter is unavailable, the trapezoid value is
  labelled **S1-sampled**, never pooled.
- **`power.draw` semantics:** H100 a trailing 1 s average (P2); A100 documented instantaneous (Average Power Draw N/A in P4
  and the archive; untested, P7), so archived cross-host `power.draw` is not like-for-like (Paper 1 carries the caveat).
- Scope: whole GPU board (GPU, HBM, VRM), device 0, one GPU per host; the host is excluded. On GH200 whether the counter is
  GPU-only or the Grace-Hopper module is fixed by P7 (integrals of `power.draw.instant` and `module.power.draw.instant`
  against the counter); module scope is labelled S1-module. Also logged: temperature, SM/memory clocks, throttle reasons,
  power limit. Nothing is tuned: no clock locking, power capping or governor change.

### 2.4 CPU energy (scopes S2, S3)
Source order, decided per host class by the probe report (fixed at the lock, never at analysis time):
1. **S2: RAPL** via powercap (`/sys/class/powercap/intel-rapl*/energy_uj`), the primary source is powercap per package and
   `dram` zone; `perf stat -e power/energy-pkg/,power/energy-ram/` (with `--per-socket`, verified first) or `turbostat` is a
   cross-check only. On the CPU-only host (P3: 2 sockets, `package-0`, `package-1` and one `dram` zone each, all advancing;
   perf agreed with powercap per package, but `perf -a` reported about one package, not the whole box) **S2-pinned**
   (primary) = package + DRAM of the socket hosting the engine threads, package and DRAM also shown separately; **S2-all** =
   all four zones, the idle socket reported separately, not attributed. Root only (CVE-2020-8694): the sampler runs as root,
   or `setup_h100.sh --enable-rapl-read` is applied on this single-tenant host only; the choice is recorded. The sampler
   unwraps the counter with `max_energy_range_uj`; windows containing a sample flagged `decrease` (counter went backwards)
   or `gap` (> 30 s since the previous sample) are invalid.
2. **S3: whole node** via IPMI/DCMI (`ipmitool dcmi power reading`, ~1 s), only if a BMC is reachable (none: P1, P3, P4).
3. **None.** The cell reports time and cost and the text "CPU energy not measured on this host". **No model value is ever
   placed in a confirmatory table.** Every energy value carries a tag `S1-counter | S1-sampled | S1-module | S2-pinned |
   S2-all | S3 | none`, and the analysis code raises an error for an untagged value. Model values (EX2: host-CPU power as a
   function of CPU utilisation, fitted on the CPU-only host's RAPL and applied to a GPU host's `host.csv`) appear only in
   files and figures labelled MODEL.
- **Probe results.** P1 and P4: Lambda KVM guests, no powercap, MSR or IPMI, `energy-psys` reads 0 J: **CPU energy is not
  measurable on these Lambda classes** (GH200, B200, 1x A100: P7; Grace has no x86 RAPL). P3: RAPL works, no BMC.

### 2.5 Scope matrix and what may be compared
| Host class | Engines | Energy reported | Cost |
|---|---|---|---|
| Lambda GPU host (A100, H100, GH200, B200; KVM VMs) | GPU engines | S1-counter, GPU-board-only (CPU energy not measurable: P1, P4) | host price |
| same host | CPU engines | S1 *idle GPU* (the GPU paid for while idle), GPU-board-only | host price |
| CPU-only bare metal (GCP `c3-highmem-192-metal`; fallbacks §3.1) | CPU engines | S2-pinned, S2-all | right-sized VM B1 (primary), as rented B2 (§2.6) |

- **Within-class** energy contrasts (GPU vs GPU across hosts; CPU vs CPU) use one scope. **No common scope exists across
  classes:** no GPU-engine run is measured at node level (P1, P4; the bare-metal GPU host option was dropped by the author),
  so cross-class contrasts compare `E_G` (S1-counter, GPU board) with `E_C` (S2-pinned, package + DRAM), each a node-energy
  lower bound.
- **Assumption N1** (an assumption, not a fact; not measured, not tested). Per query, node energy = measured + unmeasured.
  GPU node: `E_G` plus `U_G` (host CPU, DRAM, platform). CPU node: `E_C` plus `U_C` (platform; the other socket belongs to a
  larger machine than the right-sized B1 node and is shown as S2-all). **N1: `U_G >= U_C`.** Plausible because the GPU node
  adds a host CPU and DRAM running the same engine process to the same kind of platform, but it may fail. Every cross-class
  energy statement is worded "under N1", with the assumption-free scope-tagged figures (`E_G`, `E_C`, S2-all, `rho = E_C /
  E_G`) always shown beside it. Under N1:
  - **Survive:** (i) the direction "the GPU engine uses *more* node energy", claimed when the cell is "CPU better" (§5.3);
    (ii) a **lower bound on the energy break-even scale** (the break-even on the measured scopes, §5.3, because node-level
    "GPU better" implies measured-scope "GPU better"); (iii) all time and cost claims; (iv) scope-labelled within-class
    claims, never worded as node energy; (v) the idle-GPU energy a CPU engine burns on a Lambda host.
  - **Do not survive:** "the GPU engine uses *less* or equal energy" at any scale (§11); an energy break-even scale (only
    its lower bound); the magnitude of any cross-class ratio; "no difference" across classes. Spark GPU versus Polars GPU is
    GPU-board only; their host-CPU draw differs and is unmeasured. A modelled host-CPU term (EX2) may appear only as
    exploratory MODEL. If N1 is not accepted, only (iii)-(v) survive. Cost is fully measured either way.
- **Tipping point (pre-registered, every cross-class energy cell).** Node energies are equal iff `U_G - U_C = Delta*`, where
  `Delta* = E_C - E_G`. `Delta* < 0` ("CPU better" at the measured scope): N1 excludes it, so the GPU engine uses more node
  energy under N1. `Delta* > 0`: the GPU node uses less only if its unmeasured excess is below `Delta*`; the claim is not
  made, and `Delta*` says how large that excess would have to be to erase the measured advantage. `Delta*` comes from
  per-round suite totals (queries both classes ran), reported as mean and §5.2 t-interval over rounds, in Wh per suite run
  and in W (over the GPU suite time), beside the measured S2 idle power of one socket and the S1 idle GPU power for scale.

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
  billed, so cost ratios depend only on measured time and a table fixed before any outcome exists. The lock is planned after
  2026-11-16, so P-NEW is the expected billed price; a changed console value is corrected before the lock.
- **P-OLD (sensitivity):** Lambda prices before 2026-11-16, per the API on 2026-11-11: H100 SXM5 4.29, GH200 2.29, B200
  6.99, A100 SXM4 1.99. **P-BILLED (third sensitivity):** `campaign.json` records per instance the provider's price at
  launch, instance id, launch and terminate UTC times; the invoice is reconciled against hours x price and a gap above 2% is
  reported. Cost results are reported under P-NEW (primary), P-OLD and P-BILLED; which is primary is not revisited after
  data exist.
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
| G-GH200 | Lambda `gpu_1x_gh200` (GH200 96 GB, 64 vCPU Arm, 432 GiB) | 4 engines | 2.59 (2.29) |
| G-B200 | Lambda `gpu_1x_b200_sxm6` (B200 180 GB, 26 vCPU, 360 GiB) | 4 engines | 7.99 (6.99) |
| C-GCP | GCP `c3-highmem-192-metal`, us-east4-a (2x Xeon Platinum 8481C, 96 cores / 192 threads, 1,536 GB), engines pinned to 26 vCPUs on socket 0 | 2 CPU engines, RAPL (P3) | 12.701568 (same) |
| C-VM | smallest GCP C3 VM with >= 26 vCPU and >= 200 GiB (§2.6; `c3-highmem-44`) | time check for B1: baseline SF10, SF30, SF300, 2 rounds, time only | 2.910776 (same) |

- The four GPU hosts are Paper 1's, so energy and cost can be set beside its timings. Only instance types returned by the
  Lambda API at the lock (response archived) are used (it lists no H200, GB200 or B300); a type missing at the lock is
  dropped by amendment, not substituted. Hosts are never pooled (Paper 1 A2). Stack and engine settings are Paper 1's
  (A2-A4: versions, `a100-parity`, 26 threads, strict GPU guard), recorded in `campaign.json` with instance type, CPU model,
  hypervisor, kernel, driver, RAM, storage type, probe-declared energy sources and the design commit (§7).
- **CPU-only host choice.** At P3 (~15:00 UTC) `c3-highmem-192-metal` had capacity in us-east4-a only; the `c3-standard-`
  and `c3-highcpu-192-metal` shapes were stocked out in every US zone. Fallback order, first obtainable: (1)
  `c3-highmem-192-metal` in another US zone; (2) `c3-standard-192-metal` (768 GB, 9.677184); (3) `c3-highcpu-192-metal` (512
  GB, 8.669056); (4) AWS `m7i.metal-24xl` (96 vCPU, 384 GiB; last resort, probed first as P8). The choice is fixed before
  round 1 and recorded in `campaign.json` (provider, shape, zone, RAPL zones); shapes differ in populated DRAM, so data from
  different shapes are never pooled. Host loss mid-campaign counts as "one square only" (§3.2). No AMD platform.
- **Pinning.** Process CPU affinity (`os.sched_setaffinity`, set by the runner before any child starts) to 13 physical cores
  plus SMT siblings (26 vCPUs, as the H100 VM), all on socket 0, plus NUMA memory binding to the pinned socket (`numactl` or
  libnuma; recorded; fail-fast if unavailable); socket 1 stays idle and its package + DRAM is the measured idle floor (P3
  QA: the four RAPL zones sum to ~430 W idle, CPU and DRAM only, so a lower bound on wall power; packages ~175 W, DRAM ~38 W
  per socket). Placement and cross-socket traffic are recorded, not attributed. C-GCP has network storage (Hyperdisk only),
  not Lambda's local SSD, so Spark spill I/O may differ (recorded).

### 3.2 Workload, rounds, controls
- **Workload.** Baseline suite q01, q03, q06, q10, q12, q14 at SF1, SF10, SF30, SF100, SF300; complex suite (v2) at SF30,
  SF100, SF300. Inputs, generation parameters and the `verify_generated_inputs.py` gate as Paper 1 §3; Float64
  normalisation. Queries are never rewritten. Pre-declared Polars GPU gaps (cx_q13, cx_window) and the A1
  coverage-versus-integrity classification carry over. The scale grid is Paper 1's (SF1 is the small-data end where fixed
  GPU/JVM start-up should matter), so Paper 3 can be read against Paper 1 timings; SF1 energy may be "below resolution". The
  grid is not refined after seeing data (§5.3).
- **GPU hosts:** R = 8 rounds = the Paper 1 Williams square (n = 4) twice, the second time in reverse row order. Seed rule:
  the integer `YYYYMMDD` of the lock date; `random.Random(seed).shuffle(["polars","polars_gpu","spark_cpu", "spark_gpu"])`
  gives letters A-D; rounds 1-4 use `A B D C / B C A D / C D B A / D A C B`, rounds 5-8 use the same rows in reverse order
  (4, 3, 2, 1). Suite order alternates (odd rounds baseline first); scales ascend. **CPU-only host:** 2 engines, orders `AB
  BA BA AB BA AB AB BA`.
- The two squares run on **separate instance launches** where capacity allows, so between-instance variation enters the
  across-round interval; instance is reported as a sensitivity factor. If a host completes only one square, R = 4 (df = 3)
  is used and stated. No round is added or dropped for what its values look like. Sessions: 32 per GPU-host round, 16 per
  CPU-only round.
- **Controls.** Paper 1 §6 (no other workload, no foreign GPU processes, preheated files, off-host rsync + checksums), plus:
  >= 60 s cool-down between sessions; C-GCP is single-tenant bare metal (network storage is shared); Lambda hosts are KVM
  VMs (noise visible via `host.csv`, clock and throttle logs). Measurement processes (`nvidia-smi` with `TZ=UTC`, RAPL
  sampler) run in every session including idle windows, so their draw is in `P_idle` and `E`; P5 quantifies their overhead.

## 4. Integrity and telemetry gates (pass/fail; never based on engine comparisons)
Paper 1 §7 and A1 apply unchanged: G1 every session exits, no timeout; G2 output agreement with `polars` (rel 1e-8, abs
1e-6, integers exact); G3 strict GPU guard, only classified coverage outcomes fail; G4 Spark GPU plan check; G5
`verified_gc` barriers; input gate before round 1. New:

| Gate | Rule | On failure |
|---|---|---|
| G6 source conformance | each session's counters match the probe-declared sources; none missing or non-monotone mid-session; every sampler series has liveness verdict "advancing"; launch, completed and process-exit stamps with counter readings present | session energy invalid; round fails, repeated once; no substitution |
| G7 sample coverage | trapezoid windows have >= 80% of expected 200 ms samples, no gap > 1 s | cross-check flagged; counter value unaffected |
| G8 clock alignment | activity-onset correlation, **onset times only** (no magnitudes): for GPU-engine windows >= 5 s, the first `gpu.csv` sample with `utilization.gpu` >= 10% must follow the worker's window start with median lag within +-0.4 s and first-third versus last-third drift <= 0.2 s; sessions without such windows use a 3 s load pulse (separate process) at start and end; `chronyc tracking` offset logged | `gpu.csv` values disabled for the session |
| G9 resolution | window rule and `t_min` of §2.1 met | cell "below resolution" |
| G10 idle stability | pre/post idle mean power within 10% per scope | above-idle flagged unreliable; total unaffected |
| G11 thermal/power state | throttle reasons, power limit, temperature logged | flagged, not excluded |
| G12 exclusive use | no foreign GPU compute process; nothing else scheduled on C-GCP | integrity failure |

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
  of CPU-engine runs in Wh per suite run (not set against a CPU-node figure).

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

**Break-even scale** for X3 (and X1, X2), per suite, host and metric (energy total, energy above-idle, cost B1, B2, B3, each
under P-NEW, P-OLD and P-BILLED): the smallest grid scale `s` such that every grid scale `>= s` is "GPU better". It is
reported as the bracket (previous grid scale, `s]`; "<= SF1" and "> SF300" are censored outcomes and reported as such. For
cross-class energy it is computed on the measured scopes (S1 vs S2-pinned) and labelled "lower bound on the node-level
break-even, under N1" (§2.5). A log-linear interpolation of the mean log-ratio against log SF is descriptive only. No scales
are added to refine a break-even inside this design.

**5.4 Multiplicity, precision, missing data, code.**
- No tests or p-values. Confirmatory set: X1-X3 for {energy, cost} x suite x scale x GPU host; X4-X6 are estimates.
  Intervals are marginal; no multiplicity correction. No power calculation: between-round variance of energy is unknown
  before data; interval width is a result, and R is set by time and instance availability.
- Ineligible cells (coverage outcome, below resolution, scope absent, invalid session) go to a coverage matrix and are
  excluded from the affected contrast; no imputation.
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
`campaign.json` and in the paper (commit dates are author-controlled).

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
  - **Planned:** P5 instrumentation overhead and idle floor on every host class, and on C-GCP a real NUMA-binding check
    (`power_telemetry.py --probe-membind --nodes <socket-0 nodes>`; `numa_maps` must show the engine's pages on those nodes,
    else the run is fixed before the lock or records `--no-membind` with the DRAM-attribution caveat); P6 counter versus
    sampled power on pulses of known length (fixes `t_min`, §2.1) and the G8 sync pulse; P7 GH200, B200, 1x A100: counter,
    CPU-energy check, `power.draw` semantics, GH200 scope; P8 RAPL, topology, idle floor of a fallback host if used.
- **Feasibility audit blinding note** (structure-only audit of the archive): before switching to timestamp-only readers the
  auditor printed raw rows of the h100 round-1 baseline suite (the first and last two `gpu.csv` rows, idle and outside any
  session; the first and last two `host.csv` rows, with `cpu_percent` and memory of one `spark_cpu` sf1 and one `polars`
  sf300 execute sample; the head of `schedule.json`). No aggregate by engine, host or scale was computed from them; `t16`,
  `t24` and Paper 1's Power column were not opened. The auditor rated it minor.
- **Seen by the author before the lock:** [AUTHOR: state what you have seen of the archived power data: raw sample rows
  only, or also the per-engine aggregates of §6.1]. Paper 1 already published per-engine GPU power summaries, so
  archived-data analyses are exploratory only.
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
  drafted with OpenAI Codex (Paper 1, amendment A5). Funding, credits or relationships with Lambda, AWS or GCP: none known
  to the drafter [AUTHOR: state].
- **Relation to Papers 1 and 2.** Paper 1 (`docs/H100_EXPERIMENT_DESIGN.md`, locked 2026-10-29, amendments A1-A5) reports
  timing on four Lambda hosts; A5 discloses the exploratory pilot that preceded its lock; it already published per-engine
  GPU power summaries (§6.1). Paper 2 re-examines the CPU baseline and gives the reporting checklist behind B1-B3. Paper 3
  reuses the workload, engines, gates and Williams design, collects **new** data with new instrumentation, and does not pool
  it with Paper 1/2 data; archived data enter only as labelled in §6.
- **Workload and TPC notice.** Copied from `paper/macros.tex` (`\tpcdisclaimer`, `\workloadname` expanded). The workload
  name is an open author decision: `\workloadname` is "H100 study workload", which fits poorly with four GPU generations and
  CPU-only hosts [AUTHOR]:

  > The H100 study workload is derived from the TPC-H Benchmark and as such is not comparable to published
  > TPC-H Benchmark results, as the H100 study workload results do not comply with the TPC-H Benchmark
  > Specification.

  Deviations as in Paper 1 (selected adapted queries, Float64, Parquet inputs, no refresh, power or throughput tests). The
  energy figures here are **not** TPC-Energy metrics and not comparable to them. TPC written-permission status for the
  benchmark name is open [AUTHOR] (`docs/TPC_PERMISSION_REQUEST.md`).

## 9. Budget (estimates; prices and hours are inputs, not results)
**Prices.** Lambda rows: P-NEW (§2.6; no spot price is listed). GCP rows: the P-NEW GCP table. AWS fallback: Price List API,
us-east-2. Every price is re-checked in the provider console before the lock.

**Hours.** Paper 1 wall times (A100 11h24m, H100 9h15m, GH200 6h47m, B200 9h07m: 4 main rounds plus a 64-session side study
that Paper 3 omits). GPU host = (2 x that time + 3 h set-up for two launches) x 1.2 (one repeated round or failed launch).
C-GCP = (2 x 9.25 h x 0.75 (CPU engines assumed <= 75% of a four-engine round) + 2 h set-up + 0.5 h boot) x 1.2 = 20 h.
**Instrumentation overhead** (P5 confirms): the §2.2 brackets cost 180 s per session (256 sessions per GPU host, 128 on
C-GCP) and the window rule at most 180 s more per session where queries run under 10 s (half the sessions), x 1.2: 23.0 h
per GPU host, 11.5 h on C-GCP.

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

## 10. Threats to validity
- **Virtualised counters.** Lambda guests are KVM VMs with a working NVML counter and no CPU energy (P1, P4), so GPU-host
  CPU/DRAM energy is unmeasured (§2.5). RAPL covers package and DRAM only (no PSU, fans, storage, network), and no external
  wall-power reference exists in a public cloud. P3's virtualisation verdict was "ambiguous" (no hypervisor flag, but DMI
  says Google); its counters advance and agree between powercap and perf, and G6 re-checks them every session.
- **Scope asymmetry and N1.** S1 and S2 are different slices of a node; §2.5 limits the claims, and N1 is untested: the
  tipping points show its weight without testing it.
- **Over-provisioned CPU-only host.** 96 cores and 1.5 TB DRAM: the pinned socket's package and DRAM idle high (§3.1)
  against light work, and DRAM includes idle capacity a right-sized node lacks. This inflates `E_C`, a bias against the CPU
  engines: cross-class "CPU better" findings are conservative, while "GPU better at the measured scope" may partly reflect
  host size. Package and DRAM are reported separately and above-idle beside total for exactly this reason; B1 prices a
  right-sized VM while the energy comes from the large host.
- **Sensors and idle draw.** `power.draw` is a 1 s average on Hopper/Blackwell and instantaneous on A100; the counter avoids
  this, but its ~100 ms update still limits short runs (§2.1). Back-to-back window runs are warmer than Paper 1's
  interleaved trials. Idle power is large relative to light work, so total and above-idle can give different break-evens
  (both fixed up front); it depends on temperature, persistence mode and C-states (recorded, not controlled).
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

## 11. Author decisions (approved defaults and open items)
**Standing limitation.** In this study "a GPU engine uses less energy than its CPU counterpart" **cannot be claimed at any
scale**, with or without N1. Node energy of a GPU-engine run needs the host CPU, DRAM and platform energy of the GPU host,
and P1 (Lambda H100 SXM5) and P4 (Lambda 8x A100) found Lambda GPU hosts to be KVM guests with no RAPL, MSR, IPMI or working
psys counter; the one way to measure it, a bare-metal GPU host, was dropped by the author. GPU-board energy is a lower bound
on the node's, so "the CPU engine wins" can survive under N1 and "the GPU engine wins" cannot. GH200, B200 and the 1x A100
type are unprobed (P7); if one exposed CPU energy, that is an author decision before the lock.

Approved by the author on 2026-11-11 ("approve defaults"): items 1-6. Open: item 7 only.
1. R = 8 rounds on two launches (R = 4 only if one square completes, §3.2).
2. Primary idle rule *total*, above-idle always shown; primary CPU cost baseline B1 = `c3-highmem-44` with the 200 GiB RAM
   floor; primary price table P-NEW with P-OLD as sensitivity (§2.6).
3. Equivalence margin +-10%; keep SF1 in the grid; 10 s window and `t_min` = 1 s (§2.1); G8 thresholds (§4): median onset
   lag within +-0.4 s, drift <= 0.2 s, onset at `utilization.gpu` >= 10%.
4. Idle brackets (§2.2) per session as written (12.8 h per GPU host, ~15 h with the 1.2 margin, ~$270 across the four).
5. Probes P5-P8 authorised, run by the team lead's agents under the lease rules.
6. N1 accepted as a labelled assumption; the wording of §2.5, the tipping points (`Delta*`) and the ~$10k cap (§9)
   confirmed.
7. Open `[AUTHOR]` items: what the author has seen of the archived power aggregates (§7.2); funding and relationships (§8);
   workload name and TPC permission status (§8.3). Note, not a decision: whether arXiv:2312.02741 applies to our drivers
   (§2.3) does not matter for the primary counter.

## 12. Amendments
None (draft).
