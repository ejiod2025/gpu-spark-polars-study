"""X1-X6 of design 5.1: the contrasts, their estimates and classifications (5.2, 5.3), suite totals, Delta* (2.5) and the break-even scales.

rho = reference / engine, so rho > 1 means the engine uses less (time, energy, cost). Per query and round the log-ratio is taken, the
estimate is the geometric mean over rounds and the interval the t-interval of the mean log-ratio (df = R - 1); a (suite, scale) cell
takes, per round, the mean of the per-query log-ratios over the queries both sides have in that round, then the same interval across
rounds (round = block). Suite totals (sum of per-query medians) are reported beside the geometric means and never mixed with them.

Where a reference value comes from (5.1, 2.5, 2.6):
  time              same host (CPU engine on the GPU host / GPU engine), and time_vs_cgcp (CPU engine on C-GCP)
  cost B1 / B2      CPU engine time on C-GCP priced as the right-sized VM (B1) or as rented (B2), over the GPU engine's cost on its host
  cost B3           the same CPU engine on the same GPU host (the idle-GPU tax baseline): equal to the time ratio, kept as its own row
  energy            S2-pinned on C-GCP (package + DRAM of the pinned socket) over the GPU engine's S1 energy: cross-class, "under N1"
Cross-host contrasts pair the rounds that are in the analysis on both hosts by round number (round = block). A GPU host and C-GCP with no
round in common give "not_estimable", never a pooled or unpaired fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics

from .constants import (B1_INSTANCE, BASIS_CONFIRMATORY, BASIS_DESCRIPTIVE, BASIS_INCOMPLETE, BASIS_LAUNCH, BILLING_GAP, C_VM_BAND, C_VM_SCALES,
                        CLASS_CPU_BETTER, CLASS_GPU_BETTER, CLASS_NOT_ESTIMABLE, CONTRAST_MODES, CONTRASTS, CPU_ENGINES, FIXED_TABLES, GPU_ENGINES, N1_NOTE,
                        PRICE_TABLES, REFERENCE_GPU_HOST, S1_TAG, S2_ALL, S2_PINNED, SECONDS_PER_HOUR, WH)
from .estimation import break_even, classify, geomean, mean_record, ratio_record, t_critical, welch_record, within_margin
from .tree import scale_key, scale_sort

SLUG = {"P-NEW": "p_new", "P-OLD": "p_old", "P-BILLED": "p_billed"}


@dataclass(frozen=True)
class Metric:
    kind: str                       # time | energy | cost
    scope: object = None            # energy: "S1" (the host's counter tag), S2-pinned or S2-all
    above: bool = False             # energy above idle
    table: object = None            # cost: price table
    price: str = "own"              # cost: own (the host's price) | b1 (the right-sized VM)


@dataclass(frozen=True)
class Side:
    host: str
    engines: tuple
    metric: Metric
    mode: str = "ran"               # how the side's best engine is chosen: ran | all | lenient (constants.CONTRAST_MODES)


@dataclass
class Selection:
    """Which rounds of which instance enter an analysis: {host: {round: instance label}} and each host's basis."""
    name: str
    subject: dict
    basis: dict
    reference: dict
    reference_basis: dict


def host_rounds(hd, mode: str = "main"):
    """({round: instance label}, basis) of a host: its confirmatory rounds, or, with no complete square, the rounds of the last instance of each launch,
    described (a lost launch's rounds are never pooled with its relaunch's: they are in the sensitivities)."""
    if mode == "main":
        return {r: inst.label for r, (inst, _) in hd.analysis.items()}, hd.basis
    raise ValueError(mode)


def main_selection(tree) -> Selection:
    rounds, basis = {}, {}
    for host, hd in tree.hosts.items():
        rounds[host], basis[host] = host_rounds(hd)
    return Selection("main", rounds, basis, rounds, basis)


def sensitivity_selections(tree) -> list:
    """Sensitivities (3.2, A1 item 2(c)): per instance, the accepted rounds outside the main analysis (an incomplete square, or a launch that was lost and
    replaced; instances are never pooled), and each instance's own confirmatory rounds when a host has several. A GPU host is varied as the subject; the
    CPU-only host, which is the reference of the cross-class contrasts, is varied as the reference."""
    main = main_selection(tree)
    out = []
    for host, hd in tree.hosts.items():
        if hd.kind == "cpu-vm":
            continue
        subject_side = hd.kind == "gpu"

        def selection(name, rounds, basis):
            if subject_side:
                return Selection(name, {host: rounds}, {host: basis}, main.reference, main.basis)
            return Selection(name, main.subject, main.basis, {**main.reference, host: rounds}, {**main.reference_basis, host: basis})
        for label, attempts in sorted(hd.sensitivity.items()):
            out.append(selection(f"incomplete:{label}", {r: label for r in attempts}, BASIS_INCOMPLETE))
        owners = {}
        for number, (inst, _) in hd.confirmatory.items():
            owners.setdefault(inst.label, {})[number] = inst.label
        if len(owners) > 1:
            for label, rmap in sorted(owners.items()):
                out.append(selection(f"launch:{label}", rmap, BASIS_LAUNCH))
    return out


def safe_log(value):
    return math.log(value) if value is not None and value > 0 else None


