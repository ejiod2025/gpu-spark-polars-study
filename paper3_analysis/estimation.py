"""The estimators of design 5.2 and 5.3: per-round log-ratios, the t-interval, the cell classification, the break-even bracket.

Numerics are Paper 1's (analyze_campaign: t quantile by bisection on the t distribution, no scipy), so an interval here and an interval
there agree to the last digit for the same per-round values.
"""
from __future__ import annotations

from functools import lru_cache
import math

import analyze_campaign as AC

from .constants import (CLASS_CPU_BETTER, CLASS_GPU_BETTER, CLASS_INCONCLUSIVE, CLASS_NO_DIFFERENCE, CLASS_NOT_ESTIMABLE, EQUIVALENCE_MARGIN)

geomean = AC.geomean


@lru_cache(maxsize=None)
def t_critical(df: int) -> float:
    """Two-sided 95% Student-t critical value (R = 8: 2.365, R = 4: 3.182); Paper 1's bisection, cached (the same few df recur thousands of times)."""
    return AC.t_critical(df)


def ratio_record(per_round: dict) -> dict:
    """Estimate of one ratio from {round: positive ratio}: geometric mean, 95% t-interval on the mean log-ratio (df = R - 1), range.

    One round gives an estimate and no interval; none gives nothing. The record is JSON-ready and has stable keys. Same numbers as
    analyze_campaign.ratio_summary (tests/test_paper3_analysis.py asserts it)."""
    rounds = sorted(per_round)
    ratios = [float(per_round[r]) for r in rounds]
    n = len(ratios)
    record = {"estimate": None, "ci_low": None, "ci_high": None, "min": None, "max": None, "df": max(n - 1, 0) if n else None, "n_rounds": n,
              "log_mean": None, "log_sd": None, "rounds": rounds, "per_round": ratios}
    if n == 0:
        return record
    mean, sd = AC.mean_sd([math.log(r) for r in ratios])
    record.update(estimate=math.exp(mean), min=min(ratios), max=max(ratios), log_mean=mean, log_sd=sd)
    if n >= 2:
        half = t_critical(n - 1) * sd / math.sqrt(n)
        record.update(ci_low=math.exp(mean - half), ci_high=math.exp(mean + half))
    return record


def mean_record(per_round: dict) -> dict:
    """Mean of {round: value} (any sign) with the same 95% t-interval (df = R - 1): suite totals and Delta* (5.2, 2.5)."""
    rounds = sorted(per_round)
    values = [float(per_round[r]) for r in rounds]
    mean, sd = AC.mean_sd(values)
    record = {"mean": mean, "ci_low": None, "ci_high": None, "sd": sd, "df": max(len(values) - 1, 0) if values else None,
              "n_rounds": len(values), "rounds": rounds, "per_round": values, "min": min(values) if values else None,
              "max": max(values) if values else None}
    if len(values) >= 2:
        half = t_critical(len(values) - 1) * sd / math.sqrt(len(values))
        record.update(ci_low=mean - half, ci_high=mean + half)
    return record


def welch_record(host_values: dict, reference_values: dict) -> dict:
    """Host / reference ratio of geometric means of unpaired per-round values, Welch-type interval on the log scale (X4, Paper 1 A2)."""
    summary = AC.welch_summary([host_values[r] for r in sorted(host_values)], [reference_values[r] for r in sorted(reference_values)])
    return {"estimate": summary["estimate"], "ci_low": summary["ci_low"], "ci_high": summary["ci_high"], "df": summary["df"],
            "n_rounds": summary["n_host"], "n_rounds_reference": summary["n_ref"], "log_se": summary["log_se"],
            "rounds": sorted(host_values), "rounds_reference": sorted(reference_values)}


def classify(record: dict) -> str:
    """Cell class from the 95% interval of rho = reference / engine (5.3).

    gpu_better: interval entirely above 1; cpu_better: entirely below 1; no_difference: entirely inside [1/1.10, 1.10]; anything else
    with an interval, including one that merely contains 1, is inconclusive. Without an interval (fewer than two rounds) the cell is
    inconclusive, never a difference or "no difference". The order of the checks is the order of design 5.3: a narrow interval above 1
    (say [1.02, 1.05]) is gpu_better, and `within_margin` says it is also inside the equivalence band."""
    low, high = record.get("ci_low"), record.get("ci_high")
    if record.get("estimate") is None:
        return CLASS_NOT_ESTIMABLE
    if low is None or high is None:
        return CLASS_INCONCLUSIVE
    if low > 1.0:
        return CLASS_GPU_BETTER
    if high < 1.0:
        return CLASS_CPU_BETTER
    if low >= 1.0 / EQUIVALENCE_MARGIN and high <= EQUIVALENCE_MARGIN:
        return CLASS_NO_DIFFERENCE
    return CLASS_INCONCLUSIVE


def within_margin(record: dict):
    low, high = record.get("ci_low"), record.get("ci_high")
    if low is None or high is None:
        return None
    return low >= 1.0 / EQUIVALENCE_MARGIN and high <= EQUIVALENCE_MARGIN


def break_even(classes: dict, grid) -> dict:
    """The break-even scale of design 5.3 from {scale: class} over the suite's grid (ascending scale strings).

    The smallest grid scale s such that every grid scale >= s is gpu_better, as the bracket (previous grid scale, s]. Walking down from the top of the grid
    while the cells are gpu_better, the walk ends at the first cell that is not:
      * a real class (cpu_better, no_difference, inconclusive): that scale is not GPU better, the bracket is (that scale, s];
      * no class (not estimable: below resolution, a coverage outcome, no common round): it was not measured, so it is NOT "not GPU better": the outcome is
        censored, "<= s (SFu not estimable)", never a bracket that claims SFu is not GPU better;
      * the bottom of the grid: "<= lowest", censored.
    If the top of the grid is not gpu_better, the outcome is "> top" when it has a real class, and "not determinable" when it is not estimable (nothing is
    known above the last scale that was measured). A log-linear interpolation is descriptive only and is made elsewhere."""
    grid = list(grid)
    top = grid[-1]
    run = []
    stop = None
    for scale in reversed(grid):
        if classes.get(scale) == CLASS_GPU_BETTER:
            run.append(scale)
        else:
            stop = scale
            break
    blank = {"scale": None, "lower": None, "upper": None, "censored": True, "lower_class": None, "blocked_by": None, "reason": None}
    if not run:
        if classes.get(top) in (None, CLASS_NOT_ESTIMABLE):
            return {**blank, "outcome": "not_determinable", "text": f"not determinable (SF{top} not estimable)", "reason": f"SF{top} has no estimate",
                    "blocked_by": top}
        return {**blank, "outcome": "greater_than_top", "text": f"> SF{top}"}
    smallest = run[-1]
    if stop is None:
        return {**blank, "outcome": "at_most_bottom", "scale": smallest, "upper": smallest, "text": f"<= SF{smallest}"}
    if classes.get(stop) in (None, CLASS_NOT_ESTIMABLE):
        return {**blank, "outcome": "at_most_unmeasured", "scale": smallest, "upper": smallest, "text": f"<= SF{smallest} (SF{stop} not estimable)",
                "blocked_by": stop, "reason": f"SF{stop} has no estimate"}
    return {**blank, "outcome": "bracket", "scale": smallest, "lower": stop, "upper": smallest, "text": f"(SF{stop}, SF{smallest}]", "censored": False,
            "lower_class": classes.get(stop)}
