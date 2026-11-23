"""The synthetic recovery run (design 5.4): the analysis code, run on synthetic campaigns with injected KNOWN effects, shows that it recovers them.

One command (`python analyze_paper3.py --recovery OUT`) writes OUT/recovery.json:

  pipeline       one synthetic campaign tree of the real shape (A100, H100, C-GCP, C-VM; two squares on two launches, one on the A100; a polars_gpu coverage gap)
                 whose per-round values are the injected law times seeded log-normal noise (sigma 3% on time, 2% on energy, mean one), written as the harness writes
                 it and analysed by the real pipeline. Every X1-X3 suite-level estimate (all metrics, all scales, both suites), X4, X5, the idle-GPU energy of X6, Delta*
                 and the suite totals is set against its TRUE value (computed from the law alone, with its own arithmetic), with the interval, whether it covers, and
                 the standardised error z; classes and break-even brackets are set against the classes and brackets the true ratios imply.
  replicates     the same on smaller trees, many seeds: how often the 95% interval covers the truth, in total and for R = 8 and R = 4 rounds.
  estimator      the interval machinery alone on simulated per-round log-ratios (R = 2, 3, 4, 8) and the Welch interval (4 against 8 rounds).
  checks         pass / fail statements; `all_passed` is the verdict, and the exit status of the command is 0 only if it holds.

Nothing here reads campaign data: the trees are generated in a temporary directory from the seed.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import tempfile

from . import synthetic as S
from .constants import B1_INSTANCE, CONTRAST_MODES, CONTRASTS, FIXED_TABLES, WH
from .estimation import break_even, classify, ratio_record, t_critical, welch_record

SIGMA_T, SIGMA_E = 0.03, 0.02
DEFAULT_SEED = 20261123
TIME_BASE = {"polars": 2.0, "polars_gpu": 6.0, "spark_cpu": 4.0, "spark_gpu": 12.0}          # spark_gpu never crosses polars_gpu: best-engine choices are stable
TIME_EXPONENT = {"polars": 0.9, "polars_gpu": 0.5, "spark_cpu": 0.85, "spark_gpu": 0.5}
METRICS = ["time", "time_vs_cgcp", "energy_total", "energy_above_idle", "energy_total_s2all", "energy_above_idle_s2all",
           *[f"cost_{b}_{t}" for t in ("p_new", "p_old", "p_billed") for b in ("b1", "b2", "b3")]]
TABLE = {"p_new": "P-NEW", "p_old": "P-OLD", "p_billed": "P-BILLED"}
AMBIGUOUS_HALF_WIDTHS = 3.0           # a class is only checked where the true ratio is further than this many interval half-widths from 1
Z_LIMIT = 5.0                         # |z| above this for any estimate of the single recovery tree fails the check (|z| ~ t_7: the maximum of ~600 of them is about 3.5)


class RecoveryWorld(S.World):
    """The injected law, with seeded log-normal noise of mean one on every value (so a ratio of two values has log-mean equal to the law's)."""

    def __init__(self, seed, sigma_t=SIGMA_T, sigma_e=SIGMA_E):
        super().__init__()
        self.seed, self.sigma_t, self.sigma_e, self.cache = seed, sigma_t, sigma_e, {}

    def noise(self, sigma, *key):
        token = (sigma, key)
        if token not in self.cache:
            z = random.Random("|".join(map(str, (self.seed, sigma, *key)))).gauss(0.0, 1.0)
            self.cache[token] = math.exp(sigma * z - sigma ** 2 / 2.0)
        return self.cache[token]

    def law_time(self, host, suite, scale, query, engine):
        return TIME_BASE[engine] * float(scale) ** TIME_EXPONENT[engine] * S.QUERY_FACTOR[query] * S.HOST_TIME_FACTOR[host][S.engine_kind(engine)]

    def time(self, host, round_, suite, scale, query, engine):
        return self.law_time(host, suite, scale, query, engine) * self.noise(self.sigma_t, "t", host, round_, suite, scale, query, engine)

    def energy(self, host, round_, suite, scale, query, engine):
        watts = self.power(host, engine)
        if watts is None:
            return None
        factor = self.time(host, round_, suite, scale, query, engine) * self.noise(self.sigma_e, "e", host, round_, suite, scale, query, engine)
        return {series: w * factor for series, w in watts.items()}


def recovery_tree(root, world, grid=None, a100=True):
    """H100 (two launches), A100 (one launch: one complete square), C-GCP (two launches), C-VM (one); nothing flagged, polars_gpu cannot run cx_q13."""
    builder = S.TreeBuilder(root, world.truth(), grid=grid)
    builder.add_launch("h100", 1, [1, 2, 3, 4], price=S.PRICE_BILLED["h100"])
    builder.add_launch("h100", 2, [5, 6, 7, 8], price=S.PRICE_BILLED["h100"])
    if a100:
        builder.add_launch("a100", 1, [1, 2, 3, 4], price=S.PRICE_BILLED["a100"])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4], price=S.PRICE_BILLED["c-gcp"])
    builder.add_launch("c-gcp", 2, [5, 6, 7, 8], price=S.PRICE_BILLED["c-gcp"])
    builder.add_launch("c-vm", 1, [1, 2], price=S.PRICE_BILLED["c-vm"])
    return builder


