"""Fixed facts of the Paper 3 analysis: the numbers of docs/PAPER3_ENERGY_DESIGN.md, restated once.

Nothing here is tuned. Prices are the frozen tables of design 2.6 (P-NEW at the lock, P-OLD the pre-2026-11-16 Lambda list); the
equivalence margin is 5.3; the grid and engines are 3.2. tests/test_paper3_analysis.py checks the price tables against the planner's
and the thresholds against the design text it quotes.
"""
from __future__ import annotations

import power_telemetry

DESIGN_DOC = "docs/PAPER3_ENERGY_DESIGN.md"
FORMAT = 1

ENGINES = ("polars", "polars_gpu", "spark_cpu", "spark_gpu")
CPU_ENGINES = ("polars", "spark_cpu")
GPU_ENGINES = ("polars_gpu", "spark_gpu")
SUITES = ("baseline", "complex")
SUITE_SCALES = {"baseline": ("1", "10", "30", "100", "300"), "complex": ("30", "100", "300")}      # design 3.2 (the C-VM runs 10, 30, 300 only)
# contrast id -> (CPU reference engines, GPU engines); x3 is the best CPU engine over the best GPU engine (5.1)
CONTRASTS = {"x1": (("polars",), ("polars_gpu",)), "x2": (("spark_cpu",), ("spark_gpu",)),
             "x3": (CPU_ENGINES, GPU_ENGINES), "x3_strict": (CPU_ENGINES, GPU_ENGINES), "x3_lenient": (CPU_ENGINES, GPU_ENGINES)}
# how a side picks its best engine (contrasts.Analyzer.pick): "ran" = among the engines that ran the query, and one of them with no value (below
# resolution, ...) makes the pair unavailable; "all" = every engine of the side ran and has a value (the strict variant of 5.1); "lenient" = among the
# engines that have a value (a labelled diagnostic: it can silently swap in another engine)
CONTRAST_MODES = {"x1": ("ran", "ran"), "x2": ("ran", "ran"), "x3": ("ran", "ran"), "x3_strict": ("ran", "all"), "x3_lenient": ("lenient", "lenient")}
CONTRAST_LABELS = {"x1": "Polars CPU / Polars GPU", "x2": "Spark CPU / Spark GPU", "x3": "best CPU engine / best GPU engine (among the engines that ran the query)",
                   "x3_strict": "best CPU engine / best GPU engine (queries both GPU engines ran)",
                   "x3_lenient": "diagnostic: best of the engines that have a value, whether or not another engine also ran the query"}

TRIALS = 3                           # 3.2: three trial windows per query, the median is the round's value
LEVEL = 0.95                         # 5.2
EQUIVALENCE_MARGIN = 1.10            # 5.3: +-10%, the interval [1/1.10, 1.10]
IDLE_LABELS = ("before_warmups", "after_last_trial")           # 2.2: P_idle = mean of the two windows' mean power
REFERENCE_GPU_HOST = "h100"          # 5.1 X4: host h / G-H100
GPU_HOSTS = ("a100", "h100", "gh200", "b200")
CPU_HOSTS = ("c-gcp", "c-vm")
CPU_ONLY_ENERGY_HOST = "c-gcp"
TIME_CHECK_HOST = "c-vm"
EXPECTED_HOSTS = ("a100", "h100", "c-gcp", "c-vm")             # amendment A5: GH200 and B200 are not run
OPTIONAL_HOSTS = ("gh200", "b200")
C_VM_SCALES = ("10", "30", "300")
C_VM_BAND = (0.90, 1.10)             # 2.6 B1: pass if the geometric-mean time ratio VM / metal is in [0.90, 1.10]
WH = 3600.0
SECONDS_PER_HOUR = 3600.0
BILLING_GAP = 0.02                   # 2.6 P-BILLED: an invoice gap above 2% is reported

# 2.4 scope tags; the S1 tag of a GPU host follows its declared counter scope
S1_TAG = {"board": power_telemetry.SCOPE_S1_COUNTER, "module": power_telemetry.SCOPE_S1_MODULE}
S2_PINNED, S2_ALL = power_telemetry.SCOPE_S2_PINNED, power_telemetry.SCOPE_S2_ALL
S2_PINNED_PACKAGE, S2_PINNED_DRAM = S2_PINNED + "/package", S2_PINNED + "/dram"       # series keys; both carry the tag S2-pinned
SERIES_TAG = {S2_PINNED_PACKAGE: S2_PINNED, S2_PINNED_DRAM: S2_PINNED}
UNMEASURED_ENERGY_NOTE = "CPU energy not measured on this host"

# 2.6 price tables, USD per instance-hour. P-NEW frozen at the lock; P-OLD differs in the three Lambda prices that rose on 2026-11-16.
P_NEW = {"gpu_1x_h100_sxm5": 4.89, "gpu_1x_gh200": 2.59, "gpu_1x_b200_sxm6": 7.99, "gpu_1x_a100_sxm4": 1.99,
         "c3-highmem-192-metal": 12.701568, "c3-standard-192-metal": 9.677184, "c3-highcpu-192-metal": 8.669056,
         "c3-highmem-44": 2.910776, "m7i.metal-24xl": 4.8384, "r7i.8xlarge": 2.1168}
P_OLD = {**P_NEW, "gpu_1x_h100_sxm5": 4.29, "gpu_1x_gh200": 2.29, "gpu_1x_b200_sxm6": 6.99}
PRICE_TABLES = ("P-NEW", "P-OLD", "P-BILLED")
FIXED_TABLES = {"P-NEW": P_NEW, "P-OLD": P_OLD}
# B1 (2.6): the smallest GCP C3 VM with >= 26 vCPU and >= 200 GiB; on the AWS fallback host r7i.8xlarge
B1_INSTANCE = {"c3-highmem-192-metal": "c3-highmem-44", "c3-standard-192-metal": "c3-highmem-44", "c3-highcpu-192-metal": "c3-highmem-44",
               "m7i.metal-24xl": "r7i.8xlarge"}
BASELINES = ("b1", "b2", "b3")

CLASS_GPU_BETTER, CLASS_CPU_BETTER, CLASS_NO_DIFFERENCE, CLASS_INCONCLUSIVE = "gpu_better", "cpu_better", "no_difference", "inconclusive"
CLASS_NOT_ESTIMABLE = "not_estimable"
CLASSES = (CLASS_GPU_BETTER, CLASS_CPU_BETTER, CLASS_NO_DIFFERENCE, CLASS_INCONCLUSIVE, CLASS_NOT_ESTIMABLE)

BASIS_CONFIRMATORY = "confirmatory"                    # complete squares only (3.2)
BASIS_DESCRIPTIVE = "descriptive_no_complete_square"   # a host with no complete square (5.4): reported outside the confirmatory set
BASIS_INCOMPLETE = "sensitivity_incomplete_squares"    # rounds of an incomplete square (3.2): a sensitivity only
BASIS_LAUNCH = "sensitivity_launch"                    # one launch's confirmatory rounds (3.2: instance is a sensitivity factor)

WATERMARK = "SYNTHETIC - NOT RESULTS"

N1_NOTE = ("cross-class energy: E_G (S1, GPU board or module) against E_C (S2-pinned, package + DRAM), each a node-energy lower bound; "
           "any node-level statement is worded 'under N1' (design 2.5)")


def energy_series_tag(series: str) -> str:
    """The scope tag a series key carries; a key that is not a tag nor a known component raises (the analysis refuses untagged values)."""
    return power_telemetry.require_scope_tag(SERIES_TAG.get(series, series))
