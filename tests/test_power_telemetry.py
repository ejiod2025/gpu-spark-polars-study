"""Power and per-query telemetry: power_telemetry.py, the timed_* record fields of worker.py, the idle baseline and
GPU clock anchor of run_experiment.py, and the optional cost/telemetry blocks of run_campaign.py.

Nothing here needs RAPL, perf, turbostat, ipmitool or a GPU: sysfs is a tmp tree, external tools are fakes, the worker
runs the real Polars CPU engine on a three-row table (and a fake Spark session for the Spark branch), and run_experiment's
subprocess launches are replaced exactly as in test_run_experiment_engine_order.py.
"""
from __future__ import annotations

import csv
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import types

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import power_telemetry as pt  # noqa: E402
import run_campaign as rc  # noqa: E402
import run_experiment as rx  # noqa: E402
import worker  # noqa: E402
from sysfs_fakes import C3_SOCKET_0, c3_powercap, deny  # noqa: E402

IPMI_ACTIVE = """\

    Instantaneous power reading:                   312 Watts
    Minimum during sampling period:                 87 Watts
    Maximum during sampling period:                454 Watts
    Average power reading over sampling period:    268 Watts
    Sampling period:                          00000010 Seconds.
    Power reading state is:                   activated
"""
MAX_RANGE = 262143328850


# ---- fakes ---------------------------------------------------------------------------------------------

def make_zone(root: Path, zone: str, name: str, energy: int, max_range: int = MAX_RANGE) -> Path:
    directory = root / zone
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "name").write_text(name + "\n")
    (directory / "energy_uj").write_text(f"{energy}\n")
    (directory / "max_energy_range_uj").write_text(f"{max_range}\n")
    return directory


def set_energy(root: Path, zone: str, energy: int) -> None:
    (root / zone / "energy_uj").write_text(f"{energy}\n")


def two_socket_tree(root: Path) -> Path:
    """Two packages, each with core + dram sub-zones, plus an MMIO copy of package-0 (a duplicate, not a second CPU)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "intel-rapl").mkdir(exist_ok=True)  # the control-type directory: no energy_uj, must be ignored
    for socket in (0, 1):
        make_zone(root, f"intel-rapl:{socket}", f"package-{socket}", 1_000_000 * (socket + 1))
        make_zone(root, f"intel-rapl:{socket}:0", "core", 400_000)
        make_zone(root, f"intel-rapl:{socket}:1", "dram", 200_000)
    make_zone(root, "intel-rapl-mmio:0", "package-0", 1_000_000)
    return root


def read_rows(path: Path) -> list:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def no_tools(monkeypatch):
    """perf, turbostat and ipmitool are 'not installed'."""
    monkeypatch.setattr(pt.shutil, "which", lambda name: None)


def sampler_for(tmp_path, tree, **kwargs):
    kwargs.setdefault("hwmon_root", tmp_path / "no-hwmon")
    kwargs.setdefault("which", lambda name: None)
    kwargs.setdefault("liveness_seconds", 0)           # static fake trees do not advance; liveness has its own tests
    return pt.PowerSampler(tmp_path / "out", powercap_root=tree, **kwargs)


def bump(tree: Path, step: int = 50_000) -> None:
    """Add energy to every zone of a fake powercap tree, as running hardware does."""
    for counter in Path(tree).glob("intel-rapl*/energy_uj"):
        temporary = counter.with_name("energy_uj.tmp")        # replaced atomically: a reader never sees a truncated file
        temporary.write_text(f"{int(counter.read_text()) + step}\n")
        temporary.replace(counter)


class Ticking:
    """Background 'hardware': keeps the counters of a fake powercap tree advancing while a test runs."""

    def __init__(self, tree, period=0.01):
        self.tree, self.period, self.stop = tree, period, threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.wait(self.period):
            bump(self.tree)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()


# ---- clocks and windows --------------------------------------------------------------------------------

def test_utc_stamps_always_carry_microseconds_and_a_zero_offset():
    for _ in range(50):
        stamp = pt.utc_now()
        assert len(stamp.split(".")[1]) == len("123456+00:00"), stamp   # isoformat() alone drops a zero fraction
        assert datetime.fromisoformat(stamp).utcoffset().total_seconds() == 0


def test_open_and_close_stamps_bracket_the_timer():
    open_utc, open_mono = pt.stamp_open()
    started = time.perf_counter()
    time.sleep(0.01)
    elapsed = time.perf_counter() - started
    close_utc, close_mono = pt.stamp_close()
    window = (close_mono - open_mono) / 1e9
    assert elapsed <= window < elapsed + 0.05
    assert datetime.fromisoformat(open_utc) < datetime.fromisoformat(close_utc)


def test_clock_anchor_pairs_wall_and_monotonic_within_its_stated_uncertainty():
    before = time.monotonic_ns()
    anchor = pt.clock_anchor()
    after = time.monotonic_ns()
    assert before <= anchor["monotonic_ns"] <= after and 0 <= anchor["uncertainty_ns"] <= after - before
    assert abs(datetime.fromisoformat(anchor["utc"]).timestamp() - time.time()) < 5


def test_host_timezone_records_what_converts_nvidia_smi_stamps(monkeypatch):
    zone = pt.host_timezone()
    assert set(zone) >= {"tz_env", "tzname", "utc_offset_seconds", "dst_in_effect", "has_dst_rules", "etc_localtime"}
    assert isinstance(zone["utc_offset_seconds"], int)


def test_record_window_prefers_the_timed_window_and_accepts_legacy_records():
    new = {"started_at_utc": "2026-01-01T00:00:00+00:00", "ended_at_utc": "2026-01-01T00:00:09+00:00",
           "timed_start_utc": "2026-01-01T00:00:01.000000+00:00", "timed_end_utc": "2026-01-01T00:00:02.000000+00:00",
           "timed_start_monotonic_ns": 10, "timed_end_monotonic_ns": 20}
    assert pt.record_window(new) == {"basis": "timed", "start_utc": new["timed_start_utc"], "end_utc": new["timed_end_utc"],
                                     "start_monotonic_ns": 10, "end_monotonic_ns": 20}
    legacy = {"query": "q01", "seconds": 1.5, "started_at_utc": "2026-01-01T00:00:00+00:00",
              "ended_at_utc": "2026-01-01T00:00:09+00:00"}
    assert pt.record_window(legacy) == {"basis": "legacy", "start_utc": legacy["started_at_utc"],
                                        "end_utc": legacy["ended_at_utc"], "start_monotonic_ns": None, "end_monotonic_ns": None}
    for broken in ({}, {"timed_start_utc": "x"}, {**new, "timed_start_monotonic_ns": True}, {**new, "timed_end_utc": ""}, None):
        assert pt.record_window(broken)["basis"] == "legacy"      # never raises; a half-filled record falls back


# ---- RAPL / powercap -----------------------------------------------------------------------------------

def test_powercap_discovery_names_domains_per_socket_and_flags_the_mmio_duplicate(tmp_path):
    zones, reasons = pt.discover_powercap(two_socket_tree(tmp_path / "powercap"))
    assert reasons == []
    by_zone = {z.zone: z for z in zones}
    assert "intel-rapl" not in by_zone                                    # control-type dir has no counter
    assert by_zone["intel-rapl:0"].domain == "package-0" and by_zone["intel-rapl:0"].kind == "package"
    assert by_zone["intel-rapl:1:1"].domain == "package-1/dram" and by_zone["intel-rapl:1:1"].kind == "dram"
    assert by_zone["intel-rapl:0:0"].domain == "package-0/core" and by_zone["intel-rapl:0:0"].kind == "core"
    assert by_zone["intel-rapl-mmio:0"].duplicate_of == "intel-rapl:0" and not by_zone["intel-rapl-mmio:0"].sampled
    assert sum(z.sampled for z in zones) == 6                              # 2 packages x (package, core, dram)
    assert by_zone["intel-rapl:0"].max_range_uj == MAX_RANGE


def test_missing_powercap_tree_is_a_reason_not_an_error(tmp_path):
    zones, reasons = pt.discover_powercap(tmp_path / "nope")
    assert zones == [] and "does not exist" in reasons[0]


def test_empty_powercap_tree_is_a_reason(tmp_path):
    (tmp_path / "powercap" / "intel-rapl").mkdir(parents=True)
    zones, reasons = pt.discover_powercap(tmp_path / "powercap")
    assert zones == [] and "no intel-rapl*/energy_uj zone" in reasons[0]


def test_permission_denied_is_recorded_per_zone(monkeypatch, tmp_path):
    tree = two_socket_tree(tmp_path / "powercap")
    real = pt.read_int
    denied = tree / "intel-rapl:1:1" / "energy_uj"

    def read_int(path):
        if Path(path) == denied:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)
    monkeypatch.setattr(pt, "read_int", read_int)
    zones, reasons = pt.discover_powercap(tree)
    by_zone = {z.zone: z for z in zones}
    assert "PermissionError" in by_zone["intel-rapl:1:1"].error and not by_zone["intel-rapl:1:1"].sampled
    assert by_zone["intel-rapl:1"].sampled
    assert len(reasons) == 1 and "1 of 7 RAPL zones are not readable" in reasons[0] and "CVE-2020-8694" in reasons[0]


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads mode-000 files")
def test_a_real_unreadable_energy_file_is_permission_denied(tmp_path):
    tree = tmp_path / "powercap"
    make_zone(tree, "intel-rapl:0", "package-0", 5)
    (tree / "intel-rapl:0" / "energy_uj").chmod(0o000)
    sampler = sampler_for(tmp_path, tree, ipmi=False)
    report = sampler.probe()
    assert report["status"] == "unavailable" and any("PermissionError" in r for r in report["reasons"])
    assert any("chmod" in hint for hint in report["setup_hints"])


def test_counter_series_unwraps_wraparound_so_differences_stay_exact():
    series = pt.CounterSeries(1000)
    values = [series.update(raw, i * 200_000_000) for i, raw in enumerate([900, 950, 100, 150, 990, 40])]
    assert [energy for energy, _ in values] == [900, 950, 1100, 1150, 1990, 2040]
    assert [flag for _, flag in values] == ["", "", "wrap", "", "", "wrap"]
    assert values[-1][0] - values[0][0] == 1140                              # the true energy: (1000-900) + 1000 + 40


def test_counter_decrease_without_a_known_range_is_flagged_not_unwrapped():
    series = pt.CounterSeries(None)
    assert series.update(500, 0) == (500, "")
    assert series.update(100, 1) == (100, "decrease")


def test_a_long_gap_is_flagged_because_a_multiple_wrap_would_be_invisible():
    series = pt.CounterSeries(1000)
    series.update(10, 0)
    assert series.update(20, int(31 * 1e9))[1] == "gap"
    assert series.update(30, int(31.2 * 1e9))[1] == ""


def test_sampler_writes_unwrapped_energy_per_domain_with_labels(tmp_path):
    tree = two_socket_tree(tmp_path / "powercap")
    make_zone(tree, "intel-rapl:0", "package-0", MAX_RANGE - 500_000)       # about to wrap
    phase = {"value": "idle-baseline"}
    sampler = sampler_for(tmp_path, tree, labels=lambda: ("30", "polars", phase["value"]))
    report = sampler.start(background=False)
    assert report["status"] == "available" and report["cpu_energy_source"] == "powercap-rapl"
    sampler.poll_counters()
    set_energy(tree, "intel-rapl:0", 700_000)                                 # wrapped: +1_200_000 uJ since the first poll
    phase["value"] = "execute"
    sampler.poll_counters()
    sampler.stop()
    rows = read_rows(tmp_path / "out" / "power.csv")
    assert list(rows[0]) == list(pt.CSV_COLUMNS)
    package0 = [r for r in rows if r["domain"] == "package-0"]
    assert [r["raw_counter_uj"] for r in package0] == [str(MAX_RANGE - 500_000), "700000"]
    assert int(package0[1]["energy_uj"]) - int(package0[0]["energy_uj"]) == 1_200_000
    assert [r["flag"] for r in package0] == ["", "wrap"]
    assert [r["phase"] for r in package0] == ["idle-baseline", "execute"]
    assert {r["scale"] for r in rows} == {"30"} and {r["engine"] for r in rows} == {"polars"}
    assert {r["domain"] for r in rows} == {"package-0", "package-0/core", "package-0/dram",
                                           "package-1", "package-1/core", "package-1/dram"}
    assert all(r["source"] == "powercap-rapl" and r["power_w"] == "" for r in rows)
    assert int(package0[0]["monotonic_ns"]) < int(package0[1]["monotonic_ns"])


def test_partial_status_when_some_zones_are_denied(monkeypatch, tmp_path):
    tree = two_socket_tree(tmp_path / "powercap")
    real = pt.read_int

    def read_int(path):
        if Path(path).parent.name == "intel-rapl:1:1":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)
    monkeypatch.setattr(pt, "read_int", read_int)
    sampler = sampler_for(tmp_path, tree)
    report = sampler.start(background=False)
    sampler.poll_counters()
    sampler.stop()
    assert report["status"] == "partial" and report["cpu_energy_source"] == "powercap-rapl"
    assert any("PermissionError" in r for r in report["reasons"])
    assert {r["domain"] for r in read_rows(tmp_path / "out" / "power.csv")} == {
        "package-0", "package-0/core", "package-0/dram", "package-1", "package-1/core"}   # the denied zone is skipped


def test_background_thread_samples_on_a_deadline_schedule(tmp_path):
    tree = two_socket_tree(tmp_path / "powercap")
    sampler = sampler_for(tmp_path, tree, interval=0.02, ipmi=False)
    sampler.start()
    time.sleep(0.4)
    report = sampler.stop()
    rows = [r for r in read_rows(tmp_path / "out" / "power.csv") if r["domain"] == "package-0"]
    assert len(rows) >= 8                                                     # immediate first poll + about 20 ticks + final
    monos = [int(r["monotonic_ns"]) for r in rows]
    assert monos == sorted(monos) and len(set(monos)) == len(monos)
    stamps = [datetime.fromisoformat(r["timestamp_utc"]) for r in rows]
    assert stamps == sorted(stamps)
    assert report["state"] == "finished" and report["samples"]["rows"]["powercap-rapl"] == 6 * len(rows)
    assert report["overhead"]["counter_polls"] == len(rows) and report["overhead"]["wall_seconds"] >= 0.4
    assert report["clock"]["start"]["monotonic_ns"] < report["clock"]["stop"]["monotonic_ns"]
    assert json.loads((tmp_path / "out" / "power_sources.json").read_text())["state"] == "finished"


def test_stop_is_idempotent_and_safe_before_start(tmp_path):
    sampler = sampler_for(tmp_path, tmp_path / "none")
    assert sampler.stop() == {}
    sampler.start()
    sampler.stop()
    assert sampler.stop()["state"] == "finished"


def test_amd_energy_socket_counters(tmp_path):
    hwmon = tmp_path / "hwmon"
    device = hwmon / "hwmon3"
    device.mkdir(parents=True)
    (device / "name").write_text("amd_energy\n")
    for channel, label, value in ((1, "Ecore000", 11), (2, "Ecore001", 12), (3, "Esocket0", 123456), (4, "Esocket1", 654321)):
        (device / f"energy{channel}_input").write_text(f"{value}\n")
        (device / f"energy{channel}_label").write_text(label + "\n")
    other = hwmon / "hwmon0"
    other.mkdir()
    (other / "name").write_text("coretemp\n")
    zones, reasons = pt.discover_amd_energy(hwmon)
    assert reasons == [] and [z.domain for z in zones] == ["socket-0", "socket-1"]
    sampler = sampler_for(tmp_path, tmp_path / "no-powercap", hwmon_root=hwmon)
    report = sampler.start(background=False)
    sampler.poll_counters()
    sampler.stop()
    assert report["cpu_energy_source"] == "amd-energy" and report["status"] == "available"
    rows = read_rows(tmp_path / "out" / "power.csv")
    assert {(r["domain"], r["energy_uj"]) for r in rows} == {("socket-0", "123456"), ("socket-1", "654321")}


def test_amd_energy_without_a_socket_counter_is_unavailable(tmp_path):
    device = tmp_path / "hwmon" / "hwmon1"
    device.mkdir(parents=True)
    (device / "name").write_text("amd_energy\n")
    (device / "energy1_input").write_text("5\n")
    (device / "energy1_label").write_text("Ecore000\n")
    zones, reasons = pt.discover_amd_energy(tmp_path / "hwmon")
    assert zones == [] and "no Esocket* counter" in reasons[0]


# ---- nothing available -----------------------------------------------------------------------------------

def test_unavailable_writes_status_reasons_and_only_a_header(tmp_path):
    sampler = sampler_for(tmp_path, tmp_path / "no-powercap")                 # which() finds no perf/turbostat/ipmitool
    report = sampler.start()
    final = sampler.stop()
    assert report["status"] == "unavailable" and report["cpu_energy_source"] is None and report["node_power_source"] is None
    sources = {s["source"]: s for s in report["sources"]}
    assert set(sources) == {"powercap-rapl", "amd-energy", "perf-stat", "turbostat", "ipmi-dcmi"}
    assert all(s["status"] == "unavailable" for s in sources.values())
    assert "'ipmitool' not found on PATH" in sources["ipmi-dcmi"]["reasons"][0]
    assert "'perf' not found on PATH" in sources["perf-stat"]["reasons"][0]
    assert len(report["reasons"]) == 5 and report["reasons"][0].startswith("powercap-rapl: ")
    assert read_rows(tmp_path / "out" / "power.csv") == []
    assert (tmp_path / "out" / "power.csv").read_text().strip() == ",".join(pt.CSV_COLUMNS)
    assert final["state"] == "finished" and final["samples"]["total"] == 0 and final["overhead"]["counter_polls"] == 0
    assert report["host"]["timezone"]["utc_offset_seconds"] is not None


def test_probe_cli_exit_codes(monkeypatch, tmp_path, capsys):
    no_tools(monkeypatch)
    assert pt.main(["--probe", "--no-ipmi", "--powercap-root", str(tmp_path / "none")]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "unavailable"
    tree = two_socket_tree(tmp_path / "powercap")
    assert pt.main(["--probe", "--no-ipmi", "--liveness-seconds", "0", "--powercap-root", str(tree)]) == 0
    assert json.loads(capsys.readouterr().out)["cpu_energy_source"] == "powercap-rapl"


def test_lower_priority_sources_are_not_probed_once_one_works(tmp_path):
    calls = []
    sampler = sampler_for(tmp_path, two_socket_tree(tmp_path / "powercap"), ipmi=False,
                          which=lambda name: calls.append(name))
    report = sampler.probe()
    assert calls == [] and [s["status"] for s in report["sources"]] == ["active", "skipped", "skipped", "skipped"]


# ---- IPMI ----------------------------------------------------------------------------------------------

class FakeRun:
    """subprocess.run stand-in: `answers` maps the first word of the command to a CompletedProcess-like result."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        answer = self.answers[command[0]]
        if isinstance(answer, BaseException):
            raise answer
        return types.SimpleNamespace(returncode=answer[0], stdout=answer[1], stderr=answer[2])


