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
