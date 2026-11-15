"""tools/paper3_probes/p3_gpu_probes.py: the analysis helpers and the PTX text, without a GPU."""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "paper3_probes"
REPO = TOOLS.parents[1]
for path in (str(REPO), str(TOOLS)):
    if path not in sys.path:
        sys.path.insert(0, path)
import p3_gpu_probes as probes  # noqa: E402


def test_integrate_constant_and_ramp_with_interpolated_ends():
    times = [0.0, 1.0, 2.0, 3.0, 4.0]
    flat = probes.integrate(times, [100.0] * 5, 0.5, 3.5)
    assert flat["joules"] == pytest.approx(300.0)
    assert flat["samples"] == 3 and flat["max_gap_s"] == pytest.approx(1.0)
    ramp = probes.integrate(times, [0.0, 10.0, 20.0, 30.0, 40.0], 0.5, 3.5)       # power = 10 t -> 5 * (3.5^2 - 0.5^2)
    assert ramp["joules"] == pytest.approx(60.0)


def test_integrate_refuses_uncovered_windows_and_skips_missing_values():
    assert probes.integrate([1.0, 2.0], [5.0, 5.0], 0.5, 1.5)["joules"] is None          # starts before the first sample
    assert probes.integrate([1.0, 2.0], [5.0, 5.0], 1.5, 2.5)["joules"] is None          # ends after the last
    assert probes.integrate([1.0], [5.0], 1.0, 1.0)["joules"] is None
    gap = probes.integrate([0.0, 1.0, 2.0], [10.0, None, 10.0], 0.0, 2.0)                # the None row is skipped
    assert gap["joules"] == pytest.approx(20.0)


def test_quantile_matches_linear_interpolation():
    assert probes.quantile([1, 2, 3, 4, 5], 0.5) == 3
    assert probes.quantile([1, 2, 3, 4, 5], 0.9) == pytest.approx(4.6)
    assert probes.quantile([None, 2.0], 0.5) == 2.0 and probes.quantile([], 0.5) is None


def test_parse_smi_csv_handles_headers_units_and_na():
    fields = ["timestamp", "power.draw", "power.draw.instant"]
    text = ("timestamp, power.draw [W], power.draw.instant [W]\n"
            "2026/11/11 14:17:03.250, 71.06 W, 70.5 W\n"
            "2026/11/11 14:17:03.270, 71.06 W, [N/A]\n"
            "garbage line\n")
    rows = probes.parse_smi_csv(text, fields)
    assert rows["power.draw"] == [71.06, 71.06] and rows["power.draw.instant"] == [70.5, None]
    assert rows["t"][1] - rows["t"][0] == pytest.approx(0.02)
    assert rows["t"][0] == pytest.approx(1794406623.25)             # 2026-11-11T14:17:03.250Z


def test_error_summary_and_t_min_rule():
    records = []
    for length, errors in ((0.1, [-0.30, -0.20, -0.25]), (1.0, [-0.05, 0.01, 0.02]), (2.0, [0.01, -0.01, 0.015]),
                           (5.0, [0.005, 0.0, -0.002])):
        records += [{"length_s": length, "rel_error": e, "counter_j": 1.0, "integral_j": 1.0, "window_s": length} for e in errors]
    summary = probes.error_summary(records)
    assert summary["1.0"]["median_rel_error"] == pytest.approx(0.01)
    assert summary["1.0"]["median_abs_rel_error"] == pytest.approx(0.02)
    assert summary["0.1"]["p90_abs_rel_error"] == pytest.approx(0.29)
    result = probes.t_min_candidates(summary, 0.02)
    assert result["t_min_s"] == 1.0                                  # 1 s is the shortest length that, with all longer ones, is <= 2%
    summary["5.0"]["median_abs_rel_error"] = 0.05
    assert probes.t_min_candidates(summary, 0.02)["t_min_s"] is None  # a longer length that fails voids the shorter ones


