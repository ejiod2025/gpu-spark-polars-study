"""analyze_paper3.py on synthetic campaign trees with KNOWN ground truth (tests/paper3_tree_fakes.py).

Every expected number below is computed here, from the injected values and the design's rules (docs/PAPER3_ENERGY_DESIGN.md sections 2.2, 2.5,
2.6, 5.1-5.3), with its own few lines of arithmetic and a hard-coded Student-t table: nothing is read back from the module under test. The
trees are written as the campaign tooling leaves them (launch directories, accepted and failed attempts, gate reports, RAPL series), so the
tests cover the reader as well as the estimators.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
for path in (REPO, REPO / "tests", REPO / "tools" / "paper3_campaign"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import analyze_campaign as AC  # noqa: E402
import analyze_paper3 as A  # noqa: E402
import paper3_fakes as RF  # noqa: E402
import paper3_gates as pg  # noqa: E402
import paper3_tree_fakes as F  # noqa: E402
from paper3_analysis import cells as cells_mod  # noqa: E402
from paper3_analysis import constants as K  # noqa: E402
from paper3_analysis import contrasts as contrasts_mod  # noqa: E402
from paper3_analysis import estimation as E  # noqa: E402
from paper3_analysis import qa as qa_mod  # noqa: E402
from paper3_analysis import tree as tree_mod  # noqa: E402

T95 = {1: 12.7062047, 2: 4.30265273, 3: 3.18244631, 7: 2.36462425}            # two-sided 95% Student-t, the table values
PNEW = {"h100": 4.89, "a100": 1.99, "c-gcp": 12.701568, "b1": 2.910776}
POLD = {"h100": 4.29, "a100": 1.99, "c-gcp": 12.701568, "b1": 2.910776}
PBILLED = F.PRICE_BILLED
TABLES = {"P-NEW": PNEW, "P-OLD": POLD}
IDLE_PINNED_W = 180.0 + 36.0


# ---- an oracle of the design, written from the design text --------------------------------------------------------------------------------

def summary(per_round: dict) -> dict:
    """5.2: geometric mean of the per-round ratios, t-interval of the mean log-ratio with df = R - 1."""
    logs = [math.log(v) for _, v in sorted(per_round.items())]
    n = len(logs)
    mean = sum(logs) / n
    out = {"n": n, "estimate": math.exp(mean), "ci_low": None, "ci_high": None}
    if n >= 2:
        sd = math.sqrt(sum((x - mean) ** 2 for x in logs) / (n - 1))
        half = T95[n - 1] * sd / math.sqrt(n)
        out.update(ci_low=math.exp(mean - half), ci_high=math.exp(mean + half))
    return out


def classify(low, high):
    """5.3: gpu_better / cpu_better / no_difference (+-10%) / inconclusive."""
    if low is None:
        return "inconclusive"
    if low > 1:
        return "gpu_better"
    if high < 1:
        return "cpu_better"
    if low >= 1 / 1.10 and high <= 1.10:
        return "no_difference"
    return "inconclusive"


class Oracle:
    """Expected values from the injected truth. rounds: {host: [confirmatory rounds]}; below: {(host, suite, scale)} whose energy is below resolution."""

    def __init__(self, world, rounds, queries=F.QUERIES, below=(), billed=PBILLED):
        self.w, self.rounds, self.queries, self.below, self.billed = world, rounds, queries, set(below), billed

    # -- values of one cell
    def time(self, host, r, suite, sf, q, e):
        return self.w.time(host, r, suite, sf, q, e)

    def energy(self, host, r, suite, sf, q, e, above=False, scope="S1"):
        if (host, suite, sf) in self.below and F.HOST_KIND[host] == "gpu":
            return None
        joules = self.w.energy(host, r, suite, sf, q, e)
        t = self.w.time(host, r, suite, sf, q, e)
        if F.HOST_KIND[host] == "gpu":
            tag = F.S1_TAG[F.HOST_SCOPE[host]]
            if above and (F.HOST_SCOPE[host] == "board" and e in F.CPU_ENGINES):
                return None
            idle = F.CONTEXT_W[host] if e in F.GPU_ENGINES else F.IDLE_GPU_W[host]
            return joules[tag] - idle * t if above else joules[tag]
        pinned = joules["S2-pinned/package"] + joules["S2-pinned/dram"]
        total = pinned + joules["S2-other"] if scope == "S2-all" else pinned
        idle = IDLE_PINNED_W + (214.0 if scope == "S2-all" else 0.0)
        return total - idle * t if above else total

    def cost(self, host, r, suite, sf, q, e, table, baseline_host=None):
        t = self.w.time(host, r, suite, sf, q, e)
        price = self.billed[host] if table == "P-BILLED" else TABLES[table][host]
        return price / 3600 * t

    def cost_b1(self, r, suite, sf, q, e, table):
        price = PBILLED["c-vm"] if table == "P-BILLED" else TABLES[table]["b1"]
        return price / 3600 * self.w.time("c-gcp", r, suite, sf, q, e)

    # -- sides of the GPU-host contrast of `metric`
    def sides(self, host, metric):
        w = self
        if metric == "time":
            return (lambda r, s, sf, q, e: w.time(host, r, s, sf, q, e)), (lambda r, s, sf, q, e: w.time(host, r, s, sf, q, e)), host
        if metric == "time_vs_cgcp":
            return (lambda r, s, sf, q, e: w.time("c-gcp", r, s, sf, q, e)), (lambda r, s, sf, q, e: w.time(host, r, s, sf, q, e)), "c-gcp"
        if metric.startswith("energy_"):
            above = "above_idle" in metric
            scope = "S2-all" if metric.endswith("_s2all") else "S2-pinned"
            return ((lambda r, s, sf, q, e: w.energy("c-gcp", r, s, sf, q, e, above, scope)),
                    (lambda r, s, sf, q, e: w.energy(host, r, s, sf, q, e, above)), "c-gcp")
        _, base, slug = metric.split("_", 2)
        table = {"p_new": "P-NEW", "p_old": "P-OLD", "p_billed": "P-BILLED"}[slug]
        own = lambda r, s, sf, q, e: w.cost(host, r, s, sf, q, e, table)                       # noqa: E731
        if base == "b1":
            return (lambda r, s, sf, q, e: w.cost_b1(r, s, sf, q, e, table)), own, "c-gcp"
        if base == "b2":
            return (lambda r, s, sf, q, e: w.cost("c-gcp", r, s, sf, q, e, table)), own, "c-gcp"
        return own, own, host

    def contrast(self, cname, host, metric, suite, sf, strict=False):
        cpu_e, gpu_e = {"x1": (("polars",), ("polars_gpu",)), "x2": (("spark_cpu",), ("spark_gpu",)), "x3": (F.CPU_ENGINES, F.GPU_ENGINES),
                        "x3_strict": (F.CPU_ENGINES, F.GPU_ENGINES)}[cname]
        ref, eng, ref_host = self.sides(host, metric)
        rounds = sorted(set(self.rounds[host]) & set(self.rounds[ref_host]))
        per_round, per_query, queries_by_round = {}, {}, {}
        for r in rounds:
            logs = []
            for q in self.queries[suite]:
                refs, engs, usable = [], [], True
                for e in cpu_e:                                         # an engine that ran the query must have a value: no other engine stands in for it
                    value = ref(r, suite, sf, q, e)
                    usable, refs = usable and value is not None, refs + ([value] if value is not None else [])
                for e in gpu_e:
                    if self.w.failure(host, r, suite, sf, q, e) is not None:     # a coverage outcome: did not run
                        continue
                    value = eng(r, suite, sf, q, e)
                    usable, engs = usable and value is not None, engs + ([value] if value is not None else [])
                if not usable or not refs or not engs or (cname == "x3_strict" and len(engs) < len(gpu_e)):
                    continue
                if min(refs) > 0 and min(engs) > 0:
                    ratio = min(refs) / min(engs)
                    per_query.setdefault(q, {})[r] = ratio
                    logs.append(math.log(ratio))
            if logs:
                per_round[r] = math.exp(sum(logs) / len(logs))
                queries_by_round[r] = len(logs)
        out = summary(per_round) if per_round else {"n": 0, "estimate": None, "ci_low": None, "ci_high": None}
        out["per_query"] = {q: summary(v) for q, v in per_query.items()}
        out["queries_by_round"] = queries_by_round
        out["class"] = classify(out["ci_low"], out["ci_high"]) if out["estimate"] is not None else "not_estimable"
        return out


# ---- the big tree ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def big(tmp_path_factory):
    root = tmp_path_factory.mktemp("p3big")
    world = F.World()
    builder = F.standard_tree(root / "tree", world)
    out = root / "out"
    stats = A.run_analysis(root / "tree", out, unblind=True, allow_synthetic=True)
    rounds = {"h100": list(range(1, 9)), "a100": [1, 2, 3, 4], "c-gcp": list(range(1, 9)), "c-vm": [1, 2]}
    oracle = Oracle(world, rounds, below={("h100", "baseline", "1")})
    tree = tree_mod.load_tree(root / "tree")
    store = cells_mod.build_cells(tree, allow_synthetic=True)
    return SimpleNamespace(root=root, tree=root / "tree", out=out, stats=stats, world=world, oracle=oracle, builder=builder, tree_obj=tree, store=store)


def cell_of(stats, *path):
    node = stats
    for key in path:
        node = node[key]
    return node


# ---- primitives: the interval, the classes, the break-even rule, the tables ---------------------------------------------------------------

def test_the_t_critical_values_are_the_tables_values():
    assert E.t_critical(7) == pytest.approx(T95[7], rel=1e-6)           # R = 8
    assert E.t_critical(3) == pytest.approx(T95[3], rel=1e-6)           # R = 4
    assert E.t_critical(1) == pytest.approx(T95[1], rel=1e-6)
    assert E.t_critical(2) == pytest.approx(T95[2], rel=1e-6)


@pytest.mark.parametrize("ratios", [{1: 1.9, 2: 2.1, 3: 2.0, 4: 2.2, 5: 1.8, 6: 2.0, 7: 2.1, 8: 1.9}, {1: 0.5, 2: 0.55, 3: 0.6, 4: 0.52}, {4: 3.0}])
def test_the_ratio_record_is_the_geometric_mean_with_a_t_interval_on_the_log_ratios(ratios):
    expected, got = summary(ratios), E.ratio_record(ratios)
    assert got["estimate"] == pytest.approx(expected["estimate"], rel=1e-9)
    assert got["n_rounds"] == expected["n"] and got["df"] == expected["n"] - 1
    if expected["n"] > 1:
        assert got["ci_low"] == pytest.approx(expected["ci_low"], rel=1e-6) and got["ci_high"] == pytest.approx(expected["ci_high"], rel=1e-6)
        paper1 = AC.ratio_summary([ratios[r] for r in sorted(ratios)], sorted(ratios))
        assert (got["estimate"], got["ci_low"], got["ci_high"]) == (paper1["estimate"], paper1["ci_low"], paper1["ci_high"])
    else:
        assert got["ci_low"] is None and got["ci_high"] is None


@pytest.mark.parametrize("low, high, expected, margin", [
    (1.01, 1.30, "gpu_better", False), (1.0000001, 1.2, "gpu_better", False), (1.0, 1.2, "inconclusive", False),      # merely containing 1 is never a difference
    (0.70, 0.95, "cpu_better", False), (0.5, 0.9999, "cpu_better", False),
    (0.95, 1.05, "no_difference", True), (1 / 1.10, 1.10, "no_difference", True),                                      # the margin is inclusive
    (0.90, 1.05, "inconclusive", False), (0.95, 1.1001, "inconclusive", False), (0.8, 1.2, "inconclusive", False),
    (1.02, 1.05, "gpu_better", True),                                                                                  # the order of 5.3: a difference first, the margin is reported beside it
    (0.93, 0.97, "cpu_better", True)])
def test_the_cell_classes_follow_the_interval_and_the_ten_percent_margin(low, high, expected, margin):
    record = {"estimate": math.sqrt(low * high), "ci_low": low, "ci_high": high}
    assert E.classify(record) == expected
    assert E.within_margin(record) is margin


def test_a_cell_without_an_interval_is_inconclusive_and_one_without_an_estimate_is_not_estimable():
    assert E.classify({"estimate": 1.2, "ci_low": None, "ci_high": None}) == "inconclusive"
    assert E.classify({"estimate": None, "ci_low": None, "ci_high": None}) == "not_estimable"


@pytest.mark.parametrize("classes, expected", [
    ({"1": "cpu_better", "10": "cpu_better", "30": "gpu_better", "100": "gpu_better", "300": "gpu_better"}, ("bracket", "(SF10, SF30]")),
    ({"1": "gpu_better", "10": "gpu_better", "30": "gpu_better", "100": "gpu_better", "300": "gpu_better"}, ("at_most_bottom", "<= SF1")),
    ({"1": "gpu_better", "10": "gpu_better", "30": "gpu_better", "100": "gpu_better", "300": "inconclusive"}, ("greater_than_top", "> SF300")),
    ({"1": "gpu_better", "10": "cpu_better", "30": "gpu_better", "100": "gpu_better", "300": "gpu_better"}, ("bracket", "(SF10, SF30]")),     # the run must be unbroken
    ({"1": None, "10": "gpu_better", "30": "gpu_better", "100": "gpu_better", "300": "gpu_better"}, ("at_most_unmeasured", "<= SF10 (SF1 not estimable)")),   # not measured is not "not GPU better"
    ({"1": "gpu_better", "10": "gpu_better", "30": "gpu_better", "100": "gpu_better", "300": None}, ("not_determinable", "not determinable (SF300 not estimable)")),   # never "> SF300"
    ({"1": "cpu_better", "10": "cpu_better", "30": "gpu_better", "100": None, "300": "gpu_better"}, ("at_most_unmeasured", "<= SF300 (SF100 not estimable)")),
    ({"1": "gpu_better", "10": "gpu_better", "30": "gpu_better", "100": "gpu_better", "300": "not_estimable"}, ("not_determinable", "not determinable (SF300 not estimable)")),
    ({"1": "cpu_better", "10": "cpu_better", "30": "no_difference", "100": "gpu_better", "300": "gpu_better"}, ("bracket", "(SF30, SF100]")),
    ({"1": "cpu_better", "10": "cpu_better", "30": "cpu_better", "100": "cpu_better", "300": "cpu_better"}, ("greater_than_top", "> SF300"))])
def test_the_break_even_is_the_smallest_scale_from_which_every_larger_scale_is_gpu_better(classes, expected):
    result = E.break_even(classes, ["1", "10", "30", "100", "300"])
    assert (result["outcome"], result["text"]) == expected
    assert result["censored"] is (expected[0] != "bracket")


def test_the_complex_suite_grid_starts_at_sf30():
    result = E.break_even({"30": "gpu_better", "100": "gpu_better", "300": "gpu_better"}, ["30", "100", "300"])
    assert result["text"] == "<= SF30" and result["censored"]


def test_the_price_tables_are_the_planners_p_new_and_the_design_s_p_old():
    import p3_plan
    for plan in p3_plan.PLANS.values():
        assert K.P_NEW[plan.instance_type] == plan.price
    assert K.P_NEW["c3-highmem-44"] == 2.910776 and K.B1_INSTANCE["c3-highmem-192-metal"] == "c3-highmem-44" and K.B1_INSTANCE["m7i.metal-24xl"] == "r7i.8xlarge"
    assert {k: (K.P_OLD[k], K.P_NEW[k]) for k in K.P_NEW if K.P_OLD[k] != K.P_NEW[k]} == {
        "gpu_1x_h100_sxm5": (4.29, 4.89), "gpu_1x_gh200": (2.29, 2.59), "gpu_1x_b200_sxm6": (6.99, 7.99)}       # 2.6: the three Lambda prices that rose; A100 unchanged at 1.99


def test_the_welch_record_is_the_ratio_of_geometric_means_with_welch_satterthwaite_df():
    host, ref = {1: 2.0, 2: 2.2, 3: 1.9, 4: 2.1}, {r: 1.0 + 0.02 * r for r in range(1, 9)}
    got = E.welch_record(host, ref)
    la, lb = [math.log(v) for v in host.values()], [math.log(v) for v in ref.values()]
    ma, mb = sum(la) / 4, sum(lb) / 8
    va = sum((x - ma) ** 2 for x in la) / 3 / 4
    vb = sum((x - mb) ** 2 for x in lb) / 7 / 8
    assert got["estimate"] == pytest.approx(math.exp(ma - mb), rel=1e-12)
    assert got["df"] == pytest.approx((va + vb) ** 2 / (va ** 2 / 3 + vb ** 2 / 7), rel=1e-9)
    assert got["log_se"] == pytest.approx(math.sqrt(va + vb), rel=1e-12)


# ---- the reader: attempts, squares, launches ---------------------------------------------------------------------------------------------

def test_the_accepted_attempt_is_the_passing_repeat_and_the_failed_attempt_is_never_pooled(big):
    instance = next(i for i in big.tree_obj.instances if i.label == "h100-l1")
    assert instance.accepted[2].path.name == "round-2-repeat-1" and [a.verdict for a in instance.attempts[2]] == ["fail", "pass"]
    cells = [c for c in big.store.cells if c.host == "h100" and c.round == 2 and c.time_s is not None]
    assert cells                                                         # the failed attempt was written with every value x1000: a leak would show here
    assert all(c.time_s == pytest.approx(big.world.time("h100", 2, c.suite, c.scale, c.query, c.engine), rel=1e-12) for c in cells)


def test_two_complete_squares_on_two_launches_give_eight_confirmatory_rounds_one_gives_four(big):
    hosts = big.stats["hosts"]
    assert hosts["h100"]["confirmatory_rounds"] == list(range(1, 9)) and hosts["h100"]["status"] == "measured"
    assert hosts["a100"]["confirmatory_rounds"] == [1, 2, 3, 4] and hosts["a100"]["squares"]["2"]["complete"] is False
    assert big.stats["x3"]["h100"]["baseline"]["sf30"]["time"]["df"] == 7 and big.stats["x3"]["a100"]["baseline"]["sf30"]["time"]["df"] == 3
    assert hosts["c-vm"]["confirmatory_rounds"] == [1, 2]


def test_an_incomplete_square_enters_no_confirmatory_estimate_and_is_a_sensitivity(tmp_path):
    world = F.World()
    builder = F.TreeBuilder(tmp_path / "tree", world.truth(), grid={"baseline": ("30", "300"), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    builder.add_launch("h100", 2, [5, 6, 7])                                    # the second square stops after three rounds
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    builder.add_launch("c-gcp", 2, [5, 6, 7, 8])
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    host = stats["hosts"]["h100"]
    assert host["confirmatory_rounds"] == [1, 2, 3, 4] and host["sensitivity_rounds"] == [5, 6, 7] and host["sensitivity_by_instance"] == {"h100-l2": [5, 6, 7]}
    main = stats["x3"]["h100"]["baseline"]["sf30"]["time"]
    assert main["rounds"] == [1, 2, 3, 4] and main["df"] == 3 and main["basis"] == "confirmatory"
    sens = stats["sensitivity"]["incomplete:h100-l2"]["x3"]["h100"]["baseline"]["sf30"]["time"]
    assert sens["rounds"] == [5, 6, 7] and sens["basis"] == "sensitivity_incomplete_squares" and sens["df"] == 2
    expected = Oracle(world, {"h100": [5, 6, 7], "c-gcp": list(range(1, 9))}).contrast("x3", "h100", "time", "baseline", "30")
    assert sens["estimate"] == pytest.approx(expected["estimate"], rel=1e-9)


def test_a_host_with_no_complete_square_is_reported_descriptively(tmp_path):
    world = F.World()
    builder = F.TreeBuilder(tmp_path / "tree", world.truth(), grid={"baseline": ("30", "300"), "complex": ("30",)})
    builder.add_launch("a100", 1, [1, 2, 3])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    assert stats["hosts"]["a100"]["status"] == "descriptive_no_complete_square" and stats["hosts"]["a100"]["confirmatory_rounds"] == []
    cell = stats["x3"]["a100"]["baseline"]["sf30"]["time"]
    assert cell["basis"] == "descriptive_no_complete_square" and cell["rounds"] == [1, 2, 3]
    assert cell["estimate"] == pytest.approx(Oracle(world, {"a100": [1, 2, 3], "c-gcp": [1, 2, 3, 4]}).contrast("x3", "a100", "time", "baseline", "30")["estimate"], rel=1e-9)


def test_a_relaunch_after_a_loss_is_never_pooled_with_the_lost_instance(tmp_path):
    world = F.World()
    builder = F.TreeBuilder(tmp_path / "tree", world.truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2], created="2026-11-24T00:00:00+00:00", launch_name="launch1")                          # lost after two rounds
    builder.add_launch("h100", 1, [1, 2, 3, 4], created="2026-11-24T12:00:00+00:00", launch_name="launch1-relaunch")
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    host = stats["hosts"]["h100"]
    labels = [i["label"] for i in host["instances"]]
    assert labels == ["h100-l1", "h100-l1r2"]
    assert host["squares"]["1"] == {"complete": True, "instance": "h100-l1r2", "rounds": [1, 2, 3, 4]}
    assert host["confirmatory_rounds"] == [1, 2, 3, 4] and host["sensitivity_rounds"] == [1, 2]
    assert stats["x3"]["h100"]["baseline"]["sf30"]["time"]["n_rounds"] == 4


def test_two_instances_that_complete_the_same_square_are_refused(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4], launch_name="launch1")
    builder.add_launch("h100", 1, [1, 2, 3, 4], created="2026-11-24T12:00:00+00:00", launch_name="launch1b")
    with pytest.raises(tree_mod.AnalysisError, match="complete in more than one instance"):
        tree_mod.load_tree(tmp_path / "tree")


def test_launches_of_different_designs_are_refused(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    builder.add_launch("h100", 2, [5, 6, 7, 8], common="0" * 64)
    with pytest.raises(tree_mod.AnalysisError, match="common_sha256"):
        tree_mod.load_tree(tmp_path / "tree")
    assert any("common_sha256" in w for w in tree_mod.load_tree(tmp_path / "tree", allow_design_mismatch=True).warnings)


def test_status_json_is_cross_checked_against_the_accepted_attempts_on_disk(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4], status_override={"confirmatory_rounds": [1, 2, 3]})
    with pytest.raises(tree_mod.AnalysisError, match="status.json lists confirmatory rounds"):
        tree_mod.load_tree(tmp_path / "tree")
    tree = tree_mod.load_tree(tmp_path / "tree", allow_status_mismatch=True)
    assert tree.hosts["h100"].confirmatory_rounds == [1, 2, 3, 4] and tree.warnings


def test_a_paper_1_root_is_never_analysed_and_is_skipped_when_it_sits_beside_paper_3_roots(tmp_path):
    root = tmp_path / "tree" / "campaign-h100"
    root.mkdir(parents=True)
    (root / "campaign.json").write_text(json.dumps({"design": {"format": 1, "seed": 20261029}}))
    with pytest.raises(tree_mod.AnalysisError, match="no Paper 3 campaign.json"):
        tree_mod.load_tree(tmp_path / "tree")
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    tree = tree_mod.load_tree(tmp_path / "tree")
    assert sorted(tree.hosts) == ["h100"] and any("not a Paper 3 campaign" in w for w in tree.warnings)


def test_the_sealed_bring_up_preflight_is_never_read(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    sealed = tmp_path / "tree" / "h100" / "launch1" / "rerun" / "preflight-bringup"
    sealed.mkdir(parents=True)
    (sealed / "campaign.json").write_text("not json: the sealed preflight output")
    assert [p.name for p in tree_mod.walk_for_campaigns(tmp_path / "tree")] == ["paper3-h100-l1"]


# ---- the guards ------------------------------------------------------------------------------------------------------------------------------

def test_the_full_analysis_refuses_without_unblind(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    with pytest.raises(tree_mod.AnalysisError, match="design 7.3"):
        A.run_analysis(tmp_path / "tree", tmp_path / "out", allow_synthetic=True)
    assert A.main(["--tree", str(tmp_path / "tree"), "--output", str(tmp_path / "o2"), "--allow-synthetic"]) == 2


@pytest.mark.parametrize("state", ["running", "starting", "paused", "interrupted"])
def test_a_launch_that_is_not_complete_or_stopped_by_rule_blocks_the_analysis_and_unblind_does_not_override_it(tmp_path, state):
    """run_campaign.py writes "paused" between rounds (--stop-after-round): the copy the driver takes after each round says so."""
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2], state=state)
    builder.add_launch("c-gcp", 1, [1, 2], state=state)
    with pytest.raises(tree_mod.AnalysisError, match=f"h100-l1: status.json state is '{state}'"):
        A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    assert not (tmp_path / "out").exists()                           # nothing was computed, nothing was written
    assert A.main(["--tree", str(tmp_path / "tree"), "--output", str(tmp_path / "o2"), "--unblind", "--allow-synthetic"]) == 2


@pytest.mark.parametrize("state", ["complete", "stopped"])
def test_a_complete_or_stopped_by_rule_launch_does_not_block(tmp_path, state):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4], state=state)
    assert A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)["_meta"]["declared_ended"] == []


def test_planned_launches_that_are_not_in_the_tree_block_until_they_are_declared_ended(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    plan = {"h100": ("1", "2"), "c-gcp": ("1",), "a100": ("1", "2")}
    with pytest.raises(tree_mod.AnalysisError) as blocked:
        A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True, expected_launches_override=plan)
    for label in ("h100-l2", "a100-l1", "a100-l2"):
        assert f"{label}: planned launch is not in the tree" in str(blocked.value)
    with pytest.raises(tree_mod.AnalysisError, match="a100-l2: planned launch"):                                 # a partial declaration is not enough
        A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True, expected_launches_override=plan, declare_ended=["h100-l2", "a100-l1"])
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True, expected_launches_override=plan,
                           declare_ended=["h100-l2", "a100-l1", "a100-l2"])
    assert stats["_meta"]["declared_ended"] == ["a100-l1", "a100-l2", "h100-l2"] and len(stats["_meta"]["declared_ended_problems"]) == 3
    with pytest.raises(tree_mod.AnalysisError, match="names nothing"):
        A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True, declare_ended=["h100-l9"])


def test_a_launch_replaced_by_a_relaunch_is_judged_by_its_last_instance(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2], state="running", created="2026-11-24T00:00:00+00:00", launch_name="launch1")           # lost: its status stays "running"
    builder.add_launch("h100", 1, [1, 2, 3, 4], created="2026-11-24T12:00:00+00:00", launch_name="launch1-relaunch")
    assert A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)["hosts"]["h100"]["squares"]["1"]["instance"] == "h100-l1r2"


def test_the_outputs_are_never_written_inside_the_tree_they_were_computed_from(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    for call in (lambda out: A.run_analysis(tmp_path / "tree", out, unblind=True, allow_synthetic=True), lambda out: A.run_qa(tmp_path / "tree", out)):
        with pytest.raises(tree_mod.AnalysisError, match="inside --tree"):
            call(tmp_path / "tree" / "analysis")


def test_an_informal_campaign_is_refused_unless_a_test_tree_is_allowed(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    with pytest.raises(tree_mod.AnalysisError, match="not a formal campaign"):
        A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True)


def test_the_smoke_tests_synthetic_data_never_enter_an_analysis(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    root = builder.add_launch("h100", 1, [1, 2, 3, 4])
    path = root / "round-1" / "baseline" / "sf30" / "polars" / "results.json"
    data = json.loads(path.read_text())
    data["synthetic_smoke"] = True
    path.write_text(json.dumps(data))
    with pytest.raises(tree_mod.AnalysisError, match="synthetic_smoke"):
        A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)


# ---- cells: what a session's records become -------------------------------------------------------------------------------------------------

def find(store, host, r, suite, scale, query, engine):
    return next(c for c in store.cells if (c.host, c.round, c.suite, c.scale, c.query, c.engine) == (host, r, suite, scale, query, engine))


def small_tree(tmp_path, world=None, hosts=("h100", "c-gcp"), rounds=(1, 2, 3, 4), grid=None, hooks=None, **launch):
    """A one-square tree of the given hosts on a reduced grid; returns (builder, root)."""
    builder = F.TreeBuilder(tmp_path / "tree", (world or F.World()).truth(), grid=grid or {"baseline": ("30", "300"), "complex": ("30",)})
    for host, hook in (hooks or {}).items():
        for r in rounds:
            builder.gate_hooks[(host, r)] = hook
    for host in hosts:
        builder.add_launch(host, 1, list(rounds), **launch)
    return builder, tmp_path / "tree"


def load_small(root):
    tree = tree_mod.load_tree(root)
    return tree, cells_mod.build_cells(tree, allow_synthetic=True)


def test_a_gpu_engine_cell_has_its_median_time_total_energy_and_above_idle_energy(big):
    w = big.world
    cell = find(big.store, "h100", 3, "baseline", "30", "q03", "polars_gpu")
    t = w.time("h100", 3, "baseline", "30", "q03", "polars_gpu")
    e = w.energy("h100", 3, "baseline", "30", "q03", "polars_gpu")["S1-counter"]
    assert cell.time_s == pytest.approx(t, rel=1e-12)                       # the median of trial times 1.05 t, 0.97 t, t
    assert cell.energy["S1-counter"].tag == "S1-counter"
    assert cell.energy["S1-counter"].total_j == pytest.approx(e, rel=1e-6)
    assert cell.energy["S1-counter"].idle_w == pytest.approx(142.0, rel=1e-6)          # mean of the two idle windows (0.98 and 1.02 of P_idle)
    assert cell.energy["S1-counter"].above_j == pytest.approx(e - 142.0 * t, rel=1e-6)


def test_a_cpu_engine_on_an_x86_gpu_host_carries_the_idle_gpu_s1_energy_and_no_above_idle(big):
    cell = find(big.store, "h100", 3, "baseline", "30", "q03", "polars")
    t = big.world.time("h100", 3, "baseline", "30", "q03", "polars")
    assert cell.energy["S1-counter"].total_j == pytest.approx(76.0 * t, rel=1e-6)
    assert cell.energy["S1-counter"].above_j is None and cell.energy["S1-counter"].above_reason == "not_reported_idle_gpu"
    assert cell.energy["S1-counter"].idle_w == pytest.approx(76.0, rel=1e-6)


def test_cpu_energy_is_the_series_interpolated_at_the_run_stamps_in_every_scope(big):
    cell = find(big.store, "c-gcp", 5, "baseline", "100", "q01", "spark_cpu")
    t = big.world.time("c-gcp", 5, "baseline", "100", "q01", "spark_cpu")
    e = cell.energy
    assert e["S2-pinned"].total_j == pytest.approx((330.0 + 40.0) * t, rel=1e-9)
    assert e["S2-pinned/package"].total_j == pytest.approx(330.0 * t, rel=1e-9) and e["S2-pinned/dram"].total_j == pytest.approx(40.0 * t, rel=1e-9)
    assert e["S2-all"].total_j == pytest.approx((330.0 + 40.0 + 214.0) * t, rel=1e-9)
    assert e["S2-pinned"].tag == e["S2-pinned/package"].tag == "S2-pinned" and e["S2-all"].tag == "S2-all"
    assert e["S2-pinned"].idle_w == pytest.approx(216.0, rel=1e-9) and e["S2-all"].idle_w == pytest.approx(430.0, rel=1e-9)
    assert e["S2-pinned"].above_j == pytest.approx(154.0 * t, rel=1e-9) and e["S2-all"].above_j == pytest.approx((370.0 - 216.0) * t, rel=1e-9)
    assert not any(k.startswith("S1") for k in e)                              # no GPU counter on the CPU-only host


def test_rapl_interpolation_between_samples_is_linear_in_the_series(tmp_path):
    """Samples every 0.2 s on a grid that does not hit the stamps: constant power inside a run gives the exact energy (5.2 uses the shared function)."""
    import power_telemetry as pt
    series = {"z": {"domain": "package-0", "mono": [int(i * 2e8) for i in range(0, 200)], "uj": [100.0 * i * 2e8 / 1e3 for i in range(0, 200)], "flags": [""] * 200}}
    start, end = int(3.07e9), int(7.31e9)
    joules = cells_mod.rapl_joules(series, ["z"], start, end)
    assert joules == pytest.approx(100.0 * (end - start) / 1e9, rel=1e-12)
    assert joules == pt.s2_pinned_energy_j(series, ["z"], start, end)["joules"]


def test_energy_below_resolution_keeps_time_and_drops_the_energy(big):
    cell = find(big.store, "h100", 4, "baseline", "1", "q01", "polars_gpu")
    assert cell.time_s == pytest.approx(big.world.time("h100", 4, "baseline", "1", "q01", "polars_gpu"), rel=1e-12)
    assert cell.energy["S1-counter"].total_j is None and cell.energy["S1-counter"].total_reason == "below_resolution"
    assert cell.energy["S1-counter"].above_j is None and cell.energy["S1-counter"].above_reason == "below_resolution"
    assert find(big.store, "h100", 4, "baseline", "10", "q01", "polars_gpu").energy["S1-counter"].total_j is not None


def test_a_coverage_outcome_is_a_cell_without_a_time(big):
    cell = find(big.store, "h100", 2, "complex", "100", "cx_q13", "polars_gpu")
    assert cell.coverage == "unsupported_operation" and cell.time_s is None and cell.time_reason == "coverage:unsupported_operation"
    assert find(big.store, "h100", 2, "complex", "100", "cx_q13", "spark_gpu").time_s is not None


def test_a_window_rule_violation_makes_the_whole_cell_unusable_and_g10_drops_above_idle_only_for_its_scope(tmp_path):
    def hook(kind, key, default):
        if kind == "cell" and key == ("baseline", "30", "polars_gpu", "q01"):
            return {**default, "outcome": "fail", "reasons": ["window_rule_violation"], "violations": ["x"]}
        if kind == "session" and key == ("baseline", "300", "spark_gpu", "G10"):
            return {"outcome": "fail", "problems": ["x"], "relative_difference": {"S1-counter": 0.2}, "unreliable_scopes": ["S1-counter"]}
        if kind == "session" and key == ("baseline", "300", "polars_gpu", "G6"):
            return {"outcome": "fail", "problems": ["x"]}
        return default
    _, root = small_tree(tmp_path, hosts=("h100",), hooks={"h100": hook}, rounds=(1, 2, 3, 4))
    tree, store = load_small(root)
    assert find(store, "h100", 1, "baseline", "30", "q01", "polars_gpu").time_s is None
    assert find(store, "h100", 1, "baseline", "30", "q01", "polars_gpu").time_reason == "window_rule_violation"
    unreliable = find(store, "h100", 1, "baseline", "300", "q01", "spark_gpu").energy["S1-counter"]
    assert unreliable.total_j is not None and unreliable.above_j is None and unreliable.above_reason == "above_idle_unreliable"      # total is unaffected (4)
    invalid = find(store, "h100", 1, "baseline", "300", "q01", "polars_gpu")
    assert invalid.time_s is not None and invalid.energy["S1-counter"].total_j is None and invalid.energy["S1-counter"].total_reason == "g6_session_invalid"


def edit_results(root, host, r, suite, scale, engine, change):
    path = root / host / "launch1" / "rerun" / f"paper3-{host}-l1" / f"round-{r}" / suite / f"sf{scale}" / engine / "results.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


@pytest.mark.parametrize("change, message", [
    (lambda b: b["gpu_energy"].pop("scope_tag"), "without a valid scope tag"),
    (lambda b: b["gpu_energy"].update(scope_tag="S9-guess"), "without a valid scope tag"),
    (lambda b: b["gpu_energy"].update(scope_tag="S1-module"), "declared scope gives"),
    (lambda b: b["gpu_energy"].update(scope_tag="none"), "tagged 'none'"),
    (lambda b: b.pop("gpu_energy"), "no gpu_energy block")])
def test_an_untagged_or_wrongly_tagged_energy_value_raises(tmp_path, change, message):
    _, root = small_tree(tmp_path, hosts=("h100",))
    edit_results(root, "h100", 1, "baseline", "30", "polars_gpu", lambda data: [change(r["energy_window"]) for r in data["records"] if r["phase"] == "measured"][0:1])
    with pytest.raises(cells_mod.UntaggedEnergyError, match=message):
        load_small(root)


def test_a_missing_counter_value_is_a_missing_energy_not_a_number_and_time_survives(tmp_path):
    def blank(data):
        for r in data["records"]:
            if r["phase"] == "measured" and r["query"] == "q01":
                r["energy_window"]["gpu_energy"].update(scope_tag="none", per_run_delta_mj=None, window_delta_mj=None)
    _, root = small_tree(tmp_path, hosts=("h100",))
    edit_results(root, "h100", 1, "baseline", "30", "polars_gpu", blank)
    tree, store = load_small(root)
    cell = find(store, "h100", 1, "baseline", "30", "q01", "polars_gpu")
    assert cell.time_s is not None and cell.energy["S1-counter"].total_j is None and cell.energy["S1-counter"].total_reason == "no_counter_value"
    assert find(store, "h100", 1, "baseline", "30", "q03", "polars_gpu").energy["S1-counter"].total_j is not None


def test_a_negative_above_idle_value_is_kept_not_clamped_and_has_no_ratio(tmp_path):
    t = F.World().time("h100", 1, "baseline", "30", "q01", "polars_gpu")
    world = F.World(energy_overrides={("h100", 1, "baseline", "30", "q01", "polars_gpu"): {"S1-counter": 100.0 * t}})            # the idle floor is 142 W
    _, root = small_tree(tmp_path, world=world)
    tree, store = load_small(root)
    cell = find(store, "h100", 1, "baseline", "30", "q01", "polars_gpu")
    assert cell.energy["S1-counter"].above_j == pytest.approx(-42.0 * t, rel=1e-6)
    stats = A.run_analysis(root, tmp_path / "out", unblind=True, allow_synthetic=True)
    record = stats["x1"]["h100"]["baseline"]["sf30"]["energy_above_idle"]
    assert record["n_excluded_nonpositive"] >= 1 and record["q"]["q01"]["n_rounds"] == 3 and record["q"]["q03"]["n_rounds"] == 4
    assert stats["x1"]["h100"]["baseline"]["sf30"]["energy_total"]["q"]["q01"]["n_rounds"] == 4


def test_the_idle_windows_are_the_two_engine_ready_windows_of_the_session(big):
    cell = find(big.store, "h100", 1, "baseline", "300", "q01", "spark_gpu")
    assert cell.energy["S1-counter"].idle_w == pytest.approx(142.0, rel=1e-9)      # (0.98 + 1.02) / 2 of the context-open floor
    assert big.store.idle[("h100", "polars", "S1-counter")][0][2] == pytest.approx(76.0, rel=1e-6)
    assert big.store.idle[("c-gcp", "polars", "S2-all")][0][2] == pytest.approx(430.0, rel=1e-9)


def test_a_cpu_only_host_whose_pin_cannot_be_attributed_has_no_s2_pinned_series(tmp_path):
    _, root = small_tree(tmp_path, hosts=("c-gcp",))
    campaign_root = tmp_path / "tree" / "c-gcp" / "launch1" / "rerun" / "paper3-c-gcp-l1"
    for r in (1, 2, 3, 4):
        for suite in ("baseline", "complex"):
            path = campaign_root / f"round-{r}" / suite / "power_sources.json"
            data = json.loads(path.read_text())
            data["pinning"]["attribution"]["possible"] = False
            path.write_text(json.dumps(data))
    tree, store = load_small(root)
    series = set(find(store, "c-gcp", 1, "baseline", "30", "q01", "polars").energy)
    assert series == {"S2-all"}                                                  # the pinned scope is absent, never guessed


# ---- the estimands against the oracle -----------------------------------------------------------------------------------------------------

METRICS = ["time", "time_vs_cgcp", "energy_total", "energy_above_idle", "energy_total_s2all", "energy_above_idle_s2all",
           *[f"cost_{b}_{t}" for t in ("p_new", "p_old", "p_billed") for b in ("b1", "b2", "b3")]]
SUITE_SCALES = [(s, sf) for s, scales in F.GRID.items() for sf in scales]


def n_stable(expected, rounds):
    return sum(1 for q, v in expected["per_query"].items() if v["n"] == len(rounds))


@pytest.mark.parametrize("cname", ["x1", "x2", "x3", "x3_strict"])
@pytest.mark.parametrize("metric", METRICS)
def test_every_suite_cell_of_x1_x2_x3_matches_the_oracle(big, cname, metric):
    rel = 1e-9 if metric.startswith(("time", "cost")) else 1e-5
    for host in ("h100", "a100"):
        for suite, sf in SUITE_SCALES:
            got = big.stats[cname][host][suite][f"sf{sf}"][metric]
            want = big.oracle.contrast(cname, host, metric, suite, sf, strict=cname == "x3_strict")
            where = (cname, host, suite, sf, metric)
            assert got["class"] == want["class"], where
            if want["estimate"] is None:
                assert got["estimate"] is None and got["n_rounds"] == 0, where
                continue
            assert got["estimate"] == pytest.approx(want["estimate"], rel=rel), where
            assert got["n_rounds"] == want["n"] and got["df"] == want["n"] - 1, where
            assert got["ci_low"] == pytest.approx(want["ci_low"], rel=rel * 10) and got["ci_high"] == pytest.approx(want["ci_high"], rel=rel * 10), where
            assert got["n_queries"] == n_stable(want, got["rounds"]), where
            for q, per in want["per_query"].items():
                assert got["q"][q]["estimate"] == pytest.approx(per["estimate"], rel=rel) and got["q"][q]["n_rounds"] == per["n"], where + (q,)


def test_the_oracle_exercises_every_class_and_the_rounds_that_the_squares_give(big):
    classes = {big.stats[c][h][s][f"sf{sf}"][m]["class"] for c in ("x1", "x2", "x3") for h in ("h100", "a100") for s, sf in SUITE_SCALES for m in METRICS}
    assert {"gpu_better", "cpu_better", "not_estimable"} <= classes
    assert big.stats["x3"]["h100"]["baseline"]["sf30"]["time"]["rounds"] == list(range(1, 9))
    assert big.stats["x3"]["a100"]["baseline"]["sf30"]["energy_total"]["rounds"] == [1, 2, 3, 4]          # the rounds both hosts have: a100 has one square


def test_the_cross_class_energy_cell_is_scope_tagged_and_worded_under_n1(big):
    record = big.stats["x3"]["h100"]["baseline"]["sf100"]["energy_total"]
    assert (record["ref_scope"], record["eng_scope"], record["ref_host"], record["cross_class"], record["mixed_scope"]) == ("S2-pinned", "S1-counter", "c-gcp", True, False)
    assert record["class"] == "gpu_better" and "no node-level claim is made" in record["n1"] and "lower bound" in record["n1"]
    time = big.stats["x3"]["h100"]["baseline"]["sf100"]["time"]
    assert time["ref_scope"] is None and time["n1"] is None and time["cross_class"] is False
    cpu_better = big.stats["x3"]["a100"]["baseline"]["sf10"]["energy_total"]
    assert cpu_better["class"] == "cpu_better" and "uses more node energy" in cpu_better["n1"] and "under N1" in cpu_better["n1"]


def test_the_best_engine_is_chosen_per_query_and_round_and_the_strict_variant_needs_both_gpu_engines(big):
    nonstrict, strict = big.stats["x3"]["h100"]["complex"]["sf100"]["time"], big.stats["x3_strict"]["h100"]["complex"]["sf100"]["time"]
    assert "cx_q13" in nonstrict["queries"] and "cx_q13" not in strict["queries"]            # polars_gpu cannot run it; spark_gpu can
    assert big.stats["x1"]["h100"]["complex"]["sf100"]["time"]["queries"] == ["cx_q09"] and big.stats["x2"]["h100"]["complex"]["sf100"]["time"]["queries"] == ["cx_q09", "cx_q13"]
    w = big.world
    best_cpu = min(w.time("h100", 1, "complex", "100", "cx_q13", e) for e in F.CPU_ENGINES)
    assert nonstrict["q"]["cx_q13"]["per_round"][0] == pytest.approx(best_cpu / w.time("h100", 1, "complex", "100", "cx_q13", "spark_gpu"), rel=1e-9)


def test_cost_is_price_times_time_under_each_table_and_baseline(big):
    w = big.world
    # B1 under P-NEW, P-OLD and P-BILLED (the C-VM's billed price) of the H100's X1 at baseline SF100 / q03 in round 3
    for table, p_gpu, p_b1 in (("p_new", 4.89, 2.910776), ("p_old", 4.29, 2.910776), ("p_billed", 4.95, 3.05)):
        got = big.stats["x1"]["h100"]["baseline"]["sf100"][f"cost_b1_{table}"]["q"]["q03"]["per_round"][2]
        expected = (p_b1 * w.time("c-gcp", 3, "baseline", "100", "q03", "polars")) / (p_gpu * w.time("h100", 3, "baseline", "100", "q03", "polars_gpu"))
        assert got == pytest.approx(expected, rel=1e-9), table
    got = big.stats["x1"]["h100"]["baseline"]["sf100"]["cost_b2_p_new"]["q"]["q03"]["per_round"][2]
    assert got == pytest.approx(12.701568 * w.time("c-gcp", 3, "baseline", "100", "q03", "polars") / (4.89 * w.time("h100", 3, "baseline", "100", "q03", "polars_gpu")), rel=1e-9)
    b3 = big.stats["x1"]["h100"]["baseline"]["sf100"]["cost_b3_p_new"]
    time = big.stats["x1"]["h100"]["baseline"]["sf100"]["time"]
    assert b3["estimate"] == pytest.approx(time["estimate"], rel=1e-12)             # one price on both sides: B3 is the time ratio
    # factorised reporting: the GPU host is cheaper iff p_gpu / p_cpu < t_cpu / t_gpu
    b1 = big.stats["x1"]["h100"]["baseline"]["sf100"]["cost_b1_p_new"]
    cgcp_time = big.stats["x1"]["h100"]["baseline"]["sf100"]["time_vs_cgcp"]
    assert b1["estimate"] == pytest.approx(cgcp_time["estimate"] * 2.910776 / 4.89, rel=1e-9)


def test_the_factorised_price_ratios_sit_beside_the_time_ratios(big):
    ratios = big.stats["price_ratios"]["h100"]
    assert ratios["p_new"]["b1"]["p_gpu_over_p_cpu"] == pytest.approx(4.89 / 2.910776) and ratios["p_new"]["b2"]["p_gpu_over_p_cpu"] == pytest.approx(4.89 / 12.701568)
    assert ratios["p_old"]["b1"]["p_gpu_over_p_cpu"] == pytest.approx(4.29 / 2.910776) and ratios["p_billed"]["b1"]["p_gpu_over_p_cpu"] == pytest.approx(4.95 / 3.05)
    assert ratios["p_billed"]["b2"]["p_gpu"] == 4.95 and ratios["p_billed"]["b2"]["p_cpu"] == 12.9
    # the GPU host is cheaper iff p_gpu / p_cpu < t_cpu / t_gpu: the two sides agree with the B1 class of the contrast (one engine, one scale)
    cell = big.stats["x1"]["h100"]["baseline"]["sf100"]
    assert (ratios["p_new"]["b1"]["p_gpu_over_p_cpu"] < cell["time_vs_cgcp"]["estimate"]) == (cell["cost_b1_p_new"]["estimate"] > 1)


def test_p_billed_is_the_price_of_the_launch_that_ran_the_round(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4], price=5.00)
    builder.add_launch("h100", 2, [5, 6, 7, 8], price=4.00)
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4], price=12.7)
    builder.add_launch("c-gcp", 2, [5, 6, 7, 8], price=12.7)
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    per_round = stats["x1"]["h100"]["baseline"]["sf30"]["cost_b2_p_billed"]["q"]["q01"]["per_round"]
    w = F.World()
    for r, price in ((1, 5.00), (5, 4.00)):
        expected = 12.7 * w.time("c-gcp", r, "baseline", "30", "q01", "polars") / (price * w.time("h100", r, "baseline", "30", "q01", "polars_gpu"))
        assert per_round[[1, 2, 3, 4, 5, 6, 7, 8].index(r)] == pytest.approx(expected, rel=1e-9)


def test_x4_is_the_welch_ratio_of_each_gpu_engine_on_a_host_over_the_h100(big):
    o, w = big.oracle, big.world
    for engine in F.GPU_ENGINES:
        for suite, sf in (("baseline", "30"), ("complex", "300")):
            got = big.stats["x4"]["a100"][engine][suite][f"sf{sf}"]
            for name, getter in (("time", lambda h, r, q: w.time(h, r, suite, sf, q, engine)),
                                 ("cost_p_new", lambda h, r, q: PNEW[h] / 3600 * w.time(h, r, suite, sf, q, engine)),
                                 ("energy_total", lambda h, r, q: w.energy(h, r, suite, sf, q, engine)["S1-counter"])):
                queries = [q for q in F.QUERIES[suite] if w.failure("a100", 1, suite, sf, q, engine) is None]
                mine = {r: math.exp(sum(math.log(getter("a100", r, q)) for q in queries) / len(queries)) for r in (1, 2, 3, 4)}
                ref = {r: math.exp(sum(math.log(getter("h100", r, q)) for q in queries) / len(queries)) for r in range(1, 9)}
                expected = E.welch_record(mine, ref)
                record = got[name]
                assert record["estimate"] == pytest.approx(expected["estimate"], rel=1e-5), (engine, suite, sf, name)
                assert record["df"] == pytest.approx(expected["df"], rel=1e-5) and record["n_rounds"] == 4 and record["n_rounds_reference"] == 8
                assert record["class"] is None and record["mixed_scope"] is False
    assert big.stats["x4"]["a100"]["polars_gpu"]["baseline"]["sf1"]["energy_total"]["estimate"] is None            # the H100's SF1 energy is below resolution


def test_x5_is_the_cpu_engine_on_the_gpu_host_over_the_same_engine_on_c_gcp_and_x6_factorises_the_idle_gpu_tax(big):
    w = big.world
    for engine in F.CPU_ENGINES:
        record = big.stats["x5"]["h100"][engine]["baseline"]["sf30"]
        per_round = {r: math.exp(sum(math.log(w.time("h100", r, "baseline", "30", q, engine) / w.time("c-gcp", r, "baseline", "30", q, engine)) for q in F.QUERIES["baseline"]) / 2)
                     for r in range(1, 9)}
        want = summary(per_round)
        assert record["time"]["estimate"] == pytest.approx(want["estimate"], rel=1e-9) and record["time"]["ci_low"] == pytest.approx(want["ci_low"], rel=1e-6)
        x6 = big.stats["x6"]["h100"][engine]["baseline"]["sf30"]
        assert x6["price_ratio_b3_over_b1"]["p_new"] == pytest.approx(4.89 / 2.910776, rel=1e-12)
        assert x6["cost_ratio_b3_over_b1"]["p_new"]["estimate"] == pytest.approx(want["estimate"] * 4.89 / 2.910776, rel=1e-9)         # B3 / B1 = price ratio x time ratio
        idle_wh = {r: sum(76.0 * w.time("h100", r, "baseline", "30", q, engine) for q in F.QUERIES["baseline"]) / 3600 for r in range(1, 9)}
        mean = sum(idle_wh.values()) / 8
        sd = math.sqrt(sum((v - mean) ** 2 for v in idle_wh.values()) / 7)
        got = x6["idle_gpu_energy_wh_per_suite_run"]
        assert got["mean"] == pytest.approx(mean, rel=1e-6) and got["ci_low"] == pytest.approx(mean - T95[7] * sd / math.sqrt(8), rel=1e-5)
        assert got["scope"] == "S1-counter" and got["n_queries"] == 2
    assert big.stats["x6"]["h100"]["polars"]["baseline"]["sf1"]["idle_gpu_energy_wh_per_suite_run"]["mean"] is None             # SF1 energy: below resolution
    assert "c-gcp" not in big.stats["x5"] and "c-gcp" not in big.stats["x6"]


def test_delta_star_is_the_per_round_suite_total_difference_in_wh_and_in_w_over_the_gpu_suite_time(big):
    w = big.world
    host, suite, sf = "h100", "baseline", "100"
    record = big.stats["delta_star"]["x3"][host][suite][f"sf{sf}"]["energy_total"]
    wh, watts = {}, {}
    for r in range(1, 9):
        diff, seconds = 0.0, 0.0
        for q in F.QUERIES[suite]:
            e_c = min(sum(w.energy("c-gcp", r, suite, sf, q, e).values()) - w.energy("c-gcp", r, suite, sf, q, e)["S2-other"] for e in F.CPU_ENGINES)
            picked = min(F.GPU_ENGINES, key=lambda e: w.energy(host, r, suite, sf, q, e)["S1-counter"])
            diff += e_c - w.energy(host, r, suite, sf, q, picked)["S1-counter"]
            seconds += w.time(host, r, suite, sf, q, picked)
        wh[r], watts[r] = diff / 3600, diff / seconds
    mean_wh = sum(wh.values()) / 8
    sd = math.sqrt(sum((v - mean_wh) ** 2 for v in wh.values()) / 7)
    assert record["wh_per_suite_run"]["mean"] == pytest.approx(mean_wh, rel=1e-5)
    assert record["wh_per_suite_run"]["ci_low"] == pytest.approx(mean_wh - T95[7] * sd / math.sqrt(8), rel=1e-4)
    assert record["w_over_gpu_suite_time"]["mean"] == pytest.approx(sum(watts.values()) / 8, rel=1e-5)
    assert record["ref_scope"] == "S2-pinned" and record["eng_scope"] == "S1-counter" and "Delta* > 0" in record["reading"]
    assert record["idle_reference_w"] == {"s2_pinned_idle_w_one_socket": pytest.approx(216.0), "s2_all_idle_w": pytest.approx(430.0), "s1_idle_gpu_w_no_context": pytest.approx(76.0),
                                          "s1_idle_gpu_w_context_open": pytest.approx(142.0), "s1_tag": "S1-counter"}
    negative = big.stats["delta_star"]["x3"]["a100"]["baseline"]["sf10"]["energy_total"]            # the A100's SF10 cell is "CPU better": Delta* < 0
    assert negative["wh_per_suite_run"]["mean"] < 0 and "N1 excludes" in negative["reading"]


def test_suite_totals_are_sums_of_per_query_medians_over_the_queries_every_round_has(big):
    w = big.world
    got = big.stats["totals"]["h100"]["polars_gpu"]["baseline"]["sf30"]
    per_round = [sum(w.time("h100", r, "baseline", "30", q, "polars_gpu") for q in F.QUERIES["baseline"]) for r in range(1, 9)]
    assert got["time_s"]["mean"] == pytest.approx(sum(per_round) / 8, rel=1e-9) and got["time_s"]["n_rounds"] == 8
    assert got["cost_usd_p_new"]["mean"] == pytest.approx(4.89 / 3600 * sum(per_round) / 8, rel=1e-9)
    assert got["cost_usd_p_billed"]["mean"] == pytest.approx(4.95 / 3600 * sum(per_round) / 8, rel=1e-9)
    energy = [sum(w.energy("h100", r, "baseline", "30", q, "polars_gpu")["S1-counter"] for q in F.QUERIES["baseline"]) / 3600 for r in range(1, 9)]
    assert got["energy_wh"]["mean"] == pytest.approx(sum(energy) / 8, rel=1e-5)
    complex_totals = big.stats["totals"]["h100"]["polars_gpu"]["complex"]["sf100"]["time_s"]
    assert complex_totals["queries"] == ["cx_q09"]                               # cx_q13 has no polars_gpu time: it is not in the engine's total
    assert big.stats["totals"]["c-gcp"]["polars"]["baseline"]["sf30"]["energy_wh"]["mean"] == pytest.approx(
        sum(sum(w.energy("c-gcp", r, "baseline", "30", q, "polars")[k] for k in ("S2-pinned/package", "S2-pinned/dram")) for q in F.QUERIES["baseline"] for r in range(1, 9)) / 8 / 3600, rel=1e-6)


def independent_break_even(classes):
    """5.3 from the design text: the smallest scale from which every larger scale is GPU better; a scale with no estimate was not measured."""
    scales = ["1", "10", "30", "100", "300"] if len(classes) == 5 else ["30", "100", "300"]
    run, blocker = [], None
    for sf in reversed(scales):
        if classes[sf] != "gpu_better":
            blocker = sf
            break
        run.append(sf)
    if not run:
        return f"not determinable (SF{scales[-1]} not estimable)" if classes[scales[-1]] == "not_estimable" else f"> SF{scales[-1]}"
    smallest = run[-1]
    if blocker is None:
        return f"<= SF{smallest}"
    if classes[blocker] == "not_estimable":
        return f"<= SF{smallest} (SF{blocker} not estimable)"
    return f"(SF{blocker}, SF{smallest}]"


@pytest.mark.parametrize("cname", ["x1", "x2", "x3"])
@pytest.mark.parametrize("metric", ["energy_total", "energy_above_idle", "cost_b1_p_new", "cost_b2_p_old", "cost_b3_p_billed"])
def test_the_break_even_brackets_follow_the_oracle_classes(big, cname, metric):
    for host in ("h100", "a100"):
        for suite in ("baseline", "complex"):
            classes = {sf: big.oracle.contrast(cname, host, metric, suite, sf)["class"] for s, sf in SUITE_SCALES if s == suite}
            got = big.stats["break_even"][cname][host][suite][metric]
            assert got["text"] == independent_break_even(classes), (cname, host, suite, metric)
            assert got["classes"] == {f"sf{sf}": c for sf, c in classes.items()}
            assert (got["label"] is not None) == metric.startswith("energy")             # energy: "lower bound on the node-level break-even, under N1"


def test_the_break_even_brackets_of_the_designed_world_are_where_its_ratios_cross_one(big):
    be = big.stats["break_even"]
    assert be["x1"]["h100"]["baseline"]["cost_b3_p_new"]["text"] == "(SF10, SF30]"           # polars: (2 / 6) sf^0.4 passes 1 between SF10 and SF30
    assert be["x1"]["h100"]["baseline"]["energy_total"]["classes"]["sf1"] == "not_estimable"      # the H100's SF1 energy is below resolution: never "<= SF1"
    assert be["x1"]["h100"]["baseline"]["energy_total"]["text"] == "<= SF10 (SF1 not estimable)" and be["x1"]["h100"]["baseline"]["energy_total"]["blocked_by"] == "1"
    assert be["x1"]["h100"]["complex"]["cost_b3_p_new"]["text"] == "<= SF30"                       # the complex grid starts at SF30: censored at its bottom
    assert be["x3"]["h100"]["baseline"]["cost_b1_p_new"]["text"] == "(SF30, SF100]"
    assert be["x1"]["a100"]["baseline"]["energy_total"]["text"] == "(SF10, SF30]" and be["x1"]["a100"]["baseline"]["energy_total"]["censored"] is False


def test_the_descriptive_interpolation_puts_the_crossing_inside_the_bracket(big):
    got = big.stats["break_even"]["x1"]["h100"]["baseline"]["cost_b3_p_new"]
    low, high = big.stats["x1"]["h100"]["baseline"]["sf10"]["cost_b3_p_new"]["estimate"], big.stats["x1"]["h100"]["baseline"]["sf30"]["cost_b3_p_new"]["estimate"]
    expected = math.exp(math.log(10) + (0 - math.log(low)) / (math.log(high) - math.log(low)) * (math.log(30) - math.log(10)))
    assert got["text"] == "(SF10, SF30]" and 10 < got["interpolated_scale"] < 30 and got["interpolated_scale"] == pytest.approx(expected, rel=1e-9)
    assert big.stats["break_even"]["x1"]["h100"]["complex"]["cost_b3_p_new"]["interpolated_scale"] is None          # censored at the bottom: nothing to interpolate


def test_the_c_vm_check_passes_inside_the_band_and_b1_stays_on_the_metal_times(big):
    check = big.stats["c_vm_check"]
    w = big.world
    ratios = []
    for engine in F.CPU_ENGINES:
        for sf in K.C_VM_SCALES:
            for q in F.QUERIES["baseline"]:
                vm = math.exp(sum(math.log(w.time("c-vm", r, "baseline", sf, q, engine)) for r in (1, 2)) / 2)
                metal = math.exp(sum(math.log(w.time("c-gcp", r, "baseline", sf, q, engine)) for r in range(1, 9)) / 8)
                ratios.append(vm / metal)
    assert check["status"] == "pass" and check["b1_basis"] == "metal" and check["n_cells"] == 12
    assert check["ratio_vm_over_metal"] == pytest.approx(math.exp(sum(map(math.log, ratios)) / 12), rel=1e-9)
    assert 0.90 <= check["ratio_vm_over_metal"] <= 1.10 and check["resource_exhaustion_at_sf300"] == []


class SlowVM(F.World):
    def time(self, host, round_, suite, scale, query, engine):
        return super().time(host, round_, suite, scale, query, engine) * (1.3 if host == "c-vm" else 1.0)


@pytest.mark.parametrize("world, reason", [(SlowVM(), "ratio"), (F.World(failures={("c-vm", 2, "baseline", "300", "q03", "spark_cpu"): ("MemoryError", "java.lang.OutOfMemoryError: Java heap space")}), "oom")])
def test_a_failed_c_vm_check_moves_b1_to_the_vm_times_where_it_has_them_and_reports_the_gap(tmp_path, world, reason):
    grid = {"baseline": ("30", "100", "300"), "complex": ("30",)}
    builder = F.TreeBuilder(tmp_path / "tree", world.truth(), grid=grid)
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    builder.add_launch("c-vm", 1, [1, 2])
    builder_vm_grid = builder.suites_of("c-vm")
    assert builder_vm_grid == {"baseline": ["10", "30", "300"]}
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    check = stats["c_vm_check"]
    assert check["status"] == "fail" and check["b1_basis"] == "vm"
    if reason == "oom":
        assert check["resource_exhaustion_at_sf300"] == [["spark_cpu", "q03", 2]]
    else:
        assert check["ratio_vm_over_metal"] == pytest.approx(1.3 * 1.15 / 1.1, rel=0.03)
    w = F.World()
    # SF30 (on the VM grid) uses the VM's geometric-mean time; SF100 (not on it) keeps the metal time; the complex suite keeps the metal time
    record = stats["x1"]["h100"]["baseline"]["sf30"]["cost_b1_p_new"]["q"]["q01"]["per_round"][0]
    vm = math.exp(sum(math.log(world.time("c-vm", r, "baseline", "30", "q01", "polars")) for r in (1, 2)) / 2)
    assert record == pytest.approx((2.910776 * vm) / (4.89 * w.time("h100", 1, "baseline", "30", "q01", "polars_gpu")), rel=1e-9)
    record = stats["x1"]["h100"]["baseline"]["sf100"]["cost_b1_p_new"]["q"]["q01"]["per_round"][0]
    assert record == pytest.approx((2.910776 * w.time("c-gcp", 1, "baseline", "100", "q01", "polars")) / (4.89 * w.time("h100", 1, "baseline", "100", "q01", "polars_gpu")), rel=1e-9)


@pytest.mark.parametrize("message", ["java.lang.OutOfMemoryError: Java heap space", "An error occurred while calling o91.collectToPython: GC overhead limit exceeded",
                                     "failed to allocate 4096 bytes", "unable to allocate array", "allocation failed", "GpuRetryOOM: retry exhausted"])
def test_the_c_vm_check_fails_on_any_resource_exhaustion_the_a1_rules_name_at_sf300(tmp_path, message):
    """Query-level errors are classified with preflight.classify_error (the campaign's own A1 rules), not Paper 1's narrower classifier."""
    fails = {("c-vm", r, "baseline", "300", "q03", "spark_cpu"): ("Py4JJavaError", message) for r in (1, 2)}
    builder = F.TreeBuilder(tmp_path / "tree", F.World(failures=fails).truth(), grid={"baseline": ("30", "300"), "complex": ("30",)})
    for host, rounds in (("c-gcp", [1, 2, 3, 4]), ("h100", [1, 2, 3, 4]), ("c-vm", [1, 2])):
        builder.add_launch(host, 1, rounds)
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    assert stats["c_vm_check"]["status"] == "fail" and stats["c_vm_check"]["b1_basis"] == "vm"
    assert stats["c_vm_check"]["resource_exhaustion_at_sf300"] == [["spark_cpu", "q03", 1], ["spark_cpu", "q03", 2]]


def test_a_c_vm_session_killed_by_heap_exhaustion_fails_the_check_and_keeps_every_query_s_outcome(tmp_path):
    """A session that did not complete (the JVM died) is a coverage outcome of the session (preflight C1); its cells keep the class, completed queries marked."""
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30", "300"), "complex": ("30",)})
    for host, rounds in (("c-gcp", [1, 2, 3, 4]), ("h100", [1, 2, 3, 4])):
        builder.add_launch(host, 1, rounds)
    root = builder.add_launch("c-vm", 1, [1, 2])
    for r in (1, 2):
        schedule = root / f"round-{r}" / "baseline" / "schedule.json"
        items = json.loads(schedule.read_text())
        for item in items:
            if item["scale"] == "300" and item["engine"] == "spark_cpu":
                item["status"], item["returncode"] = "failed", 1
        schedule.write_text(json.dumps(items))
        path = root / f"round-{r}" / "baseline" / "sf300" / "spark_cpu" / "results.json"
        data = json.loads(path.read_text())
        data.pop("completed_at_utc", None)
        data["failure"] = {"type": "Py4JJavaError", "message": "java.lang.OutOfMemoryError: Java heap space"}
        data["records"] = [rec for rec in data["records"] if not (rec["query"] == "q03" and rec["phase"] == "measured")]       # q01 completed, q03 never got there
        path.write_text(json.dumps(data))
    tree, store = load_small(tmp_path / "tree")
    cells = {c.query: c for c in store.cells if (c.host, c.round, c.scale, c.engine) == ("c-vm", 1, "300", "spark_cpu")}
    assert cells["q01"].coverage == cells["q03"].coverage == "resource_exhaustion" and cells["q01"].time_s is None
    assert cells["q01"].completed_before_failure and not cells["q03"].completed_before_failure
    assert cells["q01"].time_reason == "session_failed:resource_exhaustion:completed" and cells["q03"].time_reason == "session_failed:resource_exhaustion:not_reached"
    assert contrasts_mod.c_vm_check(tree, store)["status"] == "fail"
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    rows = read_csv(tmp_path / "out" / "tables" / "t11_coverage_matrix.csv")
    row = next(r for r in rows if (r["host"], r["scale"], r["query"], r["engine"]) == ("c-vm", "300", "q01", "spark_cpu"))
    assert row["failure_class"] == "resource_exhaustion" and row["completed_before_session_failure"] == "2" and "session_failed:resource_exhaustion:completed=2" in row["reasons"]
    assert stats["counts"]["c-vm"]["coverage_outcomes"]["resource_exhaustion"] == 4


def test_a_session_that_left_no_results_json_is_a_warning_not_a_crash_when_it_did_not_complete(tmp_path):
    _, root = small_tree(tmp_path, hosts=("h100",))
    attempt = root / "h100" / "launch1" / "rerun" / "paper3-h100-l1" / "round-1" / "baseline"
    schedule = json.loads((attempt / "schedule.json").read_text())
    for item in schedule:
        if item["engine"] == "spark_gpu" and item["scale"] == "30":
            item.update(status="failed", returncode=1)
    (attempt / "schedule.json").write_text(json.dumps(schedule))
    (attempt / "sf30" / "spark_gpu" / "results.json").unlink()
    tree, store = load_small(root)
    assert any("left no results.json" in w for w in store.warnings)
    (attempt / "sf30" / "polars" / "results.json").unlink()                      # a COMPLETE session with no results.json is an error
    with pytest.raises(tree_mod.AnalysisError, match="missing or unreadable"):
        load_small(root)


def test_without_a_c_vm_launch_the_check_is_not_run_and_b1_uses_the_metal_times(tmp_path):
    _, root = small_tree(tmp_path)
    stats = A.run_analysis(root, tmp_path / "out", unblind=True, allow_synthetic=True)
    assert stats["c_vm_check"]["status"] == "not_run" and stats["c_vm_check"]["b1_basis"] == "metal"
    assert stats["x1"]["h100"]["baseline"]["sf30"]["cost_b1_p_billed"]["estimate"] is not None            # P-BILLED falls back to P-NEW's B1 price, recorded below
    assert stats["_meta"]["hosts_not_run"] == ["a100", "c-vm"]


def test_a_gpu_host_and_c_gcp_with_no_round_in_common_give_not_estimable_never_a_pooled_estimate(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("a100", 2, [5, 6, 7, 8])                   # the second square only
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])                  # the first square only
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    cross = stats["x3"]["a100"]["baseline"]["sf30"]["energy_total"]
    assert cross["class"] == "not_estimable" and cross["n_rounds"] == 0 and cross["estimate"] is None
    same_host = stats["x3"]["a100"]["baseline"]["sf30"]["time"]
    assert same_host["rounds"] == [5, 6, 7, 8] and same_host["estimate"] is not None


def test_one_round_gives_an_estimate_and_no_interval_and_is_never_a_difference(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    record = stats["x1"]["h100"]["baseline"]["sf30"]["time"]
    assert record["n_rounds"] == 1 and record["estimate"] is not None and record["ci_low"] is None and record["class"] == "inconclusive"
    assert record["basis"] == "descriptive_no_complete_square"


def test_the_gh200_counter_is_module_scope_and_its_cross_class_energy_cells_say_so(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30", "300"), "complex": ("30",)})
    builder.add_launch("gh200", 1, [1, 2, 3, 4])
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    record = stats["x3"]["gh200"]["baseline"]["sf30"]["energy_total"]
    assert record["eng_scope"] == "S1-module" and record["mixed_scope"] is True and "mixed scope" in record["n1"] and record["ref_scope"] == "S2-pinned"
    assert stats["x3"]["h100"]["baseline"]["sf30"]["energy_total"]["mixed_scope"] is False
    tree, store = load_small(tmp_path / "tree")
    cpu = find(store, "gh200", 1, "baseline", "30", "q01", "polars")
    assert cpu.energy["S1-module"].above_j is not None                   # 2.2: on the GH200 the CPU engine's above-idle is reported
    assert stats["x4"]["gh200"]["polars_gpu"]["baseline"]["sf30"]["energy_total"]["mixed_scope"] is True
    assert "gh200" not in stats["x6"] or stats["x6"]["gh200"]["polars"]["baseline"]["sf30"] == {}      # the idle-GPU energy is x86 only


def test_a_wide_interval_that_contains_one_is_inconclusive_never_no_difference(tmp_path):
    base = F.World()
    overrides = {("h100", r, "baseline", "30", q, "polars_gpu"): base.time("h100", r, "baseline", "30", q, "polars") * f
                 for r, f in zip((1, 2, 3, 4), (2.0, 0.5, 2.0, 0.5)) for q in F.QUERIES["baseline"]}              # the ratio alternates 0.5, 2, 0.5, 2
    _, root = small_tree(tmp_path, world=F.World(time_overrides=overrides), hosts=("h100", "c-gcp"))
    stats = A.run_analysis(root, tmp_path / "out", unblind=True, allow_synthetic=True)
    record = stats["x1"]["h100"]["baseline"]["sf30"]["time"]
    assert record["estimate"] == pytest.approx(1.0, rel=1e-9) and record["ci_low"] < 1.0 < record["ci_high"] and record["class"] == "inconclusive"
    assert record["within_margin"] is False and record["df"] == 3
    assert stats["x1"]["h100"]["baseline"]["sf300"]["time"]["class"] == "gpu_better"


# ---- coverage matrix and QA ---------------------------------------------------------------------------------------------------------------

def read_csv(path):
    with Path(path).open(newline="") as handle:
        return list(csv.DictReader(handle))


def test_the_coverage_matrix_lists_each_cell_with_the_reason_it_is_missing(big):
    rows = read_csv(big.out / "tables" / "t11_coverage_matrix.csv")
    gap = next(r for r in rows if (r["host"], r["suite"], r["scale"], r["query"], r["engine"]) == ("h100", "complex", "100", "cx_q13", "polars_gpu"))
    assert gap["coverage"] == "unsupported_operation" and gap["time_ok"] == "0" and gap["rounds_with_cell"] == "8" and "coverage:unsupported_operation=8" in gap["reasons"]
    sf1 = next(r for r in rows if (r["host"], r["suite"], r["scale"], r["query"], r["engine"]) == ("h100", "baseline", "1", "q01", "polars_gpu"))
    assert sf1["time_ok"] == "8" and sf1["energy_ok"] == "0" and sf1["energy_series"] == "S1-counter" and "energy:below_resolution=8" in sf1["reasons"]
    fine = next(r for r in rows if (r["host"], r["suite"], r["scale"], r["query"], r["engine"]) == ("c-gcp", "baseline", "30", "q01", "polars"))
    assert fine["energy_series"] == "S2-pinned" and fine["energy_ok"] == fine["above_idle_ok"] == fine["time_ok"] == "8" and fine["reasons"] == ""
    idle_gpu = next(r for r in rows if (r["host"], r["suite"], r["scale"], r["query"], r["engine"]) == ("a100", "baseline", "30", "q01", "polars"))
    assert idle_gpu["energy_ok"] == "4" and idle_gpu["above_idle_ok"] == "0" and "above_idle:not_reported_idle_gpu=4" in idle_gpu["reasons"]


def qa_tree(tmp_path):
    """An H100 square with G8 and other flags injected in known sessions, and a CPU-only host."""
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30", "300"), "complex": ("30",)})

    def hook_for(r):
        def hook(kind, key, default):
            if kind == "session" and key[2] == "polars_gpu" and key[1] == "300" and key[3] == "G8" and r in (1, 2):
                return {"outcome": "fail", "problems": ["median onset lag +0.592 s outside +-0.4 s"], "method": "windows", "median_lag_s": 0.592, "drift_s": 0.339}
            if kind == "session" and key[2] == "polars" and key[1] == "30" and key[3] == "G8" and r == 3:
                return {"outcome": "not_evaluable", "reasons": ["one usable onset"], "method": "none"}
            if kind == "session" and key[3] == "G7" and key[2] == "spark_gpu" and r == 4:
                return {"outcome": "fail", "problems": ["coverage"], "windows": 4, "flagged_windows": [{}]}
            if kind == "session" and key[3] == "G11" and key[1] == "30" and key[2] == "spark_cpu":
                return {"outcome": "fail", "problems": ["x"], "throttle_samples_with_reasons": 3}
            if kind == "session" and key[3] == "G10" and key[2] == "polars_gpu" and key[1] == "30":
                return {"outcome": "fail", "problems": ["x"], "relative_difference": {"S1-counter": 0.16}, "unreliable_scopes": ["S1-counter"]}
            if kind == "cell" and key[1] == "30" and key[2] == "polars_gpu" and key[0] == "baseline" and key[3] == "q01":
                return {**default, "outcome": "fail", "reasons": ["per_run_below_t_min"]}
            return default
        return hook
    for r in (1, 2, 3, 4):
        builder.gate_hooks[("h100", r)] = hook_for(r)
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    return builder, tmp_path / "tree"


def test_the_qa_tables_count_g8_per_host_and_engine_as_amendment_a4_requires(tmp_path):
    _, root = qa_tree(tmp_path)
    stats = A.run_qa(root, tmp_path / "qa", synthetic=True)
    g8 = stats["qa"]["g8"]["h100"]
    assert g8["polars_gpu"]["sessions"] == 12 and g8["polars_gpu"]["fail"] == 2 and g8["polars_gpu"]["pass"] == 10 and g8["polars_gpu"]["flagged"] == 2
    assert g8["polars_gpu"]["flagged_share"] == pytest.approx(2 / 12) and g8["polars_gpu"]["method_windows"] == 12
    assert g8["polars"]["not_evaluable"] == 2 and g8["polars"]["flagged"] == 2 and g8["polars"]["method_pulses"] == 10      # round 3, SF30 of both suites
    assert g8["spark_cpu"]["flagged"] == 0 and g8["polars_gpu"]["median_lag_s"]["max"] == 0.592
    assert "c-gcp" not in stats["qa"]["g8"] or all(v["pass"] == 0 and v["fail"] == 0 for v in stats["qa"]["g8"]["c-gcp"].values())      # G8 is not applicable on the CPU-only host
    rows = read_csv(tmp_path / "qa" / "tables" / "qa_g8_by_host_engine.csv")
    row = next(r for r in rows if (r["host"], r["engine"]) == ("h100", "polars_gpu"))
    assert row["data_status"] == K.WATERMARK and row["fail"] == "2" and row["sessions"] == "12" and float(row["flagged_share"]) == pytest.approx(2 / 12)


def test_the_qa_tables_count_the_g7_to_g11_flags_and_the_g9_cells(tmp_path):
    _, root = qa_tree(tmp_path)
    flags = A.run_qa(root, tmp_path / "qa", synthetic=True)["qa"]["flags"]["h100"]
    assert flags["spark_gpu"]["G7"] == {"pass": 9, "fail": 3, "not_evaluable": 0, "not_applicable": 0}                 # round 4: three sessions
    assert flags["spark_cpu"]["G11"]["fail"] == 8 and flags["polars_gpu"]["G10"]["fail"] == 8 and flags["polars_gpu"]["G11"]["pass"] == 12
    assert flags["polars"]["G12"]["pass"] == 12
    g9 = A.run_qa(root, tmp_path / "qa", synthetic=True)["qa"]["g9"]
    assert g9["h100|baseline|30|polars_gpu"]["fail"] == 4 and g9["h100|baseline|30|polars_gpu"]["per_run_below_t_min"] == 4 and g9["h100|baseline|30|polars_gpu"]["cells"] == 8
    stability = A.run_qa(root, tmp_path / "qa", synthetic=True)["qa"]["g10_relative_difference"]["h100"]["polars_gpu"]["S1-counter"]
    assert stability["max"] == 0.16 and stability["n"] == 12
    attempts = A.run_qa(root, tmp_path / "qa", synthetic=True)["qa"]["attempts"]
    assert len(attempts) == 8 and all(a["accepted"] and a["verdict"] == "pass" for a in attempts)


def test_the_qa_mode_opens_only_the_files_the_qa_view_allows_and_works_without_any_other(tmp_path, monkeypatch):
    _, root = qa_tree(tmp_path)
    for name in ("results.json", "schedule.json", "power.csv", "power_sources.json", "campaign.json"):     # nothing but the gate files and status.json is needed
        for path in root.rglob(name):
            path.unlink()
    opened = []
    real = Path.open

    def tracking(self, *args, **kwargs):
        opened.append(Path(self))
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", tracking)
    stats = A.run_qa(root, tmp_path / "qa", synthetic=True)
    assert stats["instances"] and stats["qa"]["g8"]
    inside = [p for p in opened if root in p.parents]
    assert inside
    for path in inside:
        relative = path.relative_to(next(a for a in path.parents if (a / "status.json").is_file()))
        assert qa_mod.ALLOWED.match(relative.as_posix()), relative


def test_the_qa_reader_refuses_every_other_file_by_construction(tmp_path):
    builder, root = qa_tree(tmp_path)
    campaign = root / "h100" / "launch1" / "rerun" / "paper3-h100-l1"
    for name in ("round-1/baseline/schedule.json", "round-1/baseline/sf30/polars/results.json", "round-1/baseline/power.csv", "campaign.json", "round-1/attempt.json",
                 "../outside.json"):
        with pytest.raises(qa_mod.ForbiddenRead):
            qa_mod.guarded_read(campaign, name)
    assert qa_mod.guarded_read(campaign, "status.json")["state"] == "complete"


def test_the_qa_output_holds_no_energy_power_or_cost_aggregate(tmp_path):
    _, root = qa_tree(tmp_path)
    A.run_qa(root, tmp_path / "qa", synthetic=True)
    text = "".join(p.read_text() for p in (tmp_path / "qa").rglob("*") if p.is_file())
    for forbidden in ("estimate", "total_j", "energy_wh", "cost_", "ratio_vm_over_metal", "idle_w"):
        assert forbidden not in text, forbidden


def test_the_real_gate_report_has_the_shape_the_reader_depends_on(tmp_path):
    """paper3_gates.evaluate_round on a RunBuilder run, against the fake's report and the reader's use of both."""
    root = RF.RunBuilder(tmp_path / "run", host="h100", engines=("polars", "polars_gpu")).build().root
    real = pg.evaluate_round({"baseline": root}, RF.host_gates("h100"))
    fake = F.gate_report("h100", {"baseline": [("1", "polars"), ("1", "polars_gpu")]}, {"baseline": ("q01", "q03")})
    assert set(real) == set(fake)
    real_session, fake_session = real["runs"][0]["sessions"][0], fake["runs"][0]["sessions"][0]
    assert set(real_session) == set(fake_session) and set(real_session["flags"]) == set(fake_session["flags"]) and set(real_session["gates"]) == set(fake_session["gates"])
    needed = {"suite", "scale", "engine", "query", "outcome", "reasons"}
    assert needed <= set(real["runs"][0]["cells"][0]) and needed <= set(fake["runs"][0]["cells"][0])
    g8 = next(s for s in real["runs"][0]["sessions"] if s["engine"] == "polars_gpu")["gates"]["G8"]
    assert {"outcome", "method"} <= set(g8) and {"outcome", "method"} <= set(fake_session["gates"]["G8"] | {"method": 1})
    sessions, cells = cells_mod.gate_views(real)
    assert ("baseline", "1", "polars_gpu") in sessions and ("baseline", "1", "polars_gpu", "q01") in cells
    g10 = real["runs"][0]["sessions"][0]["gates"]["G10"]
    assert "unreliable_scopes" in g10 and "relative_difference" in g10
    instance = qa_mod.QAInstance("h100", "1", tmp_path, {"state": "complete"}, [qa_mod.QAAttempt("round-1", 1, 0, "pass", real)])
    result = qa_mod.qa_from_gates([instance])
    assert result["g8"]["h100"]["polars_gpu"]["sessions"] == 1 and result["flags"]["h100"]["polars"]["G6"]["pass"] == 1
    assert result["g9"]["h100|baseline|1|polars_gpu"]["cells"] == 2


# ---- outputs ----------------------------------------------------------------------------------------------------------------------------------

SUITE_RECORD_KEYS = ["basis", "ci_high", "ci_low", "class", "cross_class", "df", "eng_host", "eng_scope", "estimate", "log_mean", "log_sd", "max", "min", "mixed_scope",
                     "n1", "n_excluded_nonpositive", "n_queries", "n_queries_union", "n_rounds", "per_round", "per_round_n_queries", "q", "queries", "ref_host",
                     "ref_scope", "rounds", "rounds_requested", "selection", "totals", "units", "within_margin"]
QUERY_RECORD_KEYS = ["ci_high", "ci_low", "class", "df", "estimate", "log_mean", "log_sd", "max", "min", "n_rounds", "per_round", "rounds"]
TOP_LEVEL_KEYS = ["_meta", "billing", "billing_by_host", "break_even", "c_vm_check", "counts", "coverage", "delta_star", "descriptive", "hosts", "price_ratios", "prices", "qa",
                  "scope_matrix", "sensitivity", "sensitivity_unpaired", "totals", "x1", "x2", "x3", "x3_lenient", "x3_strict", "x4", "x5", "x6"]


def test_stats_json_has_stable_keys(big):
    assert sorted(big.stats) == TOP_LEVEL_KEYS
    assert sorted(big.stats["x3"]["h100"]["baseline"]["sf30"]["energy_total"]) == SUITE_RECORD_KEYS
    assert sorted(big.stats["x3"]["h100"]["baseline"]["sf30"]["energy_total"]["q"]["q01"]) == QUERY_RECORD_KEYS
    assert sorted(big.stats["x3"]["h100"]) == ["baseline", "complex"] and sorted(big.stats["x3"]["h100"]["baseline"]) == ["sf1", "sf10", "sf100", "sf30", "sf300"]
    assert sorted(big.stats["x3"]["h100"]["baseline"]["sf30"]) == sorted(METRICS)
    assert sorted(big.stats["x3"]["h100"]["baseline"]["sf30"]["time"]["totals"]) == ["eng", "n_queries", "ratio", "ref", "units"]
    assert sorted(big.stats["_meta"]) == ["analysis", "analysis_code_sha256", "declared_ended", "declared_ended_problems", "design", "design_doc_sha256", "format", "hosts_in_tree",
                                           "hosts_not_run", "inputs_sha256", "n1", "p_billed_b1", "price_tables", "synthetic", "warnings", "watermark"]
    assert big.stats["_meta"]["synthetic"] is True and big.stats["_meta"]["watermark"] == "SYNTHETIC - NOT RESULTS"
    assert big.stats["_meta"]["hosts_not_run"] == []
    assert sorted(big.stats["x4"]["a100"]["polars_gpu"]["baseline"]["sf30"]) == ["cost_p_billed", "cost_p_new", "cost_p_old", "energy_above_idle", "energy_total", "time"]


def test_stats_flat_and_stats_tex_hold_the_same_scalar_statistics_the_paper_macro_reads(big):
    flat = json.loads((big.out / "stats_flat.json").read_text())
    assert flat["x3.h100.baseline.sf30.energy_total.estimate"] == big.stats["x3"]["h100"]["baseline"]["sf30"]["energy_total"]["estimate"]
    tex = (big.out / "stats.tex").read_text()
    estimate = big.stats["x3"]["h100"]["baseline"]["sf30"]["time"]["estimate"]
    assert f"\\defstat{{x3.h100.baseline.sf30.time.estimate}}{{{AC.sig3(estimate)}}}" in tex
    assert "\\defstat{x3.h100.baseline.sf30.time.class}{gpu\\_better}" in tex
    assert ".q.q01." not in tex and "per_round" not in tex                                   # per-query values and lists are not paper macros
    assert "\\syntheticstatstrue" in tex and "SYNTHETIC --- NOT RESULTS" in tex
    assert "\\defstat{hosts.h100.confirmatory_rounds}" not in tex                            # lists are never printable


def test_the_tables_are_written_as_csv_markdown_and_latex_with_the_watermark(big):
    tables = big.out / "tables"
    for name in ("t01_x_suite_contrasts", "t02_break_even", "t03_delta_star", "t04_g8_by_host_engine"):
        for suffix in ("csv", "md", "tex"):
            assert (tables / f"{name}.{suffix}").is_file(), (name, suffix)
    rows = read_csv(tables / "t01_x_suite_contrasts.csv")
    assert rows[0]["data_status"] == "SYNTHETIC — NOT RESULTS" or rows[0]["data_status"] == K.WATERMARK
    row = next(r for r in rows if (r["contrast"], r["host"], r["suite"], r["scale"], r["metric"]) == ("x3", "h100", "baseline", "sf30", "energy_total"))
    assert float(row["estimate"]) == big.stats["x3"]["h100"]["baseline"]["sf30"]["energy_total"]["estimate"] and row["class"] == "gpu_better"
    assert row["ref_scope"] == "S2-pinned" and row["eng_scope"] == "S1-counter" and row["cross_class"] == "True"
    be = read_csv(tables / "t02_break_even.csv")
    line = next(r for r in be if (r["contrast"], r["host"], r["suite"], r["metric"]) == ("x1", "h100", "baseline", "cost_b3_p_new"))
    assert line["bracket"] == "(SF10, SF30]" and "SF1" not in line["label"]
    assert "SYNTHETIC" in (tables / "t02_break_even.tex").read_text() and "\\begin{longtable}" in (tables / "t01_x_suite_contrasts.tex").read_text()
    queries = read_csv(tables / "t05_x_query_contrasts.csv")
    assert any(r["contrast"] == "x1" and r["query"] == "q03" and r["metric"] == "time" for r in queries)
    cells = read_csv(tables / "t10_cells.csv")
    assert {"time_s", "total_j", "above_idle_j", "idle_w", "tag"} <= set(cells[0])
    assert any(r["tag"] == "S2-all" for r in cells) and any(r["tag"] == "S1-counter" for r in cells)
    squares = read_csv(tables / "t12_squares.csv")
    assert {(r["host"], r["square"], r["complete"]) for r in squares} >= {("h100", "1", "True"), ("a100", "2", "False"), ("c-vm", "1", "True")}


def test_the_outputs_are_deterministic_and_a_second_run_gives_the_same_bytes(tmp_path):
    _, root = small_tree(tmp_path)
    A.run_analysis(root, tmp_path / "one", unblind=True, allow_synthetic=True)
    A.run_analysis(root, tmp_path / "two", unblind=True, allow_synthetic=True)
    files = sorted(p.relative_to(tmp_path / "one") for p in (tmp_path / "one").rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(tmp_path / "two") for p in (tmp_path / "two").rglob("*") if p.is_file()) and len(files) > 25
    for name in files:
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes(), name


def test_the_meta_block_records_the_code_and_the_inputs(big):
    meta = big.stats["_meta"]
    assert len(meta["analysis_code_sha256"]) == 64 and len(meta["inputs_sha256"]["all"]) == 64
    assert sorted(meta["inputs_sha256"]["per_instance"]) == ["a100-l1", "c-gcp-l1", "c-gcp-l2", "c-vm-l1", "h100-l1", "h100-l2"]
    assert meta["design_doc_sha256"] == __import__("hashlib").sha256((REPO / "docs" / "PAPER3_ENERGY_DESIGN.md").read_text().encode()).hexdigest()


def test_the_scope_matrix_and_billing_reconciliation(big):
    matrix = {(r["host"], r["engine"]): r["scope_tag"] for r in big.stats["scope_matrix"]}
    assert matrix[("h100", "polars_gpu")] == matrix[("h100", "polars")] == "S1-counter" and matrix[("c-gcp", "polars")] == "S2-pinned" and matrix[("c-vm", "polars")] == "none"
    row = next(r for r in big.stats["billing"] if r["instance"] == "h100-l1")
    assert row["hours"] == pytest.approx(24.0) and row["expected_usd"] == pytest.approx(24.0 * 4.95) and row["invoice_usd"] is None and row["gap"] is None
    stats = A.run_analysis(big.tree, big.root / "out2", unblind=True, allow_synthetic=True, invoices={"h100-l1": 24.0 * 4.95 * 1.05, "a100-l1": 24.0 * 2.10})
    rows = {r["instance"]: r for r in stats["billing"]}
    assert rows["h100-l1"]["gap_above_2pct"] is True and rows["h100-l1"]["gap"] == pytest.approx(0.05)
    assert rows["a100-l1"]["gap_above_2pct"] is False


def test_the_command_line_runs_the_full_analysis_and_the_qa_mode(tmp_path, capsys):
    _, root = small_tree(tmp_path)
    assert A.main(["--tree", str(root), "--output", str(tmp_path / "full"), "--unblind", "--allow-synthetic"]) == 0
    assert (tmp_path / "full" / "stats.json").is_file()
    assert A.main(["--tree", str(root), "--output", str(tmp_path / "qa"), "--qa-only", "--allow-synthetic"]) == 0
    assert (tmp_path / "qa" / "qa.json").is_file() and not (tmp_path / "qa" / "stats.json").exists()
    assert A.main(["--tree", str(tmp_path / "empty"), "--output", str(tmp_path / "x"), "--unblind"]) == 2
    assert "no campaign.json" in capsys.readouterr().err


def test_the_analysis_has_no_default_tree_so_nothing_opens_the_blinded_campaign_directory_unasked(capsys):
    with pytest.raises(SystemExit) as exit_info:
        A.main(["--output", "x"])
    assert exit_info.value.code == 2 and "--tree" in capsys.readouterr().err
    assert "rerun/paper3/" not in (REPO / "analyze_paper3.py").read_text().replace("rerun/paper3-<host>", "")


# ---- the descriptive summaries of 5.2 and the per-launch sensitivity ------------------------------------------------------------------------

class PositionWorld(F.World):
    """Time grows 4% with the engine's position in the round (0 first): a known order effect."""

    def __init__(self):
        super().__init__()
        self.orders = {}

    def position(self, host, round_, engine):
        import paper3_campaign as pc
        key = (host, round_)
        if key not in self.orders:
            self.orders[key] = pc.build_plan(host, "20261116").rounds[round_ - 1].engine_order
        return self.orders[key].index(engine)

    def time(self, host, round_, suite, scale, query, engine):
        return super().time(host, round_, suite, scale, query, engine) * (1.0 + 0.04 * self.position(host, round_, engine))


def test_cv_and_the_position_and_predecessor_summaries_recover_a_known_order_effect(tmp_path):
    import paper3_campaign as pc
    world = PositionWorld()
    builder = F.TreeBuilder(tmp_path / "tree", world.truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    builder.add_launch("h100", 2, [5, 6, 7, 8])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    builder.add_launch("c-gcp", 2, [5, 6, 7, 8])
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    engine, host, suite, sf = "polars_gpu", "h100", "baseline", "30"
    plan = pc.build_plan(host, "20261116")
    queries = F.QUERIES[suite]
    mean = {q: math.exp(sum(math.log(world.time(host, r, suite, sf, q, engine)) for r in range(1, 9)) / 8) for q in queries}
    per_round = {r: math.exp(sum(math.log(world.time(host, r, suite, sf, q, engine) / mean[q]) for q in queries) / 2) for r in range(1, 9)}
    got = stats["descriptive"]["order_and_carryover"][host][engine][suite]["sf30"]["time"]
    for position in "1234":
        rounds = [r for r in range(1, 9) if plan.rounds[r - 1].engine_order.index(engine) == int(position) - 1]
        assert got["by_position"][position]["rounds"] == rounds
        assert got["by_position"][position]["estimate"] == pytest.approx(math.exp(sum(math.log(per_round[r]) for r in rounds) / len(rounds)), rel=1e-9)
    assert got["by_position"]["4"]["estimate"] > got["by_position"]["1"]["estimate"]
    predecessors = {plan.rounds[r - 1].engine_order[plan.rounds[r - 1].engine_order.index(engine) - 1] if plan.rounds[r - 1].engine_order.index(engine) else "none" for r in range(1, 9)}
    assert set(got["by_predecessor"]) == predecessors
    cv = stats["descriptive"]["cv"][host][engine][suite]["sf30"]["time"]
    factors = F.TRIAL_FACTORS
    mean_f = sum(factors) / 3
    sd_f = math.sqrt(sum((f - mean_f) ** 2 for f in factors) / 2)
    assert cv["within_session_cv"] == pytest.approx(sd_f / mean_f, rel=1e-9)             # the three trial windows were written at 1.05, 0.97 and 1.00 of the median
    series = [world.time(host, r, suite, sf, "q01", engine) for r in range(1, 9)]
    m = sum(series) / 8
    between = [math.sqrt(sum((x - m) ** 2 for x in series) / 7) / m]
    series3 = [world.time(host, r, suite, sf, "q03", engine) for r in range(1, 9)]
    m3 = sum(series3) / 8
    between.append(math.sqrt(sum((x - m3) ** 2 for x in series3) / 7) / m3)
    assert cv["between_round_cv"] == pytest.approx(sum(between) / 2, rel=1e-9) and cv["n_rounds"] == 8


def test_each_launch_of_a_host_is_a_sensitivity_of_the_pooled_estimate(tmp_path):
    world = F.World()
    builder = F.TreeBuilder(tmp_path / "tree", world.truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    builder.add_launch("h100", 2, [5, 6, 7, 8])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    builder.add_launch("c-gcp", 2, [5, 6, 7, 8])
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    assert sorted(stats["sensitivity"]) == ["launch:c-gcp-l1", "launch:c-gcp-l2", "launch:h100-l1", "launch:h100-l2"]
    first = stats["sensitivity"]["launch:c-gcp-l1"]["x1"]["h100"]["baseline"]["sf30"]["energy_total"]            # the CPU-only host varied as the reference
    want_first = Oracle(world, {"h100": list(range(1, 9)), "c-gcp": [1, 2, 3, 4]}).contrast("x1", "h100", "energy_total", "baseline", "30")
    assert first["rounds"] == [1, 2, 3, 4] and first["basis"] == "sensitivity_launch" and first["estimate"] == pytest.approx(want_first["estimate"], rel=1e-5)
    second = stats["sensitivity"]["launch:h100-l2"]["x1"]["h100"]["baseline"]["sf30"]["energy_total"]
    want = Oracle(world, {"h100": [5, 6, 7, 8], "c-gcp": list(range(1, 9))}).contrast("x1", "h100", "energy_total", "baseline", "30")
    assert second["rounds"] == [5, 6, 7, 8] and second["df"] == 3 and second["basis"] == "sensitivity_launch"
    assert second["estimate"] == pytest.approx(want["estimate"], rel=1e-5) and second["ci_low"] == pytest.approx(want["ci_low"], rel=1e-4)
    assert "q" not in second and "totals" not in second                      # the sensitivities carry the suite-level record only


def test_partial_trees_do_not_crash_and_say_what_they_cannot_estimate(tmp_path):
    builder = F.TreeBuilder(tmp_path / "gpu-only", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    stats = A.run_analysis(tmp_path / "gpu-only", tmp_path / "o1", unblind=True, allow_synthetic=True)
    energy = stats["x3"]["h100"]["baseline"]["sf30"]["energy_total"]
    assert energy["class"] == "not_estimable" and energy["reason"] == "c-gcp not in the tree" and stats["x3"]["h100"]["baseline"]["sf30"]["time"]["estimate"] is not None
    assert stats["x3"]["h100"]["baseline"]["sf30"]["cost_b3_p_new"]["estimate"] is not None and stats["delta_star"] == {} and stats["x5"] == {}
    assert stats["_meta"]["hosts_not_run"] == ["a100", "c-gcp", "c-vm"]
    builder = F.TreeBuilder(tmp_path / "cpu-only", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    builder.add_launch("c-vm", 1, [1, 2])
    stats = A.run_analysis(tmp_path / "cpu-only", tmp_path / "o2", unblind=True, allow_synthetic=True)
    assert stats["x3"] == {} and stats["c_vm_check"]["status"] in ("pass", "fail", "not_evaluable") and stats["totals"]["c-gcp"]["polars"]["baseline"]["sf30"]["energy_wh"]["mean"] > 0


# ---- more ways a cell can be missing ---------------------------------------------------------------------------------------------------------

def test_a_flagged_sampler_value_inside_a_run_makes_that_cells_cpu_energy_invalid_and_nothing_else(tmp_path):
    _, root = small_tree(tmp_path, hosts=("c-gcp",))
    campaign = tmp_path / "tree" / "c-gcp" / "launch1" / "rerun" / "paper3-c-gcp-l1" / "round-1" / "baseline"
    results = json.loads((campaign / "sf30" / "polars" / "results.json").read_text())
    window = next(r for r in results["records"] if r["phase"] == "measured" and r["query"] == "q01" and r["trial"] == 2)["energy_window"]
    end_ns = window["runs"][0]["timed_end_monotonic_ns"]
    lines = (campaign / "power.csv").read_text().splitlines()
    header = lines[0].split(",")
    flagged = []
    for line in lines[1:]:
        cells = line.split(",")
        if int(cells[header.index("monotonic_ns")]) == end_ns:
            cells[header.index("flag")] = "decrease"
        flagged.append(",".join(cells))
    (campaign / "power.csv").write_text("\n".join([lines[0], *flagged]) + "\n")
    tree, store = load_small(root)
    bad = find(store, "c-gcp", 1, "baseline", "30", "q01", "polars")
    assert bad.time_s is not None and bad.energy["S2-pinned"].total_j is None and bad.energy["S2-pinned"].total_reason == "energy_invalid"
    assert find(store, "c-gcp", 1, "baseline", "30", "q03", "polars").energy["S2-pinned"].total_j is not None
    assert find(store, "c-gcp", 1, "baseline", "30", "q01", "spark_cpu").energy["S2-pinned"].total_j is not None


def test_a_missing_idle_window_drops_above_idle_and_keeps_the_total(tmp_path):
    _, root = small_tree(tmp_path, hosts=("h100",))
    edit_results(root, "h100", 1, "baseline", "30", "polars_gpu", lambda data: data["idle_windows"].pop())
    tree, store = load_small(root)
    cell = find(store, "h100", 1, "baseline", "30", "q01", "polars_gpu").energy["S1-counter"]
    assert cell.total_j is not None and cell.above_j is None and cell.above_reason == "idle_window_missing"


def test_a_query_with_fewer_than_three_trial_windows_has_no_value(tmp_path):
    def drop_a_trial(data):
        data["records"] = [r for r in data["records"] if not (r["phase"] == "measured" and r["query"] == "q01" and r["trial"] == 3)]
    _, root = small_tree(tmp_path, hosts=("h100",))
    edit_results(root, "h100", 1, "baseline", "30", "spark_gpu", drop_a_trial)
    tree, store = load_small(root)
    cell = find(store, "h100", 1, "baseline", "30", "q01", "spark_gpu")
    assert cell.time_s is None and cell.time_reason == "incomplete_trials" and cell.energy == {}
    assert find(store, "h100", 1, "baseline", "30", "q03", "spark_gpu").time_s is not None


def test_a_session_that_did_not_complete_has_no_values_and_keeps_its_class(tmp_path):
    _, root = small_tree(tmp_path, hosts=("h100",))
    path = root / "h100" / "launch1" / "rerun" / "paper3-h100-l1" / "round-1" / "baseline" / "schedule.json"
    schedule = json.loads(path.read_text())
    for item in schedule:
        if item["engine"] == "spark_cpu" and item["scale"] == "30":
            item.update(status="failed", returncode=1)
    path.write_text(json.dumps(schedule))
    tree, store = load_small(root)
    cell = find(store, "h100", 1, "baseline", "30", "q01", "spark_cpu")
    assert cell.time_s is None and cell.session_failed and cell.coverage is None and cell.failure_class == "unclassified"
    assert cell.time_reason == "session_failed:unclassified:completed" and cell.energy == {}           # an integrity failure: the engine "ran", no engine replaces it


def test_every_statistic_the_manuscript_cites_is_in_stats_json(big):
    """The contract with paper3/main.tex: paper3/tools/stats_json_to_tex.py scans the paper for \\stat keys and checks them against stats.json."""
    tool = REPO / "paper3" / "tools" / "stats_json_to_tex.py"
    if not tool.is_file():
        pytest.skip("no manuscript converter in this checkout")
    import subprocess
    done = subprocess.run([sys.executable, str(tool), "--stats", str(big.out / "stats.json"), "--check"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# ---- the independent review's findings (A1 item 2(c), 5.1, 5.3, 3.1, 2.4) ----------------------------------------------------------------------

def test_a_lost_launch_is_never_pooled_with_its_relaunch_when_no_square_is_complete(tmp_path):
    """A lost launch (rounds 1-2) and its relaunch (rounds 1-3, lost again): the host is described from the relaunch's rounds only; the lost launch is a sensitivity."""
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2], created="2026-11-24T00:00:00+00:00", launch_name="launch1", state="stopped")
    builder.add_launch("h100", 1, [1, 2, 3], created="2026-11-25T00:00:00+00:00", launch_name="launch1-relaunch", state="stopped")
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    host = stats["hosts"]["h100"]
    assert host["status"] == "descriptive_no_complete_square" and host["analysis_rounds"] == [1, 2, 3]
    assert host["sensitivity_by_instance"] == {"h100-l1": [1, 2]}
    record = stats["x3"]["h100"]["baseline"]["sf30"]["time"]
    assert record["basis"] == "descriptive_no_complete_square" and record["rounds"] == [1, 2, 3]
    lost = stats["sensitivity"]["incomplete:h100-l1"]["x3"]["h100"]["baseline"]["sf30"]["time"]
    assert lost["rounds"] == [1, 2] and lost["basis"] == "sensitivity_incomplete_squares"
    cells = [c for c in cells_mod.build_cells(tree_mod.load_tree(tmp_path / "tree"), allow_synthetic=True).cells if c.host == "h100" and c.round in (1, 2)]
    assert {c.instance for c in cells} == {"h100-l1", "h100-l1r2"}


def test_the_cpu_only_host_has_incomplete_square_and_per_instance_sensitivities_too(tmp_path):
    world = F.World()
    builder = F.TreeBuilder(tmp_path / "tree", world.truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    builder.add_launch("c-gcp", 2, [5, 6])                                    # the second square of C-GCP is incomplete
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    assert stats["hosts"]["c-gcp"]["sensitivity_by_instance"] == {"c-gcp-l2": [5, 6]}
    record = stats["sensitivity"]["incomplete:c-gcp-l2"]["x3"]["h100"]["baseline"]["sf30"]["energy_total"]
    assert record["basis"] == "sensitivity_incomplete_squares" and record["rounds"] == []                   # H100 has rounds 1-4, C-GCP's incomplete rounds are 5-6


def test_a_cross_host_cell_without_a_common_round_still_has_an_unpaired_welch_estimate_labelled_as_such(tmp_path):
    world = F.World()
    builder = F.TreeBuilder(tmp_path / "tree", world.truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])                        # H100: the first square
    builder.add_launch("c-gcp", 2, [5, 6, 7, 8])                       # C-GCP: the second square only
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    paired = stats["x3"]["h100"]["baseline"]["sf30"]["energy_total"]
    assert paired["class"] == "not_estimable" and paired["n_rounds"] == 0
    unpaired = stats["sensitivity_unpaired"]["x3"]["h100"]["baseline"]["sf30"]["energy_total"]
    assert unpaired["pairing"] == "unpaired_welch" and unpaired["basis"] == "confirmatory" and (unpaired["n_rounds_ref_side"], unpaired["n_rounds_eng_side"]) == (4, 4)
    o = Oracle(world, {"h100": [1, 2, 3, 4], "c-gcp": [5, 6, 7, 8]})
    ref = {r: math.exp(sum(math.log(min(o.energy("c-gcp", r, "baseline", "30", q, e) for e in F.CPU_ENGINES)) for q in F.QUERIES["baseline"]) / 2) for r in (5, 6, 7, 8)}
    best_gpu = {r: math.exp(sum(math.log(min(o.energy("h100", r, "baseline", "30", q, e) for e in F.GPU_ENGINES)) for q in F.QUERIES["baseline"]) / 2) for r in (1, 2, 3, 4)}
    expected = E.welch_record(ref, best_gpu)                                            # ratio of geometric means CPU side / GPU side
    assert unpaired["estimate"] == pytest.approx(expected["estimate"], rel=1e-5) and unpaired["df"] == pytest.approx(expected["df"], rel=1e-4)
    assert unpaired["class"] == E.classify({**expected})


def test_the_unpaired_welch_ratio_does_not_depend_on_which_round_numbers_the_two_hosts_happen_to_share(tmp_path):
    class Wobble(F.World):
        def time(self, host, round_, suite, scale, query, engine):
            base = super().time(host, round_, suite, scale, query, engine)
            return base * (math.exp(0.06 * math.sin(round_ * 2.1)) if host == "c-gcp" else 1.0)
    outputs = {}
    for name, order in (("same", [1, 2, 3, 4]), ("shuffled", [3, 1, 4, 2])):
        class Permuted(Wobble):
            def time(self, host, round_, suite, scale, query, engine, order=order):
                return super().time(host, order[round_ - 1] if host == "c-gcp" else round_, suite, scale, query, engine)
        builder = F.TreeBuilder(tmp_path / name / "tree", Permuted().truth(), grid={"baseline": ("30",), "complex": ("30",)})
        builder.add_launch("h100", 1, [1, 2, 3, 4])
        builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
        stats = A.run_analysis(tmp_path / name / "tree", tmp_path / name / "out", unblind=True, allow_synthetic=True)
        outputs[name] = (stats["x3"]["h100"]["baseline"]["sf30"]["time_vs_cgcp"], stats["sensitivity_unpaired"]["x3"]["h100"]["baseline"]["sf30"]["time_vs_cgcp"])
    paired_same, unpaired_same = outputs["same"]
    paired_shuffled, unpaired_shuffled = outputs["shuffled"]
    assert unpaired_same["estimate"] == pytest.approx(unpaired_shuffled["estimate"], rel=1e-12)
    assert (unpaired_same["ci_low"], unpaired_same["ci_high"]) == pytest.approx((unpaired_shuffled["ci_low"], unpaired_shuffled["ci_high"]), rel=1e-12)       # the matching does not matter here
    assert paired_same["estimate"] == pytest.approx(paired_shuffled["estimate"], rel=1e-12)
    assert (paired_same["ci_low"], paired_same["ci_high"]) != pytest.approx((paired_shuffled["ci_low"], paired_shuffled["ci_high"]), rel=1e-6)      # the paired interval does


def test_x3_does_not_swap_in_another_engine_when_an_engine_that_ran_the_query_has_no_value(tmp_path):
    """polars_gpu (the lower-energy GPU engine) is below resolution at SF30 while spark_gpu is not: X3 energy is not estimable there, the lenient diagnostic is."""
    def hook(kind, key, default):
        if kind == "cell" and key[0] == "baseline" and key[1] == "30" and key[2] == "polars_gpu":
            return {**default, "outcome": "fail", "reasons": ["per_run_below_t_min"]}
        return default
    _, root = small_tree(tmp_path, hooks={"h100": hook})
    stats = A.run_analysis(root, tmp_path / "out", unblind=True, allow_synthetic=True)
    cell = stats["x3"]["h100"]["baseline"]["sf30"]
    assert cell["energy_total"]["class"] == "not_estimable" and cell["energy_total"]["n_rounds"] == 0
    assert stats["x3_strict"]["h100"]["baseline"]["sf30"]["energy_total"]["class"] == "not_estimable"
    assert cell["time"]["estimate"] is not None                                    # time and cost are unaffected by the resolution flag
    lenient = stats["x3_lenient"]["h100"]["baseline"]["sf30"]["energy_total"]
    assert lenient["estimate"] is not None and lenient["n_rounds"] == 4            # the diagnostic picks spark_gpu silently; it is labelled as a diagnostic key
    assert stats["x1"]["h100"]["baseline"]["sf30"]["energy_total"]["class"] == "not_estimable"
    assert stats["x2"]["h100"]["baseline"]["sf30"]["energy_total"]["estimate"] is not None


def test_an_engine_that_did_not_run_a_query_is_left_out_but_one_that_ran_without_a_value_blocks_the_pair(big):
    """cx_q13: polars_gpu has no time (coverage outcome, did not run) so X3 uses spark_gpu; at the H100's SF1 energy both ran and neither has a value."""
    cell = big.stats["x3"]["h100"]["complex"]["sf100"]["energy_total"]
    assert "cx_q13" in cell["queries"]
    assert big.stats["x3"]["h100"]["baseline"]["sf1"]["energy_total"]["class"] == "not_estimable"


def test_the_break_even_is_not_determinable_when_the_top_of_the_grid_was_never_measured(tmp_path):
    """polars_gpu runs out of device memory at SF300 in every round: X1 at SF300 has no estimate, so the break-even is not ">SF300"."""
    fails = {("h100", r, s, "300", q, "polars_gpu"): ("RuntimeError", "RMM failure: out of memory") for r in range(1, 5) for s, qs in F.QUERIES.items() for q in qs}
    _, root = small_tree(tmp_path, world=F.World(failures=fails), grid={"baseline": ("30", "100", "300"), "complex": ("30", "100", "300")})
    stats = A.run_analysis(root, tmp_path / "out", unblind=True, allow_synthetic=True)
    be = stats["break_even"]["x1"]["h100"]["baseline"]["cost_b3_p_new"]
    assert be["classes"] == {"sf30": "gpu_better", "sf100": "gpu_better", "sf300": "not_estimable"}
    assert be["outcome"] == "not_determinable" and be["text"] == "not determinable (SF300 not estimable)" and be["censored"] is True
    assert stats["break_even"]["x2"]["h100"]["baseline"]["cost_b3_p_new"]["text"] == "<= SF30"           # spark_gpu ran everywhere
    assert stats["counts"]["h100"]["coverage_outcomes"]["resource_exhaustion"] == 4 * 2 * 2          # 4 rounds x (baseline + complex) x 2 queries at SF300


def test_launches_that_ran_different_instance_shapes_are_never_pooled(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4], instance_type="c3-highmem-192-metal")
    builder.add_launch("c-gcp", 2, [5, 6, 7, 8], instance_type="c3-standard-192-metal", price=9.677184)
    with pytest.raises(tree_mod.AnalysisError, match="different instance types.*never pooled"):
        tree_mod.load_tree(tmp_path / "tree")


def test_a_lost_launch_of_another_shape_is_a_sensitivity_not_a_refusal(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)})
    builder.add_launch("c-gcp", 1, [1, 2], instance_type="c3-standard-192-metal", price=9.677184, created="2026-11-24T00:00:00+00:00", launch_name="l1")
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4], instance_type="c3-highmem-192-metal", created="2026-11-24T12:00:00+00:00", launch_name="l1b")
    tree = tree_mod.load_tree(tmp_path / "tree")
    assert [i.instance_type for i in tree.hosts["c-gcp"].analysis_instances] == ["c3-highmem-192-metal"]


