"""Paper 3 campaign schedule (paper3_campaign.py): seed rule, Williams squares, orders, hosts and the lock checks.

The expectations are written from docs/PAPER3_ENERGY_DESIGN.md section 3.2, not read from the module: the seed rule is
restated here in three lines and every order below is a literal. A change to the design is a change to this file.
"""
from __future__ import annotations

from datetime import date
import hashlib
import itertools
from pathlib import Path
import random
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import paper3_campaign as pc  # noqa: E402

BASE = ["polars", "polars_gpu", "spark_cpu", "spark_gpu"]
ROWS = ["ABDC", "BCAD", "CDBA", "DACB"]
ENGINES_OF = {"A": "spark_cpu", "B": "polars_gpu", "C": "polars", "D": "spark_gpu"}   # Paper 1 seed 20261029


def letters_by_the_design(seed):
    """The seed rule of section 3.2 in its own words, independent of the module."""
    engines = ["polars", "polars_gpu", "spark_cpu", "spark_gpu"]
    random.Random(seed).shuffle(engines)
    return dict(zip("ABCD", engines))


# ---- seed rule ------------------------------------------------------------------------------------------------------

def test_paper1_seed_gives_paper1_letters():
    assert pc.derive_letters(20261029) == ENGINES_OF


@pytest.mark.parametrize("seed", [20261116, 20261117, 20261120, 20261201, 20270101, 1, 0])
def test_letters_follow_the_seed_rule(seed):
    assert pc.derive_letters(seed) == letters_by_the_design(seed)
    assert sorted(pc.derive_letters(seed).values()) == BASE


def test_golden_letters_and_orders_for_a_lock_on_2026_11_16():
    """Literal expectations for one plausible lock date, as a reviewer would write them down from the design."""
    plan = pc.build_plan("h100", "20261116")
    assert plan.seed == 20261116
    assert plan.letters == {"A": "spark_cpu", "B": "spark_gpu", "C": "polars", "D": "polars_gpu"}
    sc, sg, po, pg = "spark_cpu", "spark_gpu", "polars", "polars_gpu"
    expected = {1: [sc, sg, pg, po],       # A B D C
                2: [sg, po, sc, pg],       # B C A D
                3: [po, pg, sg, sc],       # C D B A
                4: [pg, sc, po, sg],       # D A C B
                5: [pg, sc, po, sg],       # row 4
                6: [po, pg, sg, sc],       # row 3
                7: [sg, po, sc, pg],       # row 2
                8: [sc, sg, pg, po]}       # row 1
    assert {r.round: list(r.engine_order) for r in plan.rounds} == expected


def test_golden_orders_for_the_paper1_seed():
    plan = pc.build_plan("a100", "20261029")
    sc, pg, po, sg = "spark_cpu", "polars_gpu", "polars", "spark_gpu"
    assert {r.round: list(r.engine_order) for r in plan.rounds} == {
        1: [sc, pg, sg, po], 2: [pg, po, sc, sg], 3: [po, sg, pg, sc], 4: [sg, sc, po, pg],
        5: [sg, sc, po, pg], 6: [po, sg, pg, sc], 7: [pg, po, sc, sg], 8: [sc, pg, sg, po]}


@pytest.mark.parametrize("seed", [20261029, 20261116, 20261130, 20270215])
def test_rounds_5_to_8_are_the_same_rows_in_reverse_order(seed):
    plan = pc.build_plan("h100", str(seed))
    assert [r.sequence for r in plan.rounds] == ROWS + ROWS[::-1]
    assert [r.row for r in plan.rounds] == [1, 2, 3, 4, 4, 3, 2, 1]
    assert [r.square for r in plan.rounds] == [1] * 4 + [2] * 4
    letters = letters_by_the_design(seed)
    assert [list(r.engine_order) for r in plan.rounds] == [[letters[c] for c in seq] for seq in ROWS + ROWS[::-1]]


