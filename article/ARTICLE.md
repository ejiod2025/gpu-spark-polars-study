<!--
DRAFT companion article. Methods written before any results existed; findings
drafted from the final analysis outputs (2026-10-30).
Every number is a {{key}} (or {{ci:prefix}} = "estimate× (95% CI low–high)")
placeholder filled from the analysis stats.json by article/tools/fill_article.py.
Host blocks (A100, GH200, B200 ladder campaigns; "any" = at least one ran) are
IF:<host> / ELSE:<host> / ENDIF:<host> comments resolved by the same tool; every
host needs a stated outcome before the final fill.
Findings drafted from the final analysis outputs (2026-10-30); every number is a
placeholder filled from the stats, and every claim follows its interval. Do not
publish any sentence whose claim the intervals do not support.
Figure paths point at paper/figures/ (copied there by paper/tools/sync_figures.py).
-->

# Do you need a GPU for your dataframes? We timed Polars and Spark, with and without one

*A pre-registered, four-engine comparison on one NVIDIA H100 host, from 6 million to 1.8 billion rows<!-- IF:any -->, repeated on {{hosts.other_ran_list}}<!-- ENDIF:any -->. One catch shapes every Polars GPU number: the GPU engine only runs on an older Polars release.*

**By Daniel Ejiofor** · Boston University · ejiod@bu.edu · [TODO(author): publication date]

> **Notice.** The study workload is derived from the TPC-H Benchmark and as such is not comparable to published TPC-H Benchmark results, as the study workload results do not comply with the TPC-H Benchmark Specification. We ran a selected, adapted subset of queries plus custom ones, on generated data, with our own timing method. Nothing here is an official or audited benchmark result.

> **Read this first.** The Polars GPU engine requires Polars 1.44, so our "Polars GPU versus Polars CPU" numbers compare the GPU with Polars 1.44 on the CPU. The current Polars 2.0 on the CPU was {{bridge_summary.both}}; the time ratio is 2.0 time divided by 1.44 time. Where Polars 2.0 is faster, the GPU's advantage over the newest CPU Polars is smaller than the headline ratios below suggest.

Put a GPU in the machine and your analytics get faster. That is the pitch, and vendor charts back it up with large multiples. But those charts usually compare a framework with itself: Spark on a GPU against Spark on a CPU, or Polars on a GPU against Polars on a CPU. That is a fair question, but it is not the one most teams face. The question we hear is closer to this:

**If I can choose any engine for this job on this one machine, is a GPU engine faster than the best CPU option, and at what data size does that start to be true?**

We built an experiment to answer that as carefully as we could. The design was written down and locked before we collected a single timing in these campaigns, and the four changes we made later (all before the data they concern existed) are dated in the same document, which is public: it was committed and pushed as commit `ee2e6a7` of the study repository on 30 October 2026, with the last change added in commit `afd07c0`. One caveat: before the lock, we ran exploratory H100 campaigns (26–28 October) under an earlier design that was not pre-registered. We saw their results, and they shaped the design (that is where we found the two GPU gaps described below), but none of their data appears here. Their history is public on the repository's `H100_main_dev` branch.

The short answer: yes, on these machines a GPU engine beat the fastest CPU engine we tested, from {{crossover.baseline.c4.scale}} upward on the simpler queries and at every size of the harder ones, by {{ci:c4.baseline.sf300}} and {{ci:c4.complex.sf300}} at the largest size. On the smallest data the CPU won. And the comparison is against an older Polars release; against the current one, the GPU's margin shrinks.

## The four contenders

We ran four engines on the same machine, reading the same files:

- **Polars (CPU).** A fast, Rust-based dataframe library, using its streaming engine on 26 CPU threads.
- **Polars (GPU).** The same Polars code, executed by NVIDIA's cuDF-based GPU engine. We set it to *fail* rather than silently fall back to the CPU, so every "GPU" time really is a GPU time.
- **Spark (CPU).** Apache Spark SQL in single-machine mode with 26 cores, the configuration many teams already run.
- **Spark (GPU).** The same Spark SQL with NVIDIA's cuDF plugin for Apache Spark (formerly the RAPIDS Accelerator), which swaps supported operators for GPU versions.