class Truth:
    """True values from the law alone (no noise, no files, none of the analysis code)."""

    def __init__(self, world):
        self.w = world

    def ran(self, host, suite, scale, query, engine):
        return self.w.failure(host, 1, suite, scale, query, engine) is None

    def t(self, host, suite, scale, query, engine):
        return self.w.law_time(host, suite, scale, query, engine)

    def energy(self, host, suite, scale, query, engine, above=False, scope="S1"):
        watts = self.w.power(host, engine)
        t = self.t(host, suite, scale, query, engine)
        if S.HOST_KIND[host] == "gpu":
            value = next(iter(watts.values())) * t
            idle = S.CONTEXT_W[host] if engine in S.GPU_ENGINES else S.IDLE_GPU_W[host]
            if above and S.HOST_SCOPE[host] == "board" and engine in S.CPU_ENGINES:
                return None
            return value - idle * t if above else value
        pinned = (watts["S2-pinned/package"] + watts["S2-pinned/dram"]) * t
        total = pinned + (watts["S2-other"] * t if scope == "S2-all" else 0.0)
        idle = (S.CGCP_IDLE["S2-pinned/package"] + S.CGCP_IDLE["S2-pinned/dram"] + (S.CGCP_IDLE["S2-other"] if scope == "S2-all" else 0.0)) * t
        return total - idle if above else total

    def price(self, host, table, b1=False):
        if table == "P-BILLED":
            return S.PRICE_BILLED["c-vm"] if b1 else S.PRICE_BILLED[host]
        if b1:
            return FIXED_TABLES[table][B1_INSTANCE[S.INSTANCE_TYPE["c-gcp"]]]
        return FIXED_TABLES[table][S.INSTANCE_TYPE[host]]

    def value(self, metric, host, suite, scale, query, engine, role):
        """The law value of `metric` for one side of a GPU-host contrast; role is 'ref' (CPU engines, on c-gcp for cross-host metrics) or 'eng'."""
        cgcp = "c-gcp"
        if metric == "time":
            return self.t(host, suite, scale, query, engine)
        if metric == "time_vs_cgcp":
            return self.t(cgcp if role == "ref" else host, suite, scale, query, engine)
        if metric.startswith("energy_"):
            above, scope = "above_idle" in metric, "S2-all" if metric.endswith("_s2all") else "S2-pinned"
            return self.energy(cgcp, suite, scale, query, engine, above, scope) if role == "ref" else self.energy(host, suite, scale, query, engine, above)
        _, base, slug = metric.split("_", 2)
        table = TABLE[slug]
        if role == "eng":
            return self.price(host, table) / 3600.0 * self.t(host, suite, scale, query, engine)
        if base == "b1":
            return self.price(cgcp, table, b1=True) / 3600.0 * self.t(cgcp, suite, scale, query, engine)
        if base == "b2":
            return self.price(cgcp, table) / 3600.0 * self.t(cgcp, suite, scale, query, engine)
        return self.price(host, table) / 3600.0 * self.t(host, suite, scale, query, engine)

    def best(self, metric, host, suite, scale, query, engines, role, mode):
        """The best law value of a side under the design's rule: the engines that ran the query, all of which must have a value; the strict mode needs every engine."""
        ran = [e for e in engines if self.ran(host, suite, scale, query, e)]
        if not ran or (mode == "all" and len(ran) < len(engines)):
            return None
        values = [self.value(metric, host, suite, scale, query, e, role) for e in ran]
        return None if any(v is None for v in values) else min(values)

    def suite_ratio(self, cname, host, metric, suite, scale):
        """exp of the mean over queries of the log of the law ratio reference / engine, and the queries used."""
        cpu, gpu = CONTRASTS[cname]
        modes = CONTRAST_MODES[cname]
        logs = []
        for q in S.QUERIES[suite]:
            ref = self.best(metric, host, suite, scale, q, cpu, "ref", modes[0])
            eng = self.best(metric, host, suite, scale, q, gpu, "eng", modes[1])
            if ref and eng and ref > 0 and eng > 0:
                logs.append(math.log(ref / eng))
        return (math.exp(sum(logs) / len(logs)), len(logs)) if logs else (None, 0)