@pytest.mark.parametrize("seed", [20261029, 20261116, 20270215])
def test_each_square_is_a_williams_square_and_the_eight_rounds_pair_every_adjacency_twice(seed):
    plan = pc.build_plan("gh200", str(seed))
    for square in (1, 2):
        orders = [list(r.engine_order) for r in plan.rounds if r.square == square]
        assert all(sorted(order[p] for order in orders) == BASE for p in range(4)), "each engine once per position"
        pairs = sorted((a, b) for order in orders for a, b in zip(order, order[1:]))
        assert pairs == sorted(itertools.permutations(BASE, 2)), "each ordered adjacent pair exactly once"
    both = sorted((a, b) for r in plan.rounds for a, b in zip(r.engine_order, r.engine_order[1:]))
    assert both == sorted(pair for pair in itertools.permutations(BASE, 2) for _ in range(2))


def test_check_square_rejects_a_broken_square():
    letters = pc.derive_letters(20261029)
    pc.check_square(ROWS, letters)
    for broken in (("ABDC", "BCAD", "CDBA", "DABC"), ("ABDC", "BCAD", "CDBA", "CACB"), ("ABCD", "BCDA", "CDAB", "DABC")):
        with pytest.raises(pc.PlanError):
            pc.check_square(broken, letters)


def test_suite_order_alternates_with_odd_rounds_baseline_first():
    plan = pc.build_plan("h100", "20261116")
    assert [r.suites[0] for r in plan.rounds] == ["baseline", "complex"] * 4
    assert all(sorted(r.suites) == ["baseline", "complex"] for r in plan.rounds)


# ---- hosts ------------------------------------------------------------------------------------------------------------

def test_gpu_hosts_have_four_engines_the_full_grid_and_32_sessions_per_round():
    for host in ("a100", "h100", "gh200", "b200"):
        spec = pc.HOSTS[host]
        assert spec.kind == "gpu" and sorted(spec.engines) == BASE and spec.energy and not spec.pinned
        assert {s: list(v) for s, v in spec.suites.items()} == {"baseline": ["1", "10", "30", "100", "300"], "complex": ["30", "100", "300"]}
        assert pc.sessions_per_round(pc.build_plan(host, "20261116")) == 32


def test_declared_scopes_per_host_class():
    assert [pc.HOSTS[h].scope for h in ("a100", "h100", "gh200", "b200", "c-gcp", "c-vm")] == \
        ["board", "board", "module", None, "none", "none"]
    assert pc.HOSTS["h100"].telemetry == {"gpu_energy_scope": "board", "energy_window_s": 10.0, "idle_baseline_seconds": 0,
                                          "g8_pulse_seconds": 3.0, "g8_pulse_engines": "all"}
    assert "g8_pulse_seconds" not in pc.HOSTS["c-gcp"].telemetry and "g8_pulse_seconds" not in pc.HOSTS["c-vm"].telemetry, "no GPU, no pulse"
    assert pc.HOSTS["gh200"].telemetry["gpu_energy_scope"] == "module"
    assert pc.HOSTS["b200"].telemetry["gpu_energy_scope"] is None            # the operator declares it after the B200 semantics step


def test_the_cpu_only_host_runs_two_engines_in_the_written_orders():
    plan = pc.build_plan("c-gcp", "20261116")
    assert [r.sequence for r in plan.rounds] == "AB BA BA AB BA AB AB BA".split()
    assert plan.letters == {"A": "spark_cpu", "B": "polars"}                     # lock 20261116: spark_cpu is A in the four-engine assignment
    sc, po = "spark_cpu", "polars"
    assert [list(r.engine_order) for r in plan.rounds] == [[sc, po], [po, sc], [po, sc], [sc, po], [po, sc], [sc, po], [sc, po], [po, sc]]
    assert pc.sessions_per_round(plan) == 16 and plan.spec.pinned and plan.spec.telemetry["cpu_energy"] == "require"
    for half in (plan.rounds[:4], plan.rounds[4:]):
        assert sorted(r.engine_order[0] for r in half) == ["polars", "polars", "spark_cpu", "spark_cpu"], "each half is balanced"