class Analyzer:
    """The estimands of one tree. `store` is the CellStore of paper3_analysis.cells, `tree` the Tree of paper3_analysis.tree."""

    def __init__(self, tree, store, *, vm_check=None):
        self.tree, self.store = tree, store
        self.vm_check = vm_check
        self.warnings = []
        self.vm_billed = None
        vm = tree.hosts.get("c-vm")
        if vm is not None:                                  # the C-VM instance whose rounds are used
            prices = [i.billed_price for i in (vm.analysis_instances or vm.instances) if i.billed_price is not None]
            self.vm_billed = prices[0] if prices else None
        self.b1_vm_times = (vm_check or {}).get("reference_times") if (vm_check or {}).get("b1_basis") == "vm" else None
        self.unknown_instances = set()

    # ---- values -----------------------------------------------------------------------------------------------------------------
    def price(self, table: str, instance, which: str = "own"):
        itype = instance.instance_type
        if which == "b1":
            b1 = B1_INSTANCE.get(itype)
            if table == "P-BILLED":
                return self.vm_billed if self.vm_billed is not None else FIXED_TABLES["P-NEW"].get(b1)
            return FIXED_TABLES[table].get(b1)
        if table == "P-BILLED":
            return instance.billed_price
        price = FIXED_TABLES[table].get(itype)
        if price is None and itype not in self.unknown_instances:
            self.unknown_instances.add(itype)
            self.warnings.append(f"no {table} price for instance type {itype!r}: cost is not estimable on it")
        return price

    def value(self, cell, metric: Metric):
        if cell is None:
            return None
        if metric.kind == "time":
            return cell.time_s
        if metric.kind == "cost":
            if cell.time_s is None:
                return None
            if metric.price == "b1" and self.b1_vm_times is not None:
                reference = self.b1_vm_times.get((cell.suite, cell.scale, cell.engine, cell.query))
                if reference is not None:
                    price = self.price(metric.table, cell.price_instance, "b1")
                    return None if price is None else price / SECONDS_PER_HOUR * reference
            price = self.price(metric.table, cell.price_instance, metric.price)
            return None if price is None else price / SECONDS_PER_HOUR * cell.time_s
        if metric.kind == "energy":
            series = S1_TAG.get(self.tree.hosts[cell.host].scope) if metric.scope == "S1" else metric.scope
            return cell.energy_value(series, metric.above) if series else None
        raise ValueError(metric)

    def pick(self, side: Side, selection_rounds: dict, round_: int, suite: str, scale: str, query: str):
        """(value, engine) of the side's best (lowest) value for one query and round, or None.

        mode "ran": the best among the engines that ran the query (design 5.1); if one of them has no value for the metric (below resolution, an invalid
        counter) the pair is unavailable, no other engine stands in for it. mode "all": every engine of the side ran and has a value (the strict variant).
        mode "lenient": the best among the engines that have a value (a labelled diagnostic)."""
        by_engine = self.store.at(side.host, selection_rounds[round_], round_, suite, scale).get(query, {})
        found, ran = [], []
        for engine in side.engines:
            cell = by_engine.get(engine)
            if cell is None or not cell.ran:
                continue
            ran.append(engine)
            value = self.value(cell, side.metric)
            if value is not None:
                found.append((value, engine))
        if not found:
            return None
        if side.mode == "lenient":
            return min(found)
        if len(found) < len(ran) or (side.mode == "all" and len(ran) < len(side.engines)):
            return None
        return min(found)

    # ---- pairing ----------------------------------------------------------------------------------------------------------------
    def rounds_for(self, sel: Selection, hosts: tuple, subject: str) -> dict:
        """{round: {host: instance label}} for the rounds the hosts have in common (the subject host from sel.subject, others from sel.reference)."""
        maps = {}
        for host in hosts:
            if host == subject and subject in sel.subject:
                maps[host] = sel.subject[host]
            else:
                maps[host] = sel.reference.get(host, {})
        common = sorted(set.intersection(*[set(m) for m in maps.values()])) if maps else []
        return {r: {h: maps[h][r] for h in hosts} for r in common}

    def paired(self, num: Side, den: Side, rounds: dict, suite: str, scale: str) -> dict:
        """Per (query, round): the numerator's and denominator's best value and engine, over the rounds the hosts share."""
        queries = set()
        for r, labels in rounds.items():
            for side in (num, den):
                queries |= set(self.store.at(side.host, labels[side.host], r, suite, scale))
        cells = {}
        for r, labels in rounds.items():
            for q in sorted(queries):
                n = self.pick(num, {r: labels[num.host]}, r, suite, scale, q)
                d = self.pick(den, {r: labels[den.host]}, r, suite, scale, q)
                if n is not None and d is not None:
                    cells[(q, r)] = (n[0], n[1], d[0], d[1])
        return {"rounds": sorted(rounds), "queries": sorted(queries), "cells": cells}

    def estimate(self, pairing: dict, units: str) -> dict:
        """The suite-level and per-query records of a pairing: per-round log-ratios, geometric mean, t-interval, totals."""
        cells, rounds = pairing["cells"], pairing["rounds"]
        per_query, excluded = {}, 0
        by_round = {r: {} for r in rounds}
        for (q, r), (nv, _, dv, _) in sorted(cells.items()):
            if nv > 0 and dv > 0:
                by_round[r][q] = nv / dv
                per_query.setdefault(q, {})[r] = nv / dv
            else:
                excluded += 1                                   # a non-positive value (above-idle can be negative) has no log-ratio
        per_round = {r: math.exp(statistics.fmean(math.log(v) for v in qs.values())) for r, qs in by_round.items() if qs}
        record = ratio_record(per_round)
        stable = sorted(set.intersection(*[set(qs) for qs in by_round.values()])) if rounds and all(by_round.values()) else []
        record.update(n_queries=len(stable), n_queries_union=len({q for qs in by_round.values() for q in qs}), queries=stable,
                      n_excluded_nonpositive=excluded, rounds_requested=list(rounds), units=units,
                      per_round_n_queries={str(r): len(qs) for r, qs in by_round.items()})
        record["class"] = classify(record)
        record["within_margin"] = within_margin(record)
        record["q"] = {q: {**ratio_record(vals), "class": None} for q, vals in sorted(per_query.items())}
        for q, rec in record["q"].items():
            rec["class"] = classify(rec)
        record["totals"] = self.totals_of(pairing, stable, units)
        return record

    @staticmethod
    def totals_of(pairing: dict, stable: list, units: str):
        """Sum of per-query values over the queries every round has, per round, and their ratio (beside the geometric mean, 5.2)."""
        rounds, cells = pairing["rounds"], pairing["cells"]
        if not stable or not rounds:
            return None
        scale = {"J": 1.0 / WH, "s": 1.0, "USD": 1.0}[units]
        ref = {r: scale * math.fsum(cells[(q, r)][0] for q in stable) for r in rounds if all((q, r) in cells for q in stable)}
        eng = {r: scale * math.fsum(cells[(q, r)][2] for q in stable) for r in ref}
        if not ref:
            return None
        ratio = {r: ref[r] / eng[r] for r in ref if ref[r] > 0 and eng[r] > 0}
        return {"units": "Wh" if units == "J" else units, "ref": mean_record(ref), "eng": mean_record(eng), "ratio": ratio_record(ratio) if ratio else None,
                "n_queries": len(stable)}

    # ---- X1-X3 ------------------------------------------------------------------------------------------------------------------
    def metric_sides(self, name: str, host: str, cpu_engines: tuple, gpu_engines: tuple, modes: tuple = ("ran", "ran")) -> dict:
        """The two sides of one metric of a GPU-host contrast, and what its values mean (units, scopes)."""
        cgcp = "c-gcp"
        own = lambda table, kind="own": Metric("cost", table=table, price=kind)           # noqa: E731
        ref_host, ref_metric, eng_metric, units, ref_scope, eng_scope = host, None, None, "s", None, None
        if name == "time":
            ref_metric = eng_metric = Metric("time")
        elif name == "time_vs_cgcp":
            ref_host, ref_metric, eng_metric = cgcp, Metric("time"), Metric("time")
        elif name.startswith("energy_"):
            above = "above_idle" in name
            scope = S2_ALL if name.endswith("_s2all") else S2_PINNED
            ref_host, ref_metric, eng_metric = cgcp, Metric("energy", scope=scope, above=above), Metric("energy", scope="S1", above=above)
            units, ref_scope, eng_scope = "J", scope, S1_TAG.get(self.tree.hosts[host].scope)
        else:
            _, baseline, slug = name.split("_", 2)
            table = {v: k for k, v in SLUG.items()}[slug]
            ref_metric, eng_metric, units = own(table), own(table), "USD"
            if baseline == "b1":
                ref_host, ref_metric = cgcp, own(table, "b1")
            elif baseline == "b2":
                ref_host = cgcp
        return {"ref": Side(ref_host, cpu_engines, ref_metric, modes[0]), "eng": Side(host, gpu_engines, eng_metric, modes[1]), "units": units,
                "ref_scope": ref_scope, "eng_scope": eng_scope}

    def contrast_metric_names(self) -> list:
        names = ["time", "time_vs_cgcp", "energy_total", "energy_above_idle", "energy_total_s2all", "energy_above_idle_s2all"]
        names += [f"cost_{b}_{SLUG[t]}" for t in PRICE_TABLES for b in ("b1", "b2", "b3")]
        return names

    def n1_reading(self, name: str, record: dict, scopes: dict) -> object:
        """The wording of a cross-class energy cell (2.5): what survives under N1, and what the measured advantage would take to erase."""
        if not name.startswith("energy_"):
            return None
        mixed = scopes["eng_scope"] == "S1-module"
        klass = record["class"]
        if klass == CLASS_CPU_BETTER:
            text = "the GPU engine uses more node energy than the CPU engine under N1 (the direction survives)"
        elif klass == CLASS_GPU_BETTER:
            text = ("GPU better at the measured scopes only; no node-level claim is made (see delta_star for how large the GPU node's "
                    "unmeasured excess would have to be to erase it); the break-even is a lower bound under N1")
        else:
            text = "no node-level energy claim"
        return text + ("; mixed scope: GPU side S1-module, CPU side S2-pinned (shown with both tags, not a like-for-like ratio)" if mixed else "")

    def gpu_host_contrasts(self, sel: Selection) -> dict:
        out = {}
        cgcp_present = "c-gcp" in self.tree.hosts
        for cname, (cpu, gpu) in CONTRASTS.items():
            for host, hd in sorted(self.tree.hosts.items()):
                if hd.kind != "gpu" or host not in sel.subject:
                    continue
                instance = hd.reference_instance
                for suite in sorted(instance.suites):
                    for scale in scale_sort(instance.suites[suite]):
                        for name in self.contrast_metric_names():
                            sides = self.metric_sides(name, host, cpu, gpu, CONTRAST_MODES[cname])
                            hosts = tuple(dict.fromkeys((sides["ref"].host, host)))
                            node = out.setdefault(cname, {}).setdefault(host, {}).setdefault(suite, {}).setdefault(scale_key(scale), {})
                            if sides["ref"].host == "c-gcp" and not cgcp_present:
                                node[name] = self.not_estimable("c-gcp not in the tree", sides)
                                continue
                            rounds = self.rounds_for(sel, hosts, host)
                            pairing = self.paired(sides["ref"], sides["eng"], rounds, suite, scale)
                            record = self.estimate(pairing, sides["units"])
                            self.finish(record, name, sel, host, sides, hosts)
                            node[name] = record
        return out

    def finish(self, record: dict, name: str, sel: Selection, host: str, sides: dict, hosts: tuple) -> None:
        record["basis"] = self.joint_basis(sel, host, sides["ref"].host)
        record.update(ref_host=sides["ref"].host, eng_host=host, ref_scope=sides["ref_scope"], eng_scope=sides["eng_scope"],
                      cross_class=sides["ref_scope"] is not None, mixed_scope=sides["eng_scope"] == "S1-module",
                      n1=self.n1_reading(name, record, sides), selection=sel.name)

    @staticmethod
    def joint_basis(sel: Selection, subject: str, reference: str):
        """The basis of an estimate that rests on two hosts' rounds: confirmatory only if both are; otherwise the first that is not."""
        first = sel.basis.get(subject) if subject in sel.subject else None
        second = sel.reference_basis.get(reference) if reference != subject else first
        bases = [first, second]
        return BASIS_CONFIRMATORY if all(b == BASIS_CONFIRMATORY for b in bases) else next(b for b in bases if b != BASIS_CONFIRMATORY)

    @staticmethod
    def not_estimable(reason: str, sides=None) -> dict:
        record = {"estimate": None, "ci_low": None, "ci_high": None, "min": None, "max": None, "df": None, "n_rounds": 0, "rounds": [], "per_round": [],
                  "n_queries": 0, "queries": [], "q": {}, "totals": None, "class": CLASS_NOT_ESTIMABLE, "within_margin": None, "reason": reason,
                  "basis": None, "ref_scope": None, "eng_scope": None, "cross_class": None, "mixed_scope": None, "n1": None}
        return record

    # ---- cross-host contrasts without the round pairing ------------------------------------------------------------------------
    def cross_host_unpaired(self, sel: Selection) -> dict:
        """A labelled sensitivity of every cross-host X1-X3 cell: the two hosts' rounds are independent runs on different machines, so the primary estimator
        (5.2: a log-ratio per round, which pairs round k of one host with round k of the other) rests on a matching by round number that nothing in the
        design makes meaningful. Here each side keeps its own rounds: per round, the geometric mean over the queries both sides have in every round, then the
        ratio of the two sides' geometric means over rounds with a Welch-type interval on the log scale (unpaired, Welch-Satterthwaite df), and the same
        classes. It exists also where the paired estimate does not (no round in common)."""
        out = {}
        for cname, (cpu, gpu) in CONTRASTS.items():
            for host, hd in sorted(self.tree.hosts.items()):
                if hd.kind != "gpu" or host not in sel.subject:
                    continue
                instance = hd.reference_instance
                for suite in sorted(instance.suites):
                    for scale in scale_sort(instance.suites[suite]):
                        for name in self.contrast_metric_names():
                            sides = self.metric_sides(name, host, cpu, gpu, CONTRAST_MODES[cname])
                            ref_host = sides["ref"].host
                            if ref_host == host or ref_host not in self.tree.hosts:
                                continue
                            ref_rounds, eng_rounds = sel.reference.get(ref_host, {}), sel.subject[host]
                            record = self.unpaired_cell(sides, ref_rounds, eng_rounds, suite, scale)
                            record.update(basis=self.joint_basis(sel, host, ref_host), ref_host=ref_host, eng_host=host, ref_scope=sides["ref_scope"],
                                          eng_scope=sides["eng_scope"], pairing="unpaired_welch", selection=sel.name)
                            out.setdefault(cname, {}).setdefault(host, {}).setdefault(suite, {}).setdefault(scale_key(scale), {})[name] = record
        return out

    def unpaired_cell(self, sides: dict, ref_rounds: dict, eng_rounds: dict, suite: str, scale: str) -> dict:
        def series(side, rounds):
            queries = set()
            for r, label in rounds.items():
                queries |= set(self.store.at(side.host, label, r, suite, scale))
            per_query = {}
            for q in sorted(queries):
                for r, label in rounds.items():
                    picked = self.pick(side, {r: label}, r, suite, scale, q)
                    if picked is not None and picked[0] > 0:
                        per_query.setdefault(q, {})[r] = picked[0]
            return {q: v for q, v in per_query.items() if len(v) == len(rounds)}
        ref_series, eng_series = series(sides["ref"], ref_rounds), series(sides["eng"], eng_rounds)
        queries = sorted(set(ref_series) & set(eng_series))
        record = {"estimate": None, "ci_low": None, "ci_high": None, "df": None, "n_rounds_ref_side": len(ref_rounds), "n_rounds_eng_side": len(eng_rounds),
                  "n_queries": len(queries), "queries": queries, "class": CLASS_NOT_ESTIMABLE, "within_margin": None}
        if not queries or not ref_rounds or not eng_rounds:
            return record
        ref_values = {r: math.exp(statistics.fmean(math.log(ref_series[q][r]) for q in queries)) for r in ref_rounds}
        eng_values = {r: math.exp(statistics.fmean(math.log(eng_series[q][r]) for q in queries)) for r in eng_rounds}
        welch = welch_record(ref_values, eng_values)
        record.update({k: welch[k] for k in ("estimate", "ci_low", "ci_high", "df", "log_se")})
        record["class"] = classify(record)
        record["within_margin"] = within_margin(record)
        return record

    # ---- Delta* -----------------------------------------------------------------------------------------------------------------
    def delta_star(self, sel: Selection) -> dict:
        """Delta* = E_C - E_G per round suite total (queries both classes ran in every round): Wh per suite run and W over the GPU suite time."""
        out = {}
        if "c-gcp" not in self.tree.hosts:
            return out
        for cname, (cpu, gpu) in CONTRASTS.items():
            for host, hd in sorted(self.tree.hosts.items()):
                if hd.kind != "gpu" or host not in sel.subject:
                    continue
                instance = hd.reference_instance
                for suite in sorted(instance.suites):
                    for scale in scale_sort(instance.suites[suite]):
                        for name in ("energy_total", "energy_above_idle"):
                            sides = self.metric_sides(name, host, cpu, gpu, CONTRAST_MODES[cname])
                            rounds = self.rounds_for(sel, ("c-gcp", host), host)
                            pairing = self.paired(sides["ref"], sides["eng"], rounds, suite, scale)
                            cells = pairing["cells"]
                            stable = [q for q in pairing["queries"] if pairing["rounds"] and all((q, r) in cells for r in pairing["rounds"])]
                            if not stable:
                                continue
                            wh, watts = {}, {}
                            for r in pairing["rounds"]:
                                diff = math.fsum(cells[(q, r)][0] - cells[(q, r)][2] for q in stable)
                                seconds = math.fsum(self.time_of(host, rounds[r][host], r, suite, scale, q, cells[(q, r)][3]) for q in stable)
                                wh[r] = diff / WH
                                watts[r] = diff / seconds if seconds > 0 else None
                            record = {"wh_per_suite_run": mean_record(wh), "w_over_gpu_suite_time": mean_record({r: v for r, v in watts.items() if v is not None}),
                                      "n_queries": len(stable), "queries": stable, "ref_scope": sides["ref_scope"], "eng_scope": sides["eng_scope"],
                                      "reading": self.delta_reading(wh), "n1_note": N1_NOTE, "idle_reference_w": self.idle_reference(sel, host),
                                      "basis": self.joint_basis(sel, host, "c-gcp"), "selection": sel.name}
                            out.setdefault(cname, {}).setdefault(host, {}).setdefault(suite, {}).setdefault(scale_key(scale), {})[name] = record
        return out

    def idle_reference(self, sel: Selection, host: str) -> dict:
        """Idle powers beside Delta* for scale (2.5): the measured S2 idle of one socket on C-GCP and the S1 idle of the GPU on this host, with and
        without a CUDA context (a GPU-engine session's windows against a CPU-engine session's), means over the sessions of the analysis's rounds."""
        def mean_of(host_, engines, series, rounds):
            values = [w for engine in engines for r, label, w in self.store.idle.get((host_, engine, series), []) if rounds.get(r) == label and w is not None]
            return statistics.fmean(values) if values else None
        cgcp_rounds = sel.reference.get("c-gcp", {})
        gpu_rounds = sel.subject.get(host, {})
        tag = S1_TAG.get(self.tree.hosts[host].scope)
        return {"s2_pinned_idle_w_one_socket": mean_of("c-gcp", CPU_ENGINES, S2_PINNED, cgcp_rounds),
                "s2_all_idle_w": mean_of("c-gcp", CPU_ENGINES, S2_ALL, cgcp_rounds),
                "s1_idle_gpu_w_no_context": mean_of(host, CPU_ENGINES, tag, gpu_rounds),
                "s1_idle_gpu_w_context_open": mean_of(host, GPU_ENGINES, tag, gpu_rounds), "s1_tag": tag}

    def time_of(self, host, label, round_, suite, scale, query, engine) -> float:
        return self.store.at(host, label, round_, suite, scale)[query][engine].time_s

    @staticmethod
    def delta_reading(wh: dict) -> str:
        """What the sign of Delta* means under N1 (2.5)."""
        values = list(wh.values())
        if not values:
            return "n/a"
        mean = statistics.fmean(values)
        if mean < 0:
            return "Delta* < 0: the CPU side measured less; N1 excludes erasing it, so the GPU engine uses more node energy under N1"
        return ("Delta* > 0: the GPU side measured less; the GPU node uses less node energy only if its unmeasured excess "
                "(U_G - U_C) stays below Delta*; no such claim is made")

    # ---- break-even ---------------------------------------------------------------------------------------------------------------
    def break_evens(self, contrasts: dict) -> dict:
        """Per contrast, host, suite and metric (energy total and above-idle, cost B1/B2/B3 under each price table): 5.3's bracket."""
        out = {}
        wanted = [n for n in self.contrast_metric_names() if n.startswith(("energy_", "cost_")) and not n.endswith("_s2all")]
        for cname, by_host in contrasts.items():
            for host, by_suite in by_host.items():
                for suite, by_scale in by_suite.items():
                    grid = [s for s in scale_sort(self.tree.hosts[host].reference_instance.suites[suite])]
                    for name in wanted:
                        classes = {s: (by_scale.get(scale_key(s), {}).get(name) or {}).get("class") for s in grid}
                        result = break_even(classes, grid)
                        estimates = {s: (by_scale.get(scale_key(s), {}).get(name) or {}).get("estimate") for s in grid}
                        result["interpolated_scale"] = interpolated_crossing(result, estimates)
                        result["classes"] = {scale_key(s): classes[s] for s in grid}
                        result["label"] = ("lower bound on the node-level break-even, under N1 (measured scopes S1 vs S2-pinned)"
                                           if name.startswith("energy_") else None)
                        bases = [(by_scale.get(scale_key(s), {}).get(name) or {}).get("basis") for s in grid]
                        bases = [b for b in bases if b]
                        result["basis"] = BASIS_CONFIRMATORY if bases and all(b == BASIS_CONFIRMATORY for b in bases) else next((b for b in bases if b != BASIS_CONFIRMATORY), None)
                        out.setdefault(cname, {}).setdefault(host, {}).setdefault(suite, {})[name] = result
        return out

    # ---- X4 ---------------------------------------------------------------------------------------------------------------------
    def x4(self, sel: Selection) -> dict:
        """GPU generation: each GPU engine on host h over the same engine on the reference host (H100), Welch-type on the log scale (unpaired rounds)."""
        out = {}
        ref = self.tree.hosts.get(REFERENCE_GPU_HOST)
        if ref is None:
            return out
        ref_rounds = sel.reference.get(REFERENCE_GPU_HOST, {})
        for host, hd in sorted(self.tree.hosts.items()):
            if hd.kind != "gpu" or host == REFERENCE_GPU_HOST or host not in sel.subject:
                continue
            instance = hd.reference_instance
            for engine in GPU_ENGINES:
                for suite in sorted(instance.suites):
                    for scale in scale_sort(instance.suites[suite]):
                        for name, metric, units in self.x4_metrics():
                            record = self.x4_cell(hd, ref, sel.subject[host], ref_rounds, engine, suite, scale, metric, units)
                            record["basis"] = self.joint_basis(sel, host, REFERENCE_GPU_HOST)
                            record["mixed_scope"] = metric.kind == "energy" and S1_TAG.get(hd.scope) != S1_TAG.get(ref.scope)
                            record["host_scope"], record["reference_scope"] = (S1_TAG.get(hd.scope), S1_TAG.get(ref.scope)) if metric.kind == "energy" else (None, None)
                            out.setdefault(host, {}).setdefault(engine, {}).setdefault(suite, {}).setdefault(scale_key(scale), {})[name] = record
        return out

    @staticmethod
    def x4_metrics() -> list:
        metrics = [("time", Metric("time"), "s"), ("energy_total", Metric("energy", scope="S1"), "J"),
                   ("energy_above_idle", Metric("energy", scope="S1", above=True), "J")]
        return metrics + [(f"cost_{SLUG[t]}", Metric("cost", table=t), "USD") for t in PRICE_TABLES]

    def x4_cell(self, hd, ref, host_rounds_, ref_rounds, engine, suite, scale, metric, units) -> dict:
        def series(host, rounds):
            per_query = {}
            for r, label in rounds.items():
                for q, by_engine in self.store.at(host, label, r, suite, scale).items():
                    v = self.value(by_engine.get(engine), metric)
                    if v is not None:
                        per_query.setdefault(q, {})[r] = v
            return per_query
        mine, theirs = series(hd.host, host_rounds_), series(ref.host, ref_rounds)
        queries = sorted(q for q in set(mine) & set(theirs) if len(mine[q]) == len(host_rounds_) and len(theirs[q]) == len(ref_rounds)
                         and all(v > 0 for v in list(mine[q].values()) + list(theirs[q].values())))
        record = {"estimate": None, "ci_low": None, "ci_high": None, "df": None, "n_rounds": len(host_rounds_), "n_rounds_reference": len(ref_rounds),
                  "n_queries": len(queries), "queries": queries, "units": units, "q": {}, "basis": None}
        if not queries:
            record["class"] = CLASS_NOT_ESTIMABLE
            return record
        for q in queries:
            record["q"][q] = welch_record(mine[q], theirs[q])

        def round_values(data, rounds):
            return {r: math.exp(statistics.fmean(math.log(data[q][r]) for q in queries)) for r in rounds}
        record.update(welch_record(round_values(mine, host_rounds_), round_values(theirs, ref_rounds)))
        record["class"] = None                                      # descriptive, confounded with CPU, RAM, link and driver (5.1)
        return record

    # ---- X5, X6 ------------------------------------------------------------------------------------------------------------------
    def x5_x6(self, sel: Selection) -> tuple:
        """X5: a CPU engine on a GPU host over the same engine on C-GCP (time; cost B3 over B1 and over B2). X6: B3 / B1 as price ratio x time
        ratio, and the S1 idle-GPU energy of the CPU-engine runs in Wh per suite run (x86 GPU hosts only)."""
        x5, x6 = {}, {}
        if "c-gcp" not in self.tree.hosts:
            return x5, x6
        for host, hd in sorted(self.tree.hosts.items()):
            if hd.kind != "gpu" or host not in sel.subject:
                continue
            instance = hd.reference_instance
            rounds = self.rounds_for(sel, (host, "c-gcp"), host)
            for engine in CPU_ENGINES:
                for suite in sorted(instance.suites):
                    for scale in scale_sort(instance.suites[suite]):
                        node5 = x5.setdefault(host, {}).setdefault(engine, {}).setdefault(suite, {}).setdefault(scale_key(scale), {})
                        specs = {"time": (Metric("time"), Metric("time"), "s")}
                        for t in PRICE_TABLES:
                            specs[f"cost_b1_{SLUG[t]}"] = (Metric("cost", table=t), Metric("cost", table=t, price="b1"), "USD")
                            specs[f"cost_b2_{SLUG[t]}"] = (Metric("cost", table=t), Metric("cost", table=t), "USD")
                        for name, (on_host, on_cgcp, units) in specs.items():
                            pairing = self.paired(Side(host, (engine,), on_host), Side("c-gcp", (engine,), on_cgcp), rounds, suite, scale)
                            record = self.estimate(pairing, units)
                            record.update(basis=self.joint_basis(sel, host, "c-gcp"), selection=sel.name)
                            record["class"] = None                  # a ratio of one engine on two platforms: estimates only (5.1)
                            node5[name] = record
                        node6 = x6.setdefault(host, {}).setdefault(engine, {}).setdefault(suite, {}).setdefault(scale_key(scale), {})
                        if hd.scope == "board":
                            node6.update(self.x6_cell(hd, sel, host, engine, suite, scale, node5, rounds))
        return x5, x6

    def x6_cell(self, hd, sel, host, engine, suite, scale, node5, rounds) -> dict:
        own_price = {t: self.price(t, hd.reference_instance) for t in ("P-NEW", "P-OLD")}
        cgcp = self.tree.hosts["c-gcp"].reference_instance
        b1_price = {t: self.price(t, cgcp, "b1") for t in ("P-NEW", "P-OLD")}
        out = {"price_ratio_b3_over_b1": {SLUG[t]: (own_price[t] / b1_price[t] if own_price[t] and b1_price[t] else None) for t in own_price},
               "time_ratio": node5["time"],
               "cost_ratio_b3_over_b1": {SLUG[t]: node5[f"cost_b1_{SLUG[t]}"] for t in PRICE_TABLES}}
        s1 = S1_TAG["board"]
        idle = {}
        for r, labels in rounds.items():
            by_query = self.store.at(host, labels[host], r, suite, scale)
            idle[r] = {q: by_engine[engine].energy_value(s1) for q, by_engine in by_query.items()
                       if engine in by_engine and by_engine[engine].energy_value(s1) is not None}
        stable = sorted(set.intersection(*[set(d) for d in idle.values()])) if idle else []
        per_round = {r: math.fsum(d[q] for q in stable) / WH for r, d in idle.items()} if stable else {}
        out["idle_gpu_energy_wh_per_suite_run"] = {**mean_record(per_round), "n_queries": len(stable), "queries": stable, "scope": S1_TAG["board"],
                                                  "note": "S1 idle-GPU energy of CPU-engine runs; not set against a CPU-node figure (2.5)"}
        return out

    # ---- totals -----------------------------------------------------------------------------------------------------------------
    def totals(self, sel: Selection) -> dict:
        """Per host, engine, suite and scale: suite totals (sum of per-query medians over the queries every round has) per round, mean and interval."""
        out = {}
        for host, hd in sorted(self.tree.hosts.items()):
            rounds = sel.subject.get(host, {})
            if not rounds:
                continue
            instance = hd.reference_instance
            metrics = {"time_s": (Metric("time"), 1.0), **{f"cost_usd_{SLUG[t]}": (Metric("cost", table=t), 1.0) for t in PRICE_TABLES}}
            if instance.kind == "gpu":
                metrics.update(energy_wh=(Metric("energy", scope="S1"), 1.0 / WH), energy_above_idle_wh=(Metric("energy", scope="S1", above=True), 1.0 / WH))
            elif instance.kind == "cpu-metal":
                metrics.update(energy_wh=(Metric("energy", scope=S2_PINNED), 1.0 / WH), energy_above_idle_wh=(Metric("energy", scope=S2_PINNED, above=True), 1.0 / WH),
                               energy_s2all_wh=(Metric("energy", scope=S2_ALL), 1.0 / WH), cost_usd_b1_p_new=(Metric("cost", table="P-NEW", price="b1"), 1.0))
            for engine in instance.engines:
                for suite in sorted(instance.suites):
                    for scale in scale_sort(instance.suites[suite]):
                        for name, (metric, factor) in metrics.items():
                            per_round, queries = {}, None
                            for r, label in rounds.items():
                                values = {q: self.value(by_engine.get(engine), metric) for q, by_engine in self.store.at(host, label, r, suite, scale).items()}
                                values = {q: v for q, v in values.items() if v is not None}
                                per_round[r] = values
                                queries = set(values) if queries is None else queries & set(values)
                            queries = sorted(queries or [])
                            if not queries:
                                continue
                            record = mean_record({r: factor * math.fsum(vals[q] for q in queries) for r, vals in per_round.items()})
                            record.update(n_queries=len(queries), queries=queries, basis=sel.basis.get(host),
                                          scope_tag=(S1_TAG.get(instance.scope) if metric.scope == "S1" else metric.scope) if metric.kind == "energy" else None)
                            out.setdefault(host, {}).setdefault(engine, {}).setdefault(suite, {}).setdefault(scale_key(scale), {})[name] = record
        return out