def z_score(record, true):
    """(log estimate - log true) / se with se from the interval's width and the t quantile of its df; None without an interval."""
    if record.get("estimate") is None or record.get("ci_low") is None or not record.get("df") or true is None:
        return None
    se = (math.log(record["ci_high"]) - math.log(record["ci_low"])) / (2.0 * t_critical(int(round(record["df"]))))
    return (math.log(record["estimate"]) - math.log(true)) / se if se > 0 else None


def compare(estimand, key, record, true, extra=None) -> dict:
    out = {"estimand": estimand, "key": key, "true": true, "estimate": record.get("estimate"), "ci_low": record.get("ci_low"), "ci_high": record.get("ci_high"),
           "n_rounds": record.get("n_rounds")}
    if record.get("estimate") is not None and true is not None:
        out["rel_error"] = record["estimate"] / true - 1.0
    if record.get("ci_low") is not None and true is not None:
        out["covers"] = record["ci_low"] <= true <= record["ci_high"]
        out["z"] = z_score(record, true)
    out.update(extra or {})
    return out


def mean_record_compare(estimand, key, record, true) -> dict:
    """A mean with a t-interval (Delta*, totals, idle energy): the same comparison on the linear scale."""
    out = {"estimand": estimand, "key": key, "true": true, "estimate": record.get("mean"), "ci_low": record.get("ci_low"), "ci_high": record.get("ci_high"),
           "n_rounds": record.get("n_rounds")}
    if record.get("mean") is not None and true:
        out["rel_error"] = record["mean"] / true - 1.0
    if record.get("ci_low") is not None and true is not None:
        out["covers"] = record["ci_low"] <= true <= record["ci_high"]
        se = (record["ci_high"] - record["ci_low"]) / (2.0 * t_critical(int(record["df"])))
        out["z"] = (record["mean"] - true) / se if se > 0 else None
    return out


def independent_break_even(classes: dict, grid) -> str:
    """5.3, written again from the design text for this check: the smallest scale from which every larger scale is GPU better."""
    run, blocker = [], None
    for scale in reversed(grid):
        if classes[scale] != "gpu_better":
            blocker = scale
            break
        run.append(scale)
    if not run:
        return f"> SF{grid[-1]}"
    smallest = run[-1]
    return f"<= SF{smallest}" if blocker is None else f"(SF{blocker}, SF{smallest}]"