def test_the_records_of_one_launch_must_agree_on_the_instance_type_that_ran(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30",), "complex": ("30",)}, )
    root = builder.add_launch("h100", 1, [1, 2, 3, 4], record_extra={"instance_type": "gpu_1x_a100_sxm4"})
    with pytest.raises(tree_mod.AnalysisError, match="records name different instance types"):
        tree_mod.load_tree(tmp_path / "tree")


@pytest.mark.parametrize("change", [lambda w: w.pop("scope_tag"), lambda w: w.update(scope_tag="S2-pinned"), lambda w: w.update(scope_tag="S1-module"),
                                    lambda w: w.update(scope_tag="none")])
def test_an_idle_window_energy_value_carries_the_right_scope_tag_or_raises(tmp_path, change):
    _, root = small_tree(tmp_path, hosts=("h100",))
    edit_results(root, "h100", 1, "baseline", "30", "polars_gpu", lambda data: change(data["idle_windows"][1]["gpu_energy"]))
    with pytest.raises(cells_mod.UntaggedEnergyError, match="idle window"):
        load_small(root)


def test_a_cell_with_measured_windows_and_no_g9_verdict_is_an_error(tmp_path):
    _, root = small_tree(tmp_path, hosts=("h100",))
    path = root / "h100" / "launch1" / "rerun" / "paper3-h100-l1" / "round-1" / "paper3-gates.json"
    report = json.loads(path.read_text())
    report["runs"][0]["cells"] = [c for c in report["runs"][0]["cells"] if not (c["scale"] == "30" and c["engine"] == "polars_gpu" and c["query"] == "q01")]
    path.write_text(json.dumps(report))
    with pytest.raises(tree_mod.AnalysisError, match="no G9 verdict"):
        load_small(root)


@pytest.mark.parametrize("target", ["report", "campaign"])
def test_a_t_min_other_than_the_pre_registered_one_is_refused(tmp_path, target):
    _, root = small_tree(tmp_path, hosts=("a100",))                      # paper3_hosts.json: the A100's t_min_s is 2.0
    campaign_root = root / "a100" / "launch1" / "rerun" / "paper3-a100-l1"
    path = campaign_root / "round-1" / "paper3-gates.json" if target == "report" else campaign_root / "campaign.json"
    data = json.loads(path.read_text())
    (data if target == "report" else data["design"]["paper3"])["t_min_s"] = 1.0
    path.write_text(json.dumps(data))
    with pytest.raises(tree_mod.AnalysisError, match="t_min_s 1.0 but paper3_hosts.json has 2.0"):
        load_small(root)


def test_every_estimate_carries_the_basis_it_rests_on(tmp_path):
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("30", "300"), "complex": ("30",)})
    builder.add_launch("a100", 1, [1, 2, 3])                      # no complete square: descriptive
    builder.add_launch("h100", 1, [1, 2, 3, 4])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    stats = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)
    for host, basis in (("h100", "confirmatory"), ("a100", "descriptive_no_complete_square")):
        assert stats["break_even"]["x3"][host]["baseline"]["energy_total"]["basis"] == basis
        assert stats["delta_star"]["x3"][host]["baseline"]["sf30"]["energy_total"]["basis"] == basis
        assert stats["x5"][host]["polars"]["baseline"]["sf30"]["time"]["basis"] == basis
        assert stats["totals"][host]["polars_gpu"]["baseline"]["sf30"]["time_s"]["basis"] == basis
    assert stats["x4"]["a100"]["polars_gpu"]["baseline"]["sf30"]["time"]["basis"] == "descriptive_no_complete_square"
    assert stats["x4"]["a100"]["polars_gpu"]["baseline"]["sf30"]["energy_total"]["basis"] == "descriptive_no_complete_square"