The main machine was a single cloud instance with one H100 80 GB GPU, 26 virtual CPUs and {{host.host_ram_gib}} GiB of RAM. Nothing was tuned for the occasion. Spark used exactly the settings from our earlier A100 study, and the Polars GPU engine used its defaults. We then repeated the whole experiment on other GPU generations; more on that [below](#does-a-newer-gpu-help).

## What we did

```text
 DATA        DuckDB's TPC-H-style generator, 5 sizes: SF1 -> SF10 -> SF30 -> SF100 -> SF300
             (6 million -> 1.8 billion line-item rows), Parquet files, verified against
             the earlier study's content fingerprints before any timing
   |
 QUERIES     Baseline suite: 6 adapted TPC-H queries (scans, aggregations, 1-3 joins)
             Complex suite:  6 harder queries (6-table join, outer join, EXISTS/NOT EXISTS,
                             window functions, exact distinct counts, self-join), SF30-SF300
   |
 SESSION     one engine, one suite, one size: warm the files -> 1 warmup per query
             -> 3 timed runs per query (shuffled) -> keep the median
   |
 ROUND       all 4 engines x all 8 suite/size combinations = 32 sessions
   |
 CAMPAIGN    4 rounds; engine order rotated by a balanced Latin square, so every engine
             goes first, second, third and last once, and follows every other engine once
   |
 GATES       every answer must match Polars CPU; the Polars GPU engine must not fall
             back to the CPU; Spark GPU plans must contain GPU operators. Gates never
             look at speed.
   |
 STATS       per query: GPU speedup ratio in each round -> geometric mean over 4 rounds
             with a 95% confidence interval
```

The timer starts when the query is built and stops when the result is back in host memory, so it includes planning, reading Parquet, computing and (for the GPU engines) copying the answer back from the GPU. It does not include starting the engine, which matters a lot for Spark; we report startup separately.

Two choices deserve a word. First, we repeated the whole experiment four times, in different engine orders, instead of reporting one run. Machines drift, caches warm and cool, and the engine that happens to run first can be helped or hurt. Rotating the order and repeating it lets us put an honest error bar on every ratio. Second, we report the *geometric mean* of per-query speedups. This gives each query equal weight, so one slow query cannot dominate the summary.

## Finding 1: on large data the GPU speeds up both frameworks; on small data it slows them down

At the largest size (SF300, 1.8 billion rows), moving Polars from the CPU to the GPU changed its baseline-suite time by a factor of {{ci:c1.baseline.sf300}}. For Spark, adding the GPU plugin gave {{ci:c2.baseline.sf300}}. On the complex suite the corresponding figures were {{ci:c1.complex.sf300}} for Polars (over the {{c1.complex.sf300.n_queries}} complex queries it can run) and {{ci:c2.complex.sf300}} for Spark. A ratio above 1 means the GPU version was faster.

At the smallest size (SF1, 6 million rows) the picture reversed: {{ci:c1.baseline.sf1}} for Polars and {{ci:c2.baseline.sf1}} for Spark, so the GPU versions were slower. Across sizes, the Polars ratio was {{c1.baseline.summary.pattern}} on the baseline suite, and it was larger on the complex suite at {{c1.complex_larger_scales}} (the sizes both suites share). A GPU engine pays fixed costs for every query, such as setting up the device and copying results back, and a small input does not give it enough work to earn them back.

Keep the catch from the top in mind: the Polars figures are GPU against Polars 1.44 on the CPU. The newer Polars 2.0 on the CPU took {{bridge_summary.complex.min}} to {{bridge_summary.complex.max}} of 1.44's time on the complex suite, and on one query ({{bridge_summary.complex.extreme_query}}) only {{bridge_summary.complex.extreme.min}} to {{bridge_summary.complex.extreme.max}}. After seeing that, we added one comparison we had not planned in advance (so treat it as exploratory, not as a pre-registered result): the Polars GPU engine against Polars 2.0 on the CPU. At SF300 that ratio was {{ci:exploratory.e1.baseline.sf300}} on the baseline suite and {{ci:exploratory.e1.complex.sf300}} on the complex suite, against {{ci:c1.baseline.sf300}} and {{ci:c1.complex.sf300}} for the planned comparison with Polars 1.44. The two sides of this exploratory ratio come from different runs, so its interval is computed differently from the others. Even so, it stayed above 1 at {{exploratory.e1.baseline.summary.above}} on the baseline suite: smaller, but still a GPU win.

![Per-query GPU speedups with 95% confidence intervals](../paper/figures/f01_forest_sf300.png)
*Figure 1. Per-query speedups at SF300 by contrast, with 95% confidence intervals across four rounds. Right of the line means the second engine is faster. Derived from TPC-H; not comparable to published TPC-H results.*

## Finding 2: Polars on the GPU won most cases, but not on smaller data

This is the comparison that matters if you can choose any engine. Taking the faster of the two GPU engines for each query, the GPU's advantage over Polars CPU at SF300 was {{ci:c4.baseline.sf300}} on the baseline suite and {{ci:c4.complex.sf300}} on the complex suite.

Counting the fastest engine for each query and size, Polars CPU won {{fastest.polars.baseline.count}} of {{fastest.total.baseline.count}} baseline cases, Polars GPU {{fastest.polars_gpu.baseline.count}}, Spark CPU {{fastest.spark_cpu.baseline.count}} and Spark GPU {{fastest.spark_gpu.baseline.count}}. On the complex suite the counts were {{fastest.polars.complex.count}}, {{fastest.polars_gpu.complex.count}}, {{fastest.spark_cpu.complex.count}} and {{fastest.spark_gpu.complex.count}} out of {{fastest.total.complex.count}}. Polars CPU's baseline wins were all at the smaller sizes: {{fastest.polars.baseline.won_text}}. On the complex suite, Polars GPU won {{fastest.polars_gpu.complex.of_supported}} cases it could run, and Spark GPU won most of the rest, because it can run the two queries Polars GPU cannot. Between the two GPU engines it was not close: Spark GPU took between {{ladder.c3.min}} and {{ladder.c3.max}} times as long as Polars GPU across all four machines, sizes and both suites ({{ci:c3.baseline.sf300}} on the H100's baseline suite at SF300). Both run on the same NVIDIA library underneath, so the gap is in what surrounds it: Spark's JVM, task scheduling and shuffles.

