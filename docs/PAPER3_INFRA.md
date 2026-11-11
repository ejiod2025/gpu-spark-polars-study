# Paper 3 infrastructure: hosts, power probes, data staging, cost guardrails

Status: research, tooling and three probe runs. **The task that wrote this document launched, started or created
nothing in any cloud;** every cloud call it made was a read-only listing (instance types, prices, quotas and quota
usage, bucket listing). The three probe hosts of section 3 (P1 Lambda H100, P3 GCP `c3-highmem-192-metal`, P4 Lambda
8x A100) were launched by the team lead or the author, not by that task. Access date of every source: **2026-11-11**
(UTC times given where they matter). "Unverified" means: not confirmed by a primary source or a measurement in
this session; it is stated so that nobody builds on it unchecked.

Contents: 0 Decisions - 1 Lambda GPU hosts - 2 CPU-only hosts - 3 Power-interface probes - 4 Data staging - 5 Lifecycle
guardrails - 6 Tools in this repository - 7 Open risks - 8 Sources.

## 0. Decisions (author, 2026-11-11)

1. **CPU-only primary host: GCP `c3-highmem-192-metal` in `us-east4-a`** (official price $12.701568/h, section 2.2).
2. **Fallback order:** the same shape in another US zone (section 2.4 lists the zones whose project quota allows it),
   then `c3-standard-192-metal`, then `c3-highcpu-192-metal`, then AWS `m7i.metal-24xl` as the last resort.
3. **AMD hosts are dropped** (no AMD metal type is priced for use or probed). **A bare-metal GPU host is dropped:** CPU
   energy is measured on the GCP metal host only; on Lambda GPU hosts (KVM guests, no CPU energy source) GPU energy is
   measured through NVML and CPU energy is, at most, modelled and labelled as a model.
4. **Launch convention (every instance, every cloud):** name and labels/tags carry the owner and an expiry (section 5).
   GCP launches use `--max-run-duration` with `--instance-termination-action=DELETE`, which the API accepted on a metal
   instance (P3).

## 1. Lambda GPU instance types, prices, availability

Source S1 (Lambda Cloud API, `GET /api/v1/instance-types`, raw JSON saved at 2026-11-11T14:46:09Z) and source S2
(public page, https://lambda.com/pricing, which `lambda.ai/pricing` redirects to; raw HTML saved at the same minute and
byte-identical to a first fetch at about 13:50 UTC). Both files, with SHA-256 and fetch times, are archived in
`docs/paper3-evidence/prices-20261111/` (`MANIFEST.md`); the Lambda hashes are
`3a277b42...b077fa` (page) and `a31b0845...8b02d` (API).

**Price change on 2026-11-16.** The footer of each of the four instance-price tables (8x, 4x, 2x, 1x) on the page says:
"These prices take effect November 16, 2026, at 00:00 UTC. Until then, log in to your Lambda Cloud account to see
current pricing." The API still returns the old prices. **All estimates in this document use the new prices** (the
page); the old (API) prices are listed next to them. Whether a running instance is re-priced at the switch is not
stated (unverified), so a session that crosses 2026-11-16 00:00 UTC should be costed at the new prices. Costs in this
repository must state a price date. The page rows were parsed from the saved HTML (per GPU-hour on the 8x/4x/2x
tables, per instance on the 1x table) and multiplied out; the 15 rows below are the ones that matter here.

| API name | GPU | vCPU | RAM GiB | Disk GiB | **$/h new (page, from 11-16)** | $/h old (API today) | Change | Capacity at 14:46 UTC |
|---|---|---:|---:|---:|---:|---:|---|---|
| `gpu_1x_h100_sxm5` | 1x H100 80 GB SXM5 | 26 | 225 | 2816 | **4.89** | 4.29 | +14% | us-south-2 |
| `gpu_1x_h100_pcie` | 1x H100 80 GB PCIe | 26 | 200 | 1024 | **3.79** | 3.29 | +15% | none |
| `gpu_2x_h100_sxm5` | 2x H100 SXM5 | 52 | 450 | 5632 | **9.58** | 8.38 | +14% | none |
| `gpu_4x_h100_sxm5` | 4x H100 SXM5 | 104 | 900 | 11264 | **18.76** | 16.36 | +15% | none |
| `gpu_8x_h100_sxm5` | 8x H100 SXM5 | 208 | 1800 | 22528 | **36.72** | 31.92 | +15% | none |
| `gpu_1x_gh200` | 1x GH200 96 GB (aarch64) | 64 | 432 | 4096 | **2.59** | 2.29 | +13% | none |
| `gpu_1x_b200_sxm6` | 1x B200 180 GB SXM6 | 26 | 360 | 2816 | **7.99** | 6.99 | +14% | none |
| `gpu_2x_b200_sxm6` | 2x B200 | 52 | 720 | 5632 | **15.78** | 13.78 | +15% | none |
| `gpu_8x_b200_sxm6` | 8x B200 | 208 | 2900 | 22528 | **61.52** | 53.52 | +15% | none |
| `gpu_1x_a100_sxm4` | 1x A100 40 GB SXM4 | 30 | 200 | 512 | **1.99** | 1.99 | same | asia-south-1, us-east-1, us-west-2 |
| `gpu_1x_a100` | 1x A100 40 GB PCIe | 30 | 200 | 512 | **1.99** | 1.99 | same | none |
| `gpu_2x_a100` / `gpu_4x_a100` | 2x / 4x A100 PCIe | 60 / 120 | 400 / 800 | 1024 | **3.98 / 7.96** | 3.98 / 7.96 | same | none |
| `gpu_8x_a100` | 8x A100 40 GB SXM4 | 124 | 1800 | 6144 | **15.92** | 15.92 | same | us-east-1 |
| `gpu_8x_a100_80gb_sxm4` | 8x A100 80 GB SXM4 | 240 | 1800 | 20480 | **25.52** | 22.32 | +14% | us-east-1 |
| `gpu_1x_a10` | 1x A10 24 GB | 30 | 200 | 1400 | **1.29** | 1.29 | same | us-east-1, us-west-1 |

The API lists 24 types in total. The other nine (A6000 in 1/2/4/8 sizes, RTX 6000, V100 x2, two small CPU types) are
unchanged or not on the page (the page has no row for `gpu_8x_v100` and the `cpu_4x_general*` types). **H200, GB200
and B300 are not offered**: no API type, no row on the pricing page (the word "H200" occurs only in the page title
and meta tags; "GH200" is the only match elsewhere). The page also lists 1-Click Clusters (16+ GPUs, 2 weeks to 1 year;
B200 9.86 $/GPU-h at 16 GPUs, H100 6.16); they are out of scope for single-host work. No spot or reserved on-demand
price is published.

Capacity changes minute to minute (between 13:48 and 14:46 UTC the H100 PCIe, 2x H100 and 8x H100 slots disappeared and
8x A100 80 GB appeared; the single H100 SXM5 slot is one region). A launch can fail with "no capacity"; plan for retries rather than for a fixed day.

Host facts that matter for energy work, from earlier campaigns (`results/campaign-20261030/`) and the P1 probe:

