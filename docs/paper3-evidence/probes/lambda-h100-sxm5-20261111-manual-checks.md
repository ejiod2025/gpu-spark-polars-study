# Lambda gpu_1x_h100_sxm5 (us-south-3) — manual follow-up checks, 2026-11-11 ~14:09 UTC

Pre-lock probe (no study queries run). Host: KVM VM, Intel Xeon Platinum 8480+, 13 cores / 26 threads,
driver 580.105.08. Companion file: lambda-h100-sxm5-*.json (probe_power.py output).

1. CPU energy: `sudo perf stat -a -e power/energy-psys/ sleep 5` under a 26-thread busy loop reported
   **0.00 J**. The psys event is exposed by the VM but does not count. No powercap RAPL zones, no
   /dev/cpu/*/msr, no IPMI. Conclusion: **CPU energy is not measurable on this Lambda VM type.**
2. GPU energy: NVML `nvmlDeviceGetTotalEnergyConsumption` (via ctypes on libnvidia-ml.so.1) returned
   rc=0 and advanced 355,288 mJ over 5 s idle (71.1 W), consistent with nvidia-smi power.draw 71.06 W.
   `nvidia-smi --query-gpu` does not offer the field, so the probe's "energy_counter_available: false"
   is a probe limitation, not a hardware one. Power limit (NVML): 700 W.

3. power.draw semantics (driver 580.105.08), ~14:17 UTC. Synthetic pulsed GPU load (a CUDA spin kernel,
   0.3 s on / 0.7 s off, 8 pulses; not a study query), sampled with
   `nvidia-smi --query-gpu=timestamp,power.draw,power.draw.average,power.draw.instant -lms 50`:
   228 samples; power.draw equalled power.draw.average in 228/228 and power.draw.instant in 1/228;
   corr(draw, average) = 1.000, corr(draw, instant) = 0.421; range draw/average 71–202 W vs instant 71–392 W.
   Conclusion: on this host `power.draw` is the trailing 1 s average; bursty load is strongly smoothed.

4. Instrumentation smoke test (uncommitted p3-telemetry code), ~14:18–14:21 UTC, synthetic load only:
   - `power_telemetry.py --probe --no-ipmi`: exit 3, status "unavailable" (no CPU energy source), as expected.
   - `--probe-gpu-energy --duration 5`: source nvml-ctypes; 1,298 reads, 0 failed; counter update interval
     median 99.999 ms (97.2–102.9); step median 7,495.5 mJ; read latency median 2.77 ms (max 18.3 ms); non-decreasing.
   - 60 s CPU sampler under load: 0 rows written (no fake zeros), state finished.
   - Per-record read, idle 5 s: 358,025 mJ = 71.6 W.
   - Per-record read around the 8-pulse load: NVML 1,627.4 J over 8.37 s (194.5 W) vs the integral of
     416 power.draw.instant samples at 20 ms over the same window: 1,624.7 J (194.2 W); difference 0.17%.