![Median time per query and engine](../paper/figures/f10_headline_times.png)
*Figure 2. Median time per query for each engine at SF300. Lower is better; a missing bar means the query is not supported on that GPU engine. Derived from TPC-H; not comparable to published TPC-H results.*

## Finding 3: the GPU took over well before the largest size

We decided in advance what "the GPU takes over" would mean: the smallest data size at which the confidence interval for "best GPU engine versus Polars CPU" lies entirely above 1. By that rule, the crossover for the baseline suite was **{{crossover.baseline.c4.scale}}**, and for the complex suite **{{crossover.complex.c4.scale}}**.

On the other three machines the baseline crossover was {{a100.crossover.baseline.c4.scale}} (A100), {{gh200.crossover.baseline.c4.scale}} (GH200) and {{b200.crossover.baseline.c4.scale}} (B200). Remember that we only measured five sizes: a crossover "at SF30" means somewhere between SF10 and SF30, not a precise threshold, and nothing here extrapolates beyond SF300.

![Suite speedup versus data size](../paper/figures/f02_suite_speedup_vs_scale.png)
*Figure 3. Suite-level geometric-mean speedup against data size, with 95% confidence bands. Lines connect measured sizes only. Derived from TPC-H; not comparable to published TPC-H results.*

## Finding 4: the GPU engine does not support everything (and we did not let it pretend)

By default, the Polars GPU engine quietly hands any query it cannot handle back to the CPU. That is convenient in production but misleading in a benchmark, so we turned it off. Under that strict setting it ran {{coverage.polars_gpu.complex.supported}} of {{coverage.polars_gpu.complex.total}} complex query-size cases. The unsupported queries were {{coverage.polars_gpu.unsupported_list}}. We found both gaps in a dry run before the experiment and declared them in advance. One uses a regular-expression flag the GPU engine does not accept; the other uses a running count inside a window function that it has not implemented yet. Neither is exotic. Running out of GPU memory would have counted as a coverage result too, not as a failed experiment; the number of such cases across all four machines, including the A100 with only 40 GB, was {{ladder.resource_exhaustion_total}}. Spark's GPU plugin, which can run part of a plan on the CPU when it must, produced plans with GPU operators for {{coverage.spark_gpu.complex.supported}} of {{coverage.spark_gpu.complex.total}} complex cases.