| Host | CPU seen by the guest | Note |
|---|---|---|
| H100 SXM5 | Xeon Platinum 8480+ (Sapphire Rapids); 1 socket, 13 cores / 26 threads in the VM | KVM/QEMU Q35 guest (P1, section 3) |
| B200 | Xeon Platinum 8592+ (Emerald Rapids) | virtualization not checked (docs/PAPER3_FEASIBILITY.md: three of four hosts are KVM guests) |
| A100 SXM4 (1x) | EPYC 7J13 (Zen 3 Milan) | earlier campaigns; virtualization not checked |
| A100 SXM4 (8x, `gpu_8x_a100`) | EPYC 7542 (Zen 2 Rome), shown as 2 sockets x 31 cores x 1 thread = 124 vCPU, 1772 GiB | KVM/QEMU Q35 guest (P4, section 3); no CPU energy source; NVML energy on all 8 GPUs |
| GH200 | Neoverse-V2 (Grace), aarch64, 64 vCPU | idem; no x86 RAPL by construction |

## 2. CPU-only hosts where CPU power counters are readable

### 2.1 What the documentation says, and what was measured

* RAPL (`/sys/class/powercap/intel-rapl*/energy_uj`) and the MSRs behind it are a property of the physical CPU. A
  hypervisor decides whether a guest sees them. General-purpose VMs are **expected** not to expose them; that is a
  prior from experience, not something either cloud documents (unverified). The two Lambda VMs probed (H100, 8x A100;
  both KVM guests) exposed no CPU energy source at all (P1, P4, section 3).
* **GCP (primary).** The bare-metal page (S9, "Last updated 2026-11-10 UTC") says "Bare metal instances also expose all
  CPU performance counters and built in CPU accelerators" and lists "direct access to CPU counters" as a use case. It
  **does not mention RAPL, power telemetry, MSRs or powercap, but the probe on a real `c3-highmem-192-metal` found
  RAPL working, readable by root only (P3, section 3.1).** Limits stated by the page that affect us: only OS images that
  support the Intel IDPF network driver (no gVNIC or VirtIO); Hyperdisk Balanced and Hyperdisk Extreme can be attached,
  there is no Persistent Disk, and "Local SSD disks aren't available for C3 bare metal instances" (they exist on C3 VMs
  with an `-lssd` machine type); the maintenance behaviour must be TERMINATE; "CPU utilization monitoring isn't
  available" (install the Ops Agent instead). The shapes are 192 vCPUs with 512 / 768 / 1,536 GB of memory for
  `c3-highcpu` / `c3-standard` / `c3-highmem`.
* **AWS (last resort).** The Nitro page says bare metal instances "eliminate virtualization overhead and support
  workloads that require full access to host hardware" (S7). The processor-state-control page lists "All bare metal
  instances with Intel and AMD processors" among the types where the OS can control C-states and P-states (S8).
  **Neither page mentions RAPL, powercap, MSRs or energy**, and no AWS metal host has been probed: that RAPL is readable
  there is **unverified**. The instance-type API confirms the candidate is bare metal (`BareMetal: True`,
  `Hypervisor: None`) (S5).
* **Linux.** `energy_uj` has been root-only since the PLATYPUS fix (CVE-2020-8694, published 2020-11-12 (S11); kernel
  change "restrict energy meter to root access" - the commit is recalled from memory, unverified); P3 shows mode 0400
  on every zone. The powercap documentation (S12) describes the zone layout (`intel-rapl:N` packages, `:N:M`
  sub-zones, `energy_uj`, `max_energy_range_uj`, constraints) and says nothing about permissions. AMD hosts are out of
  scope (section 0).
* **Pinning consequence (measured topology).** The harness uses 26 threads (`--cpus 26`); the primary host has 192
  logical CPUs on 2 sockets (P3: 2 x 48 cores, SMT on, 4 NUMA nodes: node0 CPUs 0-23 and 96-119, node1 24-47 and
  120-143, node2 48-71 and 144-167, node3 72-95 and 168-191). A package counter contains every core of its socket, so
  pin the engines (and their memory, `numactl --membind`) to one socket, read that package and its DRAM zone only
  (`intel-rapl:0` and `intel-rapl:0:0` for socket 0), and log the idle floor of both. Which NUMA nodes belong to which
  package is not recorded in the probe (CPU numbering suggests nodes 0-1 = package 0; unverified).

### 2.2 Candidates and prices

**GCP, official on-demand list prices** (S15: Google's own pricing pages, fetched 2026-11-11T16:21Z-16:24Z, archived in
`docs/paper3-evidence/prices-20261111/gcp-official/` with SHA-256 and re-derivable by
`extract_gcp_official_prices.py` into `gcp-official-prices.json`; `MANIFEST.md`). **us-east4 (Northern Virginia) and
us-central1 (Iowa) are priced identically**, and so are us-east1, us-east5, us-west1 and us-west8 for the primary shape.
This replaces the third-party numbers used in the first version of this document; they agreed with the official ones
(c3-standard-192-metal $9.6772/h, c4-standard-288-metal $14.2322/h).

| Type | vCPU | RAM GiB | $/h | $ per month (730 h) | Role |
|---|---:|---:|---:|---:|---|
| **`c3-highmem-192-metal`** | 192 | 1536 | **12.701568** | 9,272.14 | **primary** (probed, P3) |
| `c3-standard-192-metal` | 192 | 768 | 9.677184 | 7,064.34 | fallback 3 |
| `c3-highcpu-192-metal` | 192 | 512 | 8.669056 | 6,328.41 | fallback 4 |
| `c3-highmem-44` (VM) | 44 | 352 | 2.910776 | 2,124.87 | smallest C3 VM with at least 26 vCPU and 200 GiB (below); a "right-sized VM" cost reference, no RAPL expected |
| `c3-standard-88` (VM) | 88 | 352 | 4.435376 | 3,237.82 | next-cheapest fit |
| `c4-standard-288-metal` | 288 | 1080 | 14.23224 | 10,389.54 | not in the order (larger and dearer) |

* **Metal is priced per shape on the page, and it equals linear VM pricing.** The page lists a price per machine
  type, not per vCPU or per GiB. All 42 C3 rows (21 shapes x 2 regions, VMs and metal) are reproduced to 2e-15 USD/h by
  **$0.03465 per vCPU-hour + $0.003938 per GiB-hour** (the two rates solved from the standard and highcpu metal shapes,
  which differ only in memory). Examples: `c3-highmem-192-metal` = 192 x 0.03465 + 1536 x 0.003938 = 6.6528 + 6.048768 =
  **12.701568**; `c3-standard-192-metal` = 6.6528 + 768 x 0.003938 (3.024384) = 9.677184; `c3-highcpu-192-metal` =
  6.6528 + 512 x 0.003938 (2.016256) = 8.669056; `c3-highmem-44` = 44 x 0.03465 + 352 x 0.003938 = 1.5246 + 1.386176 =
  2.910776. There is no metal premium. The page's Monthly column is exactly 730 x the hourly price.
* **Smallest C3 VM with at least 26 vCPU and 200 GiB:** C3 VMs come in 4, 8, 22, 44, 88 and 176 vCPUs with 4 GiB
  (standard), 8 GiB (highmem) or 2 GiB (highcpu) per vCPU (the `-lssd` Local SSD variants are not on the saved page and
  are not considered). `c3-standard-44` has only 176 GiB and `c3-highmem-22` only 22 vCPUs, so the cheapest fit is
  **`c3-highmem-44`** (44 vCPU, 352 GiB, $2.910776/h), then `c3-standard-88` ($4.435376).
* **Hyperdisk Balanced** (the only disk type besides Hyperdisk Extreme that C3 metal takes), identical in us-east4 and
  us-central1: **$0.08 per GiB-month** of provisioned space (page: $0.000109589 per GiB-hour); **$0.005 per provisioned
  IOPS-month** and **$0.04 per MBps-month** of provisioned throughput, each charged only above the baseline of 3,000
  IOPS and 140 MBps (page text; hourly $0.000006849 and $0.000054795). Example for the 800 GiB data volume at 16,000
  IOPS and 1,000 MBps: 800 x 0.08 = 64.00 + (16,000 - 3,000) x 0.005 = 65.00 + (1,000 - 140) x 0.04 = 34.40 =
  **$163.40 per month = $0.2238/h**. Whether C3 metal allows these provisioned values is not in the saved pages
  (unverified).