def test_every_energy_total_carries_its_scope_tag(big):
    totals = big.stats["totals"]
    assert totals["h100"]["polars_gpu"]["baseline"]["sf30"]["energy_wh"]["scope_tag"] == "S1-counter"
    assert totals["h100"]["polars"]["baseline"]["sf30"]["energy_wh"]["scope_tag"] == "S1-counter"
    assert totals["c-gcp"]["polars"]["baseline"]["sf30"]["energy_wh"]["scope_tag"] == "S2-pinned"
    assert totals["c-gcp"]["polars"]["baseline"]["sf30"]["energy_s2all_wh"]["scope_tag"] == "S2-all"
    assert totals["h100"]["polars_gpu"]["baseline"]["sf30"]["time_s"]["scope_tag"] is None
    rows = read_csv(big.out / "tables" / "t09_totals.csv")
    assert {r["scope_tag"] for r in rows if r["metric"].startswith("energy")} == {"S1-counter", "S2-pinned", "S2-all"}


def test_the_scalars_the_paper_cites_are_flat_keys(big):
    c = big.stats["counts"]
    assert c["h100"]["launches_planned"] == 2 and c["h100"]["launches_found"] == 2 and c["h100"]["launches_lost_replaced"] == 0
    assert c["h100"]["rounds_confirmatory"] == 8 and c["h100"]["rounds_accepted"] == 8 and c["h100"]["rounds_planned"] == 8
    assert c["h100"]["round_attempts"] == 9 and c["h100"]["round_repeats"] == 1 and c["h100"]["round_attempts_failed"] == 1       # round 2 failed once and was repeated
    assert c["a100"]["rounds_confirmatory"] == 4 and c["a100"]["squares_complete"] == 1 and c["a100"]["squares_planned"] == 2 and c["a100"]["launches_found"] == 1
    assert c["h100"]["sessions_analysed"] == 256 and c["c-gcp"]["sessions_analysed"] == 128 and c["c-vm"]["sessions_analysed"] == 12
    assert c["h100"]["g9"]["cells_below_resolution"] == 8 * 4 * 2 and c["h100"]["g9"]["window_below_10s"] == 8 * 4 * 2          # baseline SF1: 4 engines x 2 queries x 8 rounds
    assert c["h100"]["g9"]["by_engine"]["polars_gpu"] == {"cells": 8 * 16, "below_resolution": 16}
    assert c["h100"]["coverage_outcomes"]["unsupported_operation"] == 8 * 3 and c["h100"]["coverage_outcomes"]["resource_exhaustion"] == 0         # polars_gpu cx_q13, 3 scales x 8 rounds
    assert c["c-gcp"]["coverage_outcomes"]["total"] == 0 and c["h100"]["cells_analysed"] == 8 * 4 * 8 * 2
    b = big.stats["billing_by_host"]
    assert b["h100"]["instances"] == 2 and b["h100"]["hours"] == pytest.approx(48.0) and b["h100"]["billed_usd"] == pytest.approx(48.0 * 4.95)
    assert b["h100"]["invoice_usd"] is None and b["a100"]["billed_usd"] == pytest.approx(24.0 * 2.10)
    flat = json.loads((big.out / "stats_flat.json").read_text())
    assert flat["counts.h100.rounds_confirmatory"] == 8 and flat["billing_by_host.h100.hours"] == pytest.approx(48.0)
    assert "counts.h100.rounds_confirmatory" in (big.out / "stats.tex").read_text()