def pipeline(stats: dict, world) -> dict:
    """Set every estimate of the analysed single tree against its true value."""
    truth = Truth(world)
    rows, classes, brackets = [], {"decided": 0, "agree": 0, "ambiguous": 0, "disagree": []}, {"cells": 0, "exact": 0, "ambiguous": 0, "wrong": []}
    for cname in CONTRASTS:
        if cname == "x3_lenient":
            continue
        for host in ("h100", "a100"):
            for suite, scales in S.GRID.items():
                for metric in METRICS:
                    true_by_scale, class_by_scale, ambiguous = {}, {}, False
                    for sf in scales:
                        record = stats[cname][host][suite][f"sf{sf}"][metric]
                        true, n = truth.suite_ratio(cname, host, metric, suite, sf)
                        row = compare(cname, f"{host}.{suite}.sf{sf}.{metric}", record, true, {"class": record["class"], "n_queries": record["n_queries"]})
                        rows.append(row)
                        true_by_scale[sf] = true
                        if true is None or record["ci_low"] is None:
                            class_by_scale[sf] = "not_estimable"
                            continue
                        class_by_scale[sf] = record["class"]
                        half = (math.log(record["ci_high"]) - math.log(record["ci_low"])) / 2.0
                        expected = "gpu_better" if true > 1.0 else "cpu_better"
                        row["true_class"] = expected
                        if abs(math.log(true)) <= AMBIGUOUS_HALF_WIDTHS * half:
                            classes["ambiguous"] += 1
                            ambiguous = True
                        else:
                            classes["decided"] += 1
                            if record["class"] == expected:
                                classes["agree"] += 1
                            else:
                                classes["disagree"].append({"key": f"{cname}.{host}.{suite}.sf{sf}.{metric}", "class": record["class"], "expected": expected})
                    if metric in ("energy_total", "energy_above_idle") or metric.startswith("cost_"):
                        if metric.endswith("_s2all"):
                            continue
                        got = stats["break_even"][cname][host][suite][metric]["text"]
                        expected_classes = {sf: ("gpu_better" if true_by_scale[sf] and true_by_scale[sf] > 1.0 else "cpu_better" if true_by_scale[sf] else "not_estimable") for sf in scales}
                        want = independent_break_even(expected_classes, list(scales))
                        brackets["cells"] += 1
                        if got == want:
                            brackets["exact"] += 1
                        elif ambiguous or "not_estimable" in class_by_scale.values():
                            brackets["ambiguous"] += 1
                        else:
                            brackets["wrong"].append({"key": f"{cname}.{host}.{suite}.{metric}", "got": got, "true": want})
    for engine in S.GPU_ENGINES:                                                      # X4: A100 over H100
        for suite, scales in S.GRID.items():
            for sf in scales:
                for name, metric in (("time", "time"), ("energy_total", "energy_total"), ("cost_p_new", "cost")):
                    queries = [q for q in S.QUERIES[suite] if truth.ran("a100", suite, sf, q, engine)]
                    if not queries:
                        continue

                    def law(host, q):
                        t = truth.t(host, suite, sf, q, engine)
                        return t if metric == "time" else (truth.energy(host, suite, sf, q, engine) if metric == "energy_total" else FIXED_TABLES["P-NEW"][S.INSTANCE_TYPE[host]] / 3600.0 * t)
                    true = math.exp(sum(math.log(law("a100", q) / law("h100", q)) for q in queries) / len(queries))
                    rows.append(compare("x4", f"a100.{engine}.{suite}.sf{sf}.{name}", stats["x4"]["a100"][engine][suite][f"sf{sf}"][name], true))
    for engine in S.CPU_ENGINES:                                                      # X5: a CPU engine on the GPU host over the same engine on C-GCP
        for suite, scales in S.GRID.items():
            for sf in scales:
                queries = S.QUERIES[suite]
                true = math.exp(sum(math.log(truth.t("h100", suite, sf, q, engine) / truth.t("c-gcp", suite, sf, q, engine)) for q in queries) / len(queries))
                rows.append(compare("x5", f"h100.{engine}.{suite}.sf{sf}.time", stats["x5"]["h100"][engine][suite][f"sf{sf}"]["time"], true))
                idle = sum(truth.energy("h100", suite, sf, q, engine) for q in queries) / WH               # X6: the idle GPU's energy in Wh per suite run
                rows.append(mean_record_compare("x6_idle_gpu_wh", f"h100.{engine}.{suite}.sf{sf}", stats["x6"]["h100"][engine][suite][f"sf{sf}"]["idle_gpu_energy_wh_per_suite_run"], idle))
    for suite, scales in S.GRID.items():
        for sf in scales:
            queries = S.QUERIES[suite]
            kept = list(queries)
            record = stats["delta_star"]["x3"]["h100"][suite][f"sf{sf}"]["energy_total"]["wh_per_suite_run"]
            true = sum(min(truth.energy("c-gcp", suite, sf, q, e) for e in S.CPU_ENGINES)
                       - min(truth.energy("h100", suite, sf, q, e) for e in S.GPU_ENGINES if truth.ran("h100", suite, sf, q, e)) for q in kept) / WH
            rows.append(mean_record_compare("delta_star", f"x3.h100.{suite}.sf{sf}.energy_total", record, true))
            total = sum(truth.t("h100", suite, sf, q, "polars_gpu") for q in queries if truth.ran("h100", suite, sf, q, "polars_gpu"))
            rows.append(mean_record_compare("totals", f"h100.polars_gpu.{suite}.sf{sf}.time_s", stats["totals"]["h100"]["polars_gpu"][suite][f"sf{sf}"]["time_s"], total))
    covered = [r for r in rows if "covers" in r]
    zs = [abs(r["z"]) for r in rows if r.get("z") is not None]
    return {"estimates": rows, "n_estimates": len(rows), "n_intervals": len(covered), "covered": sum(1 for r in covered if r["covers"]),
            "max_abs_z": max(zs) if zs else None, "median_abs_rel_error": statistics.median(abs(r["rel_error"]) for r in rows if "rel_error" in r),
            "classification": classes, "break_even": brackets}