def test_ipmi_is_sampled_alongside_rapl_and_never_needs_interactive_auth(tmp_path):
    run = FakeRun(ipmitool=(0, IPMI_ACTIVE, ""))
    sampler = sampler_for(tmp_path, two_socket_tree(tmp_path / "powercap"), which=lambda name: "/usr/bin/" + name,
                          run=run, cpu_sources=("powercap-rapl",), labels=lambda: ("1", "spark_cpu", "execute"))
    report = sampler.start(background=False)
    assert report["status"] == "available" and report["node_power_source"] == "ipmi-dcmi"
    assert sampler.poll_ipmi() is True
    sampler.stop()
    command, kwargs = run.calls[0]
    assert command == ["ipmitool", "dcmi", "power", "reading"] and kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["timeout"] > 0
    ipmi = [r for r in read_rows(tmp_path / "out" / "power.csv") if r["source"] == "ipmi-dcmi"]
    assert len(ipmi) == 1 and ipmi[0]["domain"] == "node" and ipmi[0]["power_w"] == "312" and ipmi[0]["energy_uj"] == ""
    assert ipmi[0]["engine"] == "spark_cpu" and ipmi[0]["phase"] == "execute"


def test_ipmi_alone_makes_the_status_partial(tmp_path):
    sampler = sampler_for(tmp_path, tmp_path / "none", which=lambda name: "/usr/bin/" + name,
                          run=FakeRun(ipmitool=(0, IPMI_ACTIVE, ""), perf=(1, "", ""), turbostat=(1, "", "")))
    report = sampler.probe()
    assert report["status"] == "partial" and report["cpu_energy_source"] is None and report["node_power_source"] == "ipmi-dcmi"


@pytest.mark.parametrize("answer, expected", [
    ((1, "", "Could not open device at /dev/ipmi0 or /dev/ipmidev/0: No such file or directory\n"), "Could not open device"),
    ((1, "", "Get Device ID command failed: Permission denied\n"), "Permission denied"),
    ((0, "Power reading state is: deactivated\n", ""), "deactivated"),
    ((0, "garbage\n", ""), "no 'Instantaneous power reading'"),
    (subprocess.TimeoutExpired("ipmitool", 10), "timed out"),
    (FileNotFoundError(2, "No such file"), "FileNotFoundError"),
])
def test_ipmi_failures_are_reasons(tmp_path, answer, expected):
    sampler = sampler_for(tmp_path, tmp_path / "none", cpu_sources=(), which=lambda name: "/usr/bin/" + name,
                          run=FakeRun(ipmitool=answer))
    report = sampler.probe()
    assert report["status"] == "unavailable"
    assert expected in report["sources"][0]["reasons"][0]


def test_ipmi_command_can_be_overridden_for_a_nopasswd_sudo_rule(monkeypatch, tmp_path):
    monkeypatch.setenv("POWER_TELEMETRY_IPMI_COMMAND", "sudo -n ipmitool")
    run = FakeRun(sudo=(0, IPMI_ACTIVE, ""))
    sampler = sampler_for(tmp_path, tmp_path / "none", cpu_sources=(), which=lambda name: "/usr/bin/" + name, run=run)
    assert sampler.probe()["node_power_source"] == "ipmi-dcmi"
    assert run.calls[0][0] == ["sudo", "-n", "ipmitool", "dcmi", "power", "reading"]


def test_ipmi_loop_gives_up_after_consecutive_failures(tmp_path):
    answers = iter([(0, IPMI_ACTIVE, "")] + [(1, "", "BMC busy")] * 20)
    run = types.SimpleNamespace(calls=0)

    def fake(command, **kwargs):
        run.calls += 1
        code, out, err = next(answers)
        return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)
    sampler = sampler_for(tmp_path, tmp_path / "none", cpu_sources=(), which=lambda name: "/usr/bin/" + name, run=fake,
                          ipmi_interval=0.005)
    sampler.start()
    time.sleep(0.5)
    report = sampler.stop()
    assert run.calls == 1 + pt.IPMI_MAX_CONSECUTIVE_FAILURES                  # the probe, then five failures, then it stops
    assert report["read_errors"]["ipmi-dcmi"]["count"] == pt.IPMI_MAX_CONSECUTIVE_FAILURES + 1
    assert "stopped after" in report["read_errors"]["ipmi-dcmi"]["last"]


# ---- perf stat and turbostat ---------------------------------------------------------------------------

PERF_PROBE = """\
     0.100123,12.50,Joules,power/energy-pkg/,100123000,100.00,,
     0.100123,1.50,Joules,power/energy-ram/,100123000,100.00,,
     0.100123,<not supported>,,power/energy-cores/,0,100.00,,
     0.100123,<not supported>,,power/energy-psys/,0,100.00,,
"""


def test_perf_stat_parser_accumulates_joules_into_microjoules():
    parser = pt.PerfStatParser()
    assert parser.parse("# started on Tue") == [] and parser.parse("") == []
    first = parser.parse("0.2,12.5,Joules,power/energy-pkg/,200000000,100.00,,")
    second = parser.parse("0.4,13.0,Joules,power/energy-pkg/,200000000,100.00,,")
    assert first == [("energy-pkg", "all-sockets:energy-pkg", 12_500_000, None)]
    assert second[0][2] == 25_500_000
    with pytest.raises(ValueError, match="not counted"):
        parser.parse("0.6,<not counted>,,power/energy-pkg/,0,0.00,,")
    with pytest.raises(ValueError, match="unexpected unit"):
        parser.parse("0.6,5,Watts,power/energy-pkg/,0,100.00,,")


