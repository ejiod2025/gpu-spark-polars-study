# Complex workload plan observations — exploratory v1

Draft technical notes, 2026-10-25. Publication remains `draft_not_cleared`; see `NOTICE_RESEARCH.md`. These observations concern the saved **v1 SF30 and SF100** sessions in `results/complex-formal-20261025`, with the legacy `none` cleanup protocol. They do not characterize the later uniform `verified_gc` runs. V1 timings are exploratory and excluded from v2 curves; no performance ranking is asserted here.

## Operator coverage

Inspected all 96 saved Spark plans: six queries × two scales × two Spark modes × four actions (one warmup and three measurements). Every adaptive plan is marked `isFinalPlan=true`. Coverage below uses only text between `== Final Plan ==` and `== Initial Plan ==`, and counts operator nodes rather than arbitrary mentions in expressions.

All 48 GPU-session final plans contain GPU operators. The analyzer detects no CPU core scan/filter/project/join/aggregate/sort/window nodes in those final sections. All 48 CPU-session plans contain no GPU operators. This is operator-selection evidence, not a GPU runtime percentage: the GPU plans still contain `GpuColumnarToRow`, and scheduling, orchestration, I/O and Python/JVM work are not quantified by this name check.

| Query | GPU final-plan observations at both scales | CPU final-plan comparison and qualification |
| --- | --- | --- |
| `cx_q09` | Six `GpuFileGpuScan` nodes; five joins; `GpuHashAggregate` and `GpuSort`. The partsupp join retains the two-key part/supplier condition. At SF30 the five joins are three `GpuBroadcastHashJoin` plus two `GpuShuffledSymmetricHashJoin`; at SF100 they are two broadcast plus three shuffled symmetric joins. | Five joins: three broadcast/two sort-merge at SF30; one broadcast/four sort-merge at SF100. Thus the two modes do not share an identical physical join strategy. |
| `cx_q13` | `GpuShuffledAsymmetricHashJoin LeftOuter`, followed by nested `GpuHashAggregate` operators and `GpuSort`. The orders-side filter excludes the comment pattern before the outer join. | `SortMergeJoin LeftOuter` with nested `HashAggregate`. The saved plans retain substantive outer-join and nested-aggregation work. |
| `cx_semi_anti` | Two `GpuShuffledHashJoin LeftSemi` nodes and `GpuTopN`. **No LeftAnti node remains in any final plan.** | Two `SortMergeJoin LeftSemi` nodes in final plans. Initial plans contain a `LeftAnti` branch, but that initial text is not evidence of final anti-join execution. See the empty-input observation below. |
| `cx_window` | `GpuBatchedBoundedWindow` for `LAG`, `GpuRunningWindow` for `ROW_NUMBER`, two `GpuSort` nodes, and `GpuTopN`. | Two `Window` nodes, sorting, and `WindowGroupLimit`. The GPU window path is present rather than a detected CPU-window fallback. |
| `cx_distinct` | `GpuExpand` with three alternatives for the three exact-distinct expressions, four `GpuHashAggregate` nodes, joins and exchanges. SF30 uses two broadcast/one shuffled symmetric join; SF100 uses one broadcast/two shuffled symmetric joins. | `Expand`, four `HashAggregate` nodes, broadcast and sort-merge joins. The distinct formulation introduces additional grouping and exchange work; it is not an approximate distinct sketch. |
| `cx_self_join` | `GpuShuffledSymmetricHashJoin` preserves the residual `left.l_linenumber < right.l_linenumber` condition; hash aggregates and sorting follow it. A `ReusedExchange` supplies the second join input. | `SortMergeJoin` with the inequality and a reused exchange. Both modes show one physical scan node reused through the exchange, so this is not evidence of two independent full file scans. |

The q09/distinct join-strategy descriptions above hold across all four saved actions at each scale. Exchange/coalescing counts can vary between actions; counts of textual plan occurrences are not unique physical-stage or runtime-work counts.

## The semi/anti case has an empty anti input

The SQL contains genuine correlated `EXISTS` and `NOT EXISTS`, and the saved initial plans decorrelate these into semi/anti joins. The anti restriction is 1996 orders with status `F`. In **all 16 saved semi/anti actions** across the two scales and Spark modes, the event evidence identifies the orders scan feeding the anti branch, followed by a completed exchange that writes **zero records and zero bytes**. Its scan-output row accumulator totals 45,000,000 rows at SF30 or 150,000,000 at SF100. The final AQE plan omits the anti join.