@pytest.mark.parametrize("seed", [20261029, 20261116, 20261117, 20261120, 20270101, 20000101, 19991231])
def test_the_cpu_letters_are_derived_from_the_four_engine_assignment_of_the_same_seed(seed):
    """Lead's decision: the two CPU engines take A and B in the order their letters appear in the GPU-host assignment."""
    letters = letters_by_the_design(seed)
    in_letter_order = [letters[c] for c in "ABCD" if letters[c] in ("polars", "spark_cpu")]
    for host in ("c-gcp", "c-vm"):
        plan = pc.build_plan(host, str(seed))
        assert plan.letters == {"A": in_letter_order[0], "B": in_letter_order[1]}
        assert plan.all_letters == letters and plan.rounds[0].engine_order == (in_letter_order[0], in_letter_order[1])


def test_both_cpu_orders_occur_over_the_seeds_and_nothing_is_asked_of_the_operator():
    first = {pc.build_plan("c-gcp", str(20261101 + day)).letters["A"] for day in range(30)}
    assert first == {"polars", "spark_cpu"}, "the derivation is not a constant"
    with pytest.raises(TypeError):
        pc.build_plan("c-gcp", "20261116", cpu_first="polars")


def test_the_cpu_only_orders_depend_on_the_seed_only_through_the_derived_letters():
    a, b = pc.build_plan("c-gcp", "20261116"), pc.build_plan("c-gcp", "20261120")        # spark_cpu is A on the first, polars on the second
    assert a.seed != b.seed and a.letters != b.letters
    assert [r.sequence for r in a.rounds] == [r.sequence for r in b.rounds]
    assert [r.engine_order for r in a.rounds] == [tuple(reversed(r.engine_order)) for r in b.rounds]


def test_the_vm_time_check_is_baseline_sf10_sf30_sf300_two_rounds_time_only():
    plan = pc.build_plan("c-vm", "20261116")
    spec = plan.spec
    assert spec.kind == "cpu-vm" and not spec.energy and not spec.pinned and spec.engines == ("polars", "spark_cpu")
    assert {s: list(v) for s, v in spec.suites.items()} == {"baseline": ["10", "30", "300"]}
    assert len(plan.rounds) == 2 and [r.sequence for r in plan.rounds] == ["AB", "BA"]
    assert plan.letters == pc.build_plan("c-gcp", "20261116").letters and plan.spec.vm_pin and plan.spec.cooldown_s == 60.0
    assert [r.suites for r in plan.rounds] == [("baseline",), ("baseline",)]
    assert pc.sessions_per_round(plan) == 6
    assert spec.telemetry["power_telemetry"] == "off" and spec.telemetry["idle_baseline_seconds"] == 0
    assert spec.telemetry["session_idle_seconds"] == 0 and spec.telemetry["gpu_energy_scope"] == "none"
    assert [r.round for r in pc.launch_rounds(plan, "1")] == [1, 2] and [r.round for r in pc.launch_rounds(plan, "both")] == [1, 2]
    with pytest.raises(pc.PlanError, match="one launch"):
        pc.launch_rounds(plan, "2")


def test_unknown_hosts_and_launches_are_refused():
    with pytest.raises(pc.PlanError):
        pc.build_plan("h200", "20261116")
    with pytest.raises(pc.PlanError):
        pc.launch_rounds(pc.build_plan("h100", "20261116"), "3")


# ---- launches -----------------------------------------------------------------------------------------------------------