def test_perf_stat_is_used_when_powercap_is_unreadable_and_streams_rows(tmp_path):
    run = FakeRun(perf=(0, "", PERF_PROBE))
    streams = []

    class FakeProcess:
        def __init__(self, command, **kwargs):
            self.command = command
            self.stdout = iter(["0.2,12.5,Joules,power/energy-pkg/,200000000,100.00,,\n",
                                "0.2,1.5,Joules,power/energy-ram/,200000000,100.00,,\n",
                                "0.4,13.0,Joules,power/energy-pkg/,200000000,100.00,,\n",
                                "0.4,<not counted>,,power/energy-ram/,0,0.00,,\n"])
            self.terminated = False
            streams.append(self)

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0
    sampler = sampler_for(tmp_path, tmp_path / "no-powercap", ipmi=False, cpu_sources=("powercap-rapl", "perf-stat"),
                          which=lambda name: "/usr/bin/" + name, run=run, popen=FakeProcess,
                          labels=lambda: ("10", "polars", "execute"))
    report = sampler.start()
    time.sleep(0.2)
    final = sampler.stop()
    assert report["cpu_energy_source"] == "perf-stat" and report["status"] == "available"
    assert [s["status"] for s in report["sources"]] == ["unavailable", "active"]
    perf = report["sources"][1]
    assert perf["events"] == ["power/energy-pkg/", "power/energy-ram/"]
    assert streams[0].command[:6] == ["perf", "stat", "-a", "-I", "200", "-x,"] and streams[0].terminated
    rows = read_rows(tmp_path / "out" / "power.csv")
    assert [(r["source"], r["domain"], r["energy_uj"]) for r in rows] == [
        ("perf-stat", "all-sockets:energy-pkg", "12500000"), ("perf-stat", "all-sockets:energy-ram", "1500000"),
        ("perf-stat", "all-sockets:energy-pkg", "25500000")]
    assert rows[0]["engine"] == "polars" and rows[0]["power_w"] == ""
    assert final["read_errors"]["perf-stat"]["count"] == 1                    # the <not counted> line was an error, not a row


def test_perf_stat_denied_by_paranoid_setting_is_a_reason(tmp_path):
    message = "Error:\nAccess to performance monitoring and observability operations is limited.\n"
    sampler = sampler_for(tmp_path, tmp_path / "none", ipmi=False, cpu_sources=("perf-stat",),
                          which=lambda name: "/usr/bin/" + name, run=FakeRun(perf=(255, "", message)))
    report = sampler.probe()
    assert report["status"] == "unavailable"
    assert "perf exit 255" in report["reasons"][-1] and "limited" in report["reasons"][-1]
    assert any("perf_event_paranoid" in hint for hint in report["setup_hints"])


def test_turbostat_parser_and_probe(tmp_path):
    parser = pt.TurbostatParser()
    assert parser.parse("PkgWatt\tRAMWatt\n") == []
    assert parser.parse("45.20\t3.10\n") == [("PkgWatt", "all-sockets:PkgWatt", None, 45.2),
                                             ("RAMWatt", "all-sockets:RAMWatt", None, 3.1)]
    with pytest.raises(ValueError, match="without a matching header"):
        pt.TurbostatParser().parse("45.20\t3.10\n")
    sampler = sampler_for(tmp_path, tmp_path / "none", ipmi=False, cpu_sources=("turbostat",),
                          which=lambda name: "/usr/bin/" + name,
                          run=FakeRun(turbostat=(0, "PkgWatt\tRAMWatt\n45.20\t3.10\n", "")))
    report = sampler.probe()
    assert report["cpu_energy_source"] == "turbostat" and report["sources"][0]["kind"] == "power_reading"
    denied = sampler_for(tmp_path, tmp_path / "none", ipmi=False, cpu_sources=("turbostat",),
                         which=lambda name: "/usr/bin/" + name,
                         run=FakeRun(turbostat=(1, "", "turbostat: /dev/cpu/0/msr: Permission denied\n")))
    assert "Permission denied" in denied.probe()["reasons"][0]


# ---- worker: per-query timestamps ----------------------------------------------------------------------

@pytest.fixture
def worker_data(tmp_path):
    import polars as pl
    data = tmp_path / "data"
    (data / "t").mkdir(parents=True)
    pl.DataFrame({"k": [1, 1, 2], "v": [1.0, 2.0, 3.0]}).write_parquet(data / "t" / "part-0.parquet")
    (data / "manifest.json").write_text(json.dumps({"scale_factor": 1}))
    return data


def install_fake_suite(monkeypatch):
    import polars as pl
    suite = types.ModuleType("workloads")
    suite.QUERY_IDS = ["qa", "qb"]
    suite.DESCRIPTIONS = {"qa": "first", "qb": "second"}
    suite.REQUIRED_TABLES = {"qa": ("t",), "qb": ("t",)}
    suite.FLOAT_COLUMNS = {"t": ("v",)}
    suite.polars_query = lambda query, tables: tables["t"].group_by("k").agg(pl.col("v").sum().alias(query)).sort("k")
    suite.spark_sql = lambda query: f"SELECT {query}"
    monkeypatch.setitem(sys.modules, "workloads", suite)


class FakeRow:
    def __init__(self, values):
        self.values = values

    def asDict(self, recursive=False):
        return dict(self.values)


class FakeSparkFrame:
    _jdf = types.SimpleNamespace(queryExecution=lambda: types.SimpleNamespace(
        executedPlan=lambda: types.SimpleNamespace(toString=lambda: "== Final Plan ==\nHashAggregate\nFileScan parquet\n")))

    def withColumn(self, *_):
        return self

    def createOrReplaceTempView(self, _name):
        pass

    def collect(self):
        time.sleep(0.01)
        return [FakeRow({"k": 1, "total": 3.0})]


def install_fake_spark(monkeypatch):
    class Session:
        version = "3.5.9"
        sparkContext = types.SimpleNamespace(
            setLogLevel=lambda level: None, defaultParallelism=4,
            getConf=lambda: types.SimpleNamespace(getAll=lambda: [], get=lambda key, default="": default))
        read = types.SimpleNamespace(parquet=lambda path: FakeSparkFrame())

        def sql(self, text):
            return FakeSparkFrame()

        def stop(self):
            pass
    sql = types.ModuleType("pyspark.sql")
    sql.SparkSession = types.SimpleNamespace(builder=types.SimpleNamespace(
        appName=lambda name: types.SimpleNamespace(getOrCreate=lambda: Session())))
    sql.functions = types.SimpleNamespace(col=lambda name: types.SimpleNamespace(cast=lambda kind: None))
    package = types.ModuleType("pyspark")
    package.sql = sql
    monkeypatch.setitem(sys.modules, "pyspark", package)
    monkeypatch.setitem(sys.modules, "pyspark.sql", sql)


def run_worker(monkeypatch, tmp_path, data, engine, runs=2, *extra):
    out = tmp_path / f"out-{engine}"
    monkeypatch.setattr(sys, "argv", ["worker.py", "--engine", engine, "--data", str(data), "--output", str(out),
                                      "--runs", str(runs), *extra])
    worker.main()
    return json.loads((out / "results.json").read_text())


@pytest.fixture(autouse=True)
def hermetic_gpu_energy(monkeypatch):
    """Workers must not read a real GPU in tests: no counter source unless a test installs its own reader."""
    monkeypatch.setattr(worker, "GpuEnergyReader", lambda device: pt.GpuEnergyReader(
        device, nvml=FakeNvml(not_supported=True), which=lambda name: None))


@pytest.mark.parametrize("engine", ["polars", "spark_cpu"])
def test_every_timed_record_carries_a_tight_utc_and_monotonic_window(monkeypatch, tmp_path, worker_data, engine):
    install_fake_suite(monkeypatch)
    if engine == "spark_cpu":
        install_fake_spark(monkeypatch)
    before = time.monotonic_ns()
    result = run_worker(monkeypatch, tmp_path, worker_data, engine)
    after = time.monotonic_ns()
    records = result["records"]
    assert [r["phase"] for r in records] == ["warmup"] * 2 + ["measured"] * 4          # warmups are timed records too
    anchor = result["timing_clock"]["anchor"]
    assert before <= anchor["monotonic_ns"] <= records[0]["timed_start_monotonic_ns"]
    previous_end = 0
    for record in records:
        start, end = record["timed_start_monotonic_ns"], record["timed_end_monotonic_ns"]
        assert isinstance(start, int) and isinstance(end, int) and before < start < end < after
        assert previous_end <= start                                                   # sequential windows never overlap
        previous_end = end
        window = (end - start) / 1e9
        assert record["seconds"] - 1e-6 <= window < record["seconds"] + 0.05            # contains the timed region, barely more
        for key in ("timed_start_utc", "timed_end_utc"):
            assert len(record[key].split(".")[1]) == len("123456+00:00")               # microseconds, explicit UTC
        wall = (datetime.fromisoformat(record["timed_end_utc"]) - datetime.fromisoformat(record["timed_start_utc"])).total_seconds()
        assert abs(wall - window) < 0.02                                               # both clocks describe the same window
        # the legacy wide window is untouched and still contains the tight one
        assert datetime.fromisoformat(record["started_at_utc"]) <= datetime.fromisoformat(record["timed_start_utc"])
        assert datetime.fromisoformat(record["timed_end_utc"]) <= datetime.fromisoformat(record["ended_at_utc"])
        assert pt.record_window(record)["basis"] == "timed"
    assert result["timing_clock"]["monotonic"].startswith("time.monotonic_ns()")


def test_a_pre_existing_record_without_timed_fields_still_resolves(monkeypatch, tmp_path, worker_data):
    install_fake_suite(monkeypatch)
    record = dict(run_worker(monkeypatch, tmp_path, worker_data, "polars", runs=1)["records"][0])
    for key in pt.TIMED_KEYS:
        del record[key]
    window = pt.record_window(record)
    assert window["basis"] == "legacy" and window["start_utc"] == record["started_at_utc"]


# ---- run_experiment: idle baseline, GPU clock anchor, power sampler wiring ------------------------------

class FakePopen:
    launched: list = []
    telemetry: list = []

    def __init__(self, command, **_kwargs):
        self.command, self.pid = list(command), 2 ** 22 + 1
        (FakePopen.telemetry if command and command[0] == "nvidia-smi" else FakePopen.launched).append(self.command)

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass


class Smi:
    def __init__(self):
        self.apps, self.queries = [], 0

    def capture(self, command, timeout=None):
        if any(part.startswith("--query-compute-apps") for part in command):
            self.queries += 1
            return {"command": command, "returncode": 0, "output": "\n".join(self.apps) + ("\n" if self.apps else "")}
        return {"command": command, "returncode": 0, "output": ""}


@pytest.fixture
def host(monkeypatch):
    FakePopen.launched, FakePopen.telemetry = [], []
    fake = Smi()
    monkeypatch.setattr(rx, "subprocess", types.SimpleNamespace(
        Popen=FakePopen, run=rx.subprocess.run, PIPE=rx.subprocess.PIPE, STDOUT=rx.subprocess.STDOUT,
        TimeoutExpired=rx.subprocess.TimeoutExpired))
    monkeypatch.setattr(rx, "capture", fake.capture)
    previous = signal.getsignal(signal.SIGTERM)
    yield fake
    signal.signal(signal.SIGTERM, previous)


@pytest.fixture
def layout(tmp_path):
    data_root = tmp_path / "data"
    (data_root / "sf1").mkdir(parents=True)
    (data_root / "sf1" / "manifest.json").write_text(json.dumps({"scale_factor": 1.0}))
    jar = tmp_path / "rapids.jar"
    jar.write_bytes(b"")
    return types.SimpleNamespace(data_root=data_root, jar=jar, out=tmp_path / "out", tmp=tmp_path)


def run_experiment(monkeypatch, layout, engines, *extra):
    monkeypatch.setattr(sys, "argv", [
        "run_experiment.py", "--data-root", str(layout.data_root), "--output", str(layout.out), "--scales", "1",
        "--engines", *engines, "--runs", "1", "--jar", str(layout.jar), "--keep-going", "--queries", "q06", *extra])
    rx.main()
    return (json.loads((layout.out / "schedule.json").read_text()), json.loads((layout.out / "environment.json").read_text()))


