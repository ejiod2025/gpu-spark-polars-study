# Paper 3 analysis: how to run it and what it computes

Implements `docs/PAPER3_ENERGY_DESIGN.md` sections 2, 4 and 5 (amendments A1-A5; hosts A100, H100, C-GCP, C-VM; X7 is dropped).
Code: `analyze_paper3.py` (command line, orchestration) and the package `paper3_analysis/`. Tests: `tests/test_paper3_analysis.py` on the synthetic
trees of `paper3_analysis/synthetic.py` (injected, known values; also `tests/paper3_tree_fakes.py`, a re-export), every expected number computed in the
test from the design text. A watermarked synthetic `stats.json` to draft with:
`python tests/paper3_tree_fakes.py --tree /tmp/p3-fake && python analyze_paper3.py --tree /tmp/p3-fake --output /tmp/p3-fake-out --unblind --allow-synthetic`.

## Synthetic recovery (design 5.4) and the code manifest

    python analyze_paper3.py --recovery OUT [--replicates 12] [--seed 20261123]     # about 20 s; exit 0 only if every check passes
    python analyze_paper3.py --manifest                                              # the files that constitute the analysis code, with sha256

`--recovery` generates synthetic campaigns in a temporary directory (the real shape: two squares on two launches, one square on the A100, a polars_gpu
coverage gap; per-round values = an injected law times seeded log-normal noise of mean one, 3% on time and 2% on energy), analyses them with the real
pipeline and writes `OUT/recovery.json`: every X1-X3 suite-level estimate (all metrics, scales, suites), X4, X5, the idle-GPU energy of X6, Delta* and the
suite totals against their TRUE values (computed from the law alone) with interval, `covers`, `z` and relative error; classes and break-even brackets against what
the true ratios imply; interval coverage over replicate seeds (in total and for R = 8 and R = 4); and the t-interval (R = 2, 3, 4, 8) and Welch interval on
simulated data; then `checks` and `all_passed`. `tests/test_paper3_analysis.py` runs it and shows it fails on a biased pipeline.
The analysis code is `analyze_paper3.py` and `paper3_analysis/*.py`, hashed in this order (`--manifest`; `_meta.analysis_code_sha256` of every `stats.json`
and of `recovery.json`): its name, NUL, its bytes, NUL. It imports, without owning, `power_telemetry.py`, `paper3_gates.py`, `paper3_campaign.py`,
`preflight.py` (the A1 error rules), `analyze_campaign.py` (Paper 1's t quantile and table writer), `analyze_results.py` and `paper3_hosts.json` (the
pre-registered `t_min_s`); `--manifest` lists their hashes too.

## Blinding (design 7.3)

