### GPU memory per engine and scale (what is measured differs by engine: see the Measure column)

| Suite | Scale | Engine | Measure | Min–max over queries (GiB) | Median (GiB) | Max / device |
|---|---|---|---|---|---|---|
| baseline | SF1 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 1.9–1.9 | 1.9 | 2.4% |
| baseline | SF1 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| baseline | SF1 | Spark GPU | task-reported max (not a total) | 0.2–0.3 | 0.2 | 0.3% |
| baseline | SF10 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 9.4–9.4 | 9.4 | 11.8% |
| baseline | SF10 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| baseline | SF10 | Spark GPU | task-reported max (not a total) | 0.6–1.7 | 0.7 | 2.1% |
| baseline | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 11.1–11.4 | 11.2 | 14.3% |
| baseline | SF30 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| baseline | SF30 | Spark GPU | task-reported max (not a total) | 1.5–6.1 | 1.9 | 7.7% |
| baseline | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 14.7–14.7 | 14.7 | 18.5% |
| baseline | SF100 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| baseline | SF100 | Spark GPU | task-reported max (not a total) | 1.9–6.5 | 3.2 | 8.2% |
| baseline | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 29.1–29.1 | 29.1 | 36.6% |
| baseline | SF300 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| baseline | SF300 | Spark GPU | task-reported max (not a total) | 2.7–9.7 | 3.4 | 12.1% |
| complex | SF30 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 16.1–16.1 | 16.1 | 20.2% |
| complex | SF30 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| complex | SF30 | Spark GPU | task-reported max (not a total) | 0.8–5.2 | 2.2 | 6.5% |
| complex | SF100 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 22.4–22.4 | 22.4 | 28.2% |
| complex | SF100 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| complex | SF100 | Spark GPU | task-reported max (not a total) | 1.4–12.3 | 3.7 | 15.4% |
| complex | SF300 | Polars GPU | nvidia-smi, process high-water mark (RMM pool) | 40.0–40.0 | 40.0 | 50.3% |
| complex | SF300 | Spark GPU | nvidia-smi, pool reservation | 78.6–78.6 | 78.6 | 98.7% |
| complex | SF300 | Spark GPU | task-reported max (not a total) | 1.6–30.0 | 5.5 | 37.7% |

max_bytes / median_bytes / min_bytes are the nvidia-smi samples of the worker process tree during the timed action (worker peak_gpu_memory_bytes); maximum over the 3 measured trials, then over rounds; a sampled peak can miss short transients. What the sample means differs by engine: see measure / measure_note, and the task_reported blocks for Spark GPU Polars GPU: nvidia-smi device memory of the worker process, sampled during the timed action. cudf-polars allocates through an RMM async pool that keeps freed blocks (release threshold 90% of the device), so the value is the pool's high-water mark in that session so far, not the working set of the query: it never falls within a session (see high_water_check) and an upper bound on what the query needs. Spark GPU: the largest device allocation a single task reported (RAPIDS task accumulator gpuMaxDeviceMemoryBytes, Spark event log) among the tasks launched inside the action; not a simultaneous total over concurrent tasks. The nvidia-smi samples (max_bytes, ...) are the RAPIDS pool reservation made at plugin start, not memory in use.