def test_draw_semantics_classification():
    avg = [10.0, 20.0, 30.0, 40.0]
    assert probes.equality_fraction(avg, avg) == 1.0
    assert probes.equality_fraction(avg, [10.0, 20.0, 31.0, None]) == pytest.approx(2 / 3)
    assert probes.equality_fraction([None], [1.0]) is None
    assert probes.classify_draw(1.0, 0.004).startswith("power.draw == power.draw.average")
    assert probes.classify_draw(None, 1.0).startswith("power.draw == power.draw.instant")
    assert probes.classify_draw(0.5, 0.5).endswith("in >= 95% of rows")
    assert probes.correlation(avg, avg) == pytest.approx(1.0) and probes.correlation([1, 1, 1], [1, 2, 3]) is None


def test_onset_lags_and_drift():
    times = [i * 0.2 for i in range(100)]
    util = [0 if t < 5.15 or t > 8.3 else 60 for t in times]
    lags = probes.onset_lags(times, util, [5.0, 20.0])
    assert lags[0] == pytest.approx(0.2, abs=1e-9) and lags[1] is None
    early = probes.onset_lags([4.9, 5.1], [50, 50], [5.0])
    assert early[0] == pytest.approx(-0.1)                           # a row stamped before the start is reported, negative
    assert probes.thirds_drift([0.1] * 3) is None
    assert probes.thirds_drift([0.1, 0.1, 0.1, 0.3, 0.3, 0.3]) == pytest.approx(-0.2)


def test_ptx_source_is_well_formed():
    ptx = probes.ptx_source()
    assert ptx.startswith(".version 7.0\n.target sm_70\n.address_size 64")
    assert ptx.count("fma.rn.f32") == 8 * 8 + 8
    assert ptx.count("SPIN_LOOP") == 2 and "@%p1 bra SPIN_LOOP;" in ptx and ptx.rstrip().endswith("}")
    assert probes.hex_float(1.0) == "0f3F800000" and probes.hex_float(0.5) == "0f3F000000"
    registers = {"f": 40, "rd": 8, "r": 3, "p": 3}
    for kind, count in registers.items():                           # every register used is below its declared count
        used = [int(n) for n in re.findall(rf"%{kind}(\d+)\b", ptx.split("{", 1)[1])]
        assert max(used) < count, kind
    for literal in re.findall(r"\b0f[0-9A-F]+\b", ptx):
        assert len(literal) == 10


def test_window_run_count_follows_the_design_rule():
    counts = {length: max(1, min(probes.WINDOW_MAX_RUNS, math.ceil(probes.WINDOW_TARGET_S / length))) for length in probes.PULSE_LENGTHS_S}
    assert counts == {0.1: 50, 0.25: 40, 0.5: 20, 1.0: 10, 2.0: 5, 5.0: 2, 10.0: 1}


# ---- p3_cpu_probes.py ----------------------------------------------------------------------------------------

import p3_cpu_probes as cpu_probes  # noqa: E402


def test_numa_maps_pages_sums_nodes_and_page_sizes(tmp_path):
    maps = tmp_path / "numa_maps"
    maps.write_text("55d0 bind:0-1 anon=10 dirty=10 N0=6 N1=4 kernelpagesize_kB=4\n"
                    "7f00 bind:0-1 anon=2 N0=2 kernelpagesize_kB=2048\n"
                    "7ffd bind:0-1 stack anon=1 N0=1 kernelpagesize_kB=4\n")
    pages = cpu_probes.numa_maps_pages(0, maps)
    assert pages["pages"] == {"0": 9, "1": 4}
    assert pages["mib"]["0"] == pytest.approx((6 * 4 + 2 * 2048 + 4) / 1024.0, abs=0.1)
    assert pages["policies"] == ["bind:0-1"]


def test_parse_perf_csv_per_socket_and_aggregate():
    per_socket = ("S0,1,176.5,Joules,power/energy-pkg/,10000000000,100.00,,\n"
                  "S1,1,172.0,Joules,power/energy-pkg/,10000000000,100.00,,\n"
                  "S0,1,38.1,Joules,power/energy-ram/,10000000000,100.00,,\n"
                  "# comment\n")
    rows = cpu_probes.parse_perf_csv(per_socket)
    assert [(r["socket"], r["event"], r["joules"]) for r in rows] == [
        ("S0", "power/energy-pkg/", 176.5), ("S1", "power/energy-pkg/", 172.0), ("S0", "power/energy-ram/", 38.1)]
    assert rows[0]["runtime_ns"] == 10_000_000_000
    aggregate = cpu_probes.parse_perf_csv("350.2,Joules,power/energy-pkg/,10001000000,100.00,,\n<not counted>,Joules,power/energy-psys/,0,0.00,,\n")
    assert aggregate[0]["socket"] is None and aggregate[0]["joules"] == 350.2 and aggregate[1]["joules"] is None