The campaign is blind until every round has completed or been stopped by the rules. The full analysis computes engine-versus-engine and host-versus-host
energy, power and cost aggregates, so it **refuses without `--unblind`**, and `--unblind` does not override what follows. It refuses, before it opens any
result file, unless every planned launch (A100, H100, C-GCP launches 1 and 2, C-VM launch 1; any other host in the tree likewise) is in the tree with a
`status.json` state of `complete` or `stopped` (stopped by the campaign's own rule). `paused` (written between rounds), `running`, `starting`, `interrupted` or no
status block it; so does a planned launch that is not in the tree. A launch replaced by a relaunch is judged by its last instance. A launch that was lost,
abandoned or will not run is declared, by label, with `--declare-ended h100-l2 ...` (recorded in `_meta.declared_ended`); a label that blocks nothing is an
error. The tool has no default path. Until the campaign is over only the QA mode is allowed:

    python analyze_paper3.py --tree rerun/paper3 --output /tmp/p3-qa --qa-only

It reads only `status.json`, `round-*/gates.json`, `round-*/paper3-gates.json` (the files `tools/paper3_campaign/p3_qa_view.py` allows; any other name raises
`ForbiddenRead`) and writes gate outcomes, G7-G11 flag counts, G8 per host and engine, G9 cells, idle stability and the attempts overview: no energy, power or cost value.

## After the campaign

    python analyze_paper3.py --tree rerun/paper3 --output results/paper3-analysis --unblind [--declare-ended LABEL ...] [--invoices invoices.json]

`--tree` holds every launch of every host; any directory below it with a `campaign.json` is a launch (`<host>/launch<N>/rerun/paper3-<host>-l<N>/`); the operator's
`launch-record.json` above it gives P-BILLED's times. Sealed or irrelevant directories are never entered (`preflight-bringup`, `bringup-artifacts`, `snapshots`, ...);
a Paper 1 `campaign.json` is skipped with a warning. `--output` may not be inside the tree. Other flags: `--allow-synthetic` (test trees; outputs say "SYNTHETIC - NOT
RESULTS"), `--allow-status-mismatch` and `--allow-design-mismatch` (continue where the disk and `status.json`, or two launches' `common_sha256`, disagree; a warning),
`--invoices` (`{"h100-l1": usd}`; a gap above 2% is flagged). Exit status 2: the tree breaks a design rule, the message says which, nothing is written.

## What is read and which rounds count

* **Accepted round** = the last attempt of `round-<k>[-repeat-<n>]` whose `gates.json` says `pass` (what `run_campaign.py` accepts). Failed and abandoned attempts are never pooled (counted in `qa.attempts`, `counts`).
* **Complete square** (3.2) = all rounds of one design square accepted within ONE instance; **confirmatory rounds** are those of complete squares (R = 8; one square: R = 4, df = 3). A relaunch after a loss
  (A1 item 2) is another instance of the same launch and is never pooled with the lost one: with no complete square a host is described from the last instance of each launch
  (`basis: descriptive_no_complete_square`), and every other accepted round, per instance, is a sensitivity (`sensitivity.incomplete:<instance>`, also for C-GCP as the reference; `sensitivity.launch:<instance>` per launch).
  Launches of one host that ran different instance types are refused (shapes are never pooled, 3.1); so are records of one launch that name different types. `status.json`'s squares are a cross-check.
* **Eligibility** comes from the accepted attempt's `paper3-gates.json`, whose `t_min_s` must equal `paper3_hosts.json`'s and `campaign.json`'s: G9 `window_below_10s` / `per_run_below_t_min` drop that
  cell's energy (time and cost stay), a G9 window-rule violation drops the whole cell, a cell with measured windows and no G9 verdict is an error, G10 drops above-idle for the scopes it names,
  G6 `energy_valid` false drops the session's energy; G7, G8, G11, G12 are counted in the QA tables and change no estimate.
* **Failures** are classified with `preflight.classify_error` (the A1 rules the campaign itself uses): resource exhaustion (including a JVM heap death or "GC overhead limit exceeded") and unsupported operations are
  coverage outcomes (the engine did not run the query); anything else is an integrity failure. A session that did not complete keeps every query's outcome with the session's class (`completed` or `not_reached`) in the coverage matrix.
* **Energy** (2.4): counter values from `energy_window.gpu_energy` and the idle windows (S1-counter, S1-module), CPU energy from `power.csv` interpolated at each run's stamps by `power_telemetry.s2_pinned_energy_j`
  (S2-pinned = pinned socket package + DRAM, S2-all, the two parts). **A value without a valid tag, or with another host's tag, raises `UntaggedEnergyError`**; no model value exists. A cell is the median over its 3 trial
  windows (all three valid); above-idle = E - P_idle * T, P_idle the mean of the two engine-ready idle windows per scope; negative values are kept.

## Outputs (`OUT/`)

| File | Content | Design |
|---|---|---|
| `stats.json`, `stats_flat.json` | nested and dotted-key statistics, stable keys, no clock; `_meta` has the code and input sha256 | 5.4, 7.4 |
| `stats.tex` | `\defstat{key}{value}`: the headline scalars and every scalar of `counts`, `billing_by_host`, `qa.crosscheck` | - |
| `tables/t01..t04` (`.csv .md .tex`) | X1-X3 suite-level rho with interval, class, scopes, basis, N1 wording; break-even brackets; Delta*; G8 per host and engine | 5.1-5.3, 2.5, A4 |
| `tables/t05..t17.csv` | per query, X4, X5, X6, totals (with scope tag), every cell, coverage matrix, squares, G7-G11 flags, G9 cells, idle stability, attempts, billing | 5.1, 5.4, 4, 2.6 |

`stats.json` blocks: `x1 x2 x3 x3_strict` (`[host][suite][sf][metric]`; metrics `time time_vs_cgcp energy_total energy_above_idle energy_total_s2all energy_above_idle_s2all cost_{b1,b2,b3}_{p_new,p_old,p_billed}`),
`x3_lenient` (diagnostic, see reading 6), `x4 x5 x6`, `delta_star`, `break_even`, `totals`, `price_ratios`, `c_vm_check`, `sensitivity`, `sensitivity_unpaired`, `descriptive` (CV; order and carryover),
`hosts`, `counts`, `scope_matrix`, `qa`, `billing`, `billing_by_host`. rho = reference / engine (> 1: the engine uses less); a record has `estimate ci_low ci_high df n_rounds n_queries min max class within_margin basis`
and, for energy, `ref_scope eng_scope cross_class mixed_scope n1`. Classes: `gpu_better`, `cpu_better`, `no_difference` (interval inside [1/1.10, 1.10]), `inconclusive`, `not_estimable`.
Scalars for the paper (all flat keys): `counts.<host>.{launches_planned, launches_found, launches_lost_replaced, instances, rounds_planned, rounds_accepted, rounds_analysed, rounds_confirmatory, rounds_sensitivity,
round_attempts, round_repeats, round_attempts_failed, squares_planned, squares_complete, sessions_accepted, sessions_analysed, cells_analysed, cells_without_time, coverage_outcomes.{resource_exhaustion,
unsupported_operation,integrity_failure,other_without_time,total}, g9.{cells_below_resolution, window_below_10s, per_run_below_t_min, window_rule_violation, by_engine.<engine>.{cells,below_resolution}}}`;
`qa.crosscheck.<host>.{n_sessions, n_windows, median, p10, p90, min, max, median_abs, spread_iqr, field}` (S1-sampled against the counter, windows of 10 s or more of sessions with clean G7 and G8);
`billing_by_host.<host>.{hours, billed_usd, invoice_usd, gap, gap_above_2pct, instances}`.

## Where each design section is implemented

2.2 idle, 2.4 tags: `cells.py`; 2.5 scopes, N1, Delta*, 2.6 prices, B1-B3, the C-VM check: `contrasts.py` (`metric_sides`, `n1_reading`, `delta_star`, `price`, `c_vm_check`, `price_ratios`, `billing_reconciliation`); 3.2 squares: `tree.py`;
4 gates: `qa.py`, `crosscheck.py`; 5.1 X1-X6: `contrasts.py`; 5.2: `estimation.py` (Paper 1's t quantile), `descriptive.py`; 5.3: `estimation.classify` / `break_even`; 5.4: `qa.coverage_matrix`, `recovery.py`.

## Readings of the design to confirm

1. **Cross-host contrasts pair the rounds both hosts have, by round number** (X1-X3 energy, cost B1/B2, `time_vs_cgcp`, X5, X6; round = block, 5.2). Two hosts' rounds are independent runs, so which round of C-GCP meets which round of a GPU host is a
   convention, and the interval depends on it (not the estimate). Where the two hosts have **no round number in common** (say H100 completed square 1 and C-GCP square 2) the paired cell is `not_estimable`; it is never pooled. Beside every
   cross-host cell, `sensitivity_unpaired` holds a labelled Welch-type estimate (each side keeps its own rounds; unpaired, log scale, Welch-Satterthwaite df, same classes), which exists where the paired one does not. X4 is Welch as written.
2. **G9 drops energy only**; time and cost (perf_counter, price x time) of a below-resolution cell stay.
3. **Class precedence is the order of 5.3**: an interval inside the band but wholly above 1 (say [1.02, 1.05]) is `gpu_better`, `within_margin: true` beside it.
4. Per-query ratios with a non-positive value (above-idle can be negative) are excluded and counted in `n_excluded_nonpositive`.
5. Suite-level geometric means use, per round, the queries both sides have in that round (5.2); suite **totals**, Delta* and X6 use the queries every round has, so rounds are comparable.
6. **X3 takes the best value among the engines that ran the query** (a coverage outcome means "did not run"); if one engine that ran has no value for the metric (below resolution, an invalid counter), that (query, round) is not available for the metric: no other engine stands in.
   `x3_strict` also needs both GPU engines to have run (5.1). `x3_lenient` (best of the engines that have a value) is a labelled diagnostic and carries no claim.
7. **Break-even** (5.3): the smallest scale from which every larger scale is GPU better. A scale that was not measured is not "not GPU better": if the walk down from the top ends at a scale with no estimate the outcome is
   censored `<= SFs (SFu not estimable)`; if the top scale has no estimate it is `not determinable`; `> SF300` only when the top scale has a class that is not GPU better. `basis` is on every record.
8. If the C-VM check fails, B1 uses the C-VM's geometric-mean time per cell (baseline SF10/30/300) as a fixed reference, not paired by round. P-BILLED's B1 price is the C-VM launch's billed price, or P-NEW's if there is none (`_meta.p_billed_b1`).
   P-BILLED and the listed prices are taken from the instance whose rounds are used.

Not computed: session energy and cold-job cost (secondary, 2.6), EX1-EX3 (6), X7; the S1-sampled `gpu.csv` cross-check is a QA summary only, never an energy value.
