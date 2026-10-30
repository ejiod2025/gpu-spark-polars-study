### GPU memory per engine and scale (what is measured differs by engine: see the Measure column)

| Suite | Scale | Engine | Measure | Min–max over queries (GiB) | Median (GiB) | Max / device |
|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 2.2–2.2 | 2.2 | 2.3% |
| baseline | SF1 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| baseline | SF1 | Spark GPU | task-reported max (not a total) | 0.2–0.3 | 0.2 | 0.3% |
| baseline | SF10 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 10.1–10.5 | 10.3 | 11.0% |
| baseline | SF10 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| baseline | SF10 | Spark GPU | task-reported max (not a total) | 0.6–1.7 | 0.7 | 1.8% |
| baseline | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 11.7–11.8 | 11.8 | 12.4% |
| baseline | SF30 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| baseline | SF30 | Spark GPU | task-reported max (not a total) | 1.4–5.4 | 1.6 | 5.7% |
| baseline | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 21.1–21.2 | 21.1 | 22.2% |
| baseline | SF100 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| baseline | SF100 | Spark GPU | task-reported max (not a total) | 1.3–6.5 | 3.2 | 6.8% |
| baseline | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 29.8–29.8 | 29.8 | 31.2% |
| baseline | SF300 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| baseline | SF300 | Spark GPU | task-reported max (not a total) | 2.6–13.5 | 5.4 | 14.1% |
| complex | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 14.7–16.5 | 16.5 | 17.3% |
| complex | SF30 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| complex | SF30 | Spark GPU | task-reported max (not a total) | 0.6–5.8 | 2.4 | 6.1% |
| complex | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 22.0–22.0 | 22.0 | 23.0% |
| complex | SF100 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| complex | SF100 | Spark GPU | task-reported max (not a total) | 1.6–11.8 | 5.3 | 12.3% |
| complex | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 42.4–42.4 | 42.4 | 44.4% |
| complex | SF300 | Spark GPU | nvidia-smi, pool reservation | 93.9–93.9 | 93.9 | 98.2% |
| complex | SF300 | Spark GPU | task-reported max (not a total) | 1.8–28.3 | 5.5 | 29.6% |

max_bytes / median_bytes / min_bytes are the nvidia-smi samples of the worker process tree during the timed action (worker peak_gpu_memory_bytes); maximum over the 3 measured trials, then over rounds; a sampled peak can miss short transients. What the sample means differs by engine: see measure / measure_note, and the task_reported blocks for Spark GPU Polars GPU: nvidia-smi device memory of the worker process, sampled during the timed action. cudf-polars allocates through an RMM async pool that keeps freed blocks (release threshold 90% of the device), so the value is the pool's high-water mark in that session so far, not the working set of the query: it never falls within a session (see high_water_check) and an upper bound on what the query needs. Spark GPU: the largest device allocation a single task reported (RAPIDS task accumulator gpuMaxDeviceMemoryBytes, Spark event log) among the tasks launched inside the action; not a simultaneous total over concurrent tasks. The nvidia-smi samples (max_bytes, ...) are the RAPIDS pool reservation made at plugin start, not memory in use.