* **Spend rates of the primary host:** $12.70/h, $304.84/day; a 60-minute session costs about $12.70 + $0.22 disk =
  **$12.92**; a 2-hour probe session about $25.8.
* Zones (from `gcloud compute machine-types list`, project siriusmedai, `gcp-metal-machine-types.csv`;
  machine-type presence, not capacity): `c3-highmem-192-metal` is offered in us-central1-a, us-east1-b/c/d,
  us-east4-a/c, us-east5-a/b and us-west1-a/b; `c3-standard-192-metal` in the same plus us-south1-b;
  `c3-highcpu-192-metal` in the same as highmem except that it is in us-central1-c instead of us-central1-a. The
  bare-metal page lists the zones slightly differently (it omits us-east1-b for highmem); the project's `gcloud` answer
  is the one that matters.

**AWS (last resort only), us-east-2, Linux, shared tenancy, on-demand** (AWS Price List API, S4; evidence:
`aws-price-list-us-east-2.json`, `get-products` responses fetched 2026-11-11T14:46:31Z-14:46:40Z, price list
`publicationDate` 2026-11-10T17:38:52Z, SHA-256 in `MANIFEST.md`):

| Type | CPU | Physical cores / vCPU | RAM GiB | Storage | $/h | Role |
|---|---|---:|---:|---|---:|---|
| **`m7i.metal-24xl`** | Xeon Sapphire Rapids (same generation as the P3 host's 8481C and the H100 hosts' 8480+) | 48 / 96 | 384 | EBS only | **4.8384** | **last resort** (fallback 5); RAPL on AWS metal unprobed |
| `r7i.8xlarge` (VM, not metal) | Sapphire Rapids | 16 / 32 | 256 | EBS | 2.1168 | the AWS "right-sized VM" price reference (no RAPL expected) |

The other types in the saved bundle were evaluated and are not in the fallback order: Intel `r7iz.metal-16xl` (5.9520),
`m6i.metal` (6.1440), `m5.metal` (4.6080), `c7i.metal-24xl` (4.2840; 192 GiB is below the 200 GiB bar), `m6id.metal`
(7.5936) and `i7i.metal-24xl` (9.0605); and the AMD types `m6a.metal` (8.2944), `m8a.metal-24xl` (5.8426) and
`m7a.metal-48xl` (11.1283), **dropped by the author's decision** (section 0). Region choice for AWS: the account default
is us-east-2; the types are offered in all three of its zones (S5; offering is not capacity).

### 2.3 Quotas in our accounts (read-only)

* **GCP** (`gcloud compute regions describe`, S10; `gcp-region-quotas.json` 14:47Z for us-west1, us-central1,
  us-east1 and `gcp-region-quotas-us-east.json` 16:27Z for us-east4, us-east5, us-east1): the `C3_CPUS` limit is
  **300 in us-east4, us-east1, us-central1 and us-west1** (usage 0 at the time), so **one** 192-vCPU metal host fits in
  each and two do not (384 > 300). **us-east5 has `C3_CPUS` = 24: no `c3-*-192-metal` can start there without a quota
  increase**, although the shapes are offered in its zones. us-east4 also showed `CPUS` usage 0 and `INSTANCES` usage 0
  at 16:27:07Z, i.e. nothing was running in that region (the P3 host had been deleted). `CPUS` limits are 3000
  (us-east4, us-central1, us-east1) and 1500 (us-west1). The project already runs small VMs of another product
  (`INSTANCES` usage 5 in us-central1 and 2 in us-west1 at 14:47Z) and, per the team lead, unrelated production VMs; a
  dedicated project, or at least a dedicated label and budget, keeps the benchmark spend and any cleanup separate. No `C4_CPUS` metric
  exists, so C4 metal presumably draws on `CPUS` (unverified).
* **AWS** (`aws service-quotas list-service-quotas`, S6): "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z)
  instances" (L-1216C47A) = **512 vCPUs** in us-east-2, us-east-1 and us-west-2, so one `m7i.metal-24xl` (96 vCPUs)
  fits. Currently used in us-east-2 (read-only `describe-instances`): three small `t3` instances running and one
  stopped `g6.2xlarge`, ownership unknown - not ours to touch. "Running On-Demand High Memory instances" = 0, "All X
  Spot Instance Requests" = 0.

### 2.4 Fallback order (author decision) and cost

| # | Host | Where | $/h | Note |
|---|---|---|---:|---|
| 1 | GCP `c3-highmem-192-metal` | us-east4-a | 12.701568 | probed (P3): RAPL readable by root, ~2 min to SSH |
| 2 | same shape, another zone | us-east4-c, then us-east1-b/c/d, us-central1-a, us-west1-a/b (not us-east5: `C3_CPUS` = 24) | 12.701568 | same price in these regions; us-west2/3/4 and us-south1 are dearer (14.31-15.27 $/h for this shape) |
| 3 | GCP `c3-standard-192-metal` | same regions | 9.677184 | 768 GiB; same CPU; stockouts seen at about 15:00 UTC |
| 4 | GCP `c3-highcpu-192-metal` | same regions | 8.669056 | 512 GiB; stockouts seen at about 15:00 UTC |
| 5 | AWS `m7i.metal-24xl` | us-east-2 | 4.8384 | last resort; 384 GiB; RAPL unverified; a metal boot can take 20+ minutes (S7) |

Falling down the list changes the memory (1536, 768, 512, 384 GiB) but not the CPU generation; if the shape changes
between runs, record the shape with every result. The P3 stockout evidence is the team lead's report and is not
archived; capacity changes by the minute (section 1), so retry before falling back.

## 3. What the probes answer, and the probe sequence

### 3.1 Results so far (P1, P3, P4; raw JSON in `docs/paper3-evidence/probes/`)

**P1 - Lambda `gpu_1x_h100_sxm5`**, 2026-11-11 14:07-14:21 UTC (re-run with the updated probe at 14:44 UTC, `...probe-v2.json`):
a KVM/QEMU Q35 VM (hypervisor flag set, DMI QEMU/SeaBIOS), Xeon Platinum 8480+, 1 socket, 13 cores / 26 threads, 221 GiB,
kernel 6.8.0-1064-nvidia. No powercap zones, no `/dev/cpu/*/msr`, no IPMI device. The perf power PMU exposes only
`energy-psys`, which counted **0.00 J** under a 26-thread load (as root; `perf_event_paranoid` is 4, so non-root perf
fails). `nvidia-smi --query-gpu` does not offer the cumulative energy field, but NVML `nvmlDeviceGetTotalEnergyConsumption`
works (advanced 355,288 mJ in 5 s idle = 71.1 W, matching `power.draw`; power limit 700 W). Clock synchronised (chrony
offset 41 us).