def replicate_coverage(run_analysis, seeds, workdir: Path) -> dict:
    """Interval coverage of the pipeline over many seeds, on smaller trees (two scales per suite): all X1-X3 suite-level estimates, by the rounds they rest on."""
    grid = {"baseline": ("10", "100"), "complex": ("30",)}
    total = {"intervals": 0, "covered": 0}
    by_rounds = {}
    for seed in seeds:
        world = RecoveryWorld(seed)
        tree = workdir / f"replicate-{seed}"
        recovery_tree(tree / "tree", world, grid=grid)
        stats = run_analysis(tree / "tree", tree / "out", unblind=True, allow_synthetic=True, write=False)
        truth = Truth(world)
        for cname in ("x1", "x2", "x3"):
            for host in ("h100", "a100"):
                for suite, scales in grid.items():
                    for sf in scales:
                        for metric in METRICS:
                            record = stats[cname][host][suite][f"sf{sf}"][metric]
                            true, _ = truth.suite_ratio(cname, host, metric, suite, sf)
                            if true is None or record["ci_low"] is None:
                                continue
                            hit = record["ci_low"] <= true <= record["ci_high"]
                            total["intervals"] += 1
                            total["covered"] += hit
                            node = by_rounds.setdefault(str(record["n_rounds"]), {"intervals": 0, "covered": 0})
                            node["intervals"] += 1
                            node["covered"] += hit
    for node in [total, *by_rounds.values()]:
        node["rate"] = node["covered"] / node["intervals"] if node["intervals"] else None
    return {"seeds": list(seeds), "total": total, "by_rounds": dict(sorted(by_rounds.items()))}


def estimator_coverage(seed: int, repetitions: int = 4000) -> dict:
    """The interval machinery alone: per-round log-ratios ~ N(mu, s^2); the t-interval's coverage for R = 2, 3, 4, 8, and the Welch interval (4 against 8 rounds)."""
    rng = random.Random(seed)
    out = {"repetitions": repetitions, "t_interval": {}, "welch_4_vs_8": None}
    for rounds in (2, 3, 4, 8):
        hits = 0
        for _ in range(repetitions):
            logs = [rng.gauss(0.3, 0.05) for _ in range(rounds)]
            record = ratio_record({r: math.exp(v) for r, v in enumerate(logs)})
            hits += record["ci_low"] <= math.exp(0.3) <= record["ci_high"]
        out["t_interval"][str(rounds)] = hits / repetitions
    hits = 0
    for _ in range(repetitions):
        a = {r: math.exp(rng.gauss(0.4, 0.08)) for r in range(4)}
        b = {r: math.exp(rng.gauss(0.1, 0.03)) for r in range(8)}
        record = welch_record(a, b)
        hits += record["ci_low"] <= math.exp(0.3) <= record["ci_high"]
    out["welch_4_vs_8"] = hits / repetitions
    return out