def interpolated_crossing(result: dict, estimates: dict):
    """5.3's descriptive interpolation: inside the break-even bracket, the scale where the mean log-ratio, linear in log SF between the two grid
    scales, reaches zero. None unless the previous scale has an estimate below 1 and the bracket's upper scale one above 1. Descriptive only."""
    if result["outcome"] != "bracket":
        return None
    low, high = estimates.get(result["lower"]), estimates.get(result["upper"])
    if not low or not high or not (low < 1.0 < high):
        return None
    l_low, l_high = math.log(low), math.log(high)
    x_low, x_high = math.log(float(result["lower"])), math.log(float(result["upper"]))
    return math.exp(x_low + (0.0 - l_low) / (l_high - l_low) * (x_high - x_low))


# ---- the C-VM time check of B1 (2.6) ---------------------------------------------------------------------------------------------------

def c_vm_check(tree, store) -> dict:
    """B1 passes if the geometric-mean time ratio VM / metal is in [0.90, 1.10] and no CPU-engine query ran out of memory at SF300 on the VM;
    otherwise B1 uses the VM's times where it has them and the gap is reported (2.6). Cells: baseline SF10, SF30, SF300 x the two CPU engines x queries."""
    vm, metal = tree.hosts.get("c-vm"), tree.hosts.get("c-gcp")
    if vm is None or metal is None:
        return {"status": "not_run", "reason": "no C-VM launch in the tree" if vm is None else "no C-GCP launch in the tree", "b1_basis": "metal", "reference_times": {}}
    vm_rounds, vm_basis = host_rounds(vm)
    metal_rounds, metal_basis = host_rounds(metal)
    times = {}
    for host, rounds in (("vm", (vm, vm_rounds)), ("metal", (metal, metal_rounds))):
        hd, rmap = rounds
        for r, label in rmap.items():
            for scale in C_VM_SCALES:
                for q, by_engine in store.at(hd.host, label, r, "baseline", scale).items():
                    for engine in CPU_ENGINES:
                        cell = by_engine.get(engine)
                        if cell is not None and cell.time_s:
                            times.setdefault((host, "baseline", scale, engine, q), []).append(cell.time_s)
    cells, reference = {}, {}
    for (host, suite, scale, engine, q), values in times.items():
        if host != "vm" or ("metal", suite, scale, engine, q) not in times:
            continue
        v, m = geomean(values), geomean(times[("metal", suite, scale, engine, q)])
        cells[(suite, scale, engine, q)] = v / m
    for (host, suite, scale, engine, q), values in times.items():
        if host == "vm":
            reference[(suite, scale, engine, q)] = geomean(values)
    exhausted = sorted({(c.engine, c.query, c.round) for c in store.cells
                        if c.host == "c-vm" and c.scale == "300" and c.engine in CPU_ENGINES and c.coverage == "resource_exhaustion"})
    overall = geomean(list(cells.values()))
    logs = [math.log(v) for v in cells.values()]
    interval = None
    if len(logs) >= 2:
        mean = statistics.fmean(logs)
        half = t_critical(len(logs) - 1) * statistics.stdev(logs) / math.sqrt(len(logs))
        interval = (math.exp(mean - half), math.exp(mean + half))
    if overall is None and not exhausted:
        return {"status": "not_evaluable", "reason": "no cell has a time on both the VM and the metal host", "b1_basis": "metal", "reference_times": {},
                "resource_exhaustion_at_sf300": [list(e) for e in exhausted], "n_cells": 0}
    passed = overall is not None and C_VM_BAND[0] <= overall <= C_VM_BAND[1] and not exhausted
    by = {}
    for (suite, scale, engine, q), v in cells.items():
        by.setdefault(f"{engine}.sf{scale}", []).append(v)
    return {"status": "pass" if passed else "fail", "ratio_vm_over_metal": overall, "ci_low": interval[0] if interval else None,
            "ci_high": interval[1] if interval else None, "n_cells": len(cells), "band": list(C_VM_BAND),
            "by_engine_scale": {k: geomean(v) for k, v in sorted(by.items())}, "resource_exhaustion_at_sf300": [list(e) for e in exhausted],
            "b1_basis": "metal" if passed else "vm", "vm_rounds": sorted(vm_rounds), "metal_rounds": sorted(metal_rounds),
            "vm_basis": vm_basis, "metal_basis": metal_basis, "reference_times": {} if passed else reference,
            "rule": "design 2.6: pass if the geometric-mean time ratio VM / metal is in [0.90, 1.10] and no CPU-engine query fails for host "
                    "resource exhaustion at SF300; otherwise B1 uses the C-VM times where available (baseline SF10, SF30, SF300) and the gap is "
                    "reported. The interval is descriptive (t-interval over the cells' log-ratios)."}