def test_the_s1_sampled_cross_check_summarises_the_relative_difference_on_eligible_windows(tmp_path):
    """The stream shows the counter's power in every run, scaled by 1.02: the trapezoid reads 2% above the counter, on windows of 10 s or more of clean sessions."""
    def hook(kind, key, default):
        if kind == "session" and key[2] == "spark_gpu" and key[3] == "G8":
            return {"outcome": "fail", "problems": ["x"], "method": "windows", "median_lag_s": 0.6, "drift_s": 0.3}        # a session whose gpu.csv values are disabled
        return default
    world = F.World(time_overrides={("h100", r, s, "1", q, e): 4.0 for r in (1, 2) for s in ("baseline",) for q in F.QUERIES["baseline"] for e in F.ENGINES})
    builder = F.TreeBuilder(tmp_path / "tree", world.truth(), grid={"baseline": ("1",), "complex": ("30",)}, gpu_stream=True, stream_scale=1.02)
    for r in (1, 2):
        builder.gate_hooks[("h100", r)] = hook
    builder.add_launch("h100", 1, [1, 2])
    tree = tree_mod.load_tree(tmp_path / "tree")
    store = cells_mod.build_cells(tree, allow_synthetic=True)
    summary = qa_mod.crosscheck_summary(store, {"h100": {1: "h100-l1", 2: "h100-l1"}})["h100"]
    assert summary["field"] == "power.draw.instant" and summary["n_sessions"] > 0 and summary["n_windows"] > 0
    assert summary["median"] == pytest.approx(0.02, abs=2e-3) and summary["min"] == pytest.approx(0.02, abs=5e-3) and summary["max"] == pytest.approx(0.02, abs=5e-3)
    engines = {engine for _, _, engine, _ in store.crosscheck["h100"]["sessions"]}
    assert "spark_gpu" not in engines and {"polars", "polars_gpu", "spark_cpu"} <= engines            # the G8-flagged session is not eligible