For a compact reproducibility check, SQL execution 15 corresponds to measured trial 1 in each of the four sessions. The anti-input scan/exchange stage IDs are 146 (SF30 CPU), 142 (SF30 GPU), 178 (SF100 CPU) and 177 (SF100 GPU). Attribution uses the scan node's SQL-metric accumulator ID in `SparkListenerTaskEnd.Task Info.Accumulables`, then the same successful task's `Task Metrics.Shuffle Write Metrics`. The other saved actions are SQL executions 9, 21 and 27; each shows the same zero-output result.

This establishes an empty exchanged anti input in these observed actions; it does not independently identify the cause of that emptiness. The anti-input scan/filter work still runs inside the collect action, but these records do **not** demonstrate timed probing of a nonempty GPU anti join. Edge-fixture semantic validation and actual workload operator coverage are separate claims. The frozen query is unchanged.

## What event metrics establish

The existing `inspect_spark_metrics.py` attribution logic matches **96/96 actions** to one collect SQL execution and its job/stage IDs. There are no ambiguous stage mappings, unmatched collect executions, parse issues or metric parse errors in these four sessions. Seven setup tasks per session have no SQL execution and are excluded. Deduplication selects 23,913 successful tasks overall, including 18,026 from measured actions. No failed or killed task attempts occur among these attributed actions.

Every selected task exposes Spark's `Memory Bytes Spilled` and `Disk Bytes Spilled`, and both are zero throughout this SF30/SF100 subset. These counters do not quantify retained shuffle output, total temporary-disk occupancy or GPU device-to-host transfers. No explicitly spill-named `gpu*` task accumulator is observed by the inspector. Therefore **GPU device-to-host spill volume is unquantified**, not proven absent.

GPU task accumulators have different coverage. Of 12,045 selected GPU-session tasks, `gpuMaxDeviceMemoryBytes` is present for 5,817; `gpuTime` for 11,722; and `gpuSemaphoreWait` for 11,977. Missing updates are not zero measurements. The largest reported device-memory update is 15,840,357,014 bytes, a task-reported maximum across these actions, not a simultaneous whole-device allocation trace or the RMM pool size. GPU-session standard Spark peak-execution-memory counters being zero do not establish zero host or device memory use.

## Bottleneck interpretation limits

The joins, exchanges, sorts, multi-distinct expansion and windows identify concrete work to investigate. They do not identify which work controls elapsed time. In particular:

- Many exchanges suggest examining shuffle bytes, partition sizes and the stage critical path; operator presence alone cannot establish a shuffle bottleneck.
- Sorts and the two window orderings suggest examining their stage durations and distributions; a GPU window node cannot establish that its compute dominates the query.
- Nonzero GPU semaphore-wait updates establish recorded waiting in covered task updates. Their sums, `gpuTime`, executor runtime, executor CPU time and JVM GC are cumulative across overlapping tasks, so dividing them by query wall time does not yield GPU/CPU fractions or a valid latency decomposition.
- Task memory maxima and zero standard spill counters cannot establish spare usable VRAM or absence of memory pressure. Explicit device/host transfer, allocation and scratch-lifecycle observations are required for that conclusion.
- These legacy sessions have no verified cleanup barrier after each action. Their process/resource history cannot be treated as the later `verified_gc` protocol. The v1 SF300 disk-space incident is separate nonperformance evidence and is outside this SF30/SF100 coverage inspection.

The evidence supports broad GPU operator selection for the six saved formulations, substantive six-table/outer/self/semi joins, GPU windows and exact-distinct aggregation, and the specific anti-join coverage limitation above. It does not support a causal bottleneck diagnosis or a claim that all elapsed execution ran on the GPU.

## Evidence locations

All paths below are relative to the benchmark directory:

- `results/complex-formal-20261025/sf30/{spark_cpu,spark_gpu}/results.json` and corresponding `plans/*.txt`.
- `results/complex-formal-20261025/sf100/{spark_cpu,spark_gpu}/results.json` and corresponding `plans/*.txt`.
- The single nonhidden JSON event file in each corresponding `{spark_cpu,spark_gpu}-events/` directory.
- `analyze_complex.py`: final-plan node inspection; `inspect_spark_metrics.py`: execution/stage mapping, successful-attempt selection and metric coverage rules.

Only existing saved local evidence was read. No benchmark, data-generation, remote or runtime-modifying job was run for these notes.