def test_idle_baseline_window_is_labelled_in_power_csv_host_csv_and_the_schedule(monkeypatch, layout, host):
    tree = two_socket_tree(layout.tmp / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    no_tools(monkeypatch)
    with Ticking(tree):                                                       # the default liveness probe needs live counters
        schedule, environment = run_experiment(monkeypatch, layout, ["polars", "spark_cpu"],
                                               "--idle-baseline-seconds", "1.2", "--power-interval-ms", "40")
    assert sorted(item["engine"] for item in schedule) == ["polars", "spark_cpu"]
    rows = read_rows(layout.out / "power.csv")
    for item in schedule:
        window = item["idle_baseline"]
        assert window["requested_seconds"] == 1.2 and window["actual_seconds"] >= 1.2 and window["phase_label"] == "idle-baseline"
        assert "skipped" not in window and window["start_monotonic_ns"] < window["end_monotonic_ns"]
        mine = [r for r in rows if r["engine"] == item["engine"] and r["domain"] == "package-0"]
        inside = [r for r in mine if window["start_monotonic_ns"] + 5e6 < int(r["monotonic_ns"]) < window["end_monotonic_ns"] - 5e6]
        assert len(inside) >= 15                                                        # 1.2 s at 40 ms
        assert {r["phase"] for r in inside} == {"idle-baseline"}                        # labelled while nothing ran
        assert {r["scale"] for r in inside} == {"1"}
        assert any(r["phase"] == "idle-baseline" for r in mine)
    assert environment["power_telemetry"]["status"] == "available"
    assert environment["power_telemetry"]["cpu_energy_source"] == "powercap-rapl"
    sidecar = json.loads((layout.out / "power_sources.json").read_text())
    assert sidecar["state"] == "finished" and sidecar["samples"]["total"] == len(rows)
    host_rows = read_rows(layout.out / "host.csv")
    assert list(host_rows[0])[-1] == "monotonic_ns" and all(int(r["monotonic_ns"]) > 0 for r in host_rows)
    assert {"idle-baseline"} <= {r["phase"] for r in host_rows}


def test_gpu_timestamps_get_a_timezone_and_a_utc_monotonic_anchor(monkeypatch, layout, host):
    _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off")
    gpu = environment["gpu_telemetry"]
    assert gpu["started"] is True and gpu["interval_ms"] == 200 and "no zone suffix" in gpu["timestamp_format"]
    assert isinstance(gpu["timezone"]["utc_offset_seconds"], int)
    before, after = gpu["anchor_before_launch"], gpu["anchor_after_launch"]
    assert before["monotonic_ns"] <= after["monotonic_ns"]
    assert datetime.fromisoformat(before["utc"]) <= datetime.fromisoformat(after["utc"])
    query = next(a for command in FakePopen.telemetry for a in command if a.startswith("--query-gpu="))
    assert query.removeprefix("--query-gpu=").split(",")[:6] == [
        "timestamp", "index", "utilization.gpu", "utilization.memory", "memory.used", "power.draw"]
    assert environment["power_telemetry"] == {"enabled": False} and not (layout.out / "power.csv").exists()


def test_power_telemetry_failing_to_start_never_stops_the_run(monkeypatch, layout, host):
    def boom(self, background=True):
        raise OSError("disk on fire")
    monkeypatch.setattr(pt.PowerSampler, "start", boom)
    schedule, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0")
    assert schedule[0]["status"] == "complete"
    assert environment["power_telemetry"] == {"enabled": True, "started": False, "error": "OSError: disk on fire"}


def test_unavailable_power_is_recorded_with_its_reasons_in_environment_json(monkeypatch, layout, host):
    monkeypatch.setattr(pt, "POWERCAP_ROOT", layout.tmp / "no-powercap")
    monkeypatch.setattr(pt, "HWMON_ROOT", layout.tmp / "no-hwmon")
    no_tools(monkeypatch)
    schedule, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0")
    info = environment["power_telemetry"]
    assert info["status"] == "unavailable" and info["cpu_energy_source"] is None and len(info["reasons"]) == 5
    assert (layout.out / "power.csv").read_text().strip() == ",".join(pt.CSV_COLUMNS)


def test_gpu_engine_idle_baseline_requires_an_idle_gpu_first(monkeypatch, layout, host):
    no_tools(monkeypatch)
    schedule, _ = run_experiment(monkeypatch, layout, ["spark_gpu"], "--idle-baseline-seconds", "0.2")
    window = schedule[0]["idle_baseline"]
    assert window["gpu_idle_precheck"]["idle"] is True and "skipped" not in window
    assert host.queries == 2                                                            # precheck, then the launch gate
    host.apps, host.queries = ["4242, python, 1024"], 0
    layout.out.rename(layout.tmp / "first")
    schedule, _ = run_experiment(monkeypatch, layout, ["spark_gpu"], "--idle-baseline-seconds", "0.2",
                                 "--gpu-idle-timeout-seconds", "0")
    assert schedule[0]["idle_baseline"]["skipped"] == "gpu_not_idle" and "start_utc" not in schedule[0]["idle_baseline"]
    assert schedule[0]["failure_reason"] == "gpu_not_idle"


def test_run_idle_baseline_skips_without_any_power_source():
    active = {"scale": "1", "engine": "polars", "phase": "preheat"}
    record = rx.run_idle_baseline(30, active, False, 60, measurable=False, sleep=lambda s: pytest.fail("must not sleep"))
    assert record == {"requested_seconds": 30, "phase_label": "idle-baseline", "skipped": "no_power_source"}


def test_run_idle_baseline_labels_the_phase_only_while_it_sleeps():
    active = {"scale": "1", "engine": "polars", "phase": "preheat"}
    seen = []
    record = rx.run_idle_baseline(2.5, active, False, 60, sleep=lambda s: seen.append((s, active["phase"])))
    assert seen == [(2.5, "idle-baseline")] and active["phase"] == "idle"
    assert record["start_monotonic_ns"] <= record["end_monotonic_ns"] and record["requested_seconds"] == 2.5


@pytest.mark.parametrize("flag, value", [("--idle-baseline-seconds", "-1"), ("--idle-baseline-seconds", "nan"),
                                         ("--power-interval-ms", "0"), ("--ipmi-interval-seconds", "inf")])
def test_invalid_telemetry_options_are_rejected_before_the_output_exists(monkeypatch, layout, host, flag, value):
    with pytest.raises(SystemExit) as raised:
        run_experiment(monkeypatch, layout, ["polars"], flag, value)
    assert raised.value.code == 2 and not layout.out.exists()


# ---- run_campaign: optional cost and telemetry blocks --------------------------------------------------

COST_ENV = ("CAMPAIGN_COST_INSTANCE_TYPE", "CAMPAIGN_COST_PROVIDER", "CAMPAIGN_COST_REGION", "CAMPAIGN_COST_HOURLY_PRICE_USD",
            "CAMPAIGN_COST_PRICE_SOURCE_URL", "CAMPAIGN_COST_PRICE_SNAPSHOT_UTC", "CAMPAIGN_HOST_LABEL")


@pytest.fixture
def clean_env(monkeypatch):
    for name in COST_ENV:
        monkeypatch.delenv(name, raising=False)


def campaign_config(*argv):
    return rc.resolve_config(rc.parse_args(list(argv)))


COST_FLAGS = ["--cost-provider", "lambda-labs", "--cost-region", "us-west-1", "--cost-hourly-price-usd", "3.29",
              "--cost-price-source-url", "https://lambda.ai/pricing", "--cost-price-snapshot-utc", "2026-11-10T18:30:00Z"]


def test_no_cost_flags_means_no_cost_block(clean_env):
    cfg = campaign_config("--dry-run")
    assert cfg.cost is None and cfg.telemetry == {}


def test_cost_block_from_flags_normalises_and_defaults_the_instance_type(clean_env):
    cfg = campaign_config("--host-label", "gpu_1x_h100_sxm5", *COST_FLAGS)
    assert cfg.cost == {"instance_type": "gpu_1x_h100_sxm5", "provider": "lambda-labs", "region": "us-west-1",
                        "hourly_price_usd": 3.29, "price_source_url": "https://lambda.ai/pricing",
                        "price_snapshot_utc": "2026-11-10T18:30:00+00:00"}
    explicit = campaign_config("--host-label", "label", "--cost-instance-type", "billed", "--cost-hourly-price-usd", "1")
    assert explicit.cost["instance_type"] == "billed" and explicit.cost["provider"] is None   # missing stays null


def test_cost_block_from_environment(monkeypatch, clean_env):
    monkeypatch.setenv("CAMPAIGN_COST_PROVIDER", "aws")
    monkeypatch.setenv("CAMPAIGN_COST_HOURLY_PRICE_USD", "98.32")
    monkeypatch.setenv("CAMPAIGN_COST_PRICE_SNAPSHOT_UTC", "2026-11-10T10:00:00+02:00")
    monkeypatch.setenv("CAMPAIGN_COST_REGION", "")                                     # empty means not supplied
    cfg = campaign_config()
    assert cfg.cost["provider"] == "aws" and cfg.cost["hourly_price_usd"] == 98.32 and cfg.cost["region"] is None
    assert cfg.cost["price_snapshot_utc"] == "2026-11-10T08:00:00+00:00"


@pytest.mark.parametrize("flag, value", [("--cost-hourly-price-usd", "0"), ("--cost-hourly-price-usd", "-3"),
                                         ("--cost-hourly-price-usd", "nan"), ("--cost-hourly-price-usd", "free"),
                                         ("--cost-price-source-url", "lambda.ai/pricing"),
                                         ("--cost-price-snapshot-utc", "2026-11-10T18:30:00"),
                                         ("--cost-price-snapshot-utc", "yesterday"),
                                         ("--idle-baseline-seconds", "-5"), ("--power-interval-ms", "0")])
def test_bad_cost_and_telemetry_values_are_rejected(clean_env, capsys, flag, value):
    with pytest.raises(SystemExit) as raised:
        rc.parse_args([flag, value])
    assert raised.value.code == 2


def test_a_bad_cost_environment_value_is_rejected_too(monkeypatch, clean_env):
    monkeypatch.setenv("CAMPAIGN_COST_HOURLY_PRICE_USD", "cheap")
    with pytest.raises(SystemExit):
        rc.parse_args([])


def test_telemetry_flags_reach_run_experiment_only_when_given(clean_env):
    plain = campaign_config("--dry-run")
    job = rc.build_units(plain)[0].jobs[0]
    command = rc.job_command(job, Path("/out"), plain)
    assert not any(flag in command for flag in rc.TELEMETRY_FLAGS.values())
    cfg = campaign_config("--idle-baseline-seconds", "0", "--power-interval-ms", "100", "--power-telemetry", "auto")
    command = rc.job_command(rc.build_units(cfg)[0].jobs[0], Path("/out"), cfg)
    assert command[command.index("--idle-baseline-seconds") + 1] == "0.0"
    assert command[command.index("--power-interval-ms") + 1] == "100.0"
    assert command[command.index("--power-telemetry") + 1] == "auto"
    assert "--ipmi-interval-seconds" not in command
    assert command[:len(rc.job_command(job, Path("/out"), plain))] == rc.job_command(job, Path("/out"), plain)  # purely appended


PROV = {"design_doc_sha256": "a" * 64, "git": {"commit": None}, "source_sha256": {"run_campaign.py": "b" * 64},
        "inputs": {"manifest_sha256": {}, "inputs_reports_sha256": {}, "rapids_jar_sha256": None}}
DESIGN = {"format": 1, "engines": ["polars"]}


def start_file(tmp_path, *flags):
    cfg = campaign_config("--root", str(tmp_path), *flags)
    cfg.root.mkdir(exist_ok=True)
    rc.start_campaign_file(cfg, DESIGN, PROV, types.SimpleNamespace(accept_design_doc_change=False))
    return cfg


def test_campaign_json_is_unchanged_when_no_cost_is_given(clean_env, tmp_path):
    start_file(tmp_path)
    stored = json.loads((tmp_path / "campaign.json").read_text())
    assert set(stored) == {"design", "design_sha256", "provenance"}
    invocation = json.loads((tmp_path / "invocations.jsonl").read_text().splitlines()[0])
    assert "cost" not in invocation and "telemetry" not in invocation


def test_campaign_json_gains_the_cost_and_telemetry_blocks(clean_env, tmp_path):
    cfg = start_file(tmp_path, "--host-label", "gpu_1x_h100_sxm5", *COST_FLAGS, "--idle-baseline-seconds", "15")
    stored = json.loads((tmp_path / "campaign.json").read_text())
    assert set(stored) == {"design", "design_sha256", "provenance", "cost", "telemetry"}
    assert list(stored["cost"]) == list(rc.COST_FIELDS) and stored["cost"] == cfg.cost
    assert stored["cost"]["hourly_price_usd"] == 3.29 and stored["telemetry"] == {"idle_baseline_seconds": 15.0}


def test_resuming_with_a_different_cost_reports_drift_and_keeps_campaign_json(clean_env, tmp_path):
    start_file(tmp_path, *COST_FLAGS)
    before = (tmp_path / "campaign.json").read_text()
    start_file(tmp_path, "--cost-provider", "lambda-labs", "--cost-hourly-price-usd", "4.00")
    assert (tmp_path / "campaign.json").read_text() == before
    drift = json.loads((tmp_path / "invocations.jsonl").read_text().splitlines()[1])["drift"]
    assert drift[0]["cost_differs_from_campaign_json"]["stored"]["hourly_price_usd"] == 3.29
    assert drift[0]["cost_differs_from_campaign_json"]["this_invocation"]["hourly_price_usd"] == 4.0


def test_resuming_a_campaign_written_before_the_cost_block_existed_is_not_an_error(clean_env, tmp_path):
    start_file(tmp_path)
    start_file(tmp_path, *COST_FLAGS)                      # cost supplied later: noted, never written into the old file
    assert "cost" not in json.loads((tmp_path / "campaign.json").read_text())
    drift = json.loads((tmp_path / "invocations.jsonl").read_text().splitlines()[1])["drift"]
    assert drift[0]["cost_differs_from_campaign_json"]["stored"] is None


def test_the_design_hash_ignores_cost_and_telemetry(clean_env):
    base = campaign_config("--dry-run")
    both = campaign_config("--dry-run", *COST_FLAGS, "--idle-baseline-seconds", "5")
    assert rc.design_dict(base, rc.build_units(base)) == rc.design_dict(both, rc.build_units(both))
    assert both.formal is True                             # instrumentation flags do not make a campaign informal


def test_eta_history_counts_the_idle_baseline_a_session_now_spends_before_launch():
    item = {"process_wall_seconds": 100.0, "preheat": {"seconds": 5.0}}
    assert rc.session_seconds(item) == 105.0                                        # records written before the baseline existed
    assert rc.session_seconds({**item, "idle_baseline": {"actual_seconds": 30.02}}) == pytest.approx(135.02)
    assert rc.session_seconds({**item, "idle_baseline": {"skipped": "no_power_source"}}) == 105.0
    assert rc.session_seconds({**item, "idle_baseline": None}) == 105.0


# ---- GPU board energy: NVML cumulative counter read around every timed run -------------------------------

class FakeNvml:
    """pynvml stand-in: every read of the counter advances it by `step` mJ."""

    def __init__(self, start=5_000_000, step=1500, init_error=None, not_supported=False):
        self.value, self.step, self.init_error, self.not_supported = start, step, init_error, not_supported
        self.fail_reads = False
        self.index = None

    def nvmlInit(self):
        if self.init_error:
            raise RuntimeError(self.init_error)

    def nvmlDeviceGetHandleByIndex(self, index):
        self.index = index
        return ("handle", index)

    def nvmlDeviceGetTotalEnergyConsumption(self, handle):
        if self.not_supported or self.fail_reads:
            raise RuntimeError("Not Supported" if self.not_supported else "GPU is lost")
        self.value += self.step
        return self.value


def test_nvml_is_the_first_source_and_each_read_is_bracketed_by_monotonic_stamps():
    nvml = FakeNvml()
    reader = pt.GpuEnergyReader(1, nvml=nvml, which=lambda name: None)
    assert reader.source == pt.GPU_ENERGY_NVML and nvml.index == 1 and reader.reasons == []
    first, second = reader.read(), reader.read()
    assert second["mj"] - first["mj"] == 1500 and isinstance(first["mj"], int)
    assert first["monotonic_ns"][0] <= first["monotonic_ns"][1] <= second["monotonic_ns"][0] <= second["monotonic_ns"][1]
    described = reader.describe()
    assert described["unit"] == "mJ" and described["device_index"] == 1 and "since the driver was loaded" in described["semantics"]


def test_nvidia_smi_is_the_fallback_when_nvml_cannot_read_the_counter():
    run = FakeRun(**{"nvidia-smi": (0, "123456789\n", "")})
    reader = pt.GpuEnergyReader(2, nvml=FakeNvml(not_supported=True), which=lambda name: "/usr/bin/" + name, run=run)
    assert reader.source == pt.GPU_ENERGY_SMI and "pynvml: RuntimeError: Not Supported" in reader.reasons[0]
    assert reader.read()["mj"] == 123456789
    command, kwargs = run.calls[-1]
    assert command == ["nvidia-smi", "-i", "2", "--query-gpu=total_energy_consumption", "--format=csv,noheader,nounits"]
    assert kwargs["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize("answer, expected", [
    ((2, "", 'Field "total_energy_consumption" is not a valid field to query.\n'), "not a valid field"),
    ((0, "[N/A]\n", ""), "not a counter value"),
    ((0, "", ""), "no output"),
    (FileNotFoundError(2, "No such file"), "FileNotFoundError"),
])
def test_unavailable_when_neither_source_can_read_the_counter(answer, expected):
    reader = pt.GpuEnergyReader(0, nvml=FakeNvml(init_error="NVML Shared Library Not Found"),
                                which=lambda name: "/usr/bin/" + name, run=FakeRun(**{"nvidia-smi": answer}))
    assert reader.source == pt.GPU_ENERGY_UNAVAILABLE
    assert "NVML Shared Library Not Found" in reader.reasons[0] and expected in reader.reasons[1]
    read = reader.read()                                                              # never raises
    assert read["mj"] is None and read["error"] == "unavailable"