# ---- the synthetic recovery run (design 5.4), the code manifest, and the committed smoke evidence ------------------------------------------------

def test_the_recovery_run_recovers_the_injected_effects_and_writes_a_machine_readable_report(tmp_path):
    assert A.main(["--recovery", str(tmp_path / "rec"), "--replicates", "2"]) == 0
    report = json.loads((tmp_path / "rec" / "recovery.json").read_text())
    assert report["all_passed"] is True and report["_meta"]["synthetic"] is True and report["_meta"]["seed"] == 20261123
    assert report["_meta"]["analysis_code_sha256"] == A.code_sha256()
    assert {c["name"] for c in report["checks"]} >= {"estimates_are_within_sampling_error_of_the_truth", "no_class_contradicts_the_true_ratio_where_it_is_clear",
                                                      "no_break_even_bracket_is_wrong_where_every_scale_is_clear", "replicated_interval_coverage_is_near_95_percent",
                                                      "t_interval_coverage_R8", "t_interval_coverage_R4", "welch_interval_coverage_4_vs_8"}
    first = report["estimates"][0]
    assert {"estimand", "key", "true", "estimate", "ci_low", "ci_high", "covers", "z", "rel_error"} <= set(first)
    assert report["pipeline"]["n_intervals"] > 1000 and report["pipeline"]["classification"]["disagree"] == [] and report["pipeline"]["break_even"]["wrong"] == []
    assert {"x1", "x2", "x3", "x3_strict", "x4", "x5", "x6_idle_gpu_wh", "delta_star", "totals"} <= {r["estimand"] for r in report["estimates"]}
    assert A.main(["--recovery", str(tmp_path / "rec2"), "--replicates", "2"]) == 0
    assert (tmp_path / "rec" / "recovery.json").read_bytes() == (tmp_path / "rec2" / "recovery.json").read_bytes()         # one seed, one report


