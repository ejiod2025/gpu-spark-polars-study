# GCP `c3-highmem-192-metal` (us-east4-a) — pre-lock probe P5 + NUMA/RAPL follow-up, 2026-11-11

Pre-lock probe (no study queries run). Instance `p3-probe-c3hm-owner-ejiod-expires-20261111t2116z`, labels
`owner=ejiod purpose=paper3-power-probe expires=20261111t2116z`, launched 2026-11-11T20:32:31Z with
`--max-run-duration=45m --instance-termination-action=DELETE --maintenance-policy=TERMINATE`, a 2400 s
(40 min) local watchdog (`gcp_watchdog.sh`) as a second layer, 50 GB Hyperdisk Balanced boot disk, image
`ubuntu-2404-lts-amd64` (project `ubuntu-os-cloud`). Deleted manually at 2026-11-11T20:44:14Z after all
probes finished (11m43s runtime), confirmed gone by an immediate `gcloud compute instances list` (empty).
No AWS/Lambda resources touched for this host. **No study queries were run.**

Commands run (as the `danielejiofor` user unless noted), each written to the file named in brackets:

1. `python3 probe_power.py --lease-name <name> --no-load --output probe-before.json` [`...-probe-before.json`]
2. `sudo ./setup_h100.sh --power-only --enable-rapl-read --install-power-tools`
3. `python3 power_telemetry.py --check-cpu-energy require` (ok, not saved as a separate file — see below)
4. `python3 power_telemetry.py --topology` [`...-topology.json`]
5. `python3 power_telemetry.py --probe-membind --nodes 0,1` [`...-membind-socket0.json`]
6. `python3 p3_cpu_probes.py numa-bind --out numa-bind.json --mib 8192` [`...-numa-bind.json`]
7. `sudo python3 p3_cpu_probes.py rapl-perf --out rapl-perf.json --seconds 20` [`...-rapl-perf.json`]
8. Two 60 s idle windows and one 60 s busy window of `power_telemetry.py --output DIR --duration 60`
   [`...-idle1-sidecar.json`/`-idle1-power.csv`, `...-idle2-sidecar.json`/`-idle2-power.csv`,
   `...-busy1-sidecar.json`/`-busy1-power.csv`]; the busy window ran `p3_cpu_probes.py burn --cpus 0-12,96-108
   --seconds 60` (26 vCPUs = 13 physical cores + SMT siblings of socket 0, the real campaign pin) concurrently.

## Results

**Topology (verified, resolves an "unverified" item in `docs/PAPER3_INFRA.md` §2.1/§7):** socket 0 =
NUMA nodes {0,1} (CPUs 0-47,96-143); socket 1 = NUMA nodes {2,3} (CPUs 48-95,144-191). Sub-NUMA clustering
(2 nodes per socket) is active. The pinned 26 vCPUs (13 physical cores + SMT siblings, first 13 cores of
socket 0) are `0-12,96-108`.

**RAPL / CPU energy.** `setup_h100.sh --power-only --enable-rapl-read --install-power-tools` worked
cleanly on a real cloud host for the first time (previously only tested in containers/fake trees, infra
doc open risk #9): all 4 `energy_uj` files (`package-0`, `package-0/dram`, `package-1`, `package-1/dram`)
were `chmod a+r`'d, the udev rule was installed, and `power_telemetry.py --check-cpu-energy require`
then reported `ok: true, state: readable, cpu_energy_source: powercap-rapl` as the plain user — no root
needed afterward. `ipmitool`/`linux-tools` installed without error; no BMC device exists (as P3), so
there is still no IPMI/DCMI cross-check on this host.

**`perf stat --per-socket -a` vs `perf stat -a` vs powercap (resolves infra-doc open risk #2 with hard
numbers).** `perf stat --per-socket -a power/energy-pkg/,power/energy-ram/` matches powercap **per socket**
to within 0.05% idle and under a socket-0-only synthetic load (relative differences -0.05%..+0.001%
across all 4 socket x event rows, both windows). `perf stat -a` **without** `--per-socket` reads almost
exactly **half** of the sum of both packages' powercap readings: idle `perf_over_sum = 0.49997`, busy
`perf_over_sum = 0.49997`. Conclusion for the design: **use `perf stat --per-socket -a` (or powercap
directly) for whole-host or per-socket energy; a bare `perf stat -a power/energy-pkg/` must never be read
as whole-host energy** — it silently reports roughly 1/(socket count) of the total, not "one package's
worth" by name. Powercap per package stays the primary CPU-energy source (S2-pinned / S2-all), as already
decided; this is a cross-check confirmation, not a source change.

**Real NUMA binding (the hard requirement: binding must win over first-touch locality).**
`python3 power_telemetry.py --probe-membind --nodes 0,1` **exits 0** (`ok: true`, mechanism
`libnuma (libnuma.so.1) set_mempolicy`, readback mode=2/MPOL_BIND, nodes=[0,1]). The four
`p3_cpu_probes.py numa-bind` scenarios, each with the runner's real sequence (`resolve_pinning` ->
`plan_membind` -> `apply_membind` -> `apply_pinning`, confirmed identical in `run_experiment.py:326-361`)
and an ~8 GiB allocating grandchild (mmap + bytearray + ctypes buffer, one 4 KiB-page touch each),
read back from `/proc/<pid>/numa_maps`:

| Scenario | % of pages on node 0 | % on node 2 |
|---|---:|---:|
| `pin26-bound` (the real 26-vCPU pin + its membind) | 99.96% | 0.00% |
| `socket0-bound` (`--socket 0`) | 99.96% | 0.00% |
| `socket1-cpus-bound-to-socket0-nodes` (runs **on socket 1's CPUs**, memory **bound to socket 0's nodes**) | 99.96% | 0.01% |
| `socket1-cpus-unbound-control` (runs on socket 1's CPUs, **no** binding) | 0.05% | 99.91% |

The third row is the decisive case: a process scheduled on socket 1 still gets 99.96% of its memory on
socket 0 when bound there, i.e. **binding wins over first-touch locality**; the fourth row (control) shows
that without binding the same CPU placement puts 99.91% of memory on socket 1 (its own node), so the third
row's result is the binding acting, not a default outcome. **DRAM energy attribution to the pinned socket
is therefore sound** for the real 26-vCPU pin and for `--socket 0`.

**Sampler overhead (P5).** Idle: 0.40% (idle1) and 0.48% (idle2) of one core; busy (under the socket-0
burn): 0.39%. Mean poll time 1.1-1.6 ms, max 2.0 ms, 302/302 polls on schedule (0 overruns, max gap
0.2002 s at a 0.2 s interval), all 4 RAPL series "advancing" throughout every window (never stalled).

**Idle floor and idle stability (G10, +-10%).** idle1 vs idle2 relative difference: package-0 0.17%,
package-0/dram 0.03%, package-1 0.19%, package-1/dram 0.01% — all far inside +-10%. Idle totals (sum of 4
zones): idle1 429.02 W, idle2 429.05 W (0.0065% apart). Package-0 mean 179.26 W idle -> 302.47 W busy
(+68.8%, the 26-vCPU burn is pinned there); package-1 (left idle) 178.69 W idle -> 178.47 W busy (-0.12%,
confirming the burn did not leak to socket 1); DRAM on both sockets essentially flat between idle and busy
(CPU-only load does not drive DRAM power, as P3 also found).

## Cost

Launched 20:32:31Z, deleted 20:44:14Z: 11m43s = 703 s = 0.195278 h x $12.701568/h = **$2.48** (list price,
Hyperdisk Balanced negligible at this duration; corrected 2026-11-12 — an earlier draft of this file
miscomputed the interval as 12m43s/$2.69). Watchdog never fired (instance was deleted manually once probes
finished, well inside its 40-minute deadline and the 45-minute `--max-run-duration`).

## Anomalies / notes

None. No foreign instances were listed, touched or affected (only `p3-probe-c3hm-...` was ever queried or
acted on; `gcloud compute instances list` without a name filter was not run against the whole project).
`setup_h100.sh`'s `--enable-rapl-read` path (previously untested outside containers) worked first try with
no manual intervention. No study queries were run.

## Correction, 2026-11-15 (perf diagnosis)

The "perf and powercap" paragraph above draws the wrong conclusion from the `perf stat -a` rows. In
`...-rapl-perf.json`, bare `perf stat -a` returns **Joules equal to the two-package powercap sum** (idle: 7,138.3 J
against 3,569.1 + 3,584.7 = 7,153.8 J, -0.2%; busy: 9,623.7 J against 6,073.9 + 3,573.9 = 9,647.8 J, -0.25%), with a
`runtime` of 40.0 s for the 20 s window (summed over the two sockets). The `perf_over_sum = 0.49997` figure comes from
dividing those Joules by the summed runtime, so it is an artefact of the runtime column, not perf covering one package.
Rule unchanged: powercap is the primary CPU-energy source; any perf cross-check uses `--per-socket` and Joules, never
power derived from perf's runtime column. (Found by the design reviewer; `docs/PAPER3_ENERGY_DESIGN.md` §2.4 and
`docs/PAPER3_INFRA.md` open risk #2 carry the corrected wording.)