def missing_library(name):
    raise OSError(f"{name}: cannot open shared object file: No such file or directory")


def test_unavailable_without_libnvidia_ml_pynvml_or_nvidia_smi(monkeypatch):
    monkeypatch.setattr(pt.importlib, "import_module", lambda name: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'pynvml'")))
    reader = pt.GpuEnergyReader(0, ctypes_loader=missing_library, which=lambda name: None)
    assert reader.source == "unavailable" and len(reader.reasons) == 3
    assert reader.reasons[0].startswith("nvml-ctypes: NvmlError: cannot load libnvidia-ml") and "libnvidia-ml.so.1" in reader.reasons[0]
    assert reader.reasons[1].startswith("pynvml: ModuleNotFoundError") and reader.reasons[2] == "nvidia-smi: 'nvidia-smi' not found on PATH"


def test_a_failing_read_is_recorded_not_raised_and_a_decrease_voids_the_delta():
    nvml = FakeNvml()
    reader = pt.GpuEnergyReader(0, nvml=nvml, which=lambda name: None)
    good = reader.read()
    nvml.fail_reads = True
    lost = reader.read()
    assert lost["mj"] is None and "GPU is lost" in lost["error"]
    window = pt.gpu_energy_window(reader.source, 0, good, lost)
    assert window["delta_mj"] is None and "GPU is lost" in window["error"] and window["start_mj"] == good["mj"]
    backwards = pt.gpu_energy_window(reader.source, 0, {"mj": 900, "monotonic_ns": [1, 2]}, {"mj": 100, "monotonic_ns": [3, 4]})
    assert backwards["delta_mj"] is None and "decreased" in backwards["error"]
    assert pt.gpu_energy_window(reader.source, 0, {"mj": 5, "monotonic_ns": [1, 2]},
                                {"mj": 5, "monotonic_ns": [3, 4]})["delta_mj"] == 0


def test_gpu_energy_delta_helper_tolerates_old_and_odd_records():
    window = pt.gpu_energy_window(pt.GPU_ENERGY_NVML, 0, {"mj": 1000, "monotonic_ns": [1, 2]}, {"mj": 3500, "monotonic_ns": [3, 4]})
    assert pt.gpu_energy_delta_j({"gpu_energy": window}) == 2.5
    for record in ({}, None, {"gpu_energy": None}, {"gpu_energy": {"source": "unavailable", "delta_mj": None}},
                   {"gpu_energy": {"delta_mj": True}}, {"gpu_energy": {"delta_mj": -3}}, {"gpu_energy": {"delta_mj": "5"}}):
        assert pt.gpu_energy_delta_j(record) is None
    assert pt.gpu_energy_window(pt.GPU_ENERGY_UNAVAILABLE, 0, {}, {}) == {
        "source": "unavailable", "unit": "mJ", "device_index": 0, "start_mj": None, "end_mj": None, "delta_mj": None}


class SteppedReader:
    """+150 mJ every 10 ms of a fake clock; every read costs 20 us."""
    source = pt.GPU_ENERGY_NVML

    def __init__(self):
        self.now = 0

    def read(self):
        before, self.now = self.now, self.now + 20_000
        return {"mj": 1000 + 150 * (before // 10_000_000), "monotonic_ns": [before, self.now]}


def test_resolution_probe_reports_how_the_counter_really_advances():
    reader = SteppedReader()
    report = pt.probe_gpu_energy_resolution(reader, duration=0.1, clock=lambda: reader.now)
    assert report["source"] == pt.GPU_ENERGY_NVML and report["reads"] >= 4900 and report["nondecreasing"] is True
    assert 8 <= report["changes_observed"] <= 10
    assert report["update_interval_ms"]["median"] == pytest.approx(10.0, abs=0.05)
    assert report["step_mj"] == {"min": 150, "median": 150, "max": 150}
    assert report["read_latency_us"]["median"] == pytest.approx(20.0)


def test_resolution_probe_on_a_coarse_counter_shows_the_coarseness():
    class Coarse(SteppedReader):
        def read(self):
            before, self.now = self.now, self.now + 20_000
            return {"mj": 1000 + 150 * (before // 100_000_000), "monotonic_ns": [before, self.now]}
    reader = Coarse()
    report = pt.probe_gpu_energy_resolution(reader, duration=1.0, clock=lambda: reader.now)
    assert report["update_interval_ms"]["median"] == pytest.approx(100.0, abs=0.05)


def test_probe_gpu_energy_cli(monkeypatch, capsys):
    monkeypatch.setattr(pt.CtypesNvml, "LIBRARIES", ("libnot-installed.so.1",))
    monkeypatch.setattr(pt.importlib, "import_module", lambda name: FakeNvml())
    assert pt.main(["--probe-gpu-energy", "--duration", "0.05"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["source"] == pt.GPU_ENERGY_NVML and out["resolution"]["reads"] > 1 and out["resolution"]["nondecreasing"]
    monkeypatch.setattr(pt.importlib, "import_module", lambda name: FakeNvml(init_error="no driver"))
    monkeypatch.setattr(pt.shutil, "which", lambda name: None)
    assert pt.main(["--probe-gpu-energy", "--duration", "0.05"]) == 3
    out = json.loads(capsys.readouterr().out)
    assert out["source"] == "unavailable" and out["resolution"] is None and "no driver" in out["reasons"][1]


class FakeEnergyReader:
    """What worker.py sees from GpuEnergyReader: +2500 mJ per read, with real monotonic brackets."""
    source = pt.GPU_ENERGY_NVML

    def __init__(self, device):
        self.device, self.value = device, 10_000

    def describe(self):
        return {"source": self.source, "unit": "mJ", "device_index": self.device, "reasons": []}

    def read(self):
        before = time.monotonic_ns()
        self.value += 2500
        return {"mj": self.value, "monotonic_ns": [before, time.monotonic_ns()]}


@pytest.mark.parametrize("engine", ["polars", "spark_cpu"])
def test_every_timed_record_carries_the_gpu_energy_delta_read_just_outside_the_timer(monkeypatch, tmp_path, worker_data, engine):
    install_fake_suite(monkeypatch)
    if engine == "spark_cpu":
        install_fake_spark(monkeypatch)
    monkeypatch.setattr(worker, "GpuEnergyReader", FakeEnergyReader)
    result = run_worker(monkeypatch, tmp_path, worker_data, engine, 2, "--gpu-device", "0")
    assert result["gpu_energy_counter"]["source"] == pt.GPU_ENERGY_NVML
    assert len(result["records"]) == 6                                                # warmups and measured alike
    for record in result["records"]:
        energy = record["gpu_energy"]
        assert energy["source"] == pt.GPU_ENERGY_NVML and energy["unit"] == "mJ" and energy["device_index"] == 0
        assert energy["end_mj"] - energy["start_mj"] == energy["delta_mj"] == 2500
        assert pt.gpu_energy_delta_j(record) == 2.5 and "error" not in energy
        # the reads sit outside the timer: before it starts, after it stops
        assert energy["start_read_monotonic_ns"][1] <= record["timed_start_monotonic_ns"]
        assert record["timed_end_monotonic_ns"] <= energy["end_read_monotonic_ns"][0]
        # per-record read latency, and the most time outside the timed region that the delta can include
        first, last = energy["start_read_monotonic_ns"], energy["end_read_monotonic_ns"]
        assert energy["start_read_latency_ns"] == first[1] - first[0] >= 0 and energy["end_read_latency_ns"] == last[1] - last[0] >= 0
        assert energy["overhang_ns_max"] == (record["timed_start_monotonic_ns"] - first[0]) + (last[1] - record["timed_end_monotonic_ns"])
        assert energy["overhang_ns_max"] >= energy["start_read_latency_ns"] + energy["end_read_latency_ns"]
        window = (energy["end_read_monotonic_ns"][1] - energy["start_read_monotonic_ns"][0]) / 1e9
        assert record["seconds"] < window < record["seconds"] + 0.05


def test_gpu_energy_unavailable_is_recorded_per_record_and_never_fails_the_run(monkeypatch, tmp_path, worker_data):
    install_fake_suite(monkeypatch)
    result = run_worker(monkeypatch, tmp_path, worker_data, "polars", 1)              # autouse fixture: no pynvml, no nvidia-smi
    assert result["gpu_energy_counter"]["source"] == "unavailable" and len(result["gpu_energy_counter"]["reasons"]) == 2
    assert result["query_execution_status"] == "complete"
    for record in result["records"]:
        assert record["gpu_energy"] == {"source": "unavailable", "unit": "mJ", "device_index": 0,
                                        "start_mj": None, "end_mj": None, "delta_mj": None}
        assert pt.gpu_energy_delta_j(record) is None


def test_gpu_energy_off_reads_nothing_and_adds_no_key(monkeypatch, tmp_path, worker_data):
    install_fake_suite(monkeypatch)

    def forbidden(device):
        raise AssertionError("the reader must not even be built with --gpu-energy off")
    monkeypatch.setattr(worker, "GpuEnergyReader", forbidden)
    result = run_worker(monkeypatch, tmp_path, worker_data, "polars", 1, "--gpu-energy", "off")
    assert result["gpu_energy_counter"] == {"source": "disabled"}
    assert all("gpu_energy" not in record for record in result["records"])


def probe_capture(monkeypatch, host, output):
    calls = []
    original = host.capture

    def capture(command, timeout=None):
        if "--probe-gpu-energy" in command:
            calls.append(command)
            return {"command": command, "returncode": 0, "output": output}
        return original(command, timeout)
    monkeypatch.setattr(rx, "capture", capture)
    return calls


def worker_commands():
    return [c for c in FakePopen.launched if any(a.endswith("worker.py") for a in c)]


def test_run_experiment_probes_the_energy_counter_once_in_a_child_and_records_it(monkeypatch, layout, host):
    payload = {"source": "pynvml.nvmlDeviceGetTotalEnergyConsumption", "unit": "mJ",
               "resolution": {"update_interval_ms": {"median": 100.0}}}
    calls = probe_capture(monkeypatch, host, "some warning\n" + json.dumps(payload, indent=2))
    schedule, environment = run_experiment(monkeypatch, layout, ["polars", "spark_cpu"], "--idle-baseline-seconds", "0")
    assert environment["gpu_energy_counter"] == payload
    assert len(calls) == 1 and calls[0][1].endswith("power_telemetry.py") and calls[0][-2:] == ["--duration", "1.0"]
    assert len(schedule) == 2 and all("--gpu-energy" not in c for c in worker_commands())   # default: command lines unchanged


def test_an_unreadable_probe_is_recorded_and_the_run_goes_on(monkeypatch, layout, host):
    probe_capture(monkeypatch, host, "Traceback: boom")
    schedule, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0")
    assert environment["gpu_energy_counter"]["source"] == "unknown" and "boom" in environment["gpu_energy_counter"]["error"]
    assert schedule[0]["status"] == "complete"


def test_gpu_energy_off_skips_the_probe_and_reaches_the_workers(monkeypatch, layout, host):
    calls = probe_capture(monkeypatch, host, "{}")
    schedule, environment = run_experiment(monkeypatch, layout, ["polars", "spark_cpu"],
                                           "--idle-baseline-seconds", "0", "--gpu-energy", "off")
    assert calls == [] and environment["gpu_energy_counter"] == {"source": "disabled"}
    commands = worker_commands()
    assert len(commands) == 2 and all(c[c.index("--gpu-energy") + 1] == "off" for c in commands)


def test_campaign_passes_gpu_energy_only_when_given(clean_env):
    plain = campaign_config("--dry-run")
    assert "--gpu-energy" not in rc.job_command(rc.build_units(plain)[0].jobs[0], Path("/out"), plain)
    cfg = campaign_config("--gpu-energy", "off")
    command = rc.job_command(rc.build_units(cfg)[0].jobs[0], Path("/out"), cfg)
    assert command[command.index("--gpu-energy") + 1] == "off" and cfg.telemetry == {"gpu_energy": "off"}
    with pytest.raises(SystemExit):
        rc.parse_args(["--gpu-energy", "maybe"])


# ---- NVML through ctypes (the path that works on the H100 hosts: no pynvml installed) ---------------------

class Fn:
    """A C function stand-in: callable, and (like a ctypes function) it accepts argtypes/restype assignment."""

    def __init__(self, impl):
        self.impl = impl

    def __call__(self, *args):
        return self.impl(*args)


class FakeLibNvml:
    """libnvidia-ml.so.1: the _v2 init/handle calls, the energy call, and C-style return codes and out-parameters."""

    def __init__(self, start=7_000_000, step=1500, energy_rc=0, v2=True):
        self.value, self.step, self.energy_rc = start, step, energy_rc
        self.index = self.handle_value = None
        names = ("nvmlInit_v2", "nvmlDeviceGetHandleByIndex_v2") if v2 else ("nvmlInit", "nvmlDeviceGetHandleByIndex")
        setattr(self, names[0], Fn(lambda: 0))
        setattr(self, names[1], Fn(self.handle))
        self.nvmlDeviceGetTotalEnergyConsumption = Fn(self.total_energy)
        self.nvmlErrorString = Fn(lambda code: {3: b"Not Supported"}.get(code, b"Unknown Error"))

    def handle(self, index, out):
        self.index = index.value                       # ctypes.c_uint(index)
        out._obj.value = 0xBEEF                        # ctypes.byref(c_void_p()): CArgObject keeps the target in _obj
        return 0

    def total_energy(self, handle, out):
        self.handle_value = handle.value
        if self.energy_rc:
            return self.energy_rc
        self.value += self.step
        out._obj.value = self.value
        return 0


def test_ctypes_nvml_is_the_first_choice_and_needs_no_python_package(monkeypatch):
    monkeypatch.setattr(pt.importlib, "import_module", lambda name: pytest.fail("pynvml must not be needed"))
    lib, loaded = FakeLibNvml(), []

    def loader(name):
        loaded.append(name)
        return lib
    reader = pt.GpuEnergyReader(1, ctypes_loader=loader, which=lambda name: pytest.fail("nvidia-smi must not be needed"))
    assert loaded == ["libnvidia-ml.so.1"] and reader.source == pt.GPU_ENERGY_CTYPES and reader.reasons == []
    assert lib.index == 1 and lib.handle_value == 0xBEEF
    first, second = reader.read(), reader.read()
    assert second["mj"] - first["mj"] == 1500 and isinstance(first["mj"], int) and "error" not in first
    assert lib.nvmlDeviceGetTotalEnergyConsumption.restype is not None and reader.describe()["source"] == pt.GPU_ENERGY_CTYPES


def test_ctypes_nvml_falls_back_to_the_unversioned_symbols_and_the_unversioned_library():
    lib = FakeLibNvml(v2=False)
    loaded = []

    def loader(name):
        loaded.append(name)
        if name.endswith(".so.1"):
            raise OSError("no such file")
        return lib
    reader = pt.GpuEnergyReader(0, ctypes_loader=loader, which=lambda name: None)
    assert loaded == ["libnvidia-ml.so.1", "libnvidia-ml.so"] and reader.source == pt.GPU_ENERGY_CTYPES
    assert reader.read()["mj"] == 7_003_000                                            # init consumed one step


def test_ctypes_nvml_error_codes_become_reasons_then_pynvml_is_tried(monkeypatch):
    monkeypatch.setattr(pt.importlib, "import_module", lambda name: FakeNvml())
    reader = pt.GpuEnergyReader(0, ctypes_loader=lambda name: FakeLibNvml(energy_rc=3), which=lambda name: None)
    assert reader.source == pt.GPU_ENERGY_NVML                                   # ctypes could not read it: pynvml could
    assert reader.reasons == ["nvml-ctypes: NvmlError: nvmlDeviceGetTotalEnergyConsumption returned 3: Not Supported"]


def test_a_ctypes_read_that_fails_later_is_recorded_not_raised():
    lib = FakeLibNvml()
    reader = pt.GpuEnergyReader(0, ctypes_loader=lambda name: lib, which=lambda name: None)
    lib.energy_rc = 999
    lost = reader.read()
    assert lost["mj"] is None and "returned 999: Unknown Error" in lost["error"]


# ---- a counter that exists but never advances is not a zero reading ---------------------------------------

def test_a_constant_powercap_counter_is_unavailable_never_zero_energy(tmp_path):
    tree = two_socket_tree(tmp_path / "powercap")                                # readable, but nothing ever changes
    slept = []
    sampler = sampler_for(tmp_path, tree, ipmi=False, liveness_seconds=0.3, sleep=slept.append)
    report = sampler.start()
    final = sampler.stop()
    assert slept == [0.3]
    assert report["status"] == "unavailable" and report["cpu_energy_source"] is None
    domains = report["sources"][0]["domains"]
    assert [d["advancing"] for d in domains if d["sampled"] is False and d["duplicate_of"] is None] == [False] * 6
    assert all("not advancing" in d["error"] for d in domains if d["advancing"] is False)
    assert any("do not advance over 0.3 s" in r and "never reported as 0 J" in r for r in report["reasons"])
    assert (tmp_path / "out" / "power.csv").read_text().strip() == ",".join(pt.CSV_COLUMNS)    # no row, in particular no 0
    assert final["samples"]["total"] == 0


def test_only_the_stuck_zones_are_dropped_when_others_advance(tmp_path):
    tree = two_socket_tree(tmp_path / "powercap")

    def sleep(_seconds):                                                         # package and core tick; dram is stuck
        for counter in tree.glob("intel-rapl:*/energy_uj"):
            if (counter.parent / "name").read_text().strip() != "dram":
                counter.write_text(f"{int(counter.read_text()) + 10_000}\n")
    sampler = sampler_for(tmp_path, tree, ipmi=False, liveness_seconds=0.3, sleep=sleep)
    report = sampler.start(background=False)
    sampler.poll_counters()
    sampler.stop()
    assert report["status"] == "partial" and report["cpu_energy_source"] == "powercap-rapl"
    assert any("2 of 7 counters are readable but do not advance" in r for r in report["reasons"])
    assert {r["domain"] for r in read_rows(tmp_path / "out" / "power.csv")} == {
        "package-0", "package-0/core", "package-1", "package-1/core"}


def test_live_counters_pass_the_liveness_probe(tmp_path):
    tree = two_socket_tree(tmp_path / "powercap")
    sampler = sampler_for(tmp_path, tree, ipmi=False, liveness_seconds=0.3, sleep=lambda s: bump(tree))
    report = sampler.probe()
    assert report["status"] == "available" and report["reasons"] == []
    assert all(d["advancing"] is True for d in report["sources"][0]["domains"] if d["sampled"])


def test_the_h100_host_without_rapl_msr_or_ipmi_and_a_psys_event_that_reads_zero_is_unavailable(tmp_path):
    perf_output = ("     0.250,<not supported>,,power/energy-pkg/,0,100.00,,\n"
                   "     0.250,<not supported>,,power/energy-ram/,0,100.00,,\n"
                   "     0.250,<not supported>,,power/energy-cores/,0,100.00,,\n"
                   "     0.250,0.00,Joules,power/energy-psys/,250000000,100.00,,\n")
    sampler = sampler_for(tmp_path, tmp_path / "no-powercap", which=lambda name: "/usr/bin/" + name,
                          run=FakeRun(perf=(0, "", perf_output), turbostat=(1, "", "no /dev/cpu/0/msr\n"),
                                      ipmitool=(1, "", "Could not open device at /dev/ipmi0\n")))
    report = sampler.start()
    final = sampler.stop()
    assert report["status"] == "unavailable" and report["cpu_energy_source"] is None and report["node_power_source"] is None
    perf = next(s for s in report["sources"] if s["source"] == "perf-stat")
    assert perf["status"] == "unavailable"
    assert any("all-sockets:energy-psys: reads 0 J over the probe: counter present but not counting" in r for r in perf["reasons"])
    assert (tmp_path / "out" / "power.csv").read_text().strip() == ",".join(pt.CSV_COLUMNS)    # not one 0 J row
    assert final["samples"]["total"] == 0 and sampler.stream is None


def test_zero_watts_from_turbostat_or_the_bmc_is_not_a_measurement(tmp_path):
    zero_ipmi = IPMI_ACTIVE.replace("312 Watts", "0 Watts")
    sampler = sampler_for(tmp_path, tmp_path / "none", cpu_sources=("turbostat",), which=lambda name: "/usr/bin/" + name,
                          run=FakeRun(turbostat=(0, "PkgWatt\tRAMWatt\n0.00\t0.00\n", ""), ipmitool=(0, zero_ipmi, "")))
    report = sampler.probe()
    assert report["status"] == "unavailable"
    assert "reports 0 W for every domain" in report["sources"][0]["reasons"][0]
    assert "not a measurement" in report["sources"][1]["reasons"][0] and sampler.stream is None and not sampler.ipmi_active


def test_a_perf_event_that_reads_zero_is_dropped_while_the_others_are_kept(tmp_path):
    probe = ("0.25,10.0,Joules,power/energy-pkg/,250000000,100.00,,\n0.25,0.00,Joules,power/energy-ram/,250000000,100.00,,\n")
    sampler = sampler_for(tmp_path, tmp_path / "none", ipmi=False, cpu_sources=("perf-stat",),
                          which=lambda name: "/usr/bin/" + name, run=FakeRun(perf=(0, "", probe)))
    report = sampler.probe()
    assert report["status"] == "available" and report["sources"][0]["events"] == ["power/energy-pkg/"]
    assert any("energy-ram: reads 0 J" in r for r in report["sources"][0]["reasons"])


def test_a_counter_that_stops_advancing_during_the_run_demotes_the_sidecar_not_the_csv(tmp_path):
    tree = two_socket_tree(tmp_path / "powercap")
    sampler = sampler_for(tmp_path, tree, ipmi=False)                            # probe passes (liveness skipped) ...
    report = sampler.start(background=False)
    for _ in range(5):
        sampler.poll_counters()                                                  # ... but the counters never move
    final = sampler.stop()
    assert report["status_at_start"] == "available" and final["status"] == "unavailable"
    assert final["cpu_energy_source"] is None and final["cpu_energy_source_rejected"] == "powercap-rapl"
    assert {v["verdict"] for v in final["liveness"].values()} == {"never_advanced"}
    assert all(v["samples"] == 5 and v["changes"] == 0 for v in final["liveness"].values())
    assert any("never_advanced over 5 samples" in r and "must not be used" in r for r in final["reasons"])


def test_a_healthy_run_gets_advancing_verdicts_and_keeps_its_status(tmp_path):
    tree = two_socket_tree(tmp_path / "powercap")
    sampler = sampler_for(tmp_path, tree, ipmi=False)
    sampler.start(background=False)
    for _ in range(4):
        bump(tree)
        sampler.poll_counters()
    final = sampler.stop()
    assert final["status"] == "available" and "status_at_start" not in final and final["cpu_energy_source"] == "powercap-rapl"
    assert {v["verdict"] for v in final["liveness"].values()} == {"advancing"}


def test_liveness_verdicts_cover_stalls_short_runs_and_power_readings(tmp_path):
    sampler = sampler_for(tmp_path, tmp_path / "none")
    second = int(1e9)
    for t, value in ((0, 10), (second, 11), (8 * second, 11), (9 * second, 11)):    # moved once, then flat for 8 s
        sampler._track("stalls", value, t, "energy")
    sampler._track("short", 5, 0, "energy")
    sampler._track("short", 5, second, "energy")
    for t in range(3):
        sampler._track("zero-watts", 0, t * second, "power")
        sampler._track("steady-watts", 312, t * second, "power")                     # a steady power reading is fine
    report = sampler.liveness_report()
    assert report["stalls"]["verdict"] == "stalled" and report["stalls"]["longest_flat_seconds"] == 8.0
    assert report["short"]["verdict"] == "insufficient_samples"
    assert report["zero-watts"]["verdict"] == "all_zero" and report["steady-watts"]["verdict"] == "reading"


def test_all_zero_perf_stream_during_the_run_is_flagged_at_stop(tmp_path):
    probe = "0.25,10.0,Joules,power/energy-pkg/,250000000,100.00,,\n"

    class ZeroProcess:
        def __init__(self, command, **kwargs):
            self.stdout = iter([f"{0.2 * i:.1f},0.00,Joules,power/energy-pkg/,200000000,100.00,,\n" for i in range(1, 6)])

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0
    sampler = sampler_for(tmp_path, tmp_path / "none", ipmi=False, cpu_sources=("perf-stat",),
                          which=lambda name: "/usr/bin/" + name, run=FakeRun(perf=(0, "", probe)), popen=ZeroProcess)
    sampler.start()
    time.sleep(0.2)
    final = sampler.stop()
    assert final["liveness"]["perf-stat/energy-pkg"]["verdict"] == "never_advanced" and final["status"] == "unavailable"
    assert {r["energy_uj"] for r in read_rows(tmp_path / "out" / "power.csv")} == {"0"}    # the raw rows stay, flagged in the sidecar


def test_cli_probe_reports_a_static_tree_as_unavailable(monkeypatch, tmp_path, capsys):
    no_tools(monkeypatch)
    tree = two_socket_tree(tmp_path / "powercap")
    assert pt.main(["--probe", "--no-ipmi", "--liveness-seconds", "0.05", "--powercap-root", str(tree)]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "unavailable" and any("do not advance" in r for r in report["reasons"])


FAKE_NVML_C = """
#include <stdint.h>
int nvmlInit_v2(void) { return 0; }
int nvmlDeviceGetHandleByIndex_v2(unsigned int idx, void **h) { if (idx > 1) return 6; *h = (void *)(uintptr_t)(0x1000 + idx); return 0; }
int nvmlDeviceGetTotalEnergyConsumption(void *h, unsigned long long *e) {
    static unsigned long long v = 5000000000ULL;       /* above 2^32, as a long-running host's counter is */
    if ((uintptr_t)h == 0x1001) return 3;
    v += 1500; *e = v; return 0; }
const char *nvmlErrorString(int c) { return c == 3 ? "Not Supported" : "No such device"; }
"""


@pytest.mark.skipif(__import__("shutil").which("cc") is None, reason="needs a C compiler")
def test_ctypes_nvml_marshals_pointers_64_bit_counters_and_error_strings_against_a_real_shared_library(tmp_path):
    source, library = tmp_path / "fake_nvml.c", tmp_path / "libfake_nvml.so"
    source.write_text(FAKE_NVML_C)
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(library), str(source)], check=True, timeout=60)
    import ctypes

    def loader(name):
        return ctypes.CDLL(str(library))
    reader = pt.GpuEnergyReader(0, ctypes_loader=loader, which=lambda name: None)
    assert reader.source == pt.GPU_ENERGY_CTYPES
    first, second = reader.read(), reader.read()
    assert first["mj"] > 2 ** 32 and second["mj"] - first["mj"] == 1500            # c_ulonglong, not a truncated int
    unsupported = pt.GpuEnergyReader(1, ctypes_loader=loader, which=lambda name: None)
    assert unsupported.source == "unavailable" and "returned 3: Not Supported" in unsupported.reasons[0]
    missing = pt.GpuEnergyReader(5, ctypes_loader=loader, which=lambda name: None)
    assert "nvmlDeviceGetHandleByIndex returned 6: No such device" in missing.reasons[0]


def test_resolution_verdict_is_left_to_the_analysis_but_provided_here():
    record = {"seconds": 0.5}
    assert pt.gpu_energy_resolution_verdict(record, 100.0) == "below_resolution"        # 500 ms < 10 x 100 ms
    assert pt.gpu_energy_resolution_verdict({"seconds": 1.0}, 100.0) == "ok"           # exactly 10 x: not below
    assert pt.gpu_energy_resolution_verdict({"seconds": 2.0}, 100.0) == "ok"
    assert pt.gpu_energy_resolution_verdict({"seconds": 0.5}, 100.0, factor=2) == "ok"
    for bad in ((record, None), (record, 0), (record, float("nan")), ({}, 100.0), ({"seconds": True}, 100.0), (None, 100.0)):
        assert pt.gpu_energy_resolution_verdict(*bad) is None


def test_gpu_energy_window_without_timed_marks_has_no_overhang_field():
    window = pt.gpu_energy_window(pt.GPU_ENERGY_NVML, 0, {"mj": 1, "monotonic_ns": [10, 15]}, {"mj": 2, "monotonic_ns": [30, 42]})
    assert "overhang_ns_max" not in window and window["start_read_latency_ns"] == 5 and window["end_read_latency_ns"] == 12


# ---- no hostname in anything this code writes, unless asked ---------------------------------------------

LAMBDA_HOSTNAME = "192-222-55-32"


def test_the_sidecar_has_no_hostname_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(pt.platform, "node", lambda: LAMBDA_HOSTNAME)
    sampler = sampler_for(tmp_path, two_socket_tree(tmp_path / "powercap"))
    sampler.start(background=False)
    sampler.poll_counters()
    report = sampler.stop()
    assert "hostname" not in report["host"]
    for name in ("power.csv", "power_sources.json"):
        assert LAMBDA_HOSTNAME not in (tmp_path / "out" / name).read_text()


def test_the_hostname_is_recorded_only_on_request(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pt.platform, "node", lambda: LAMBDA_HOSTNAME)
    no_tools(monkeypatch)
    assert pt.PowerSampler(tmp_path / "out", include_hostname=True, powercap_root=tmp_path / "none",
                           hwmon_root=tmp_path / "none", which=lambda name: None).probe()["host"]["hostname"] == LAMBDA_HOSTNAME
    pt.main(["--probe", "--no-ipmi", "--powercap-root", str(tmp_path / "none")])
    assert LAMBDA_HOSTNAME not in capsys.readouterr().out
    pt.main(["--probe", "--no-ipmi", "--include-hostname", "--powercap-root", str(tmp_path / "none")])
    assert json.loads(capsys.readouterr().out)["host"]["hostname"] == LAMBDA_HOSTNAME


def test_nothing_run_experiment_writes_with_telemetry_on_contains_the_hostname(monkeypatch, layout, host):
    monkeypatch.setattr(pt.platform, "node", lambda: LAMBDA_HOSTNAME)
    monkeypatch.setattr(pt, "POWERCAP_ROOT", two_socket_tree(layout.tmp / "powercap"))
    no_tools(monkeypatch)
    with Ticking(layout.tmp / "powercap"):
        run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0.1", "--power-interval-ms", "40")
    written = [p for p in layout.out.rglob("*") if p.is_file()]
    assert {"environment.json", "power.csv", "power_sources.json", "host.csv", "schedule.json"} <= {p.name for p in written}
    assert not [p.name for p in written if LAMBDA_HOSTNAME in p.read_text()]


# ---- environment.json: GPU identifiers redacted by default -----------------------------------------------

SMI_Q_SAMPLE = ("Driver Version                            : 580.105.08\nAttached GPUs                             : 1\n"
                "GPU 00000000:08:00.0\n    Product Name                          : NVIDIA H100 80GB HBM3\n"
                "    Serial Number                         : 1654321098765\n"
                "    GPU UUID                              : GPU-3c2f1a6e-0b7d-9f53-1111-222233334444\n"
                "    PCI\n        Bus Id                            : 00000000:08:00.0\n"
                "    FB Memory Usage\n        Total                             : 81559 MiB\n")
SMI_IDENTIFIERS = ("1654321098765", "GPU-3c2f1a6e-0b7d-9f53-1111-222233334444", "00000000:08:00.0")


def with_smi_q(monkeypatch, host):
    original = host.capture

    def capture(command, timeout=None):
        if list(command[:2]) == ["nvidia-smi", "-q"]:
            return {"command": command, "returncode": 0, "output": SMI_Q_SAMPLE}
        return original(command, timeout)
    monkeypatch.setattr(rx, "capture", capture)


def test_environment_json_redacts_gpu_identifiers_by_default(monkeypatch, layout, host):
    with_smi_q(monkeypatch, host)
    _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off")
    text = (layout.out / "environment.json").read_text()
    assert not [identifier for identifier in SMI_IDENTIFIERS if identifier in text]
    smi = next(i for i in environment["inventory"] if i["command"][:2] == ["nvidia-smi", "-q"])["output"]
    assert "Product Name                          : NVIDIA H100 80GB HBM3" in smi and "Total                             : 81559 MiB" in smi
    assert environment["redactions"]["gpu_identifiers"] == "on"
    assert environment["redactions"]["counts"] == {"Serial Number": 1, "GPU UUID": 1, "Bus Id": 1, "GPU bus id header": 1}
    assert environment["arguments"]["redact_gpu_identifiers"] == "on"


def test_environment_json_keeps_identifiers_verbatim_when_redaction_is_off(monkeypatch, layout, host):
    with_smi_q(monkeypatch, host)
    _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off",
                                    "--redact-gpu-identifiers", "off")
    smi = next(i for i in environment["inventory"] if i["command"][:2] == ["nvidia-smi", "-q"])["output"]
    assert smi == SMI_Q_SAMPLE and environment["redactions"] == {"gpu_identifiers": "off", "fields": [], "counts": {}}


def test_campaign_passes_the_redaction_choice_only_when_given(clean_env):
    plain = campaign_config("--dry-run")
    assert "--redact-gpu-identifiers" not in rc.job_command(rc.build_units(plain)[0].jobs[0], Path("/out"), plain)
    cfg = campaign_config("--redact-gpu-identifiers", "off")
    command = rc.job_command(rc.build_units(cfg)[0].jobs[0], Path("/out"), cfg)
    assert command[command.index("--redact-gpu-identifiers") + 1] == "off"


# ---- CPU-only bare metal: socket pinning and the CPU-energy gate ----------------------------------------

def launched_children():
    return len(FakePopen.launched) + len(FakePopen.telemetry)


def test_socket_pinning_is_applied_before_any_child_starts_and_recorded_everywhere(monkeypatch, layout, host, c3):
    pinned_at = []
    inner = os.sched_setaffinity

    def setaffinity(pid, cpus):
        pinned_at.append(launched_children())
        inner(pid, cpus)
    monkeypatch.setattr(os, "sched_setaffinity", setaffinity)
    tree = c3_powercap(layout.tmp / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    no_tools(monkeypatch)
    with Ticking(tree):
        schedule, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0",
                                               "--socket", "0", "--power-interval-ms", "40")
    assert pinned_at == [0]                                                      # before nvidia-smi and before every worker
    assert c3.mask["set_calls"] == [set(pt.parse_cpu_list(C3_SOCKET_0))] and schedule[0]["status"] == "complete"
    pinning = environment["cpu_pinning"]
    assert pinning["requested"] == {"socket": 0, "cpu_list": None} and pinning["cpus"] == pinning["applied_affinity"] == C3_SOCKET_0
    assert pinning["cpu_count"] == 96 and pinning["sockets"] == [0] and pinning["numa_nodes"] == [0, 1]
    assert "inherit" in pinning["mechanism"]
    assert pinning["membind"] == {
        "enabled": True, "policy": "MPOL_BIND", "nodes": [0, 1], "mechanism": "fake set_mempolicy", "verified": True,
        "reason": "memory of the engine processes is bound to the NUMA nodes of the pinned CPUs",
        "readback": {"source": "get_mempolicy", "mode": 2, "nodes": [0, 1]}}
    assert environment["arguments"]["socket"] == 0 and environment["arguments"]["cpu_list"] is None
    sidecar = json.loads((layout.out / "power_sources.json").read_text())
    assert sidecar["pinning"]["attribution"]["pinned_zones"] == {"0": {"package": ["intel-rapl:0"], "dram": ["intel-rapl:0:0"]}}
    assert sidecar["pinning"]["attribution"]["other_zones"] == {"1": {"package": ["intel-rapl:1"], "dram": ["intel-rapl:1:0"]}}
    assert {r["domain"] for r in read_rows(layout.out / "power.csv")} == {
        "package-0", "package-0/dram", "package-1", "package-1/dram"}            # every zone logged: S2-all as well as S2-pinned


def test_a_cpu_list_pins_exactly_those_cpus(monkeypatch, layout, host, c3):
    _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off",
                                    "--cpu-list", "48-73", "--cpus", "26")
    assert c3.mask["cpus"] == set(range(48, 74))
    assert environment["cpu_pinning"]["cpus"] == "48-73" and environment["cpu_pinning"]["sockets"] == [1]
    assert environment["cpu_pinning"]["whole_sockets"] == []


def test_a_run_without_pinning_says_so(monkeypatch, layout, host, c3):
    _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off")
    assert environment["cpu_pinning"] == {"pinned": False, "affinity": {
        "cpus": "0-191", "count": 192, "source": "os.sched_getaffinity(0) in the worker"}}
    assert c3.mask["set_calls"] == []


@pytest.mark.parametrize("flags, message", [
    (["--socket", "5"], "no such socket"),
    (["--cpu-list", "0-3"], "oversubscribed"),                                   # the default is 30 engine threads
    (["--cpu-list", "0-four"], "not a CPU list"),
    (["--socket", "0", "--cpu-list", "0-3"], "not allowed with argument"),
])
def test_an_impossible_pinning_stops_the_run_before_anything_exists(monkeypatch, layout, host, c3, capsys, flags, message):
    with pytest.raises(SystemExit) as stopped:
        run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", *flags)
    assert stopped.value.code == 2 and message in capsys.readouterr().err
    assert not layout.out.exists() and launched_children() == 0 and c3.mask["set_calls"] == []


def test_pinning_off_linux_stops_the_run_with_a_clear_error(monkeypatch, layout, host, c3):
    monkeypatch.delattr(os, "sched_setaffinity")
    with pytest.raises(SystemExit) as stopped:
        run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--socket", "0")
    assert "needs Linux" in str(stopped.value.code) and not layout.out.exists()


def test_rapl_that_exists_but_is_unreadable_stops_the_run_before_it_starts(monkeypatch, layout, host):
    tree = c3_powercap(layout.tmp / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    deny(monkeypatch, *(tree.glob("intel-rapl*/energy_uj")))
    with pytest.raises(SystemExit) as stopped:
        run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0")
    message = str(stopped.value.code)
    assert "cannot be read by uid" in message and "./setup_h100.sh --power-only --enable-rapl-read" in message
    assert not layout.out.exists() and launched_children() == 0


@pytest.mark.parametrize("flags", [["--cpu-energy", "off"], ["--power-telemetry", "off"]])
def test_the_explicit_opt_outs_still_run_on_such_a_host(monkeypatch, layout, host, flags):
    tree = c3_powercap(layout.tmp / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    deny(monkeypatch, *(tree.glob("intel-rapl*/energy_uj")))
    no_tools(monkeypatch)
    schedule, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", *flags)
    assert schedule[0]["status"] == "complete" and environment["cpu_energy_gate"] is None
    if flags[0] == "--cpu-energy":
        assert environment["power_telemetry"]["status"] == "unavailable"          # recorded, with its reasons, by the sampler
        assert any("PermissionError" in r for r in environment["power_telemetry"]["reasons"])


def test_require_stops_a_host_with_no_cpu_energy_at_all(monkeypatch, layout, host):
    no_tools(monkeypatch)
    with pytest.raises(SystemExit) as stopped:
        run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--cpu-energy", "require")
    assert "--cpu-energy require" in str(stopped.value.code) and not layout.out.exists()
    schedule, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0")   # auto: recorded
    assert schedule[0]["status"] == "complete" and environment["cpu_energy_gate"]["state"] == "absent"


def test_the_gate_result_is_recorded_when_the_counters_work(monkeypatch, layout, host):
    tree = c3_powercap(layout.tmp / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    no_tools(monkeypatch)
    with Ticking(tree):
        _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0",
                                        "--cpu-energy", "require", "--power-interval-ms", "40")
    gate = environment["cpu_energy_gate"]
    assert gate["ok"] is True and gate["mode"] == "require" and gate["state"] == "readable" and gate["cpu_energy_source"] == "powercap-rapl"


def test_every_worker_records_the_affinity_it_really_has(monkeypatch, tmp_path, worker_data):
    install_fake_suite(monkeypatch)
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {4, 5, 6, 40}, raising=False)
    result = run_worker(monkeypatch, tmp_path, worker_data, "polars", 1)
    assert result["cpu_affinity"] == {"cpus": "4-6,40", "count": 4, "source": "os.sched_getaffinity(0) in the worker"}
    monkeypatch.delattr(os, "sched_getaffinity")
    other = run_worker(monkeypatch, tmp_path / "again", worker_data, "polars", 1)
    assert other["cpu_affinity"]["cpus"] is None and "unavailable" in other["cpu_affinity"]["source"]


# ---- socket mode binds memory to the pinned NUMA nodes ----------------------------------------------------

def test_socket_mode_binds_memory_before_the_cpus_and_before_any_child(monkeypatch, layout, host, c3):
    events = []
    bind, pin = c3.membind, os.sched_setaffinity
    inner_set = pt.NumaPolicyApi
    monkeypatch.setattr(pt, "NumaPolicyApi", lambda *a, **k: _Spy(inner_set(), events))
    monkeypatch.setattr(os, "sched_setaffinity", lambda pid, cpus: (events.append(("cpus", launched_children())), pin(pid, cpus)))
    _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off",
                                    "--socket", "1")
    assert events == [("membind", [2, 3], 0), ("cpus", 0)]                       # memory first, both before nvidia-smi and workers
    assert bind.policy == (pt.MPOL_BIND, [2, 3])
    membind = environment["cpu_pinning"]["membind"]
    assert membind["enabled"] and membind["nodes"] == [2, 3] and membind["verified"] is True


class _Spy:
    """Wraps the fake NUMA API: records set_membind(nodes) with the number of children started so far."""

    def __init__(self, api, events):
        self.api, self.events = api, events
        self.mechanism, self.reasons, self.usable = api.mechanism, api.reasons, api.usable

    def set_membind(self, nodes):
        self.events.append(("membind", sorted(nodes), launched_children()))
        self.api.set_membind(nodes)

    def get_policy(self):
        return self.api.get_policy()


def test_a_cpu_list_binds_only_the_nodes_that_hold_its_cpus(monkeypatch, layout, host, c3):
    _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off",
                                    "--cpu-list", "0-12,96-108", "--cpus", "26")
    assert c3.membind.calls == [[0]] and environment["cpu_pinning"]["membind"]["nodes"] == [0]


def test_the_binding_reaches_the_power_sidecar_next_to_the_zone_attribution(monkeypatch, layout, host, c3):
    tree = c3_powercap(layout.tmp / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    no_tools(monkeypatch)
    with Ticking(tree):
        run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--socket", "0", "--power-interval-ms", "40")
    pinning = json.loads((layout.out / "power_sources.json").read_text())["pinning"]
    assert pinning["membind"]["policy"] == "MPOL_BIND" and pinning["membind"]["nodes"] == [0, 1]
    assert pinning["attribution"]["pinned_sockets"] == [0]


def test_a_host_that_cannot_bind_memory_stops_a_socket_run_before_it_starts(monkeypatch, layout, host, c3):
    c3.membind.fail = "Operation not permitted"
    monkeypatch.setattr(pt.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as stopped:
        run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off", "--socket", "0")
    message = str(stopped.value.code)
    assert "cannot be bound to NUMA node(s) 0-1" in message and "--no-membind" in message and "apt install numactl" in message
    assert not layout.out.exists() and launched_children() == 0 and c3.mask["set_calls"] == []   # no output, no CPU pinning either


def test_a_refused_set_mempolicy_re_executes_under_numactl_before_the_cpus_are_pinned(monkeypatch, layout, host, c3):
    c3.membind.fail = "Operation not permitted"
    monkeypatch.setattr(pt.shutil, "which", lambda name: "/usr/bin/numactl" if name == "numactl" else None)
    replaced = []

    class Replaced(BaseException):
        pass

    def execv(path, command):
        replaced.append((path, command, os.environ.get(pt.MEMBIND_GUARD_ENV)))
        raise Replaced()
    monkeypatch.setattr(os, "execv", execv)
    monkeypatch.delenv(pt.MEMBIND_GUARD_ENV, raising=False)
    with pytest.raises(Replaced):
        run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off", "--socket", "0")
    monkeypatch.delenv(pt.MEMBIND_GUARD_ENV, raising=False)
    (path, command, guard), = replaced
    assert path == "/usr/bin/numactl" and command[:3] == ["/usr/bin/numactl", "--membind=0-1", "--"] and command[3] == sys.executable
    assert command[4:] == sys.argv and sys.argv[0] == "run_experiment.py" and "--socket" in command   # the same command line again
    assert guard == "0-1" and c3.mask["set_calls"] == [] and not layout.out.exists()


def test_the_numactl_child_verifies_and_records_instead_of_binding_again(monkeypatch, layout, host, c3):
    c3.membind.policy = (pt.MPOL_BIND, [0, 1])                                   # what numactl already did
    monkeypatch.setenv(pt.MEMBIND_GUARD_ENV, "0-1")
    _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off",
                                    "--socket", "0")
    membind = environment["cpu_pinning"]["membind"]
    assert c3.membind.calls == [] and membind["verified"] is True and membind["mechanism"].startswith("numactl --membind")


def test_no_membind_runs_unbound_and_says_so_in_environment_json(monkeypatch, layout, host, c3):
    c3.membind.fail = "Operation not permitted"
    _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off",
                                    "--socket", "0", "--no-membind")
    membind = environment["cpu_pinning"]["membind"]
    assert c3.membind.calls == [] and membind["enabled"] is False and membind["policy"] == "default" and membind["nodes"] == [0, 1]
    assert "--no-membind" in membind["reason"] and environment["arguments"]["no_membind"] is True
    assert environment["cpu_pinning"]["cpus"] == C3_SOCKET_0                        # the CPUs are still pinned


def test_no_membind_without_a_pinning_is_a_usage_error(monkeypatch, layout, host, c3, capsys):
    with pytest.raises(SystemExit) as stopped:
        run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--no-membind")
    assert stopped.value.code == 2 and "--no-membind needs --socket or --cpu-list" in capsys.readouterr().err
    assert not layout.out.exists()


def test_a_host_with_no_numa_nodes_runs_pinned_with_nothing_to_bind(monkeypatch, layout, host, c3):
    import shutil
    shutil.rmtree(c3.node_root)
    _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-telemetry", "off",
                                    "--socket", "0")
    membind = environment["cpu_pinning"]["membind"]
    assert c3.membind.calls == [] and membind["enabled"] is False and "no NUMA node" in membind["reason"]