def code_sha256_of(files) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(Path(path).name.encode() + b"\0" + Path(path).read_bytes() + b"\0")
    return digest.hexdigest()


def run_recovery(output, *, run_analysis, replicates: int = 12, seed: int = DEFAULT_SEED, estimator_repetitions: int = 4000, code_sha256: str = None) -> dict:
    """Run the three parts, write OUT/recovery.json and return the report (report["all_passed"] is the verdict)."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p3-recovery-") as scratch:
        scratch = Path(scratch)
        world = RecoveryWorld(seed)
        recovery_tree(scratch / "single" / "tree", world)
        stats = run_analysis(scratch / "single" / "tree", scratch / "single" / "out", unblind=True, allow_synthetic=True, write=False)
        single = pipeline(stats, world)
        seeds = [seed + 1 + i for i in range(replicates)]
        replicated = replicate_coverage(run_analysis, seeds, scratch / "replicates") if replicates else None
    estimator = estimator_coverage(seed, estimator_repetitions)
    checks = []

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
    check("every_x1_x3_estimate_has_a_true_value", single["n_estimates"] > 1000 and single["n_intervals"] > 1000, f"{single['n_estimates']} estimates, {single['n_intervals']} intervals")
    check("estimates_are_within_sampling_error_of_the_truth", single["max_abs_z"] is not None and single["max_abs_z"] <= Z_LIMIT,
          f"largest |z| = {single['max_abs_z']:.2f} (limit {Z_LIMIT}); median |relative error| {single['median_abs_rel_error']:.4f}")
    rate = single["covered"] / single["n_intervals"]
    check("intervals_of_the_single_tree_cover_the_truth_mostly", rate >= 0.90, f"{single['covered']} of {single['n_intervals']} cover ({rate:.3f}); many are the same ratio under different metrics")
    check("no_class_contradicts_the_true_ratio_where_it_is_clear", not single["classification"]["disagree"],
          f"{single['classification']['decided']} cells clear ({single['classification']['agree']} agree), {single['classification']['ambiguous']} within "
          f"{AMBIGUOUS_HALF_WIDTHS:g} half-widths of 1 not scored; disagreements: {single['classification']['disagree'][:3]}")
    check("no_break_even_bracket_is_wrong_where_every_scale_is_clear", not single["break_even"]["wrong"],
          f"{single['break_even']['exact']} of {single['break_even']['cells']} brackets equal the true one, {single['break_even']['ambiguous']} differ only through an "
          f"unclear or unmeasured scale; wrong: {single['break_even']['wrong'][:3]}")
    if replicated:
        r = replicated["total"]["rate"]
        check("replicated_interval_coverage_is_near_95_percent", r is not None and 0.91 <= r <= 0.985,
              f"{replicated['total']['covered']} of {replicated['total']['intervals']} over {len(seeds)} seeds ({r:.3f}); by rounds " +
              ", ".join(f"R={k}: {v['rate']:.3f} (n={v['intervals']})" for k, v in replicated["by_rounds"].items()))
    for rounds, rate_ in estimator["t_interval"].items():
        check(f"t_interval_coverage_R{rounds}", abs(rate_ - 0.95) <= 0.015, f"{rate_:.4f} over {estimator['repetitions']} simulations")
    check("welch_interval_coverage_4_vs_8", abs(estimator["welch_4_vs_8"] - 0.95) <= 0.02, f"{estimator['welch_4_vs_8']:.4f}")
    report = {"_meta": {"purpose": "design 5.4: the analysis code run on synthetic campaigns with injected known effects; nothing here is a result", "seed": seed,
                        "replicates": replicates, "noise": {"sigma_time": SIGMA_T, "sigma_energy": SIGMA_E, "kind": "log-normal, mean one"},
                        "analysis_code_sha256": code_sha256, "synthetic": True, "watermark": "SYNTHETIC - NOT RESULTS"},
              "pipeline": {k: v for k, v in single.items() if k != "estimates"}, "estimates": single["estimates"], "replicates": replicated, "estimator": estimator,
              "checks": checks, "all_passed": all(c["passed"] for c in checks)}
    (output / "recovery.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str, allow_nan=False) + "\n")
    return report