def test_the_recovery_run_fails_when_the_analysis_is_wrong(tmp_path):
    """A pipeline that biases every X1-X3 estimate by 5% is caught: the recovery is a test of the code, not a formality."""
    from paper3_analysis import recovery

    def biased(*args, **kwargs):
        stats = A.run_analysis(*args, **kwargs)
        for cname in ("x1", "x2", "x3", "x3_strict"):
            for by_suite in stats[cname].values():
                for by_scale in by_suite.values():
                    for by_metric in by_scale.values():
                        for record in by_metric.values():
                            for key in ("estimate", "ci_low", "ci_high"):
                                if record.get(key) is not None:
                                    record[key] *= 1.05
        return stats
    report = recovery.run_recovery(tmp_path / "bad", run_analysis=biased, replicates=0, estimator_repetitions=200, code_sha256="x")
    failed = {c["name"] for c in report["checks"] if not c["passed"]}
    assert report["all_passed"] is False and "estimates_are_within_sampling_error_of_the_truth" in failed


def test_the_manifest_lists_the_exact_files_of_the_analysis_code_and_their_combined_hash(capsys):
    assert A.main(["--manifest"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    files = list(manifest["analysis_code_files"])
    assert files[0] == "analyze_paper3.py" and files[1:] == sorted(files[1:]) and "paper3_analysis/synthetic.py" in files and "paper3_analysis/recovery.py" in files
    import hashlib
    digest = hashlib.sha256()
    for name in files:
        digest.update(Path(name).name.encode() + b"\0" + (REPO / name).read_bytes() + b"\0")
    assert manifest["analysis_code_sha256"] == digest.hexdigest() == A.code_sha256()
    assert {"power_telemetry.py", "paper3_gates.py", "preflight.py", "analyze_campaign.py", "paper3_hosts.json"} <= set(manifest["imported_not_owned"])


SMOKE = REPO / "docs" / "paper3-evidence" / "smoke"


@pytest.mark.skipif(not SMOKE.is_dir(), reason="the committed smoke evidence is not in this checkout")
@pytest.mark.parametrize("stamp", ["h100-20261122T213531Z", "h100-20261123T031358Z"])
def test_the_reader_reads_the_committed_smoke_evidence_of_a_real_h100(tmp_path, stamp):
    """Format check against harness output on a real H100 (synthetic queries, docs/paper3-evidence/smoke, public, not study data): nothing is asserted about values."""
    evidence = SMOKE / stamp
    if not (evidence / "run" / "schedule.json").is_file():
        pytest.skip("run directory not in the evidence")
    import shutil
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("1",), "complex": ("30",)})
    root = builder.add_launch("h100", 1, [1])                                   # scaffolding: campaign.json, status.json, launch-record.json
    shutil.rmtree(root / "round-1")
    attempt = root / "round-1"
    (attempt / "baseline").mkdir(parents=True)
    for name in ("schedule.json", "power_sources.json", "environment.json"):
        if (evidence / "run" / name).exists():
            shutil.copy(evidence / "run" / name, attempt / "baseline" / name)
    shutil.copytree(evidence / "run" / "sf1", attempt / "baseline" / "sf1", ignore=shutil.ignore_patterns("plans", "*.log", "*.txt"))
    report = json.loads((evidence / "gates.json").read_text())
    for run in report["runs"]:                                                   # the smoke run's suite is named "smoke"; the campaign's is "baseline"
        run["suite"] = "baseline"
        for entry in run["sessions"] + run["cells"]:
            entry["suite"] = "baseline"
    (attempt / "paper3-gates.json").write_text(json.dumps(report))
    (attempt / "gates.json").write_text(json.dumps({"verdict": "pass"}))
    for path in attempt.rglob("results.json"):
        data = json.loads(path.read_text())
        data.pop("synthetic_smoke", None)
        path.write_text(json.dumps(data))
    campaign = json.loads((root / "campaign.json").read_text())
    campaign["design"]["suites"] = {"baseline": {"scales": ["1"]}}
    (root / "campaign.json").write_text(json.dumps(campaign))
    tree = tree_mod.load_tree(tmp_path / "tree")
    store = cells_mod.build_cells(tree, allow_synthetic=True)
    assert {c.engine for c in store.cells} == set(F.ENGINES) and len(store.cells) == 12
    assert all(c.time_s is not None and c.coverage is None for c in store.cells)
    for cell in store.cells:
        series = cell.energy["S1-counter"]
        assert series.idle_w is not None and series.tag == "S1-counter" and cell.n_trials == 3 and len(series.trial_j) == 3 and all(isinstance(j, float) for j in series.trial_j)
        assert series.total_reason in (None, "below_resolution")                  # the smoke queries are tiny: G9 says so, and the energy is then not a number
    assert {(h, e) for (h, e, s) in store.idle if s == "S1-counter"} == {("h100", e) for e in F.ENGINES}
    assert store.latencies and all(v["read_latency_ms"] for v in store.latencies.values())


