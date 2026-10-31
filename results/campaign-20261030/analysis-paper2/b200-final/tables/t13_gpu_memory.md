### GPU memory per engine and scale (what is measured differs by engine: see the Measure column)

| Suite | Scale | Engine | Measure | Min–max over queries (GiB) | Median (GiB) | Max / device |
|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 2.2–2.2 | 2.2 | 1.2% |
| baseline | SF1 | Spark GPU | nvidia-smi sample (not constant within sessions) | 2.6–2.6 | 2.6 | 1.5% |
| baseline | SF1 | Spark GPU | task-reported max (not a total) | 0.2–0.3 | 0.2 | 0.1% |
| baseline | SF10 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 9.4–10.1 | 9.8 | 5.7% |
| baseline | SF10 | Spark GPU | nvidia-smi sample (not constant within sessions) | 3.8–3.8 | 3.8 | 2.1% |
| baseline | SF10 | Spark GPU | task-reported max (not a total) | 0.5–1.3 | 0.7 | 0.7% |
| baseline | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 11.0–11.9 | 11.5 | 6.7% |
| baseline | SF30 | Spark GPU | nvidia-smi sample (not constant within sessions) | 8.9–8.9 | 8.9 | 5.0% |
| baseline | SF30 | Spark GPU | task-reported max (not a total) | 1.2–5.3 | 1.9 | 2.9% |
| baseline | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 14.4–14.4 | 14.4 | 8.0% |
| baseline | SF100 | Spark GPU | nvidia-smi sample (not constant within sessions) | 9.5–9.5 | 9.5 | 5.3% |
| baseline | SF100 | Spark GPU | task-reported max (not a total) | 1.6–6.5 | 3.2 | 3.6% |
| baseline | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 22.8–22.8 | 22.8 | 12.8% |
| baseline | SF300 | Spark GPU | nvidia-smi sample (not constant within sessions) | 13.3–13.3 | 13.3 | 7.4% |
| baseline | SF300 | Spark GPU | task-reported max (not a total) | 2.6–10.5 | 3.7 | 5.9% |
| complex | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 16.7–16.7 | 16.7 | 9.3% |
| complex | SF30 | Spark GPU | nvidia-smi sample (not constant within sessions) | 7.8–7.8 | 7.8 | 4.4% |
| complex | SF30 | Spark GPU | task-reported max (not a total) | 0.6–5.2 | 2.5 | 2.9% |
| complex | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 22.9–22.9 | 22.9 | 12.8% |
| complex | SF100 | Spark GPU | nvidia-smi sample (not constant within sessions) | 18.0–18.0 | 18.0 | 10.0% |
| complex | SF100 | Spark GPU | task-reported max (not a total) | 2.0–13.5 | 5.8 | 7.6% |
| complex | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 40.9–40.9 | 40.9 | 22.9% |
| complex | SF300 | Spark GPU | nvidia-smi sample (not constant within sessions) | 37.9–37.9 | 37.9 | 21.2% |
| complex | SF300 | Spark GPU | task-reported max (not a total) | 1.7–34.0 | 5.8 | 19.0% |

max_bytes / median_bytes / min_bytes are the nvidia-smi samples of the worker process tree during the timed action (worker peak_gpu_memory_bytes); maximum over the 3 measured trials, then over rounds; a sampled peak can miss short transients. What the sample means differs by engine: see measure / measure_note, and the task_reported blocks for Spark GPU Polars GPU: nvidia-smi device memory of the worker process, sampled during the timed action. cudf-polars allocates through an RMM async pool that keeps freed blocks (release threshold 90% of the device), so the value is the pool's high-water mark in that session so far, not the working set of the query: it never falls within a session (see high_water_check) and an upper bound on what the query needs. Spark GPU: the largest device allocation a single task reported (RAPIDS task accumulator gpuMaxDeviceMemoryBytes, Spark event log) among the tasks launched inside the action; not a simultaneous total over concurrent tasks. The nvidia-smi samples (max_bytes, ...) are the worker process tree's device memory when the action was sampled; they are constant within a session in only 0 of 32 sessions, so on this host they are not a fixed pool reservation (the first sampled action of a session reads 177.8 GiB and the minimum over the later actions 8.3 GiB, medians over sessions) and they are not task-level usage.
