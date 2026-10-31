# stats.json schema

`stats.json` is nested; `stats_flat.json` holds the same leaves under dotted keys (`c1.baseline.sf300.estimate`). Scales are `sf1 sf10 sf30 sf100 sf300`, suites `baseline` / `complex`, engines `polars polars_gpu spark_cpu spark_gpu`, queries `q01 ...` / `cx_...`. `{c}` is one of `c1 c2 c3 c4 c4_strict`. Missing values are `null`, never absent. Keys listed here are stable; new keys may be added, none removed.

Ratios are slower-is-numerator speedups `t_ref / t_engine`: above 1 the denominator engine is faster. `estimate` is a geometric mean over rounds; `ci_low`/`ci_high` are the 95% t-interval on the mean log-ratio (df = rounds - 1) back-transformed; `min`/`max` are the extreme per-round ratios. No p-values are computed.

Contrasts: C1 `polars / polars_gpu`, C2 `spark_cpu / spark_gpu`, C3 `spark_gpu / polars_gpu`, C4 `polars / best GPU engine per query and round` (the faster of the GPU engines that produced a timing), `c4_strict` the same restricted to queries both GPU engines ran (sensitivity). Queries unsupported on polars_gpu are excluded from C1/C3/`c4_strict`; see `coverage`.

Exploratory block `exploratory.{e}` (`e1`: Polars 2.0.0 CPU / Polars GPU, `e4`: Polars 2.0.0 CPU / best GPU engine): **exploratory (post-hoc; not in the locked design §9)**. The CPU side comes from the side-study sessions and the GPU side from the main rounds (unpaired), so the interval is Welch-type on the log scale; it is never used by C1-C4, the crossover or any other primary output.