def price_tables_used(tree) -> dict:
    """Which instance types the tree touches and their prices under each table (the record that goes into stats.json)."""
    out = {}
    for hd in tree.hosts.values():
        for instance in hd.instances:
            itype = instance.instance_type
            out[instance.label] = {"instance_type": itype, "P-NEW": FIXED_TABLES["P-NEW"].get(itype), "P-OLD": FIXED_TABLES["P-OLD"].get(itype),
                                   "P-BILLED": instance.billed_price, "b1_instance": B1_INSTANCE.get(itype),
                                   "credit_funded": (instance.record or {}).get("credit_funded"),
                                   "launch_utc": (instance.campaign.get("cost") or {}).get("launch_utc") or (instance.record or {}).get("launch_utc"),
                                   "terminate_confirmed_utc": (instance.record or {}).get("terminate_confirmed_utc")}
    return out


def price_ratios(analyzer) -> dict:
    """Factorised reporting (2.6): the GPU host is cheaper than the CPU baseline iff p_gpu / p_cpu < t_cpu / t_gpu. The price side, per GPU host, price table and
    baseline (B1 right-sized VM, B2 as rented), for the instances whose rounds are analysed (P-BILLED is the price of each launch); the first instance is the
    record, `instances` has every GPU instance x C-GCP instance pair. The time side is the time_vs_cgcp metric of the contrasts."""
    out = {}
    cgcp = analyzer.tree.hosts.get("c-gcp")
    if cgcp is None:
        return out
    cpu_instances = cgcp.analysis_instances or cgcp.instances
    for host, hd in sorted(analyzer.tree.hosts.items()):
        if hd.kind != "gpu":
            continue
        for table in PRICE_TABLES:
            for baseline, which in (("b1", "b1"), ("b2", "own")):
                records = {}
                for gpu_instance in hd.analysis_instances or hd.instances:
                    for cpu_instance in cpu_instances:
                        gpu, cpu = analyzer.price(table, gpu_instance), analyzer.price(table, cpu_instance, which)
                        records[f"{gpu_instance.label}|{cpu_instance.label}"] = {"p_gpu_over_p_cpu": gpu / cpu if gpu and cpu else None, "p_gpu": gpu, "p_cpu": cpu}
                first = next(iter(records.values()))
                out.setdefault(host, {}).setdefault(SLUG[table], {})[baseline] = {**first, "instances": records}
    return out