GPU memory is harder to read than it looks, because both engines reserve memory in pools. For Polars GPU, the most memory we saw on the complex suite at SF300 was {{peak_gpu_mem_gib.polars_gpu.complex.sf300.max}} GiB, but its pool keeps memory it has freed, so that is an upper bound on what a query needs. Spark GPU reserves a large pool when it starts ({{pool_gpu_mem_gib.spark_gpu.complex.sf300.max}} GiB here), whatever the query; the most any single Spark task reported using was {{task_gpu_mem_gib.spark_gpu.complex.sf300.max}} GiB. Several tasks can run at once, so that is not the total either.

And the answers were right: of {{ladder.outputs_compared_total}} saved results compared with Polars CPU across the four machines, {{ladder.outputs_failed_total}} disagreed.

![GPU coverage matrix](../paper/figures/f04_gpu_coverage.png)
*Figure 4. Which queries ran fully on each GPU engine, by size.*

## Does a newer GPU help?

We planned the same experiment on a ladder of four GPU generations, each on its own machine: an A100 (40 GB, the GPU of our earlier study), the H100 above, a GH200 (an H100-class GPU joined to a 64-core Arm CPU) and a B200 (NVIDIA's Blackwell, 180 GB). Each machine ran the complete experiment on its own and was analyzed on its own; we never mixed timings from different machines. Every engine used 26 CPU threads on every machine, even where the machine had more, so the CPU side stays comparable.

<!-- IF:any -->
The experiment ran on {{hosts.ran_list}}. The short version: moving from the A100 to the H100 helped both GPU engines (the A100 took {{ladder.xhost_gpu.a100.min}} to {{ladder.xhost_gpu.a100.max}} times as long), but going beyond the H100 did not consistently help (the {{ladder.xhost_gpu.later.hosts}} took {{ladder.xhost_gpu.later.min}} to {{ladder.xhost_gpu.later.max}} times as long). Polars GPU on the complex queries was even a little slower on the B200 than on the H100 (up to {{xhost.b200.polars_gpu.complex.summary.max}} times as long). These timings do not tell us why. First question: does the same engine get faster? Here is each machine's time for the same suite at SF300, relative to the H100 (below 1 means faster than on the H100):

| Machine | Polars GPU | Spark GPU | Polars CPU | Spark CPU |
|---|---|---|---|---|
<!-- IF:a100 -->
| A100 | {{ci:xhost.a100.polars_gpu.baseline.sf300}} | {{ci:xhost.a100.spark_gpu.baseline.sf300}} | {{ci:xhost.a100.polars.baseline.sf300}} | {{ci:xhost.a100.spark_cpu.baseline.sf300}} |
<!-- ENDIF:a100 -->
| H100 | 1 (reference) | 1 (reference) | 1 (reference) | 1 (reference) |
<!-- IF:gh200 -->
| GH200 | {{ci:xhost.gh200.polars_gpu.baseline.sf300}} | {{ci:xhost.gh200.spark_gpu.baseline.sf300}} | {{ci:xhost.gh200.polars.baseline.sf300}} | {{ci:xhost.gh200.spark_cpu.baseline.sf300}} |
<!-- ENDIF:gh200 -->
<!-- IF:b200 -->
| B200 | {{ci:xhost.b200.polars_gpu.baseline.sf300}} | {{ci:xhost.b200.spark_gpu.baseline.sf300}} | {{ci:xhost.b200.polars.baseline.sf300}} | {{ci:xhost.b200.spark_cpu.baseline.sf300}} |
<!-- ENDIF:b200 -->

*Baseline suite, SF300: geometric mean of per-query time ratios against the H100, with 95% intervals. Each machine differs in more than its GPU (CPU, memory, driver), so these are whole-machine comparisons.*

Second question, the one that matters for choosing an engine: on each machine, does the best GPU engine beat Polars on that machine's own CPU? At SF300 on the baseline suite:

| Machine | Best GPU engine vs Polars CPU (above 1: GPU faster) |
|---|---|
<!-- IF:a100 -->
| A100 | {{ci:a100.c4.baseline.sf300}} |
<!-- ENDIF:a100 -->
| H100 | {{ci:c4.baseline.sf300}} |
<!-- IF:gh200 -->
| GH200 | {{ci:gh200.c4.baseline.sf300}} |
<!-- ENDIF:gh200 -->
<!-- IF:b200 -->
| B200 | {{ci:b200.c4.baseline.sf300}} |
<!-- ENDIF:b200 -->

On every machine the answer had the same shape: the CPU won on the smallest data and the GPU won on the largest. A newer GPU can make the GPU engines faster without changing this answer, because the CPU in the machine changes too.
<!-- IF:a100 -->

On the A100 this also fills the gap in our earlier study, which had no Polars GPU engine and found Polars on the CPU ahead overall. With Polars GPU included, the best GPU engine beat Polars CPU on the A100 too, from {{a100.crossover.baseline.c4.scale}} on the baseline suite. With only 40 GB on the card, running out of GPU memory was a real possibility at SF300, but it happened in {{a100.coverage.polars_gpu.complex.resource_exhaustion}} complex cases for Polars GPU and {{a100.coverage.spark_gpu.complex.resource_exhaustion}} for Spark GPU.
<!-- ENDIF:a100 -->
<!-- IF:gh200 -->

The GH200 is the odd one out: its GPU is the same generation as the H100's, but its CPU is an Arm chip, and the CPU engines run Arm builds of the same software versions. So on that machine both sides of the comparison move at once. Polars on the CPU was faster there (it took {{xhost.gh200.polars.overall.min}} to {{xhost.gh200.polars.overall.max}} of the H100's time, with its 26 threads on {{gh200.host.cpus_arg_cores_text}} physical cores against {{host.cpus_arg_cores_text}} on the H100), while the GPU engines took {{ladder.xhost_gpu.gh200.min}} to {{ladder.xhost_gpu.gh200.max}} of the H100's time. The GH200's smaller GPU advantage therefore comes from its faster CPU side, not from a slower GPU side.
<!-- ENDIF:gh200 -->

