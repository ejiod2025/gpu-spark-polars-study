### GPU memory per engine and scale (what is measured differs by engine: see the Measure column)

| Suite | Scale | Engine | Measure | Min–max over queries (GiB) | Median (GiB) | Max / device |
|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 2.0–2.0 | 2.0 | 5.0% |
| baseline | SF1 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| baseline | SF1 | Spark GPU | task-reported max (not a total) | 0.2–0.3 | 0.3 | 0.9% |
| baseline | SF10 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 8.5–8.5 | 8.5 | 21.3% |
| baseline | SF10 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| baseline | SF10 | Spark GPU | task-reported max (not a total) | 0.7–1.9 | 0.9 | 4.8% |
| baseline | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 9.7–9.7 | 9.7 | 24.1% |
| baseline | SF30 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| baseline | SF30 | Spark GPU | task-reported max (not a total) | 1.9–6.0 | 2.6 | 15.1% |
| baseline | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 17.6–17.8 | 17.7 | 44.5% |
| baseline | SF100 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| baseline | SF100 | Spark GPU | task-reported max (not a total) | 1.7–6.9 | 3.4 | 17.3% |
| baseline | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 22.7–22.7 | 22.7 | 56.6% |
| baseline | SF300 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| baseline | SF300 | Spark GPU | task-reported max (not a total) | 3.3–9.5 | 4.1 | 23.7% |
| complex | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 14.2–14.6 | 14.6 | 36.5% |
| complex | SF30 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| complex | SF30 | Spark GPU | task-reported max (not a total) | 0.8–5.5 | 2.5 | 13.7% |
| complex | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 20.0–20.0 | 20.0 | 49.9% |
| complex | SF100 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| complex | SF100 | Spark GPU | task-reported max (not a total) | 2.4–12.8 | 5.1 | 32.1% |
| complex | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 34.2–38.9 | 37.2 | 97.2% |
| complex | SF300 | Spark GPU | nvidia-smi, pool reservation | 38.9–38.9 | 38.9 | 97.2% |
| complex | SF300 | Spark GPU | task-reported max (not a total) | 2.3–31.4 | 5.9 | 78.4% |

max_bytes / median_bytes / min_bytes are the nvidia-smi samples of the worker process tree during the timed action (worker peak_gpu_memory_bytes); maximum over the 3 measured trials, then over rounds; a sampled peak can miss short transients. What the sample means differs by engine: see measure / measure_note, and the task_reported blocks for Spark GPU Polars GPU: nvidia-smi device memory of the worker process, sampled during the timed action. cudf-polars allocates through an RMM async pool that keeps freed blocks (release threshold 90% of the device), so the value is the pool's high-water mark in that session so far, not the working set of the query: it did not fall in 28 of 32 sessions (see high_water_check) but fell in the other 4 (by up to 18.4 GiB; their samples reach 97% of the device), so there it is the pool size at the time of the action, and an upper bound on what the query needs only while the pool keeps its blocks. Spark GPU: the largest device allocation a single task reported (RAPIDS task accumulator gpuMaxDeviceMemoryBytes, Spark event log) among the tasks launched inside the action; not a simultaneous total over concurrent tasks. The nvidia-smi samples (max_bytes, ...) are the RAPIDS pool reservation made at plugin start, not memory in use.