def billing_reconciliation(tree, invoices: dict) -> list:
    """P-BILLED (2.6): hours (launch to confirmed termination) x the price at launch against the invoice; a gap above 2% is reported."""
    from datetime import datetime
    rows = []
    for hd in tree.hosts.values():
        for instance in hd.instances:
            record = instance.record or {}
            launch = (instance.campaign.get("cost") or {}).get("launch_utc") or record.get("launch_utc")
            end = record.get("terminate_confirmed_utc")
            hours = None
            try:
                hours = (datetime.fromisoformat(str(end).replace("Z", "+00:00")) - datetime.fromisoformat(str(launch).replace("Z", "+00:00"))).total_seconds() / 3600.0
            except (TypeError, ValueError):
                pass
            price = instance.billed_price
            expected = hours * price if hours is not None and price is not None else None
            invoice = invoices.get(instance.label)
            gap = (invoice - expected) / expected if invoice is not None and expected else None
            rows.append({"instance": instance.label, "host": instance.host, "launch_utc": launch, "terminate_confirmed_utc": end, "hours": hours,
                         "price_usd_per_hour": price, "expected_usd": expected, "invoice_usd": invoice, "gap": gap,
                         "gap_above_2pct": None if gap is None else abs(gap) > BILLING_GAP, "credit_funded": record.get("credit_funded")})
    return rows