def test_launches_split_the_eight_rounds_into_the_two_squares():
    plan = pc.build_plan("h100", "20261116")
    assert [r.round for r in pc.launch_rounds(plan, "1")] == [1, 2, 3, 4]
    assert [r.round for r in pc.launch_rounds(plan, "2")] == [5, 6, 7, 8]
    assert [r.round for r in pc.launch_rounds(plan, "both")] == list(range(1, 9))
    assert pc.LAUNCHES == ("1", "2", "both")


def test_the_design_block_of_the_two_launches_shares_a_common_hash_and_differs_where_it_must():
    plan = pc.build_plan("h100", "20261116")
    lock = {"date_utc": "20261116", "seed": 20261116, "tag": pc.LOCK_TAG, "commit": "a" * 40, "zenodo_doi": "10.5281/zenodo.1"}
    kw = dict(host_config_sha256="e" * 64, t_min_s=1.0, lock=lock)
    one = pc.design_block(plan, "1", pc.launch_rounds(plan, "1"), **kw)
    two = pc.design_block(plan, "2", pc.launch_rounds(plan, "2"), **kw)
    assert one["common_sha256"] == two["common_sha256"]
    assert one != two and one["launch_rounds"] == [1, 2, 3, 4] and two["launch_rounds"] == [5, 6, 7, 8]
    assert one["squares"] == [1] and two["squares"] == [2] and one["separate_launches"] and two["separate_launches"]
    assert not pc.design_block(plan, "both", plan.rounds, **kw)["separate_launches"]
    assert len(one["schedule"]) == 8 and one["sessions_per_round"] == 32
    changed = pc.design_block(pc.build_plan("h100", "20261117"), "1", pc.launch_rounds(plan, "1"), **kw)
    assert changed["common_sha256"] != one["common_sha256"], "another lock date is another design"
    assert pc.design_block(plan, "1", pc.launch_rounds(plan, "1"), **{**kw, "t_min_s": 2.0})["common_sha256"] != one["common_sha256"]
    vm = pc.build_plan("c-vm", "20261116")
    assert one["cooldown_s"] == 0.0, "the idle brackets are the cool-down"
    assert pc.design_block(vm, "1", vm.rounds, **kw)["cooldown_s"] == 60.0, "the C-VM has no brackets: section 3.2 applies literally"


# ---- the lock date -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["2026-11-16", "2026111", "202611160", "", "abcdefgh", "20261316", "20261132", "20260230", None, 20261116])
def test_a_lock_date_is_exactly_eight_digits_naming_a_real_date(text):
    with pytest.raises(pc.PlanError):
        pc.parse_lock_date(text)


def test_a_lock_date_is_the_seed_and_may_not_lie_in_the_future_when_checked():
    assert pc.parse_lock_date("20261116") == (20261116, "20261116")
    assert pc.parse_lock_date("20261116", today=date(2026, 11, 16)) == (20261116, "20261116")
    with pytest.raises(pc.PlanError, match="future"):
        pc.parse_lock_date("20261117", today=date(2026, 11, 16))


# ---- verify_lock with a fake git ----------------------------------------------------------------------------------------

LOCKED = "# Paper 3\n\nStatus: **LOCKED 2026-11-16T10:00Z.** At the lock ...\n\n## 1. Questions\ntext\n\n## 11. Decisions\ntext\n"
AMENDMENTS = "\n## 12. Amendments\nNone.\n"


class FakeGit:
    def __init__(self, tag_commit="c" * 40, commit="c" * 40, kind="tag", tagger="2026-11-16T10:00:12+00:00", locked=LOCKED + AMENDMENTS):
        self.answers = {("rev-parse", "--verify", f"refs/tags/{pc.LOCK_TAG}^{{commit}}"): (0, tag_commit + "\n"),
                        ("rev-parse", "--verify", f"{commit}^{{commit}}"): (0, commit + "\n"),
                        ("for-each-ref", f"refs/tags/{pc.LOCK_TAG}", "--format=%(objecttype)|%(taggerdate:iso-strict)"): (0, f"{kind}|{tagger}\n"),
                        ("show", f"{commit}:{pc.DESIGN_DOC_REL}"): (0, locked)}
        self.commit = commit

    def __call__(self, *args):
        return self.answers.get(args, (128, f"fatal: unknown revision {args}"))