def test_series_watts_from_power_csv(tmp_path):
    path = tmp_path / "power.csv"
    lines = ["timestamp_utc,monotonic_ns,source,zone,domain,energy_uj,power_w,raw_counter_uj,flag,scale,engine,phase"]
    for i in range(0, 41):                                            # 200 ms rows, 100 W exactly, 8 s
        lines.append(f"t,{i * 200_000_000},powercap-rapl,intel-rapl:0,package-0,{20_000 * i * 1000},,,,,,")
    path.write_text("\n".join(lines) + "\n")
    summary = cpu_probes.series_watts(path)["intel-rapl:0|package-0"]
    assert summary["mean_w"] == pytest.approx(100.0) and summary["rows"] == 41
    assert summary["per_second_w"]["min"] == pytest.approx(100.0) and summary["median_row_spacing_ms"] == pytest.approx(200.0)



def test_p6_reference_field_selection():
    def boom():
        raise AssertionError("nvidia-smi must not be consulted for an explicit choice")
    assert probes.p6_reference_field("module.power.draw.instant", boom) == "module.power.draw.instant"
    assert probes.p6_reference_field("auto", lambda: ["power.draw", "power.draw.instant"]) == "power.draw.instant"
    assert probes.p6_reference_field("auto", lambda: ["power.draw"]) == "power.draw"
    with pytest.raises(SystemExit):     # the CLI rejects fields outside the documented choices
        probes.main(["p6", "--out", "x", "--p6-field", "memory.power.draw"])


def test_perf_watts_use_wall_seconds_not_summed_runtime():
    # bare `perf stat -a` on a 2-socket host: whole-host Joules, runtime summed over sockets (2 x 20 s)
    row = cpu_probes.add_perf_watts({"joules": 7138.29, "runtime_ns": 40_006_563_581}, 20)
    assert abs(row["watts"] - 356.9145) < 1e-3
    assert cpu_probes.add_perf_watts({"joules": None, "runtime_ns": 1}, 20).get("watts") is None



# ---- p3_cpu_probes.py p6-cpu: the RAPL analogue of P6 ----------------------------------------------------------

import argparse  # noqa: E402
import csv  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import power_telemetry as pt  # noqa: E402
from sysfs_fakes import c3_powercap  # noqa: E402
from test_power_telemetry import no_tools  # noqa: E402

LENGTHS = [0.05, 0.2, 0.6]


class SimulatedHost:
    """Hardware stand-in: the four RAPL counters of the c3 host advance with a power model, 150 W more on socket 0 under load."""
    IDLE_W = {"intel-rapl:0": 60.0, "intel-rapl:0:0": 8.0, "intel-rapl:1": 55.0, "intel-rapl:1:0": 8.0}
    LOAD_W = {"intel-rapl:0": 140.0, "intel-rapl:0:0": 10.0}

    def __init__(self, tree):
        self.tree, self.load, self.stop = Path(tree), False, threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        last = time.monotonic()
        while not self.stop.wait(0.002):
            now = time.monotonic()
            for zone, idle in self.IDLE_W.items():
                watts = idle + (self.LOAD_W.get(zone, 0.0) if self.load else 0.0)
                counter = self.tree / zone / "energy_uj"
                temporary = counter.with_name("energy_uj.tmp")
                temporary.write_text(f"{int(counter.read_text()) + int(watts * (now - last) * 1e6)}\n")
                temporary.replace(counter)
            last = now

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()


class FakeCpuPulse:
    def __init__(self, host, cpus):
        self.host, self.cpus = host, cpus

    def pulse(self, seconds):
        self.host.load = True
        time.sleep(seconds)
        self.host.load = False
        return {"nominal_s": seconds, "cpus": len(self.cpus)}