| Key pattern | Meaning |
|---|---|
| `schema_version` | Schema version of this file ('1.0'). |
| `synthetic` | true when the tree is synthetic (injected ratios); every output then carries 'SYNTHETIC — NOT RESULTS'. |
| `watermark` | 'SYNTHETIC — NOT RESULTS' for synthetic data, else null. |
| `generated_at_utc` | UTC time of the analysis run. |
| `host.label` | Host label used in cross-host comparisons (--label; default: GPU short name such as H100, A100, GH200, B200). |
| `host.instance_type` | Instance type recorded by run_campaign.py (--host-label), e.g. gpu_1x_h100_sxm5; null if not recorded. |
| `host.gpu` | GPU product name (nvidia-smi). |
| `host.gpu_short` | Short GPU name: H100, A100, GH200, B200. |
| `host.gpu_count` | Number of GPUs. |
| `host.gpu_memory_gib` | Device memory, GiB. |
| `host.cpu_model` | CPU model name (lscpu). |
| `host.arch` | CPU architecture: x86_64 or aarch64 (null if not recorded). |
| `host.vcpus` | vCPUs of the host. |
| `host.cpus_arg` | Value of --cpus used by every engine (design: 26 on all hosts). |
| `host.hypervisor` | Hypervisor vendor line of lscpu (e.g. KVM); null when lscpu has none (which does not establish bare metal). |
| `host.smt_threads_per_core` | lscpu 'Thread(s) per core' (2 = SMT/hyperthreading as the machine reports it; 1 = one thread per core); null if not recorded. |
| `host.cores_per_socket` | lscpu 'Core(s) per socket'. |
| `host.sockets` | lscpu 'Socket(s)'. |
| `host.physical_cores` | cores_per_socket x sockets, as lscpu reports it (a VM can report a flat topology of many one-core sockets). |
| `host.numa_nodes` | lscpu 'NUMA node(s)'. |
| `host.numa_nodes_with_cpus` | NUMA nodes whose 'NUMA nodeN CPU(s)' list is not empty (e.g. 1 of 9 on GH200, where the other nodes are CPU-less). |
| `host.topology_consistent` | true when smt_threads_per_core x physical_cores equals the CPU(s) count; null if a field is missing. |
| `host.cpus_arg_cores_min` | Fewest physical cores that --cpus threads can occupy: ceil(min(cpus_arg, vcpus) / smt_threads_per_core) (threads packed onto sibling hardware threads). |
| `host.cpus_arg_cores_max` | Most physical cores that --cpus threads can occupy: min(min(cpus_arg, vcpus), physical_cores) (threads spread over distinct cores). |
| `host.cpus_arg_share_of_vcpus` | cpus_arg / vcpus (1.0 = the threads can use every vCPU). |
| `host.host_ram_gib` | Host RAM, GiB. |
| `host.driver_version` | NVIDIA driver version. |
| `host.cuda_version` | CUDA version reported by nvidia-smi. |
| `host.cuda_runtime_version` | CUDA runtime the pinned Python stack loads (cudaRuntimeGetVersion, e.g. '12.9.0'); null if the environment probe did not record it. |
| `host.platform` | Platform string recorded by run_campaign.py. |
| `host.packages` | Opaque object: versions of polars/cudf/rmm/pyspark packages found in the environment inventory. |
| `host.rapids_jar` | File name of the RAPIDS Accelerator jar the campaign was started with (the aarch64 build carries the -cuda12-arm64 classifier); null if not recorded. |
| `host.rapids_jar_sha256` | sha256 of that jar as recorded by run_campaign.py. |
| `host.preflight` | List of {scope, path, verdict, counts, formal, min_free_gib, allow_any_gpu, runs, started_at_utc, completed_at_utc} for the round preflights and any host rehearsal preflight kept beside the campaign root; min_free_gib is an operational threshold, not a design parameter. |
| `host.gpu_policy` | h100_required or 'any_gpu (amendment A2 hosts)' (campaign.json design). |
| `host.formal` | campaign.json design flag: false when test overrides were used. |
| `host.synthetic` | true for a synthetic tree. |
| `host.source` | Where each identity field came from (inventory, resource_allocation, campaign.json). |
| `meta.gpu_name` | GPU product name (same as host.gpu). |
| `meta.cpu_model` | CPU model name (same as host.cpu_model). |
| `meta.driver_version` | NVIDIA driver version (same as host.driver_version). |
| `meta.rounds_completed` | Accepted rounds that enter the estimates. |
| `meta.rounds_repeated` | Round numbers that have more than one attempt directory (round-<k>, round-<k>-repeat-<n>). |
| `meta.rounds_gated` | Accepted rounds whose gates.json carries a pass verdict (fewer than rounds_completed under --allow-ungated). |
| `meta.input_gate` | verify_generated_inputs verdict for the campaign's inputs: 'RESULT: OK' | 'RESULT: FAILED' | 'RESULT: INCOMPLETE' (every needed scale verified, complete report); null if no report was found. |
| `meta.input_gate_source` | File(s) or check the verdict was read from (null if none). |
| `meta.amendments` | Number of '### A<n>' headings in section 11 of the design-document version this campaign started under (the commit whose docs/H100_EXPERIMENT_DESIGN.md sha256 equals the one recorded in campaign.json); null if that version cannot be determined. |
| `meta.later_amendments` | List of amendment ids in the current design document that are not in the version the campaign started under (e.g. ['A4']); [] if none, null if undetermined. |
| `meta.design_doc_matches_campaign` | true when the sha256 recorded in campaign.json resolves to a committed version of the design document; false (with a warning) when it matches no commit; null if none was recorded. |
| `meta.design_commit` | Oldest git commit holding the design-document version the campaign started under (full hash); null if it cannot be resolved. |
| `meta.design_commit_short` | First 7 characters of meta.design_commit. |
| `meta.run_commit` | Git HEAD commit recorded by run_campaign.py when the campaign was created (the code that ran), null if not recorded. |
| `meta.run_commit_dirty_files` | Number of uncommitted files recorded by run_campaign.py at that time. |
| `meta.design_doc_sha256` | sha256 of the design document recorded at campaign start. |
| `meta.design_sha256` | sha256 of the resolved campaign design (campaign.json). |
| `meta.sessions_completed` | Sessions of accepted rounds that finished with exit status 0 and no recorded failure. |
| `meta.measured_trials` | Measured (timed) trial records with a finite time, over accepted rounds. |
| `meta.outputs_compared` | Saved query outputs compared with Polars' (gate 2), recomputed from results.json rows over accepted rounds (null if none could be compared). |
| `meta.outputs_failed` | Of those, outputs outside rel 1e-8 / abs 1e-6 (integers exact); 0 in a gated campaign. |
| `meta.max_rel_diff` | Largest relative difference among the compared numeric values. |
| `meta.spark_gpu_plans_checked` | spark_gpu final plans (one per record) in sessions whose preflight plan rule passed (gate 4); 0 if spark_gpu did not run; null if a spark_gpu session has no passing preflight plan check. |
| `meta.cleanup_barriers_passed` | verified_gc cleanup barriers with status passed (gate 5), over accepted rounds. |
| `meta.campaign_start_utc` | Start of the earliest session of the accepted rounds, 'YYYY-MM-DD HH:MM UTC'. |
| `meta.campaign_end_utc` | End of the latest session of the accepted rounds, same format. |
| `meta.integrity_failures_in_accepted_rounds` | Cells of accepted rounds that A1 classifies as integrity failures (error, timeout, failed or invalid session, incomplete trials); must be 0. |
| `campaign.root` | Campaign root that was analyzed. |
| `campaign.design` | Opaque object: selected fields of campaign.json (seed, engines, sequences, ...). |
| `campaign.provenance` | Opaque object: created_utc, design_doc_sha256, design_sha256, git_commit, git_branch, git_dirty_files. |
| `campaign.rounds_found` | Round directory names discovered. |
| `campaign.rounds_included` | Integer round ids used in every estimate (gates passed). |
| `campaign.rounds_included_labels` | Directory names of the included rounds. |
| `campaign.rounds_excluded` | List of {round, label, reason, gate_reasons} for rounds left out (failed gate, superseded, no verdict). |
| `campaign.n_rounds` | Number of included rounds R. |
| `campaign.df` | Degrees of freedom of every interval: R - 1. |
| `campaign.level` | Confidence level (0.95). |
| `campaign.t_critical` | Two-sided Student-t critical value used (null if R < 2). |
| `campaign.engines` | Engines present, canonical order. |
| `campaign.suites` | Suites present. |
| `campaign.scales.{suite}` | Scale keys (sf1, sf10, ...) of a suite, ascending. |
| `campaign.queries.{suite}` | Query ids of a suite. |
| `campaign.trials_per_cell` | Distinct measured-trial counts per session (expected [3]). |
| `campaign.energy.gpu_csv_runs` | Number of suite runs with a gpu.csv used for the energy proxy. |
| `campaign.energy.tz_offset_seconds` | Estimated nvidia-smi local-time offset to UTC, seconds (one entry per distinct value). |
| `campaign.warnings` | Non-fatal loader warnings. |
| `contrasts.{c}.numerator` | Reference-side engine (slower-is-numerator speedup t_ref / t_engine). |
| `contrasts.{c}.denominator` | Engine whose speedup is estimated ('best_gpu' = faster of the supported GPU engines per round). |
| `contrasts.{c}.label` | Human-readable contrast label. |
| `contrasts.{c}.rq` | Research question the contrast answers. |
| `contrasts.{c}.subject` | Engine whose colour the figures use (null for C4). |
| `contrasts.{c}.definition` | One-line definition. |
| `{c}.{suite}.{scale}.estimate` | Geometric mean over rounds of the per-round suite geomean ratio (null if no common query). |
| `{c}.{suite}.{scale}.ci_low` | Lower 95% limit: exp(mean log - t*se), df = rounds - 1 (null if rounds < 2). |
| `{c}.{suite}.{scale}.ci_high` | Upper 95% limit. |
| `{c}.{suite}.{scale}.min` | Smallest per-round suite ratio. |
| `{c}.{suite}.{scale}.max` | Largest per-round suite ratio. |
| `{c}.{suite}.{scale}.n_rounds` | Rounds contributing. |
| `{c}.{suite}.{scale}.df` | Degrees of freedom. |
| `{c}.{suite}.{scale}.log_mean` | Mean of the per-round log ratios. |
| `{c}.{suite}.{scale}.log_sd` | Sample sd of the per-round log ratios. |
| `{c}.{suite}.{scale}.rounds` | Round ids aligned with per_round. |
| `{c}.{suite}.{scale}.per_round` | Per-round suite geomean ratios. |
| `{c}.{suite}.{scale}.n_queries` | Queries in the suite geomean. |
| `{c}.{suite}.{scale}.queries_used` | Query ids in the suite geomean (available for both sides in every round). |
| `{c}.{suite}.{scale}.queries_excluded` | Dict query -> reason; empty when every query was used. |
| `{c}.{suite}.{scale}.queries_excluded.{query}` | Why a query is outside the suite geomean (unsupported engine, partial rounds, not run). (present only when applicable) |
| `{c}.{suite}.{scale}.q.{query}.estimate` | Per-query geometric mean over rounds of t_ref / t_engine (null if not computable). |
| `{c}.{suite}.{scale}.q.{query}.ci_low` | Per-query lower 95% limit. |
| `{c}.{suite}.{scale}.q.{query}.ci_high` | Per-query upper 95% limit. |
| `{c}.{suite}.{scale}.q.{query}.min` | Smallest per-round ratio. |
| `{c}.{suite}.{scale}.q.{query}.max` | Largest per-round ratio. |
| `{c}.{suite}.{scale}.q.{query}.n_rounds` | Rounds with both sides valid. |
| `{c}.{suite}.{scale}.q.{query}.df` | Degrees of freedom. |
| `{c}.{suite}.{scale}.q.{query}.log_mean` | Mean per-round log ratio. |
| `{c}.{suite}.{scale}.q.{query}.log_sd` | Sample sd of per-round log ratios. |
| `{c}.{suite}.{scale}.q.{query}.rounds` | Round ids aligned with per_round. |
| `{c}.{suite}.{scale}.q.{query}.per_round` | Per-round ratios. |
| `{c}.{suite}.{scale}.q.{query}.status` | ok | partial (some rounds) | unsupported | unstable (A1: unstable coverage of an engine involved; no estimate) | no_overlap | not_run. |
| `{c}.{suite}.{scale}.q.{query}.unstable_engines` | Engines of this contrast whose cell is unstable coverage (A1) and was therefore left out of the estimate (empty list normally). |
| `{c}.{suite}.{scale}.q.{query}.blocked_by` | List of {engine, reason_class, reason} that prevented the ratio. |
| `{c}.{suite}.{scale}.q.{query}.best_engine_rounds` | C4 only: dict engine -> rounds in which it was the best GPU engine. (present only when applicable) |
| `{c}.{suite}.{scale}.q.{query}.best_engine_rounds.{engine}` | C4 only: rounds in which this GPU engine was the best. (present only when applicable) |
| `medians.{suite}.{scale}.{query}.{engine}.status` | ok | partial | failed | not_run across rounds; 'unstable' = unstable coverage (A1), no time is reported (see unstable_coverage). |
| `medians.{suite}.{scale}.{query}.{engine}.geomean` | Geometric mean over rounds of the per-round median of 3 trials, seconds. |
| `medians.{suite}.{scale}.{query}.{engine}.min` | Smallest per-round median. |
| `medians.{suite}.{scale}.{query}.{engine}.max` | Largest per-round median. |
| `medians.{suite}.{scale}.{query}.{engine}.n_rounds` | Rounds with a valid median. |
| `medians.{suite}.{scale}.{query}.{engine}.rounds` | Round ids aligned with per_round. |
| `medians.{suite}.{scale}.{query}.{engine}.per_round` | Per-round medians (null where invalid). |
| `medians.{suite}.{scale}.{query}.{engine}.cv_between_rounds` | Coefficient of variation of the per-round medians. |
| `medians.{suite}.{scale}.{query}.{engine}.reason_class` | unsupported_operation | out_of_memory | timeout | error | session_failed | invalid | unstable_coverage | null. |
| `fastest.{suite}.{scale}.{query}.winner` | Engine with the lowest geometric-mean time (null if none ran). |
| `fastest.{suite}.{scale}.{query}.ranking` | Engines fastest to slowest. (present only when applicable) |
| `fastest.{suite}.{scale}.{query}.geomean_seconds` | Winner's geometric-mean time. (present only when applicable) |
| `fastest.{suite}.{scale}.{query}.runner_up` | Second-fastest engine. (present only when applicable) |
| `fastest.{suite}.{scale}.{query}.runner_up_over_winner` | Geometric-mean ratio runner-up / winner (descriptive; winner picked from the same data). (present only when applicable) |
| `fastest.{suite}.{scale}.{query}.ci_low` | t-interval lower limit of that ratio. (present only when applicable) |
| `fastest.{suite}.{scale}.{query}.ci_high` | t-interval upper limit of that ratio. (present only when applicable) |
| `cpu_reference.{suite}.{scale}.n_queries` | Queries with timings for both CPU engines (C4 uses Polars CPU as the CPU reference). |
| `cpu_reference.{suite}.{scale}.queries_where_spark_cpu_faster` | Queries where Spark CPU's geometric-mean time beats Polars CPU's. |
| `legacy.note` | Legacy totals are arithmetic sums of medians; never mixed with geometric means. |
| `legacy.totals.{suite}.{scale}.{engine}.complete` | true if every query has a valid median in every round. |
| `legacy.totals.{suite}.{scale}.{engine}.per_round` | Per-round sum of the per-query medians (null where a query is missing). |
| `legacy.totals.{suite}.{scale}.{engine}.rounds` | Round ids aligned with per_round. |
| `legacy.totals.{suite}.{scale}.{engine}.mean_total` | Mean over rounds of the sum of medians (null unless complete). |
| `legacy.totals.{suite}.{scale}.{engine}.min_total` | Smallest per-round total. |
| `legacy.totals.{suite}.{scale}.{engine}.max_total` | Largest per-round total. |
| `legacy.totals.{suite}.{scale}.{engine}.queries_missing` | Queries without a valid median in some round. |
| `legacy.ratios.{suite}.{scale}.{c}.ratio_of_mean_totals` | Legacy ratio: mean total of the reference side / mean total of the engine side. |
| `legacy.ratios.{suite}.{scale}.{c}.per_round` | Per-round legacy ratios. |
| `variability.cells` | List of {engine, suite, scale, query, rounds, cv_within[], cv_between} per cell. |
| `variability.{group}.{engine}.within_session.{stat}` | Distribution (n, median, mean, p90, max) of per-session trial CVs. |
| `variability.{group}.{engine}.between_rounds.{stat}` | Distribution of between-round CVs of the per-round medians. |
| `variability.by_engine_suite.{engine}.{suite}.within_session.{stat}` | Same, per suite. |
| `variability.by_engine_suite.{engine}.{suite}.between_rounds.{stat}` | Same, per suite. |
| `variability.by_engine_scale.{engine}.{suite}.{scale}.within_session.{stat}` | Same, per suite and scale. |
| `variability.by_engine_scale.{engine}.{suite}.{scale}.between_rounds.{stat}` | Same, per suite and scale. |
| `order.n_observations` | Cell-round deviations used. |
| `order.design_balance` | Opaque object: observed order per round and whether positions / adjacent pairs are balanced. |
| `order.position.{p}.{stat}` | Position 1-4: n_cells, n_rounds, mean_pct, ci_low_pct, ci_high_pct, sd_log of the log-time deviation. |
| `order.position_by_engine.{p}.{engine}.{stat}` | Same, per measured engine. |
| `order.predecessor.{pred}.{stat}` | By predecessor engine ('none' = first session of a block). |
| `order.predecessor_by_engine.{pred}.{engine}.{stat}` | Same, per measured engine. |
| `crossover.{c}.{suite}.verdicts.{scale}` | gpu_faster (CI above 1) | cpu_faster (CI below 1) | inconclusive | n/a. |
| `crossover.{c}.{suite}.estimates.{scale}` | C4 suite estimate at that scale. |
| `crossover.{c}.{suite}.crossover_found` | true if some scale has the C4 CI entirely above 1. |
| `crossover.{c}.{suite}.first_gpu_faster_scale` | Smallest such scale (null if none). |
| `crossover.{c}.{suite}.stable_from_scale` | Smallest scale from which every larger scale is also gpu_faster. |
| `crossover.{c}.{suite}.bracket` | [previous scale, first gpu_faster scale] or null. |
| `crossover.{c}.{suite}.first_point_estimate_above_1` | Smallest scale whose point estimate exceeds 1 (ignores the interval). |
| `crossover.{c}.{suite}.queries.{query}.{field}` | Per-query version of the five fields above (verdicts / estimates are dicts keyed by scale). |
| `crossover.{c}.{suite}.queries.{query}.{field}.{scale}` | Per-query verdicts and estimates by scale. |
| `coverage.matrix.{suite}.{query}.{scale}.{engine}.status` | supported | partial | failed | not_run across included rounds. |
| `coverage.matrix.{suite}.{query}.{scale}.{engine}.rounds_ok` | Rounds with a valid timing. |
| `coverage.matrix.{suite}.{query}.{scale}.{engine}.rounds_total` | Included rounds in this block. |
| `coverage.matrix.{suite}.{query}.{scale}.{engine}.reason_class` | unsupported_operation | out_of_memory | timeout | error | session_failed | invalid | null. |
| `coverage.matrix.{suite}.{query}.{scale}.{engine}.reason` | First recorded error text (truncated). |
| `coverage.matrix.{suite}.{query}.{scale}.{engine}.predeclared_unsupported` | true for the section 8 pre-declared GPU gaps. |
| `coverage.matrix.{suite}.{query}.{scale}.{engine}.unstable` | true if a coverage outcome (unsupported operation, out of memory) occurred in some rounds but not all (A1 unstable coverage): excluded from every contrast in all rounds. |
| `output_agreement.{suite}.{scale}.{engine}.{field}` | Integrity evidence recomputed from the saved result rows against Polars: compared, failed, max_rel_diff (largest relative difference among numeric values, all accepted rounds), max_rel_diff_query (query attaining it), rounds, max_rel_diff_per_round (aligned with rounds; null if no numeric value). |
| `output_agreement.{suite}.{scale}.{engine}.queries.{query}` | Largest relative difference of that query over the accepted rounds. (present only when applicable) |
| `plan_variability.engines` | Engines whose saved final adaptive plans were compared (spark_cpu, spark_gpu). |
| `plan_variability.note` | What the section is: descriptive, nothing in it feeds an estimate (medians stay as designed). |
| `plan_variability.sessions_checked` | Spark sessions of the included rounds with at least one saved plan. |
| `plan_variability.cells_checked` | (suite, scale, query, engine) Spark cells with at least one saved plan. |
| `plan_variability.trials_with_plan` | Trials (warm-up and measured) whose plan file was read. |
| `plan_variability.plan_files_missing` | Plan files named in results.json that could not be read. |
| `plan_variability.cells_variable` | Cells whose trials do not all end in the same final plan (length of cells). |
| `plan_variability.cells` | List with one entry per variable cell: suite, scale, query, engine, rounds, n_trials, n_measured, n_warmup, n_plans, reference_signature (plan with the lowest median measured time), initial_plan_identical (true when every trial started from the same initial plan; null if no initial plan was saved), measured_time_ranges_overlap (do the plans' measured-time ranges intersect), median_seconds_slowest_over_fastest, and plans: one entry per final plan {signature (12 hex digits of the normalized plan; equal plans have equal signatures on every host), n_measured, n_warmup, measured_per_round, warmup_per_round, measured_seconds {min, median, max}, warmup_seconds, execution (event-log stages, tasks and shuffle_write_bytes per action as {min, median, max}, with trials_with_event_log; null without a log), features {operators, joins, exchanges (kind(keys, partitions) -> count), shuffle_readers (coalesced|local|skewed|plain -> count)}, changes_vs_reference (feature -> name -> [reference count, this count], lines_only_in_this_plan, lines_only_in_reference_plan; null for the reference plan), trials [{round, phase, trial, seconds}]}. |
| `unstable_coverage.note` | Why these cells are listed and why they are not estimates (amendment A1). |
| `unstable_coverage.cells` | List of {suite, scale, query, engine, rounds, outcome_per_round, seconds_per_round, coverage_rounds, label}: raw per-round outcomes of unstable-coverage cells, labelled 'excluded by A1 (unstable coverage); not an estimate'. |
| `coverage.discrepancies` | Cells that contradict the pre-declaration (declared but ran, or undeclared GPU failure). |
| `coverage.predeclared_unsupported` | engine:query pairs pre-declared unsupported. |
| `secondary.gpu_memory.capacity_bytes` | Device memory of the GPU (from gpu_inventory), bytes. |
| `secondary.gpu_memory.note` | Sampling caveat and what the per-engine numbers mean. |
| `secondary.gpu_memory.measure.{engine}` | What the engine's memory number is: polars_gpu 'process_high_water_mark' (nvidia-smi, RMM async pool); spark_gpu 'task_reported_max_device_memory' (event-log accumulator gpuMaxDeviceMemoryBytes) or 'pool_reservation_only' when the event logs carry no such accumulator (then Spark GPU is left out of the memory figure). |
| `secondary.gpu_memory.measure_note.{engine}` | One-paragraph explanation of that measure and its limits. |
| `secondary.gpu_memory.task_reported_cells.{engine}.{field}` | with_value / cells: Spark GPU (query, scale) cells with a task-reported device-memory value. |
| `secondary.gpu_memory.constant_check.{engine}.{field}` | sessions (>= 3 sampled actions), constant (sessions whose sampled device memory stays within 5% of its minimum: a constant sample is a reserved pool, not memory in use), first_sample_median_bytes (median over sessions of the first sampled action, a warm-up) and later_minimum_median_bytes (median over sessions of the smallest later sample). |
| `secondary.gpu_memory.high_water_check.{engine}.{field}` | sessions (>= 3 sampled actions), nondecreasing (sessions whose sampled device memory never falls, within 1% + 64 MiB, in time order: evidence that the sample is a session high-water mark rather than a per-query value), largest_fall_bytes (the largest drop below the running maximum in any session) and falling_sessions (list of {round, suite, scale, largest_fall_bytes, max_bytes} for the sessions whose sample does fall; empty when none). |
| `secondary.gpu_memory.cells.{suite}.{scale}.{query}.{engine}.{field}` | recorded, per_round_max_bytes, rounds, max_bytes, median_bytes of the per-trial nvidia-smi sample (for Spark GPU: the pool reservation). |
| `secondary.gpu_memory.cells.{suite}.{scale}.{query}.spark_gpu.task_reported.{field}` | Spark GPU only: recorded, per_round_max_bytes, rounds, max_bytes, median_bytes of the largest task-reported device memory of the action (not a simultaneous total). |
| `secondary.gpu_memory.summary.{suite}.{scale}.{engine}.{field}` | n_queries, max_bytes, min_bytes, median_bytes, fraction_of_capacity_max over queries (nvidia-smi sample). |
| `secondary.gpu_memory.summary.{suite}.{scale}.spark_gpu.task_reported.{field}` | Spark GPU only: the same summary over the task-reported device memory (null when no cell has a value). (present only when applicable) |
| `secondary.process.{suite}.{scale}.{engine}.{field}` | Mean over rounds: process_wall_seconds, launch_to_engine_ready_seconds, session_and_table_setup_seconds, warmup_seconds, measured_seconds, n_sessions, per_round_process_wall_seconds. |
| `secondary.cleanup.{suite}.{scale}.{engine}.{field}` | verified_gc cost: n_sessions, cleanups_per_session, mean_seconds_per_cleanup, max_seconds, total_seconds_per_session, share_of_process_wall. (present only when applicable) |
| `secondary.energy.definition` | Energy-proxy definition and caveat. |
| `secondary.energy.by_cell.{suite}.{scale}.{engine}.{field}` | n_sessions, mean_power_w, energy_wh, per_round_energy_wh, process_wall_seconds. (present only when applicable) |
| `side_study.meta` | Opaque object: availability, versions, rounds, definition (ratio = newer / older Polars). |
| `side_study.ratio.{suite}.{scale}.{field}` | Suite-level ratio newer / older Polars; same fields as {c}.{suite}.{scale}. (present only when applicable) |
| `side_study.ratio.{suite}.{scale}.queries_excluded.{query}` | Excluded query reason. (present only when applicable) |
| `side_study.ratio.{suite}.{scale}.q.{query}.{field}` | Per-query ratio newer / older Polars; same fields as {c}.{suite}.{scale}.q.{query}. (present only when applicable) |
| `side_study.medians.{suite}.{scale}.{query}.{version}.rounds` | Side-study round ids aligned with per_round. (present only when applicable) |
| `side_study.medians.{suite}.{scale}.{query}.{version}.per_round` | The version's per-round median seconds (median of three measured trials) for that query; used by the paper-2 protocol check. (present only when applicable) |
| `side_study.ratio.{suite}.{scale}.geomean_seconds.{version}.{query}` | Geometric-mean time of that Polars version. (present only when applicable) |
| `secondary.gpu_telemetry.meta.label` | Always 'descriptive telemetry; not a pre-registered contrast; no causal claim'. |
| `secondary.gpu_telemetry.meta.available` | true when gpu.csv files were read (false with --no-energy or without gpu.csv). |
| `secondary.gpu_telemetry.meta.reason` | Why it is unavailable (null when available). |
| `secondary.gpu_telemetry.meta.definition` | What is pooled and what idle_fraction and utilization.gpu mean. |
| `secondary.gpu_telemetry.meta.idle_threshold_pct` | A sample with utilization.gpu below this percentage counts as GPU-idle (10). |
| `secondary.gpu_telemetry.meta.min_action_seconds` | Measured actions shorter than this (1 s) are left out of every statistic of the section. |
| `secondary.gpu_telemetry.meta.sample_interval_ms` | Median spacing of the gpu.csv samples (about 200 ms); null without gpu.csv. |
| `secondary.gpu_telemetry.meta.gpu_csv_runs` | Suite runs whose gpu.csv was read. |
| `secondary.gpu_telemetry.meta.power_limit_w` | Current power limit of the first GPU from nvidia-smi -q in environment.json (null if not recorded). |
| `secondary.gpu_telemetry.meta.power_limit_source` | Where power_limit_w came from. |
| `secondary.gpu_telemetry.meta.max_sm_clock_mhz` | Maximum SM clock from nvidia-smi -q (null if not recorded). |
| `secondary.gpu_telemetry.meta.resolution_note` | How the sampling interval limits short actions. |
| `secondary.gpu_telemetry.cells.{suite}.{scale}.{engine}.{field}` | Pooled over the samples inside the measured actions (>= 1 s) of the included rounds: n_actions, n_actions_excluded_short (actions under 1 s), seconds_excluded_short, n_actions_without_samples, n_sessions, action_seconds, n_samples, util_median_pct, util_p90_pct (utilization.gpu), idle_fraction (share of samples with utilization.gpu below 10%), power_median_w, power_fraction_of_limit (median power / power_limit_w), sm_clock_median_mhz, sm_clock_fraction_of_max, mem_util_median_pct (utilization.memory); statistics are null when no action qualifies. (present only when applicable) |
| `secondary.gpu_telemetry.cells.{suite}.{scale}.{engine}.rounds` | Round ids aligned with idle_fraction_by_round. (present only when applicable) |
| `secondary.gpu_telemetry.cells.{suite}.{scale}.{engine}.idle_fraction_by_round` | idle_fraction per round (null for a round without a qualifying action). (present only when applicable) |
| `secondary.gpu_telemetry.cells.{suite}.{scale}.{engine}.q.{query}.{field}` | Per query: n_actions, util_median_pct, util_p90_pct, idle_fraction. (present only when applicable) |
| `exploratory.{e}.meta.label` | Always 'exploratory (post-hoc; not in the locked design §9)'. E1 and E4 are not part of C1-C4, the crossover verdict or any pre-registered estimate. |
| `exploratory.{e}.meta.available` | true when a side study with two Polars versions exists (otherwise the suite blocks are absent). |
| `exploratory.{e}.meta.contrast` | E1: 'Polars <new> CPU / Polars GPU'; E4: 'Polars <new> CPU / best GPU engine'. |
| `exploratory.{e}.meta.counterpart` | The locked-design contrast it can be read next to (E1: c1, E4: c4). |
| `exploratory.{e}.meta.numerator` | The CPU side: the newer Polars' side-study sessions (null if unavailable). |
| `exploratory.{e}.meta.denominator` | The GPU side taken from the main rounds. |
| `exploratory.{e}.meta.cpu_version` | Polars version of the CPU side, e.g. '2.0.0'. |
| `exploratory.{e}.meta.side_study_rounds` | Round ids of the side study that enter the CPU side. |
| `exploratory.{e}.meta.main_rounds` | Round ids of the main campaign that enter the GPU side. |
| `exploratory.{e}.meta.interval` | How the interval is built: Welch-type on the log scale, unpaired sessions from different rounds. |
| `exploratory.{e}.meta.definition` | What the ratio is and the orientation (above 1: the GPU side was faster, as for C1 and C4). |
| `exploratory.{e}.meta.reason` | Why the analysis is unavailable (null when available). |
| `exploratory.{e}.{suite}.{scale}.estimate` | Geometric mean over side-study rounds of the CPU suite geomean divided by the geometric mean over main rounds of the GPU suite geomean (queries timed in every round on both sides). (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.ci_low` | Lower Welch-type 95% limit on the log scale (null with fewer than 2 rounds on either side). (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.ci_high` | Upper limit. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.df` | Welch-Satterthwaite degrees of freedom (non-integer). (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.log_se` | Standard error of the difference of mean log times. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.n_cpu_rounds` | Side-study rounds on the CPU side. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.n_gpu_rounds` | Main rounds on the GPU side. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.cpu_seconds` | Geometric mean over side-study rounds of the CPU suite geomean (seconds). (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.gpu_seconds` | Same for the GPU side (seconds). (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.cpu_per_round` | Per-round CPU suite geomean times (seconds). (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.gpu_per_round` | Per-round GPU suite geomean times (seconds). (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.cpu_rounds` | Round ids aligned with cpu_per_round. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.gpu_rounds` | Round ids aligned with gpu_per_round. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.status` | ok | no_common_query. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.n_queries` | Queries in the suite estimate. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.queries_used` | Query ids in the suite estimate. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.queries_excluded` | Dict query -> reason; empty when all queries are used. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.queries_excluded.{query}` | Why a query is outside the suite estimate. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.q.{query}.{field}` | Per-query version of the suite fields (estimate, ci_low, ci_high, df, log_se, n_cpu_rounds, n_gpu_rounds, cpu_seconds, gpu_seconds, cpu_per_round, gpu_per_round, cpu_rounds, gpu_rounds), plus status (ok|partial|unsupported|unstable|no_overlap|not_run). (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.q.{query}.blocked_by` | List of {engine, reason_class, reason} that prevented the ratio. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.q.{query}.unstable_engines` | GPU engines left out by A1 (unstable coverage). (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.q.{query}.best_engine_rounds` | E4 only: dict GPU engine -> main rounds in which it was the best. (present only when applicable) |
| `exploratory.{e}.{suite}.{scale}.q.{query}.best_engine_rounds.{engine}` | E4 only: rounds in which this GPU engine was the best. (present only when applicable) |
| `a100_vs_h100.meta` | Opaque object: hosts, non-equivalence flags, definition, engine facts. |
| `a100_vs_h100.study.label` | Who the A100 side is, e.g. '2026-10-25 A100 study': the previous study's saved evidence (dated from its sessions), null when no A100 roots were given. |
| `a100_vs_h100.study.previous_study` | true when the A100 roots are run_experiment output roots of an earlier study (no campaign.json), false for an A100 campaign root; null without roots. |
| `a100_vs_h100.study.roots` | The A100 evidence roots that were read. |
| `a100_vs_h100.study.first_session_utc` | Start of the earliest A100 session (UTC). |
| `a100_vs_h100.study.last_session_utc` | Start of the latest A100 session (UTC). |
| `a100_vs_h100.study.engines_present` | Engines with sessions in the earlier study. |
| `a100_vs_h100.study.engines_compared` | Engines whose A100 / H100 ratio is reported (GPU engines present in both). |
| `a100_vs_h100.study.engines_only_in_h100_campaign` | Engines of the H100 campaign that the earlier study did not run (e.g. polars_gpu). |
| `a100_vs_h100.study.gpu` | A100 GPU name recorded by the earlier study. |
| `a100_vs_h100.study.vcpus` | vCPUs allocated in the earlier study. |
| `a100_vs_h100.study.host_ram_gib` | Approximate host RAM of the earlier study, GiB. |
| `a100_vs_h100.study.polars_version` | Polars version of the earlier study's Polars sessions (null if several). |
| `a100_vs_h100.study.polars_version_h100` | Polars version of the H100 campaign's Polars sessions (null if several). |
| `a100_vs_h100.study.spark_version` | Spark version of the earlier study. |
| `a100_vs_h100.study.spark_version_h100` | Spark version of the H100 campaign. |
| `a100_vs_h100.study.cpu_threads` | Threads (--cpus) of the earlier study's sessions (null if several). |
| `a100_vs_h100.study.cpu_threads_h100` | Threads (--cpus) of the H100 campaign's sessions. |
| `a100_vs_h100.study.sessions_per_cell` | Sessions per (suite, scale, engine) in the earlier study (one). |
| `a100_vs_h100.study.rounds_h100` | Rounds of the H100 campaign that enter the comparison. |
| `a100_vs_h100.study.arguments` | List of {root, cpus, runs, driver_memory, engine_timeout_seconds, keep_going, query_cleanup, cleanup_timeout_seconds} recorded in each A100 root's environment.json. |
| `a100_vs_h100.study.differences` | List of {field, engines, suites, a100_study, h100_campaign, in_compared_ratio}: every recorded field that differs between the earlier study and the H100 campaign (library version, threads, trials, policies, Spark GPU profile, jars, sessions_per_cell, engine_order, GPU, vCPUs, host RAM, spark_conf). A field the earlier study did not record has null on its side. in_compared_ratio: the entry applies to an engine whose ratio is reported (host and protocol entries apply to all). |
| `a100_vs_h100.study.same` | List of {field, engine, suite, value} for recorded fields that are identical on both sides. |
| `a100_vs_h100.study.spark_conf_differences` | Opaque object: Spark GPU configuration keys recorded by both sides whose values differ ({a100_study, h100_campaign, relative_difference}); run-specific keys are left out. |
| `a100_vs_h100.study.spark_conf_keys_recorded.a100_study` | Number of Spark GPU configuration keys the earlier study recorded (it saved only the keys it set). |
| `a100_vs_h100.study.spark_conf_keys_recorded.h100_campaign` | Number of Spark GPU configuration keys the H100 campaign recorded. |
| `a100_vs_h100.study.plan_variability` | Opaque object with the structure of the top-level plan_variability, computed over the earlier study's Spark sessions (one session per cell): cells whose trials, warm-up included, ended in more than one final plan. Plan signatures of the two studies are not comparable (the earlier study's scan locations are sanitized); compare features. |
| `a100_vs_h100.study.difference_headline` | One sentence: Polars version, threads and protocol differences (used in the F9 footnote and caption). |
| `a100_vs_h100.study.difference_summary` | All differences in one sentence each (the full list behind the headline). |
| `a100_vs_h100.ratio.{engine}.{suite}.{scale}.{field}` | A100 time / H100 time for a GPU engine (non-equivalent hosts); suite-level fields as above. (present only when applicable) |
| `a100_vs_h100.ratio.{engine}.{suite}.{scale}.queries_excluded.{query}` | Excluded query reason. (present only when applicable) |
| `a100_vs_h100.ratio.{engine}.{suite}.{scale}.q.{query}.{field}` | Per-query A100 / H100 ratio. (present only when applicable) |

Placeholders: `{suite}`, `{scale}`, `{query}`, `{engine}`, `{c}`, `{e}` (`e1` `e4`), `{p}` (position 1-4), `{pred}` (predecessor engine or `none`), `{version}` (`polars_1_44_2`, `polars_2_0_0`), `{stat}`/`{field}` (the field names listed in the row), `{group}` (`by_engine`).

## Cross-host file `cross_host.json`

Written by `analyze_campaign.py --host LABEL=PATH ...` (and by `--synthetic`, under `cross_host/`); amendments A2/A3 of the design. `cross_host_flat.json` holds the same leaves under dotted keys (`ratio.B200.polars_gpu.baseline.sf300.estimate`). For the paper tooling, `xhost_<HOST>.json` repeats one host's suite-level ratios as `<engine>.<suite>.<scale>.{estimate,ci_low,ci_high,df,n_queries}` (pass it as `--stats xhost=PATH`). `{host}` is a host label (`A100 H100 GH200 B200`); per-host `stats.json` files keep the schema above. Ratios are host / reference time (below 1: the host is faster); intervals are Welch-type on the log scale.

| Key pattern | Meaning |
|---|---|
| `schema_version` | Schema version of cross_host.json ('1.0'). |
| `synthetic` | true if any host tree is synthetic; every output then carries 'SYNTHETIC — NOT RESULTS'. |
| `watermark` | 'SYNTHETIC — NOT RESULTS' or null. |
| `generated_at_utc` | UTC time of the comparison. |
| `reference` | Reference host label (default H100); every ratio is host / reference. |
| `hosts` | All host labels in GPU-generation order (A100, H100, GH200, B200; unknown labels last). |
| `hosts_not_included` | Ladder hosts (A100, H100, GH200, B200) that have no entry in this comparison (empty list when all four are present). (present only when applicable) |
| `partial` | true when hosts_not_included is not empty: the comparison covers only part of the GPU-generation ladder; the report and F11 say so. |
| `level` | Confidence level of the intervals (0.95). |
| `definition` | How the ratio, the Welch-type interval and the suite-level estimate are defined. |
| `caveats` | List of general caveats (separate campaigns, confounding, unpaired rounds, coverage exclusions). |
| `caveats_by_host.{host}` | List of host-specific caveats derived from the identity differences versus the reference. |
| `identity_differences.{host}` | Dict field -> [host value, reference value] for identity fields that differ (empty dict when identical). (present only when applicable) |
| `identity_differences.{host}.{field}` | [host value, reference value] for gpu, gpu_memory_gib, cpu_model, arch, vcpus, cpus_arg, smt_threads_per_core, physical_cores, numa_nodes, hypervisor, host_ram_gib, driver_version, cuda_version, cuda_runtime_version, rapids_jar. (present only when applicable) |
| `host_info.{host}.{field}` | Identity of a host (the 'host' block of its stats.json) plus role (reference|comparison), the design version it started under (design_commit, design_doc_sha256, amendments, later_amendments), n_rounds, rounds_included, engines, root, synthetic. |
| `host_info.{host}.packages` | Opaque object: package versions found in the host's environment inventory. |
| `ratio.{host}.{engine}.{suite}.{scale}.estimate` | Suite-level time ratio host / reference: geometric-mean round medians over the queries valid in every round on both hosts (below 1: host faster). |
| `ratio.{host}.{engine}.{suite}.{scale}.ci_low` | Lower 95% limit, Welch-type interval on the log scale (null with fewer than 2 rounds on either host). |
| `ratio.{host}.{engine}.{suite}.{scale}.ci_high` | Upper 95% limit. |
| `ratio.{host}.{engine}.{suite}.{scale}.df` | Welch-Satterthwaite degrees of freedom (non-integer). |
| `ratio.{host}.{engine}.{suite}.{scale}.log_se` | Standard error of the difference of mean log times. |
| `ratio.{host}.{engine}.{suite}.{scale}.n_host` | Rounds of the host. |
| `ratio.{host}.{engine}.{suite}.{scale}.n_ref` | Rounds of the reference. |
| `ratio.{host}.{engine}.{suite}.{scale}.host_seconds` | Geometric mean over rounds of the host's per-round suite geomean time (seconds). |
| `ratio.{host}.{engine}.{suite}.{scale}.ref_seconds` | Same for the reference host (seconds). |
| `ratio.{host}.{engine}.{suite}.{scale}.host_per_round` | Host's per-round suite geomean times (seconds). |
| `ratio.{host}.{engine}.{suite}.{scale}.ref_per_round` | Reference's per-round suite geomean times (seconds). |
| `ratio.{host}.{engine}.{suite}.{scale}.host_rounds` | Round ids aligned with host_per_round. |
| `ratio.{host}.{engine}.{suite}.{scale}.ref_rounds` | Round ids aligned with ref_per_round. |
| `ratio.{host}.{engine}.{suite}.{scale}.status` | ok | no_common_query. |
| `ratio.{host}.{engine}.{suite}.{scale}.n_queries` | Queries in the suite-level estimate. |
| `ratio.{host}.{engine}.{suite}.{scale}.queries_used` | Query ids in the estimate. |
| `ratio.{host}.{engine}.{suite}.{scale}.queries_excluded` | Dict query -> reason (partial rounds, unavailable on one host with the failure class); empty when all queries are used. (present only when applicable) |
| `ratio.{host}.{engine}.{suite}.{scale}.queries_excluded.{query}` | Why a query is outside the suite estimate. (present only when applicable) |
| `ratio_query.{host}.{engine}.{suite}.{scale}.{query}.{field}` | Per-query version: estimate, ci_low, ci_high, df, log_se, n_host, n_ref, host_seconds, ref_seconds, host_per_round, ref_per_round, status (ok|partial|unavailable), blocked_by. |
| `ratio_query.{host}.{engine}.{suite}.{scale}.{query}.blocked_by` | List of {host, reason_class} that prevented the ratio. |
| `speedup.{c}.{host}.{suite}.{scale}.{field}` | The host's own suite-level speedup of contrast c (estimate, ci_low, ci_high, df, n_rounds, n_queries), copied from that host's stats.json (not combined across hosts); the reference host is included. |
| `coverage_differences.{host}` | List of {suite, scale, query, engine, host_status, host_reason_class, ref_status, ref_reason_class} for cells whose coverage outcome differs from the reference. |
| `skipped.{host}` | Notes on (suite, scale) blocks present on only one of the two hosts. |
| `agreement.{host}.{suite}.{scale}.{engine}.{field}` | Integrity comparison of the output disagreement with Polars: host_max_rel_diff and ref_max_rel_diff (largest relative difference, all accepted rounds), their worst queries, larger_than_reference, host_over_reference (their ratio; null if the reference is 0), host_within_gate (<= 1e-8), host_failed, host_rounds, ref_rounds, matched_rounds (round -> [host, reference]), queries_larger (queries whose difference exceeds the reference's). |
| `plan_variability.{host}.cells_checked` | Spark cells with a saved plan on that host (null when the host's stats.json has no plan_variability section). |
| `plan_variability.{host}.cells_variable` | Of those, cells whose trials ended in more than one final adaptive plan. |
| `plan_variability.{host}.cells` | List with one entry per variable cell: suite, scale, query, engine, rounds, n_plans, initial_plan_identical, measured_time_ranges_overlap, median_seconds_slowest_over_fastest, reference_signature, and plans [{signature (equal plans have equal signatures on every host), n_measured, n_warmup, measured_median_seconds, warmup_median_seconds, exchanges, shuffle_write_bytes_median}]. Descriptive; no estimate depends on it. |
| `gpu_telemetry.{host}.label` | 'descriptive telemetry; not a pre-registered contrast; no causal claim'. |
| `gpu_telemetry.{host}.available` | true when the host's stats.json has GPU telemetry from gpu.csv. |
| `gpu_telemetry.{host}.sample_interval_ms` | Median spacing of the host's gpu.csv samples (about 200 ms). |
| `gpu_telemetry.{host}.power_limit_w` | The card's current power limit (nvidia-smi -q), null if not recorded. |
| `gpu_telemetry.{host}.max_sm_clock_mhz` | The card's maximum SM clock (nvidia-smi -q), null if not recorded. |
| `gpu_telemetry.{host}.cells.{suite}.{scale}.{engine}.{field}` | n_actions, n_actions_excluded_short, n_samples, util_median_pct, util_p90_pct, idle_fraction, power_median_w, power_fraction_of_limit, sm_clock_median_mhz, sm_clock_fraction_of_max, mem_util_median_pct: as in the host's secondary.gpu_telemetry.cells (actions under 1 s excluded). (present only when applicable) |
| `agreement.{host}.{suite}.{scale}.{engine}.matched_rounds.{round}` | [host value, reference value] of the largest relative difference in a round both hosts completed. (present only when applicable) |

Cross-host placeholders: `{host}`, `{engine}`, `{suite}`, `{scale}`, `{query}`, `{c}` (as above), `{field}` (the field names listed in the row).