![The GPU-generation ladder](../paper/figures/f11_gpu_ladder.png)
*Figure 5. Each engine's suite time on each machine relative to the H100 (machines left to right in GPU-generation order; one marker per data size, smallest to largest), with 95% intervals. Below 1 means faster than on the H100. Machines that did not run are left out. Derived from TPC-H; not comparable to published TPC-H results.*
<!-- ENDIF:any -->
<!-- IF:a100 --><!-- ELSE:a100 -->

The A100 run did not happen: {{a100.not_run_reason}}.
<!-- ENDIF:a100 -->
<!-- IF:gh200 --><!-- ELSE:gh200 -->

The GH200 run did not happen: {{gh200.not_run_reason}}.
<!-- ENDIF:gh200 -->
<!-- IF:b200 --><!-- ELSE:b200 -->

The B200 run did not happen: {{b200.not_run_reason}}.
<!-- ENDIF:b200 -->
<!-- IF:any --><!-- ELSE:any -->

We had committed in advance not to change software versions to make another machine work, so every result here comes from the H100.
<!-- ENDIF:any -->

## What it means for you

- **Benchmark against the best CPU option, not just your current one.** A GPU speedup over CPU Spark ({{ci:c2.baseline.sf300}} here) and a GPU speedup over a fast single-node CPU engine ({{ci:c4.baseline.sf300}}) are different numbers. Make sure you know which one you are looking at.
- **Try the strict mode first.** If you evaluate the Polars GPU engine, run your workload once with `pl.GPUEngine(raise_on_fail=True)`. If it raises, you have found a query that would silently run on the CPU in production.
- **Size matters, in both directions.** On our smallest data the CPU was faster; from {{crossover.baseline.c4.scale}} on, the GPU was. Small jobs pay fixed costs (engine start-up, data transfer to the GPU) that large ones amortize. Spark's start-up alone was about {{startup.spark_gpu.median_s}} seconds with the GPU plugin, against {{startup.polars.median_s}} seconds for Polars CPU, before any query ran.
- **Query shape matters more than averages.** Look at the per-query chart before the summary. The GPU helped most on grouped aggregations and joins (for example q01, {{ci:c1.baseline.sf300.q.q01}} at SF300) and least on a simple filter-and-sum (q06, {{ci:c1.baseline.sf300.q.q06}}, an interval that {{c1.baseline.sf300.q.q06.verdict}}).
- **Pin your versions.** The Polars GPU engine supports a narrow range of Polars versions, which is why this study used Polars 1.44.2 rather than 2.0. Changing the Polars version alone moved CPU times by {{ci:bridge.baseline.sf300}} at SF300 in our side study (2.0.0 time divided by 1.44.2 time).