def p6_cpu_args(tmp_path, **overrides):
    values = dict(out=tmp_path / "out", cpus="0-12,96-108", repeats=2, window_repeats=1, lengths=LENGTHS, window_lengths=[0.05, 0.2],
                  window_target_s=0.6, window_max_runs=8, settle=0.3, warmup_pulse_s=0.1, interval_ms=20.0, seed=7, wait_scale=0.02)
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
def simulated(monkeypatch, tmp_path, c3):
    tree = c3_powercap(tmp_path / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tree)
    no_tools(monkeypatch)
    with SimulatedHost(tree) as host:
        monkeypatch.setattr(cpu_probes, "CpuPulse", lambda cpus: FakeCpuPulse(host, cpus))
        yield host


def interpolate(rows, instant_ns):
    """Plain-Python linear interpolation of (mono_ns, uJ) rows, independent of power_telemetry."""
    for (t0, e0), (t1, e1) in zip(rows, rows[1:]):
        if t0 <= instant_ns <= t1:
            return e0 + (e1 - e0) * (instant_ns - t0) / (t1 - t0)
    raise AssertionError("instant outside the series")


def test_p6_cpu_end_to_end_on_a_simulated_host(simulated, tmp_path, capsys):
    args = p6_cpu_args(tmp_path)
    assert cpu_probes.p6_cpu(args) == 0
    out = args.out
    assert sorted(p.name for p in out.iterdir()) == ["p6-cpu-power-sources.json", "p6-cpu-power.csv", "p6-cpu.json"]
    result = json.loads((out / "p6-cpu.json").read_text())
    assert result["step"] == "p6-cpu" and "error" not in result
    assert [z["zone"] for z in result["pinning"]["zones"]] == ["intel-rapl:0", "intel-rapl:0:0"]       # package-0 + its DRAM, not socket 1
    assert result["meta"]["cpu_energy_source"] == "powercap-rapl" and result["meta"]["attribution"]["possible"] is True
    assert result["meta"]["pinning"]["cpus"] == "0-12,96-108"
    assert result["stream"]["csv"] == "p6-cpu-power.csv" and result["stream"]["interval_s"] == 0.02 and result["stream"]["series"] == 4
    isolated, windows = result["isolated"], result["windows"]
    assert len(isolated["records"]) == 2 * len(LENGTHS) and len(windows["records"]) == 1 * 2
    assert {r["length_s"] for r in isolated["records"]} == set(LENGTHS) and all(r["runs"] == 1 for r in isolated["records"])
    for record in isolated["records"]:
        pair = record["pair"]
        assert pair["start_mono_ns"] < pair["end_mono_ns"] and record["window_s"] == pytest.approx(pair["host_s"])
        assert record["reference_j"] == pytest.approx(pair["reference_j"]) and record["estimate_j"] == pytest.approx(pair["estimate_j"])
        watts = record["reference_j"] / record["window_s"]
        assert 60 + 8 <= watts <= 60 + 8 + 150 + 10                                            # socket 0 only: package + DRAM, idle to load
    long = [r for r in isolated["records"] if r["length_s"] == 0.6]
    assert all(abs(r["rel_error"]) < 0.15 for r in long)                                         # 30 samples of 20 ms inside: interpolation is close
    assert all(r["reference_j"] == pytest.approx((60 + 8 + 150) * r["window_s"], rel=0.2) for r in long)     # the reference follows the power model
    for record in windows["records"]:
        assert record["runs"] == len(record["pairs"]) >= 2 and record["stop_reason"] in ("target_reached", "max_runs")
        assert record["window_s"] == pytest.approx(sum(p["host_s"] for p in record["pairs"]))
        assert record["reference_j"] == pytest.approx(sum(p["reference_j"] for p in record["pairs"]))
        assert (record["window_s"] >= 0.6) == (record["stop_reason"] == "target_reached") and record["runs"] <= 8
        assert record["stop_reason"] == "max_runs" or sum(p["host_s"] for p in record["pairs"][:-1]) < 0.6
    summary = isolated["summary"]["0.6"]
    assert summary["n"] == 2 and summary["n_invalid"] == 0 and summary["median_estimate_j"] > 0 and summary["median_reference_j"] > 0
    assert isolated["t_min"] == probes.t_min_candidates(isolated["summary"]) and windows["t_min"] == probes.t_min_candidates(windows["summary"])
    # the recorded estimate is what the written power.csv gives, with plain interpolation
    series = {}
    with (out / "p6-cpu-power.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["energy_uj"]:
                series.setdefault(row["zone"], []).append((int(row["monotonic_ns"]), float(row["energy_uj"])))
    pair = isolated["records"][0]["pair"]
    expected = sum(interpolate(series[z], pair["end_mono_ns"]) - interpolate(series[z], pair["start_mono_ns"]) for z in ("intel-rapl:0", "intel-rapl:0:0")) / 1e6
    assert pair["estimate_j"] == pytest.approx(expected, rel=1e-9)
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["step"] == "p6-cpu"


def test_p6_cpu_uses_the_campaigns_sampler_pin_and_node_binding(simulated, tmp_path, c3):
    cpu_probes.p6_cpu(p6_cpu_args(tmp_path, lengths=[0.05], window_lengths=[], repeats=1, window_repeats=0))
    assert c3.membind.calls == [[0]] and c3.mask["set_calls"] == [set(pt.parse_cpu_list("0-12,96-108"))]      # node 0 only, the 26 vCPUs
    sidecar = json.loads((tmp_path / "out" / "p6-cpu-power-sources.json").read_text())
    assert sidecar["interval_seconds"] == 0.02 and sidecar["pinning"]["cpus"] == "0-12,96-108"
    assert sidecar["pinning"]["attribution"]["pinned_zones"] == {"0": {"package": ["intel-rapl:0"], "dram": ["intel-rapl:0:0"]}}


def test_p6_cpu_refuses_a_host_without_pinned_energy_zones(monkeypatch, tmp_path, c3):
    monkeypatch.setattr(pt, "POWERCAP_ROOT", tmp_path / "no-powercap")
    no_tools(monkeypatch)
    assert cpu_probes.p6_cpu(p6_cpu_args(tmp_path)) == 1
    result = json.loads((tmp_path / "out" / "p6-cpu.json").read_text())
    assert "cannot measure" in result["error"] and "isolated" not in result


def test_a_window_the_series_cannot_cover_is_invalid_and_counted_not_averaged(simulated, tmp_path, monkeypatch):
    real = pt.read_power_csv_series

    def truncated(path):
        series = real(path)
        for entry in series.values():                      # the sampler "stopped" before the last second
            keep = [i for i, t in enumerate(entry["mono"]) if t <= entry["mono"][-1] - 700_000_000]
            for key in ("mono", "uj", "flags"):
                entry[key] = [entry[key][i] for i in keep]
        return series
    monkeypatch.setattr(pt, "read_power_csv_series", truncated)
    cpu_probes.p6_cpu(p6_cpu_args(tmp_path, window_lengths=[0.2], repeats=1, window_repeats=1))
    result = json.loads((tmp_path / "out" / "p6-cpu.json").read_text())
    invalid = [r for r in result["isolated"]["records"] + result["windows"]["records"] if r["rel_error"] is None]
    assert invalid and all("outside the sampled range" in r["estimate_reason"] for r in invalid)
    assert sum(row["n_invalid"] for row in result["isolated"]["summary"].values()) + \
        sum(row["n_invalid"] for row in result["windows"]["summary"].values()) == len(invalid)


def test_zone_reader_counts_a_wrap_once_and_reads_the_files_directly(tmp_path):
    for name, value in (("a", 900), ("b", 5_000)):
        (tmp_path / name).write_text(f"{value}\n")
    reader = cpu_probes.ZoneReader([{"zone": "a", "path": tmp_path / "a", "max": 1000}, {"zone": "b", "path": tmp_path / "b", "max": 10_000}])
    start = reader.read()
    (tmp_path / "a").write_text("100\n")                       # 900 -> 100 with a range of 1000: 200 uJ
    (tmp_path / "b").write_text("5700\n")
    end = reader.read()
    assert start["uj"] == {"a": 900, "b": 5000} and start["monotonic_ns"][0] <= start["monotonic_ns"][1]
    assert reader.delta_j(start, end) == pytest.approx((200 + 700) / 1e6)


def test_pinned_zone_files_come_from_the_samplers_own_report():
    report = {"pinning": {"attribution": {"possible": True, "pinned_zones": {"0": {"package": ["intel-rapl:0"], "dram": ["intel-rapl:0:1"]}}}},
              "sources": [{"source": "powercap-rapl", "domains": [
                  {"zone": "intel-rapl:0", "domain": "package-0", "path": "/p/0", "max_energy_range_uj": 262143328850, "sampled": True},
                  {"zone": "intel-rapl:0:1", "domain": "package-0/dram", "path": "/p/0/1", "max_energy_range_uj": 65712999613, "sampled": True},
                  {"zone": "intel-rapl:1", "domain": "package-1", "path": "/p/1", "max_energy_range_uj": 262143328850, "sampled": True}]}]}
    assert cpu_probes.pinned_zone_files(report) == [
        {"zone": "intel-rapl:0", "domain": "package-0", "path": "/p/0", "max": 262143328850},
        {"zone": "intel-rapl:0:1", "domain": "package-0/dram", "path": "/p/0/1", "max": 65712999613}]
    assert cpu_probes.pinned_zone_files({}) == []


def test_cpu_error_summary_names_its_keys_and_counts_invalid_runs():
    records = [{"length_s": 1.0, "runs": 1, "estimate_j": 99.0, "reference_j": 100.0, "window_s": 1.0, "rel_error": -0.01},
               {"length_s": 1.0, "runs": 1, "estimate_j": 103.0, "reference_j": 100.0, "window_s": 1.0, "rel_error": 0.03},
               {"length_s": 1.0, "runs": 1, "estimate_j": None, "reference_j": 100.0, "window_s": 1.0, "rel_error": None}]
    row = cpu_probes.cpu_error_summary(records)["1.0"]
    assert row["n"] == 2 and row["n_total"] == 3 and row["n_invalid"] == 1
    assert row["median_abs_rel_error"] == pytest.approx(0.02) and row["median_estimate_j"] == pytest.approx(101.0)
    assert "median_counter_j" not in row and "median_integral_j" not in row


@pytest.mark.skipif(not hasattr(os, "sched_setaffinity") or not hasattr(os, "fork"), reason="forked, pinned spin loops need Linux")
def test_the_cpu_pulse_forks_one_pinned_spin_loop_per_cpu_for_the_requested_time():
    cpus = sorted(os.sched_getaffinity(0))[:2]
    started = time.monotonic()
    done = cpu_probes.CpuPulse(cpus).pulse(0.2)
    assert done == {"nominal_s": 0.2, "cpus": len(cpus)} and 0.2 <= time.monotonic() - started < 2.0


def test_p6_cpu_defaults_mirror_the_gpu_p6_and_the_campaign(capsys, monkeypatch):
    parser_args = []
    monkeypatch.setattr(cpu_probes, "p6_cpu", lambda args: parser_args.append(args) or 0)
    assert cpu_probes.main(["p6-cpu", "--out", "x"]) == 0
    args = parser_args[0]
    assert args.cpus == "0-12,96-108" and args.repeats == 8 and args.window_repeats == 5
    assert args.lengths == list(probes.PULSE_LENGTHS_S) == [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
    assert args.window_lengths == list(probes.WINDOW_LENGTHS_S) and args.window_target_s == 10.0 and args.window_max_runs == 50
    assert args.interval_ms is None and cpu_probes.P6_CPU_INTERVAL_S == 0.2
    minutes = float(re.search(r"about (\d+) min", capsys.readouterr().err).group(1))
    assert 12 <= minutes <= 25                                                                 # ~17 min of pulses and pauses, plus fork cost on the host


@pytest.mark.parametrize("flags", [["--lengths", "0"], ["--window-target-s", "-1"], ["--repeats", "0"], ["--interval-ms", "0"],
                                   ["--wait-scale", "-1"], ["--warmup-pulse-s", "0"]])
def test_p6_cpu_rejects_nonsense_options(flags):
    with pytest.raises(SystemExit):
        cpu_probes.main(["p6-cpu", "--out", "x", *flags])