def lock_with(tmp_path, git, current=LOCKED + AMENDMENTS, date_text="20261116", commit="c" * 40):
    doc = tmp_path / "PAPER3.md"
    doc.write_text(current)
    return pc.verify_lock(date_text, commit, git=git, design_doc=doc)


def test_verify_lock_accepts_the_locked_file_and_returns_evidence(tmp_path):
    evidence = lock_with(tmp_path, FakeGit())
    assert evidence["commit"] == "c" * 40 and evidence["sections_1_11_unchanged"] and evidence["tag"] == pc.LOCK_TAG
    assert evidence["design_doc_sha256_at_lock"] == hashlib.sha256((LOCKED + AMENDMENTS).encode()).hexdigest()


def test_verify_lock_allows_appended_amendments_only(tmp_path):
    grown = LOCKED + AMENDMENTS + "### A1 - 2026-12-01 B200 admitted\nOutcome data seen: none.\n"
    assert lock_with(tmp_path, FakeGit(), current=grown)["sections_1_11_unchanged"]
    with pytest.raises(pc.PlanError, match="sections 1-11 differ"):
        lock_with(tmp_path, FakeGit(), current=LOCKED.replace("text", "edited text") + AMENDMENTS)


def test_verify_lock_refuses_every_mismatch(tmp_path):
    with pytest.raises(pc.PlanError, match="points at"):
        lock_with(tmp_path, FakeGit(tag_commit="d" * 40))
    with pytest.raises(pc.PlanError, match="not an annotated tag"):
        lock_with(tmp_path, FakeGit(kind="commit"))
    with pytest.raises(pc.PlanError, match="does not say LOCKED"):
        lock_with(tmp_path, FakeGit(locked=LOCKED.replace("LOCKED", "DRAFT") + AMENDMENTS), current=LOCKED + AMENDMENTS)
    with pytest.raises(pc.PlanError, match="git rev-parse"):
        pc.verify_lock("20261116", "f" * 40, git=FakeGit(), design_doc=tmp_path / "x.md")


def test_the_lock_date_is_compared_with_the_tag_in_utc(tmp_path):
    late_evening_pacific = FakeGit(tagger="2026-11-16T23:30:00-08:00")      # 2026-11-17 07:30 UTC
    with pytest.raises(pc.PlanError, match="UTC date 20261117"):
        lock_with(tmp_path, late_evening_pacific, date_text="20261116")
    assert lock_with(tmp_path, late_evening_pacific, date_text="20261117")["tagger_date_utc"].startswith("2026-11-17T07:30")
    with pytest.raises(pc.PlanError, match="seed is the lock date"):
        lock_with(tmp_path, FakeGit(), date_text="20261115")


def test_sections_1_to_11_are_the_text_before_the_amendments_heading():
    assert pc.sections_1_to_11(LOCKED + AMENDMENTS).rstrip() == LOCKED.rstrip()
    assert pc.sections_1_to_11(LOCKED) == LOCKED
    assert pc.sections_1_to_11(LOCKED + AMENDMENTS + "more") == pc.sections_1_to_11(LOCKED + AMENDMENTS)


# ---- the flags a host class sends to run_experiment.py exist there --------------------------------------------------------

def test_every_run_experiment_flag_a_host_class_sets_exists_in_run_experiment():
    import run_campaign as rc
    source = (REPO / "run_experiment.py").read_text()
    flags = {**rc.TELEMETRY_FLAGS, **rc.PAPER3_TELEMETRY_FLAGS}
    for host in pc.HOSTS.values():
        for key in host.telemetry:
            assert key in flags, f"{host.host}: telemetry key {key} has no run_experiment.py flag"
            assert f'"{flags[key]}"' in source, f"{flags[key]} is not a run_experiment.py option"