## What this does not show

We want to be clear about the limits, because single-machine benchmarks are easy to over-read.

- **A few machines.** The main results come from one cloud instance with 26 vCPUs and one H100, checked on three other single-GPU machines. They say nothing about Spark clusters, object storage, other GPUs, or a CPU-only machine of the same price, which could have many more cores.
- **Not TPC-H.** We used a selected, adapted subset of TPC-H-derived queries plus custom ones, on generated data, with decimals converted to floating point. The numbers are not comparable to official TPC-H results.
- **Untuned.** Spark kept the settings from our earlier study and the Polars GPU engine kept its defaults. Tuning might change any of these results, in either direction.
- **Four rounds.** Repeating the experiment four times gives honest but wide confidence intervals. An interval that includes 1 means "not resolved", not "no difference".
- **Slightly different stopwatches.** Spark's timer includes turning results into Python objects; Polars' timer stops at the dataframe. The results are small, so we expect little effect, but the boundaries are not identical.
- **A snapshot.** These are October 2026 releases (Polars 1.44.2, cudf-polars 26.10.2, Spark 3.5.9, plugin 26.08.1). The Polars GPU engine is still labelled open beta and is moving quickly.
- **Machines, not GPUs.** Each step on the GPU ladder changes the whole machine: the CPU model, memory, the link between CPU and GPU, and the GPU driver all differ. A faster result on a newer machine is not a pure GPU effect.
- **26 threads everywhere.** Every engine used 26 CPU threads on every machine. That keeps the comparison even, but it leaves the extra virtual CPUs on the A100 and GH200 machines (30 and 64 in the provider's listing) idle, so it understates what the CPU engines could do on those machines, especially on the GH200.
<!-- IF:b200 -->
- **A known compiler bug on Blackwell.** The CUDA compiler version our pinned software was built with has a known bug on Blackwell GPUs that can silently produce wrong results in one GPU routine ([NVIDIA/cccl#11167](https://github.com/NVIDIA/cccl/issues/11167)). The Polars GPU build we used includes cuDF's workaround ([NVIDIA/cudf#23973](https://github.com/NVIDIA/cudf/pull/23973)); the Spark GPU plugin predates it. We recorded this before any B200 data existed. Every B200 answer was checked against Polars on the CPU, so a wrong answer would have failed the run rather than appearing as a timing; a bug that only slowed a query would not show up in that check.
<!-- ENDIF:b200 -->
- **Arm versus x86.** On the GH200 the CPU engines run Arm builds of the same software on a different kind of processor, so CPU-side differences there are not about the GPU at all.
- **Each extra machine was conditional.** A machine ran only if the cloud provider had one available and our fixed software versions worked on it; we did not change versions to make one work. GB300 and H200 machines were not offered at all. <!-- IF:any -->Machines that did not run: {{hosts.not_run_list}}.<!-- ELSE:any -->None of the extra machines ran.<!-- ENDIF:any -->
- **Earlier study.** Our earlier A100 study used 30 threads, one run per size and a different Polars version, so we compare with it only as a rough guide.

## Read more

The full paper describes the design, statistics and threats to validity in detail, and every number in it is generated directly from the saved results:

- Paper: [TODO(author): arXiv link]
- Code, saved results and the locked experiment design: [TODO(author): repository URL]

<!-- TODO(author): date, target outlet, disclosure of any vendor relationship. No endorsement by NVIDIA, the Polars project, DuckDB, the Apache Software Foundation or TPC is implied. -->

*Use of AI tools: AI coding and writing assistants (Anthropic's Claude via Claude Code; OpenAI Codex in the earlier exploratory work) were used to write and test code, run and monitor the cloud experiments, draft the design and analysis plan, and draft and edit the text. The author directed the study, reviewed and approved the design, the analyses and the text, and takes full responsibility for the content.*

*No endorsement by NVIDIA, the Polars project, DuckDB, the Apache Software Foundation or the TPC is implied.*