**P3 - GCP `c3-highmem-192-metal`, us-east4-a**, probe run 2026-11-11 15:04:12Z-15:04:27Z
(`gcp-c3-highmem-192-metal-20261111T150427Z.json`). Ubuntu 24.04.5, kernel 7.0.0-1017-gcp. Xeon Platinum 8481C
(Sapphire Rapids), **2 sockets x 48 cores, SMT on, 192 logical CPUs**, 1511 GiB visible (1536 nominal), 4 NUMA nodes,
`intel_pstate` (powersave, 0.8-3.8 GHz, turbo on). No hypervisor flag in `/proc/cpuinfo`; `systemd-detect-virt` still
answers `google` and DMI says "Google Compute Engine", so the probe's verdict is `ambiguous` ("trust the RAPL checks").
It reached SSH about **2 minutes** after launch (team lead's observation).

* **RAPL works, root-only.** powercap has `package-0`, `package-1` and one `dram` zone under each
  (`intel-rapl:0`, `:0:0`, `:1`, `:1:0`); every `energy_uj` is mode 0400 (not readable as the benchmark user, readable
  with `sudo`); `intel_rapl_common` and `intel_rapl_msr` are loaded; counters advance. No `/dev/cpu/*/msr` (so no MSR path,
  and `turbostat` is skipped), no hwmon energy file, no `amd_energy`. `max_energy_range_uj` = 262,143,328,850 (the
  counter wraps after about 12.5 minutes at 350 W, so a sampler must read faster than that and handle the wrap).
  Package limits: PL1 350 W, PL2 420 W.
* **perf:** `perf_event_paranoid` = 4, so perf needs root. The `power` PMU has `energy-pkg`, `energy-psys` and
  `energy-ram`; `energy-psys` reads **0 J** (also here), `energy-pkg` and `energy-ram` advance.
* **Measured watts** (`counter_sanity`; the idle window is 3.1 s for powercap and 6.0 s for perf, the busy window 10 s with
  192 busy threads; short windows, one sample):

  | Source | Idle W | All-threads busy W |
  |---|---:|---:|
  | powercap `package-0` | 181.3 | 349.2 |
  | powercap `package-1` | 172.1 | 348.9 |
  | powercap `dram` (socket 0 / socket 1) | 38.5 / 37.7 | 38.3 / 37.3 |
  | **Sum of the four powercap zones** | **429.7** | 773.7 |
  | perf `power/energy-pkg/` (`perf stat -a`) | 176.7 | 350.2 |
  | perf `power/energy-ram/` | 38.1 | 37.7 |
  | perf `power/energy-psys/` | 0 | 0 |

  The sum of 430 W idle is RAPL scope only (CPU packages and DRAM): it excludes fans, PSU losses, NICs and drives, so it
  is a lower bound on wall power. The busy burn sits at the 350 W PL1 limit, and DRAM does not respond to a CPU-only
  burn. **The perf figures match ONE package and ONE DRAM zone of the powercap readout, not their sum** (176.7 / 350.2 W
  against 181.3 + 172.1 / 349.2 + 348.9 W; 38.1 against 38.5 + 37.7 W): on this kernel `perf stat -a
  power/energy-pkg/` seems to cover a single package. Until that is checked (for example `perf stat -a --per-socket` or
  `-A` against the powercap deltas), use powercap per package and do not read `perf stat -a` as whole-host energy.
* **No BMC:** no `/dev/ipmi*`, the IPMI modules are not loaded, so there is no in-band DCMI power reading and no
  wall-power cross-check on this host. The clock is synchronised (chrony, reference `metadata.google.internal`, last
  offset +0.16 us).
* The probe ran without a lease name (`lease.present` false) and noted `needs_root`.

**P4 - Lambda `gpu_8x_a100`**, probe run 2026-11-11 15:23:41Z-15:23:52Z (`lambda-8x-a100-20261111T152352Z.json`).
A KVM/QEMU Q35 guest (hypervisor flag, DMI QEMU/SeaBIOS 1.13.0, `systemd-detect-virt` kvm), AMD EPYC 7542 shown as 2
sockets x 31 cores x 1 thread = 124 vCPUs, 1772 GiB, Ubuntu 22.04.5, kernel 6.8.0-60-generic, NVIDIA driver 570.148.08.
**No CPU energy source of any kind** (no powercap, hwmon, perf power PMU, MSR or IPMI; `best_source` none). **NVML's
total-energy counter works and advances on all 8 A100-SXM4-40GB GPUs**; idle draw 51.6-57.9 W each (435 W for the eight,
from the counters), power limit 400 W (minimum 100 W). Clock synchronised. Neither P3 nor P4 was given a lease name.

**Recorded conclusions.**
1. **CPU energy is measurable on the GCP metal host only** (RAPL package and DRAM per socket, root-only). It is not
   measurable on the Lambda H100 VM (P1) or the Lambda 8x A100 VM (P4); the other Lambda types (GH200, B200) have not
   been probed (`docs/PAPER3_FEASIBILITY.md` expects KVM guests there too).
2. **GPU energy is measurable on every GPU host probed** (H100, 8x A100) through the NVML total-energy counter.
3. **No probed host has a BMC power reading**, so there is no wall-power cross-check anywhere.
4. **`energy-psys` is exposed but reads 0 J on both Intel hosts that expose it** (P1 VM, P3 metal): never use it.
5. Scope rule, as decided (section 0): CPU energy comes from the GCP metal host (or its fallbacks) only. A CPU energy
   figure for a Lambda GPU run is, at most, modelled and labelled as a model.

Lessons built into `probe_power.py` after the P1 run (each has tests):
1. NVML is read through `ctypes` (`libnvidia-ml.so.1`, no `pynvml`), because `nvidia-smi --query-gpu` cannot
   return the cumulative energy counter. `gpu.energy_counter_available` stays the `nvidia-smi` answer;
   `nvml.energy_counter_available` and `summary.gpu_energy_counter(_via)` give the real one.
2. Power limits no longer come back empty: the fixed list of power/energy fields is queried even when
   `--help-query-gpu` does not list them, with fallbacks to the parsed `nvidia-smi -q -d POWER` text and to NVML
   (`gpu.power_limits_source`, `summary.gpu_power_limits_source` say which one answered).
3. The perf power events (`energy-pkg`, `energy-psys`, ...) are measured again inside `counter_sanity`, in an idle
   window and in a window with every CPU burning (`perf stat -a ... sleep N`, as root where only root may run perf).
   A counter that is present and reads 0 J (`energy-psys` on P1 and P3) is reported as `advanced: false`, ranked as no
   source, and noted in `summary.notes`; no manual load test is needed.
4. A power event that exists but counts zero was first ranked "best source"; it no longer is.

### 3.2 Per-host probe runbook (about 30 minutes per host type)

1. Launch (a human does this; name, labels and TTL from section 5). GCP metal booted to SSH in about 2 minutes (P3).
   AWS metal can take "up to 20 minutes or more from the time the instance enters the running state until it becomes
   available over the network" (S7), and billing starts at "running" - budget 20 minutes of boot there.
2. Copy the one file and run it as the normal benchmark user, passing the lease:
   `python3 probe_power.py --lease-name <instance name> --output probe-user.json` (about 15 s; it uses passwordless
   `sudo -n` for the root-only questions and falls back to "skipped" without it). Use `--idle-seconds 300 --no-load`
   once for a 5-minute idle window.
3. If `summary.cpu_energy.needs_root` is true (it was on P3): `./setup_h100.sh --power-only --enable-rapl-read
   --install-power-tools`, then run the probe again as the user (`probe-after.json`); or run the sampler as root and
   record that choice. Do **not** lower the RAPL mitigation on a multi-tenant machine; the metal hosts are single-tenant.
4. Copy the JSON files off the host, then terminate it. Compare idle and busy watts of the RAPL packages against
   `ipmitool dcmi power reading` (absent on P3) and NVML energy (the probe prints all three).

Decision table for the measurement design:

| Probe outcome | Consequence |
|---|---|
| CPU energy source user-readable and its counters advance | Measure CPU package (and DRAM where present) energy directly; pin to one socket |
| Readable only as root (**P3**) | Enable with the flagged step, or run the sampler as root; record the choice |
| No source (**P1, P4**, typical for VMs) | CPU energy is modelled, never measured, and labelled so; report GPU energy measured and CPU energy modelled separately |
| `dcmi power reading` works (no host so far) | Whole-node watts at about 1 s granularity as a cross-check, not as the primary source |
| NVML total energy present (**P1, P4**) | Per-query GPU energy by counter difference; otherwise integrate `power.draw` samples |

### 3.3 Sequence and cost

Done: P1 (Lambda H100, plus the 14:44 re-run), P3 (GCP metal), P4 (Lambda 8x A100). Their launch and delete times are not
recorded in the evidence, so their actual cost is not stated here (list price for one full hour: $12.70 for P3, $15.92 for
P4, which is unchanged by the 11-16 Lambda price change).

Remaining, optional, 30 minutes each. Lambda costs use the **new prices (page, from 2026-11-16)**, the old (API today)
prices are in the right-hand columns; billing granularity on Lambda is not stated on the docs page (S3; unverified).

| Step | Host | $/h new | 30 min new | $/h old (API) | 30 min old |
|---|---|---:|---:|---:|---:|
| G2 | Lambda `gpu_1x_gh200` (when capacity exists) | 2.59 | 1.30 | 2.29 | 1.15 |
| G3 | Lambda `gpu_1x_b200_sxm6` (when capacity exists) | 7.99 | 4.00 | 6.99 | 3.50 |
| | **Remaining GPU probes G2 + G3** | | **5.30** | | **4.65** |
| C-AWS | AWS `m7i.metal-24xl`, 60 min incl. boot, only if the GCP shapes cannot be had (EBS gp3 800 GB at 1000 MiB/s, 16000 IOPS adds about 0.23/h) | 4.84 | about 5.1 | 4.84 | about 5.1 |

Probe G2/G3 only when their capacity shows. A probe of the primary host again is only needed after a change to the
sampler (about $12.9 per hour including disk).

## 4. Data staging

Inputs are not stored anywhere: they are regenerated with DuckDB dbgen on each host (`REPRODUCE.md` step 2) and
gated by `verify_generated_inputs.py` (content fingerprints against the A100 manifests; 1-3 minutes).

* **The GCS archive cannot replace generation.** `gs://siriusmedai-gpu-benchmark-archive` (class NEARLINE, region
  US-CENTRAL1, public access prevention enforced; listed read-only, S14) holds a copy of the repository tree. Its
  `data/` prefix contains **only SF1 (309 MiB)**; `results/` is 51 MiB, `evidence/` 17.5 MiB. No SF10-SF300 Parquet
  files are archived. Copying SF300 would first require generating it once and uploading about 108 GB.
* **Measured generation time (recorded in the repository).** SF300 on the A100 host (30 vCPU, 16 workers, 60 dbgen
  children): `generation_seconds` = **345.6 s** (`evidence/INPUT_MANIFESTS/large-sf300-20261025/sf300.json`).
  All scales SF1..SF300 on the 26-vCPU and 64-vCPU Lambda hosts: "10 to 15 minutes per host" (REPRODUCE.md).
* **Estimates for the CPU hosts (not measured).** The 60 children are independent, so more workers shorten the CPU
  part. On a metal host with 24 workers the CPU part of SF300 should be about 3 to 5 minutes (about 2.5 waves of the 90 s
  per-chunk time seen on the A100 host; 24 workers hold about 120 scale factors in memory, which fits the primary's
  1536 GiB and the last-resort host's 384 GiB). The limit is storage: the data is about 108 GB (SF300) / 158 GB (all
  scales). **Primary host (GCP Hyperdisk Balanced):** the baseline of 140 MBps needs at least **12.9 min / 18.8 min**
  just to write it; at 1000 MBps it is 1.8 / 2.6 min. Provisioning 1000 MBps and 16000 IOPS on an 800 GiB volume costs
  $0.2238/h in total (section 2.2: $0.0877 space + $0.0890 IOPS + $0.0471 throughput). C3 metal has no Local SSD (S9),
  so Hyperdisk is the only option; whether C3 metal allows those provisioned values is unverified. (AWS last resort: gp3
  at its baseline of 125 MiB/s needs 14.4 / 21.1 min; at 1000 MiB/s and 16000 IOPS it is +$0.225/h for 800 GB, S4.)
* **Cost of regenerating on every launch** is host time: about 15 minutes x $12.70/h = **$3.2** on the primary
  `c3-highmem-192-metal` ($1.2 on `m7i.metal-24xl`, $1.1 on a Lambda H100 at $4.29/h) - small compared with the
  campaign hours, but not negligible next to a 60-minute probe session.
* **Cost of copying instead (unverified prices).** Cloud Storage to the internet is about $0.12/GB and Nearline
  retrieval about $0.01/GB (from general knowledge; the pricing page was not readable by the fetcher): about $13 plus
  $1 for 108 GB, plus the upload and storage ($0.01/GB-month). Inbound transfer to AWS is free (AWS rule, unverified
  here). Lambda's egress policy is not on its docs page (unverified). Intra-region transfer inside GCP is free
  (unverified). Whichever way, regeneration is cheaper and avoids a second source of truth.
* **Snapshot of a generated dataset** (to skip regeneration): AWS EBS snapshots cost $0.05/GB-month (S4), about **$7.9
  per month** for 158 GB, plus slow first-touch reads after restore; the GCP snapshot price was not collected. At the
  AWS price the break-even against regeneration is about 6 launches a month on `m7i.metal-24xl`; we expect fewer, so
  regenerate.
* **Disk the script requires:** 500 GiB free on the data volume (REPRODUCE.md). Use an 800 GiB Hyperdisk Balanced root
  or data volume on GCP (800 GB on AWS). Local NVMe instance stores (AWS `*d` types) are not mounted by the script -
  format and mount them first and pass `--data-root`; C3 metal has none.

## 5. Lifecycle guardrails (described, not created)

**Why:** the team once left GPUs idle for about 7 hours. At the prices effective 2026-11-16 that is **$34 for one H100
SXM5 and $56 for one B200** (at today's API prices $30 and $49); the same 7 hours on the primary CPU host
(`c3-highmem-192-metal`, $12.70/h, which bills whether or not anything runs) would be **$88.9**, and one forgotten day
$304.8. A worked example of the failure to design out was
seen while preparing this document: at 14:02 UTC a read-only Lambda API call found one active `gpu_1x_h100_sxm5`
instance in us-south-3 with **no name and no tags** - nothing to tell its owner, its purpose or when it should end.
According to the team lead nothing on the team's machines launched it, and it carries the author's SSH key, so it is
presumably the author's; the P1 probe was then run on it. By 14:27 UTC it had been given the name
`p3-probe-h100-owner-<id>-expires-20261111T1807Z`, which is the convention below: owner and end time are now readable
from the name alone. It was left alone throughout.

**Launch convention (every instance, every cloud): name, owner, expiry.**
* Name pattern: `p3-<purpose>-owner-<id>-expires-<YYYYMMDDTHHMMZ>` in lower case, e.g.
  `p3-probe-h100-owner-ejiod-expires-20261111T1807Z`, `p3-campaign-b200-owner-ejiod-expires-20261112T0200Z`.
  `<purpose>` may contain hyphens; `<id>` is one token; the expiry is UTC and also becomes the auto-terminate deadline
  of rule 1. **GCP instance names and label values must be entirely lower case** (and an instance name is at most 63
  characters), so there the expiry is written `20261111t1807z`, e.g. `p3-cpu-c3hm-owner-ejiod-expires-20261112t0200z`
  (46 characters). `probe_power.lease_status` accepts either case of the `T` and `Z` (a version that required capitals
  would have rejected every valid GCP name; fixed and tested).
* **Campaign scripts must refuse to start without it.** `run_campaign.py` and `preflight.py` (not owned by this
  task) should call `probe_power.lease_status(name, min_minutes=N)` or reimplement the same regular expression and
  abort with a clear message if (a) the name does not match, (b) the expiry is in the past, or (c) fewer than the
  campaign's planned duration plus a margin remain. Where does the script get the name? Three options, strongest
  first: the operator-side launcher passes `--lease-name` after reading it back from the cloud API (AWS tag, GCP
  label, Lambda `name`); or the launcher writes the name to `/etc/paper3-lease` on the host at boot (cloud-init on
  AWS/GCP; one `ssh` command on Lambda, whose user-data support is unverified); or the script is run by hand with
  the flag. `probe_power.py` already implements the check: `--lease-name NAME` (or the file) fills the `lease` section
  and `summary.lease`, and a missing or expired lease is a note in `summary.notes`.
* Enforcement for a human launcher: a wrapper that composes the name and the deadline, refuses to launch without
  `--owner` and `--hours`, and records the launch in a ledger (rule 1, Lambda).

Rules, in order of strength:

1. **A TTL on every instance, enforced from outside the instance.**
   * AWS: launch with `--instance-initiated-shutdown-behavior terminate` and put `shutdown -h +N` in user data, so
     the instance removes itself even if the controller dies; set `DeleteOnTermination` on every volume; tag
     `ExpiresAt`. A CloudWatch alarm on low CPU with the EC2 terminate action is a second layer (its behaviour on
     metal instances is unverified).
   * GCP: launch with `--max-run-duration` (for example `2h`) and `--instance-termination-action=DELETE`. **The API
     accepted both flags on a `c3-highmem-192-metal` (verified at launch on 2026-11-11, P3).** What was *not*
     observed is the automatic deletion itself: the instance was deleted manually before the 2 hours expired, so
     "accepted" is verified and "enforced at expiry" is not. Keep the second layer anyway: `shutdown -h` in user data
     or cron (a stopped instance bills no vCPU, but its disks keep billing until deleted), create every disk with
     `auto-delete=yes`, and run the audit of rule 4. Set the TTL to the planned duration plus a margin (P3 used 2 h).
   * Lambda: instances can only be restarted or terminated through the console or API (S3); there is no stop state
     and no documented TTL (unverified). In-guest shutdown is not known to stop billing. So the reaper must be
     external: a script on the operator's machine (not on the instance) that lists `GET /instances`, compares with a
     ledger of "launched by us" instances and their intended end time, and **reports** every instance that is
     untagged, unknown to the ledger or past its TTL. Terminating is a deliberate human action (or an explicit,
     separately approved `--terminate-expired` mode limited to instances carrying our tag).
   * Suggested TTLs: probe sessions 90 minutes; campaign sessions = planned duration + 60 minutes.
2. **Tags and labels at launch.** AWS tags on instance and volume: `Project=paper3-energy`, `Owner`, `Purpose`
   (`probe` | `campaign`), `ExpiresAt` (UTC ISO). GCP labels (lower case): `project=paper3-energy`, `owner`,
   `purpose`, `expires` (value like `20261111t1807z`). Lambda: the `GET /instances` response has `tags` and `name` fields, so the launch API very
   likely accepts them (the launch parameter was **not** checked); until then use a naming convention in `name`.
   An IAM condition that denies `RunInstances` without the `ExpiresAt` tag (AWS) is the enforcement.
3. **Budgets and alarms.**
   * AWS Budgets: a monthly cost budget (suggest $75) with actual alerts at 50/80/100 % and a forecast alert at
     100 %, to e-mail or SNS. Cost-allocation tags need up to 24 h to start filtering, so start with an
     account-wide budget and add the `Project` filter later. A Budget Action can attach a deny-launch policy at 100 %.
   * GCP: a Cloud Billing budget with 50/90/100 % thresholds on the project (or the `project=paper3-energy` label)
     and a Pub/Sub notification; the documented pattern then lets a function stop labelled instances. Needs Billing
     Account permissions and the Cloud Billing API (currently disabled in `siriusmedai`: enabling it needs consent).
   * Lambda: no budget or spending-limit feature is documented (unverified). The ledger and the hourly report are
     the control.
4. **An hourly read-only audit while anything runs.** Commands (all read-only):
   * Lambda: `curl -s -u "$(cat ~/.config/lambda/api_key):" https://cloud.lambdalabs.com/api/v1/instances | jq '.data[] | {type: .instance_type.name, status, region: .region.name, since: .first_healthy, name, tags}'`
   * AWS: `aws ec2 describe-instances --region us-east-2 --filters Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name,LaunchTime]' --output table` and `aws ec2 describe-volumes --region us-east-2 --filters Name=status,Values=available` (orphaned volumes).
   * GCP: `gcloud compute instances list --format='table(name,zone.basename(),machineType.basename(),status,labels)'` and `gcloud compute disks list --filter='-users:*'` (these list the whole project, including instances that are not ours; add `--filter='labels.project=paper3-energy'` to instances, and act only on that subset). A cheaper check that cannot touch anything: `INSTANCES` and `CPUS` usage in `gcloud compute regions describe us-east4` (0 at 16:27Z on 2026-11-11).
5. **Pre-launch checklist:** price date written down; TTL set; tags set; budget exists; probe or campaign script
   uploaded and tested with `--dry-run`; a human knows the termination command. **Post-run checklist:** all three
   audit commands show nothing of ours; volumes and snapshots deleted; probe JSON copied and hashed.
6. **On-instance idle detector (alert, do not kill on Lambda):** a cron entry that records GPU utilisation, load
   average and whether `run_campaign.py` is alive, and shouts (e-mail or webhook) after 30 idle minutes. It cannot
   replace the external reaper.

## 6. Tools in this repository

* `probe_power.py` - stand-alone, standard-library-only (Python 3.8+), read-only probe. Run it as the benchmark user.
  Output is JSON on stdout (and `--output FILE`), progress on stderr. Options: `--idle-seconds` (3), `--busy-seconds`
  (5), `--no-load`, `--no-sudo`, `--no-commands`, `--root DIR` (fake trees; implies no commands),
  `--include-hostname`. Hostnames, IPs, serials, UUIDs and GPU PCI bus ids are not recorded; `dmidecode` serial/UUID
  lines are redacted. Its only non-read-only act is the CPU burn of `--busy-seconds`. It never loads kernel modules; turbostat
  runs only if `/dev/cpu/0/msr` already exists (turbostat can try to modprobe `msr` itself).

  Top-level keys, in order: `schema` (`probe_power/1`), `probe` (version, UTC start/end, duration, argv, root,
  `commands_enabled`, `sudo` = root | passwordless | unavailable | disabled, euid), `host`, `cpu` (model, family,
  microcode, `logical_cpus`, `sockets`, `physical_cores`, `threads_per_core`, `smt_active`, NUMA, `mem_total_gib`,
  `hypervisor_flag`, cpufreq driver/governor/turbo), `virtualization` (`verdict` = virtualized | bare_metal |
  ambiguous | unknown, `evidence`, DMI strings, `dmidecode` processors with socket/core/thread counts),
  `powercap` (every zone: id, control type, depth, name, `energy_uj` {mode, owner, `readable_as_user`,
  `readable_as_root`, error}, `max_energy_range_uj`, constraints; `domains`, `package_zones`,
  `all_packages_readable_as_user/_root`), `hwmon` (energy/power files, e.g. `amd_energy`), `perf` (paranoid level,
  PMU devices, `power_events` with scale and unit, `perf stat` as user and as root with `counts`), `msr` (device,
  decoded energy unit, two raw package-energy samples), `turbostat`, `ipmi` (devices, `dcmi_power_reading.watts`),
  `gpu` (`nvidia-smi`: every power/energy field, `power_limits_w` with `power_limits_source`, parsed `-q -d POWER`
  with the PCI bus id removed), `nvml` (library read through ctypes, no `pynvml`: total energy mJ, power, limits per
  device), `clock` (chrony/timedatectl/clocksource/PTP, `synchronized`), `lease` (`--lease-name` or the first line of
  `/etc/paper3-lease`, checked against the launch convention of section 5: `valid`, `owner`, `expires_utc`,
  `minutes_left`, `problem`), `counter_sanity` (an idle window and a busy window, every CPU burning, for every
  readable energy counter - powercap, hwmon, NVML and the perf `power/energy-*/` events: delta, joules, average
  watts, `advanced`, `busy_over_idle`), `summary` (`virtualization`, `cpu_energy` {`best_source`, `readable_as_user`,
  `needs_root`, `counters_advance`, `rapl_domains`, `candidates[]`}, `bmc_power_available`,
  `gpu_energy_counter(_via)`, `gpu_power_limits_w` and `_source`, `clock_synchronized`, `lease`, `notes[]`), `errors`.
  `--lease-name` is the one extra option. `probe_power.lease_status(name, now=None, min_minutes=0)` is importable (the
  `T` and `Z` of the expiry may be lower case, as GCP requires).
* `setup_h100.sh` - new options (all optional; the default plan is byte-identical to before, checked by diffing
  `--dry-run` output for x86_64 and aarch64):
  * `--cpu-only`: no GPU check, no CUDA/RAPIDS packages, no RAPIDS jar; main env from `requirements-cpu.txt`
    (Polars 1.44.2, numpy, packaging, typing-extensions, i.e. `requirements-gpu.txt` minus every GPU package; a test
    guards against drift); environment file `rerun/cpu-env.sh` exports `PYTHON_CPU_ENV` instead of
    `PYTHON_GPU_ENV`/`RAPIDS_JAR`. Works on x86_64 and aarch64 Ubuntu.
  * `--enable-rapl-read`: **lowers the CVE-2020-8694 mitigation.** `chmod a+r` on every `energy_uj` now plus a udev
    rule `/etc/udev/rules.d/99-benchmark-rapl-read.rules` (never overwrites a rule it did not write). Prints a
    warning. Undo: `--revert-rapl-read`. Single-tenant benchmark hosts only.
  * `--install-power-tools`: `apt-get install ipmitool linux-tools-common linux-tools-$(uname -r)` (two calls, so a
    missing kernel package does not block ipmitool). Never loads modules (it prints the `modprobe` line).
  * `--power-only`: run just the power steps on an already prepared host.
  * Fix for stock Ubuntu 24.04: when `pip install --user uv` fails with "externally-managed-environment", the script
    now retries with `--break-system-packages` (user site only). Found by running the script (below).
* `requirements-cpu.txt`, `tests/test_probe_power.py`, `tests/test_setup_modes.py`.
* `docs/paper3-evidence/prices-20261111/extract_gcp_official_prices.py` re-derives `gcp-official-prices.json` from the
  archived Google pages (`python3 -I`), and `tests/test_price_evidence.py` checks that the evidence bundle is intact
  (every file hashed in `SHA256SUMS` and named in `MANIFEST.md`, the archived pages carry no API keys, the extractor
  reproduces the committed JSON, the official C3 and Hyperdisk numbers of section 2.2, and the C3 quotas of section 2.3).

How the script was checked: unit tests with shims; a **real** (not dry-run) `--cpu-only` run, then a second run
(idempotence), then the power steps, inside Ubuntu 24.04 containers on this machine - once natively (aarch64) and
once emulated (x86_64): `uv` fallback exercised, 10 of 10 pins matched, `polars 1.44.2 pyspark 3.5.9` imported, no
CUDA/NVIDIA package installed. **Not** checked (no record of it in the repository) on a real cloud host, on Ubuntu 22.04, with real RAPL (the P3 host has it, root-only), or with a running `udevd` (the containers have none, so
the `--enable-rapl-read` rule text and its `%p` expansion are untested; the immediate `chmod` is what is tested).

## 7. Open risks

1. **CPU energy on the primary host is root-only.** RAPL works on `c3-highmem-192-metal` (P3) but every `energy_uj` is
   mode 0400. Either run the sampler as root or lower the CVE-2020-8694 mitigation with `--enable-rapl-read` on the
   (single-tenant) host; record which. **AWS metal is unprobed**: RAPL there is unverified, so the last-resort fallback
   may not give a measured CPU baseline at all.
2. **Perf and powercap disagree by a factor of two on P3.** `perf stat -a power/energy-pkg/` read about one package
   (176.7 W idle, 350.2 W busy) while powercap gave two (353.5 W, 698.1 W). Use powercap per package until the perf
   aggregation is understood; never report a `perf stat -a` pkg value as whole-host energy.
3. **Lambda prices change 2026-11-16 00:00 UTC** (+13 to +15 % for H100, GH200, B200, A100 80 GB; A100 40 GB, A10
   and the rest unchanged; the 8x A100 of P4 is unchanged). This document uses the new prices throughout and lists the old
   ones alongside; cost tables must not mix the two sets. Re-query the API on and after 11-16 to confirm it now returns
   the page prices. GCP and AWS prices do not change on that date.
4. **Capacity.** GCP metal: `c3-standard-192-metal` and `c3-highcpu-192-metal` were out of capacity in every US zone at
   about 15:00 UTC on 2026-11-11 (team lead's report, not archived); the primary shape launched in us-east4-a. us-east5
   cannot be a fallback (`C3_CPUS` = 24). Lambda: GH200, B200 and 4x/8x H100 showed no capacity at 14:46 UTC; the single
   H100 SXM5 slot is one region.
5. **Idle cost and idle power of the primary host.** $12.70/h whether or not anything runs, and the box idles at about
   430 W of RAPL (P3); a 26-thread run uses a small part of two 48-core sockets. Pin to one socket and its memory, read
   that package and its DRAM zone only, and log the other socket's idle floor. Metal boots took about 2 minutes on GCP
   (P3) and may take 20+ minutes on AWS (S7).
6. **Storage throughput.** Hyperdisk Balanced at its 140 MBps baseline makes SF300 generation I/O bound (12.9 min to
   write 108 GB); provision 1000 MBps and 16000 IOPS (+$0.136/h). Whether C3 metal allows those values is unverified.
7. **`preflight.py` pin check:** with the CPU env (Polars 1.44.2, no `cudf-polars`) its auto mode compares against
   `requirements.lock.txt` (Polars 2.0.0) and will report a mismatch; `--pins` has no CPU choice. The setup script
   already verifies every pin it installs. Either pass `--skip-env` (loses the Java/engine checks too) or add a
   CPU pin set to `preflight.py` - a file this task does not own.
8. **Linux tool packages:** `linux-tools-$(uname -r)` may not exist for a freshly booted cloud kernel (observed with
   a linuxkit kernel in the container run: the script warns and continues). On P3 (kernel 7.0.0-1017-gcp) the probe
   found `/usr/bin/perf` and `/usr/bin/turbostat` and ran perf as root; how they got there is not recorded.
   `turbostat` could not run because the host has no `/dev/cpu/*/msr`.
9. **`setup_h100.sh` on a real cloud host:** the updated probe has now run on real hosts (H100 re-run, P3, P4), but
   `setup_h100.sh --cpu-only`, `--enable-rapl-read` and `--power-only` have only run on fake trees and in containers.
10. **Unverified numbers:** GCS and Lambda egress, Lambda billing granularity, whether `--max-run-duration` actually
    deletes at expiry (the flags were accepted, the expiry was not observed), CloudWatch terminate on metal, Lambda
    launch-time tags, which NUMA nodes belong to which package on P3, Hyperdisk provisioned limits on C3 metal. The GCP
    prices are no longer on this list: they come from Google's own pages (S15).
11. **Shared accounts:** the AWS account and the GCP project `siriusmedai` already run instances that are not ours (the
    team lead says production VMs); a mistake in a cleanup command could hit them. Always filter by our label or tag.
12. **Leases are not yet recorded by the probes:** neither P3 nor P4 was given `--lease-name`, so their JSON has no
    lease. Pass it from the next launch on (section 3.2 step 2).

## 8. Sources (all accessed 2026-11-11)

* S1 Lambda Cloud API `GET https://cloud.lambdalabs.com/api/v1/instance-types` and `/instances` (authenticated, read-only; key never printed). Saved: `docs/paper3-evidence/prices-20261111/lambda-api-instance-types.json`, 2026-11-11T14:46:09Z, SHA-256 `a31b084545695985101a1a4658dfc4d5bda21c52e5c7080250b180332fa8b02d`. The `/instances` response (IP, tokens) is not archived.
* S2 https://lambda.com/pricing (redirect from lambda.ai/pricing); raw HTML saved as `docs/paper3-evidence/prices-20261111/lambda-pricing-page.html`, 2026-11-11T14:46:09Z, SHA-256 `3a277b420f1deba0cd83020467aeb9f7df0eb4ee4534866661f42bf59eb077fa` (same hash as a first fetch at about 13:50 UTC); the table rows were parsed with an HTML parser and cross-checked against the price strings in the raw HTML.
* S3 https://docs.lambda.com/public-cloud/on-demand/ (redirect from docs.lambda.ai); read through a summarising fetcher: instances are created, restarted, listed and terminated via the API; port 22 open by default; no stated billing granularity or egress policy.
* S4 AWS Price List API, `aws pricing get-products` (AmazonEC2, us-east-2, Linux, Shared, NA, Used; saved as `aws-price-list-us-east-2.json`, 2026-11-11T14:46:31Z-14:46:40Z, publication date 2026-11-10T17:38:52Z, SHA-256 in `MANIFEST.md`): instance prices, EBS gp3 $0.08/GB-month, $0.005 per provisioned IOPS-month above 3000, $40.96 per GiBps-month throughput, snapshots $0.05/GB-month.
* S5 `aws ec2 describe-instance-types` / `describe-instance-type-offerings` (us-east-2): cores, memory, `BareMetal`, `Hypervisor`.
* S6 `aws service-quotas list-service-quotas --service-code ec2` (us-east-2, us-east-1, us-west-2; saved without ARNs as `aws-service-quotas-ec2.json`, 2026-11-11T14:47Z) and `aws ec2 describe-instances` (us-east-2; not archived: instance ids).
* S7 https://docs.aws.amazon.com/ec2/latest/instancetypes/ec2-nitro-instances.html (bare metal "full access to host hardware"; "up to 20 minutes or more" to become reachable).
* S8 https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/processor_state_control.html (bare metal Intel and AMD: C-state and P-state control).
* S9 https://cloud.google.com/compute/docs/instances/bare-metal-instances (redirects to docs.cloud.google.com; "Last updated 2026-11-10 UTC"; C3/C4 metal: CPU counters exposed; IDPF; Hyperdisk only; no Local SSD on C3 metal; maintenance TERMINATE; no mention of RAPL). Saved gzip-compressed, with 9 public Google web API keys redacted, as `docs/paper3-evidence/prices-20261111/gcp-official/bare-metal-instances.html.gz`, 2026-11-11T16:24:11Z-16:24:12Z, SHA-256 in `MANIFEST.md`.
* S10 `gcloud compute machine-types list --filter=name~metal`, `gcloud compute regions describe` (us-west1, us-central1, us-east1 at 14:47Z; us-east4, us-east5, us-east1 at 16:27Z), `gcloud compute instances list`, project `siriusmedai`; saved: `gcp-metal-machine-types.csv`, `gcp-region-quotas.json` (2026-11-11T14:47Z), `gcp-region-quotas-us-east.json` (2026-11-11T16:27Z).
* S11 NVD API, CVE-2020-8694 (published 2020-11-12): "Insufficient access control in the Linux kernel driver for some Intel(R) Processors may allow an authenticated user to potentially enable information disclosure via local access."
* S12 https://docs.kernel.org/power/powercap/powercap.html (Intel RAPL sysfs layout; AMD and permissions not mentioned).
* S13 https://gcloud-compute.com/c3-standard-192-metal.html and `/c4-standard-288-metal.html` (saved as `gcloud-compute-com-*.html`, 2026-11-11T14:48Z, SHA-256 in `MANIFEST.md`; third party; "Last Update: Sun Nov 8 02:50:48 2026 (GMT)"; accuracy not guaranteed). **Superseded by S15**; kept as a cross-check (it agrees with S15 on c3-standard-192-metal and c4-standard-288-metal).
* S14 `gsutil ls -L -b`, `gsutil ls`, `gsutil du` on `gs://siriusmedai-gpu-benchmark-archive` (listing only; nothing copied); `REPRODUCE.md`; `evidence/INPUT_MANIFESTS/large-sf300-20261025/sf300.json`; `docs/paper3-evidence/probes/`.
* S15 Google Cloud's own price pages, fetched with `curl -sL` at 2026-11-11T16:21Z-16:24Z: https://cloud.google.com/products/compute/pricing/general-purpose (C3 standard / high-memory / high-CPU tables including `-metal`, per region, Hourly and Monthly; `https://cloud.google.com/compute/vm-instance-pricing` and `/compute/all-pricing` redirect to the landing page `/products/compute/pricing`, which has no tables) and https://cloud.google.com/compute/disks-image-pricing (Hyperdisk Balanced). Saved gzip-compressed (one Google web API key redacted in each) as `docs/paper3-evidence/prices-20261111/gcp-official/general-purpose.html.gz` and `disks-image-pricing.html.gz`; the numbers used here are in `gcp-official-prices.json`, produced by `extract_gcp_official_prices.py`; hashes, redaction and cross-checks in `MANIFEST.md`. The Cloud Billing Catalog API was not used (403 without credentials; not enabled in the project).
* P3 and P4 probe runs: `docs/paper3-evidence/probes/gcp-c3-highmem-192-metal-20261111T150427Z.json`, `lambda-8x-a100-20261111T152352Z.json`, and the earlier `lambda-h100-sxm5-*` files; the GCP launch facts (accepted `--max-run-duration`, ~2 minute boot, stockouts at about 15:00 UTC, the manual deletion before expiry) are the team lead's report and are not archived.
* Not obtained: `https://cloud.google.com/storage/pricing` (Cloud Storage and egress prices in section 4 remain from general knowledge), `https://docs.kernel.org/hwmon/amd_energy.html` (HTTP 404; no longer needed, AMD is dropped).
