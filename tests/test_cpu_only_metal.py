"""CPU-only bare metal (Paper 3): CPU lists, socket topology, pinning, the CPU-energy gate, per-socket RAPL zones in the
sampler sidecar, and the CPU-only pin set and environment checks of preflight.py.

Nothing here needs RAPL, several sockets or a GPU: sysfs is a tmp tree laid out like the GCP c3-highmem-192-metal probe
(docs/paper3-evidence/probes/gcp-c3-highmem-192-metal-20261111T150427Z.json: 2 sockets x 48 cores x 2 threads, sub-NUMA
clustering = 4 NUMA nodes, RAPL package-0/1 + dram, root-only energy_uj).
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import power_telemetry as pt  # noqa: E402
import preflight  # noqa: E402
from sysfs_fakes import C3_SOCKET_0, C3_SOCKET_1, bump, c3_powercap, deny, make_cpu_tree, make_zone, ticking_sleep  # noqa: E402

# ---- CPU lists -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, cpus", [
    ("0", [0]), ("0-3", [0, 1, 2, 3]), ("8, 0-1,1", [0, 1, 8]), ("0-47,96-143\n", list(range(48)) + list(range(96, 144))),
    (" 5 ", [5]), ("3,,4", [3, 4]),
])
def test_cpu_lists_parse_in_the_kernel_syntax(text, cpus):
    assert pt.parse_cpu_list(text) == cpus


@pytest.mark.parametrize("text", ["", " ", ",", "a", "1-", "-3", "3-1", "1-2-3", "0x3", "1_0", "+3", "0-99999999", "1 2"])
def test_malformed_cpu_lists_are_rejected(text):
    with pytest.raises(ValueError):
        pt.parse_cpu_list(text)


def test_an_empty_list_is_allowed_only_when_asked_for_an_offline_node():
    assert pt.parse_cpu_list("\n", allow_empty=True) == []


def test_cpu_lists_format_back_to_ranges():
    assert pt.format_cpu_list([0, 1, 2, 3, 8, 10, 11]) == "0-3,8,10-11"
    assert pt.format_cpu_list([5, 5, 4]) == "4-5" and pt.format_cpu_list([]) == ""
    assert pt.format_cpu_list(pt.parse_cpu_list(C3_SOCKET_0)) == C3_SOCKET_0


# ---- topology ------------------------------------------------------------------------------------------

def test_topology_of_the_c3_metal(c3):
    topology = pt.cpu_topology()
    assert topology["problems"] == []
    assert topology["sockets"] == [
        {"socket": 0, "cpus": C3_SOCKET_0, "cpu_count": 96, "physical_cores": 48, "numa_nodes": [0, 1]},
        {"socket": 1, "cpus": C3_SOCKET_1, "cpu_count": 96, "physical_cores": 48, "numa_nodes": [2, 3]}]
    assert pt.format_cpu_list(pt.parse_cpu_list(C3_SOCKET_0) + pt.parse_cpu_list(C3_SOCKET_1)) == "0-191"


def test_topology_of_a_missing_tree_says_why_instead_of_raising(tmp_path):
    topology = pt.cpu_topology(tmp_path / "none", tmp_path / "none-either")
    assert topology["sockets"] == [] and "cannot list" in topology["problems"][0]


def test_offline_cpus_are_not_listed(c3):
    import shutil
    shutil.rmtree(c3.cpu_root / "cpu191" / "topology")                  # an offline CPU keeps its directory, loses topology/
    shutil.rmtree(c3.cpu_root / "cpu144" / "topology")
    sockets = {s["socket"]: s for s in pt.cpu_topology()["sockets"]}
    assert sockets[1]["cpus"] == "48-95,145-190" and sockets[1]["cpu_count"] == 94 and sockets[0]["cpu_count"] == 96


def test_a_host_without_numa_information_still_has_sockets(c3):
    import shutil
    shutil.rmtree(c3.node_root)
    assert [s["numa_nodes"] for s in pt.cpu_topology()["sockets"]] == [[], []]


def test_the_topology_cli_prints_the_sockets(c3, capsys):
    assert pt.main(["--topology", "--cpu-root", str(c3.cpu_root), "--node-root", str(c3.node_root)]) == 0
    assert [s["cpus"] for s in json.loads(capsys.readouterr().out)["sockets"]] == [C3_SOCKET_0, C3_SOCKET_1]


# ---- pinning -------------------------------------------------------------------------------------------

def test_nothing_requested_means_no_pinning(c3):
    assert pt.resolve_pinning() is None


def test_socket_zero_is_every_cpu_of_the_package_including_its_smt_siblings(c3):
    pinning = pt.resolve_pinning(socket=0)
    assert pinning["cpus"] == C3_SOCKET_0 and pinning["cpu_count"] == 96
    assert pinning["sockets"] == [0] and pinning["whole_sockets"] == [0] and pinning["numa_nodes"] == [0, 1]
    assert pinning["requested"] == {"socket": 0, "cpu_list": None}
    assert "physical_package_id" in pinning["topology_basis"]
    assert pt.resolve_pinning(socket=1)["cpus"] == C3_SOCKET_1


def test_a_cpu_list_inside_one_socket_pins_part_of_it(c3):
    pinning = pt.resolve_pinning(cpu_list="0-12,96-108")                # 13 cores with both hardware threads
    assert pinning["cpu_count"] == 26 and pinning["sockets"] == [0] and pinning["whole_sockets"] == []
    assert pinning["numa_nodes"] == [0]                                  # cores 0-12 sit in the first NUMA node of the socket
    assert pinning["cpus"] == "0-12,96-108" and pinning["requested"] == {"socket": None, "cpu_list": "0-12,96-108"}


def test_a_cpu_list_across_sockets_names_both(c3):
    pinning = pt.resolve_pinning(cpu_list="40-55")
    assert pinning["sockets"] == [0, 1] and pinning["whole_sockets"] == []


def test_a_socket_that_does_not_exist_lists_the_ones_that_do(c3):
    with pytest.raises(pt.PinningError, match=r"--socket 2: no such socket.*sockets: 0, 1"):
        pt.resolve_pinning(socket=2)


def test_both_flags_or_a_bad_list_are_refused(c3):
    with pytest.raises(pt.PinningError, match="alternatives"):
        pt.resolve_pinning(socket=0, cpu_list="0-3")
    with pytest.raises(pt.PinningError, match="--cpu-list: not a CPU list"):
        pt.resolve_pinning(cpu_list="0-three")


def test_an_offline_cpu_is_refused(c3):
    with pytest.raises(pt.PinningError, match=r"CPU\(s\) 192 are not online"):
        pt.resolve_pinning(cpu_list="190-192")


def test_cpus_the_process_may_not_use_are_refused(c3):
    c3.mask["cpus"] = set(range(48, 96)) | set(range(144, 192))            # a cpuset (or an earlier taskset) keeps us on socket 1
    with pytest.raises(pt.PinningError, match="not allowed to this process.*taskset"):
        pt.resolve_pinning(socket=0)
    assert pt.resolve_pinning(socket=1)["cpus"] == C3_SOCKET_1


def test_without_topology_a_socket_cannot_be_resolved_but_a_cpu_list_can(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(range(8)), raising=False)
    with pytest.raises(pt.PinningError, match="no such socket"):
        pt.resolve_pinning(socket=0)
    pinning = pt.resolve_pinning(cpu_list="0-3")
    assert pinning["sockets"] is None and pinning["topology_basis"] == "unavailable" and pinning["cpu_count"] == 4


def test_applying_the_pin_sets_the_affinity_and_reads_it_back(c3):
    assert pt.apply_pinning(C3_SOCKET_0) == C3_SOCKET_0
    assert c3.mask["set_calls"] == [set(pt.parse_cpu_list(C3_SOCKET_0))]
    assert pt.process_affinity() == {"cpus": C3_SOCKET_0, "count": 96, "source": "os.sched_getaffinity(0) in the worker"}


def test_a_kernel_that_ends_up_elsewhere_is_an_error_not_a_silent_miss():
    state = {}
    with pytest.raises(pt.PinningError, match="affinity after pinning is 0-3, wanted 0-1"):
        pt.apply_pinning("0-1", setaffinity=lambda pid, cpus: state.update(cpus=cpus), getaffinity=lambda pid: {0, 1, 2, 3})


def test_a_refused_sched_setaffinity_is_reported():
    def refuse(pid, cpus):
        raise OSError(22, "Invalid argument")
    with pytest.raises(pt.PinningError, match="sched_setaffinity.*Invalid argument"):
        pt.apply_pinning("0-1", setaffinity=refuse, getaffinity=lambda pid: {0, 1})


def test_pinning_needs_linux(monkeypatch):
    monkeypatch.delattr(os, "sched_setaffinity", raising=False)
    with pytest.raises(pt.PinningError, match="needs Linux"):
        pt.apply_pinning("0-1")
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    assert pt.process_affinity()["cpus"] is None and "unavailable" in pt.process_affinity()["source"]
    assert pt.available_cpus() is None


# ---- the CPU-energy gate -------------------------------------------------------------------------------

def gate(mode, tree, **kwargs):
    return pt.check_cpu_energy(mode, powercap_root=tree, hwmon_root=tree.parent / "no-hwmon", **kwargs)


def test_mode_off_checks_nothing(tmp_path, monkeypatch):
    tree = c3_powercap(tmp_path / "powercap")
    deny(monkeypatch, tree / "intel-rapl:0" / "energy_uj")
    verdict = gate("off", tree)
    assert verdict["ok"] and verdict["state"] == "skipped" and verdict["problems"] == []


def test_a_host_without_rapl_is_fine_in_auto_and_a_problem_in_require(tmp_path):
    tree = tmp_path / "no-powercap"
    auto = gate("auto", tree)
    assert auto["ok"] and auto["state"] == "absent" and "recorded as unavailable" in auto["warnings"][0]
    required = gate("require", tree, sampler=pt.PowerSampler(tmp_path, ipmi=False, powercap_root=tree, hwmon_root=tmp_path / "x",
                                                            which=lambda name: None))
    assert not required["ok"] and required["state"] == "absent" and "--cpu-energy require" in required["problems"][0]


def test_rapl_that_exists_but_is_root_only_is_a_hard_failure_that_names_the_fix(tmp_path, monkeypatch):
    tree = c3_powercap(tmp_path / "powercap")
    deny(monkeypatch, *(tree.glob("intel-rapl*/energy_uj")))                # the c3 metal as a normal user: 4 zones, all EACCES
    verdict = gate("auto", tree)
    assert verdict["ok"] is False and verdict["state"] == "unreadable"
    assert verdict["zones"] == {"total": 4, "unreadable": 4, "not_advancing": 0}
    (problem,) = verdict["problems"]
    assert "4 cannot be read by uid" in problem and "PermissionError" in problem and "CVE-2020-8694" in problem
    assert "./setup_h100.sh --power-only --enable-rapl-read" in problem
    assert not gate("require", tree)["ok"]


def test_one_unreadable_zone_is_enough_to_fail(tmp_path, monkeypatch):
    tree = c3_powercap(tmp_path / "powercap")
    deny(monkeypatch, tree / "intel-rapl:1:0" / "energy_uj")                  # package-1's dram only
    verdict = gate("auto", tree)
    assert not verdict["ok"] and verdict["zones"]["unreadable"] == 1 and "intel-rapl:1:0" in verdict["problems"][0]


def test_a_non_permission_read_error_gets_its_own_advice(tmp_path, monkeypatch):
    tree = c3_powercap(tmp_path / "powercap")
    (tree / "intel-rapl:0" / "energy_uj").write_text("not a number\n")
    verdict = gate("auto", tree)
    assert not verdict["ok"] and "ValueError" in verdict["problems"][0] and "--enable-rapl-read" not in verdict["problems"][0]
    assert "--cpu-energy off" in verdict["problems"][0]


def test_readable_advancing_counters_pass_in_every_mode(tmp_path):
    tree = c3_powercap(tmp_path / "powercap")
    sleep = ticking_sleep(tree)
    auto = gate("auto", tree, sleep=sleep)
    assert auto["ok"] and auto["state"] == "readable" and auto["warnings"] == [] and auto["zones"]["total"] == 4
    required = gate("require", tree, sleep=sleep)
    assert required["ok"] and required["sampler_status"] == "available" and required["cpu_energy_source"] == "powercap-rapl"


def test_a_counter_that_never_moves_is_a_warning_in_auto_and_a_failure_in_require(tmp_path):
    tree = c3_powercap(tmp_path / "powercap")
    auto = gate("auto", tree, sleep=lambda seconds: None)
    assert auto["ok"] and auto["state"] == "readable" and "do not advance" in auto["warnings"][0]
    assert auto["zones"]["not_advancing"] == 4
    required = gate("require", tree, sleep=lambda seconds: None)
    assert not required["ok"] and "do not advance" in required["problems"][0]


def test_an_unknown_mode_is_a_programming_error(tmp_path):
    with pytest.raises(ValueError):
        pt.check_cpu_energy("sometimes")


def test_the_gate_cli_exits_4_when_rapl_is_unreadable(tmp_path, monkeypatch, capsys):
    tree = c3_powercap(tmp_path / "powercap")
    deny(monkeypatch, *(tree.glob("intel-rapl*/energy_uj")))
    assert pt.main(["--check-cpu-energy", "--powercap-root", str(tree), "--hwmon-root", str(tmp_path / "h")]) == 4
    assert json.loads(capsys.readouterr().out)["state"] == "unreadable"
    assert pt.main(["--check-cpu-energy", "--powercap-root", str(tmp_path / "none"), "--hwmon-root", str(tmp_path / "h")]) == 0


# ---- per-socket zones in the sampler -------------------------------------------------------------------

def sampler_for(tmp_path, tree, **kwargs):
    kwargs.setdefault("hwmon_root", tmp_path / "no-hwmon")
    kwargs.setdefault("which", lambda name: None)
    kwargs.setdefault("liveness_seconds", 0)
    kwargs.setdefault("ipmi", False)
    return pt.PowerSampler(tmp_path / "out", powercap_root=tree, **kwargs)


def test_package_and_dram_of_each_socket_are_separate_series_and_the_sidecar_maps_them(c3, tmp_path):
    tree = c3_powercap(tmp_path / "powercap")
    sampler = sampler_for(tmp_path, tree)
    report = sampler.start(background=False)
    sampler.poll_counters()
    for zone, energy in (("intel-rapl:0", 6_000_000), ("intel-rapl:0:0", 800_000), ("intel-rapl:1", 11_000_000),
                         ("intel-rapl:1:0", 1_700_000)):
        (tree / zone / "energy_uj").write_text(f"{energy}\n")
    sampler.poll_counters()
    sampler.stop()
    sockets = report["sockets"]
    assert sockets["per_socket_counters"] is True
    assert sockets["zones"] == {"0": {"package": ["intel-rapl:0"], "dram": ["intel-rapl:0:0"]},
                                "1": {"package": ["intel-rapl:1"], "dram": ["intel-rapl:1:0"]}}
    assert [s["cpus"] for s in sockets["topology"]] == [C3_SOCKET_0, C3_SOCKET_1] and sockets["problems"] == []
    import csv
    with (tmp_path / "out" / "power.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    series = {}
    for row in rows:
        series.setdefault(row["domain"], []).append(int(row["energy_uj"]))
    assert sorted(series) == ["package-0", "package-0/dram", "package-1", "package-1/dram"]
    deltas = {domain: values[-1] - values[0] for domain, values in series.items()}
    assert deltas == {"package-0": 1_000_000, "package-0/dram": 100_000, "package-1": 1_000_000, "package-1/dram": 300_000}
    assert {row["zone"] for row in rows if row["domain"] == "package-1/dram"} == {"intel-rapl:1:0"}


def test_the_mmio_copy_of_a_package_is_not_listed_as_a_second_socket_zone(c3, tmp_path):
    tree = c3_powercap(tmp_path / "powercap")
    make_zone(tree, "intel-rapl-mmio:0", "package-0")
    report = sampler_for(tmp_path, tree).probe()
    assert report["sockets"]["zones"]["0"] == {"package": ["intel-rapl:0"], "dram": ["intel-rapl:0:0"]}
    assert report["sockets"]["zones_not_sampled"] == []


def test_zones_that_cannot_be_read_are_listed_as_not_sampled(c3, tmp_path, monkeypatch):
    tree = c3_powercap(tmp_path / "powercap")
    deny(monkeypatch, tree / "intel-rapl:1:0" / "energy_uj")
    report = sampler_for(tmp_path, tree).probe()
    assert report["status"] == "partial" and report["sockets"]["zones_not_sampled"] == ["intel-rapl:1:0"]
    assert report["sockets"]["zones"]["1"] == {"package": ["intel-rapl:1"]}


def test_a_zone_for_a_socket_the_topology_does_not_have_is_flagged(c3, tmp_path):
    tree = tmp_path / "powercap"
    make_zone(tree, "intel-rapl:3", "package-3")
    report = sampler_for(tmp_path, tree).probe()
    assert any("socket(s) ['3']" in p and "unverified" in p for p in report["sockets"]["problems"])


def test_amd_energy_sockets_are_per_socket_too(tmp_path):
    hwmon = tmp_path / "hwmon"
    device = hwmon / "hwmon4"
    device.mkdir(parents=True)
    (device / "name").write_text("amd_energy\n")
    for channel, label in ((1, "Esocket0"), (2, "Esocket1"), (3, "Ecore000")):
        (device / f"energy{channel}_input").write_text("1000\n")
        (device / f"energy{channel}_label").write_text(label + "\n")
    report = sampler_for(tmp_path, tmp_path / "none", hwmon_root=hwmon).probe()
    assert report["cpu_energy_source"] == "amd-energy"
    assert report["sockets"]["zones"] == {"0": {"socket": ["hwmon4/energy1"]}, "1": {"socket": ["hwmon4/energy2"]}}


def test_the_pinned_socket_decides_which_zones_are_s2_pinned(c3, tmp_path):
    tree = c3_powercap(tmp_path / "powercap")
    pinning = pt.resolve_pinning(socket=1)
    report = sampler_for(tmp_path, tree, pinning=pinning).probe()
    attribution = report["pinning"]["attribution"]
    assert report["pinning"]["cpus"] == C3_SOCKET_1 and report["pinning"]["requested"] == {"socket": 1, "cpu_list": None}
    assert attribution["possible"] is True and attribution["pinned_sockets"] == [1]
    assert attribution["pinned_zones"] == {"1": {"package": ["intel-rapl:1"], "dram": ["intel-rapl:1:0"]}}
    assert attribution["other_zones"] == {"0": {"package": ["intel-rapl:0"], "dram": ["intel-rapl:0:0"]}}


def test_a_run_spanning_both_sockets_pins_both(c3, tmp_path):
    tree = c3_powercap(tmp_path / "powercap")
    attribution = sampler_for(tmp_path, tree, pinning=pt.resolve_pinning(cpu_list="40-55")).probe()["pinning"]["attribution"]
    assert attribution["pinned_sockets"] == [0, 1] and attribution["other_zones"] == {}


def test_attribution_is_refused_when_the_energy_source_is_not_per_socket(c3, tmp_path, monkeypatch):
    tree = c3_powercap(tmp_path / "powercap")
    deny(monkeypatch, *(tree.glob("intel-rapl*/energy_uj")))                # nothing readable: no energy source at all
    report = sampler_for(tmp_path, tree, pinning=pt.resolve_pinning(socket=0)).probe()
    assert report["status"] == "unavailable" and report["sockets"]["per_socket_counters"] is False
    assert report["pinning"]["attribution"]["possible"] is False


def test_attribution_is_refused_when_the_pinned_cpus_have_no_known_socket(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(range(8)), raising=False)
    tree = c3_powercap(tmp_path / "powercap")
    pinning = pt.resolve_pinning(cpu_list="0-3")                            # no topology tree: sockets unknown
    report = sampler_for(tmp_path, tree, pinning=pinning).probe()
    assert report["pinning"]["attribution"]["possible"] is False and "unknown" in report["pinning"]["attribution"]["reason"]


def test_a_run_without_pinning_has_no_pinning_block(c3, tmp_path):
    assert "pinning" not in sampler_for(tmp_path, c3_powercap(tmp_path / "powercap")).probe()


# ---- preflight: the CPU-only pin set -------------------------------------------------------------------

def pins_of(name):
    return preflight.parse_pins(REPO / name)


def installed_from(*pin_sets, **extra):
    versions = {}
    for pins in pin_sets:
        versions.update(pins)
    versions.update(extra)
    return lambda name: versions.get(preflight.normalize_name(name))


LOCK = preflight.parse_pins(REPO / "evidence" / "requirements.lock.txt")
CPU = preflight.parse_pins(REPO / "requirements-cpu.txt")
GPU = preflight.parse_pins(REPO / "requirements-gpu.txt")


def test_the_cpu_pin_set_is_the_lock_with_polars_1_44_2_and_no_gpu_package():
    name, pins = preflight.resolve_pin_set("lock+cpu", ["polars", "spark_cpu"])
    assert name == "lock+cpu"
    assert pins["polars"] == "1.44.2" and pins["polars-runtime-32"] == "1.44.2" and pins["pyspark"] == LOCK["pyspark"]
    assert not [n for n in pins if "cudf" in n or "cupy" in n or n.startswith("nvidia") or n.endswith("cu12")]
    assert {n: v for n, v in pins.items() if LOCK.get(n) not in (None, v)} == {"polars": "1.44.2", "polars-runtime-32": "1.44.2"}


def test_the_lock_set_is_polars_2_0_0_and_the_gpu_set_has_cudf_polars():
    assert preflight.resolve_pin_set("lock", ["polars"])[1] == LOCK and LOCK["polars"] == "2.0.0"
    gpu = preflight.resolve_pin_set("lock+gpu", ["polars"])[1]
    assert gpu["cudf-polars-cu12"] == "26.10.2" and gpu["polars"] == "1.44.2"


def test_auto_picks_the_set_that_matches_the_environment():
    cpu_env = installed_from(LOCK, CPU)                                  # Polars 1.44.2, no cudf-polars
    assert preflight.resolve_pin_set("auto", ["polars", "spark_cpu"], cpu_env)[0] == "lock+cpu"
    assert preflight.resolve_pin_set("auto", ["polars", "spark_cpu"], installed_from(LOCK))[0] == "lock"
    gpu_env = installed_from(LOCK, GPU)
    assert preflight.resolve_pin_set("auto", ["polars", "spark_cpu"], gpu_env)[0] == "lock+gpu"
    assert preflight.resolve_pin_set("auto", ["polars_gpu"], installed_from(LOCK))[0] == "lock+gpu"
    assert preflight.resolve_pin_set("auto", ["polars"], installed_from(polars="1.30.0"))[0] == "lock"     # fails the pin check


def test_an_explicit_choice_overrides_auto():
    assert preflight.resolve_pin_set("lock", ["polars_gpu"], installed_from(LOCK, GPU))[0] == "lock"


def test_a_missing_cpu_pin_file_does_not_break_the_other_sets(tmp_path, monkeypatch):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "requirements.lock.txt").write_text("polars==2.0.0\n")
    monkeypatch.setattr(preflight, "HERE", tmp_path)
    assert preflight.resolve_pin_set("auto", ["polars"], installed_from(polars="1.44.2")) == ("lock", {"polars": "2.0.0"})


# ---- preflight: environment checks on a CPU-only host --------------------------------------------------

def env_checks(monkeypatch, tmp_path, argv, installed, java='openjdk version "17.0.12" 2024-07-16'):
    monkeypatch.setattr(preflight, "_installed", installed)
    monkeypatch.setattr(preflight, "run_command", lambda command, timeout=60: (0, java + "\n") if command[0] == "java" else (None, "no"))
    parser = preflight.build_parser()
    args = parser.parse_args(["--data-root", str(tmp_path), "--output", str(tmp_path / "out"), *argv])
    preflight.resolve_args(parser, args)
    checks = preflight.Checks(quiet=True)
    preflight.check_environment(checks, args, args.engines or list(preflight.ALL_ENGINES))
    return {item["id"]: item for item in checks.items}


def test_a_cpu_only_host_passes_the_environment_check_without_skip_env(monkeypatch, tmp_path):
    for label, installed in (("polars 1.44.2", installed_from(LOCK, CPU)), ("polars 2.0.0", installed_from(LOCK))):
        found = env_checks(monkeypatch, tmp_path, ["--cpu-only"], installed)
        failed = {k: v["detail"] for k, v in found.items() if v["status"] == "FAIL"}
        assert failed == {}, (label, failed)
        assert found["env.gpu"]["status"] == "SKIP" and found["env.cudf_polars"]["status"] == "SKIP" and found["env.jar"]["status"] == "SKIP"
        assert found["env.java"]["status"] == "PASS" and found["env.package_pins"]["status"] == "PASS"


def test_the_pin_check_names_the_set_it_used(monkeypatch, tmp_path):
    cpu = env_checks(monkeypatch, tmp_path, ["--cpu-only"], installed_from(LOCK, CPU))["env.package_pins"]["detail"]
    lock = env_checks(monkeypatch, tmp_path, ["--cpu-only"], installed_from(LOCK))["env.package_pins"]["detail"]
    assert "lock+cpu set" in cpu and "requirements-cpu.txt" in cpu and "lock set" in lock and "Polars 2.0.0" in lock


def test_a_cpu_environment_with_a_gpu_pin_set_requested_fails_naming_the_missing_packages(monkeypatch, tmp_path):
    found = env_checks(monkeypatch, tmp_path, ["--cpu-only", "--pins", "lock+gpu"], installed_from(LOCK, CPU))["env.package_pins"]
    assert found["status"] == "FAIL" and "cudf-polars-cu12: not installed" in found["detail"]


def test_a_wrong_polars_fails_the_cpu_host(monkeypatch, tmp_path):
    found = env_checks(monkeypatch, tmp_path, ["--cpu-only"], installed_from(LOCK, polars="1.30.0"))["env.package_pins"]
    assert found["status"] == "FAIL" and "polars: installed 1.30.0, want 2.0.0" in found["detail"]


def test_cpu_only_means_two_engines_and_refuses_gpu_ones():
    parser = preflight.build_parser()
    args = parser.parse_args(["--cpu-only"])
    preflight.resolve_args(parser, args)
    assert args.engines == ["polars", "spark_cpu"]
    for bad in (["--cpu-only", "--engines", "polars", "spark_gpu"], ["--cpu-only", "--formal", "--skip-run", "--round", "1"]):
        parser = preflight.build_parser()
        with pytest.raises(SystemExit):
            preflight.resolve_args(parser, parser.parse_args(bad))


def test_rapl_that_exists_but_is_unreadable_fails_the_environment_check(monkeypatch, tmp_path):
    tree = c3_powercap(tmp_path / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    deny(monkeypatch, *(tree.glob("intel-rapl*/energy_uj")))
    found = env_checks(monkeypatch, tmp_path, ["--cpu-only"], installed_from(LOCK, CPU))["env.cpu_energy"]
    assert found["status"] == "FAIL" and found["state"] == "unreadable" and "--enable-rapl-read" in found["detail"]
    off = env_checks(monkeypatch, tmp_path, ["--cpu-only", "--cpu-energy", "off"], installed_from(LOCK, CPU))["env.cpu_energy"]
    assert off["status"] == "SKIP"


def test_readable_rapl_passes_and_no_rapl_is_only_information(monkeypatch, tmp_path):
    tree = c3_powercap(tmp_path / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    real = pt.check_advancing
    monkeypatch.setattr(pt, "check_advancing", lambda zones, seconds, sleep=None: real(zones, seconds, ticking_sleep(tree)))
    assert env_checks(monkeypatch, tmp_path, ["--cpu-only"], installed_from(LOCK, CPU))["env.cpu_energy"]["status"] == "PASS"
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tmp_path / "nowhere")
    assert env_checks(monkeypatch, tmp_path, ["--cpu-only"], installed_from(LOCK, CPU))["env.cpu_energy"]["status"] == "INFO"
    required = env_checks(monkeypatch, tmp_path, ["--cpu-only", "--cpu-energy", "require"], installed_from(LOCK, CPU))["env.cpu_energy"]
    assert required["status"] == "FAIL"


def test_preflight_checks_the_pinning_it_will_smoke_test_with(c3, monkeypatch, tmp_path):
    found = env_checks(monkeypatch, tmp_path, ["--cpu-only", "--socket", "1", "--cpus", "26"], installed_from(LOCK, CPU))
    assert found["env.cpu_pinning"]["status"] == "PASS" and C3_SOCKET_1 in found["env.cpu_pinning"]["detail"]
    over = env_checks(monkeypatch, tmp_path, ["--cpu-only", "--cpu-list", "0-3", "--cpus", "26"], installed_from(LOCK, CPU))
    assert over["env.cpu_pinning"]["status"] == "FAIL" and "oversubscribed" in over["env.cpu_pinning"]["detail"]
    missing = env_checks(monkeypatch, tmp_path, ["--cpu-only", "--socket", "4"], installed_from(LOCK, CPU))
    assert missing["env.cpu_pinning"]["status"] == "FAIL" and "no such socket" in missing["env.cpu_pinning"]["detail"]


def test_the_smoke_run_carries_the_pinning_and_the_energy_rule_and_skips_the_idle_windows(monkeypatch, tmp_path):
    seen = []

    class Done:
        returncode = 0
    monkeypatch.setattr(preflight.subprocess, "run", lambda command, **kwargs: seen.append(command) or Done())
    parser = preflight.build_parser()
    args = parser.parse_args(["--cpu-only", "--socket", "1", "--cpu-energy", "require", "--output", str(tmp_path / "o"),
                              "--data-root", str(tmp_path)])
    preflight.resolve_args(parser, args)
    (tmp_path / "o").mkdir()
    checks = preflight.Checks(quiet=True)
    assert preflight.run_suite(checks, args, "baseline", tmp_path / "o" / "baseline", ["polars", "spark_cpu"]) is True
    (command,) = seen
    assert command[command.index("--idle-baseline-seconds") + 1] == "0"
    assert command[command.index("--cpu-energy") + 1] == "require" and command[command.index("--socket") + 1] == "1"
    assert "--cpu-list" not in command


def test_a_pinned_preflight_defaults_to_the_pinned_cpus_not_all_of_them(c3, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 192)

    def resolved(*argv):
        parser = preflight.build_parser()
        args = parser.parse_args(list(argv))
        preflight.resolve_args(parser, args)
        return args.cpus
    assert resolved("--socket", "0") == 96 and resolved("--cpu-list", "0-25") == 26 and resolved("--socket", "0", "--cpus", "12") == 12
    assert resolved() == 192 and resolved("--socket", "9") == 192                                      # env.cpu_pinning reports the latter


# ---- NUMA memory binding: the ctypes layer --------------------------------------------------------------

class FakeLibnuma:
    """libnuma's set_mempolicy/get_mempolicy as ctypes would call them: C ints, a nodemask array, an out-parameter."""

    def __init__(self):
        self.mode, self.nodes, self.set_calls = 0, [], []
        self.rc, self.errno = 0, 0

    def set_mempolicy(self, mode, mask, maxnode):
        self.set_calls.append((mode.value, [w for w in mask], maxnode.value))
        if self.rc:
            ctypes.set_errno(self.errno)
            return self.rc
        self.mode = mode.value
        self.nodes = [64 * i + b for i, word in enumerate(mask) for b in range(64) if word >> b & 1]
        return 0

    def get_mempolicy(self, mode_ref, mask, maxnode, addr, flags):
        mode_ref._obj.value = self.mode
        for node in self.nodes:
            mask[node // 64] |= 1 << node % 64
        return 0


class FakeLibc:
    """libc.syscall(number, ...): which numbers were used, and a kernel that remembers the policy."""

    def __init__(self):
        self.numbers, self.mode, self.nodes = [], 0, []

    def syscall(self, number, *args):
        self.numbers.append(number.value)
        if number.value in (237, 238):                            # set_mempolicy
            mode, mask, maxnode = args
            self.mode = mode.value
            self.nodes = [64 * i + b for i, word in enumerate(mask) for b in range(64) if word >> b & 1]
        else:                                                      # get_mempolicy
            mode_ref, mask = args[0], args[1]
            mode_ref._obj.value = self.mode
            for node in self.nodes:
                mask[node // 64] |= 1 << node % 64
        return 0


def loader_for(**libs):
    """A ctypes.CDLL replacement: libs maps a library name to a fake, anything else is 'cannot open shared object file'."""
    def load(name):
        if name in libs:
            return libs[name]
        raise OSError(f"{name}: cannot open shared object file: No such file or directory")
    return load


def test_the_nodemask_encodes_nodes_into_words_and_the_kernels_bit_count():
    mask, maxnode = pt.NumaPolicyApi._mask([0, 1])
    assert list(mask) == [0b11] and maxnode == 65
    mask, maxnode = pt.NumaPolicyApi._mask([3, 64, 70])
    assert list(mask) == [1 << 3, (1 << 0) | (1 << 6)] and maxnode == 129


def test_libnuma_is_the_first_choice_and_the_policy_reads_back():
    lib = FakeLibnuma()
    api = pt.NumaPolicyApi(loader=loader_for(**{"libnuma.so.1": lib}), system="linux")
    assert api.usable and api.mechanism == "libnuma (libnuma.so.1) set_mempolicy"
    api.set_membind([2, 3])
    assert lib.set_calls == [(pt.MPOL_BIND, [0b1100], 65)] and pt.MPOL_BIND == 2
    assert api.get_policy() == (pt.MPOL_BIND, [2, 3])


@pytest.mark.parametrize("machine, numbers", [("x86_64", (238, 239)), ("aarch64", (237, 236))])
def test_without_libnuma_the_raw_syscalls_of_the_architecture_are_used(machine, numbers):
    libc = FakeLibc()

    def only_libc(name):                                           # ctypes.CDLL(None) is the process's own libc
        if name is not None:
            raise OSError(f"{name}: cannot open shared object file")
        return libc
    api = pt.NumaPolicyApi(loader=only_libc, machine=machine, system="linux")
    assert api.mechanism == f"set_mempolicy syscall {numbers[0]} through libc"
    assert any("libnuma.so.1" in r for r in api.reasons)
    api.set_membind([1])
    assert api.get_policy() == (pt.MPOL_BIND, [1]) and libc.numbers == [numbers[0], numbers[1]]


def test_an_unknown_architecture_or_a_non_linux_system_has_no_policy_api_and_says_why():
    other = pt.NumaPolicyApi(loader=loader_for(), machine="riscv64", system="linux")
    assert not other.usable and any("riscv64" in r for r in other.reasons)
    mac = pt.NumaPolicyApi(loader=loader_for(), system="darwin")
    assert not mac.usable and "needs Linux" in mac.reasons[0]
    with pytest.raises(OSError, match="needs Linux"):
        mac.set_membind([0])
    with pytest.raises(OSError):
        mac.get_policy()


def test_a_kernel_refusal_becomes_an_oserror_with_the_errno():
    lib = FakeLibnuma()
    lib.rc, lib.errno = -1, errno.EPERM
    api = pt.NumaPolicyApi(loader=loader_for(**{"libnuma.so.1": lib}), system="linux")
    with pytest.raises(OSError) as refused:
        api.set_membind([0])
    assert refused.value.errno == errno.EPERM and "set_mempolicy" in str(refused.value)


# ---- apply_membind -------------------------------------------------------------------------------------

class Exec(Exception):
    """Raised by the fake os.execv: 'the process image was replaced'."""


def reexec_recorder():
    seen = []

    def execv(path, command):
        seen.append((path, command))
        raise Exec()
    return seen, execv


def fake_api(**state):
    from sysfs_fakes import FakeNumaApi, Membind
    membind = Membind()
    for key, value in state.items():
        setattr(membind, key, value)
    return FakeNumaApi(membind), membind


def test_socket_zeros_nodes_are_bound_and_read_back():
    api, membind = fake_api()
    record = pt.apply_membind([1, 0], api=api, environ={})
    assert membind.calls == [[0, 1]] and membind.policy == (pt.MPOL_BIND, [0, 1])
    assert record == {"policy": "MPOL_BIND", "nodes": [0, 1], "mechanism": "fake set_mempolicy", "verified": True,
                      "readback": {"source": "get_mempolicy", "mode": pt.MPOL_BIND, "nodes": [0, 1]}}


def test_a_refused_set_mempolicy_re_executes_under_numactl_with_a_guard():
    api, _ = fake_api(fail="Operation not permitted")
    seen, execv = reexec_recorder()
    environ = {}
    with pytest.raises(Exec):
        pt.apply_membind([0, 1], api=api, environ=environ, which=lambda name: "/usr/bin/numactl", reexec=execv,
                         argv=["/repo/run_experiment.py", "--socket", "0"])
    assert seen == [("/usr/bin/numactl", ["/usr/bin/numactl", "--membind=0-1", "--", sys.executable,
                                          "/repo/run_experiment.py", "--socket", "0"])]
    assert environ == {pt.MEMBIND_GUARD_ENV: "0-1"}


def test_a_policy_the_kernel_does_not_report_back_is_not_trusted():
    api, membind = fake_api()
    api.get_policy = lambda: (0, [])                                # set "worked", but nothing is bound
    seen, execv = reexec_recorder()
    with pytest.raises(Exec):
        pt.apply_membind([0], api=api, environ={}, which=lambda name: "/usr/bin/numactl", reexec=execv, argv=["x"])
    assert seen and membind.calls == [[0]]


def test_neither_set_mempolicy_nor_numactl_is_a_pinning_error_that_names_both_fixes():
    api, _ = fake_api(fail="Operation not permitted")
    with pytest.raises(pt.PinningError) as stopped:
        pt.apply_membind([0, 1], api=api, environ={}, which=lambda name: None)
    message = str(stopped.value)
    assert "cannot be bound to NUMA node(s) 0-1" in message and "Operation not permitted" in message
    assert "numactl is not installed" in message and "apt install numactl" in message and "--no-membind" in message


def test_after_the_numactl_re_exec_the_binding_is_verified_not_repeated():
    api, membind = fake_api(policy=(pt.MPOL_BIND, [0, 1]))
    record = pt.apply_membind([0, 1], api=api, environ={pt.MEMBIND_GUARD_ENV: "0-1"})
    assert membind.calls == [] and record["verified"] is True
    assert record["mechanism"].startswith("numactl --membind") and record["readback"]["source"] == "get_mempolicy"


@pytest.mark.parametrize("policies, ok", [
    (["bind:0-1"], True), (["bind:0-1", "default"], True), (["default"], False), (["bind:0"], False),
    (["bind:0-1", "interleave:0-1"], False), (None, False)])
def test_without_get_mempolicy_the_numactl_child_is_verified_from_numa_maps(policies, ok):
    api, _ = fake_api(fail="ENOSYS")                                 # get_policy still answers in the fake: make it fail too
    api.get_policy = lambda: (_ for _ in ()).throw(OSError(errno.ENOSYS, "not available"))
    environ = {pt.MEMBIND_GUARD_ENV: "0-1"}
    if ok:
        record = pt.apply_membind([0, 1], api=api, environ=environ, numa_maps=lambda: policies)
        assert record["verified"] is True and record["readback"] == {"source": "/proc/self/numa_maps", "policies": policies}
    else:
        with pytest.raises(pt.PinningError, match="did not leave the process bound"):
            pt.apply_membind([0, 1], api=api, environ=environ, numa_maps=lambda: policies)


def test_a_guard_for_other_nodes_does_not_count():
    api, membind = fake_api()
    record = pt.apply_membind([0, 1], api=api, environ={pt.MEMBIND_GUARD_ENV: "2-3"})
    assert membind.calls == [[0, 1]] and record["mechanism"] == "fake set_mempolicy"


def test_numa_maps_policies_are_the_second_column(tmp_path):
    maps = tmp_path / "numa_maps"
    maps.write_text("00400000 bind:0-1 file=/usr/bin/python3 mapped=3 N0=3 kernelpagesize_kB=4\n"
                    "7ffd1000 bind:0-1 stack anon=2 dirty=2 N1=2\n7ffff000 default\n")
    assert pt.read_numa_maps_policy(maps) == ["bind:0-1", "default"]
    assert pt.read_numa_maps_policy(tmp_path / "missing") is None


# ---- the plan ------------------------------------------------------------------------------------------

def test_a_pinned_run_binds_to_the_nodes_of_its_cpus(c3):
    assert pt.plan_membind(None) is None
    socket = pt.plan_membind(pt.resolve_pinning(socket=1))
    assert socket["enabled"] and socket["policy"] == "MPOL_BIND" and socket["nodes"] == [2, 3]
    part = pt.plan_membind(pt.resolve_pinning(cpu_list="0-12,96-108"))
    assert part["nodes"] == [0]                                        # only the first sub-NUMA node of the socket


def test_no_membind_is_a_recorded_plan_not_a_silent_one(c3):
    plan = pt.plan_membind(pt.resolve_pinning(socket=0), no_membind=True)
    assert plan["enabled"] is False and plan["policy"] == "default" and plan["nodes"] == [0, 1]
    assert "--no-membind" in plan["reason"] and "not guaranteed" in plan["reason"]


def test_a_host_that_lists_no_numa_node_has_nothing_to_bind(c3):
    import shutil
    shutil.rmtree(c3.node_root)
    plan = pt.plan_membind(pt.resolve_pinning(socket=0))
    assert plan["enabled"] is False and plan["nodes"] == [] and "no NUMA node" in plan["reason"]


# ---- the probe (a child process, so the caller's own policy is never touched) --------------------------

class Done:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def test_the_parent_reads_the_probe_childs_json():
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        return Done('noise\n{"ok": true, "mechanism": "libnuma (libnuma.so.1) set_mempolicy", "numactl": null, "libnuma": "x"}\n')
    report = pt.probe_membind([0, 1], run=run, script="/repo/power_telemetry.py")
    assert report["ok"] is True and seen == [[sys.executable, "/repo/power_telemetry.py", "--probe-membind", "--nodes", "0-1"]]


@pytest.mark.parametrize("done, reason", [
    (Done("", "Traceback boom", 1), "printed no JSON (exit 1): Traceback boom"),
    (OSError("exec format error"), "could not run: OSError: exec format error")])
def test_a_probe_that_cannot_answer_is_a_no(done, reason):
    def run(command, **kwargs):
        if isinstance(done, Exception):
            raise done
        return done
    report = pt.probe_membind([0], run=run)
    assert report["ok"] is False and reason in report["reasons"][0]


def test_the_probe_binds_in_the_child_and_reports_the_readback():
    api, _ = fake_api()
    report = pt.membind_probe_report([1, 0], api=api, which=lambda name: None)
    assert report["ok"] is True and report["nodes"] == [0, 1] and report["readback"] == {"mode": pt.MPOL_BIND, "nodes": [0, 1]}


def test_the_probe_falls_back_to_numactl_and_checks_what_the_numactl_child_sees():
    api, _ = fake_api(fail="Operation not permitted")
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        return Done('["bind:0-1"]\n')
    report = pt.membind_probe_report([0, 1], api=api, which=lambda name: "/usr/bin/numactl", run=run, python="/usr/bin/python3")
    assert report["ok"] is True and report["mechanism"] == "numactl --membind (re-exec)" and report["numactl"] == "/usr/bin/numactl"
    assert seen[0][:5] == ["/usr/bin/numactl", "--membind=0-1", "--", "/usr/bin/python3", seen[0][4]] and seen[0][-1] == "--read-membind"


@pytest.mark.parametrize("child, reason", [
    (Done('["default"]\n'), "did not bind the child"), (Done("", "numactl: set_mempolicy: Operation not permitted", 1), "failed (exit 1)")])
def test_numactl_that_does_not_bind_does_not_rescue_the_probe(child, reason):
    api, _ = fake_api(fail="Operation not permitted")
    report = pt.membind_probe_report([0], api=api, which=lambda name: "/usr/bin/numactl", run=lambda command, **kw: child)
    assert report["ok"] is False and any(reason in r for r in report["reasons"])


def test_the_probe_without_either_mechanism_says_so():
    api, _ = fake_api(fail="Operation not permitted")
    report = pt.membind_probe_report([0], api=api, which=lambda name: None)
    assert report["ok"] is False and report["numactl"] is None
    assert any("Operation not permitted" in r for r in report["reasons"]) and "numactl is not installed" in report["reasons"]


def test_the_probe_cli_exits_5_when_memory_cannot_be_bound(monkeypatch, capsys):
    api, membind = fake_api()
    monkeypatch.setattr(pt, "NumaPolicyApi", lambda *a, **k: api)
    assert pt.main(["--probe-membind", "--nodes", "0-1"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    membind.fail = "Operation not permitted"
    monkeypatch.setattr(pt.shutil, "which", lambda name: None)
    assert pt.main(["--probe-membind", "--nodes", "0"]) == 5
    assert json.loads(capsys.readouterr().out)["ok"] is False
    with pytest.raises(SystemExit):
        pt.main(["--probe-membind", "--nodes", "zero"])


# ---- preflight: can memory be bound? -------------------------------------------------------------------

def membind_check(monkeypatch, tmp_path, argv):
    found = env_checks(monkeypatch, tmp_path, argv, installed_from(LOCK, CPU))
    return found.get("env.membind")


def test_a_pinned_preflight_needs_memory_binding_and_names_what_works(c3, monkeypatch, tmp_path):
    ok = membind_check(monkeypatch, tmp_path, ["--cpu-only", "--socket", "1", "--cpus", "26"])
    assert ok["status"] == "PASS" and "NUMA node(s) [2, 3]" in ok["detail"] and "fake set_mempolicy" in ok["detail"]
    assert "numactl: not installed" in ok["detail"] and c3.membind.probes == [[2, 3]]


def test_a_pinned_preflight_fails_when_memory_cannot_be_bound(c3, monkeypatch, tmp_path):
    c3.membind.fail = "Operation not permitted"
    bad = membind_check(monkeypatch, tmp_path, ["--cpu-only", "--socket", "0", "--cpus", "26"])
    assert bad["status"] == "FAIL" and "cannot be bound to NUMA node(s) [0, 1]" in bad["detail"]
    assert "apt install numactl" in bad["detail"] and "--no-membind" in bad["detail"]
    skipped = membind_check(monkeypatch, tmp_path, ["--cpu-only", "--socket", "0", "--cpus", "26", "--no-membind"])
    assert skipped["status"] == "SKIP" and "--no-membind" in skipped["detail"]


def test_a_cpu_only_host_without_a_pinning_only_learns_whether_binding_would_work(c3, monkeypatch, tmp_path):
    assert membind_check(monkeypatch, tmp_path, ["--cpu-only"])["status"] == "PASS" and c3.membind.probes == [[0, 1]]
    c3.membind.fail = "Operation not permitted"
    assert membind_check(monkeypatch, tmp_path, ["--cpu-only"])["status"] == "INFO"
    assert membind_check(monkeypatch, tmp_path, []) is None                  # a GPU host: no memory-binding question


def test_a_host_without_numa_nodes_is_information_only(c3, monkeypatch, tmp_path):
    import shutil
    shutil.rmtree(c3.node_root)
    found = membind_check(monkeypatch, tmp_path, ["--cpu-only", "--socket", "0", "--cpus", "26"])
    assert found["status"] == "INFO" and "no NUMA node" in found["detail"] and c3.membind.probes == []


def test_an_impossible_pinning_leaves_the_membind_check_to_the_pinning_check(c3, monkeypatch, tmp_path):
    assert membind_check(monkeypatch, tmp_path, ["--cpu-only", "--socket", "9"])["status"] == "SKIP"


def test_the_smoke_run_carries_no_membind_and_the_flag_needs_a_pinning(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(preflight.subprocess, "run", lambda command, **kwargs: seen.append(command) or Done())
    parser = preflight.build_parser()
    args = parser.parse_args(["--cpu-only", "--socket", "1", "--no-membind", "--output", str(tmp_path / "o"), "--data-root", str(tmp_path)])
    preflight.resolve_args(parser, args)
    (tmp_path / "o").mkdir()
    preflight.run_suite(preflight.Checks(quiet=True), args, "baseline", tmp_path / "o" / "baseline", ["polars"])
    assert "--no-membind" in seen[0]
    parser = preflight.build_parser()
    with pytest.raises(SystemExit):
        preflight.resolve_args(parser, parser.parse_args(["--cpu-only", "--no-membind"]))