@pytest.mark.skipif(not SMOKE.is_dir(), reason="the committed smoke evidence is not in this checkout")
def test_the_reader_handles_the_smoke_run_whose_gpu_sessions_failed_on_a_cuda_fault(tmp_path):
    evidence = SMOKE / "h100-20261122T232520Z"
    if not (evidence / "run" / "schedule.json").is_file():
        pytest.skip("run directory not in the evidence")
    import shutil
    builder = F.TreeBuilder(tmp_path / "tree", F.World().truth(), grid={"baseline": ("1",), "complex": ("30",)})
    root = builder.add_launch("h100", 1, [1])
    shutil.rmtree(root / "round-1")
    attempt = root / "round-1"
    (attempt / "baseline").mkdir(parents=True)
    for name in ("schedule.json", "power_sources.json", "environment.json"):
        if (evidence / "run" / name).exists():
            shutil.copy(evidence / "run" / name, attempt / "baseline" / name)
    shutil.copytree(evidence / "run" / "sf1", attempt / "baseline" / "sf1", ignore=shutil.ignore_patterns("plans", "*.txt"))
    report = json.loads((evidence / "gates.json").read_text())
    for run in report["runs"]:                                                   # the smoke run's suite is named "smoke"; the campaign's is "baseline"
        run["suite"] = "baseline"
        for entry in run["sessions"] + run["cells"]:
            entry["suite"] = "baseline"
    (attempt / "paper3-gates.json").write_text(json.dumps(report))
    (attempt / "gates.json").write_text(json.dumps({"verdict": "pass"}))
    for path in attempt.rglob("results.json"):
        data = json.loads(path.read_text())
        data.pop("synthetic_smoke", None)
        path.write_text(json.dumps(data))
    campaign = json.loads((root / "campaign.json").read_text())
    campaign["design"]["suites"] = {"baseline": {"scales": ["1"]}}
    (root / "campaign.json").write_text(json.dumps(campaign))
    tree = tree_mod.load_tree(tmp_path / "tree")
    store = cells_mod.build_cells(tree, allow_synthetic=True)
    failed = [s for s in store.sessions if s.status != "complete"]
    assert {s.engine for s in failed} == {"spark_gpu", "polars_gpu"} and all(s.failure_class for s in failed)
    assert all(c.time_s is None and c.session_failed for c in store.cells if c.engine in ("spark_gpu", "polars_gpu"))
    assert all(c.time_s is not None for c in store.cells if c.engine in ("polars", "spark_cpu"))