# ---- the C-VM pin (design 3.1 "C-VM protocol") ------------------------------------------------------------------------------------------

def vm_tree(tmp_path, **shape):
    from sysfs_fakes import make_cpu_tree
    return make_cpu_tree(tmp_path, **shape)


def test_the_vm_pin_is_13_physical_cores_and_their_siblings_from_the_vms_own_topology(tmp_path):
    cpu_root, node_root = vm_tree(tmp_path, sockets=1, cores=22, threads=2, nodes_per_socket=1)          # c3-highmem-44: 44 vCPUs
    pin = pc.vm_pin(cpu_root, node_root)
    assert pin["cpu_list"] == "0-12,22-34" and pin["cpu_count"] == 26 and pin["socket"] == 0
    assert pin["physical_cores"] == list(range(13)) and pin["smt_siblings_reported"] is True and pin["caveat"] is None
    assert pin["numa_nodes_reported"] == 1 and pin["bind_memory"] is False, "memory is bound only if the VM reports more than one node"


def test_the_vm_pin_pattern_is_the_metal_hosts_pin_when_the_topology_is_the_metals(tmp_path):
    cpu_root, node_root = vm_tree(tmp_path, sockets=2, cores=48, threads=2, nodes_per_socket=2)          # c3-highmem-192-metal
    pin = pc.vm_pin(cpu_root, node_root)
    assert pin["cpu_list"] == "0-12,96-108", "the P5 pin of the metal host: CPUs 0-12,96-108"
    assert pin["numa_nodes_reported"] == 4 and pin["bind_memory"] is True


def test_the_vm_takes_the_first_socket_with_enough_cores(tmp_path):
    cpu_root, node_root = vm_tree(tmp_path, sockets=2, cores=12, threads=2, nodes_per_socket=1)          # 12 cores per socket: too few
    with pytest.raises(pc.PlanError, match="no socket of the VM reports 13 physical cores"):
        pc.vm_pin(cpu_root, node_root)
    cpu_root, node_root = vm_tree(tmp_path / "b", sockets=2, cores=16, threads=2, nodes_per_socket=1)
    assert pc.vm_pin(cpu_root, node_root)["socket"] == 0


def test_a_vm_without_smt_siblings_gets_26_vcpus_and_a_recorded_caveat(tmp_path):
    cpu_root, node_root = vm_tree(tmp_path, sockets=1, cores=44, threads=1, nodes_per_socket=1)
    pin = pc.vm_pin(cpu_root, node_root)
    assert pin["cpu_list"] == "0-25" and pin["cpu_count"] == 26 and pin["smt_siblings_reported"] is False and pin["physical_cores"] is None
    assert "no SMT siblings" in pin["caveat"]
    cpu_root, node_root = vm_tree(tmp_path / "small", sockets=1, cores=16, threads=1, nodes_per_socket=1)
    with pytest.raises(pc.PlanError, match="fewer than 26"):
        pc.vm_pin(cpu_root, node_root)


def test_a_vm_topology_that_does_not_give_26_vcpus_or_none_at_all_is_refused(tmp_path):
    cpu_root, node_root = vm_tree(tmp_path, sockets=1, cores=22, threads=3, nodes_per_socket=1)
    with pytest.raises(pc.PlanError, match="hold 39 vCPUs, not 26"):
        pc.vm_pin(cpu_root, node_root)
    with pytest.raises(pc.PlanError, match="cannot list the VM's CPUs"):
        pc.vm_pin(tmp_path / "empty-cpu", tmp_path / "empty-node")
    (tmp_path / "bare" / "cpu").mkdir(parents=True)
    with pytest.raises(pc.PlanError, match="reports no CPU topology"):
        pc.vm_pin(tmp_path / "bare" / "cpu", tmp_path / "bare" / "node")
